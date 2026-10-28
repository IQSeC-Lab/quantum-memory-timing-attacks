"""Independent checks of Q13 recorded traces and states; no simulator imports.

Q14 can reuse the record checker with its explicitly supplied frozen design.
The optional design argument changes only protocol inputs and policy metadata.
"""
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import q13_design as original_design

VERSION = 'q13-raw-audit-1'
TIME_TOL_NS = 1e-6
SERVICE_SUM_TOL_NS = 1e-5
STATE_TOL = 2e-9


class AuditError(ValueError):
    pass


def need(ok, message):
    if not ok:
        raise AuditError(message)


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    def reject(value):
        raise AuditError('Nonfinite JSON token: '+value)
    return json.loads(Path(path).read_text(), parse_constant=reject)


def member(folder, name):
    need(isinstance(name,str) and Path(name).name==name and name not in ('','.','..'), 'Invalid checkpoint basename')
    path=Path(folder)/name
    need(path.is_file() and not path.is_symlink(), 'Missing file or symlink: '+str(path))
    return path


def close(a,b,label,tol=TIME_TOL_NS):
    need(isinstance(a,(int,float)) and isinstance(b,(int,float)) and math.isfinite(a) and math.isfinite(b), label+': nonfinite/nonnumeric value')
    need(abs(a-b)<=tol, f'{label}: observed={a!r}, expected={b!r}, delta={a-b!r}')


def trace(rows,label,horizon,serial=False):
    need(isinstance(rows,list) and rows, label+': missing trace')
    need(len({r['task_id'] for r in rows})==len(rows), label+': duplicate task id')
    for r in rows:
        a,b=r['start_ns'],r['end_ns']
        need(math.isfinite(a) and math.isfinite(b) and 0<=a<=b<=horizon,label+': invalid interval')
    if serial:
        ordered=sorted(rows,key=lambda r:(r['start_ns'],r['end_ns'],r['task_id']))
        need(rows==ordered,label+': noncanonical boundary order')
        maximum_end=0.
        for r in ordered:
            need(r['start_ns']>=maximum_end-TIME_TOL_NS,label+': overlapping CPU service')
            maximum_end=max(maximum_end,r['end_ns'])
    return math.fsum(r['end_ns']-r['start_ns'] for r in rows)


def blank_metrics():
    return dict(trials=0,completed=0,wrong_outputs=0,simulator_executions=0,
        instruction_events=0,epr_events=0,competitor_jobs=0,
        measured_competitor_service_ns=0.,baseline_branch_records=0,
        density_matrices=0,max_abs_admission_delta_ns=0.,
        max_abs_payload_service_error_ns=0.,max_abs_competitor_budget_error_ns=0.,
        max_abs_storage_reconstruction_error_ns=0.,max_density_error=0.,
        max_fidelity_error=0.,min_start_minus_actual_admission_ns=None)


def merge_metrics(total,part):
    for k,v in part.items():
        if k.startswith('max_'):total[k]=max(total[k],v)
        elif k.startswith('min_'):
            if v is not None:total[k]=v if total[k] is None else min(total[k],v)
        else:total[k]+=v


def density(real,imag):
    rho=np.asarray(real,dtype=float)+1j*np.asarray(imag,dtype=float)
    need(rho.shape==(2,2) and np.isfinite(rho).all(),'Invalid density-matrix shape or value')
    need(np.max(np.abs(rho-rho.conj().T))<STATE_TOL,'Non-Hermitian density matrix')
    need(abs(np.trace(rho)-1)<STATE_TOL,'Density matrix trace differs from one')
    need(np.linalg.eigvalsh(rho).min()>-STATE_TOL,'Nonpositive density matrix')
    return rho


def target_state(workload,state):
    vectors=np.asarray([[1,1],[1,-1],[1,1j],[1,-1j],[1,0],[0,1]],dtype=complex)
    vectors[:4]/=np.sqrt(2)
    need(state in range(6),'Unknown state label')
    return vectors[0 if workload=='plus' else state]


def baseline_reference(s,branch,d):
    """Direct projector calculation, independent of physics_reference.py."""
    eye=np.eye(2,dtype=complex)
    x=np.array([[0,1],[1,0]],complex)
    y=np.array([[0,-1j],[1j,0]],complex)
    z=np.diag([1,-1]).astype(complex)
    def rot(p,t):return np.cos(t/2)*eye-1j*np.sin(t/2)*p
    rho=np.asarray([[.049,0,0,0],[0,.437,.284,0],[0,.284,.454,0],[0,0,0,.061]]
                   if s['matrix']=='rounded_si' else d.MODELS['exact_epr_matrix'],complex)
    rho/=np.trace(rho).real
    alpha=(np.pi/2,np.pi)[s['alpha_index']]
    theta=(np.pi/4,np.pi/2,np.pi)[s['theta_index']]
    fix=np.kron(eye,x);rho=fix@rho@fix.conj().T
    # Apply each chronological client operation separately.
    for op in (rot(y,np.pi/2),rot(x,theta),rot(x,np.pi)):
        u=np.kron(op,eye);rho=u@rho@u.conj().T
    indices=[2*branch,2*branch+1]
    state=rho[np.ix_(indices,indices)]
    probability=float(np.trace(state).real);state/=probability
    p=d.PROFILES[s['profile']]
    factor=math.exp(-(s['age_ms']/p['t_ms'])**p['exponent'])
    state=factor*state+(1-factor)*eye/2
    for op in (rot(y,np.pi/2),rot(x,alpha-theta+branch*np.pi),rot(x,np.pi)):
        state=op@state@op.conj().T
    target=((x+z)/np.sqrt(2))@rot(z,alpha)@np.array([1,1],complex)/np.sqrt(2)
    return probability,state,float((target.conj()@state@target).real)


def validate_baseline(r,c,s,d):
    need(r['spec']==c['spec']==s,'Raw/compact/job specification mismatch')
    records=r['baseline_records']
    need(2<=len(records)<=64 and r['both_branches_checked'] is True,'Baseline branch gate')
    need(r['simulator_executions']==len(records),'Baseline execution count')
    need(r['tolerance']==STATE_TOL,'Baseline tolerance changed')
    base=int(hashlib.sha256(d.stable(s).encode()).hexdigest()[:8],16)
    seen=set();m=blank_metrics();m.update(trials=1,simulator_executions=len(records),baseline_branch_records=len(records))
    duration=int(round(s['age_ms']*1e6))
    endpoints=[duration] if s['partition']=='whole' else sorted({duration//3,2*duration//3,duration})
    p=d.PROFILES[s['profile']]
    for index,record in enumerate(records):
        need(seen!={0,1},'Baseline continued after both branches')
        branch=record['branch'];need(branch in (0,1),'Invalid baseline branch')
        seen.add(branch)
        seed=int(np.random.SeedSequence([d.ROOTS['baseline'],base,index]).generate_state(1)[0]%(2**31-1))+1
        need(record['seed']==seed,'Baseline seed differs from frozen sequence')
        prob,expected,fidelity=baseline_reference(s,branch,d)
        rho=density(record['observed_density_real'],record['observed_density_imag'])
        error=float(np.max(np.abs(rho-expected)))
        need(error<STATE_TOL,'Baseline observed state disagrees with independent circuit')
        for k in ('branch_probability','expected_branch_probability'):close(record[k],prob,k,STATE_TOL)
        for k in ('fidelity','reference_fidelity'):close(record[k],fidelity,k,STATE_TOL)
        close(record['max_density_error'],error,'Baseline reported density error',STATE_TOL)
        close(record['sim_end_ns'],duration,'Baseline simulation end')
        intervals=record['applied_intervals'];need(len(intervals)==len(endpoints),'Memory interval count')
        previous=0;product=1.
        for q,end in zip(intervals,endpoints):
            need(q['start_age_ns']==previous and q['end_age_ns']==end,'Memory age partition reset or gap')
            close(q['sim_time_ns'],end,'Memory partition simulation time')
            factor=math.exp(-((end/1e6/p['t_ms'])**p['exponent']-(previous/1e6/p['t_ms'])**p['exponent']))
            close(q['contraction'],factor,'Absolute-age contraction',1e-12)
            product*=q['contraction'];previous=end
        close(product,math.exp(-(s['age_ms']/p['t_ms'])**p['exponent']),'Whole/split contraction',1e-12)
        m['density_matrices']+=1;m['max_density_error']=max(m['max_density_error'],error)
        m['max_fidelity_error']=max(m['max_fidelity_error'],abs(record['fidelity']-fidelity))
    need(seen=={0,1},'Both baseline branches absent')
    reconstructed=dict(spec=s,simulator_executions=len(records),both_branches_checked=True,
        max_density_error=max(x['max_density_error'] for x in records),
        max_fidelity_error=max(abs(x['fidelity']-x['reference_fidelity']) for x in records))
    close(r['max_density_error'],reconstructed['max_density_error'],'Baseline maximum density error',1e-12)
    need(c==reconstructed,'Baseline compact reconstruction mismatch')
    return m


def validate_record(r,c,s,frozen,design=None):
    d=original_design if design is None else design
    if s['stage']=='baseline':return validate_baseline(r,c,s,d)
    need(r['spec']==c['spec']==s,'Raw/compact/job specification mismatch')
    inp=d.inputs(s);need(r['inputs']==inp,'Seeded raw inputs differ from frozen design')
    transferred=hasattr(d,'phase_for')
    if transferred:
        phase=d.phase_for(s,frozen)
        planned=d.release_plan(d.EPOCH_NS,s['arm'],phase,inp['random_phase_ns'])
        need(r['selected_phase_ns']==phase and r['policy_sha256']==d.stable(frozen),'Transferred policy identity')
    else:
        planned=d.release_plan(d.EPOCH_NS,s['workload'],s['arm'],inp['random_phase_ns'])
    need(r['planned_release_ns']==planned,'Release plan mismatch')
    need(r['native_t2_ns']==0,'Native noise was not disabled')
    need(r['actual_cpu_scheduler']=='CpuFcfsScheduler','Unexpected scheduler')
    need(r['sim_end_ns']==d.HORIZON_NS,'Unexpected simulation horizon')
    pid=r['target_pid'];admitted=r['target_admitted_ns']
    close(admitted,inp['target_request_ns'],'Target admission')
    cpu,peer,qpu=r['cpu_trace'],r['peer_cpu_trace'],r['qpu_trace']
    trace(cpu,'Bob CPU',d.HORIZON_NS,True);trace(peer,'Alice CPU',d.HORIZON_NS,True)
    trace(qpu,'Bob QPU',d.HORIZON_NS)
    need(all(t['start_ns']>=admitted for t in cpu if t['pid']==pid),'Target CPU starts before actual admission')
    need(all(t['start_ns']>=admitted for t in peer),'Peer CPU starts before target admission')
    need(all(t['pid']==pid and t['start_ns']>=admitted for t in qpu),'Unexpected QPU owner or pre-admission execution')
    jobs=r['admission_events'];payloads=[t for t in cpu if t['block']=='payload']
    need(len(jobs)==(d.JOBS if planned is not None else 0),'Competitor job count mismatch')
    need(len({j['pid'] for j in jobs})==len(jobs) and pid not in {j['pid'] for j in jobs},'Duplicate/target job pid')
    bypid={t['pid']:t for t in payloads}
    need(len(payloads)==len(bypid)==len(jobs) and set(bypid)=={j['pid'] for j in jobs},'Payload/job ownership mismatch')
    need({t['pid'] for t in cpu}=={pid,*bypid},'Unexpected CPU owner')
    m=blank_metrics();m.update(trials=1,completed=int(bool(r['completed'])),wrong_outputs=r['wrong_output'],simulator_executions=1,competitor_jobs=len(jobs))
    for j in jobs:
        need(j['kind']=='competitor' and j['instructions']==d.INSTRUCTIONS and j['requested_ns']==planned,'Wrong job kind, budget or release')
        t=bypid[j['pid']];close(j['admitted_ns'],j['requested_ns'],'Classical admission')
        slack=t['start_ns']-j['admitted_ns'];need(slack>=0,'Payload starts before actual admission')
        error=t['end_ns']-t['start_ns']-d.INSTRUCTIONS*d.HOST_INSTRUCTION_NS
        need(abs(error)<TIME_TOL_NS,'Payload duration differs from instruction budget')
        m['max_abs_admission_delta_ns']=max(m['max_abs_admission_delta_ns'],abs(j['admitted_ns']-j['requested_ns']))
        m['max_abs_payload_service_error_ns']=max(m['max_abs_payload_service_error_ns'],abs(error))
        m['min_start_minus_actual_admission_ns']=slack if m['min_start_minus_actual_admission_ns'] is None else min(m['min_start_minus_actual_admission_ns'],slack)
    measured=math.fsum(t['end_ns']-t['start_ns'] for t in payloads)
    budget=0 if planned is None else d.SERVICE_NS
    need(abs(measured-budget)<SERVICE_SUM_TOL_NS,'Measured equal-work budget mismatch')
    m['measured_competitor_service_ns']=measured;m['max_abs_competitor_budget_error_ns']=abs(measured-budget)
    events=r['quantum_events'];instructions=[e for e in events if e['kind']=='instruction'];eprs=[e for e in events if e['kind']=='epr_ready']
    need(len(instructions)+len(eprs)==len(events),'Unknown quantum-event type')
    blocks={'prepare_state':'prepare','measure_state':'measure','corrections':'correct','verify_state':'measure'}
    for e in events:
        need(e['pid']==pid,'Unexpected quantum-event owner')
        need(math.isfinite(e['end_ns']) and admitted<=e['end_ns']<=d.HORIZON_NS,'Invalid quantum-event end')
        if e['kind']=='instruction':
            need(math.isfinite(e['start_ns']) and admitted<=e['start_ns']<=e['end_ns'],'Invalid instruction interval')
            need(e['routine'] in blocks,'Unexpected routine')
            containers=[t for t in qpu if t['block']==blocks[e['routine']] and t['task_type']=='LocalRoutineTask'
                        and t['start_ns']-TIME_TOL_NS<=e['start_ns'] and e['end_ns']<=t['end_ns']+TIME_TOL_NS]
            need(len(containers)==1,'Instruction outside unique recorded QPU routine')
        else:
            need(e.get('virt')==0,'Unexpected EPR qubit')
            need(any(t['block']=='epr' and t['start_ns']-TIME_TOL_NS<=e['end_ns']<=t['end_ns']+TIME_TOL_NS for t in qpu),'EPR event outside request task')
    m['instruction_events']=len(instructions);m['epr_events']=len(eprs)
    need(isinstance(r['completed'],bool) and isinstance(r['timeout'],bool),'Invalid completion flags')
    channel=r['memory_channel'];need(isinstance(channel,list) and len(channel)<=1,'Memory channel applied more than once')
    age,fidelity=None,None
    if r['completed']:
        need(not r['timeout'] and r['wrong_output'] in (0,1) and r['error']==r['wrong_output'],'Completed outcome inconsistency')
        output=[t for t in cpu if t['pid']==pid and t['block']=='output'];need(len(output)==1,'Missing/duplicate target output')
        close(r['target_finished_ns'],output[0]['end_ns'],'Target output completion')
        need(all(e['end_ns']<=r['target_finished_ns']+TIME_TOL_NS for e in events),'Event after target completion')
        need(len(channel)==1,'Completed execution missing channel snapshot')
    else:
        need(r['timeout'] and r['error']==1 and r['wrong_output']==0,'Unfinished outcome inconsistency')
    if channel:
        q=channel[0];need(q['pid']==pid and isinstance(q['physical_id'],int) and q['physical_id']>=0,'Channel owner or physical qubit mismatch')
        if s['workload']=='plus':
            need(not eprs and r['bell_bits'] is None,'Unexpected plus EPR record')
            starts=[e for e in instructions if e['routine']=='prepare_state' and e['name']=='h']
            ends=[e for e in instructions if e['routine']=='measure_state' and e['name']=='h']
            init=[e for e in instructions if e['routine']=='prepare_state' and e['name']=='init']
            need(len(init)==len(starts)==1 and init[0]['end_ns']<=starts[0]['start_ns']+TIME_TOL_NS,'Plus preparation order')
            meas=[e for e in instructions if e['routine']=='measure_state' and e['name']=='meas']
        else:
            bits=r['bell_bits'];need(isinstance(bits,list) and len(bits)==2 and all(b in (0,1) for b in bits),'Invalid Bell bits')
            starts=eprs;ends=[e for e in instructions if e['routine']=='verify_state' and e['name'].startswith('rot_')]
            corrections=[e for e in instructions if e['routine']=='corrections']
            need(corrections and len(starts)==len(ends)==1,'Missing teleportation correction or storage event')
            for gate,bit in [('z',bits[0]),('x',bits[1])]:need(sum(e['name']==gate for e in corrections)==bit,'Pauli correction branch mismatch: '+gate)
            need(all(starts[0]['end_ns']-TIME_TOL_NS<=e['start_ns'] and e['end_ns']<=ends[0]['start_ns']+TIME_TOL_NS for e in corrections),'Correction outside EPR-to-use interval')
            meas=[e for e in instructions if e['routine']=='verify_state' and e['name']=='meas']
        need(len(starts)==len(ends)==1,'Missing/duplicate storage boundary')
        age=ends[0]['start_ns']-starts[0]['end_ns'];need(age>=0,'Negative storage interval')
        close(q['start_ns'],starts[0]['end_ns'],'Channel storage start')
        close(q['use_ns'],ends[0]['start_ns'],'Channel application boundary')
        close(q['age_ns'],age,'Physical storage reconstruction')
        need(q['age_ns']==q['use_ns']-q['start_ns'],'Channel age arithmetic')
        m['max_abs_storage_reconstruction_error_ns']=abs(q['age_ns']-age)
        if r['completed']:
            need(len(meas)==1 and ends[0]['end_ns']<=meas[0]['start_ns']+TIME_TOL_NS,'Verification/measurement order')
        pars=d.PROFILES.get(s['profile']);need(pars is not None or s['profile']=='noise_free','Unknown memory profile')
        factor=1. if pars is None else math.exp(-(age/1e6/pars['t_ms'])**pars['exponent'])
        close(q['contraction'],factor,'Channel contraction',1e-12)
        target=target_state(s['workload'],inp['state'])
        expected=factor*np.outer(target,target.conj())+(1-factor)*np.eye(2)/2
        rho=density(q['density_real'],q['density_imag'])
        error=float(np.max(np.abs(rho-expected)));need(error<STATE_TOL,'Observed density disagrees with memory model')
        fidelity=float((target.conj()@rho@target).real)
        close(q['fidelity'],fidelity,'Density/fidelity agreement',STATE_TOL)
        close(q['fidelity'],(1+factor)/2,'Age/fidelity agreement',STATE_TOL)
        close(q['expected_infidelity'],1-q['fidelity'],'Raw infidelity complement',1e-12)
        m['density_matrices']=1;m['max_density_error']=error;m['max_fidelity_error']=abs(q['fidelity']-fidelity)
        # Compact values must reconstruct the actual stored channel fields,
        # after those fields pass independent mathematical checks.
        fidelity=q['fidelity'];age=q['age_ns']
    if s['profile']=='noise_free':need(r['completed'] and r['wrong_output']==0,'Noisefree correctness failure')
    reconstructed=dict(spec=s,state=inp['state'],bell_bits=r['bell_bits'],pairing_sha256=d.stable(inp),
        target_request_ns=inp['target_request_ns'],wrong_output=int(r['wrong_output']),unfinished=int(not r['completed']),
        fidelity=fidelity,expected_infidelity=None if fidelity is None else 1-fidelity,
        physical_storage_ns=age,planned_release_ns=planned,competitor_service_ns=int(round(measured)),
        simulator_executions=1,target_finished_ns=r['target_finished_ns'])
    if transferred:reconstructed.update(selected_phase_ns=phase,policy_sha256=d.stable(frozen))
    need(c==reconstructed,'Compact reconstruction mismatch')
    return m
