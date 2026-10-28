"""Read-only raw-to-compact replay bound to the two reviewed result archives.

Reuses the frozen simulator-independent trace auditors, not native execution.
It does not independently simulate quantum physics or prove a new theorem.
All audit output goes outside the experiment folders.
"""
import argparse
import gzip
import hashlib
import importlib
import itertools
import json
from pathlib import Path
import sys
sys.dont_write_bytecode=True
import time
import traceback
import zipfile
from process_recycling import FreshProcessExecutor

HERE=Path(__file__).resolve().parent

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def read(p):
    p=Path(p)
    if p.suffix=='.gz':
        with gzip.open(p,'rt') as f:return json.load(f)
    return json.loads(p.read_text())
def write(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.tmp')
    tmp.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n');tmp.replace(p)
def safe(root,relative):
    root=Path(root).resolve();p=(root/relative).resolve()
    if root not in p.parents:raise ValueError('Path escapes study folder')
    return p

class JSONReader:
    def __init__(self,stream):self.stream=stream;self.buf='';self.pos=0;self.eof=False
    def more(self):
        self.buf=self.buf[self.pos:];self.pos=0
        s=self.stream.read(65536)
        if not s:self.eof=True
        self.buf+=s
    def peek(self):
        while True:
            while self.pos<len(self.buf) and self.buf[self.pos].isspace():self.pos+=1
            if self.pos<len(self.buf):return self.buf[self.pos]
            if self.eof:return ''
            self.more()
    def pop(self):
        c=self.peek();self.pos+=bool(c);return c
    def value(self):
        self.peek()
        while True:
            try:
                v,end=json.JSONDecoder().raw_decode(self.buf,self.pos)
                if end==len(self.buf) and not self.eof:self.more();continue
                self.pos=end;return v
            except ValueError:
                if self.eof:raise ValueError('Invalid or truncated raw JSON array')
                self.more()

def array_records(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:
        r=JSONReader(f)
        if r.pop()!='[':raise ValueError('Expected raw JSON array')
        if r.peek()==']':r.pop()
        else:
            while True:
                yield r.value()
                c=r.pop()
                if c==']':break
                if c!=',':raise ValueError('Missing array delimiter')
        if r.peek():raise ValueError('Unexpected trailing raw data')

def jsonl_records(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:yield json.loads(line)

def file_hashes(package,mapping):
    for relative,expected in mapping.items():
        if sha(safe(package,relative))!=expected:raise ValueError('Frozen source differs: '+relative)

def index_study(study,ref,main_root,additional_root):
    root=Path(main_root if ref['kind']=='main' else additional_root).resolve()
    package=root/('P1_Feedback_Closure' if ref['kind']=='main' else 'P1_Additional_Studies')
    folder=root/'results'/(study+'-full')
    if sha(folder/'protocol.json')!=ref['protocol_sha256'] or read(folder/'protocol.json')!=ref['protocol']:
        raise ValueError('Protocol differs from reviewed archive: '+study)
    if sha(folder/'COMPLETE.json')!=ref['complete_sha256']:raise ValueError('Completion receipt differs: '+study)
    file_hashes(package,ref['protocol']['source_hashes'])
    if ref['kind']=='additional':
        parent=package/'P1_Feedback_Closure';file_hashes(parent,read(parent/'SOURCE_HASHES.json'))
    jobs=[]
    for name,digest in sorted(ref['checkpoints'].items()):
        receipt=folder/'checkpoints'/name
        if sha(receipt)!=digest:raise ValueError('Checkpoint differs from reviewed archive: '+str(receipt))
        c=read(receipt)
        for k in ('raw','compact'):
            path=safe(folder,c[k])
            if not path.is_file():raise FileNotFoundError('Required '+k+' file missing: '+str(path))
        jobs.append(dict(study=study,kind=ref['kind'],package=str(package),folder=str(folder),
            receipt=name,receipt_sha256=digest,protocol_sha256=ref['protocol_sha256']))
    return jobs

def worker(arg):
    job,outdir,identity=arg;start=time.perf_counter();folder=Path(job['folder']);package=Path(job['package'])
    key=job['study']+'/'+job['receipt'];target=Path(outdir)/'checkpoints'/key
    try:
        cp=folder/'checkpoints'/job['receipt']
        if sha(cp)!=job['receipt_sha256']:raise ValueError('Checkpoint changed during audit')
        c=read(cp);raw=safe(folder,c['raw']);compact=safe(folder,c['compact'])
        if sha(raw)!=c['raw_sha256'] or sha(compact)!=c['compact_sha256']:raise ValueError('Raw/compact checksum mismatch')
        binding=dict(job=job,auditor_identity=identity,raw_sha256=c['raw_sha256'],compact_sha256=c['compact_sha256'])
        if target.exists():
            cached=read(target)
            if cached.get('status')=='PASS_RAW_TO_COMPACT' and cached.get('binding')==binding:return dict(cached,cache_reused=True)
        rows=read(compact)
        if len(rows)!=c['native_executions']:raise ValueError('Compact count differs from checkpoint')
        if job['kind']=='main':
            sys.path[:0]=[str(package/'source'),str(package/'source/application_parent'),str(package/'source/primary_parent')]
            runner=importlib.import_module('closure_runner');design=importlib.import_module('closure_design')
            if job['study']=='timing':
                from closure_audit import primary
                from public_policy import ARMS
                import q12_design
                j=c['job']
                expected=((i,arm,*design.public_for(j['workload'],i,arm,'full')) for i in range(j['start'],j['end']) for arm in ARMS)
                def check(record,spec):
                    i,arm,public,parent=spec
                    if record['public']!=public or record['parent_spec']!=parent or record['private_inputs']!=q12_design.inputs(parent):
                        raise ValueError('Timing frozen input/plan identity differs')
                    v=primary(record);v.update(index=i,workload=j['workload']);return v
            else:
                from app_audit import validate_record
                expected=runner.app_specs(c['job']['index'],'full')
                check=lambda record,spec:validate_record(record,spec)
            records=array_records(raw)
        else:
            parent=package/'P1_Feedback_Closure'
            sys.path[:0]=[str(package/'source'),str(parent/'source'),str(parent/'source/application_parent')]
            design=importlib.import_module('study_design')
            from app_audit import validate_record
            expected=design.specs(job['study'],c['index'])
            def check(record,spec):
                design.install(spec);return validate_record(record,spec)
            records=jsonl_records(raw)
        missing=object();count=0
        for record,spec,old in itertools.zip_longest(records,expected,rows,fillvalue=missing):
            if record is missing or spec is missing or old is missing:raise ValueError('Raw/spec/compact record inventory differs')
            if check(record,spec)!=old:raise ValueError('Raw replay differs from saved compact at record '+str(count))
            count+=1
        if count!=c['native_executions']:raise ValueError('Native record count differs')
        result=dict(status='PASS_RAW_TO_COMPACT',binding=binding,records=count,wall_seconds=time.perf_counter()-start,cache_reused=False)
        write(target,result);return result
    except BaseException:
        write(Path(outdir)/'failures'/key,dict(status='FAIL_RAW_REPLAY',job=job,traceback=traceback.format_exc()))
        raise

def run(a):
    refs=read(HERE/'REVIEWED_REFERENCE.json')
    sources={p.name:sha(p) for p in HERE.glob('*.py')}
    if sources!=read(HERE/'AUDITOR_SOURCE_HASHES.json'):raise ValueError('Audit tool source changed')
    identity=hashlib.sha256(json.dumps(dict(sources=sources,reference=sha(HERE/'REVIEWED_REFERENCE.json')),sort_keys=True).encode()).hexdigest()
    alljobs=[]
    for study in a.studies:alljobs.extend(index_study(study,refs[study],a.main_root,a.additional_root))
    if a.mode=='sample':
        chosen=[]
        for study in a.studies:
            jobs=[j for j in alljobs if j['study']==study]
            chosen.extend(jobs[i] for i in sorted({0,len(jobs)//2,len(jobs)-1}))
    else:chosen=alljobs
    a.output.mkdir(parents=True,exist_ok=True)
    report=dict(status='RUNNING',mode=a.mode,studies=a.studies,expected_jobs=len(chosen),
        total_full_jobs=len(alljobs),auditor_identity=identity,simulations_executed=0,
        scope='Separate replay using frozen trace auditors; no independent noisy-physics simulation or deployed-interface validation.')
    write(a.output/(a.mode+'-REPORT.json'),report)
    totals={};finished=0
    try:
        with FreshProcessExecutor(max_workers=a.workers) as pool:
            for r in pool.map(worker,((j,str(a.output),identity) for j in chosen),chunksize=1):
                study=r['binding']['job']['study'];t=totals.setdefault(study,dict(jobs=0,records=0,worker_seconds=0.))
                t['jobs']+=1;t['records']+=r['records'];t['worker_seconds']+=r['wall_seconds'];finished+=1
                print(json.dumps(dict(audit_completed=finished,audit_total=len(chosen),study=study,records=r['records'],cache_reused=r['cache_reused'])),flush=True)
        report.update(status='PASS_FULL_RAW_TO_COMPACT_REPLAY' if a.mode=='full' else 'PASS_SAMPLE_ONLY_NOT_FULL_AUDIT',totals=totals)
    except BaseException:
        report.update(status='FAIL_PRESERVE_AUDIT_RECEIPTS',completed_jobs=finished,traceback=traceback.format_exc());raise
    finally:write(a.output/(a.mode+'-REPORT.json'),report)
    print(json.dumps(report,indent=2));return report

def export(output):
    output=Path(output);target=output.parent/(output.name+'-review.zip')
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(output.rglob('*')):
            if p.is_file() and not p.name.endswith('.tmp'):z.write(p,p.relative_to(output))
    return target

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=('run','export'))
    p.add_argument('--main-root',type=Path);p.add_argument('--additional-root',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--mode',choices=('sample','full'),default='sample')
    p.add_argument('--studies',nargs='+',choices=('timing','application','causal','robustness'),default=['timing','application','causal','robustness'])
    p.add_argument('--workers',type=int,default=4);a=p.parse_args()
    if a.command=='export':print(export(a.output))
    else:
        if not 1<=a.workers<=8 or len(a.studies)!=len(set(a.studies)):raise ValueError('Invalid workers/studies')
        if not a.main_root or not a.additional_root:raise ValueError('Both original run roots required')
        import fcntl
        for root in (a.main_root.resolve(),a.additional_root.resolve()):
            if a.output.resolve()==root or root in a.output.resolve().parents:raise ValueError('Audit output must be outside both original run directories')
        a.output.mkdir(parents=True,exist_ok=True)
        with (a.output/'AUDIT.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);run(a)
