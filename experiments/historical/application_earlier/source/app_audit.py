"""Raw auditor: no simulator or scheduler-policy imports.

Ready sets are rebuilt from the recorded task graph and message availability;
this is independent replay of recorded evidence, not a second simulator.
"""
import json
from pathlib import Path
import math
import numpy as np
import app_design as d

def need(condition, message):
    if not condition:
        raise ValueError(message)


def ready_from_graph(graph, active, now):
    roots = [r for r in graph if not r['internal_precedences'] and not r['external_precedences']]
    external = [r for r in graph if not r['internal_precedences'] and r['external_precedences']]
    if active is not None:
        same = lambda r:r['pid']==active['pid'] and r['critical_section']==active['cs_id']
        roots = [r for r in roots if same(r)]
        external = [r for r in external if same(r)]
    blocked = [r for r in roots if r['is_event'] and not r['message_available']]
    future = [r for r in roots if r['start_ns'] is not None and r['start_ns']>now]
    excluded = {r['task_id'] for r in blocked+future}
    return [r['task_id'] for r in roots if r['task_id'] not in excluded],external,blocked,future



def density(row):
    a=np.asarray(row['real'],float)+1j*np.asarray(row['imag'],float)
    need(a.shape==(2,2) and np.isfinite(a).all(),'Invalid density shape')
    need(np.max(np.abs(a-a.conj().T))<1e-8 and abs(np.trace(a)-1)<1e-8 and np.linalg.eigvalsh(a).min()>-1e-8,'Nonphysical density')
    return a


def target(state):
    return dict(zip(d.STATES,[np.array([1,0]),np.array([0,1]),np.array([1,1])/np.sqrt(2),
        np.array([1,-1])/np.sqrt(2),np.array([1,1j])/np.sqrt(2),np.array([1,-1j])/np.sqrt(2)]))[state]


def validate_record(raw,spec):
    d.HOST_NS=spec['cost_ns']
    need(raw['status']=='COMPLETE' and raw['spec']==spec,'Incomplete native run or spec differs')
    need(raw['plan']==d.plans(spec),'Frozen public/private plan differs')
    need(raw['configuration']==d.settings(spec),'Native configuration differs')
    need(raw['native_host_cost_ns']=={'bob':d.HOST_NS,'alice':d.HOST_NS},'Native host cost differs')
    conf=d.settings(spec)
    expected_latencies=dict(host_instr_time=conf['host_ns'],qnos_instr_time=conf['qnos_ns'],
        host_peer_latency=conf['host_peer_ns'],internal_sched_latency=conf['internal_sched_ns'])
    need(raw['native_latencies']=={'bob':expected_latencies,'alice':expected_latencies},'Imported native latency configuration differs')
    need(raw['actual_cpu_scheduler']==('CpuEdfScheduler' if spec['policy']=='QUOTA_EDF_NUMERIC' else 'CpuFcfsScheduler'),'Scheduler class differs')
    need(raw['sim_end_ns']==d.HORIZON_NS,'Horizon differs')
    admissions=raw['admissions'];owners={}
    need(len(admissions)==len(raw['plan']),'Offer count differs')
    programs={(p['node'],p['pid']):p for p in raw['programs']}
    need(len(programs)==len(raw['programs']),'Duplicate program')
    signatures=json.loads((Path(__file__).parent/'parser_signatures.json').read_text())
    expected_programs=set();costs_by_block={};quantum_pids=set()
    for planned,row in zip(raw['plan'],admissions):
        need({k:row[k] for k in planned}==planned and row['actual_ns']==planned['release_ns'],'Admission differs')
        pid=row['pid'];need(pid not in owners,'Duplicate PID');owners[pid]=row
        expected_programs.add(('bob',pid))
        p=programs[('bob',pid)]
        if row['kind']=='classical':
            text=d.classical_program(row);inputs=d.input_map(row);key='classical'
            costs_by_block[(pid,'reduce_0')]=30*d.HOST_NS
        else:
            quantum_pids.add(pid)
            text,peer=d.rotation_programs(spec,row);inputs={'alice_id':1}
            key=spec['variant']+':'+row['state']+':'+('cs' if spec['policy']=='QUOTA_NATIVE_CS' else 'plain')
            expected_programs.add(('alice',row['peer_pid']))
            q=programs[('alice',row['peer_pid'])]
            need(q['text']==peer and q['inputs']=={'bob_id':0,'a0':row['angle'],'a1':32-row['angle']},'Client program/input differs')
            need(q['blocks']==signatures['client']['blocks'] and q['routines']==signatures['client']['routines'],'Client parser differs')
        need(p['text']==text and p['inputs']==inputs,'Server/workload program differs')
        need(p['blocks']==signatures[key]['blocks'] and p['routines']==signatures[key]['routines'],'Pinned parser signature differs')
    need(set(programs)==expected_programs,'Unexpected or missing program')
    # An unstarted job may not yet have initialized inputs. No other omissions
    # are accepted for a process with observed host instructions.
    seen={(e['node'],e['pid']) for e in raw['host_instructions']}
    need(raw['initialized_inputs']=={n+':'+str(p):programs[(n,p)]['inputs'] for n,p in seen},'Runtime input initialization differs')
    from app_scheduler_audit import validate_scheduler
    ordered,trace,qpu,max_live,cs_stalls,reservations,selected=validate_scheduler(raw,owners,costs_by_block)
    output={r['pid']:r for r in raw['outputs']}
    need(set(output)==set(owners) and len(output)==len(raw['outputs']),'Output inventory differs')
    densities={r['pid']:r for r in raw['density_snapshots']}
    need(len(densities)==len(raw['density_snapshots']) and set(densities)<=quantum_pids,'Unexpected density snapshot')
    outcomes=[];benign=[];attacker=[]
    for pid,row in owners.items():
        tasks=[t for t in ordered if t['pid']==pid]
        values=output[pid]['values']
        if row['kind']=='classical':
            ins=[e for e in raw['host_instructions'] if e['node']=='bob' and e['pid']==pid]
            expected_ops=['AssignCValueOp']+['AddCValueOp']*28+['ReturnResultOp']
            need([e['op_type'] for e in ins]==expected_ops[:len(ins)] and len(ins)<=30,'Reduction opcode prefix differs')
            running=0;v=d.input_map(row)
            for i,e in enumerate(ins):
                if 1<=i<=28:running+=v['v%d'%(i-1)]
                need(e['total_after']==running and e['end_ns']-e['start_ns']==d.HOST_NS,'Reduction prefix/duration differs')
            completed=bool(tasks) and len(ins)==30 and values=={'total':sum(v.values())}
            if len(ins)==30:need(completed,'Classical result differs')
            else:need(values=={},'Incomplete reduction returned a result')
            latency=tasks[-1]['end_ns']-row['release_ns'] if completed else None
            record=dict(index=row['index'],complete=completed,response_ns=latency,
                actual_service_ns=sum(t['end_ns']-t['start_ns'] for t in tasks),instructions=len(ins))
            (attacker if row['tenant']=='attacker' else benign).append(record)
            need(output[pid]['peer_values'] is None,'Classical process unexpectedly has peer output')
            continue
        prog=programs[('bob',pid)]
        routine_order=['prepare_state','rotate_state','rotate_state','verify_state'] if spec['variant']=='unoptimized' else ['merged_state']
        expected_q=[(r,i,s) for r in routine_order for i,s in enumerate(prog['routines'][r])]
        qops=[e for e in raw['quantum_events'] if e['pid']==pid and e['kind']=='instruction']
        need([(e['routine'],e['index'],e['text']) for e in qops]==expected_q[:len(qops)],'Quantum instruction prefix differs')
        need(len(qops)<=len(expected_q),'Extra quantum instructions')
        dyn=[e for e in qops if e['text']=='rot_x Q0 R0 4']
        expected_angles=[row['angle'],32-row['angle']] if spec['variant']=='unoptimized' else [32]
        need([e['angle_n'] for e in dyn]==expected_angles[:len(dyn)],'Received angles were not used by quantum rotations')
        expected_host=['AssignCValueOp','ReceiveCMsgOp','ReceiveCMsgOp']+(['AddCValueOp'] if spec['variant']=='optimized' else [])+['ReturnResultOp']
        ins=[e for e in raw['host_instructions'] if e['node']=='bob' and e['pid']==pid]
        need([e['op_type'] for e in ins]==expected_host[:len(ins)],'Victim host instruction prefix differs')
        runtime_values={};receives=0
        for e in ins:
            if e['op_type']=='ReceiveCMsgOp':
                runtime_values['a%d'%receives]=[row['angle'],32-row['angle']][receives];receives+=1
            elif e['op_type']=='AddCValueOp':runtime_values['theta']=32
            need(e['dynamic_values']==runtime_values,'Received angle / runtime sum differs')
        expected_client=['AssignCValueOp','SendCMsgOp','SendCMsgOp']
        client=[e for e in raw['host_instructions'] if e['node']=='alice' and e['pid']==row['peer_pid']]
        need([e['op_type'] for e in client]==expected_client[:len(client)],'Client instruction prefix differs')
        need(output[pid]['peer_values']=={},'Client returned unexpected values')
        return_tasks=[t for t in tasks if t['block']=='output']
        complete=bool(return_tasks)
        if complete:
            need(set(values)=={'m'} and values['m'] in (0,1),'Invalid quantum return')
            need(len(qops)==len(expected_q) and len(ins)==len(expected_host),'Finished application omitted operations')
        else:
            need(set(values)<={'m'},'Unexpected incomplete output')
        infidelity=age=None
        if pid in densities:
            snap=densities[pid];a=density(snap);psi=target(row['state'])
            f=float(np.vdot(psi,a@psi).real)
            need(-1e-8<=f<=1+1e-8,'Invalid fidelity')
            infidelity=max(0.,min(1.,1-f))
            init=[e for e in qops if e['name']=='init']
            need(len(init)==1 and snap['time_ns']>=init[0]['end_ns'],'Invalid physical lifetime')
            age=snap['time_ns']-init[0]['end_ns']
            markers=[e for e in qops if e['routine']==snap['routine'] and e['index']==snap['instruction_index']]
            need(len(markers)==1 and markers[0]['start_ns']==snap['time_ns'],'Verification snapshot boundary differs')
            if spec['profile']=='ideal':need(infidelity<1e-8,'Ideal application is incorrect')
        if complete:need(pid in densities,'Completed application lacks density evidence')
        end=return_tasks[0]['end_ns'] if complete else None
        on_time=complete and end<=row['absolute_deadline_ns']
        category='unfinished' if not complete else ('late' if not on_time else ('wrong_on_time' if values['m'] else 'correct_on_time'))
        if complete and spec['profile']=='ideal':need(values['m']==0,'Ideal native verification failed')
        outcomes.append(dict(index=row['index'],state=row['state'],category=category,wrong=values.get('m'),
            complete=complete,response_ns=end-row['release_ns'] if complete else None,
            infidelity=infidelity,live_age_ns=age))
    actual_budget={}
    for task in ordered:
        if owners[task['pid']]['tenant']=='attacker':
            p=int(task['start_ns'])//d.PERIOD_NS
            need(task['end_ns']<=(p+1)*d.PERIOD_NS,'Block crosses quota boundary')
            actual_budget[p]=actual_budget.get(p,0)+task['end_ns']-task['start_ns']
    need(all(v<=d.BUDGET_NS for v in actual_budget.values()),'Executed service exceeded quota')
    need(all(actual_budget.get(p,0)<=v for p,v in reservations.items()) and set(actual_budget)<=set(reservations),'Unreserved service')
    if all(r['complete'] for r in attacker):need(actual_budget==reservations,'Finished work/reservations differ')
    # Complete traces must leave no phantom tasks, locks, or live states.
    all_complete=all(x['complete'] for x in outcomes+attacker+benign)
    if all_complete:
        need(not raw['remaining_cpu_tasks'] and not raw['remaining_qpu_tasks'] and not raw['peer_remaining_tasks'],'Completed workload left tasks')
        need(raw['final_active_cs'] is None and not raw['final_live'],'Completed workload left state/section')
    plan=raw['plan']
    return dict(spec=spec,status='PASS_RAW_AUDIT',outcomes=outcomes,benign=benign,attacker=attacker,
        public_plan_sha256=d.stable(d.public_attacker_plan(spec)),
        private_plan_sha256=d.stable([r for r in plan if r['tenant']!='attacker']),
        offered_attacker_jobs=len(attacker),executed_attacker_service_ns=sum(r['actual_service_ns'] for r in attacker),
        all_attacker_work_completed=all(r['complete'] for r in attacker),
        all_jobs_completed=all_complete,max_live_owners=max_live,cs_stall_updates=cs_stalls,
        cpu_tasks=len(trace),qpu_tasks=len(qpu),dispatches=len(selected),
        native_physics_scope='Density validity and ideal controls; noisy evolution is owned by native NetSquid, not independently resimulated here.')
