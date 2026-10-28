"""Q16 calibration coordinator, raw replay, resume and mandatory full export."""
import argparse
import concurrent.futures
import csv
import json
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

import q16_fresh_design as d
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
    import q16_fresh_engine as engine
    from q16_fresh_raw_audit import validate_record
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
            command = [sys.executable,'-u',str(HERE/'q16_fresh.py'),'worker','--repo',str(repo),
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
    from q16_fresh_raw_audit import validate_record
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

def verify_calibration(folder):
    folder=Path(folder).expanduser().resolve();binding=d.binding()
    if folder.name!=binding['run_id']: raise ValueError('Wrong calibration folder')
    for name,want in binding['file_sha256'].items():
        if sha(folder/name)!=want: raise ValueError('Reviewed calibration changed: '+name)
    complete=read(folder/'COMPLETE.json')
    for name,want in complete['source_sha256'].items():
        if sha(folder/'source'/name)!=want: raise ValueError('Calibration source changed: '+name)
    deadline=read(folder/'deadline_calibration.json');policy=read(folder/'own_policy_selection.json')
    if deadline['deadline_ns']!=d.DEADLINE_NS or policy['offsets_ns']!=list(d.SELECTED_OFFSETS_NS):
        raise ValueError('Calibrated deadline/policy differs')
    if not policy['selection_success'] or policy['template']!=[0,250000]: raise ValueError('Wrong selected policy')
    return dict(status='PASS_REVIEWED_CALIBRATION_IDENTITY',run_dir=str(folder),file_sha256=binding['file_sha256'])

def run(args):
    assert __debug__, 'Run without -O'
    if not 1<=args.workers<=8: raise ValueError('Workers must be 1 through 8')
    root,repo=args.run_dir.expanduser().resolve(),args.repo.expanduser().resolve()
    if not root.name.startswith('q16-fresh-planning-'): raise ValueError('Use a separate q16-fresh-planning-* folder')
    calibration=Path(args.calibration_dir).expanduser().resolve()
    for protected in (HERE,repo,calibration):
        if root==protected or root in protected.parents or protected in root.parents:
            raise ValueError('Run must be separate from source, repository and calibration')
    identity=verify_calibration(calibration)
    root.mkdir(parents=True,exist_ok=True)
    with (root/'RUNNING.lock').open('a+') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another coordinator owns this run') from None
        hashes=source_hashes(HERE);protocol=d.protocol(hashes)
        if (root/'protocol.json').exists() and read(root/'protocol.json')!=protocol:
            raise RuntimeError('Resume source/protocol mismatch')
        put(root/'protocol.json',protocol);put(root/'CALIBRATION_BINDING_VERIFIED.json',identity)
        (root/'source').mkdir(exist_ok=True)
        for name,want in hashes.items():
            dest=root/'source'/name
            if not dest.exists():shutil.copy2(HERE/name,dest)
            if sha(dest)!=want:raise RuntimeError('Saved source differs: '+name)
        try:
            put(root/'environment.json',check_environment(repo))
            for name in ('q16_tests.py','q16_stress_tests.py','q16_calibration_tests.py','q16_fresh_tests.py'):
                result=subprocess.run([sys.executable,'-u',str(HERE/name)],cwd=HERE,capture_output=True,text=True)
                log=result.stdout+result.stderr;(root/(name+'-'+str(time.time_ns())+'.log')).write_text(log)
                print(log,flush=True)
                if result.returncode:raise RuntimeError('Offline suite failed before fresh simulations')
            execute_stage(root,repo,'planning',args.workers,hashes)
            records=replay_stage(root,'planning',hashes)
            from q16_fresh_analysis import summarize,text_report
            summary=normalize(summarize(records));put(root/'planning_summary.json',summary)
            (root/'PLANNING_REPORT.txt').write_text(text_report(summary))
            put(root/'COMPLETE.json',dict(status=summary['status'],revision=d.REVISION,source_sha256=hashes,
                protocol_sha256=sha(root/'protocol.json'),summary_sha256=sha(root/'planning_summary.json'),
                report_sha256=sha(root/'PLANNING_REPORT.txt'),note='Fixed-policy nuisance planning only; no confirmatory claim.'))
            print('Planning complete:',summary['status'],'| 192 streams',flush=True)
        except BaseException as error:
            put(root/('STOPPED-'+str(time.time_ns())+'.json'),dict(error=repr(error),traceback=traceback.format_exc()))
            raise

def audit(args):
    root=args.run_dir.expanduser().resolve();hashes=source_hashes(HERE)
    if read(root/'protocol.json')!=d.protocol(hashes): raise ValueError('Frozen source/protocol differs')
    if source_hashes(root/'source')!=hashes:raise ValueError('Archived source differs')
    records=replay_stage(root,'planning',hashes)
    from q16_fresh_analysis import summarize,text_report
    summary=normalize(summarize(records))
    if summary!=read(root/'planning_summary.json'): raise ValueError('Rebuilt summary differs')
    complete=read(root/'COMPLETE.json')
    if complete['source_sha256']!=hashes or complete['status']!=summary['status']:raise ValueError('Completion differs')
    for field,name in [('protocol','protocol.json'),('summary','planning_summary.json'),('report','PLANNING_REPORT.txt')]:
        if complete[field+'_sha256']!=sha(root/name): raise ValueError('Receipt hash differs: '+name)
    if (root/'PLANNING_REPORT.txt').read_text()!=text_report(summary):raise ValueError('Report differs')
    print('Independent fresh planning audit: PASS | 192 raw streams',flush=True)

def export(args):
    from q16_io import export_full
    import zipfile
    root=args.run_dir.expanduser().resolve();output=args.output_dir.expanduser().resolve()
    full=export_full(root,output)
    review=output/('P1_Qoala_Q16_'+root.name+'_review.zip');temporary=review.with_name(review.name+'.partial')
    included=[];omitted=[]
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for p in sorted(root.rglob('*')):
                if not p.is_file() or p.name=='RUNNING.lock':continue
                if p.is_symlink():raise ValueError('Symlink in run')
                relative=str(p.relative_to(root));record=dict(path=relative,bytes=p.stat().st_size,sha256=sha(p))
                if p.suffix=='.gz':omitted.append(record);continue
                z.write(p,root.name+'/'+relative);included.append(record)
            z.writestr('REVIEW_MANIFEST.json',json.dumps(dict(kind='compact review; raw evidence is in full ZIP',
                full_zip=full.name,full_zip_sha256=sha(full),included_files=included,omitted_files=omitted),indent=2))
        os.replace(temporary,review)
    finally:
        if temporary.exists():temporary.unlink()
    print('Review ZIP:',review,'(%.1f MiB)'%(review.stat().st_size/2**20),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    r=sub.add_parser('run');r.add_argument('--repo',type=Path,required=True);r.add_argument('--run-dir',type=Path,required=True)
    r.add_argument('--calibration-dir',type=Path,required=True);r.add_argument('--workers',type=int,default=8)
    w=sub.add_parser('worker')
    for name in ('repo','job','raw','compact','statistics'):w.add_argument('--'+name,type=Path,required=True)
    a=sub.add_parser('audit');a.add_argument('--run-dir',type=Path,required=True)
    e=sub.add_parser('export');e.add_argument('--run-dir',type=Path,required=True);e.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args();{'run':run,'worker':worker,'audit':audit,'export':export}[args.command](args)

