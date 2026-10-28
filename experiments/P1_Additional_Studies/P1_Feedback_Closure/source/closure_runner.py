"""Additive fixed-design experiments. Native dependencies are mandatory.

No earlier experiment directory is modified. Raw failures and completed
checkpoints are retained. Smoke results never enter full-study inference.
"""
import argparse
import concurrent.futures as cf
import fcntl
import gzip
import hashlib
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

import closure_design as d
from closure_stats import interval
from public_policy import ARMS


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    def scalar(obj):
        if hasattr(obj,'item'):return obj.item()
        raise TypeError(type(obj).__name__)
    if path.suffix=='.gz':
        with gzip.open(tmp,'wt',encoding='utf-8') as f:json.dump(value,f,sort_keys=True,allow_nan=False,default=scalar)
    else:tmp.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False,default=scalar)+'\n')
    tmp.replace(path)


def read(path):
    path=Path(path)
    if path.suffix=='.gz':
        with gzip.open(path,'rt',encoding='utf-8') as f:return json.load(f)
    return json.loads(path.read_text())


def source_hashes():
    files=[p for p in (d.ROOT/'source').rglob('*') if p.is_file() and p.suffix in ('.py','.json','.txt')]
    files.append(d.ROOT/'evidence/q12_frozen_selections.json')
    return {str(p.relative_to(d.ROOT)):digest(p) for p in sorted(files)}


def preflight(output,repo=None):
    checks={};errors=[]
    for name in ('numpy','netsquid','pydynaa','qoala','netqasm'):
        try:
            mod=importlib.import_module(name)
            checks[name]=dict(path=str(getattr(mod,'__file__','')),version=str(getattr(mod,'__version__','unreported')))
        except Exception as e:errors.append(name+': '+str(e))
    if 'qoala' in checks:
        candidates=[Path(repo).resolve()] if repo else list(Path(checks['qoala']['path']).resolve().parents)
        root=next((p for p in candidates if (p/'.git').exists()),None)
        if root is None:errors.append('Cannot verify Qoala repository revision; supply --repo')
        else:
            head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
            dirty=subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],text=True).strip()
            checks['qoala_git']=dict(root=str(root),head=head,tracked_changes=dirty)
            if head!=d.PIN or dirty:errors.append('Qoala must match the pinned clean tracked source')
    expected=read(d.ROOT/'SOURCE_HASHES.json')
    actual=source_hashes()
    if expected!=actual:errors.append('Packaged source hashes differ')
    receipt=dict(status='PASS' if not errors else 'BLOCKED',errors=errors,checks=checks,
                 python=sys.executable,source_hashes=actual,native_execution_performed=False)
    write(output,receipt);return receipt


def app_specs(index,mode='full'):
    if mode=='smoke' and index==0:
        sys.path.insert(0,str(d.ROOT/'source/application_parent'))
        from app_design import preflight_specs
        yield from preflight_specs()
    for profile,cost,variant in d.APP_CASES:
        for policy in d.POLICIES:
            for arm in d.APP_ARMS:
                yield dict(stage='evaluation',stream=d.SEED_OFFSET+index,profile=profile,cost_ns=cost,
                    variant=variant,policy=policy,arm=arm,phase_ns=250000)


def completed(folder,job):
    key=d.stable(job);p=folder/'checkpoints'/(key+'.json')
    if not p.exists():return None
    receipt=read(p)
    if receipt['job']!=job:raise ValueError('Checkpoint identity mismatch')
    for field in ('raw','compact'):
        if digest(folder/receipt[field])!=receipt[field+'_sha256']:raise ValueError('Checkpoint file changed')
    return receipt


def worker(job,folder):
    folder=Path(folder);key=d.stable(job);started=time.perf_counter();raw=[];compact=[]
    try:
        if job['family']=='timing':
            from closure_native import primary
            from closure_audit import primary as audit
            for i in range(job['start'],job['end']):
                paired=[]
                for arm in ARMS:
                    public,parent=d.public_for(job['workload'],i,arm,job['mode'])
                    r=primary(public,parent);raw.append(r);c=audit(r)
                    c.update(index=i,workload=job['workload']);compact.append(c);paired.append(c)
                if len({(r['state'],r['seed'],r['target_request_ns'],r['campaign']) for r in paired})!=1:
                    raise ValueError('Unmatched private inputs')
        else:
            from closure_native import application
            for spec in app_specs(job['index'],job['mode']):
                r,c=application(spec);raw.append(r);compact.append(c)
            private=[]
            for record in raw:
                if record['spec']['stage']!='evaluation':continue
                private.append(d.stable([{k:v for k,v in r.items() if k not in ('pid','peer_pid')}
                    for r in record['plan'] if r['tenant']!='attacker']))
            if len(set(private))!=1:raise ValueError('Unmatched application offers')
        raw_path='raw/'+key+'.json.gz';compact_path='compact/'+key+'.json.gz'
        write(folder/raw_path,raw);write(folder/compact_path,compact)
        receipt=dict(job=job,raw=raw_path,compact=compact_path,raw_sha256=digest(folder/raw_path),
            compact_sha256=digest(folder/compact_path),native_executions=len(raw),wall_seconds=time.perf_counter()-started,
            status='PASS_NATIVE_RAW_AUDIT')
        write(folder/'checkpoints'/(key+'.json'),receipt);return receipt
    except BaseException:
        # A partial raw record is diagnostic evidence, never a successful job.
        failure=dict(job=job,exception=traceback.format_exc(),completed_raw_records=raw,compact_records=compact)
        if job['family']=='application':
            try:
                from app_native import failure_snapshot
                failure['failed_native_snapshot']=failure_snapshot()
            except Exception:pass
        write(folder/'failures'/(key+'.json.gz'),failure)
        raise


def protocol(family,mode):
    out=d.identity(family,mode);out['source_hashes']=source_hashes()
    out['application_primary_contrasts']=APP_CONTRASTS
    out['frozen_policy_ensemble_sha256']=digest(d.ROOT/'evidence/q12_frozen_selections.json')
    out['missingness']='Retain all offered requests; no confirmatory effect interval if any endpoint is missing'
    out['timing_comparisons']='Learned minus each of 16 fixed phases, random burst, and even spacing, in both workloads'
    out['target_precision']='Application half-width 2 percentage points under a fourfold planning-variance envelope; not a power guarantee'
    return json.loads(json.dumps(out))


# Each tuple fixes case, left policy, left arm, right policy, right arm, endpoint.
APP_CONTRASTS=[
    ['short_memory',10000,'unoptimized','QUOTA_FCFS','selected','QUOTA_FCFS','random','infidelity'],
    ['short_memory',10000,'unoptimized','QUOTA_EDF_NUMERIC','selected','QUOTA_FCFS','selected','correct_timely'],
    ['short_memory',10000,'unoptimized','QUOTA_FCFS','selected','QUOTA_NATIVE_CS','selected','infidelity'],
    ['short_memory',10000,'unoptimized','QUOTA_FCFS','selected','QUOTA_NATIVE_CS','selected','correct_timely'],
    ['short_memory',10000,'unoptimized','QUOTA_PROTECTED','selected','QUOTA_EDF_NUMERIC','selected','correct_timely'],
    ['paper_default',15,'unoptimized','QUOTA_FCFS','selected','QUOTA_FCFS','random','infidelity']]


def run(family,mode,folder,workers,repo=None,smoke=None):
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    p=protocol(family,mode);pp=folder/'protocol.json'
    if pp.exists() and read(pp)!=p:raise ValueError('Resume protocol/source mismatch; use a new folder')
    write(pp,p)
    env=preflight(folder/'preflight.json',repo)
    if env['status']!='PASS':raise RuntimeError('Native preflight blocked: '+ '; '.join(env['errors']))
    if mode=='full':
        if not smoke:raise ValueError('Full mode requires --smoke receipt directory')
        gate=read(Path(smoke)/'COMPLETE.json')
        if gate['status']!='PASS_NATIVE_RAW_AUDIT' or gate['mode']!='smoke' or gate['family']!=family or gate['source_hashes']!=p['source_hashes']:
            raise ValueError('Smoke receipt must match family and exact source')
        analyze(Path(smoke))  # Verify every required checkpoint and its raw/compact hashes.
        smoke_receipts=[read(x) for x in (Path(smoke)/'checkpoints').glob('*.json')]
        per_execution=sum(x['wall_seconds'] for x in smoke_receipts)/sum(x['native_executions'] for x in smoke_receipts)
        total_executions=p['actual_N']*(40 if family=='timing' else 80)
        write(folder/'COST_ESTIMATE.json',dict(native_executions=total_executions,
            smoke_based_serial_hours=per_execution*total_executions/3600,
            ideal_parallel_hours=per_execution*total_executions/(3600*workers),workers=workers,
            scope='Rough smoke-based extrapolation, not a runtime or storage guarantee'))
    todo=[];receipts=[]
    for job in d.jobs(family,mode):
        receipt=completed(folder,job)
        if receipt:receipts.append(receipt)
        else:todo.append(job)
    total_jobs=len(todo)+len(receipts)
    print(json.dumps(dict(family=family,mode=mode,completed=len(receipts),pending=len(todo))),flush=True)
    # Spawn avoids inherited NetSquid global state between workers.
    with cf.ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn')) as pool:
        pending={};it=iter(todo)
        def submit():
            try:job=next(it)
            except StopIteration:return False
            pending[pool.submit(worker,job,str(folder))]=job;return True
        for _ in range(min(len(todo),workers)):submit()
        while pending:
            ready,_=cf.wait(pending,return_when=cf.FIRST_COMPLETED)
            for f in ready:
                pending.pop(f);receipt=f.result();receipts.append(receipt)
                print(json.dumps(dict(completed=len(receipts),total=total_jobs,last_seconds=receipt['wall_seconds'])),flush=True)
                submit()
    write(folder/'COMPLETE.json',dict(status='PASS_NATIVE_RAW_AUDIT',family=family,mode=mode,
        source_hashes=p['source_hashes'],jobs=len(receipts),native_executions=sum(x['native_executions'] for x in receipts),
        protocol_sha256=digest(pp),effect_inference_permitted=mode=='full'))
    return analyze(folder)


def app_value(row,endpoint):
    outcomes=row['outcomes']
    if len(outcomes)!=12:raise ValueError('Incorrect all-offered denominator')
    if endpoint=='correct_timely':return sum(x['category']=='correct_on_time' for x in outcomes)/12
    if any(x['infidelity'] is None or not x['complete'] for x in outcomes):return None
    return sum(x['infidelity'] for x in outcomes)/12


def descriptive(rows,family):
    """All-offered counts; never substitute completed-only denominators."""
    groups={}
    for r in rows:
        if family=='timing':
            key=(r['workload'],r['arm'])
            g=groups.setdefault(key,dict(offered=0,wrong_completed=0,unfinished=0))
            g['offered']+=1;g['unfinished']+=not r['completed']
            g['wrong_completed']+=r['wrong_output'] if r['completed'] else 0
        else:
            s=r['spec']
            if s['stage']!='evaluation':continue
            key=(s['profile'],s['cost_ns'],s['variant'],s['policy'],s['arm'])
            g=groups.setdefault(key,dict(offered=0,correct_on_time=0,wrong_on_time=0,late=0,unfinished=0,
                wrong_all=0,wrong_and_late=0,infidelity_sum=0.,missing_infidelity=0,benign_response_ns=[]))
            for o in r['outcomes']:
                g['offered']+=1;g[o['category']]+=1
                g['wrong_all']+=bool(o['wrong'])
                g['wrong_and_late']+=bool(o['wrong']) and o['category']=='late'
                if o['infidelity'] is None:g['missing_infidelity']+=1
                else:g['infidelity_sum']+=o['infidelity']
            g['benign_response_ns'].extend(o['response_ns'] for o in r['benign'] if o['complete'])
    out=[]
    for key,g in sorted(groups.items()):
        g=dict(g)
        if family=='application':
            if sum(g[x] for x in ('correct_on_time','wrong_on_time','late','unfinished'))!=g['offered']:
                raise ValueError('All-offered partition')
            times=sorted(g.pop('benign_response_ns'))
            g['benign_p95_nearest_rank_ns']=times[max(0,(95*len(times)+99)//100-1)] if times else None
            g['mean_infidelity_bounds']=[g['infidelity_sum']/g['offered'],
                (g['infidelity_sum']+g['missing_infidelity'])/g['offered']]
        out.append(dict(configuration=list(key),**g))
    return out


def analyze(folder):
    folder=Path(folder);p=read(folder/'protocol.json');done=read(folder/'COMPLETE.json')
    if done['protocol_sha256']!=digest(folder/'protocol.json'):raise ValueError('Completion/protocol mismatch')
    rows=[]
    for job in d.jobs(p['family'],p['mode']):
        c=completed(folder,job)
        if c is None:raise ValueError('Missing required checkpoint')
        rows.extend(read(folder/c['compact']))
    out=dict(family=p['family'],mode=p['mode'],native_rows=len(rows),confirmatory=p['mode']=='full',
        comparisons=[],all_offered_summary=descriptive(rows,p['family']))
    if p['mode']=='smoke':
        out.update(status='SMOKE_ONLY_NO_EFFECT_INFERENCE',native_preflight_passed=True)
    elif p['family']=='timing':
        groups={}
        for row in rows:groups.setdefault((row['workload'],row['index']),{})[row['arm']]=row
        for w in ('plus','teleport'):
            fixed=[]
            for arm in ARMS:
                if arm in ('none','learned'):continue
                paired=[groups[(w,i)] for i in range(p['actual_N'])]
                if any(set(g)!=set(ARMS) for g in paired):raise ValueError('Missing timing arm')
                missing=sum(not g[a]['completed'] for g in paired for a in ('learned',arm))
                c=dict(workload=w,contrast='learned-minus-'+arm,missing=missing)
                if not missing:
                    c.update(interval([g['learned']['wrong_output']-g[arm]['wrong_output'] for g in paired],36))
                else:c['status']='UNRESOLVED_ENDPOINTS_NO_EFFECT_CLAIM'
                out['comparisons'].append(c)
                if arm.startswith('fixed_'):fixed.append(c)
            if all('lower' in c for c in fixed):
                out.setdefault('learned_minus_best_fixed_grid',{})[w]=dict(
                    lower=min(c['lower'] for c in fixed),upper=min(c['upper'] for c in fixed),
                    interpretation='Simultaneous envelope for learned error minus maximum fixed-grid error; oracle diagnostic, not a deployed selector')
    else:
        groups={}
        for row in rows:
            s=row['spec'];groups[(s['stream']-d.SEED_OFFSET,s['profile'],s['cost_ns'],s['variant'],s['policy'],s['arm'])]=row
        for contrast in APP_CONTRASTS:
            profile,cost,variant,lp,la,rp,ra,endpoint=contrast;values=[];missing=0
            for i in range(p['actual_N']):
                left=app_value(groups[(i,profile,cost,variant,lp,la)],endpoint)
                right=app_value(groups[(i,profile,cost,variant,rp,ra)],endpoint)
                if left is None or right is None:missing+=1
                else:values.append(left-right)
            c=dict(contrast=contrast,missing_paired_streams=missing)
            if missing:c['status']='UNRESOLVED_ENDPOINTS_NO_EFFECT_CLAIM'
            else:c.update(interval(values,6))
            out['comparisons'].append(c)
    # Preserve all compact records, including null endpoints and unfinished work.
    write(folder/'ALL_OFFERED_COMPACT.json.gz',rows)
    write(folder/'ANALYSIS.json',out);return out


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('command',choices=('preflight','run','analyze'))
    a.add_argument('--family',choices=('timing','application'),default='timing')
    a.add_argument('--mode',choices=('smoke','full'),default='smoke')
    a.add_argument('--folder',type=Path,required=True)
    a.add_argument('--workers',type=int,default=8)
    a.add_argument('--repo');a.add_argument('--smoke',type=Path)
    args=a.parse_args()
    if args.workers<1:raise ValueError('workers must be positive')
    if args.command=='preflight':
        result=preflight(args.folder/'preflight.json',args.repo)
        print(json.dumps(result,indent=2));return 0 if result['status']=='PASS' else 2
    if args.command=='analyze':result=analyze(args.folder)
    else:
        args.folder.mkdir(parents=True,exist_ok=True)
        with (args.folder/'RUN.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=run(args.family,args.mode,args.folder,args.workers,args.repo,args.smoke)
    print(json.dumps(result,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
