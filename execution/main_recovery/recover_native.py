"""Resume the existing P1 fixed-N studies with bounded worker lifetime.

No frozen file is rewritten. Original integrity, protocol, checkpoint and
native raw checks still apply. Fresh smoke compact results must exactly match
the saved original smoke results before a full study is resumed.
"""
import argparse
import contextlib
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback
import zipfile
from process_recycling import fresh_workers

HERE=Path(__file__).resolve().parent

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def write(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.tmp')
    tmp.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');tmp.replace(p)

def read(p):return json.loads(Path(p).read_text())

def memory_environment():
    data={}
    for name in ('memory.max','memory.current','memory.peak','memory.events'):
        p=Path('/sys/fs/cgroup')/name
        if p.is_file():
            with contextlib.suppress(OSError):data[str(p)]=p.read_text().strip()
    # The process may belong to a nested cgroup. Record the mapping rather than
    # claiming that the hierarchy root's limit is the session's actual limit.
    p=Path('/proc/self/cgroup')
    if p.is_file():data['process_cgroup']=p.read_text().strip()
    data['scope']='Available cgroup metadata; hierarchy-root values may not be the session limit.'
    return data

def original_modules(package):
    sys.path.insert(0,str(package/'source'))
    r=importlib.import_module('closure_runner');d=importlib.import_module('closure_design')
    if Path(r.__file__).resolve()!=package/'source/closure_runner.py':raise ValueError('Unexpected closure_runner import')
    return r,d

def verify_sources(package):
    for line in (package/'SHA256SUMS.txt').read_text().splitlines():
        expected,name=line.split('  ',1)
        if sha(package/name)!=expected:raise ValueError('Original packaged file differs: '+name)
    expected=read(HERE/'RECOVERY_SOURCE_HASHES.json')
    actual={str(p.relative_to(HERE)):sha(p) for p in sorted(HERE.glob('*.py'))}
    if actual!=expected:raise ValueError('Recovery adapter source differs')
    return actual

def call_original(r,argv):
    previous=sys.argv
    try:
        sys.argv=[str(Path(r.__file__)),*argv]
        with fresh_workers():return r.main()
    finally:sys.argv=previous

def same_smoke(r,d,family,original,fresh):
    # Both analysis calls verify the original protocols and raw/compact hashes.
    r.analyze(original);r.analyze(fresh)
    count=0
    for job in d.jobs(family,'smoke'):
        old=r.completed(original,job);new=r.completed(fresh,job)
        if old is None or new is None:raise ValueError('Missing smoke checkpoint')
        a=r.read(original/old['compact']);b=r.read(fresh/new['compact'])
        if a!=b:raise ValueError('Fresh-process smoke compact differs: '+d.stable(job))
        count+=len(a)
    return dict(status='PASS_EXACT_COMPACT_MATCH',family=family,native_executions=count,
        original=str(original),fresh=str(fresh),original_protocol_sha256=sha(original/'protocol.json'),
        fresh_protocol_sha256=sha(fresh/'protocol.json'),
        scope='Repeated native smoke, same frozen source and inputs; unchanged raw audit for each run. Not an independent simulation of noisy physics.')

def launch(a):
    package=a.package.resolve();results=a.results.resolve();scope=a.family
    logdir=results.parent/'memory-recovery-v1'/scope
    logdir.mkdir(parents=True,exist_ok=True)
    selected=('timing','application') if scope=='all' else (scope,)
    status=dict(status='STARTED',families=selected,mode=a.mode,workers=a.workers,
        worker_lifetime='one original chunk per fresh spawned process',
        frozen_sources_modified=False,sample_sizes_modified=False,
        failure_scope='MemoryError observed; exact memory-retention cause is not established')
    write(logdir/'STATUS.json',status)
    with (logdir/'RECOVERY.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            sources=verify_sources(package)
            r,d=original_modules(package)
            before=r.source_hashes()
            write(logdir/'EXECUTION_PROVENANCE.json',dict(adapter_source_sha256=sources,
                frozen_source_sha256=before,python=sys.executable,version=sys.version,
                memory_environment=memory_environment(),workers=a.workers,
                method='External executor substitution only; original main(), worker(), protocols, seeds and audits are invoked unchanged.'))
            check=r.preflight(logdir/'native-preflight.json',a.repo)
            if check['status']!='PASS':raise RuntimeError('Native preflight blocked: '+ '; '.join(check['errors']))
            for oldfile in [results/'environment/preflight.json',*[results/(f+'-full')/'preflight.json' for f in selected]]:
                if not oldfile.exists():continue
                old=read(oldfile)
                if old.get('status')!='PASS':continue
                for name in ('numpy','netsquid','pydynaa','qoala','netqasm'):
                    if old['checks'].get(name)!=check['checks'].get(name):
                        raise ValueError('Native module provenance changed since the original run: '+name)
            if a.mode=='check':status['status']='PASS_NATIVE_PREFLIGHT_ONLY';return
            repo=['--repo',str(a.repo)] if a.repo else []
            validations=[]
            for family in selected:
                original=results/(family+'-smoke')
                # Existing smokes are resumed/hash-checked, never overwritten
                # with a fresh run under changed source.
                call_original(r,['run','--family',family,'--mode','smoke','--workers',str(a.workers),'--folder',str(original),*repo])
                fresh=logdir/'native-validation'/(family+'-smoke')
                call_original(r,['run','--family',family,'--mode','smoke','--workers',str(a.workers),'--folder',str(fresh),*repo])
                validations.append(same_smoke(r,d,family,original,fresh))
                write(logdir/'SMOKE_EQUIVALENCE.json',validations)
                print(json.dumps(validations[-1]),flush=True)
            if a.mode=='smoke':status['status']='PASS_FRESH_PROCESS_SMOKE_ONLY';return
            for family in selected:
                folder=results/(family+'-full')
                # The unchanged original run() verifies every raw/compact
                # checkpoint and prints its authoritative resume count.
                call_original(r,['run','--family',family,'--mode','full','--workers',str(a.workers),
                    '--folder',str(folder),'--smoke',str(results/(family+'-smoke')),*repo])
            status.update(status='PASS_FIXED_N_NATIVE_INTERNAL_AUDIT',independent_raw_replay='PENDING')
        except BaseException as exc:
            status.update(status='FAILED_PRESERVE_CHECKPOINTS',error_type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc())
            raise
        finally:
            if 'r' in locals() and 'before' in locals():
                status['frozen_source_hashes_unchanged']=r.source_hashes()==before
            status['memory_at_exit']=memory_environment();write(logdir/'STATUS.json',status)

def export_review(results,scope):
    results=Path(results).resolve();logdir=results.parent/'memory-recovery-v1'/scope
    families=('timing','application') if scope=='all' else (scope,)
    roots=[(results/(f+'-'+mode),f+'-'+mode) for f in families for mode in ('smoke','full')]
    roots.append((logdir,'recovery-'+scope))
    archive=results.parent/(results.name+'-'+scope+'-memory-recovery-review.zip')
    inventory=[]
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for root,prefix in roots:
            if not root.exists():continue
            known={}
            for checkpoint in root.rglob('checkpoints/*.json'):
                receipt=read(checkpoint)
                if 'raw' in receipt:known[str((checkpoint.parent.parent/receipt['raw']).resolve())]=receipt['raw_sha256']
            for p in root.rglob('*'):
                if not p.is_file() or p.name.endswith('.tmp') or p.name.endswith('.lock') or p.name.endswith('.log'):continue
                rel=p.relative_to(root);name=prefix+'/'+str(rel)
                if 'raw' in rel.parts or 'failures' in rel.parts or p.name=='ALL_OFFERED_COMPACT.json.gz':
                    inventory.append(dict(path=name,bytes=p.stat().st_size,sha256=known.get(str(p.resolve())) or sha(p),retained_on_server=True))
                else:z.write(p,name)
        # The controller log is outside these roots so archive export cannot
        # accidentally include a second family's in-progress log/archive.
        log=logdir/'CONTROLLER.log'
        if log.exists():z.write(log,'recovery-'+scope+'/CONTROLLER.log')
        z.writestr('RETAINED_RAW_MANIFEST.json',json.dumps(inventory,indent=2))
    return archive

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('run','export'))
    p.add_argument('--package',type=Path,required=True);p.add_argument('--results',type=Path,required=True)
    p.add_argument('--workers',type=int,default=8);p.add_argument('--family',choices=('all','timing','application'),default='all')
    p.add_argument('--mode',choices=('check','smoke','full'),default='full');p.add_argument('--repo',type=Path)
    a=p.parse_args()
    if not 1<=a.workers<=8:raise ValueError('Use 1 through 8 workers per eight-core session')
    if a.command=='export':print(export_review(a.results,a.family));return
    launch(a)

if __name__=='__main__':main()
