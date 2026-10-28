"""P1 E1: marginal value of per-campaign fixed-phase self-calibration.

Reuses the pinned Q12 evaluator without changing its sources, sim configuration,
or original calibration/evaluation results. The adversary uses only its own
calibration observations. NEW victim measurements use a distinct seed root.

Requires the original Q12 run directory and the installed, unmodified Q12 code.
No parent files are written. Results are immutable and resumable.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict

HERE = Path(__file__).resolve().parent
STOP = threading.Event()
VERSION = 'p1-e1-calibration-cost-v1'
PARENT_HASH = '470078ab4b2c014d42b97dbba496934a0195ae13a58d3404274c06b8e5b3b7b4'
Q12_PIN = '31fa2c9699578d227736557f35cd01a134c4bbde'
CAMPAIGNS = 32
SHOTS = 256
BATCH = 128
PILOT_CAMPAIGNS = 8
PILOT_SHOTS = 16
PILOT_NOISE_FREE_SHOTS = 8
NEW_PILOT_ROOT = 27012601
NEW_EVAL_ROOT = 27012602
BOOTSTRAP_ROOT = 27012603
ARMS = (
    'q12_full256', 'low08', 'low32', 'low64', 'global_loo',
    'fixed_0ms', 'fixed_2ms', 'random_phase', 'no_competitor'
)
PHASE_POLICIES = set(ARMS)-{'random_phase','no_competitor'}
PRIMARY_COMPARATORS = ('global_loo','fixed_2ms')


def jdump(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(obj):
    return hashlib.sha256(jdump(obj).encode()).hexdigest()


def filehash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for buf in iter(lambda:fh.read(1024*1024), b''):
            h.update(buf)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def immutable(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if read(path) != obj:
            raise RuntimeError('Existing immutable metadata does not match: '+str(path))
        return
    tmp = path.with_name(path.name+'.tmp-'+str(os.getpid()))
    with tmp.open('w') as fh:
        json.dump(obj,fh,indent=2,sort_keys=True,allow_nan=False)
        fh.write('\n')
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp,path)


def source_dir(project, path_override=None):
    path = Path(path_override) if path_override else Path(project)/'experiment_code'/('q12-'+PARENT_HASH[:16])
    path = path.expanduser().resolve()
    if not path.is_dir():
        raise RuntimeError('Installed Q12 source missing at '+str(path)+'. Run Q12 setup first.')
    sys.path.insert(0,str(path)) if str(path) not in sys.path else None
    import q12_design as d
    import q12 as original
    assert d.PIN == Q12_PIN and d.VERSION == 'q12-selfcal-1'
    codehashes = original.code_hashes()
    return path, d, original, codehashes


def parent_identify(parent, codehashes):
    parent = Path(parent).expanduser().resolve()
    protocol = read(parent/'protocol.json')
    completed = read(parent/'COMPLETE.json')
    calibration = read(parent/'CALIBRATION_COMPLETE.json')
    frozen = read(parent/'frozen_selections.json')
    assert protocol['version']=='q12-selfcal-1' and protocol['repo_pin']==Q12_PIN
    assert protocol['code_hashes']==codehashes
    assert completed['code_hashes']==codehashes
    assert completed['status'].startswith('Execution complete')
    assert filehash(parent/'frozen_selections.json')==calibration['selections_sha256']
    assert filehash(parent/'frozen_selections.json')==completed['frozen_selections_sha256']
    assert filehash(parent/'calibration'/'manifest.json')==calibration['calibration_manifest_sha256']
    assert len(frozen)==64 and all('teleport|'+str(i) in frozen for i in range(CAMPAIGNS))
    return dict(q12_run_dir=str(parent),parent_protocol_sha256=filehash(parent/'protocol.json'),
                calibration_manifest_sha256=filehash(parent/'calibration'/'manifest.json'),
                frozen_selections_sha256=filehash(parent/'frozen_selections.json'),
                q12_complete_sha256=filehash(parent/'COMPLETE.json'),q12_source_hashes=codehashes),frozen


def owner_phase_observations(parent, d, original):
    """Read verified Q12 own-observation projections only, never target evaluation."""
    observations=defaultdict(dict)
    count=0
    phase_labels={'phase_%02d'%i for i in range(16)}
    batches=sorted((Path(parent)/'calibration').glob('batch-*/completed.json'))
    assert len(batches)==2304, 'Unexpected Q12 calibration checkpoint count'
    for marker_path in batches:
        folder=marker_path.parent
        job=read(folder/'job.json')
        # Never read Q12 victim evaluation, raw simulator traces, or any other stage.
        specs=job['specs']
        if all(s['workload']!='teleport' for s in specs):
            continue
        assert all(s['stage']=='calibration' and s['workload']=='teleport' for s in specs)
        assert job['code_hashes']==original.code_hashes()
        marker=read(marker_path)
        assert marker['job_hash']==d.stable(job)
        cp=folder/marker['compact_file']
        assert filehash(cp)==marker['compact_sha256']
        with gzip.open(cp,'rt') as fh:
            for text in fh:
                rec=json.loads(text)
                obs=rec['own_observation']
                # Only the attacker's returned bit and candidate/trial labels survive.
                assert obs==d.calibration_observation(dict(spec=rec['spec'],inputs={'target_owner':'attacker_calibration'},
                    completed=not bool(rec['unfinished']),wrong_output=int(rec['wrong_output'])))
                if obs['candidate'] not in phase_labels:
                    continue
                assert obs['completed'] and obs['returned_bit'] in (0,1)
                c, label, shot=obs['campaign'],obs['candidate'],obs['shot']
                assert 0<=c<CAMPAIGNS and 0<=shot<256
                k=(c,label)
                assert shot not in observations[k],('duplicate calibration observation',k,shot)
                observations[k][shot]=int(obs['returned_bit'])
                count+=1
    assert count==CAMPAIGNS*16*256,('calibration phase records',count)
    for c in range(CAMPAIGNS):
        for i in range(16):
            assert set(observations[c,'phase_%02d'%i])==set(range(256))
    return observations


def build_policies(observations, q12_frozen, d):
    """Policy ranking never sees target evaluation outcomes or simulator traces."""
    all_scores={}
    phases=list(d.PHASES_NS)
    for c in range(CAMPAIGNS):
        for k in (8,32,64,256):
            scores=[sum(observations[c,'phase_%02d'%i][s] for s in range(k)) for i in range(16)]
            winning=min(range(16),key=lambda i:(-scores[i],i))
            all_scores[c,k]=(phases[winning],winning,max(scores))
        assert all_scores[c,256][0]==q12_frozen['teleport|'+str(c)]['phase_ns'], 'Q12 original frozen phase mismatch'
    policy={}
    for c in range(CAMPAIGNS):
        transfer=[]
        for i in range(16):
            transfer.append(sum(sum(observations[other,'phase_%02d'%i].values())
                                for other in range(CAMPAIGNS) if other!=c))
        best=min(range(16),key=lambda i:(-transfer[i],i))
        policy[str(c)]={
           'q12_full256':all_scores[c,256][0],
           'low08':all_scores[c,8][0],
           'low32':all_scores[c,32][0],
           'low64':all_scores[c,64][0],
           'global_loo':phases[best],
           'fixed_0ms':0,
           'fixed_2ms':2000000,
           'random_phase':None,
           'no_competitor':None,
        }
    assert len(policy)==CAMPAIGNS and all(set(v)==set(ARMS) for v in policy.values())
    assert all(v in phases for row in policy.values() for key,v in row.items() if key in PHASE_POLICIES)
    return policy


def iter_tasks(stage):
    if stage=='pilot':
        for c in range(PILOT_CAMPAIGNS):
            for arm in ARMS:
                for i in range(PILOT_SHOTS):
                    yield dict(campaign=c, policy=arm, shot=i,
                               noise='noise_free' if i<PILOT_NOISE_FREE_SHOTS else 'dephasing',
                               fixed_state=i%6 if i<PILOT_NOISE_FREE_SHOTS else None)
    elif stage=='evaluation':
        for c in range(CAMPAIGNS):
            for arm in ARMS:
                for i in range(SHOTS):
                    yield dict(campaign=c,policy=arm,shot=i,noise='dephasing',fixed_state=None)
    else:
        raise ValueError(stage)


def task_chunks(stage):
    tasks=list(iter_tasks(stage))
    return [tasks[i:i+BATCH] for i in range(0,len(tasks),BATCH)]


def counts():
    return dict(pilot=PILOT_CAMPAIGNS*len(ARMS)*PILOT_SHOTS,
                evaluation=CAMPAIGNS*len(ARMS)*SHOTS,eval_batches=len(task_chunks('evaluation')),
                arms=list(ARMS),campaigns=CAMPAIGNS,shots_per_arm_campaign=SHOTS)


def frozen_protocol(parent_identity,policy):
    return dict(version=VERSION,q12=parent_identity, policies_sha256=digest(policy),
                seed_root_pilot=NEW_PILOT_ROOT,seed_root_evaluation=NEW_EVAL_ROOT,
                bootstrap_root=BOOTSTRAP_ROOT,phase_units='nanoseconds',
                design='Frozen Q12 16-phase self-calibration (256 own trials per phase) versus budget-limited and shared fixed phases',
                scope='Teleportation, FCFS, T2=4 ms dephasing, +/-0.5 ms supplied epoch uncertainty, four classical jobs totaling 1.2 ms',
                budgets_per_phase={'q12_full256':256,'low08':8,'low32':32,'low64':64},
                global_loo='Training on all other 31 campaigns own calibration, excludes held-out campaign and all victim outcomes; amortized not zero total training cost',
                fixed_2ms='Previously considered fixed phase; NOT genuinely preregistered from scratch',
                primary_comparators=list(PRIMARY_COMPARATORS),
                primary_endpoint='wrong verification result among all offered executions, reporting unfinished separately',
                analysis='Paired 32-campaign bootstrap CI; Bonferroni adjustment for 2 primary contrasts; all other contrasts exploratory',
                counts=counts(),source_sha256=filehash(HERE/'p1_e1.py'))


def installed_original(source):
    p=Path(source).resolve()
    if str(p) not in sys.path:sys.path.insert(0,str(p))
    import q12_design as d
    import q12_engine as engine
    import q12 as original
    import q6_reference as q6
    assert d.PIN==Q12_PIN
    return d,engine,original,q6


def worker(args):
    job=read(args.job)
    d,engine,original,q6=installed_original(job['q12_source_dir'])
    assert original.code_hashes()==job['q12_code_hashes']
    assert filehash(HERE/'p1_e1.py')==job['e1_code_hash']
    env=q6.check_repo(args.repo)
    assert job['stage'] in ('pilot','evaluation')
    d.ROOTS['evaluation']=NEW_PILOT_ROOT if job['stage']=='pilot' else NEW_EVAL_ROOT
    started=time.monotonic(); started_cpu=time.process_time()
    spec=None; current=None
    try:
        with gzip.open(args.raw,'xt',compresslevel=3) as raw, gzip.open(args.compact,'xt',compresslevel=3) as compact:
            for i,t in enumerate(job['tasks']):
                c=t['campaign']; arm=t['policy']
                selected=job['phases'][str(c)][arm]
                basearm='random_phase' if arm=='random_phase' else 'no_competitor' if arm=='no_competitor' else 'learned'
                spec=d.specification('evaluation','teleport',c,'primary_0500us',basearm,t['shot'])
                spec['noise']=t['noise']
                if t['fixed_state'] is not None:spec['fixed_state']=t['fixed_state']
                frozen={'teleport|'+str(c):{'phase_ns':selected}} if selected is not None else {}
                current=engine.run(spec,frozen)
                checked=engine.validate(current,spec,frozen)
                assert checked['competitor_service_ns']==(0 if arm=='no_competitor' else d.BURST_SERVICE_NS)
                assert checked['selected_phase_ns']==selected
                if t['noise']=='noise_free':
                    assert checked['wrong_output']==0 and checked['unfinished']==0
                checked['e1_policy']=arm
                checked['e1_task']=t
                raw.write(jdump(dict(policy=arm, task=t, q12_raw=current))+'\n')
                compact.write(jdump(checked)+'\n')
                if (i+1)%32==0:print('executed',i+1,'/',len(job['tasks']),flush=True)
    except BaseException:
        if spec is not None:immutable(args.raw.with_name(args.raw.name+'.failed-spec.json'),spec)
        if current is not None:immutable(args.raw.with_name(args.raw.name+'.failed-record.json'),current)
        raise
    immutable(args.stats,dict(trials=len(job['tasks']),wall_seconds=time.monotonic()-started,
                   cpu_seconds=time.process_time()-started_cpu,
                   peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                   validation='Q12 original raw + compact validation passed',environment=env))


def checkpoint(folder, job):
    m=read(folder/'complete.json')
    assert m['job_hash']==digest(job)
    for typ in ('raw','compact','stats'):
        assert Path(m[typ+'_file']).name==m[typ+'_file']
        assert filehash(folder/m[typ+'_file'])==m[typ+'_sha256']
    stats=read(folder/m['stats_file'])
    assert stats['trials']==len(job['tasks'])
    with gzip.open(folder/m['compact_file'],'rt') as f: rows=[json.loads(x) for x in f if x.strip()]
    assert len(rows)==len(job['tasks'])
    assert [r['e1_task'] for r in rows]==job['tasks']
    return rows,stats,m


def execute_stage(stage, root, repo, source, protocol, policies, workers):
    chunks=task_chunks(stage)
    total=sum(len(chunk) for chunk in chunks)
    print(f'{stage}: {total:,} independent simulator executions in {len(chunks)} resumable 128-case batches; workers={workers}',flush=True)
    completed={}
    start=time.monotonic()
    def one(i, tasks):
        if STOP.is_set():raise RuntimeError('Coordinator stopping')
        folder=root/stage/('batch-%05d'%i)
        ckeys=sorted({str(t['campaign']) for t in tasks},key=int)
        phases={key:policies[key] for key in ckeys}
        job=dict(stage=stage,tasks=tasks,phases=phases,
                 q12_source_dir=str(source),q12_code_hashes=protocol['q12']['q12_source_hashes'],
                 e1_code_hash=protocol['source_sha256'],run_protocol_sha256=digest(protocol))
        immutable(folder/'job.json',job)
        reused=(folder/'complete.json').exists()
        if not reused:
            stamp=str(time.time_ns()); raw=folder/(stamp+'.raw.jsonl.gz')
            compact=folder/(stamp+'.compact.jsonl.gz');stats=folder/(stamp+'.stats.json'); log=folder/(stamp+'.log')
            with log.open('w') as fout:
                cmd=[sys.executable,'-u',str(HERE/'p1_e1.py'),'worker','--job',str(folder/'job.json'),
                     '--repo',str(repo),'--raw',str(raw),'--compact',str(compact),'--stats',str(stats)]
                p=subprocess.Popen(cmd,stdout=fout,stderr=subprocess.STDOUT)
                while p.poll() is None:
                    if STOP.is_set():
                        p.terminate();p.wait()
                        raise RuntimeError('Coordinator stopping')
                    time.sleep(.3)
                if p.returncode:
                    raise RuntimeError('Worker failed: '+str(log)+'\n'+log.read_text()[-4000:])
            immutable(folder/'complete.json',dict(job_hash=digest(job),
                       raw_file=raw.name,raw_sha256=filehash(raw),
                       compact_file=compact.name,compact_sha256=filehash(compact),
                       stats_file=stats.name,stats_sha256=filehash(stats)))
        rows,stats,marker=checkpoint(folder,job)
        return i,rows,stats,marker,reused
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(one,i,tasks) for i,tasks in enumerate(chunks)]
        try:
            for f in concurrent.futures.as_completed(futures):
                idx, rows, stats, marker, reused=f.result()
                completed[idx]=(stats,marker,reused)
                n=sum(len(chunks[i]) for i in completed)
                if len(completed)<=workers or len(completed)%8==0 or len(completed)==len(chunks):
                    elapsed=time.monotonic()-start
                    eta=elapsed*(total-n)/max(1,n)/60
                    print(f'{stage}: {n:,}/{total:,}; {"reused" if reused else "new"}; RAM {stats["peak_rss_mib"]:.0f} MiB/worker; rough ETA {eta:.1f} min',flush=True)
        except BaseException:
            STOP.set()
            for f in futures:f.cancel()
            raise
    manifest=[dict(index=i,**completed[i][1],stats=completed[i][0]) for i in range(len(chunks))]
    immutable(root/stage/'manifest.json',manifest)
    resources=dict(trials=total,batches=len(chunks),worker_cpu_s=sum(m['stats']['cpu_seconds'] for m in manifest),
                   worker_wall_s=sum(m['stats']['wall_seconds'] for m in manifest),
                   max_worker_ram_mib=max(m['stats']['peak_rss_mib'] for m in manifest),
                   real_wall_s=time.monotonic()-start,
                   stage_manifest_sha256=filehash(root/stage/'manifest.json'))
    immutable(root/stage/'resources.json',resources)
    return resources


def load_stage(root, stage, policies):
    manifest=read(root/stage/'manifest.json')
    tasks=task_chunks(stage)
    assert len(manifest)==len(tasks)
    for i,batch in enumerate(tasks):
        folder=root/stage/('batch-%05d'%i)
        job=read(folder/'job.json')
        assert job['tasks']==batch
        assert job['phases']=={str(k):policies[str(k)] for k in sorted({t['campaign'] for t in batch})}
        rows,_,_=checkpoint(folder,job)
        for r in rows:yield r


def analyze(root, policies):
    import numpy as np
    groups=defaultdict(dict)
    for r in load_stage(root,'evaluation',policies):
        t=r['e1_task']; key=(t['campaign'],t['policy']);shot=t['shot']
        assert shot not in groups[key]
        groups[key][shot]=r
    assert len(groups)==CAMPAIGNS*len(ARMS)
    summaries=[]
    for arm in ARMS:
        rr=[groups[c,arm][i] for c in range(CAMPAIGNS) for i in range(SHOTS)]
        assert len(rr)==CAMPAIGNS*SHOTS
        durations=[(r['target_finished_ns']-r['target_request_ns'])/1e6 for r in rr if r['target_finished_ns'] is not None]
        storage=[r['physical_storage_ns']/1e6 for r in rr if r['physical_storage_ns'] is not None]
        summaries.append(dict(policy=arm,offered=len(rr),wrong_output=sum(r['wrong_output'] for r in rr),
                       wrong_fraction=sum(r['wrong_output'] for r in rr)/len(rr),
                       incomplete=sum(r['unfinished'] for r in rr),
                       correct_completed=sum((1-r['wrong_output'])*(1-r['unfinished']) for r in rr),
                       mean_completed_latency_ms=float(np.mean(durations)) if durations else None,
                       mean_storage_ms=float(np.mean(storage)) if storage else None))
    pairs=[]
    for c in range(CAMPAIGNS):
        for arm in ARMS:
            assert set(groups[c,arm])==set(range(SHOTS))
        for i in range(SHOTS):
            baseline=groups[c,ARMS[0]][i]
            for arm in ARMS[1:]:
                r=groups[c,arm][i]
                assert r['state']==baseline['state']
                assert r['target_request_ns']==baseline['target_request_ns']
                assert r['competitor_service_ns']==(0 if arm=='no_competitor' else baseline['competitor_service_ns'])
    rng=np.random.default_rng(BOOTSTRAP_ROOT)
    boots=rng.integers(0,CAMPAIGNS,size=(20000,CAMPAIGNS))
    for arm in ARMS[1:]:
        delta=np.asarray([sum(groups[c,'q12_full256'][i]['wrong_output']-groups[c,arm][i]['wrong_output']
                            for i in range(SHOTS))/SHOTS for c in range(CAMPAIGNS)])
        primary=arm in PRIMARY_COMPARATORS
        m=len(PRIMARY_COMPARATORS) if primary else 1
        draws=delta[boots].mean(axis=1)
        tail=.05/(2*m)
        pairs.append(dict(contrast='q12_full256 minus '+arm,primary=primary,
                          campaign_mean_difference_pp=100*float(delta.mean()),
                          interval_pp=(100*np.quantile(draws,[tail,1-tail])).tolist(),
                          bootstrap_interval='Bonferroni-adjusted for two primary comparisons' if primary else 'Exploratory pointwise',
                          positive_campaigns=int((delta>0).sum()),negative_campaigns=int((delta<0).sum()),
                          tied_campaigns=int((delta==0).sum()),all_compared_completed=all(
                              groups[c,a][i]['unfinished']==0 for a in ('q12_full256',arm)
                              for c in range(CAMPAIGNS) for i in range(SHOTS))))
    return dict(version=VERSION,protocol_sha256=filehash(root/'protocol.json'),
                policies_sha256=filehash(root/'frozen_policies.json'),
                summaries=summaries,contrasts=pairs,
                limitation='Fresh simulation, not hardware. Paired campaign bootstrap conditional on frozen policies, not uncertainty in calibration selection. Global LOO draws on other campaigns own data. Fixed2 selected with prior timing knowledge.')


def run(args):
    from pathlib import Path
    project=Path(args.project).expanduser().resolve()
    code,d,original,codehashes=source_dir(project,args.q12_code)
    parent=Path(args.q12_run).expanduser().resolve()
    identity,full=parent_identify(parent,codehashes)
    repo=Path(args.repo).expanduser().resolve()
    # No Q12 or Qoala files may be modified by E1.
    root=Path(args.run_dir).expanduser().resolve()
    if root==parent or parent in root.parents or root==repo or repo in root.parents or root==code or code in root.parents:
        raise RuntimeError('E1 run directory must not be nested inside Q12 results or Qoala source')
    root.mkdir(parents=True,exist_ok=True)
    with (root/'RUNNING.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another E1 coordinator is running in this directory')
        _,_,_,q6=installed_original(code)
        env=q6.check_repo(repo)
        observations=owner_phase_observations(parent,d,original)
        policies=build_policies(observations,full,d)
        protocol=frozen_protocol(identity,policies)
        immutable(root/'protocol.json',protocol)
        immutable(root/'frozen_policies.json',policies)
        immutable(root/'environment.json',env)
        print('RUN DIRECTORY:',root,flush=True)
        print('Calibration source (read-only):',parent,flush=True)
        print('Frozen policies:',filehash(root/'frozen_policies.json'),flush=True)
        pilot=execute_stage('pilot',root,repo,code,protocol,policies,args.workers)
        all_pilot=list(load_stage(root,'pilot',policies))
        assert len(all_pilot)==counts()['pilot']
        assert all(r['wrong_output']==r['unfinished']==0 for r in all_pilot if r['e1_task']['noise']=='noise_free')
        assert {(r['e1_policy'],r['state']) for r in all_pilot if r['e1_task']['noise']=='noise_free'}=={(a,s) for a in ARMS for s in range(6)}
        immutable(root/'PILOT_PASSED.json',dict(trials=len(all_pilot),pilot_resources=pilot,
                    test='noise-free correctness, original Q12 full raw chronology validation, actual competitor service, phase select and paired inputs'))
        print('E1 PILOT PASSED. Peak reported worker RAM',pilot['max_worker_ram_mib'],'MiB',flush=True)
        # Pilot includes noise-free controls and process overhead: this is a planning
        # extrapolation only, not a benchmark of the full, noisy evaluation.
        rough_minutes=pilot['worker_wall_s']/pilot['trials']*counts()['evaluation']/max(1,args.workers)/60
        print(f'Pilot-throughput extrapolation for full stage: ~{rough_minutes:.1f} min; noisy workload, startup and contention may differ.',flush=True)
        if args.mode=='pilot':
            print('Pilot finished. To continue, set MODE="full" using this same run directory.',flush=True)
            return
        evaluation=execute_stage('evaluation',root,repo,code,protocol,policies,args.workers)
        results=analyze(root,policies)
        immutable(root/'analysis.json',results)
        immutable(root/'COMPLETE.json',dict(protocol_sha256=filehash(root/'protocol.json'),
           analysis_sha256=filehash(root/'analysis.json'),evaluation_resources=evaluation,
           status='All E1 simulations completed and internally validated; independent raw audit remains required'))
        print('E1 COMPLETE. Contrasts (percentage points):',flush=True)
        for c in results['contrasts']:
            print(json.dumps(c),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['run','worker'])
    parser.add_argument('--project',type=Path)
    parser.add_argument('--q12-run',type=Path)
    parser.add_argument('--q12-code',type=Path,default=None)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path)
    parser.add_argument('--mode',choices=['pilot','full'],default='pilot')
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--job',type=Path)
    parser.add_argument('--raw',type=Path)
    parser.add_argument('--compact',type=Path)
    parser.add_argument('--stats',type=Path)
    args=parser.parse_args()
    if args.workers<1:parser.error('workers must be positive')
    if args.command=='worker':worker(args)
    else:
        if not all((args.project,args.q12_run,args.run_dir)):
            parser.error('run requires --project --q12-run --run-dir')
        def interrupted(sig,frame):
            STOP.set()
            raise KeyboardInterrupt('Coordinator stopped; committed checkpoints remain reusable')
        signal.signal(signal.SIGINT,interrupted)
        signal.signal(signal.SIGTERM,interrupted)
        run(args)


if __name__=='__main__':main()
