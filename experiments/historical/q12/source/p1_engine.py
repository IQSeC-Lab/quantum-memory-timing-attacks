"""Pinned Qoala simulation engine for Q7–Q10. Server runtime validation required.

All imports requiring NetSquid are deferred. Observation does not advance time or
consume RNG. Only the explicitly named PROTECTED policy modifies dispatch.
"""
import math
from collections import deque
import numpy as np
import q6_reference as q6

PIN=q6.PIN
STATE_NAMES=['+X','-X','+Y','-Y','+Z','-Z']
PREP=['rot_y Q0 8 4','rot_y Q0 24 4','rot_x Q0 24 4','rot_x Q0 8 4','rot_z Q0 0 4','rot_x Q0 16 4']
VERIFY=['rot_y Q0 24 4','rot_y Q0 8 4','rot_x Q0 8 4','rot_x Q0 24 4','rot_z Q0 0 4','rot_x Q0 16 4']

def host_payload(instructions=30):
    assert instructions>=2
    lines=['    x = assign_cval() : 0','    one = assign_cval() : 1']+['    x = add_cval_c(x, one)']*(instructions-2)
    return 'META_START\n    name: competitor\n    parameters:\n    csockets:\n    epr_sockets:\nMETA_END\n\n^payload {type = CL}:\n'+'\n'.join(lines)+'\n'

def teleport_programs(state,annotated=False):
    assert state in range(6)
    alice='''META_START
    name: alice
    parameters: bob_id
    csockets: 0 -> bob
    epr_sockets: 0 -> bob
META_END
^setup {type = CL}:
    csocket = assign_cval() : 0
^prepare {type = QL}:
    run_subroutine() : input_state
^epr {type = QC}:
    run_request() : epr_gen
^bell {type = QL}:
    tuple<m1; m2> = run_subroutine() : bell_measure
^send {type = CL}:
    send_cmsg(csocket, m1)
    send_cmsg(csocket, m2)
^output {type = CL}:
    return_result(m1)
    return_result(m2)
SUBROUTINE input_state
    params:
    returns:
    uses: 0
    keeps: 0
    request:
  NETQASM_START
    set Q0 0
    init Q0
    PREPARATION
  NETQASM_END
SUBROUTINE bell_measure
    params:
    returns: m1, m2
    uses: 0, 1
    keeps:
    request:
  NETQASM_START
    set Q0 0
    set Q1 1
    cnot Q0 Q1
    h Q0
    meas Q0 M0
    meas Q1 M1
    store M0 @output[0]
    store M1 @output[1]
  NETQASM_END
REQUEST epr_gen
  callback_type:
  callback:
  return_vars:
  remote_id: {bob_id}
  epr_socket_id: 0
  num_pairs: 1
  virt_ids: all 1
  timeout: 1000
  fidelity: 1.0
  typ: create_keep
  role: create
'''.replace('PREPARATION',PREP[state])
    bob='''META_START
    name: bob
    parameters: alice_id
    csockets: 0 -> alice
    epr_sockets: 0 -> alice
META_END
^setup {type = CL}:
    csocket = assign_cval() : 0
^epr {type = QC}:
    run_request() : epr_gen
^receive1 {type = CC}:
    m1 = recv_cmsg(csocket)
^receive2 {type = CC}:
    m2 = recv_cmsg(csocket)
^correct {type = QL}:
    run_subroutine(tuple<m1; m2>) : corrections
^measure {type = QL}:
    tuple<m> = run_subroutine() : verify_state
^output {type = CL}:
    return_result(m)
    return_result(m1)
    return_result(m2)
SUBROUTINE corrections
    params: m1, m2
    returns:
    uses: 0
    keeps: 0
    request:
  NETQASM_START
    set C15 0
    set C14 1
    load C0 @input[C15]
    load C1 @input[C14]
    set Q0 0
    set R0 0
    beq C0 R0 2
    z Q0
    beq C1 R0 2
    x Q0
    set R15 0
  NETQASM_END
SUBROUTINE verify_state
    params:
    returns: m
    uses: 0
    keeps:
    request:
  NETQASM_START
    set Q0 0
    VERIFICATION
    meas Q0 M0
    store M0 @output[0]
  NETQASM_END
REQUEST epr_gen
  callback_type:
  callback:
  return_vars:
  remote_id: {alice_id}
  epr_socket_id: 0
  num_pairs: 1
  virt_ids: all 0
  timeout: 1000
  fidelity: 1.0
  typ: create_keep
  role: receive
'''.replace('VERIFICATION',VERIFY[state])
    if annotated:
        for block,typ,prev in [('epr','QC','setup'),('receive1','CC','epr'),('receive2','CC','receive1'),('correct','QL','receive2'),('measure','QL','correct')]:
            bob=bob.replace(f'^{block} {{type = {typ}}}:',f'^{block} {{type = {typ}; deadlines = [{prev}: 500000]}}:')
    return bob,alice

def protected_choice(ready,queue,live_pids,pid_of):
    """Keep FCFS readiness-discovery order; choose the first eligible live-state task."""
    queue[:]=[t for t in queue if t in ready]
    queue.extend(t for t in ready if t not in queue)
    eligible=[t for t in queue if pid_of(t) in live_pids]
    chosen=eligible[0] if eligible else queue[0]
    queue.remove(chosen)
    return chosen

class Case:
    def __init__(self,scheduler,latency,t2,seed,workload='plus'):
        import netsquid as ns
        from qoala.runtime.config import TopologyConfigBuilder,ProcNodeConfig,LatenciesConfig,NtfConfig,ProcNodeNetworkConfig,ClassicalConnectionConfig
        from qoala.sim.build import build_network_from_config
        from qoala.lang.ehi import UnitModule
        from qoala.sim.scheduling.cpusched import CpuFcfsScheduler,CpuEdfScheduler
        ns.sim_reset();ns.set_qstate_formalism(ns.QFormalism.DM);ns.set_random_state(seed=int(seed))
        self.ns=ns;self.workload=workload;self.scheduler=scheduler;self.t2=t2
        def node(name,nid):
            nt2=t2 if name=='bob' else 0
            top=(TopologyConfigBuilder().uniform_topology().num_qubits(2 if name=='alice' and workload=='teleport' else 1)
                 .qubit_t1(0).qubit_t2(nt2).default_generic_gates().zero_gate_durations().perfect_gate_fidelities().build())
            return ProcNodeConfig(node_name=name,node_id=nid,topology=top,
                 latencies=LatenciesConfig(host_instr_time=10000,qnos_instr_time=1000),ntf=NtfConfig.from_cls_name('GenericNtf'),
                 fcfs=name=='alice' or scheduler!='EDF_NATIVE',use_deadlines=name=='bob' and scheduler=='EDF_NATIVE')
        cfg=ProcNodeNetworkConfig.from_nodes_perfect_links(nodes=[node('bob',0),node('alice',1)],link_duration=1000)
        cfg.cconns=[ClassicalConnectionConfig.from_nodes(0,1,int(latency))]
        self.net=build_network_from_config(cfg);self.bob=self.net.nodes['bob'];self.alice=self.net.nodes['alice']
        self.units={name:UnitModule.from_full_ehi(n.memmgr.get_ehi()) for name,n in self.net.nodes.items()}
        assert isinstance(self.bob.scheduler.cpu_scheduler,CpuEdfScheduler if scheduler=='EDF_NATIVE' else CpuFcfsScheduler)
        self.events=[];self.emitted=[];self.decisions=[];self.live=set();self.jobs=[];self.finished_pids={'bob':{},'alice':{}}
        for name,node_obj in [('bob',self.bob),('alice',self.alice)]:
            cpu_obj=node_obj.scheduler.cpu_scheduler
            def wrap_finish(cpu,node,label):
                original=cpu.handle_task
                def handle(tid):
                    task=cpu._task_graph.get_tinfo(tid).task
                    result=yield from original(tid)
                    if tid in cpu._tasks_executed and task.block_name==node.memmgr.get_process(task.pid).program.blocks[-1].name:
                        self.finished_pids[label][task.pid]=float(self.ns.sim_time())
                    return result
                return handle
            cpu_obj.handle_task=wrap_finish(cpu_obj,node_obj,name)
        self._observe(self.bob)
        if scheduler=='PROTECTED':self._protect()

    def _observe(self,node):
        ns=self.ns;proc=node.qnos.processor;original=proc._interpret_instruction
        from netqasm.lang.instr import core
        def instruction(pid,instr):
            routine=proc._current_routine.routine.name
            name=instr.mnemonic
            virt=None
            if isinstance(instr,core.InitInstruction):virt=proc._prog_mem().qnos_mem.get_reg_value(instr.reg)
            elif isinstance(instr,core.MeasInstruction):virt=proc._prog_mem().qnos_mem.get_reg_value(instr.qreg)
            before=float(ns.sim_time());gen=original(pid,instr)
            if gen is not None:yield from gen
            after=float(ns.sim_time())
            if isinstance(instr,core.InitInstruction):self.live.add((pid,virt))
            if isinstance(instr,core.MeasInstruction):self.live.discard((pid,virt))
            self.events.append(dict(kind='instruction',pid=pid,routine=routine,name=name,start_ns=before,end_ns=after,virt=virt))
        proc._interpret_instruction=instruction
        free=node.memmgr.free
        def free_observed(pid,virt_id,*a,**k):
            result=free(pid,virt_id,*a,**k);self.live.discard((pid,virt_id));return result
        node.memmgr.free=free_observed
        netproc=node.netstack.processor
        multi=netproc.handle_multi_pair;single=netproc.handle_single_pair
        def mark_epr(process,routine_name,indices):
            request=process.qnos_mem.get_running_request_routine(routine_name).routine.request
            for i in indices:
                v=request.virt_ids.get_id(i);self.live.add((process.pid,v))
                self.events.append(dict(kind='epr_ready',pid=process.pid,virt=v,end_ns=float(ns.sim_time())))
        def multi_observed(process,routine_name,*a,**k):
            ok=yield from multi(process,routine_name,*a,**k)
            if ok:
                request=process.qnos_mem.get_running_request_routine(routine_name).routine.request
                mark_epr(process,routine_name,range(request.num_pairs))
            return ok
        def single_observed(process,routine_name,index,*a,**k):
            ok=yield from single(process,routine_name,index,*a,**k)
            if ok:mark_epr(process,routine_name,[index])
            return ok
        netproc.handle_multi_pair=multi_observed;netproc.handle_single_pair=single_observed
        builder=node.scheduler._task_from_block_builder;build=builder.build
        def observe_build(*args,**kwargs):
            graph=build(*args,**kwargs)
            for tid,info in graph.get_tasks().items():
                self.emitted.append(dict(task_id=tid,pid=info.task.pid,block=info.task.block_name,
                    task_type=type(info.task).__name__,deadline=info.deadline,created_ns=float(ns.sim_time())))
            return graph
        builder.build=observe_build

    def _protect(self):
        from qoala.sim.scheduling.procsched import SchedulerStatus,Status
        cpu=self.bob.scheduler.cpu_scheduler
        def choose(ready):
            live_pids={p for p,v in self.live}
            t=protected_choice(ready,cpu._task_queue,live_pids,lambda tid:cpu._task_graph.get_tinfo(tid).task.pid)
            self.decisions.append(dict(time_ns=float(self.ns.sim_time()),ready=list(ready),live_pids=sorted(live_pids),selected=t))
            cpu._status=SchedulerStatus(status={Status.NEXT_TASK},params={'task_id':t})
        cpu.choose_next_task=choose

    def _batch(self,node,text,inputs):
        from qoala.lang.parse import QoalaParser
        from qoala.runtime.program import ProgramInput
        from qoala.util.runner import create_batch
        return node.submit_batch(create_batch(QoalaParser(text).parse(),self.units[node.name] if hasattr(node,'name') and node.name in self.units else self.units['bob' if node is self.bob else 'alice'],[ProgramInput(v) for v in inputs],len(inputs)))

    def victim(self,state=0,dynamic=False):
        if self.workload=='plus':texts=(q6.VICTIM_DEADLINES,q6.PEER)
        else:texts=teleport_programs(state,self.scheduler=='EDF_NATIVE')
        bb=self._batch(self.bob,texts[0],[{'alice_id':1}]);ab=self._batch(self.alice,texts[1],[{'bob_id':0}])
        bp=bb.instances[0].pid;ap=ab.instances[0].pid
        if dynamic:
            self.bob.scheduler.submit_program_instance(bb.instances[0],ap)
            self.alice.scheduler.submit_program_instance(ab.instances[0],bp)
            self.bob.scheduler.schedule_all();self.alice.scheduler.schedule_all()
        else:
            self.bob.initialize_processes({bb.batch_id:[ap]});self.alice.initialize_processes({ab.batch_id:[bp]})
        return dict(bob_pid=bp,alice_pid=ap,bob_batch=bb.batch_id,alice_batch=ab.batch_id,state=state)

    def admit_cpu(self,when,instructions=30,count=4,kind='competitor',episode=None):
        b=self._batch(self.bob,host_payload(instructions),[{} for _ in range(count)])
        for inst in b.instances:
            self.bob.scheduler.submit_program_instance(inst)
            self.jobs.append(dict(pid=inst.pid,requested_ns=float(when),admitted_ns=float(self.ns.sim_time()),instructions=instructions,kind=kind,episode=episode))
        self.bob.scheduler.schedule_all()

    def finished(self,v):
        return v['bob_pid'] in self.finished_pids['bob'] and v['alice_pid'] in self.finished_pids['alice']

    def outcome(self,v):
        values=self.bob.memmgr.get_process(v['bob_pid']).result.values
        avalues=self.alice.memmgr.get_process(v['alice_pid']).result.values
        complete='m' in values
        if complete and self.workload=='plus':assert values.get('reply')==1
        if complete and self.workload=='teleport':
            assert all(values[k]==avalues[k] for k in ['m1','m2']), 'Correction message mismatch'
        return dict(completed=complete,error=int(values['m']) if complete else 1,
                    wrong_output=int(values['m']) if complete else 0,timeout=not complete,
                    bell_bits=[values.get('m1'),values.get('m2')] if self.workload=='teleport' else None)

    def traces(self):
        stats=self.bob.scheduler.get_statistics();out={}
        for name in ['cpu','qpu']:
            tasks=getattr(stats,f'_{name}_tasks_executed');starts=getattr(stats,f'_{name}_task_starts');ends=getattr(stats,f'_{name}_task_ends')
            rows=[dict(task_id=tid,pid=t.pid,block=t.block_name,task_type=type(t).__name__,start_ns=float(starts[tid]),end_ns=float(ends[tid])) for tid,t in tasks.items()]
            if name=='cpu':rows=ordered_cpu_trace(rows)
            else:rows.sort(key=lambda x:(x['start_ns'],x['task_id']))
            out[name]=rows
        return out

def ordered_cpu_trace(rows):
    """Put zero-duration boundary events before service starting at that time.

    Task IDs identify tasks, not their execution order. This changes only the
    serialized trace order; no simulation events or timestamps are modified.
    A point event inside a busy interval still fails, as does positive overlap.
    """
    for r in rows:
        assert math.isfinite(r['start_ns']) and math.isfinite(r['end_ns']) and r['end_ns']>=r['start_ns'], f'Invalid CPU interval: {r!r}'
    ordered=sorted(rows,key=lambda r:(r['start_ns'],r['end_ns'],r['task_id']))
    for a,b in zip(ordered,ordered[1:]):
        assert b['start_ns']>=a['end_ns']-1e-6, f'CPU overlap: previous={a!r}; following={b!r}'
    return ordered

def validate_native_deadlines(emitted, victim_pid, workload):
    """Validate creation-time deadlines in pinned TaskGraphFromBlockBuilder.

    QC WAIT_ALL creates an undated MultiPairTask, while its CPU pre/post
    calls are dated zero. QL local tasks and CC/CL tasks are dated zero.
    Observe those stock semantics; never alter the graph to satisfy a check.
    """
    annotated=[e for e in emitted if e['pid']==victim_pid and e['block'] not in ['setup','output']]
    assert annotated, 'No annotated victim tasks observed'
    for e in annotated:
        expected=None if (workload=='teleport' and e['block']=='epr' and e['task_type']=='MultiPairTask') else 0
        assert e['deadline']==expected, (
            f"Native EDF deadline mismatch: block={e['block']} task={e['task_type']} "
            f"expected={expected!r} observed={e['deadline']!r}; event={e!r}")

def run_trial(spec,inputs):
    from netsquid.protocols import Protocol
    phase=inputs['phase_ns'];t2=0 if spec['noise']=='noise_free' else spec.get('t2_ns',4000000)
    c=Case(spec['scheduler'],inputs['latency_ns'],t2,inputs['seed'],spec['workload'])
    v=c.victim(inputs['state']);count=spec.get('instructions',30)
    class Admission(Protocol):
        def run(self):
            if phase>0:yield self.await_timer(phase)
            c.admit_cpu(phase,count)
    c.net.start();admission=None
    if phase is not None:admission=Admission();admission.start()
    c.ns.sim_run(duration=20000000)
    outcome=c.outcome(v);traces=c.traces();cpu=traces['cpu'];qpu=traces['qpu']
    own=[t for t in cpu if t['pid'] in {j['pid'] for j in c.jobs}]
    assert len(own)==(4 if phase is not None else 0),'Incomplete competing service'
    for t in own:
        assert t['start_ns']>=phase and abs(t['end_ns']-t['start_ns']-count*10000)<1e-6
        assert c.bob.memmgr.get_process(t['pid']).host_mem.read('x')==count-2
    if t2==0:assert outcome['completed'] and outcome['error']==0,'Noise-free correctness failure'
    physical=None;gap=None
    local=[t for t in qpu if t['pid']==v['bob_pid'] and t['task_type']=='LocalRoutineTask']
    if outcome['completed']:
        if spec['workload']=='plus':
            p=[e for e in c.events if e.get('routine')=='prepare_state' and e.get('name')=='h' and e['pid']==v['bob_pid']]
            m=[e for e in c.events if e.get('routine')=='measure_state' and e.get('name')=='h' and e['pid']==v['bob_pid']]
            assert len(p)==len(m)==1;physical=m[0]['start_ns']-p[0]['end_ns']
            gap=next(t['start_ns'] for t in local if t['block']=='measure')-next(t['end_ns'] for t in local if t['block']=='prepare')
        else:
            epr=[e for e in c.events if e['kind']=='epr_ready' and e['pid']==v['bob_pid']]
            verify=[e for e in c.events if e.get('routine')=='verify_state' and e.get('name','').startswith('rot_') and e['pid']==v['bob_pid']]
            assert len(epr)==len(verify)==1;physical=verify[0]['start_ns']-epr[0]['end_ns']
        assert physical>=0
    if spec['scheduler']=='EDF_NATIVE':validate_native_deadlines(c.emitted,v['bob_pid'],spec['workload'])
    pred=(1-math.exp(-physical/t2))/2 if spec['workload']=='plus' and physical is not None and t2>0 else None
    return dict(outcome,physical_storage_ns=physical,qpu_routine_gap_ns=gap,predicted_x_error=pred,
      competitor_service_ns=sum(t['end_ns']-t['start_ns'] for t in own),
      mean_competitor_response_ns=float(np.mean([t['end_ns']-phase for t in own])) if own else None,
      admission_events=c.jobs,cpu_trace=cpu,qpu_trace=qpu,quantum_events=c.events,created_task_deadlines=c.emitted,
      protected_decisions=c.decisions,actual_cpu_scheduler=type(c.bob.scheduler.cpu_scheduler).__name__,
      dispatch_policy=spec['scheduler'],sim_end_ns=float(c.ns.sim_time()))

def run_sustained(spec,selected):
    """One independent persistent network, FIFO victim admission; no inter-request reset."""
    from netsquid.protocols import Protocol
    from qoala.sim.events import SIGNAL_TASK_COMPLETED
    from pydynaa import EventExpression
    c=Case(spec['scheduler'],1000000,4000000,spec['seed'],spec['workload'])
    # Sustained cost uses fixed 1 ms latency; explicit extension from isolated jitter.
    period=10000000;warmup=100;measured=1000;arrivals=warmup+measured;end=(arrivals+100)*period
    rng=np.random.default_rng(np.random.SeedSequence([91004,1,spec['run']]))
    states=np.random.default_rng(np.random.SeedSequence([91004,2,spec['run']])).integers(0,6,size=arrivals)
    phases=np.random.default_rng(np.random.SeedSequence([91004,3,spec['run']])).integers(0,4000000,size=arrivals)
    events=[]
    for i in range(arrivals):
        events.append((i*period,0,'victim',i))
        if spec['condition']!='no_competitor':events.append((i*period+(selected if spec['condition']=='selected' else int(phases[i])),1,'competitor',i))
    t=0.;rate=spec['load']/300000
    while True:
        t+=float(rng.exponential(1/rate))
        if t>=arrivals*period:break
        events.append((t,2,'benign',None))
    events.sort();requests=[];queue=deque();active=None;cursor=0
    def update():
        nonlocal active
        if active is not None and c.finished(active['v']):
            active.update(c.outcome(active['v']),finished_ns=float(c.ns.sim_time()));active=None
        if active is None and queue and c.ns.sim_time()<end:
            active=queue.popleft();active['v']=c.victim(active['state'],dynamic=True);active['admitted_ns']=float(c.ns.sim_time())
    class Timeline(Protocol):
        def run(self):
            nonlocal cursor
            while c.ns.sim_time()<end:
                update()
                while cursor<len(events) and events[cursor][0]<=c.ns.sim_time()+1e-6:
                    when,_,kind,index=events[cursor];cursor+=1
                    if kind=='victim':
                        r=dict(index=index,offered_ns=when,state=int(states[index]),completed=False,error=1,timeout=True)
                        requests.append(r);queue.append(r)
                        if index%100==0:print(f"Sustained run: {index}/{arrivals} victim arrivals; pending FIFO {len(queue)}",flush=True)
                    else:c.admit_cpu(when,30,4 if kind=='competitor' else 1,kind,index)
                update()
                nxt=events[cursor][0] if cursor<len(events) else end
                ev=self.await_timer(max(0.,nxt-c.ns.sim_time()))
                if active is not None:
                    ev=ev|self.await_signal(c.bob.scheduler.cpu_scheduler,SIGNAL_TASK_COMPLETED)|self.await_signal(c.alice.scheduler.cpu_scheduler,SIGNAL_TASK_COMPLETED)
                yield ev
            update()
    c.net.start();timeline=Timeline();timeline.start();c.ns.sim_run(duration=end);update()
    traces=c.traces();cpu=traces['cpu'];completed_tasks={t['pid']:t for t in cpu if t['block']=='payload'}
    for j in c.jobs:
        task=completed_tasks.get(j['pid']);j['completed']=task is not None
        j['response_ns']=task['end_ns']-j['requested_ns'] if task else None
        if task:assert abs(task['end_ns']-task['start_ns']-300000)<1e-6
    for r in requests:
        if r.get('finished_ns') is not None:
            r['latency_ns']=r['finished_ns']-r['offered_ns']
            r['late']=r['latency_ns']>20000000
            r['failure_by_20ms']=bool(r['error'] or r['late'])
        else:r.update(latency_ns=None,late=True,failure_by_20ms=True)
        r.pop('v',None)
    target=[r for r in requests if warmup<=r['index']<arrivals]
    benign=[j for j in c.jobs if j['kind']=='benign' and warmup*period<=j['requested_ns']<arrivals*period]
    finished_b=[j['response_ns'] for j in benign if j['completed']]
    finished_v=[r['latency_ns'] for r in target if r['completed']]
    window=(arrivals-warmup)*period
    actual_busy=sum(max(0.,min(t['end_ns'],arrivals*period)-max(t['start_ns'],warmup*period)) for t in cpu)
    correct_in_window=sum(r['completed'] and r['error']==0 and warmup*period<=r.get('finished_ns',end)<arrivals*period for r in requests)
    metrics=dict(offered_victims=len(target),failure_rate=sum(r['failure_by_20ms'] for r in target)/len(target),
       completion_fraction=sum(r['completed'] for r in target)/len(target),
       correct_outputs_per_second=correct_in_window/(window/1e9),
       victim_p95_latency_ms=float(np.percentile(finished_v,95))/1e6 if finished_v else None,
       benign_offered=len(benign),benign_completion_fraction=len(finished_b)/len(benign) if benign else None,
       benign_p95_response_ms=float(np.percentile(finished_b,95))/1e6 if finished_b else None,
       cpu_busy_fraction=actual_busy/window,remaining_victims=sum(not r['completed'] for r in requests),
       remaining_cpu_jobs=sum(not j['completed'] for j in c.jobs),
       known_cpu_offered_fraction=sum(j['instructions']*10000 for j in c.jobs if warmup*period<=j['requested_ns']<arrivals*period)/window,
       cost_timing='fixed 1 ms propagation; isolated phase transferred without retuning',
       latency_note='p95 is conditional on completion; inspect completion and backlog jointly')
    return dict(metrics=metrics,requests=requests,cpu_jobs=c.jobs,cpu_trace=cpu,qpu_trace=traces['qpu'],
                protected_decisions=c.decisions,sim_end_ns=float(c.ns.sim_time()))

def calibration():
    """Deterministic density-matrix convention check, isolated from evaluation RNG."""
    import netsquid as ns
    from netsquid.components.models.qerrormodels import T1T2NoiseModel
    from netsquid.qubits import qubitapi as qa
    rows=[]
    for t2 in [2000000,4000000,8000000]:
        for t in [0,250000,1000000,4000000,8000000]:
            ns.sim_reset();ns.set_qstate_formalism(ns.QFormalism.DM)
            q=qa.create_qubits(1)[0];qa.operate(q,ns.H)
            T1T2NoiseModel(T1=0,T2=t2).error_operation([q],delta_time=t)
            rho=qa.reduced_dm([q]);err=float(.5-rho[0,1].real)
            pred=(1-math.exp(-t/t2))/2
            assert abs(err-pred)<1e-9,('Dephasing convention differs',t2,t,err,pred)
            rows.append(dict(t2_ns=t2,storage_ns=t,observed_probability=err,predicted_probability=pred))
    return rows
