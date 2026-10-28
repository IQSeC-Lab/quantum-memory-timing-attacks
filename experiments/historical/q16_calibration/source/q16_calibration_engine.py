"""Calibration driver; native scheduler, policy and channel are unchanged."""
from collections import deque
import q16_calibration_design as d
import q16_engine as parent
from q16_engine import Case, install_scheduler, install_channel, peer_trace
failure_snapshot = parent.failure_snapshot


def run(spec):
    parent.CURRENT_CASE = None
    from netsquid.protocols import Protocol
    from qoala.sim.events import SIGNAL_TASK_COMPLETED
    inp = d.inputs(spec)
    c = Case('FCFS', d.LATENCY_NS, 0, inp['private']['simulator_seed'], spec['workload'])
    c.scheduler = spec['scheduler']
    parent.CURRENT_CASE = c
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
