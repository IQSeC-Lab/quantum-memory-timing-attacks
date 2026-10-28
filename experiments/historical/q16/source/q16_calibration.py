"""Q16 calibration coordinator, raw replay, resume and mandatory full export."""
import argparse
import concurrent.futures
import csv
import fcntl
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import threading
import time
import traceback

import q16_calibration_design as d
from q16_io import read, put, sha, normalize, write_raw, read_raw, source_hashes

HERE = Path(__file__).resolve().parent
STOP = threading.Event()


def check_environment(repo):
    import q6_reference
    from importlib.metadata import version
    result = q6_reference.check_repo(repo)
    expected = read(HERE/'pinned_source_hashes.json')
    for name,digest in expected.items():
        if sha(Path(repo)/name)!=digest:
            raise RuntimeError('Pinned scheduler/host source changed: '+name)
    if version('scipy')!='1.15.3':
        raise RuntimeError('Use the existing Q15 environment: SciPy 1.15.3 is required')
    result['scipy'] = version('scipy')
    result['reviewed_source_sha256'] = expected
    return result


def worker(args):
    assert __debug__, 'Run without -O'
    job = read(args.job)
    if job['source_sha256']!=source_hashes(HERE):
        raise RuntimeError('Worker source differs from frozen job')
    environment = check_environment(args.repo)
    import q16_calibration_engine as engine
    from q16_calibration_audit import validate_record
    started, cpu_start = time.monotonic(), time.process_time()
    raw = None
    try:
        raw = normalize(engine.run(job['spec']))
        # Write raw evidence before validating, so failed checks remain reviewable.
        write_raw(args.raw,raw)
        compact = validate_record(raw,job['spec'])
        put(args.compact,compact)
        put(args.statistics,dict(wall_seconds=time.monotonic()-started,
            cpu_seconds=time.process_time()-cpu_start,
            peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
            validation='Independent calibration raw replay passed',environment=environment))
    except BaseException:
        try:
            put(args.raw.with_name(args.raw.name+'.failed-spec.json'),job['spec'])
            snapshot = normalize(engine.failure_snapshot())
            if snapshot is not None:
                write_raw(args.raw.with_name(args.raw.name+'.failure-trace.gz'),snapshot)
        except BaseException:
            traceback.print_exc()
        raise


def verified_checkpoint(folder, job):
    marker = read(folder/'completed.json')
    if marker['job_sha256']!=d.stable(job):
        raise RuntimeError('Checkpoint belongs to another job')
    for kind in ('raw','compact','statistics'):
        name = marker[kind+'_file']
        if Path(name).name!=name or sha(folder/name)!=marker[kind+'_sha256']:
            raise RuntimeError('Checkpoint corruption: '+str(folder/name))
    compact = read(folder/marker['compact_file'])
    if compact['spec']!=job['spec']:
        raise RuntimeError('Checkpoint specification differs')
    return compact,read(folder/marker['statistics_file']),marker


def execute_stage(root,repo,stage,workers,hashes):
    specs = list(d.specifications())
    print('%s: %d stream simulations, %d total workers; verified checkpoints are reused.'%(stage,len(specs),workers),flush=True)

    def one(index,spec):
        if STOP.is_set():
            raise RuntimeError('Coordinator stopping')
        folder = root/stage/('stream-%04d'%index)
        job = dict(spec=spec,source_sha256=hashes)
        if (folder/'job.json').exists():
            if read(folder/'job.json')!=job:
                raise RuntimeError('Existing job configuration differs')
        else:
            put(folder/'job.json',job)
        reused = (folder/'completed.json').exists()
        if not reused:
            tag = str(time.time_ns())
            raw,compact,stats,log = [folder/(tag+suffix) for suffix in ('.raw.json.gz','.compact.json','.stats.json','.log')]
            command = [sys.executable,'-u',str(HERE/'q16_calibration.py'),'worker','--repo',str(repo),
                       '--job',str(folder/'job.json'),'--raw',str(raw),'--compact',str(compact),'--statistics',str(stats)]
            with log.open('w') as handle:
                process = subprocess.Popen(command,stdout=handle,stderr=subprocess.STDOUT)
                last_heartbeat = time.monotonic()
                try:
                    while process.poll() is None:
                        if STOP.is_set():
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill(); process.wait()
                            raise RuntimeError('Coordinator stopping')
                        if time.monotonic()-last_heartbeat>30:
                            print(stage, 'stream',index,'still running |',log,flush=True)
                            last_heartbeat = time.monotonic()
                        time.sleep(0.2)
                    if process.returncode:
                        raise RuntimeError('Worker failed. Raw evidence retained. Log: '+str(log)+'\n'+log.read_text()[-10000:])
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill(); process.wait()
            put(folder/'completed.json',dict(job_sha256=d.stable(job),
                raw_file=raw.name,raw_sha256=sha(raw),compact_file=compact.name,compact_sha256=sha(compact),
                statistics_file=stats.name,statistics_sha256=sha(stats)))
        record,stats,marker = verified_checkpoint(folder,job)
        return index,record,stats,marker,reused

    completed = {}
    benign_records = []
    begun = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one,index,spec) for index,spec in enumerate(specs)]
        try:
            for future in concurrent.futures.as_completed(futures):
                index,record,stats,marker,reused = future.result()
                completed[index] = dict(folder='stream-%04d'%index,**marker)
                if record['spec']['stage'] == 'benign':
                    benign_records.append(record)
                    if len(benign_records) == 192:
                        from q16_calibration_audit import calibrate_deadline
                        receipt = normalize(calibrate_deadline(benign_records))
                        destination = root/'deadline_calibration.json'
                        if destination.exists() and read(destination) != receipt:
                            raise RuntimeError('Frozen benign deadline differs on resume')
                        put(destination, receipt)
                        print('Benign-only deadline:', receipt['status'], receipt['deadline_ns'], 'ns', flush=True)
                count = len(completed)
                if count<=workers or count%8==0 or count==len(specs):
                    print('%s: %d/%d verified | stream %d %s | elapsed %.1f min | worker peak %.0f MiB'%(
                        stage,count,len(specs),index,'reused' if reused else 'finished',
                        (time.monotonic()-begun)/60,stats['peak_rss_mib']),flush=True)
        except BaseException:
            STOP.set()
            for future in futures:
                future.cancel()
            raise
    manifest = [completed[i] for i in range(len(specs))]
    put(root/stage/'manifest.json',manifest)


def replay_stage(root,stage,hashes):
    from q16_calibration_audit import validate_record
    records = []
    for index,spec in enumerate(d.specifications()):
        folder = root/stage/('stream-%04d'%index)
        saved,stats,marker = verified_checkpoint(folder,dict(spec=spec,source_sha256=hashes))
        rebuilt = validate_record(read_raw(folder/marker['raw_file']),spec)
        if rebuilt!=saved:
            raise RuntimeError('Raw-to-compact replay differs: '+str(folder))
        records.append(rebuilt)
        if (index+1)%16==0:
            print('Raw replay:',stage,index+1,'streams verified',flush=True)
    return records


def run(args):
    assert __debug__, 'Assertions must remain enabled'
    if not 1<=args.workers<=8:
        raise ValueError('Invalid worker count')
    root,repo = args.run_dir.expanduser().resolve(),args.repo.expanduser().resolve()
    if not root.name.startswith('q16-calibration-'):
        raise ValueError('Use a new q16-calibration-* results folder')
    for protected in (HERE,repo):
        if root==protected or root in protected.parents or protected in root.parents:
            raise ValueError('Results must be separate from source and Qoala')
    root.mkdir(parents=True,exist_ok=True)
    with (root/'RUNNING.lock').open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('This Q16 calibration run already has an active coordinator') from None
        hashes = source_hashes(HERE)
        protocol = d.protocol(hashes)
        if (root/'protocol.json').exists() and read(root/'protocol.json')!=protocol:
            raise RuntimeError('Resume protocol/source differs; existing results not modified')
        put(root/'protocol.json',protocol)
        (root/'source').mkdir(exist_ok=True)
        for name,digest in hashes.items():
            target = root/'source'/name
            if not target.exists():
                shutil.copy2(HERE/name,target)
            if sha(target)!=digest:
                raise RuntimeError('Saved source differs')
        try:
            environment = check_environment(repo)
            put(root/'environment.json',environment)
            print('RUN DIRECTORY:',root,flush=True)
            for test_file in ('q16_tests.py','q16_stress_tests.py','q16_calibration_tests.py'):
                tests = subprocess.run([sys.executable,'-u',str(HERE/test_file)],cwd=HERE,
                                       capture_output=True,text=True)
                test_output = tests.stdout+tests.stderr
                (root/(test_file+'-'+str(time.time_ns())+'.log')).write_text(test_output)
                print(test_output,flush=True)
                if tests.returncode:
                    raise RuntimeError('Offline checks failed. No calibration simulations started.')
            execute_stage(root,repo,'calibration',args.workers,hashes)
            records = replay_stage(root,'calibration',hashes)
            from q16_calibration_audit import summarize
            summary = normalize(summarize(records))
            if read(root/'deadline_calibration.json') != summary['deadline']:
                raise RuntimeError('Raw-rebuilt deadline differs from benign-only freeze')
            put(root/'own_policy_selection.json',summary['own_policy'])
            put(root/'calibration_summary.json',summary)
            from q16_calibration_audit import text_report
            (root/'CALIBRATION_REPORT.txt').write_text(text_report(summary))
            put(root/'COMPLETE.json',dict(status=summary['status'],revision=d.REVISION,
                source_sha256=hashes,protocol_sha256=sha(root/'protocol.json'),
                summary_sha256=sha(root/'calibration_summary.json'),
                deadline_sha256=sha(root/'deadline_calibration.json'),
                selection_sha256=sha(root/'own_policy_selection.json'),
                report_sha256=sha(root/'CALIBRATION_REPORT.txt'),
                note='Calibration executions and raw replay complete. Inspect status and selection_success. '
                     'No final victim evaluation or Q16 attack-effect claim.'))
            print('Calibration finished:',summary['status'],'| streams:',summary['executions'],flush=True)
        except BaseException as error:
            put(root/('STOPPED-'+str(time.time_ns())+'.json'),dict(error=repr(error),traceback=traceback.format_exc()))
            raise


def audit(args):
    # Standard Python only. Normalize before JSON comparison (parent pilot CLI fix).
    root = args.run_dir.expanduser().resolve()
    hashes = source_hashes(HERE)
    if read(root/'protocol.json') != d.protocol(hashes):
        raise RuntimeError('Use the exact saved source from this calibration run')
    for name,digest in hashes.items():
        if sha(root/'source'/name) != digest:
            raise RuntimeError('Saved source mismatch: '+name)
    complete = read(root/'COMPLETE.json')
    if (complete['source_sha256'] != hashes or complete['revision'] != d.REVISION or
            complete['protocol_sha256'] != sha(root/'protocol.json') or
            complete['summary_sha256'] != sha(root/'calibration_summary.json')):
        raise RuntimeError('Completion receipt differs from saved source/protocol/summary')
    from q16_calibration_audit import summarize
    records = replay_stage(root,'calibration',hashes)
    summary = normalize(summarize(records))
    if summary != read(root/'calibration_summary.json') or complete['status'] != summary['status']:
        raise RuntimeError('Reconstructed calibration summary differs')
    for field, name in [('deadline','deadline_calibration.json'),('selection','own_policy_selection.json'),
                        ('report','CALIBRATION_REPORT.txt')]:
        if complete[field+'_sha256'] != sha(root/name):
            raise RuntimeError('Calibration receipt checksum differs: '+name)
    if summary['deadline'] != read(root/'deadline_calibration.json') or summary['own_policy'] != read(root/'own_policy_selection.json'):
        raise RuntimeError('Saved deadline/selection differs from complete raw replay')
    from q16_calibration_audit import text_report
    if (root/'CALIBRATION_REPORT.txt').read_text() != text_report(summary):
        raise RuntimeError('Human-readable report differs')
    print('Independent calibration audit:',summary['status'],'|',len(records),'streams',flush=True)



def export(args):
    from q16_io import export_full
    export_full(args.run_dir,args.output_dir)


def main():
    parser = argparse.ArgumentParser(description='Q16 deadline and own-policy calibration only')
    subs = parser.add_subparsers(dest='command',required=True)
    p = subs.add_parser('run')
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--workers',type=int,default=8)
    p = subs.add_parser('worker')
    for arg in ('repo','job','raw','compact','statistics'):
        p.add_argument('--'+arg,type=Path,required=True)
    p = subs.add_parser('audit')
    p.add_argument('--run-dir',type=Path,required=True)
    p = subs.add_parser('export')
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    args = parser.parse_args()
    {'run':run,'worker':worker,'audit':audit,'export':export}[args.command](args)


if __name__=='__main__':
    main()
