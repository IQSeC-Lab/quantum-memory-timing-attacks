"""Synthetic evidence exercises the application auditor; never simulation data."""
import copy
import json
from pathlib import Path
import unittest
import numpy as np
import app_design as d
from app_policy import Policy
from app_audit import validate_record,target

def fixture(variant='unoptimized',state='Z+'):
    spec=dict(stage='preflight',stream=0,profile='ideal',cost_ns=15,variant=variant,
        policy='QUOTA_FCFS',arm='none',state=state,angle=8,phase_ns=0)
    row=d.plans(spec)[0];server,client=d.rotation_programs(spec,row)
    sig=json.loads((Path(__file__).parent/'parser_signatures.json').read_text())
    shape=sig[variant+':'+state+':plain'];owner=dict(row,pid=0,peer_pid=0)
    raw=dict(spec=spec,status='COMPLETE',plan=d.plans(spec),configuration=d.settings(spec),
        programs=[dict(node='bob',pid=0,text=server,inputs={'alice_id':1},**shape),
                  dict(node='alice',pid=0,text=client,inputs={'bob_id':0,'a0':8,'a1':24},**sig['client'])],
        initialized_inputs={'bob:0':{'alice_id':1},'alice:0':{'bob_id':0,'a0':8,'a1':24}},
        actual_cpu_scheduler='CpuFcfsScheduler',native_host_cost_ns={'bob':15,'alice':15},
        cpu_trace=[],qpu_trace=[],peer_cpu_trace=[],density_snapshots=[],quantum_events=[],
        host_instructions=[],created_tasks=[],admissions=[],service_events=[],decisions=[],status_events=[],
        remaining_cpu_tasks=[],remaining_qpu_tasks=[],peer_remaining_tasks=[],final_active_cs=None,final_live=[],
        outputs=[dict(pid=0,values={'m':0},peer_values={})],sim_end_ns=d.HORIZON_NS)
    conf=d.settings(spec)
    lat=dict(host_instr_time=conf['host_ns'],qnos_instr_time=conf['qnos_ns'],host_peer_latency=conf['host_peer_ns'],internal_sched_latency=conf['internal_sched_ns'])
    raw['native_latencies']={'bob':lat,'alice':lat}
    seq=0;now=0;tid=0;graph={};policy=Policy('QUOTA_FCFS');live=set();rotation_count=0
    def event(target,**kw):
        nonlocal seq
        kw['sequence']=seq;seq+=1;raw[target].append(kw);return kw
    event('admissions',**owner,actual_ns=0)
    for kind in ('AssignCValueOp','SendCMsgOp','SendCMsgOp'):
        event('host_instructions',node='alice',pid=0,op_type=kind,op_text='',total_after=None,start_ns=0,end_ns=0)
    def create(block,typ,ext=None):
        nonlocal tid
        t=tid;tid+=1
        event('created_tasks',task_id=t,pid=0,block=block,task_type=typ,critical_section=None,
            native_creation_deadline=None,time_ns=now)
        if typ!='LocalRoutineTask':graph[t]=dict(task_id=t,pid=0,block=block,task_type=typ,
            critical_section=None,internal_precedences=[],external_precedences=ext or [],
            start_ns=None,is_event=typ=='HostEventTask',message_available=True if typ=='HostEventTask' else None)
        return t
    def start(t):
        snapshot=copy.deepcopy(list(graph.values()))
        ready=[r['task_id'] for r in snapshot if not r['external_precedences']]
        choice=policy.decide(now,ready,{i:(0,'victim') for i in ready},live,{}, {i:d.DEADLINE_NS for i in ready})
        assert choice['selected']==t
        choice.update(graph=snapshot,active_cs=None,native_remaining_deadlines_ns={},statuses=['NEXT_TASK'])
        i=len(raw['decisions']);event('decisions',**choice)
        event('status_events',time_ns=now,active_before=None,active_after=None,graph=snapshot,
            statuses=['NEXT_TASK'],params={'task_id':t},decision_index=i)
        event('service_events',kind='start',task_id=t,pid=0,block=graph[t]['block'],time_ns=now)
        return now
    def end(t,begin):
        b=graph.pop(t);event('service_events',kind='end',task_id=t,pid=0,block=b['block'],time_ns=now)
        raw['cpu_trace'].append(dict(task_id=t,pid=0,block=b['block'],task_type=b['task_type'],
            critical_section=None,start_ns=begin,end_ns=now))
    runtime_values={}
    for block in shape['blocks']:
        name=block['name']
        if block['op_types']==['RunSubroutineOp']:
            pre=create(name,'PreCallTask');q=create(name,'LocalRoutineTask');post=create(name,'PostCallTask',[q])
            begin=start(pre);now+=15;end(pre,begin)
            routine=('prepare_state' if name=='prepare' else 'rotate_state' if name.startswith('rotate') else
                'verify_state' if name=='verify' else 'merged_state')
            instructions=shape['routines'][routine];qstart=now
            marker=next(i for i,x in enumerate(instructions) if x.startswith('meas '))-len(d.state_gates(state)[1]) if routine in ('verify_state','merged_state') else None
            for idx,text in enumerate(instructions):
                mnemonic=text.split()[0]
                if idx==marker:
                    psi=target(state);rho=np.outer(psi,psi.conj())
                    event('density_snapshots',pid=0,physical_id=0,time_ns=now,real=rho.real.tolist(),imag=rho.imag.tolist(),routine=routine,instruction_index=idx)
                angle=None
                if text=='rot_x Q0 R0 4':
                    angle=32 if variant=='optimized' else [8,24][rotation_count];rotation_count+=1
                b=now;now+=50000
                virt=0 if mnemonic in ('init','meas') else None
                if mnemonic=='init':live.add(0)
                if mnemonic=='meas':live.discard(0)
                event('quantum_events',kind='instruction',pid=0,virt=virt,name=mnemonic,routine=routine,
                    index=idx,text=text,angle_n=angle,start_ns=b,end_ns=now)
            raw['qpu_trace'].append(dict(task_id=q,pid=0,block=name,task_type='LocalRoutineTask',critical_section=None,start_ns=qstart,end_ns=now))
            graph[post]['external_precedences']=[]
            begin=start(post);now+=15;end(post,begin)
        else:
            t=create(name,'HostEventTask' if name.startswith('receive') else 'HostLocalTask')
            begin=start(t)
            for typ in block['op_types']:
                if typ=='ReceiveCMsgOp':runtime_values['a0' if name=='receive0' else 'a1']=8 if name=='receive0' else 24
                if typ=='AddCValueOp':runtime_values['theta']=32
                b=now;now+=15
                event('host_instructions',node='bob',pid=0,op_type=typ,op_text='',total_after=None,dynamic_values=dict(runtime_values),start_ns=b,end_ns=now)
            end(t,begin)
    return raw,spec

class ApplicationAudit(unittest.TestCase):
    def test_all_ideal_states_and_compilation_variants(self):
        for v in d.VARIANTS:
            for state in d.STATES:
                raw,s=fixture(v,state);result=validate_record(raw,s)
                self.assertEqual(result['outcomes'][0]['category'],'correct_on_time')
                self.assertAlmostEqual(result['outcomes'][0]['infidelity'],0)

    def test_wrong_received_angle_rejected(self):
        raw,s=fixture();next(e for e in raw['host_instructions'] if e['node']=='bob' and e['op_type']=='ReceiveCMsgOp')['dynamic_values']['a0']=7
        with self.assertRaises(ValueError):validate_record(raw,s)

    def test_rotation_did_not_use_received_angle(self):
        raw,s=fixture()
        for e in raw['quantum_events']:
            if e.get('text')=='rot_x Q0 R0 4':e['angle_n']=9;break
        with self.assertRaises(ValueError):validate_record(raw,s)

    def test_missing_density_rejected(self):
        raw,s=fixture();raw['density_snapshots'][0]['time_ns']+=1
        with self.assertRaises(ValueError):validate_record(raw,s)

    def test_changed_input_initialization_rejected(self):
        raw,s=fixture();raw['initialized_inputs']['alice:0']['a1']=20
        with self.assertRaises(ValueError):validate_record(raw,s)

    def test_changed_source_signature_rejected(self):
        raw,s=fixture();raw['programs'][0]['routines']['rotate_state'][2]='load R1 @input[C15]'
        with self.assertRaises(ValueError):validate_record(raw,s)

if __name__=='__main__':unittest.main(verbosity=2)
