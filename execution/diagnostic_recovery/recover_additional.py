"""External fresh-process recovery for the frozen additional diagnostic suite."""
import argparse
import contextlib
import fcntl
import hashlib
import importlib
import json
from pathlib import Path
import sys
import traceback
import zipfile
from process_recycling import fresh_workers

HERE=Path(__file__).resolve().parent
MODULES=('numpy','netsquid','pydynaa','qoala','netqasm')

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def read(p):return json.loads(Path(p).read_text())
def write(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.tmp')
    tmp.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n');tmp.replace(p)

def memory_environment():
    out={}
    for path in ['/proc/self/cgroup',*['/sys/fs/cgroup/'+x for x in ('memory.max','memory.current','memory.peak','memory.events')]]:
        if Path(path).is_file():
            with contextlib.suppress(OSError):out[path]=Path(path).read_text().strip()
    out['scope']='Available metadata; hierarchy-root cgroup values may not be the session limit.'
    return out

def original_modules(package):
    sys.path.insert(0,str(package/'source'))
    r=importlib.import_module('study_runner');d=importlib.import_module('study_design')
    if Path(r.__file__).resolve()!=package.resolve()/'source/study_runner.py':raise ValueError('Unexpected study_runner import')
    return r,d

def verify_sources(r):
    expected=read(HERE/'RECOVERY_SOURCE_HASHES.json')
    actual={p.name:sha(p) for p in sorted(HERE.glob('*.py'))}
    if expected!=actual:raise ValueError('Recovery source changed')
    if r.hashes()!=read(r.ROOT/'SOURCE_HASHES.json'):raise ValueError('Frozen additional source changed')
    parent=r.PARENT
    for line in (parent/'SHA256SUMS.txt').read_text().splitlines():
        digest,name=line.split('  ',1)
        if sha(parent/name)!=digest:raise ValueError('Frozen parent package changed: '+name)
    return actual

def call_original(r,argv):
    old=sys.argv
    try:
        sys.argv=[str(r.__file__),*argv]
        with fresh_workers():return r.main()
    finally:sys.argv=old

def compare_smokes(r,d,family,old,fresh):
    # Both original analyzers replay each saved raw JSONL record through the
    # unchanged trace auditor. No native evolution is independently simulated.
    r.analyze(old,replay=True);r.analyze(fresh,replay=True)
    protocol=r.read(old/'protocol.json')
    if protocol!=r.read(fresh/'protocol.json'):raise ValueError('Smoke protocol mismatch')
    count=0
    for i in range(protocol['N']):
        a=r.checkpoint(old,i);b=r.checkpoint(fresh,i)
        if a is None or b is None:raise ValueError('Missing smoke checkpoint')
        x=r.read(old/a['compact']);y=r.read(fresh/b['compact'])
        if x!=y:raise ValueError('Fresh-process smoke compact differs at stream '+str(i))
        count+=len(x)
    return dict(family=family,status='PASS_EXACT_COMPACT_MATCH_AND_RAW_TRACE_REPLAY',native_executions=count,
        old_protocol_sha256=sha(old/'protocol.json'),fresh_protocol_sha256=sha(fresh/'protocol.json'),
        scope='Unchanged inputs/source; original trace auditor and exact compact agreement. Not an independent simulation of noisy quantum evolution.')

def launch(a):
    package=a.package.resolve();results=a.results.resolve()
    selected=('causal','robustness') if a.family=='all' else (a.family,)
    logs=results.parent/'additional-memory-recovery-v1'/a.family;logs.mkdir(parents=True,exist_ok=True)
    status=dict(status='STARTED',families=selected,mode=a.mode,workers=a.workers,
        original_study_source_modified=False,original_protocol_modified=False,
        execution='One original stream per fresh spawned process; bounded ordered map',
        failure_scope='Worker MemoryError observed; exact allocation/retention cause unproved.')
    write(logs/'STATUS.json',status)
    with (logs/'RECOVERY.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            r,d=original_modules(package);adapter=verify_sources(r);before=r.hashes()
            check=r.parent_preflight(logs/'native-preflight.json',a.repo)
            if check['status']!='PASS':raise RuntimeError('Native preflight blocked: '+ '; '.join(check['errors']))
            for family in selected:
                for mode in ('smoke','full'):
                    oldpath=results/(family+'-'+mode)/'preflight.json'
                    if not oldpath.exists():continue
                    old=read(oldpath)
                    if old.get('status')!='PASS':continue
                    for name in MODULES:
                        if old['checks'].get(name)!=check['checks'].get(name):raise ValueError('Native module provenance changed: '+name)
            write(logs/'EXECUTION_PROVENANCE.json',dict(adapter_sha256=adapter,frozen_source_sha256=before,
                python=sys.executable,python_version=sys.version,workers=a.workers,memory=memory_environment(),
                change='External executor with submit/map support only; original worker, checkpoint, protocol and analysis code are invoked unchanged.'))
            if a.mode=='check':status['status']='PASS_NATIVE_PREFLIGHT_ONLY';return
            repo=['--repo',str(a.repo)] if a.repo else []
            validations=[]
            for family in selected:
                old=results/(family+'-smoke')
                call_original(r,['run','--family',family,'--mode','smoke','--folder',str(old),'--workers',str(a.workers),*repo])
                fresh=logs/'native-validation'/(family+'-smoke')
                call_original(r,['run','--family',family,'--mode','smoke','--folder',str(fresh),'--workers',str(a.workers),*repo])
                validations.append(compare_smokes(r,d,family,old,fresh))
                write(logs/'SMOKE_EQUIVALENCE.json',validations);print(json.dumps(validations[-1]),flush=True)
            if a.mode=='smoke':status['status']='PASS_FRESH_PROCESS_SMOKE_ONLY';return
            for family in selected:
                full=results/(family+'-full')
                print(json.dumps(dict(event='resume',family=family,
                    checkpoint_receipts_present=len(list((full/'checkpoints').glob('*.json'))),
                    note='The original runner verifies each raw/compact hash before deciding what to reuse.')),flush=True)
                call_original(r,['run','--family',family,'--mode','full','--folder',str(full),'--workers',str(a.workers),*repo])
            status.update(status='PASS_FIXED_N_DIAGNOSTICS_INTERNAL_AUDIT',independent_full_raw_replay='PENDING')
        except BaseException as exc:
            status.update(status='FAILED_PRESERVE_CHECKPOINTS',error_type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc())
            raise
        finally:
            if 'before' in locals():status['frozen_source_hashes_unchanged']=r.hashes()==before
            status['memory_at_exit']=memory_environment();write(logs/'STATUS.json',status)

def export_review(results,family):
    results=Path(results).resolve();selected=('causal','robustness') if family=='all' else (family,)
    logs=results.parent/'additional-memory-recovery-v1'/family
    roots=[(results/(f+'-'+m),f+'-'+m) for f in selected for m in ('smoke','full')]
    roots.append((logs,'recovery-'+family));manifest=[]
    target=results.parent/(results.name+'-'+family+'-additional-memory-recovery-review.zip')
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for root,prefix in roots:
            if not root.exists():continue
            known={}
            for p in root.rglob('checkpoints/*.json'):
                receipt=read(p)
                if 'raw' in receipt:known[str((p.parent.parent/receipt['raw']).resolve())]=receipt['raw_sha256']
            for p in root.rglob('*'):
                if not p.is_file() or p.name.endswith(('.tmp','.lock','.log')):continue
                rel=p.relative_to(root);name=prefix+'/'+str(rel)
                if 'raw' in rel.parts or 'failures' in rel.parts:
                    manifest.append(dict(path=name,bytes=p.stat().st_size,sha256=known.get(str(p.resolve())) or sha(p),retained_on_server=True))
                else:z.write(p,name)
        for name in ('CONTROLLER.log','ENGINEERING_TESTS.log'):
            p=logs/name
            if p.exists():z.write(p,'recovery-'+family+'/'+name)
        z.writestr('RETAINED_RAW_MANIFEST.json',json.dumps(manifest,indent=2))
    return target

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('run','export'))
    p.add_argument('--package',type=Path,required=True);p.add_argument('--results',type=Path,required=True)
    p.add_argument('--family',choices=('all','causal','robustness'),default='all');p.add_argument('--mode',choices=('check','smoke','full'),default='full')
    p.add_argument('--workers',type=int,default=8);p.add_argument('--repo');a=p.parse_args()
    if not 1<=a.workers<=8:raise ValueError('Use 1 through 8 workers per eight-core session')
    if a.command=='export':print(export_review(a.results,a.family));return
    launch(a)

if __name__=='__main__':main()
