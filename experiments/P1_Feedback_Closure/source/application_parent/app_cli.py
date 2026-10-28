"""RQ1/RQ3 application comparison coordinator. CLI is authoritative; notebook is a launcher."""
import argparse
import concurrent.futures
import csv
import json
import hashlib
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

import app_design as d
from app_io import read, put, sha, normalize, write_raw, read_raw, source_hashes

HERE = Path(__file__).resolve().parent
STOP = threading.Event()


from app_environment import check_environment, parser_preflight


def worker(args):
    assert __debug__, 'Run without -O'
    job = read(args.job)
    if job['source_sha256']!=source_hashes(HERE):
        raise RuntimeError('Worker source differs from frozen job')
    environment = check_environment(args.repo)
    environment['parser_preflight'] = parser_preflight()
    import app_native as engine
    from app_audit import validate_record
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
            validation='Independent application raw replay passed',environment=environment))
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


def execute_stage(root,repo,stage,workers,hashes,specs):
    specs=list(specs)
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
            command = [sys.executable,'-u',str(HERE/'app_cli.py'),'worker','--repo',str(repo),
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
                    peak='unknown' if stats.get('peak_rss_mib') is None else '%.0f MiB'%stats['peak_rss_mib']
                    print('%s: %d/%d verified | stream %d %s | elapsed %.1f min | worker peak %s'%(
                        stage,count,len(specs),index,'reused' if reused else 'finished',
                        (time.monotonic()-begun)/60,peak),flush=True)
        except BaseException:
            STOP.set()
            for future in futures:
                future.cancel()
            raise
    manifest = [completed[i] for i in range(len(specs))]
    put(root/stage/'manifest.json',manifest)


def replay_stage(root,stage,hashes,specs):
    from app_audit import validate_record
    records = []
    manifest=[]
    for index,spec in enumerate(specs):
        folder = root/stage/('stream-%04d'%index)
        saved,stats,marker = verified_checkpoint(folder,dict(spec=spec,source_sha256=hashes))
        rebuilt = validate_record(read_raw(folder/marker['raw_file']),spec)
        if rebuilt!=saved:
            raise RuntimeError('Raw-to-compact replay differs: '+str(folder))
        records.append(rebuilt)
        manifest.append(dict(folder='stream-%04d'%index,**marker))
        if (index+1)%16==0:
            print('Raw replay:',stage,index+1,'streams verified',flush=True)
    if read(root/stage/'manifest.json')!=manifest:
        raise ValueError('Stage manifest differs from committed checkpoints')
    return records

def smoke_specs():
    return [dict(stage='smoke',stream=0,profile='ideal',cost_ns=10000,variant=v,policy=p,
        arm='selected',phase_ns=250000) for v in d.VARIANTS for p in d.POLICIES]


def stages(root,mode):
    yield 'preflight',list(d.preflight_specs())
    yield 'smoke',smoke_specs()
    yield 'discovery',list(d.discovery_specs(mode))
    if (root/'FROZEN_SELECTION.json').is_file():
        phase=read(root/'FROZEN_SELECTION.json')['selected_phase_ns']
        yield 'evaluation',list(d.evaluation_specs(mode,phase))


def write_summary(root,summary):
    put(root/'application_summary.json',summary)
    fields=['profile','cost_ns','variant','policy','arm','streams','offered','correct_on_time',
        'wrong_on_time','late','unfinished','on_time','infidelity_lower','infidelity_upper',
        'missing_density','benign_unfinished','benign_p95_ns','max_live_owners']
    with (root/'arm_outcomes.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(summary['arms'])
    report=['RQ1/RQ3 ROTATION APPLICATION COMPARISON',
        'Mode: '+summary['mode'],
        'Main native simulations: '+str(summary['simulations']),
        'Measured quantum requests: '+str(summary['quantum_requests']),
        'Equal completed attacker work: '+str(summary['equal_executed_attacker_work_verified']),
        '',summary['scientific_interpretation'],
        'No claim that these assumed CPU latencies were physically measured.',
        'No Q16 sample size, deadline, policy or evidence was changed.',
        'Noisy physics scope: '+summary['noisy_physics_audit'],
        '', 'See arm_outcomes.csv and application_summary.json for full outcomes,',
        'stream-paired primary contrasts, secondary comparisons and service screens.']
    (root/'APPLICATION_REPORT.txt').write_text('\n'.join(report)+'\n')


def run(args):
    if not __debug__:raise RuntimeError('Run without -O')
    if not 1<=args.workers<=8:raise ValueError('Workers must be 1 through 8')
    root,repo=args.run_dir.expanduser().resolve(),args.repo.expanduser().resolve()
    if not root.name.startswith('rq13-app-'):raise ValueError('Use a separate rq13-app-* directory')
    if any(part.startswith(('q12-','q13-','q14-','q15-','q16-','rq1-native-workload-','rq3-baseline-')) for part in root.parts):
        raise ValueError('Do not nest new results inside an earlier experiment')
    for protected in (HERE,repo):
        if root==protected or root in protected.parents or protected in root.parents:raise ValueError('Separate results, source, and Qoala checkout')
    root.mkdir(parents=True,exist_ok=True)
    with (root/'RUNNING.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another coordinator owns this run') from None
        hashes=source_hashes(HERE);protocol=d.protocol(args.mode,hashes)
        if (root/'protocol.json').exists() and read(root/'protocol.json')!=normalize(protocol):raise ValueError('Resume source/protocol/mode differs')
        put(root/'protocol.json',protocol);(root/'source').mkdir(exist_ok=True)
        for name,want in hashes.items():
            dest=root/'source'/name
            if not dest.exists():shutil.copy2(HERE/name,dest)
            if sha(dest)!=want:raise ValueError('Archived source changed: '+name)
        try:
            env=check_environment(repo)
            put(root/('environment-'+str(time.time_ns())+'.json'),env)
            put(root/'NATIVE_PARSER_PREFLIGHT.json',parser_preflight())
            from app_analysis import freeze_selection,summarize
            for stage,specs in [('preflight',list(d.preflight_specs())),('smoke',smoke_specs()),('discovery',list(d.discovery_specs(args.mode)))]:
                execute_stage(root,repo,stage,args.workers,hashes,specs)
                records=replay_stage(root,stage,hashes,specs)
                if stage in ('preflight','smoke') and any(not r['all_jobs_completed'] for r in records):
                    raise ValueError('Native application/policy controls did not finish; later stages are blocked')
                if stage=='discovery':
                    selection=normalize(freeze_selection(records,args.mode))
                    frozen=root/'FROZEN_SELECTION.json'
                    if frozen.exists() and read(frozen)!=selection:raise ValueError('Frozen discovery selection differs')
                    put(frozen,selection)
                    print('FROZEN PHASE:',selection['selected_phase_ns'],'ns. Evaluation has not influenced selection.',flush=True)
            phase=selection['selected_phase_ns'];specs=list(d.evaluation_specs(args.mode,phase))
            execute_stage(root,repo,'evaluation',args.workers,hashes,specs)
            records=replay_stage(root,'evaluation',hashes,specs)
            summary=normalize(summarize(records,args.mode,phase));write_summary(root,summary)
            put(root/'COMPLETE.json',dict(status='COMPLETE_AUDITED_APPLICATION_STUDY',mode=args.mode,
                protocol_sha256=sha(root/'protocol.json'),selection_sha256=sha(root/'FROZEN_SELECTION.json'),
                summary_sha256=sha(root/'application_summary.json'),source_sha256=hashes))
            print('\n'+(root/'APPLICATION_REPORT.txt').read_text(),flush=True)
        except BaseException:
            (root/('failure-'+str(time.time_ns())+'.txt')).write_text(traceback.format_exc())
            raise


def audit(args):
    root=args.run_dir.expanduser().resolve();hashes=source_hashes(HERE)
    protocol=read(root/'protocol.json');mode=protocol['mode']
    if protocol!=normalize(d.protocol(mode,hashes)):raise ValueError('Audit protocol/source identity differs')
    for name,want in hashes.items():
        if sha(root/'source'/name)!=want:raise ValueError('Archived source snapshot differs: '+name)
    from app_analysis import freeze_selection,summarize
    for stage,specs in stages(root,mode):
        records=replay_stage(root,stage,hashes,specs)
        if stage=='discovery':
            if read(root/'FROZEN_SELECTION.json')!=normalize(freeze_selection(records,mode)):raise ValueError('Discovery replay differs')
        if stage=='evaluation':
            result=normalize(summarize(records,mode,read(root/'FROZEN_SELECTION.json')['selected_phase_ns']))
            if result!=read(root/'application_summary.json'):raise ValueError('Main summary replay differs')
    complete=read(root/'COMPLETE.json')
    for key,name in [('protocol','protocol.json'),('selection','FROZEN_SELECTION.json'),('summary','application_summary.json')]:
        if complete[key+'_sha256']!=sha(root/name):raise ValueError('Completion receipt differs')
    print('Independent application raw audit: PASS',flush=True)


def export(args):
    from app_io import export_full
    import zipfile
    root=args.run_dir.expanduser().resolve();output=args.output_dir.expanduser().resolve()
    full=export_full(root,output)
    target=output/('P1_RQ13_'+root.name+'_full.zip');os.replace(full,target)
    review=output/('P1_RQ13_'+root.name+'_review.zip');temp=review.with_suffix('.zip.partial')
    included=[];omitted=[]
    with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(root.rglob('*')):
            if not p.is_file() or p.name=='RUNNING.lock':continue
            if p.is_symlink():raise ValueError('Unexpected symlink')
            row=dict(path=p.relative_to(root).as_posix(),bytes=p.stat().st_size,sha256=sha(p))
            if p.suffix=='.gz':omitted.append(row);continue
            z.write(p,root.name+'/'+row['path']);included.append(row)
        z.writestr('REVIEW_MANIFEST.json',json.dumps(dict(complete=(root/'COMPLETE.json').is_file(),
            full_zip=target.name,full_zip_sha256=sha(target),included=included,omitted_raw=omitted),indent=2))
    os.replace(temp,review)
    print('FULL ZIP:',target,'\nREVIEW ZIP:',review,flush=True)


if __name__=='__main__':
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[name]='1'
    parser=argparse.ArgumentParser(description='Independent RQ1/RQ3 application study')
    sub=parser.add_subparsers(dest='command',required=True)
    r=sub.add_parser('run');r.add_argument('--repo',required=True,type=Path);r.add_argument('--run-dir',required=True,type=Path)
    r.add_argument('--mode',choices=['pilot','full'],default='full');r.add_argument('--workers',type=int,default=8)
    r.add_argument('--output-dir',type=Path)
    w=sub.add_parser('worker')
    for name in ('repo','job','raw','compact','statistics'):w.add_argument('--'+name,type=Path,required=True)
    a=sub.add_parser('audit');a.add_argument('--run-dir',type=Path,required=True)
    e=sub.add_parser('export');e.add_argument('--run-dir',type=Path,required=True);e.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='run':
        failure=None
        try:run(args)
        except BaseException as exc:failure=exc
        finally:
            if args.output_dir is not None and (args.run_dir/'protocol.json').exists():
                try:export(args)
                except BaseException as exc:
                    traceback.print_exc()
                    if failure is None:failure=exc
        if failure is not None:raise failure
    else:{'worker':worker,'audit':audit,'export':export}[args.command](args)
