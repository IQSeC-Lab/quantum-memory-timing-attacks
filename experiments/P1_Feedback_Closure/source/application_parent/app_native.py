"""Native NetSquid/Qoala adapter. Nothing here opens a remote simulator API."""
import traceback
import app_design as d
from app_policy import Policy, integer_time

CURRENT_RAW = None
CURRENT_COLLECTOR = None


def failure_snapshot():
    return CURRENT_RAW


def run(spec):
    global CURRENT_RAW, CURRENT_COLLECTOR
    CURRENT_COLLECTOR = None
    d.HOST_NS = spec['cost_ns']
    raw = dict(spec=spec, status='STARTED', plan=d.plans(spec), programs=[],
        admissions=[], decisions=[], status_events=[], service_events=[],
        quantum_events=[], host_instructions=[], initialized_inputs={}, created_tasks=[], outputs=[],
        cpu_trace=[], qpu_trace=[], peer_cpu_trace=[], density_snapshots=[], configuration=d.settings(spec))
    CURRENT_RAW = raw
    try:
        _execute(raw, spec)
        raw['status'] = 'COMPLETE'
    except BaseException:
        raw.update(status='FAILED', exception=traceback.format_exc())
        if CURRENT_COLLECTOR is not None:
            CURRENT_COLLECTOR()
    return raw


def _execute(raw, spec):
    global CURRENT_COLLECTOR
    import netsquid as ns
    from netsquid.protocols import Protocol
    from netsquid.qubits import qubitapi as qapi
    from netqasm.lang.instr import core
    from qoala.lang.ehi import UnitModule
    from qoala.lang.parse import QoalaParser
    from qoala.runtime.config import (TopologyConfigBuilder, ProcNodeConfig,
        LatenciesConfig, NtfConfig, ProcNodeNetworkConfig, ClassicalConnectionConfig)
    from qoala.runtime.program import ProgramInput
    from qoala.sim.build import build_network_from_config
    from qoala.sim.scheduling.cpusched import CpuEdfScheduler, CpuFcfsScheduler
    from qoala.sim.scheduling.procsched import SchedulerStatus, Status
    from qoala.util.runner import create_batch

    cfg_values=d.settings(spec)
    ns.sim_reset()
    ns.set_qstate_formalism(ns.QFormalism.DM)
    ns.set_random_state(seed=d.seed(spec))
    def config(name, nid):
        top = (TopologyConfigBuilder().uniform_topology().num_qubits(cfg_values['num_qubits'])
            .qubit_t1(0).qubit_t2(cfg_values['t2_ns']).default_generic_gates().zero_gate_durations()
            .perfect_gate_fidelities()
            .all_comm_gates_duration(cfg_values['gate_ns'])
            .all_mem_gates_duration(cfg_values['gate_ns'])
            .all_comm_gates_fidelity(cfg_values['gate_fidelity'])
            .all_mem_gates_fidelity(cfg_values['gate_fidelity']).build())
        return ProcNodeConfig(node_name=name, node_id=nid, topology=top,
            latencies=LatenciesConfig(host_instr_time=d.HOST_NS,qnos_instr_time=cfg_values['qnos_ns'],host_peer_latency=cfg_values['host_peer_ns'],internal_sched_latency=cfg_values['internal_sched_ns']),
            ntf=NtfConfig.from_cls_name('GenericNtf'),
            fcfs=name=='alice' or spec['policy']!='QUOTA_EDF_NUMERIC',
            use_deadlines=name=='bob' and spec['policy']=='QUOTA_EDF_NUMERIC')
    cfg = ProcNodeNetworkConfig.from_nodes_perfect_links(
        nodes=[config('bob',0),config('alice',1)],link_duration=1000)
    cfg.cconns = [ClassicalConnectionConfig.from_nodes(0,1,cfg_values['latency_ns'])]
    net = build_network_from_config(cfg)
    bob, alice = net.nodes['bob'], net.nodes['alice']
    units = {name:UnitModule.from_full_ehi(n.memmgr.get_ehi()) for name,n in net.nodes.items()}
    cpu = bob.scheduler.cpu_scheduler
    expected_cls = CpuEdfScheduler if spec['policy']=='QUOTA_EDF_NUMERIC' else CpuFcfsScheduler
    if not isinstance(cpu,expected_cls):
        raise ValueError('Native CPU scheduler class differs')
    raw['actual_cpu_scheduler'] = type(cpu).__name__
    raw['native_host_cost_ns'] = {name:n.host.processor._latencies.host_instr_time for name,n in net.nodes.items()}
    raw['native_latencies']={name:{key:getattr(n.memmgr.get_ehi().latencies,key) for key in
        ('host_instr_time','qnos_instr_time','host_peer_latency','internal_sched_latency')}
        for name,n in net.nodes.items()}
    raw['actual_qpu_scheduler']=type(bob.scheduler.qpu_scheduler).__name__
    policy = Policy(spec['policy'])
    owners, live, pid_records = {}, set(), {}
    sequence = 0
    def event(target, **row):
        nonlocal sequence
        row.update(sequence=sequence)
        sequence += 1
        raw[target].append(row)
        return row
    def active():
        cs = cpu._critical_section
        return None if cs is None else dict(pid=cs.pid,cs_id=cs.cs_id)
    def graph_snapshot():
        rows = []
        for tid,info in cpu._task_graph.get_tasks().items():
            task = info.task
            rows.append(dict(task_id=tid,pid=task.pid,block=task.block_name,
                task_type=type(task).__name__,critical_section=task.critical_section,
                internal_precedences=sorted(info.precedences.get_ids()),
                external_precedences=sorted(info.ext_precedences.get_ids()),
                start_ns=info.start_time, is_event=task.is_event_task(),
                message_available=cpu.is_message_available(tid) if task.is_event_task() else None))
        return rows
    def collect_failure():
        raw['sim_end_ns'] = float(ns.sim_time())
        raw['failure_collection_errors'] = []
        for label,node,core_name in (('cpu_trace',bob,'cpu'),('qpu_trace',bob,'qpu'),('peer_cpu_trace',alice,'cpu')):
            try:
                stats = node.scheduler.get_statistics()
                starts = getattr(stats,'_'+core_name+'_task_starts')
                ends = getattr(stats,'_'+core_name+'_task_ends')
                raw[label] = [dict(task_id=t,pid=x.pid,block=x.block_name,task_type=type(x).__name__,
                    critical_section=x.critical_section,start_ns=float(starts[t]),end_ns=float(ends[t]))
                    for t,x in getattr(stats,'_'+core_name+'_tasks_executed').items()]
            except BaseException as exc:
                raw['failure_collection_errors'].append(label+': '+repr(exc))
        try:
            raw['remaining_cpu_tasks'] = graph_snapshot()
        except BaseException as exc:
            raw['failure_collection_errors'].append('remaining_cpu_tasks: '+repr(exc))
    CURRENT_COLLECTOR = collect_failure
    # Preserve native readiness, critical-section filters and wake mechanisms.
    original_update = cpu.update_status
    def update():
        before = active()
        graph = graph_snapshot()
        first = len(raw['decisions'])
        original_update()
        event('status_events', time_ns=float(ns.sim_time()), active_before=before,
            active_after=active(), graph=graph,
            statuses=sorted(s.name for s in cpu._status.status),
            params=dict(cpu._status.params), decision_index=first if len(raw['decisions'])>first else None)
    cpu.update_status = update

    def cost(tid):
        task = cpu._task_graph.get_tinfo(tid).task
        if owners[task.pid]['tenant'] != 'attacker':
            return None
        process = bob.memmgr.get_process(task.pid)
        block = process.program.get_block(task.block_name)
        if type(task).__name__!='HostLocalTask' or not task.block_name.startswith('reduce_'):
            raise ValueError('Attacker task lacks a verified CL cost')
        kinds = [type(op).__name__ for op in block.instructions]
        if any(k not in ('AssignCValueOp','AddCValueOp','ReturnResultOp') for k in kinds):
            raise ValueError('Unpriced attacker operation')
        return len(kinds)*d.HOST_NS

    native_edf_choose = cpu.choose_next_task
    def choose(ready):
        ready = list(ready)
        now = integer_time(ns.sim_time())
        tg = cpu._task_graph
        owner_map = {t:(tg.get_tinfo(t).task.pid, owners[tg.get_tinfo(t).task.pid]['tenant']) for t in ready}
        costs = {t:cost(t) for t in ready if owner_map[t][1]=='attacker'}
        deadlines = {t:owners[owner_map[t][0]]['absolute_deadline_ns'] for t in ready}
        row = policy.decide(now, ready, owner_map, {p for p,v in live}, costs, deadlines)
        row.update(active_cs=active(), graph=graph_snapshot(), native_remaining_deadlines_ns={})
        selected = row['selected']
        if selected is not None and spec['policy']=='QUOTA_EDF_NUMERIC':
            for tid in ready:
                info = tg.get_tinfo(tid)
                if info.rel_deadlines or info.ext_rel_deadlines:
                    raise ValueError('Unexpected relative deadline annotations')
                info.deadline = deadlines[tid]-now
                row['native_remaining_deadlines_ns'][str(tid)] = info.deadline
            # Use the native numerical sorter, with FCFS order as its stable tie-break.
            native_edf_choose(row['eligible'])
            if cpu._status.params['task_id'] != selected:
                raise ValueError('Native numeric EDF differs from independent priority calculation')
        elif selected is not None:
            cpu._status = SchedulerStatus(status={Status.NEXT_TASK},params={'task_id':selected})
        else:
            roots = [r for r in row['graph'] if not r['internal_precedences'] and not r['external_precedences']]
            if row['active_cs'] is not None:
                cs = row['active_cs']
                roots = [r for r in roots if r['pid']==cs['pid'] and r['critical_section']==cs['cs_id']]
            future = [r['start_ns'] for r in roots if r['start_ns'] is not None and r['start_ns']>now]
            status = {Status.WAITING_START_TIME}
            external = [r for r in row['graph'] if not r['internal_precedences'] and r['external_precedences']]
            if row['active_cs'] is not None:
                cs = row['active_cs']
                external = [r for r in external if r['pid']==cs['pid'] and r['critical_section']==cs['cs_id']]
            if external:
                status.add(Status.WAITING_OTHER_CORE)
            if any(r['is_event'] and not r['message_available'] for r in roots):
                status.add(Status.WAITING_MSG)
            row['wake_ns'] = min([row['wake_ns']]+future)
            cpu._status = SchedulerStatus(status=status,params={'start_time':row['wake_ns']})
        row['statuses'] = sorted(s.name for s in cpu._status.status)
        event('decisions', **row)
        # The common adapter owns FIFO state and prunes tasks excluded by native CS.
        cpu._task_queue = list(policy.queue)
    cpu.choose_next_task = choose

    for name,node in (('bob',bob),('alice',alice)):
        def install_cpu(label, node_obj):
            sched = node_obj.scheduler.cpu_scheduler
            original = sched.handle_task
            def handle(tid):
                task = sched._task_graph.get_tinfo(tid).task
                if label=='bob':
                    event('service_events',kind='start',task_id=tid,pid=task.pid,
                        block=task.block_name,time_ns=float(ns.sim_time()))
                yield from original(tid)
                if tid not in sched._tasks_executed:
                    raise ValueError('Native CPU task failed')
                if label=='bob':
                    event('service_events',kind='end',task_id=tid,pid=task.pid,
                        block=task.block_name,time_ns=float(ns.sim_time()))
            sched.handle_task = handle
            processor = node_obj.host.processor
            assign = processor.assign_instr
            def instruction(process, op):
                key = label+':'+str(process.pid)
                if key not in raw['initialized_inputs']:
                    raw['initialized_inputs'][key] = {
                        name:process.prog_memory.host_mem.read(name)
                        for name in process.prog_instance.inputs.values}
                before = float(ns.sim_time())
                yield from assign(process,op)
                total = None
                if label=='bob' and owners[process.pid]['kind']=='classical':
                    total=process.prog_memory.host_mem.read('total')
                dynamic={}
                if label=='bob' and owners[process.pid]['kind']=='quantum':
                    for key in ('a0','a1','theta'):
                        try:dynamic[key]=process.prog_memory.host_mem.read(key)
                        except KeyError:pass
                event('host_instructions',node=label,pid=process.pid,op_type=type(op).__name__,op_text=str(op),total_after=total,dynamic_values=dynamic,
                    start_ns=before,end_ns=float(ns.sim_time()))
            processor.assign_instr = instruction
        install_cpu(name,node)

    proc = bob.qnos.processor
    original_instruction = proc._interpret_instruction
    sampled = set()
    def quantum(pid,instr):
        routine = proc._current_routine.routine.name
        instructions=proc._current_routine.routine.subroutine.instructions
        index=next(i for i,x in enumerate(instructions) if x is instr)
        virt = None
        if isinstance(instr,core.InitInstruction):
            virt = proc._prog_mem().qnos_mem.get_reg_value(instr.reg)
        elif isinstance(instr,core.MeasInstruction):
            virt = proc._prog_mem().qnos_mem.get_reg_value(instr.qreg)
        before = float(ns.sim_time())
        # Observe before inverse-basis verification. Reading a reduced density
        # matrix does not alter its state; the native memory model owns noise.
        if routine in ('verify_state','merged_state') and pid not in sampled:
            measurement_index=next(i for i,x in enumerate(instructions) if x.mnemonic=='meas')
            inverse_count=len(d.state_gates(owners[pid]['state'])[1])
            if index==measurement_index-inverse_count:
                physical=bob.memmgr.phys_id_for(pid,0)
                dm=qapi.reduced_dm([bob.qdevice.get_local_qubit(physical)])
                event('density_snapshots',pid=pid,physical_id=physical,time_ns=before,
                    real=dm.real.tolist(),imag=dm.imag.tolist(),routine=routine,instruction_index=index)
                sampled.add(pid)
        angle_n=None
        if instr.mnemonic=='rot_x' and routine in ('rotate_state','merged_state'):
            from netqasm.lang.operand import Register
            angle_n=proc._prog_mem().qnos_mem.get_reg_value(instr.angle_num) if isinstance(instr.angle_num,Register) else instr.angle_num.value
        generator = original_instruction(pid,instr)
        if generator is not None:
            yield from generator
        if isinstance(instr,core.InitInstruction): live.add((pid,virt))
        if isinstance(instr,core.MeasInstruction): live.discard((pid,virt))
        event('quantum_events',kind='instruction',pid=pid,virt=virt,name=instr.mnemonic,
            routine=routine,index=index,text=str(instr),angle_n=angle_n,
            start_ns=before,end_ns=float(ns.sim_time()))
    proc._interpret_instruction = quantum
    original_free = bob.memmgr.free
    def free(pid,virt_id,*args,**kwargs):
        result = original_free(pid,virt_id,*args,**kwargs)
        live.discard((pid,virt_id))
        event('quantum_events',kind='free',pid=pid,virt=virt_id,time_ns=float(ns.sim_time()))
        return result
    bob.memmgr.free = free

    builder = bob.scheduler._task_from_block_builder
    original_build = builder.build
    def build(*args,**kwargs):
        graph = original_build(*args,**kwargs)
        for tid,info in graph.get_tasks().items():
            task = info.task
            event('created_tasks',task_id=tid,pid=task.pid,block=task.block_name,
                task_type=type(task).__name__,critical_section=task.critical_section,
                native_creation_deadline=info.deadline,time_ns=float(ns.sim_time()))
        return graph
    builder.build = build

    def batch(node,text,values):
        program = QoalaParser(text).parse()
        b = node.submit_batch(create_batch(program, units[node.name], [ProgramInput(values)],1))
        inst = b.instances[0]
        raw['programs'].append(dict(node=node.name,pid=inst.pid,text=text,inputs=values,
            blocks=[dict(name=x.name,critical_section=x.critical_section,
                op_types=[type(op).__name__ for op in x.instructions]) for x in program.blocks],
            routines={k:[str(x) for x in rt.subroutine.instructions] for k,rt in program.local_routines.items()}))
        return inst
    def admit(row):
        if row['kind']=='classical':
            inst = batch(bob,d.classical_program(row),d.input_map(row))
            owner = dict(row,pid=inst.pid)
            owners[inst.pid] = owner
            pid_records[inst.pid] = owner
            bob.scheduler.submit_program_instance(inst)
            peer_pid = None
        else:
            server,client=d.rotation_programs(spec,row)
            inst = batch(bob,server,{'alice_id':1})
            peer = batch(alice,client,{'bob_id':0,'a0':row['angle'],'a1':32-row['angle']})
            owner = dict(row,pid=inst.pid,peer_pid=peer.pid)
            owners[inst.pid] = owner
            pid_records[inst.pid] = owner
            bob.scheduler.submit_program_instance(inst,peer.pid)
            alice.scheduler.submit_program_instance(peer,inst.pid)
            peer_pid = peer.pid
        event('admissions',**row,pid=inst.pid,peer_pid=peer_pid,actual_ns=float(ns.sim_time()))
        bob.scheduler.schedule_all()
        alice.scheduler.schedule_all()

    class Offers(Protocol):
        def run(self):
            for row in raw['plan']:
                delay = row['release_ns']-ns.sim_time()
                if delay < 0:
                    raise ValueError('Offer clock moved backwards')
                if delay:
                    yield self.await_timer(delay)
                admit(row)
    net.start()
    offers = Offers()
    offers.start()
    ns.sim_run(duration=d.HORIZON_NS)
    raw['sim_end_ns'] = float(ns.sim_time())
    def trace(node, core_name):
        stats = node.scheduler.get_statistics()
        tasks = getattr(stats,'_'+core_name+'_tasks_executed')
        starts = getattr(stats,'_'+core_name+'_task_starts')
        ends = getattr(stats,'_'+core_name+'_task_ends')
        return sorted([dict(task_id=t,pid=x.pid,block=x.block_name,task_type=type(x).__name__,
            critical_section=x.critical_section,start_ns=float(starts[t]),end_ns=float(ends[t]))
            for t,x in tasks.items()],key=lambda r:(r['start_ns'],r['end_ns'],r['task_id']))
    raw.update(cpu_trace=trace(bob,'cpu'),qpu_trace=trace(bob,'qpu'),peer_cpu_trace=trace(alice,'cpu'),
        remaining_cpu_tasks=graph_snapshot(),remaining_qpu_tasks=list(bob.scheduler.qpu_scheduler._task_graph.get_tasks()),
        final_active_cs=active(),final_live=sorted([list(v) for v in live]),
        peer_remaining_tasks=list(alice.scheduler.cpu_scheduler._task_graph.get_tasks()))
    for pid,owner in pid_records.items():
        values = dict(bob.memmgr.get_process(pid).result.values)
        raw['outputs'].append(dict(pid=pid,values=values,peer_values=(
            dict(alice.memmgr.get_process(owner['peer_pid']).result.values) if owner['kind']=='quantum' else None)))
