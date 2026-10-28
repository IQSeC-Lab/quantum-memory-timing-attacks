"""Q14 immutable checkpoints and isolated simulator workers."""
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
import q14_design as d
import q14_engine as engine
import q6_reference as q6
import q14_prerequisites as prerequisites

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
    d.validate_policies(job['frozen'])
    assert job['fixed_configuration_sha256']==d.stable(job['frozen'])
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
        snapshot = engine.failure_snapshot()
        if snapshot is not None:
            put(args.raw.with_name(args.raw.name+'.failure-trace.json'), snapshot)
        raise
    put(args.statistics, dict(trials=len(job['specs']), wall_seconds=time.monotonic()-start,
                             cpu_seconds=time.process_time()-cpu,
                             peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                             validation='Q14 full raw-record validation passed', environment=environment))


def verified_checkpoint(folder, job):
    marker = read(folder/'completed.json')
    assert marker['job_hash'] == d.stable(job)
    for kind in ['raw', 'compact', 'statistics']:
        name = marker[kind+'_file']
        assert Path(name).name == name
        assert sha(folder/name) == marker[kind+'_sha256'], 'Checkpoint checksum mismatch: '+str(folder/name)
    stats = read(folder/marker['statistics_file'])
    assert stats['trials'] == len(job['specs']) and stats['validation'] == 'Q14 full raw-record validation passed'
    rows = consume_jsonl(folder/marker['compact_file'])
    assert len(rows) == len(job['specs'])
    assert [r['spec'] for r in rows] == job['specs']
    return rows, stats, marker


def execute(root, repo, stage, frozen, workers, consume):
    chunks = list(d.batches(stage))
    total = sum(len(x) for x in chunks)
    frozen_hash = d.stable(frozen)
    done = {}
    print(f'{stage}: {total:,} work items in {len(chunks):,} batches; workers={workers}. Completed batches are verified and reused.', flush=True)

    def one(index, chunk):
        if STOP.is_set():
            raise RuntimeError('Coordinator stopping')
        folder = root/stage/('batch-%05d' % index)
        subset = frozen  # Full immutable Q12 policy library, retained in every job.
        job = dict(stage=stage, specs=chunk, code_hashes=code_hashes(), frozen=subset, fixed_configuration_sha256=frozen_hash)
        put(folder/'job.json', job)
        reused = (folder/'completed.json').exists()
        if not reused:
            tag = str(time.time_ns())
            raw = folder/(tag+'.raw.jsonl.gz')
            compact = folder/(tag+'.compact.jsonl.gz')
            stats = folder/(tag+'.stats.json')
            log = folder/(tag+'.log')
            cmd = [sys.executable, '-u', str(HERE/'q14.py'), 'worker', '--repo', str(repo),
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


def run(args):
    assert __debug__
    from q14_analysis import analyze
    proof=prerequisites.verify(args.q12_run,args.q13_run,args.q12_audit,args.q13_audit)
    frozen=proof['policies']
    root=args.run_dir.expanduser().resolve()
    repo=args.repo.expanduser().resolve()
    parents=[Path(args.q12_run).resolve(),Path(args.q13_run).resolve(),Path(args.q12_audit).resolve(),Path(args.q13_audit).resolve(),repo,HERE]
    assert all(root!=p and p not in root.parents and root not in p.parents for p in parents),'Q14 output must be separate from all parent/source/audit folders'
    root.mkdir(parents=True,exist_ok=True)
    with (root/'RUNNING.lock').open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('This Q14 directory already has an active coordinator. Do not launch another for it.')
        environment=q6.check_repo(repo)
        from importlib.metadata import version
        environment['scipy']=version('scipy')
        hashes=code_hashes()
        put(root/'protocol.json',d.protocol(hashes,proof['identity']))
        put(root/'prerequisites.json',proof['identity'])
        put(root/'frozen_policies.json',frozen)
        put(root/'inherited_calibration_costs.json',proof['inherited_costs'])
        evidence=root/'prerequisite_evidence.json'
        if not evidence.exists():
            put(evidence,dict(identity=proof['identity'],evidence=proof['evidence']))
        else:
            assert read(evidence)['identity']==proof['identity'],'Saved parent evidence identity differs'
        put(root/'environment.json',environment)
        (root/'source').mkdir(exist_ok=True)
        for name,digest in hashes.items():
            target=root/'source'/name
            if not target.exists():
                shutil.copy2(HERE/name,target)
            assert sha(target)==digest
        print('RUN DIRECTORY:',root,flush=True)
        print('Both parent raw audits passed. Reusing all 64 learned phases without retraining.',flush=True)
        print('Resume using the same notebook and RESUME_RUN_DIR = '+repr(str(root)),flush=True)
        resources={}
        for stage in ('pilot',):
            rows=[]
            resources[stage]=execute(root,repo,stage,frozen,args.workers,rows.extend)
            order={d.stable(s):i for i,s in enumerate(d.specifications(stage))}
            rows.sort(key=lambda r:order[d.stable(r['spec'])])
            gate=engine.validate_gate(rows,stage)
            gate['resources']=resources[stage]
            put(root/(stage.upper()+'_COMPLETE.json'),gate)
            print(stage.upper()+' PASSED: '+json.dumps(gate),flush=True)
        estimate=resources['pilot']['worker_wall_seconds']/resources['pilot']['trials']*d.counts()['evaluation']/args.workers/3600
        print(f'PILOT PASSED. Approximate remaining elapsed hours at {args.workers} worker(s): {estimate:.2f}. Planning estimate, not guaranteed.',flush=True)
        if args.mode=='pilot':
            print('Pilot-only mode finished. MODE="full" resumes this directory and continues.',flush=True)
            return
        rows=[]
        resources['evaluation']=execute(root,repo,'evaluation',frozen,args.workers,rows.extend)
        analysis=analyze(rows,frozen,proof['inherited_costs'])
        put(root/'analysis.json',analysis)
        put(root/'COMPLETE.json',dict(version=d.VERSION,code_hashes=hashes,counts=d.counts(),
            analysis_sha256=sha(root/'analysis.json'),stage_resources=resources,
            frozen_policies_sha256=sha(root/'frozen_policies.json'),prerequisites_sha256=sha(root/'prerequisites.json'),
            prerequisite_evidence_sha256=sha(root/'prerequisite_evidence.json'),
            status='Execution complete; independent raw-record audit remains. Completion is not a positive-result claim.'))
        print('Q14 EXECUTION COMPLETE. Primary contrasts:',flush=True)
        for item in analysis['primary_contrasts']:
            print(json.dumps(item,indent=2),flush=True)
        print('Run the export cell and share the review ZIP. Preserve raw traces on the server.',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['run','worker'])
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path)
    parser.add_argument('--mode',choices=['pilot','full'],default='full')
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--job',type=Path)
    parser.add_argument('--raw',type=Path)
    parser.add_argument('--compact',type=Path)
    parser.add_argument('--statistics',type=Path)
    for parent in ('q12-run','q13-run','q12-audit','q13-audit'):
        parser.add_argument('--'+parent,type=Path)
    args=parser.parse_args()
    if args.workers<1:
        parser.error('workers must be positive')
    if args.command=='worker':
        worker(args)
    else:
        if args.run_dir is None:
            parser.error('--run-dir is required')
        if any(getattr(args,name) is None for name in ('q12_run','q13_run','q12_audit','q13_audit')):
            parser.error('All four parent run/audit paths are required for a Q14 run')
        def interrupted(signum,frame):
            STOP.set()
            raise KeyboardInterrupt('Interrupted; completed checkpoints remain reusable')
        signal.signal(signal.SIGTERM,interrupted)
        run(args)


if __name__=='__main__':
    main()

