"""Synthetic record fixtures for offline validation only, never experiment results."""
import math
import numpy as np
import q14_design as d
from q13_raw_checks import target_state


def fixture(spec,frozen,bits=(1,1)):
    workload=spec['workload'];profile=spec['profile'];inp=d.inputs(spec);state=inp['state']
    start=inp['target_request_ns'];phase=d.phase_for(spec,frozen)
    planned=d.release_plan(d.EPOCH_NS,spec['arm'],phase,inp['random_phase_ns'])
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
    raw.update(selected_phase_ns=phase,policy_sha256=d.stable(frozen))
    compact=dict(selected_phase_ns=phase,policy_sha256=d.stable(frozen),spec=spec,state=inp['state'],bell_bits=bells,pairing_sha256=d.stable(inp),target_request_ns=inp['target_request_ns'],
        wrong_output=0,unfinished=0,fidelity=fidelity,expected_infidelity=1-fidelity,physical_storage_ns=float(use-ready),
        planned_release_ns=planned,competitor_service_ns=0 if planned is None else 1200000,simulator_executions=1,target_finished_ns=float(finish))
    return raw,compact,spec
