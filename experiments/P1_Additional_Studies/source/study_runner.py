"""Native-only resumable diagnostics, independent raw replay, compact export."""
import argparse
import concurrent.futures as cf
import contextlib
import fcntl
import gzip
import hashlib
import importlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PARENT=ROOT/'P1_Feedback_Closure'
sys.path[:0]=[str(ROOT/'source'),str(PARENT/'source'),str(PARENT/'source/application_parent')]
import study_design as d
from closure_stats import interval
from closure_runner import preflight as parent_preflight

def digest(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def write(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.tmp')
    def scalar(v):
        if hasattr(v,'item'):return v.item()
        raise TypeError(type(v).__name__)
    if p.suffix=='.gz':
        with gzip.open(tmp,'wt') as f:json.dump(x,f,sort_keys=True,allow_nan=False,default=scalar)
    else:tmp.write_text(json.dumps(x,indent=2,sort_keys=True,allow_nan=False,default=scalar)+'\n')
    tmp.replace(p)

def read(p):
    p=Path(p)
    if p.suffix=='.gz':
        with gzip.open(p,'rt') as f:return json.load(f)
    return json.loads(p.read_text())

def hashes():
    return {str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'source').glob('*.py'))}

def checkpoint(folder,i):
    p=folder/'checkpoints'/('%06d.json'%i)
    if not p.exists():return None
    c=read(p)
    if c['index']!=i:raise ValueError('Checkpoint index')
    for name in ('raw','compact'):
        if digest(folder/c[name])!=c[name+'_sha256']:raise ValueError('Changed checkpoint '+name)
    return c

def worker(args):
    family,i,folder=args;folder=Path(folder);compact=[];start=time.perf_counter()
    rp='raw/%06d.jsonl.gz'%i;cp='compact/%06d.json.gz'%i
    partial=folder/(rp+'.partial');partial.parent.mkdir(parents=True,exist_ok=True)
    if partial.exists():
        old=folder/'failures'/('%06d-interrupted-%d.jsonl.gz'%(i,time.time_ns()))
        old.parent.mkdir(parents=True,exist_ok=True);partial.replace(old)
    try:
        from app_native import run, failure_snapshot
        from app_audit import validate_record
        with gzip.open(partial,'wt') as f:
            for spec in d.specs(family,i):
                d.install(spec)
                r=run(spec);compact.append(validate_record(r,spec))
                json.dump(r,f,sort_keys=True,allow_nan=False,default=lambda x:x.item())
                f.write('\n')
        partial.replace(folder/rp);write(folder/cp,compact)
        c=dict(index=i,status='PASS_NATIVE_TRACE_AUDIT',raw=rp,compact=cp,
               raw_sha256=digest(folder/rp),compact_sha256=digest(folder/cp),
               native_executions=len(compact),wall_seconds=time.perf_counter()-start)
        write(folder/'checkpoints'/('%06d.json'%i),c);return c
    except BaseException:
        err=dict(index=i,exception=traceback.format_exc(),partial_raw=str(partial.relative_to(folder)),compact=compact)
        with contextlib.suppress(Exception):err['failed_snapshot']=failure_snapshot()
        write(folder/'failures'/('%06d.json.gz'%i),err)
        raise

def value(row,key):
    o=row['outcomes']
    if len(o)!=12:raise ValueError('Expected all 12 offered requests')
    if key=='infidelity':
        if any(x['infidelity'] is None for x in o):return None
        return sum(x['infidelity'] for x in o)/len(o)
    if key=='correct_timely':return sum(x['category']=='correct_on_time' for x in o)/len(o)
    if key=='timely':return sum(x['category'] in ('correct_on_time','wrong_on_time') for x in o)/len(o)
    raise ValueError(key)

def analyze(folder,replay=False):
    folder=Path(folder);p=read(folder/'protocol.json');complete=read(folder/'COMPLETE.json')
    from closure_runner import source_hashes
    if p['source_hashes']!=hashes() or source_hashes()!=read(PARENT/'SOURCE_HASHES.json'):
        raise ValueError('Replay source/protocol differs')
    frozen=d.protocol(p['family'],p['mode']);frozen['source_hashes']=hashes()
    if frozen!=p:raise ValueError('Frozen protocol differs from analysis specification')
    if complete['protocol_sha256']!=digest(folder/'protocol.json'):raise ValueError('Completion identity')
    summaries={};samples={};all_offered={};matched_work=True
    for i in range(p['N']):
        c=checkpoint(folder,i)
        if c is None:raise ValueError('Missing required stream')
        rows=read(folder/c['compact'])
        expected=list(d.specs(p['family'],i))
        if [x['spec'] for x in rows]!=expected:raise ValueError('Compact spec inventory')
        if replay:
            from app_audit import validate_record
            with gzip.open(folder/c['raw'],'rt') as f:
                for s,compact in zip(expected,rows):
                    line=f.readline()
                    if not line:raise ValueError('Missing raw record')
                    r=json.loads(line);d.install(s)
                    if validate_record(r,s)!=compact:raise ValueError('Independent saved-raw replay differs')
                if f.readline():raise ValueError('Unexpected extra raw record')
        groups={}
        private=[]
        for row in rows:
            s=row['spec'];name=s['diagnostic_cell']['name'];key=(name,s['variant'],s['policy'],s['arm'])
            if key in groups:raise ValueError('Duplicate cell')
            groups[key]=row
            # Counts and bounds preserve missing/late results; no complete-case mean.
            g=all_offered.setdefault(key,dict(offered=0,correct_on_time=0,wrong_on_time=0,late=0,unfinished=0,
                infidelity_sum=0.,missing_infidelity=0,wrong_all=0,wrong_and_late=0,
                attacker_offered=0,attacker_incomplete=0,attacker_service_ns=0,
                benign_offered=0,benign_incomplete=0,benign_timely=0,benign_response_ns=[],max_live_owners=0))
            for o in row['outcomes']:
                g['offered']+=1;g[o['category']]+=1
                g['wrong_all']+=bool(o['wrong']);g['wrong_and_late']+=bool(o['wrong']) and o['category']=='late'
                if o['infidelity'] is None:g['missing_infidelity']+=1
                else:g['infidelity_sum']+=o['infidelity']
            g['attacker_offered']+=len(row['attacker'])
            g['attacker_incomplete']+=sum(not o['complete'] for o in row['attacker'])
            g['attacker_service_ns']+=row['executed_attacker_service_ns']
            g['benign_offered']+=len(row['benign']);g['benign_incomplete']+=sum(not o['complete'] for o in row['benign'])
            g['benign_timely']+=sum(o['complete'] and o['response_ns']<=5_000_000 for o in row['benign'])
            g['benign_response_ns'].extend(o['response_ns'] for o in row['benign'] if o['complete'])
            g['max_live_owners']=max(g['max_live_owners'],row['max_live_owners'])
        for contrast in p['contrasts']:
            k=(contrast['cell'],contrast['variant'],contrast['policy'])
            left,right=(groups[k+(a,)] for a in ('selected','random'))
            work_ok=(left['all_attacker_work_completed'] and right['all_attacker_work_completed'] and
                     left['executed_attacker_service_ns']==right['executed_attacker_service_ns'])
            matched_work &= work_ok
            if left['private_plan_sha256']!=right['private_plan_sha256']:raise ValueError('Unmatched private workload')
            a,b=value(left,contrast['endpoint']),value(right,contrast['endpoint'])
            v=None if a is None or b is None else a-b
            if contrast.get('reference_cell'):
                ref=(contrast['reference_cell'],contrast['variant'],contrast['policy'])
                rl,rr=(groups[ref+(arm,)] for arm in ('selected','random'))
                x,y=value(rl,contrast['endpoint']),value(rr,contrast['endpoint'])
                work_ok &= (rl['all_attacker_work_completed'] and rr['all_attacker_work_completed'] and
                            rl['executed_attacker_service_ns']==rr['executed_attacker_service_ns'])
                if rl['private_plan_sha256']!=rr['private_plan_sha256'] or rl['private_plan_sha256']!=left['private_plan_sha256']:
                    raise ValueError('Unmatched causal intervention workload')
                v=None if v is None or x is None or y is None else v-(x-y)
            samples.setdefault(d.stable(contrast),[]).append((v,work_ok))
    comparisons=[]
    for spec in p['contrasts']:
        x=samples[d.stable(spec)];missing=sum(v is None for v,w in x);work_fail=sum(not w for v,w in x)
        r=dict(spec,missing_paired_streams=missing,unmatched_completed_service_streams=work_fail)
        if p['mode']=='smoke':r['status']='SMOKE_NO_EFFECT_INFERENCE'
        elif missing or work_fail:r['status']='UNRESOLVED_NO_EQUAL_WORK_EFFECT_CLAIM'
        else:r.update(interval([v for v,w in x],len(p['contrasts']),bounds=(-2.,2.) if spec.get('reference_cell') else (-1.,1.)))
        comparisons.append(r)
    for key,g in all_offered.items():
        if sum(g[k] for k in ('correct_on_time','wrong_on_time','late','unfinished'))!=g['offered']:raise ValueError('Partition')
        times=sorted(g.pop('benign_response_ns'))
        g['benign_p95_completed_only_ns']=times[max(0,(95*len(times)+99)//100-1)] if times else None
        g['infidelity_all_offered_bounds']=[g['infidelity_sum']/g['offered'],(g['infidelity_sum']+g['missing_infidelity'])/g['offered']]
    out=dict(status='PASS_SAVED_RAW_REPLAY' if replay else 'PASS_INTERNAL_AUDIT',family=p['family'],mode=p['mode'],
             confirmatory=False,all_active_work_matched=matched_work,comparisons=comparisons,
             all_offered=[dict(cell=list(k),**g) for k,g in sorted(all_offered.items())],
             native_physics='NetSquid owns noisy evolution; raw replay checks traces, programs and density validity, not a second quantum simulation')
    write(folder/('REPLAY_ANALYSIS.json' if replay else 'ANALYSIS.json'),out)
    return out

def run(family,mode,folder,workers,repo=None):
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    expected=read(ROOT/'SOURCE_HASHES.json')
    if hashes()!=expected:raise ValueError('Additional source hashes differ')
    p=d.protocol(family,mode);p['source_hashes']=expected
    proto=folder/'protocol.json'
    if proto.exists() and read(proto)!=p:raise ValueError('Resume protocol mismatch; choose a new folder')
    write(proto,p)
    check=parent_preflight(folder/'preflight.json',repo)
    if check['status']!='PASS':raise RuntimeError('Native preflight BLOCKED: '+ '; '.join(check['errors']))
    if mode=='full':
        smoke=folder.parent/(family+'-smoke')
        sp=read(smoke/'protocol.json');sa=analyze(smoke,replay=True)
        if sp['source_hashes']!=expected or sp['family']!=family or sp['mode']!='smoke':raise ValueError('Matching smoke required')
        # The same cell/arm inventory is tested in smoke. Integrations are never
        # declared valid merely because module imports succeeded.
    pending=[i for i in range(p['N']) if checkpoint(folder,i) is None]
    write(folder/'RUN_STATUS.json',dict(status='RUNNING',pending=len(pending),N=p['N']))
    if pending:
        with cf.ProcessPoolExecutor(max_workers=workers,mp_context=mp.get_context('spawn')) as pool:
            for receipt in pool.map(worker,((family,i,str(folder)) for i in pending),chunksize=1):
                print(json.dumps(dict(stream=receipt['index'],executions=receipt['native_executions'],seconds=round(receipt['wall_seconds'],2))),flush=True)
    for i in range(p['N']):
        if checkpoint(folder,i) is None:raise ValueError('Missing stream')
    write(folder/'COMPLETE.json',dict(status='COMPLETE_FIXED_N',protocol_sha256=digest(proto)))
    out=analyze(folder)
    write(folder/'RUN_STATUS.json',dict(status=out['status'],independent_replay='PENDING',N=p['N']))
    return out

def export(results):
    results=Path(results);target=results.parent/(results.name+'-compact-review.zip')
    manifest=[]
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(results.rglob('*')):
            if not p.is_file() or p.name.endswith('.tmp') or p.name=='RUN.lock':continue
            rel=p.relative_to(results)
            if 'raw' in rel.parts or 'failures' in rel.parts:
                manifest.append(dict(path=str(rel),sha256=digest(p),bytes=p.stat().st_size))
            else:z.write(p,str(rel))
        z.writestr('RAW_MANIFEST.json',json.dumps(manifest,indent=2))
    return target

def main():
    a=argparse.ArgumentParser();a.add_argument('command',choices=('plan','run','replay','export'))
    a.add_argument('--family',choices=('causal','robustness'),default='causal')
    a.add_argument('--mode',choices=('smoke','full'),default='smoke');a.add_argument('--folder',type=Path,required=True)
    a.add_argument('--workers',type=int,default=8);a.add_argument('--repo');x=a.parse_args()
    if not 1<=x.workers<=8:raise ValueError('Use 1 to 8 workers within one allocation')
    if x.command=='plan':
        p=d.protocol(x.family,x.mode);p['native_executions']=len(list(d.specs(x.family,0)))*p['N'];print(json.dumps(p,indent=2));return
    if x.command=='export':print(export(x.folder));return
    if x.command=='replay':out=analyze(x.folder,replay=True)
    else:
        x.folder.mkdir(parents=True,exist_ok=True)
        with (x.folder/'RUN.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:out=run(x.family,x.mode,x.folder,x.workers,x.repo)
            except BaseException:
                write(x.folder/'RUN_STATUS.json',dict(status='BLOCKED_OR_FAILED',exception=traceback.format_exc()))
                raise
    print(json.dumps({k:v for k,v in out.items() if k not in ('comparisons','all_offered')},indent=2))

if __name__=='__main__':main()
