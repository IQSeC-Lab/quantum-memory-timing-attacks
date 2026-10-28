"""Synthetic audit tests. These fixtures are not NetSquid experiment results."""
from copy import deepcopy
import gzip
import json
import math
from pathlib import Path
import tempfile
import sys

import q12_design as d
from q12_raw_checks import AuditError, need, sha_file, trace, validate_record
from q12_raw_audit import audit_batch, validate_locations


def rejects(fn, message):
    try:
        fn()
    except AuditError as exc:
        assert message in str(exc), (message,str(exc))
    else:
        raise AssertionError('Expected rejection: '+message)


def fixture(workload='plus', arm='no_competitor', bits=(1,1)):
    spec=d.specification('pilot',workload,0,'primary_0500us',arm,0)
    spec['noise']='noise_free'
    if workload=='teleport':spec['fixed_state']=0
    inp=d.inputs(spec);start=inp['target_request_ns'];phase=d.phase_for(spec,{})
    planned=d.release_plan(d.EPOCH_NS,arm,phase,inp['blind_phase_ns'])
    def task(tid,pid,block,a,b,typ='HostLocalTask'):
        return dict(task_id=tid,pid=pid,block=block,task_type=typ,start_ns=float(a),end_ns=float(b))
    cpu=[task(0,0,'setup',start,start+20000)]
    jobs=[];last=start+20000
    if planned is not None:
        begin=max(planned,last+10000)
        for i in range(4):
            jobs.append(dict(pid=i+10,requested_ns=float(planned),admitted_ns=float(planned),instructions=30,kind='competitor',episode=None))
            cpu.append(task(i+10,i+10,'payload',begin+300000*i,begin+300000*(i+1)))
        last=begin+1200000
    events=[];qpu=[]
    def instruction(routine,name,a):
        return dict(kind='instruction',pid=0,routine=routine,name=name,start_ns=float(a),end_ns=float(a+1000),virt=0 if name in ('init','meas') else None)
    if workload=='plus':
        qpu.append(task(100,0,'prepare',start+20000,start+25000,'LocalRoutineTask'))
        events.extend([instruction('prepare_state','init',start+21000),instruction('prepare_state','h',start+23000)])
        ready=start+24000
        use=max(last,ready)+1000000
        qpu.append(task(101,0,'measure',use,use+5000,'LocalRoutineTask'))
        events.extend([instruction('measure_state','h',use),instruction('measure_state','meas',use+2000)])
        bells=None
    else:
        qpu.append(task(100,0,'epr',start+20000,start+21000,'MultiPairTask'))
        ready=start+21000
        events.append(dict(kind='epr_ready',pid=0,virt=0,end_ns=float(ready)))
        correction=max(last,ready)+10000
        events.append(instruction('corrections','set',correction))
        if bits[0]:events.append(instruction('corrections','z',correction+1000))
        if bits[1]:events.append(instruction('corrections','x',correction+2000))
        qpu.append(task(101,0,'correct',correction,correction+4000,'LocalRoutineTask'))
        use=correction+1000000
        qpu.append(task(102,0,'measure',use,use+5000,'LocalRoutineTask'))
        events.extend([instruction('verify_state','rot_y',use),instruction('verify_state','meas',use+2000)])
        bells=list(bits)
    finish=use+30000
    cpu.append(task(90,0,'output',use+10000,finish))
    cpu.sort(key=lambda r:(r['start_ns'],r['end_ns'],r['task_id']))
    peer=[task(0,0,'setup',start,start+10000),task(1,0,'reply',start+30000,start+40000)]
    raw=dict(completed=True,error=0,wrong_output=0,timeout=False,bell_bits=bells,spec=spec,inputs=inp,
        public_epoch_ns=d.EPOCH_NS,selected_phase_ns=phase,planned_release_ns=planned,target_pid=0,
        target_admitted_ns=float(start),target_finished_ns=float(finish),physical_storage_ns=float(use-ready),
        state_use_ns=float(use),admission_events=jobs,cpu_trace=cpu,qpu_trace=qpu,peer_cpu_trace=peer,
        quantum_events=events,actual_cpu_scheduler='CpuFcfsScheduler',sim_end_ns=float(d.HORIZON_NS))
    compact=dict(spec=spec,wrong_output=0,unfinished=0,application_failure=0,state=inp['state'],bell_bits=bells,
        physical_storage_ns=float(use-ready),target_finished_ns=float(finish),target_request_ns=inp['target_request_ns'],
        selected_phase_ns=phase,planned_release_ns=planned,competitor_service_ns=0 if planned is None else 1200000,
        bob_cpu_service_ns=sum(t['end_ns']-t['start_ns'] for t in cpu),alice_cpu_service_ns=20000.,
        qnos_instruction_count=sum(e['kind']=='instruction' for e in events),completed_epr_pairs=int(workload=='teleport'))
    return raw,compact,spec


def test_workloads_and_all_correction_branches():
    for w in d.WORKLOADS:
        for arm in d.ARMS:
            for b in [(0,0),(0,1),(1,0),(1,1)]:
                r,c,s=fixture(w,arm,b)
                m=validate_record(r,c,s,{})
                assert m['trials']==1 and m['completed']==1
                assert m['measured_competitor_service_ns']==(0 if arm=='no_competitor' else 1200000)


def test_zero_duration_boundary_and_overlap():
    good=[dict(task_id=0,start_ns=0.,end_ns=10.),dict(task_id=2,start_ns=10.,end_ns=10.),dict(task_id=1,start_ns=10.,end_ns=20.)]
    trace(good,'test',30,serial=True)
    bad=deepcopy(good);bad[1]['start_ns']=bad[1]['end_ns']=5.
    bad.sort(key=lambda r:(r['start_ns'],r['end_ns'],r['task_id']))
    rejects(lambda:trace(bad,'test',30,serial=True),'overlapping CPU')
    bad=deepcopy(good);bad[-1]['start_ns']=9.
    bad.sort(key=lambda r:(r['start_ns'],r['end_ns'],r['task_id']))
    rejects(lambda:trace(bad,'test',30,serial=True),'overlapping CPU')


def test_planned_roundoff_and_actual_causality():
    r,c,s=fixture('plus','learned')
    r['admission_events'][0]['admitted_ns']=math.nextafter(r['admission_events'][0]['admitted_ns'],math.inf)
    # The first payload starts at the planned release; moving it by one ULP
    # preserves true actual causality while remaining within duration tolerance.
    t=next(t for t in r['cpu_trace'] if t['pid']==10)
    t['start_ns']=r['admission_events'][0]['admitted_ns']
    validate_record(r,c,s,{})
    t['start_ns']=r['admission_events'][0]['admitted_ns']-1e-7
    rejects(lambda:validate_record(r,c,s,{}),'before actual admission')


def test_changed_work_budget():
    r,c,s=fixture('teleport','learned')
    next(t for t in r['cpu_trace'] if t['pid']==13)['end_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'instruction budget')


def test_storage_and_quantum_containment():
    r,c,s=fixture('teleport')
    r['physical_storage_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'Physical storage reconstruction')
    r,c,s=fixture('plus')
    e=next(e for e in r['quantum_events'] if e['name']=='init')
    e['start_ns']=r['target_admitted_ns']+10;e['end_ns']=e['start_ns']+1000
    rejects(lambda:validate_record(r,c,s,{}),'unique recorded QPU routine')


def test_correction_bits_and_compact_tampering():
    r,c,s=fixture('teleport')
    r['bell_bits']=[0,1]
    rejects(lambda:validate_record(r,c,s,{}),'Pauli correction branch')
    r,c,s=fixture('plus')
    c['wrong_output']=1
    rejects(lambda:validate_record(r,c,s,{}),'Compact reconstruction mismatch')


def test_seed_plan_and_ownership():
    r,c,s=fixture('plus','learned')
    r['planned_release_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'Release plan')
    r,c,s=fixture('plus')
    r['inputs']['latency_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'Seeded raw inputs')
    r,c,s=fixture('teleport')
    r['quantum_events'][0]['pid']=99
    rejects(lambda:validate_record(r,c,s,{}),'quantum-event owner')


def test_checkpoint_resume_corruption_and_read_only():
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp);folder=base/'original'/'pilot'/'batch-00000';folder.mkdir(parents=True)
        out=base/'audit';out.mkdir()
        r,c,s=fixture('plus')
        job=dict(stage='pilot',specs=[s],frozen={})
        (folder/'job.json').write_text(json.dumps(job))
        for kind,value in [('raw',r),('compact',c)]:
            with gzip.open(folder/(kind+'.gz'),'wt') as f:f.write(json.dumps(value)+'\n')
        stats=dict(trials=1,validation='Q12 full raw-record validation passed')
        (folder/'stats.json').write_text(json.dumps(stats))
        marker=dict(job_hash=d.stable(job))
        for kind,name in [('raw','raw.gz'),('compact','compact.gz'),('statistics','stats.json')]:
            marker[kind+'_file']=name;marker[kind+'_sha256']=sha_file(folder/name)
        (folder/'completed.json').write_text(json.dumps(marker))
        entry=dict(batch='batch-00000',**marker,stats=stats)
        task=(str(folder),str(out),entry,job,'synthetic-test-code')
        before={p.name:p.read_bytes() for p in folder.iterdir()}
        assert not audit_batch(task)['reused']
        assert audit_batch(task)['reused']
        assert before=={p.name:p.read_bytes() for p in folder.iterdir()}
        receipt=out/'checkpoints'/'pilot'/'batch-00000.json'
        value=json.loads(receipt.read_text());value['metrics']['wrong_outputs']=1;receipt.write_text(json.dumps(value))
        rejects(lambda:audit_batch(task),'Audit checkpoint checksum mismatch')
        receipt.unlink()
        with (folder/'raw.gz').open('ab') as f:f.write(b'corrupt')
        rejects(lambda:audit_batch(task),'raw checksum mismatch')


def test_extra_records_rejected():
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp);folder=base/'original';folder.mkdir();out=base/'audit'
        r,c,s=fixture()
        job=dict(stage='pilot',specs=[s],frozen={});(folder/'job.json').write_text(json.dumps(job))
        for kind,value in [('raw',r),('compact',c)]:
            with gzip.open(folder/(kind+'.gz'),'wt') as f:f.write((json.dumps(value)+'\n')*2)
        stats=dict(trials=1,validation='Q12 full raw-record validation passed');(folder/'stats.json').write_text(json.dumps(stats))
        marker=dict(job_hash=d.stable(job))
        for kind,name in [('raw','raw.gz'),('compact','compact.gz'),('statistics','stats.json')]:
            marker[kind+'_file']=name;marker[kind+'_sha256']=sha_file(folder/name)
        (folder/'completed.json').write_text(json.dumps(marker))
        entry=dict(batch='batch-00000',**marker,stats=stats)
        rejects(lambda:audit_batch((str(folder),str(out),entry,job,'test')),'Unexpected extra')


def test_output_path_protection_and_no_simulator():
    rejects(lambda:validate_locations(Path('/tmp/q12-original'),Path('/tmp/q12-original/audit')),'separate from')
    rejects(lambda:validate_locations(Path('/tmp/q12-original'),Path('/tmp')),'separate from')
    assert not any(n=='netsquid' or n.startswith('netsquid.') or n=='qoala' or n.startswith('qoala.') for n in sys.modules)


def main():
    tests=[v for k,v in globals().items() if k.startswith('test_')]
    for fn in tests:
        fn();print('PASS',fn.__name__,flush=True)
    print(f'{len(tests)} synthetic audit tests passed. Original Q12 raw files have NOT been audited by these tests. No NetSquid runtime executed.',flush=True)


if __name__=='__main__':main()
