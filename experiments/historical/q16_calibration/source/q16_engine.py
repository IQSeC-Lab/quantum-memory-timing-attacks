"""Pinned Qoala adapter. Only this module requires the simulator at run time."""
import math
from collections import deque
import numpy as np
import q16_design as d
from q16_policy import Policy, integer_time
from q15_case import Case
from p1_engine import ordered_cpu_trace

CURRENT_CASE = None


def target_vector(workload, state):
    vectors = [np.array([1,1])/np.sqrt(2), np.array([1,-1])/np.sqrt(2),
               np.array([1,1j])/np.sqrt(2), np.array([1,-1j])/np.sqrt(2),
               np.array([1,0]), np.array([0,1])]
    return np.asarray(vectors[0 if workload=='plus' else state], dtype=complex)


def install_channel(case, spec, states):
    from netsquid.qubits import qubitapi as qapi, depolarize
    proc = case.bob.qnos.processor
    original = proc._interpret_instruction
    snapshots = []
    applied = set()

    def instruction(pid, instr):
        routine = proc._current_routine.routine.name
        verification = ((spec['workload']=='plus' and routine=='measure_state' and instr.mnemonic=='h') or
                        (spec['workload']=='teleport' and routine=='verify_state' and instr.mnemonic.startswith('rot_')))
        if verification:
            assert pid in states and pid not in applied, 'Missing state or duplicate channel application'
            if spec['workload']=='plus':
                starts = [e for e in case.events if e['pid']==pid and e.get('routine')=='prepare_state' and e.get('name')=='h']
            else:
                starts = [e for e in case.events if e['pid']==pid and e['kind']=='epr_ready']
            assert len(starts)==1, 'Missing or duplicate storage start'
            start = starts[0]['end_ns']
            stop = float(case.ns.sim_time())
            assert stop >= start
            physical_id = case.bob.memmgr.phys_id_for(pid, 0)
            assert physical_id is not None
            qubit = case.bob.qdevice.get_local_qubit(physical_id)
            before = qapi.reduced_dm([qubit])
            target = target_vector(spec['workload'], states[pid])
            ideal = np.outer(target, target.conj())
            assert np.max(np.abs(before-ideal)) < 2e-9, 'Incorrect ideal state before channel'
            factor = (1.0 if spec['profile']=='noise_free' else
                      math.exp(-((stop-start)/1e6/d.PROFILE['t_ms'])**d.PROFILE['exponent']))
            depolarize(qubit, 1-factor)
            after = qapi.reduced_dm([qubit])
            fidelity = float(qapi.fidelity(qubit, target.reshape(-1,1), squared=True))
            assert np.max(np.abs(after-(factor*ideal+(1-factor)*np.eye(2)/2))) < 2e-9
            assert abs(fidelity-(1+factor)/2) < 2e-9
            assert case.ns.sim_time()==stop, 'Observation changed simulator time'
            applied.add(pid)
            snapshots.append(dict(pid=pid, state=states[pid], physical_id=physical_id,
                start_ns=start, use_ns=stop, age_ns=stop-start, contraction=factor,
                fidelity=fidelity, expected_infidelity=1-fidelity,
                before_real=before.real.tolist(), before_imag=before.imag.tolist(),
                density_real=after.real.tolist(), density_imag=after.imag.tolist()))
        generator = original(pid, instr)
        if generator is not None:
            yield from generator
    proc._interpret_instruction = instruction
    return snapshots


def install_scheduler(case, scheduler, tenants):
    from qoala.sim.scheduling.procsched import SchedulerStatus, Status
    cpu = case.bob.scheduler.cpu_scheduler
    policy = Policy(scheduler)
    case.policy = policy
    case.service_events = []
    cost_cache = {}

    def verified_cost(tid):
        task = cpu._task_graph.get_tinfo(tid).task
        if tenants[task.pid] != 'attacker':
            return None
        if tid not in cost_cache:
            process = case.bob.memmgr.get_process(task.pid)
            blocks = process.program.blocks
            assert type(task).__name__=='HostLocalTask' and len(blocks)==1, 'Unverified attacker task kind or overhead'
            block = blocks[0]
            assert task.block_name==block.name=='payload'
            kinds = [type(op).__name__ for op in block.instructions]
            assert kinds==['AssignCValueOp']*2+['AddCValueOp']*(d.INSTRUCTIONS-2), 'Attacker program differs from cost proof'
            cost_cache[tid] = len(block.instructions)*d.HOST_INSTR_NS
        return cost_cache[tid]

    def choose(ready):
        now = integer_time(case.ns.sim_time())
        tg = cpu._task_graph
        assert cpu._critical_section is None, 'Pilot programs must not use critical sections'
        ready = list(ready)
        owners = {}
        costs = {}
        for tid in ready:
            pid = tg.get_tinfo(tid).task.pid
            assert pid in tenants, 'CPU owner not registered to a tenant'
            owners[tid] = (pid, tenants[pid])
            cost = verified_cost(tid)
            if cost is not None:
                costs[tid] = cost
        roots = list(tg.get_roots())
        message_blocked = [tid for tid in roots if tg.get_tinfo(tid).task.is_event_task() and not cpu.is_message_available(tid)]
        future = {str(tid):float(tg.get_tinfo(tid).start_time) for tid in roots
                  if tg.get_tinfo(tid).start_time is not None and tg.get_tinfo(tid).start_time > now}
        assert ready==[t for t in roots if t not in message_blocked and str(t) not in future], 'Native readiness mismatch'
        blocked_other = list(tg.get_tasks_blocked_only_on_external())
        decision = policy.decide(now, ready, owners, {p for p,v in case.live}, costs)
        decision.update(sequence=case.next_sequence(), roots=roots,
                        message_blocked=message_blocked, future_starts_ns=future,
                        blocked_other_core=blocked_other)
        case.decisions.append(decision)
        cpu._task_queue[:] = policy.queue
        if decision['selected'] is not None:
            cpu._status = SchedulerStatus(status={Status.NEXT_TASK}, params={'task_id':decision['selected']})
        else:
            # Budget-throttled ready work must not hide messages or QPU completions.
            status = {Status.WAITING_START_TIME}
            wake = min([decision['wake_ns']] + list(future.values()))
            if blocked_other:
                status.add(Status.WAITING_OTHER_CORE)
            if message_blocked:
                status.add(Status.WAITING_MSG)
            cpu._status = SchedulerStatus(status=status, params={'start_time':wake})
            decision['wake_ns'] = wake
        decision['status'] = sorted(x.name for x in cpu._status.status)

    original_handle = cpu.handle_task

    def handle(tid):
        task = cpu._task_graph.get_tinfo(tid).task
        tenant = tenants[task.pid]
        before = float(case.ns.sim_time())
        cost = verified_cost(tid)
        assert tid in policy.selected, 'Service without a recorded dispatch'
        case.service_events.append(dict(kind='start', task_id=tid, pid=task.pid, tenant=tenant,
                                       time_ns=before, sequence=case.next_sequence()))
        result = yield from original_handle(tid)
        assert tid in cpu._tasks_executed, 'CPU task failed'
        after = float(case.ns.sim_time())
        if tenant=='attacker':
            assert abs(after-before-cost) < 1e-6, 'Verified cost differs from actual service'
            assert after <= (int(before)//d.PERIOD_NS+1)*d.PERIOD_NS, 'Attacker crossed budget boundary'
        case.service_events.append(dict(kind='end', task_id=tid, pid=task.pid, tenant=tenant,
                                       time_ns=after, sequence=case.next_sequence()))
        return result

    cpu.choose_next_task = choose
    cpu.handle_task = handle


def peer_trace(case):
    stats = case.alice.scheduler.get_statistics()
    return ordered_cpu_trace([dict(task_id=tid, pid=task.pid, block=task.block_name,
        task_type=type(task).__name__, start_ns=float(stats._cpu_task_starts[tid]),
        end_ns=float(stats._cpu_task_ends[tid])) for tid,task in stats._cpu_tasks_executed.items()])


def failure_snapshot():
    if CURRENT_CASE is None:
        return None
    c = CURRENT_CASE
    snapshot = dict(sim_time_ns=float(c.ns.sim_time()), quantum_events=c.events,
                    lifetime_events=c.lifetime_events, dispatch_decisions=c.decisions,
                    service_events=getattr(c, 'service_events', []), admission_events=c.jobs,
                    finished_pids=c.finished_pids, created_tasks=c.emitted)
    snapshot.update(spec=getattr(c,'pilot_spec',None),inputs=getattr(c,'pilot_inputs',None),
                    requests=getattr(c,'pilot_requests',None),tenant_by_pid=getattr(c,'pilot_tenants',None))
    try:
        snapshot.update(c.traces())
    except Exception as exc:
        snapshot['trace_error'] = repr(exc)
    return snapshot


def run(spec):
    global CURRENT_CASE
    CURRENT_CASE = None
    from netsquid.protocols import Protocol
    from qoala.sim.events import SIGNAL_TASK_COMPLETED
    inp = d.inputs(spec)
    c = Case('FCFS', d.LATENCY_NS, 0, inp['private']['simulator_seed'], spec['workload'])
    c.scheduler = spec['scheduler']
    CURRENT_CASE = c
    tenants, states = {}, {}
    c.pilot_spec,c.pilot_inputs,c.pilot_tenants = spec,inp,tenants
    install_scheduler(c, spec['scheduler'], tenants)
    channel = install_channel(c, spec, states)
    end = inp['horizon_ns']
    requests = [dict(r, actual_offered_ns=None, admitted_ns=None, victim=None) for r in inp['private']['requests']]
    c.pilot_requests = requests
    queue = deque()
    active = None

    def update_victim():
        nonlocal active
        if active is not None and c.finished(active['victim']):
            active = None
        if active is None and queue and c.ns.sim_time() < end:
            active = queue.popleft()
            active['admitted_ns'] = float(c.ns.sim_time())
            active['victim'] = c.victim(active['state'], dynamic=True)
            pid = active['victim']['bob_pid']
            tenants[pid] = 'victim'
            states[pid] = active['state']

    class Victims(Protocol):
        def run(self):
            cursor = 0
            while c.ns.sim_time() < end:
                update_victim()
                while cursor < len(requests) and requests[cursor]['offered_ns'] <= c.ns.sim_time():
                    row = requests[cursor]
                    row['actual_offered_ns'] = float(c.ns.sim_time())
                    queue.append(row)
                    cursor += 1
                update_victim()
                nxt = requests[cursor]['offered_ns'] if cursor < len(requests) else end
                event = self.await_timer(nxt-c.ns.sim_time())
                if active is not None:
                    event = event | self.await_signal(c.bob.scheduler.cpu_scheduler, SIGNAL_TASK_COMPLETED)
                    event = event | self.await_signal(c.alice.scheduler.cpu_scheduler, SIGNAL_TASK_COMPLETED)
                yield event
            update_victim()

    def job_protocol(plan, tenant):
        class Jobs(Protocol):
            def run(self):
                for job in plan:
                    delay = job['requested_ns']-c.ns.sim_time()
                    assert delay >= 0
                    if delay:
                        yield self.await_timer(delay)
                    first = len(c.jobs)
                    c.admit_cpu(job['requested_ns'], job['instructions'], 1, tenant)
                    assert len(c.jobs)==first+1
                    admitted = c.jobs[-1]
                    tenants[admitted['pid']] = tenant
                    admitted['plan_index'] = job['index']
        return Jobs()

    # Independent protocols: the attack reads only its already frozen plan.
    protocols = [job_protocol(inp['attacker'], 'attacker'),
                 job_protocol(inp['private']['benign'], 'benign'), Victims()]
    c.net.start()
    for protocol in protocols:
        protocol.start()
    c.ns.sim_run(duration=end)
    update_victim()
    traces = c.traces()
    for row in requests:
        victim = row['victim']
        values, peer_values = {}, {}
        finished = peer_finished = None
        if victim is not None:
            bp, ap = victim['bob_pid'], victim['alice_pid']
            values = dict(c.bob.memmgr.get_process(bp).result.values)
            peer_values = dict(c.alice.memmgr.get_process(ap).result.values)
            finished = c.finished_pids['bob'].get(bp)
            peer_finished = c.finished_pids['alice'].get(ap)
        completed = finished is not None and 'm' in values
        row.update(output_values=values, peer_output_values=peer_values,
                   finished_ns=finished, peer_finished_ns=peer_finished, completed=completed,
                   wrong_output=int(values['m']) if completed else None)
        row['category'] = d.outcome_category(completed, row['wrong_output'], finished, row['offered_ns'])
    for job in c.jobs:
        pid = job['pid']
        finished = c.finished_pids['bob'].get(pid)
        job['finished_ns'] = finished
        job['x'] = int(c.bob.memmgr.get_process(pid).host_mem.read('x')) if finished is not None else None
    cpu = c.bob.scheduler.cpu_scheduler
    active_cpu = []
    for tid, start in cpu._task_starts.items():
        if tid not in cpu._task_ends:
            task = cpu._task_graph.get_tinfo(tid).task
            active_cpu.append(dict(task_id=tid, pid=task.pid, block=task.block_name,
                task_type=type(task).__name__, start_ns=float(start), end_ns=float(c.ns.sim_time()),
                incomplete=True))
    return dict(spec=spec, inputs=inp, requests=requests, admission_events=c.jobs,
        tenant_by_pid={str(p):t for p,t in tenants.items()}, cpu_trace=traces['cpu'],
        active_cpu_trace=active_cpu, qpu_trace=traces['qpu'], peer_cpu_trace=peer_trace(c),
        quantum_events=c.events, lifetime_events=c.lifetime_events,
        memory_channel=channel, created_tasks=c.emitted, dispatch_decisions=c.decisions,
        service_events=c.service_events, finished_pids=c.finished_pids,
        budget_reservations_ns={str(k):v for k,v in c.policy.reserved.items()},
        actual_cpu_scheduler=type(cpu).__name__, scheduler_policy=spec['scheduler'],
        native_t2_ns=c.t2, sim_end_ns=float(c.ns.sim_time()))
