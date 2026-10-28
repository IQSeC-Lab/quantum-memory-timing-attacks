"""Synthetic audit checks; fixtures are not NetSquid observations."""
from copy import deepcopy
import gzip,json,math
from pathlib import Path
import tempfile,sys
import numpy as np
import q13_design as d
import physics_reference as ref
from q13_raw_checks import AuditError,need,sha_file,trace,validate_record,target_state,baseline_reference
from q13_raw_audit import audit_batch,validate_locations


def rejects(fn,message):
    try:fn()
    except AuditError as exc:assert message in str(exc),(message,str(exc))
    else:raise AssertionError('Expected rejection: '+message)


def fixture(workload='plus',arm='no_competitor',bits=(1,1),profile='noise_free',state=0):
    spec=d.spec('pilot',workload,profile,0,'coarse_500us',arm,0,fixed_state=state)
    inp=d.inputs(spec);start=inp['target_request_ns']
    planned=d.release_plan(d.EPOCH_NS,workload,arm,inp['random_phase_ns'])
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
        planned_release_ns=planned,target_pid=0,
        target_admitted_ns=float(start),target_finished_ns=float(finish),native_t2_ns=0,admission_events=jobs,cpu_trace=cpu,qpu_trace=qpu,peer_cpu_trace=peer,
        quantum_events=events,actual_cpu_scheduler='CpuFcfsScheduler',sim_end_ns=float(d.HORIZON_NS))
    factor=1. if profile=='noise_free' else math.exp(-((use-ready)/1e6/d.PROFILES[profile]['t_ms'])**d.PROFILES[profile]['exponent'])
    target=target_state(workload,state);rho=factor*np.outer(target,target.conj())+(1-factor)*np.eye(2)/2
    fidelity=(1+factor)/2
    raw['memory_channel']=[dict(pid=0,physical_id=0,start_ns=float(ready),use_ns=float(use),age_ns=float(use-ready),contraction=factor,
        fidelity=fidelity,expected_infidelity=1-fidelity,density_real=rho.real.tolist(),density_imag=rho.imag.tolist())]
    compact=dict(spec=spec,state=inp['state'],bell_bits=bells,pairing_sha256=d.stable(inp),target_request_ns=inp['target_request_ns'],
        wrong_output=0,unfinished=0,fidelity=fidelity,expected_infidelity=1-fidelity,physical_storage_ns=float(use-ready),
        planned_release_ns=planned,competitor_service_ns=0 if planned is None else 1200000,simulator_executions=1,target_finished_ns=float(finish))
    return raw,compact,spec


def test_checkpoint_resume_corruption_and_read_only():
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp);folder=base/'original'/'pilot'/'batch-00000';folder.mkdir(parents=True)
        out=base/'audit';out.mkdir()
        r,c,s=fixture('plus')
        job=dict(stage='pilot',specs=[s],frozen={})
        (folder/'job.json').write_text(json.dumps(job))
        for kind,value in [('raw',r),('compact',c)]:
            with gzip.open(folder/(kind+'.gz'),'wt') as f:f.write(json.dumps(value)+'\n')
        stats=dict(trials=1,validation='Q13 full raw-record validation passed')
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
        stats=dict(trials=1,validation='Q13 full raw-record validation passed');(folder/'stats.json').write_text(json.dumps(stats))
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
    r,c,s=fixture('plus','fixed_phase')
    t=next(t for t in r['cpu_trace'] if t['pid']==10)
    j=r['admission_events'][0]
    j['admitted_ns']=math.nextafter(j['admitted_ns'],math.inf)
    t['start_ns']=max(t['start_ns'],j['admitted_ns'])
    validate_record(r,c,s,{})
    t['start_ns']=j['admitted_ns']-1e-7
    rejects(lambda:validate_record(r,c,s,{}),'before actual admission')
    r,c,s=fixture('teleport','fixed_phase')
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
    r,c,s=fixture('plus','fixed_phase');r['planned_release_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'Release plan')
    r,c,s=fixture();r['inputs']['latency_ns']+=1
    rejects(lambda:validate_record(r,c,s,{}),'Seeded raw inputs')
    r,c,s=fixture();c['wrong_output']=1
    rejects(lambda:validate_record(r,c,s,{}),'Compact reconstruction')


def baseline_fixture(s):
    import hashlib
    matrix=ref.RHO_EPR_ROUNDED if s['matrix']=='rounded_si' else np.asarray(d.MODELS['exact_epr_matrix'],complex)
    alpha=(np.pi/2,np.pi)[s['alpha_index']];theta=(np.pi/4,np.pi/2,np.pi)[s['theta_index']]
    pars=d.PROFILES[s['profile']];target=ref.ideal_target(alpha)
    duration=int(round(s['age_ms']*1e6));endpoints=[duration] if s['partition']=='whole' else sorted({duration//3,2*duration//3,duration})
    base=int(hashlib.sha256(d.stable(s).encode()).hexdigest()[:8],16)
    records=[]
    for index,(branch,prob,state) in enumerate(ref.delegated_branches(alpha,theta,matrix)):
        rho=ref.depolarize_between(state,0,s['age_ms'],pars['t_ms'],pars['exponent'])
        fidelity=float((target.conj()@rho@target).real)
        intervals=[];previous=0
        for end in endpoints:
            factor=math.exp(-((end/1e6/pars['t_ms'])**pars['exponent']-(previous/1e6/pars['t_ms'])**pars['exponent']))
            intervals.append(dict(start_age_ns=previous,end_age_ns=end,sim_time_ns=float(end),contraction=factor));previous=end
        seed=int(np.random.SeedSequence([d.ROOTS['baseline'],base,index]).generate_state(1)[0]%(2**31-1))+1
        records.append(dict(seed=seed,branch=branch,branch_probability=prob,expected_branch_probability=prob,
            max_density_error=0.,fidelity=fidelity,reference_fidelity=fidelity,observed_density_real=rho.real.tolist(),
            observed_density_imag=rho.imag.tolist(),applied_intervals=intervals,sim_end_ns=float(duration)))
    raw=dict(spec=s,baseline_records=records,simulator_executions=2,max_density_error=0.,both_branches_checked=True,tolerance=2e-9)
    compact=dict(spec=s,simulator_executions=2,both_branches_checked=True,max_density_error=0.,max_fidelity_error=0.)
    return raw,compact,s


def test_all_baseline_circuits_and_partitions():
    for s in d.specifications('baseline'):
        r,c,s=baseline_fixture(s);m=validate_record(r,c,s,{})
        assert m['baseline_branch_records']==2 and m['max_density_error']<2e-9


def test_baseline_state_seed_and_age_reset_rejected():
    s=next(s for s in d.specifications('baseline') if s['age_ms']==13 and s['partition']=='split')
    r,c,s=baseline_fixture(s);r['baseline_records'][0]['seed']+=1
    rejects(lambda:validate_record(r,c,s,{}),'Baseline seed')
    r,c,s=baseline_fixture(s);r['baseline_records'][0]['applied_intervals'][1]['start_age_ns']=0
    rejects(lambda:validate_record(r,c,s,{}),'partition reset')
    r,c,s=baseline_fixture(s);r['baseline_records'][0]['observed_density_real']=[[1.,0.],[0.,0.]];r['baseline_records'][0]['observed_density_imag']=[[0.,0.],[0.,0.]]
    rejects(lambda:validate_record(r,c,s,{}),'independent circuit')


def main():
    tests=[v for k,v in globals().items() if k.startswith('test_')]
    for fn in tests:fn();print('PASS',fn.__name__,flush=True)
    print(f'{len(tests)} synthetic audit tests passed. Original Q13 raw files have NOT been audited by these tests. No NetSquid runtime executed.',flush=True)


if __name__=='__main__':main()

