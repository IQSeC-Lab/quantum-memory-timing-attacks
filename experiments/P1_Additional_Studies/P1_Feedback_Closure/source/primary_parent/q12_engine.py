"""Q12 simulation adapter and pure recorded-data validation.

Evaluator traces never enter the phase selector or release-plan function.
The reused simulator adapter is pinned to the previously audited source.
"""
import math
from collections import Counter
import q12_design as d
from p1_engine import Case, ordered_cpu_trace
from time_checks import check_payload


def run(spec, frozen):
    from netsquid.protocols import Protocol
    inp = d.inputs(spec)
    phase = d.phase_for(spec, frozen)
    planned = d.release_plan(d.EPOCH_NS, spec['arm'], phase, inp['blind_phase_ns'])
    t2 = 0 if spec['noise'] == 'noise_free' else d.T2_NS
    c = Case('FCFS', inp['latency_ns'], t2, inp['seed'], spec['workload'])
    target = {}
    arrivals = []
    class Target(Protocol):
        def run(self):
            yield self.await_timer(inp['target_request_ns'])
            arrivals.append(float(c.ns.sim_time()))
            target.update(c.victim(inp['state'], dynamic=True))
    class Competitor(Protocol):
        def run(self):
            yield self.await_timer(planned)
            c.admit_cpu(planned, d.INSTRUCTIONS, d.JOBS, 'competitor')
    class Background(Protocol):
        def run(self):
            for when in inp['background']:
                if when > c.ns.sim_time():
                    yield self.await_timer(when-c.ns.sim_time())
                c.admit_cpu(when, 10, 1, 'benign')
    c.net.start()
    protocols = [Target(), Background()]
    if planned is not None:
        protocols.append(Competitor())
    for p in protocols:
        p.start()
    c.ns.sim_run(duration=d.HORIZON_NS)
    assert target and len(arrivals) == 1
    out = c.outcome(target)
    traces = c.traces()
    peer_stats = c.alice.scheduler.get_statistics()
    peer_cpu = []
    for tid, task in peer_stats._cpu_tasks_executed.items():
        peer_cpu.append(dict(task_id=tid, pid=task.pid, block=task.block_name, task_type=type(task).__name__,
                             start_ns=float(peer_stats._cpu_task_starts[tid]), end_ns=float(peer_stats._cpu_task_ends[tid])))
    peer_cpu = ordered_cpu_trace(peer_cpu)
    physical = None
    state_use = None
    if out['completed']:
        events = [e for e in c.events if e['pid'] == target['bob_pid']]
        if spec['workload'] == 'plus':
            starts = [e for e in events if e.get('routine') == 'prepare_state' and e.get('name') == 'h']
            ends = [e for e in events if e.get('routine') == 'measure_state' and e.get('name') == 'h']
        else:
            starts = [e for e in events if e['kind'] == 'epr_ready']
            ends = [e for e in events if e.get('routine') == 'verify_state' and e.get('name','').startswith('rot_')]
        assert len(starts) == len(ends) == 1
        physical = ends[0]['start_ns']-starts[0]['end_ns']
        state_use = ends[0]['start_ns']
    return dict(out, spec=spec, inputs=inp, public_epoch_ns=d.EPOCH_NS,
                selected_phase_ns=phase, planned_release_ns=planned,
                target_pid=target['bob_pid'], target_admitted_ns=arrivals[0],
                target_finished_ns=c.finished_pids['bob'].get(target['bob_pid']),
                physical_storage_ns=physical, state_use_ns=state_use,
                admission_events=c.jobs, cpu_trace=traces['cpu'], qpu_trace=traces['qpu'],
                peer_cpu_trace=peer_cpu, quantum_events=c.events,
                actual_cpu_scheduler=type(c.bob.scheduler.cpu_scheduler).__name__,
                sim_end_ns=float(c.ns.sim_time()))


def validate(r, spec, frozen):
    assert r['spec'] == spec and r['inputs'] == d.inputs(spec)
    inp = r['inputs']
    phase = d.phase_for(spec, frozen)
    planned = d.release_plan(d.EPOCH_NS, spec['arm'], phase, inp['blind_phase_ns'])
    assert r['public_epoch_ns'] == d.EPOCH_NS and r['selected_phase_ns'] == phase
    assert r['planned_release_ns'] == planned and r['actual_cpu_scheduler'] == 'CpuFcfsScheduler'
    assert r['sim_end_ns'] == d.HORIZON_NS
    assert abs(r['target_admitted_ns']-inp['target_request_ns']) <= 1e-6
    for trace in [r['cpu_trace'], r['peer_cpu_trace']]:
        assert trace == ordered_cpu_trace(trace)
        assert len({t['task_id'] for t in trace}) == len(trace)
        assert all(0 <= t['start_ns'] <= t['end_ns'] <= d.HORIZON_NS for t in trace)
    for t in r['cpu_trace']:
        if t['pid'] == r['target_pid']:
            assert t['start_ns'] >= r['target_admitted_ns']
    jobs = r['admission_events']
    assert len({j['pid'] for j in jobs}) == len(jobs)
    payloads = {t['pid']:t for t in r['cpu_trace'] if t['block'] == 'payload'}
    assert set(payloads) == {j['pid'] for j in jobs}, 'Unfinished classical jobs; export diagnostics'
    competitors = [j for j in jobs if j['kind'] == 'competitor']
    benign = [j for j in jobs if j['kind'] == 'benign']
    assert len(competitors) == (d.JOBS if planned is not None else 0)
    assert [j['requested_ns'] for j in benign] == inp['background']
    for j in jobs:
        check_payload(j, payloads[j['pid']])
        assert j['kind'] in ('competitor', 'benign')
        assert j['instructions'] == (d.INSTRUCTIONS if j['kind'] == 'competitor' else 10)
        if j['kind'] == 'competitor':
            assert j['requested_ns'] == planned
    if r['completed']:
        assert not r['timeout'] and r['error'] == r['wrong_output'] in (0,1)
        assert r['target_finished_ns'] is not None and r['target_finished_ns'] >= r['target_admitted_ns']
        es = [e for e in r['quantum_events'] if e['pid'] == r['target_pid']]
        if spec['workload'] == 'plus':
            a = [e for e in es if e.get('routine') == 'prepare_state' and e.get('name') == 'h']
            b = [e for e in es if e.get('routine') == 'measure_state' and e.get('name') == 'h']
        else:
            a = [e for e in es if e['kind'] == 'epr_ready']
            b = [e for e in es if e.get('routine') == 'verify_state' and e.get('name','').startswith('rot_')]
            assert r['bell_bits'] and len(r['bell_bits']) == 2 and all(x in (0,1) for x in r['bell_bits'])
        assert len(a) == len(b) == 1
        assert r['state_use_ns'] == b[0]['start_ns']
        assert r['physical_storage_ns'] == b[0]['start_ns']-a[0]['end_ns'] >= 0
    else:
        assert r['timeout'] and r['error'] == 1 and r['wrong_output'] == 0
        assert r['physical_storage_ns'] is None and r['state_use_ns'] is None
    if spec['noise'] == 'noise_free':
        assert r['completed'] and r['wrong_output'] == 0, 'Noise-free correctness failure'
    cpu_ns = sum(t['end_ns']-t['start_ns'] for t in r['cpu_trace'])
    peer_cpu_ns = sum(t['end_ns']-t['start_ns'] for t in r['peer_cpu_trace'])
    own_service = sum(payloads[j['pid']]['end_ns']-payloads[j['pid']]['start_ns'] for j in competitors)
    expected_service = d.BURST_SERVICE_NS if planned is not None else 0
    assert abs(own_service-expected_service) < 1e-5
    compact = dict(spec=spec, wrong_output=int(r['wrong_output']), unfinished=int(not r['completed']),
                   application_failure=int(r['error']), state=inp['state'], bell_bits=r['bell_bits'],
                   physical_storage_ns=r['physical_storage_ns'], target_finished_ns=r['target_finished_ns'],
                   target_request_ns=inp['target_request_ns'], selected_phase_ns=phase,
                   planned_release_ns=planned, competitor_service_ns=expected_service,
                   bob_cpu_service_ns=cpu_ns, alice_cpu_service_ns=peer_cpu_ns,
                   qnos_instruction_count=len([e for e in r['quantum_events'] if e['kind']=='instruction']),
                   completed_epr_pairs=len([e for e in r['quantum_events'] if e['kind']=='epr_ready']))
    if spec['stage'] == 'calibration':
        compact['own_observation'] = d.calibration_observation(r)
    return compact


def validate_pilot(rows):
    assert [r['spec'] for r in rows] == list(d.specifications('pilot'))
    controls = [r for r in rows if r['spec']['noise'] == 'noise_free']
    assert len(controls) == 1176 and all(r['wrong_output'] == r['unfinished'] == 0 for r in controls)
    covered = {}
    for arm in d.ARMS:
        combinations = {(r['state'], *r['bell_bits']) for r in controls if r['spec']['workload']=='teleport' and r['spec']['arm']==arm}
        expected = {(s,a,b) for s in range(6) for a in (0,1) for b in (0,1)}
        assert combinations == expected, ('Missing teleportation pilot branches', arm, sorted(expected-combinations))
        covered[arm] = len(combinations)
    return dict(pilot_trials=len(rows), noise_free_controls=len(controls), teleportation_branches_per_arm=covered,
                checks='CPU chronology, actual admission causality, service budgets, physical storage, correction messages and noise-free outputs',
                inference='Integration gate only; no attack-effect inference')
