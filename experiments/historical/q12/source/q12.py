"""Q12 immutable batch checkpoints, independent calibration, and paired analysis."""
from pathlib import Path
from collections import defaultdict
import argparse
import concurrent.futures
import fcntl
import gzip
import hashlib
import json
import math
import os
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
import numpy as np
from scipy.stats import binomtest
import q12_design as d
import q12_engine as engine
import q6_reference as q6

HERE = Path(__file__).resolve().parent
STOP = threading.Event()


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def code_hashes():
    return {name: sha(HERE/name) for name in d.SOURCE_FILES}


def put(path, value):
    """Create immutable metadata; never overwrite a different protocol/result."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if read(path) != value:
            raise RuntimeError('Existing metadata differs: '+str(path)+'. Preserve this run; use a new directory for a changed protocol or code.')
        return
    temp = path.with_name(path.name+'.tmp-'+str(os.getpid()))
    with temp.open('w') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def consume_jsonl(path):
    with gzip.open(path, 'rt') as f:
        return [json.loads(line) for line in f if line.strip()]


def worker(args):
    environment = q6.check_repo(args.repo)
    job = read(args.job)
    assert job['code_hashes'] == code_hashes()
    start = time.monotonic()
    cpu = time.process_time()
    current = None
    spec = None
    try:
        with gzip.open(args.raw, 'xt', compresslevel=3) as raw, gzip.open(args.compact, 'xt', compresslevel=3) as compact:
            for index, spec in enumerate(job['specs']):
                current = None
                current = engine.run(spec, job['frozen'])
                record = engine.validate(current, spec, job['frozen'])
                raw.write(json.dumps(current, separators=(',', ':'), allow_nan=False)+'\n')
                compact.write(json.dumps(record, separators=(',', ':'), allow_nan=False)+'\n')
                if (index+1) % 32 == 0:
                    print('Worker', index+1, '/', len(job['specs']), flush=True)
    except BaseException:
        if spec is not None:
            put(args.raw.with_name(args.raw.name+'.failed-spec.json'), spec)
        if current is not None:
            failed = args.raw.with_name(args.raw.name+'.failed-record.json')
            put(failed, current)
        raise
    put(args.statistics, dict(trials=len(job['specs']), wall_seconds=time.monotonic()-start,
                             cpu_seconds=time.process_time()-cpu,
                             peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                             validation='Q12 full raw-record validation passed', environment=environment))


def verified_checkpoint(folder, job):
    marker = read(folder/'completed.json')
    assert marker['job_hash'] == d.stable(job)
    for kind in ['raw', 'compact', 'statistics']:
        name = marker[kind+'_file']
        assert Path(name).name == name
        assert sha(folder/name) == marker[kind+'_sha256'], 'Checkpoint checksum mismatch: '+str(folder/name)
    stats = read(folder/marker['statistics_file'])
    assert stats['trials'] == len(job['specs']) and stats['validation'] == 'Q12 full raw-record validation passed'
    rows = consume_jsonl(folder/marker['compact_file'])
    assert len(rows) == len(job['specs'])
    assert [r['spec'] for r in rows] == job['specs']
    return rows, stats, marker


def execute(root, repo, stage, frozen, workers, consume):
    chunks = list(d.batches(stage))
    total = sum(len(x) for x in chunks)
    frozen_hash = d.stable(frozen)
    done = {}
    print(f'{stage}: {total:,} executions in {len(chunks):,} batches; workers={workers}. Completed batches are verified and reused.', flush=True)

    def one(index, chunk):
        if STOP.is_set():
            raise RuntimeError('Coordinator stopping')
        folder = root/stage/('batch-%05d' % index)
        needed = {s['workload']+'|'+str(s['campaign']) for s in chunk if s['stage']=='evaluation' and s['arm']=='learned'}
        subset = {key: frozen[key] for key in sorted(needed)}
        job = dict(stage=stage, specs=chunk, code_hashes=code_hashes(), frozen=subset, all_selections_sha256=frozen_hash)
        put(folder/'job.json', job)
        reused = (folder/'completed.json').exists()
        if not reused:
            tag = str(time.time_ns())
            raw = folder/(tag+'.raw.jsonl.gz')
            compact = folder/(tag+'.compact.jsonl.gz')
            stats = folder/(tag+'.stats.json')
            log = folder/(tag+'.log')
            cmd = [sys.executable, '-u', str(HERE/'q12.py'), 'worker', '--repo', str(repo),
                   '--job', str(folder/'job.json'), '--raw', str(raw), '--compact', str(compact), '--statistics', str(stats)]
            with log.open('w') as handle:
                p = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT)
                try:
                    heartbeat = time.monotonic()
                    while p.poll() is None:
                        if STOP.is_set():
                            p.terminate()
                            p.wait()
                            raise RuntimeError('Coordinator stopping')
                        if time.monotonic()-heartbeat > 30:
                            print(f'{stage}: batch {index} still running; log {log}', flush=True)
                            heartbeat = time.monotonic()
                        time.sleep(.2)
                    if p.returncode:
                        raise RuntimeError('Worker failed. Preserve and export this run. Log: '+str(log)+'\n'+log.read_text()[-12000:])
                finally:
                    if p.poll() is None:
                        p.terminate()
                        p.wait()
            assert read(stats)['trials'] == len(chunk)
            put(folder/'completed.json', dict(job_hash=d.stable(job),
                raw_file=raw.name, raw_sha256=sha(raw), compact_file=compact.name, compact_sha256=sha(compact),
                statistics_file=stats.name, statistics_sha256=sha(stats)))
        rows, statistics, marker = verified_checkpoint(folder, job)
        return index, rows, statistics, marker, reused

    verified = 0
    new_records = 0
    started = time.monotonic()
    last_print = started
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, i, chunk) for i, chunk in enumerate(chunks)]
        try:
            for f in concurrent.futures.as_completed(futures):
                i, rows, stats, marker, reused = f.result()
                consume(rows)
                verified += len(rows)
                new_records += 0 if reused else len(rows)
                done[i] = dict(batch='batch-%05d' % i, **marker, stats=stats)
                elapsed = time.monotonic()-started
                if len(done) <= workers or len(done) % 8 == 0 or time.monotonic()-last_print >= 20 or verified == total:
                    eta = elapsed*(total-verified)/new_records/3600 if new_records else None
                    suffix = f' | rough remaining {eta:.2f} h' if eta is not None else ''
                    print(f'{stage}: {verified:,}/{total:,} verified | batch {i} {"reused" if reused else "finished"} | RAM {stats["peak_rss_mib"]:.0f} MiB{suffix}', flush=True)
                    last_print = time.monotonic()
        except BaseException:
            STOP.set()
            for f in futures:
                f.cancel()
            raise
    manifest = [done[i] for i in range(len(chunks))]
    put(root/stage/'manifest.json', manifest)
    return dict(trials=total, batches=len(chunks), worker_cpu_seconds=sum(x['stats']['cpu_seconds'] for x in manifest),
                worker_wall_seconds=sum(x['stats']['wall_seconds'] for x in manifest),
                peak_worker_rss_mib=max(x['stats']['peak_rss_mib'] for x in manifest),
                manifest_sha256=sha(root/stage/'manifest.json'))


def row_key(r):
    s = r['spec']
    return (d.WORKLOADS.index(s['workload']), s['campaign'], list(d.SCENARIOS).index(s['scenario']),
            d.ARMS.index(s['arm']), s['shot'])


def contrast(values, bootstrap, family):
    values = np.asarray(values, dtype=float)
    assert len(values) == d.CAMPAIGNS and np.isfinite(values).all()
    draws = values[bootstrap].mean(axis=1)
    tail = .05/(2*family)
    positive = int(np.sum(values > 0))
    negative = int(np.sum(values < 0))
    p = float(binomtest(positive, positive+negative, .5).pvalue) if positive+negative else 1.0
    return dict(independent_campaigns=len(values), mean_difference_pp=100*float(values.mean()),
                approximate_bootstrap95_interval_pp=(100*np.quantile(draws, [tail, 1-tail])).tolist(),
                positive_campaigns=positive, negative_campaigns=negative, tied_campaigns=int(np.sum(values == 0)),
                exact_direction_p=p, adjusted_direction_p=min(1.0, family*p), family=family)


def analyze(rows, frozen, calibration_costs):
    assert len(rows) == d.counts()['evaluation']
    assert [r['spec'] for r in sorted(rows, key=row_key)] == list(d.specifications('evaluation'))
    groups = defaultdict(dict)
    for r in rows:
        s = r['spec']
        key = (s['workload'], s['campaign'], s['scenario'], s['arm'])
        assert s['shot'] not in groups[key]
        groups[key][s['shot']] = r
    bootstrap = np.random.default_rng(d.BOOTSTRAP_ROOT).integers(0, d.CAMPAIGNS, size=(20000,d.CAMPAIGNS))
    primary, secondary, summaries = [], [], []
    for w in d.WORKLOADS:
        for scenario in d.SCENARIOS:
            n = d.PRIMARY_SHOTS if scenario == 'primary_0500us' else d.SECONDARY_SHOTS
            delta, storage_delta = [], []
            all_complete = True
            for campaign in range(d.CAMPAIGNS):
                a = groups[w,campaign,scenario,'learned']
                b = groups[w,campaign,scenario,'random_phase']
                assert set(a) == set(b) == set(range(n))
                # Pairing is by independent campaign and fresh input-stream index.
                for i in range(n):
                    assert a[i]['state'] == b[i]['state'] and a[i]['target_request_ns'] == b[i]['target_request_ns']
                    assert a[i]['competitor_service_ns'] == b[i]['competitor_service_ns'] == d.BURST_SERVICE_NS
                delta.append(sum(a[i]['wrong_output']-b[i]['wrong_output'] for i in range(n))/n)
                if all(a[i]['physical_storage_ns'] is not None and b[i]['physical_storage_ns'] is not None for i in range(n)):
                    storage_delta.append(sum(a[i]['physical_storage_ns']-b[i]['physical_storage_ns'] for i in range(n))/n/1e6)
                all_complete = all_complete and all(r['unfinished'] == 0 for r in [*a.values(), *b.values()])
            is_primary = scenario == 'primary_0500us'
            effect = contrast(delta, bootstrap, 2 if is_primary else 1)
            effect.update(workload=w, scenario=scenario, comparison='learned minus equal-work random',
                          shots_per_arm_per_campaign=n, all_compared_targets_completed=all_complete,
                          mean_storage_difference_ms=float(np.mean(storage_delta)) if len(storage_delta)==d.CAMPAIGNS else None,
                          interpretation='Prespecified primary family of two' if is_primary else 'Exploratory pointwise interval; no multiplicity-controlled secondary claim')
            if is_primary:
                effect['supports_additional_wrong_outputs'] = bool(all_complete and effect['approximate_bootstrap95_interval_pp'][0]>0 and effect['adjusted_direction_p']<.05)
                primary.append(effect)
            else:
                secondary.append(effect)
            for arm in d.ARMS:
                rr = [groups[w,campaign,scenario,arm][i] for campaign in range(d.CAMPAIGNS) for i in range(n)]
                intervals = [r['physical_storage_ns']/1e6 for r in rr if r['physical_storage_ns'] is not None]
                summaries.append(dict(workload=w, scenario=scenario, arm=arm, offered=len(rr),
                    wrong_outputs=sum(r['wrong_output'] for r in rr), unfinished=sum(r['unfinished'] for r in rr),
                    wrong_output_fraction=sum(r['wrong_output'] for r in rr)/len(rr),
                    wrong_or_unfinished_fraction=sum(r['application_failure'] for r in rr)/len(rr),
                    mean_completed_storage_ms=float(np.mean(intervals)) if intervals else None,
                    competitor_service_ns_per_execution=0 if arm=='no_competitor' else d.BURST_SERVICE_NS))
    return dict(version=d.VERSION, primary_contrasts=primary, secondary_contrasts=secondary, summaries=summaries,
                both_workloads_meet_prespecified_criterion=all(x['supports_additional_wrong_outputs'] for x in primary),
                calibration_costs=calibration_costs,
                selected_phases={key: value['phase_ns'] for key,value in frozen.items()},
                inference='Campaigns, not individual shots, are independent units. Bootstrap mean intervals are approximate; sign tests address direction, a different estimand. All primary and secondary cells retained.',
                scope='Conditional on supplied coarse execution epoch, workload-class knowledge, calibration-job access and co-location. No deployed timing API or hardware attack is demonstrated.')


def run(args):
    assert __debug__
    root = args.run_dir.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root/'RUNNING.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('This run already has an active coordinator. Do not launch a second notebook for the same run.')
        environment = q6.check_repo(repo)
        from importlib.metadata import version
        environment['scipy'] = version('scipy')
        hashes = code_hashes()
        put(root/'protocol.json', d.protocol(hashes))
        put(root/'environment.json', environment)
        (root/'source').mkdir(exist_ok=True)
        for name, digest in hashes.items():
            target = root/'source'/name
            if not target.exists():
                shutil.copy2(HERE/name, target)
            assert sha(target) == digest
        print('RUN DIRECTORY:', root, flush=True)
        print('Resume with the same notebook: RESUME_RUN_DIR = '+repr(str(root)), flush=True)
        print('All phases and sample sizes are frozen. MODE="full" runs pilot, calibration and evaluation in order.', flush=True)
        pilot = []
        pilot_resources = execute(root, repo, 'pilot', {}, args.workers, pilot.extend)
        # Batches finish out of order; compare using the complete specified order.
        index = {d.stable(s): i for i,s in enumerate(d.specifications('pilot'))}
        pilot.sort(key=lambda r: index[d.stable(r['spec'])])
        gate = engine.validate_pilot(pilot)
        gate['resources'] = pilot_resources
        put(root/'PILOT_COMPLETE.json', gate)
        remaining = d.counts()['calibration'] + d.counts()['evaluation']
        estimate = pilot_resources['worker_wall_seconds']/pilot_resources['trials']*remaining/max(1,args.workers)/3600
        print(f'PILOT PASSED. Planning estimate for calibration + evaluation: {estimate:.2f} hours at {args.workers} workers; conditions and resume overhead can differ.', flush=True)
        if args.mode == 'pilot':
            print('Pilot only. Set MODE="full" and reuse this directory to continue.', flush=True)
            return

        observations = defaultdict(list)
        costs = defaultdict(lambda: dict(own_application_executions=0, competing_cpu_service_ns=0,
                                        bob_cpu_service_ns=0.0, alice_cpu_service_ns=0.0,
                                        completed_epr_pairs=0))
        def own_rows(rows):
            for r in rows:
                o = r['own_observation']
                key = o['workload']+'|'+str(o['campaign'])
                observations[key].append(o)
                cost = costs[key]
                cost['own_application_executions'] += 1
                for field, source in [('competing_cpu_service_ns','competitor_service_ns'), ('bob_cpu_service_ns','bob_cpu_service_ns'), ('alice_cpu_service_ns','alice_cpu_service_ns'), ('completed_epr_pairs','completed_epr_pairs')]:
                    cost[field] += r[source]
        resources_cal = execute(root, repo, 'calibration', {}, args.workers, own_rows)
        frozen = {}
        for w,campaign in itertools_product(d.WORKLOADS, range(d.CAMPAIGNS)):
            key = w+'|'+str(campaign)
            obs = sorted(observations[key], key=lambda o:(d.CANDIDATES.index(o['candidate']),o['shot']))
            frozen[key] = d.select_phase(obs)
        del observations
        # Floating resource sums are finalized deterministically from exact integer-ns
        # service totals, not future-completion order. All instruction times are integer.
        for cost in costs.values():
            for field in ['bob_cpu_service_ns','alice_cpu_service_ns']:
                cost[field] = int(round(cost[field]))
        costs = dict(sorted(costs.items()))
        put(root/'calibration_costs.json', costs)
        put(root/'frozen_selections.json', frozen)
        put(root/'CALIBRATION_COMPLETE.json', dict(resources=resources_cal, selections_sha256=sha(root/'frozen_selections.json'),
            calibration_manifest_sha256=sha(root/'calibration'/'manifest.json'), independent_campaigns=d.CAMPAIGNS,
            note='Every selection used only the owner observation projection; controls and all candidate outcomes are retained.'))
        print('CALIBRATION COMPLETE. All 64 campaign/workload phase choices are frozen before evaluation.', flush=True)
        evaluation = []
        resources_eval = execute(root, repo, 'evaluation', frozen, args.workers, evaluation.extend)
        analysis = analyze(evaluation, frozen, costs)
        put(root/'analysis.json', analysis)
        put(root/'COMPLETE.json', dict(version=d.VERSION, counts=d.counts(), code_hashes=hashes,
            frozen_selections_sha256=sha(root/'frozen_selections.json'), analysis_sha256=sha(root/'analysis.json'),
            stage_resources=dict(pilot=pilot_resources, calibration=resources_cal, evaluation=resources_eval),
            status='Execution complete; independent raw-record audit still required. This marker does not imply a positive attack result.'))
        print('Q12 EXECUTION COMPLETE. Primary contrasts:', flush=True)
        for result in analysis['primary_contrasts']:
            print(json.dumps(result, indent=2), flush=True)
        print('Export the review archive from the next notebook cell. Raw trace files remain in this run directory.', flush=True)


def itertools_product(*args):
    from itertools import product
    return product(*args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['run','worker'])
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--mode', choices=['pilot','full'], default='full')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--job', type=Path)
    parser.add_argument('--raw', type=Path)
    parser.add_argument('--compact', type=Path)
    parser.add_argument('--statistics', type=Path)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('workers must be positive')
    if args.command == 'worker':
        worker(args)
    else:
        if args.run_dir is None:
            parser.error('--run-dir is required')
        def interrupted(signum, frame):
            STOP.set()
            raise KeyboardInterrupt('Coordinator interrupted; complete checkpoints remain reusable')
        signal.signal(signal.SIGTERM, interrupted)
        run(args)


if __name__ == '__main__':
    main()
