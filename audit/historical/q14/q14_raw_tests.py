"""Synthetic audit checks; fixtures are not NetSquid observations."""
from copy import deepcopy
import gzip,json,math
from pathlib import Path
import tempfile,sys
import numpy as np
import q14_design as d
from synthetic_records import fixture as make_fixture
from q13_raw_checks import AuditError,need,sha_file,trace,validate_record as checked_record,target_state
from q14_raw_audit import audit_batch,validate_locations


def rejects(fn,message):
    try:fn()
    except AuditError as exc:assert message in str(exc),(message,str(exc))
    else:raise AssertionError('Expected rejection: '+message)


FROZEN={w+'|'+str(c):dict(phase_ns=1500000,source_observations_sha256='0'*64) for w in d.WORKLOADS for c in range(d.CAMPAIGNS)}
def validate_record(r,c,s,frozen):
    return checked_record(r,c,s,FROZEN,design=d)
def fixture(workload='plus',arm='no_competitor',bits=(1,1),profile='noise_free',state=0):
    return make_fixture(d.spec('pilot',workload,profile,0,arm,0,fixed_state=state),FROZEN,bits)


def test_checkpoint_resume_corruption_and_read_only():
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp);folder=base/'original'/'pilot'/'batch-00000';folder.mkdir(parents=True)
        out=base/'audit';out.mkdir()
        r,c,s=fixture('plus')
        job=dict(stage='pilot',specs=[s],frozen=FROZEN)
        (folder/'job.json').write_text(json.dumps(job))
        for kind,value in [('raw',r),('compact',c)]:
            with gzip.open(folder/(kind+'.gz'),'wt') as f:f.write(json.dumps(value)+'\n')
        stats=dict(trials=1,validation='Q14 full raw-record validation passed')
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
        job=dict(stage='pilot',specs=[s],frozen=FROZEN);(folder/'job.json').write_text(json.dumps(job))
        for kind,value in [('raw',r),('compact',c)]:
            with gzip.open(folder/(kind+'.gz'),'wt') as f:f.write((json.dumps(value)+'\n')*2)
        stats=dict(trials=1,validation='Q14 full raw-record validation passed');(folder/'stats.json').write_text(json.dumps(stats))
        marker=dict(job_hash=d.stable(job))
        for kind,name in [('raw','raw.gz'),('compact','compact.gz'),('statistics','stats.json')]:
            marker[kind+'_file']=name;marker[kind+'_sha256']=sha_file(folder/name)
        (folder/'completed.json').write_text(json.dumps(marker))
        entry=dict(batch='batch-00000',**marker,stats=stats)
        rejects(lambda:audit_batch((str(folder),str(out),entry,job,'test')),'Unexpected extra')


def test_output_path_protection_and_no_simulator():
    rejects(lambda:validate_locations(Path('/tmp/q13-original'),Path('/tmp/q13-original/audit')),'separate from')
    rejects(lambda:validate_locations(Path('/tmp/q13-original'),Path('/tmp')),'separate from')
    assert not any(n=='netsquid' or n.startswith('netsquid.') or n=='qoala' or n.startswith('qoala.') for n in sys.modules)


def test_workloads_states_and_bell_branches():
    for w in d.WORKLOADS:
        for arm in d.ARMS:
            for state in range(6) if w=='teleport' else [0]:
                for bits in ((0,0),(0,1),(1,0),(1,1)):
                    for profile in ('noise_free',*d.PROFILES):
                        r,c,s=fixture(w,arm,bits,profile,state)
                        m=validate_record(r,c,s,{})
                        assert m['trials']==m['completed']==m['density_matrices']==1


def test_zero_duration_boundary_and_real_overlap():
    rows=[dict(task_id=0,start_ns=0.,end_ns=10.),dict(task_id=2,start_ns=10.,end_ns=10.),dict(task_id=1,start_ns=10.,end_ns=20.)]
    trace(rows,'test',30,True)
    rows[-1]['start_ns']=9.;rows.sort(key=lambda t:(t['start_ns'],t['end_ns'],t['task_id']))
    rejects(lambda:trace(rows,'test',30,True),'overlapping CPU')


def test_actual_admission_and_budget():
    r,c,s=fixture('plus','learned')
    t=next(t for t in r['cpu_trace'] if t['pid']==10)
    j=r['admission_events'][0]
    j['admitted_ns']=math.nextafter(j['admitted_ns'],math.inf)
    t['start_ns']=max(t['start_ns'],j['admitted_ns'])
    validate_record(r,c,s,{})
    t['start_ns']=j['admitted_ns']-1e-7
    rejects(lambda:validate_record(r,c,s,{}),'before actual admission')
    r,c,s=fixture('teleport','learned')
    next(t for t in r['cpu_trace'] if t['pid']==13)['end_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'instruction budget')


def test_storage_and_channel_location():
    r,c,s=fixture('teleport')
    r['memory_channel'][0]['age_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'Physical storage reconstruction')
    r,c,s=fixture('plus')
    r['memory_channel'][0]['use_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'application boundary')
    r,c,s=fixture('plus')
    r['memory_channel'].append(deepcopy(r['memory_channel'][0]))
    rejects(lambda:validate_record(r,c,s,{}),'more than once')


def test_density_fidelity_and_profile_corruption():
    r,c,s=fixture('plus',profile='final_si')
    r['memory_channel'][0]['density_real'][0][1]=0
    r['memory_channel'][0]['density_real'][1][0]=0
    rejects(lambda:validate_record(r,c,s,{}),'Observed density')
    r,c,s=fixture('teleport',profile='final_si',state=4)
    r['memory_channel'][0]['fidelity']+=.01
    rejects(lambda:validate_record(r,c,s,{}),'Density/fidelity')
    r,c,s=fixture('plus',profile='final_si');r['native_t2_ns']=4000000
    rejects(lambda:validate_record(r,c,s,{}),'Native noise')


def test_correction_plan_input_and_compact_corruption():
    r,c,s=fixture('teleport');r['bell_bits']=[0,1]
    rejects(lambda:validate_record(r,c,s,{}),'Pauli correction')
    r,c,s=fixture('plus','learned');r['planned_release_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'Release plan')
    r,c,s=fixture();r['inputs']['latency_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'Seeded raw inputs')
    r,c,s=fixture();c['wrong_output']=1
    rejects(lambda:validate_record(r,c,s,{}),'Compact reconstruction')


def test_transferred_policy_identity():
    r,c,s=fixture('plus','learned')
    r['selected_phase_ns']+=250000
    rejects(lambda:validate_record(r,c,s,{}),'Transferred policy identity')
    r,c,s=fixture('teleport','learned')
    r['policy_sha256']='0'*64
    rejects(lambda:validate_record(r,c,s,{}),'Transferred policy identity')

def test_nonfinite_json_rejected():
    from q14_raw_audit import strict_line
    rejects(lambda:strict_line('{"unused": NaN}'),'Nonfinite JSON')

def test_record_counts_and_sample_selection():
    from q14_raw_audit import fixed_sample
    specs=[s for stage in ('pilot','evaluation') for s in d.specifications(stage)]
    assert len(specs)==100248
    assert sum(fixed_sample(s) for s in specs)==30


def main():
    tests=[v for k,v in globals().items() if k.startswith('test_')]
    for fn in tests:fn();print('PASS',fn.__name__,flush=True)
    print(f'{len(tests)} synthetic audit tests passed. Original Q14 raw files have NOT been audited by these tests. No NetSquid runtime executed.',flush=True)


if __name__=='__main__':main()

