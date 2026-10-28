"""Qoala scheduling with a source-informed isotropic memory envelope.

The native T1/T2 channel is disabled. Because storage operations in these fixed
programs are ideal local unitaries and remote operations, the full isotropic
channel can be moved to immediately before verification. This would not be
valid for arbitrary gates, local adaptive measurements, or nonunital noise.
"""
import math
import numpy as np
import q13_design as d
import q13_baseline as baseline
from p1_engine import Case, ordered_cpu_trace
from time_checks import check_payload

CURRENT_CASE = None


def failure_snapshot():
    if CURRENT_CASE is None:
        return None
    c = CURRENT_CASE
    result = dict(sim_time_ns=float(c.ns.sim_time()), quantum_events=c.events,
                  admission_events=c.jobs, finished_pids=c.finished_pids)
    try:
        result['traces'] = c.traces()
    except Exception as error:
        result['trace_error'] = repr(error)
    return result


def target_vector(workload, state):
    vectors = [np.array([1,1])/np.sqrt(2), np.array([1,-1])/np.sqrt(2),
               np.array([1,1j])/np.sqrt(2), np.array([1,-1j])/np.sqrt(2),
               np.array([1,0]), np.array([0,1])]
    return np.asarray(vectors[0 if workload=='plus' else state], dtype=complex)


def contraction(profile, age_ns):
    assert math.isfinite(age_ns) and age_ns >= 0
    if profile == 'noise_free':
        return 1.0
    p = d.PROFILES[profile]
    return float(np.exp(-(age_ns/1e6/p['t_ms'])**p['exponent']))


def install_channel(case, spec, state):
    from netsquid.qubits import qubitapi as qapi, depolarize
    proc = case.bob.qnos.processor
    original = proc._interpret_instruction
    snapshots = []
    target = target_vector(spec['workload'], state)
    def instruction(pid, instr):
        routine = proc._current_routine.routine.name
        name = instr.mnemonic
        verification = ((spec['workload']=='plus' and routine=='measure_state' and name=='h') or
                        (spec['workload']=='teleport' and routine=='verify_state' and name.startswith('rot_')))
        if verification:
            assert not snapshots, 'The storage channel must be applied once only'
            if spec['workload']=='plus':
                starts = [e for e in case.events if e['pid']==pid and e.get('routine')=='prepare_state' and e.get('name')=='h']
            else:
                starts = [e for e in case.events if e['pid']==pid and e['kind']=='epr_ready']
            assert len(starts)==1, ('Missing or duplicate storage-start event', starts)
            start = starts[0]['end_ns']
            stop = float(case.ns.sim_time())
            assert stop >= start
            physical_id = case.bob.memmgr.phys_id_for(pid, 0)
            assert physical_id is not None
            qubit = case.bob.qdevice.get_local_qubit(physical_id)
            before = qapi.reduced_dm([qubit])
            ideal = np.outer(target, target.conj())
            assert np.max(np.abs(before-ideal)) < baseline.TOL, ('Incorrect state before memory channel', spec, before)
            factor = contraction(spec['profile'], stop-start)
            depolarize(qubit, 1-factor)
            after = qapi.reduced_dm([qubit])
            expected = factor*before + (1-factor)*np.eye(2)/2
            fidelity = float(qapi.fidelity(qubit, target.reshape(-1,1), squared=True))
            assert np.max(np.abs(after-expected)) < baseline.TOL
            assert abs(fidelity-(1+factor)/2) < baseline.TOL
            assert case.ns.sim_time()==stop, 'Inspection or channel application advanced simulator time'
            snapshots.append(dict(pid=pid, physical_id=physical_id, start_ns=start, use_ns=stop,
                                  age_ns=stop-start, contraction=factor, fidelity=fidelity,
                                  expected_infidelity=1-fidelity,
                                  density_real=after.real.tolist(), density_imag=after.imag.tolist()))
        # This wrapper yields the original instrumented interpreter unchanged.
        generator = original(pid, instr)
        if generator is not None:
            yield from generator
    proc._interpret_instruction = instruction
    return snapshots


def run(spec, frozen=None):
    global CURRENT_CASE
    CURRENT_CASE = None
    if spec['stage']=='baseline':
        return baseline.run(spec)
    from netsquid.protocols import Protocol
    inp = d.inputs(spec)
    planned = d.release_plan(d.EPOCH_NS, spec['workload'], spec['arm'], inp['random_phase_ns'])
    c = Case('FCFS', inp['latency_ns'], 0, inp['seed'], spec['workload'])
    CURRENT_CASE = c
    channel = install_channel(c, spec, inp['state'])
    target, arrivals = {}, []
    class Target(Protocol):
        def run(self):
            yield self.await_timer(inp['target_request_ns'])
            arrivals.append(float(c.ns.sim_time()))
            target.update(c.victim(inp['state'], dynamic=True))
    class Competitor(Protocol):
        def run(self):
            yield self.await_timer(planned)
            c.admit_cpu(planned, d.INSTRUCTIONS, d.JOBS, 'competitor')
    c.net.start()
    protocols = [Target()]
    if planned is not None:
        protocols.append(Competitor())
    for protocol in protocols:
        protocol.start()
    c.ns.sim_run(duration=d.HORIZON_NS)
    assert target and len(arrivals)==1
    outcome = c.outcome(target)
    traces = c.traces()
    stats = c.alice.scheduler.get_statistics()
    peer = ordered_cpu_trace([dict(task_id=tid, pid=t.pid, block=t.block_name,
                                  task_type=type(t).__name__, start_ns=float(stats._cpu_task_starts[tid]),
                                  end_ns=float(stats._cpu_task_ends[tid]))
                             for tid,t in stats._cpu_tasks_executed.items()])
    return dict(outcome, spec=spec, inputs=inp, planned_release_ns=planned,
                target_pid=target['bob_pid'], target_admitted_ns=arrivals[0],
                target_finished_ns=c.finished_pids['bob'].get(target['bob_pid']),
                memory_channel=channel, quantum_events=c.events,
                admission_events=c.jobs, cpu_trace=traces['cpu'], qpu_trace=traces['qpu'],
                peer_cpu_trace=peer, actual_cpu_scheduler=type(c.bob.scheduler.cpu_scheduler).__name__,
                native_t2_ns=c.t2, sim_end_ns=float(c.ns.sim_time()))


def validate(r, spec, frozen=None):
    if spec['stage']=='baseline':
        return baseline.validate(r, spec)
    assert r['spec']==spec and r['inputs']==d.inputs(spec)
    inp = r['inputs']
    planned = d.release_plan(d.EPOCH_NS,spec['workload'],spec['arm'],inp['random_phase_ns'])
    assert r['planned_release_ns']==planned and r['native_t2_ns']==0
    assert r['actual_cpu_scheduler']=='CpuFcfsScheduler' and r['sim_end_ns']==d.HORIZON_NS
    assert abs(r['target_admitted_ns']-inp['target_request_ns']) <= 1e-6
    for trace in [r['cpu_trace'],r['peer_cpu_trace']]:
        assert trace==ordered_cpu_trace(trace)
        assert len({t['task_id'] for t in trace})==len(trace)
        assert all(0<=t['start_ns']<=t['end_ns']<=d.HORIZON_NS for t in trace)
    assert all(t['start_ns']>=r['target_admitted_ns'] for t in r['cpu_trace'] if t['pid']==r['target_pid'])
    jobs = r['admission_events']
    payloads = {t['pid']:t for t in r['cpu_trace'] if t['block']=='payload'}
    assert len(jobs)==(d.JOBS if planned is not None else 0)
    assert set(payloads)=={j['pid'] for j in jobs}
    for j in jobs:
        assert j['requested_ns']==planned and j['kind']=='competitor' and j['instructions']==d.INSTRUCTIONS
        check_payload(j,payloads[j['pid']])
    service = sum(t['end_ns']-t['start_ns'] for t in payloads.values())
    assert abs(service-(d.SERVICE_NS if planned is not None else 0)) < 1e-5
    channel = r['memory_channel']
    assert len(channel) <= 1
    fidelity, age = None, None
    if channel:
        q = channel[0]
        assert q['pid']==r['target_pid'] and q['age_ns']==q['use_ns']-q['start_ns']>=0
        events = [x for x in r['quantum_events'] if x['pid']==r['target_pid']]
        if spec['workload']=='plus':
            starts = [x for x in events if x.get('routine')=='prepare_state' and x.get('name')=='h']
            ends = [x for x in events if x.get('routine')=='measure_state' and x.get('name')=='h']
        else:
            starts = [x for x in events if x['kind']=='epr_ready']
            ends = [x for x in events if x.get('routine')=='verify_state' and x.get('name','').startswith('rot_')]
        assert len(starts)==len(ends)==1
        assert q['start_ns']==starts[0]['end_ns'] and q['use_ns']==ends[0]['start_ns']
        factor = contraction(spec['profile'],q['age_ns'])
        assert abs(q['contraction']-factor)<1e-12
        fidelity, age = q['fidelity'], q['age_ns']
        assert abs(fidelity-(1+factor)/2)<baseline.TOL and abs(q['expected_infidelity']-(1-fidelity))<1e-12
        state = np.asarray(q['density_real'])+1j*np.asarray(q['density_imag'])
        target = target_vector(spec['workload'],inp['state'])
        expected = factor*np.outer(target,target.conj())+(1-factor)*np.eye(2)/2
        assert np.max(np.abs(state-expected))<baseline.TOL
    if r['completed']:
        assert channel and not r['timeout'] and r['error']==r['wrong_output'] in (0,1)
        assert r['target_finished_ns'] is not None
        if spec['workload']=='teleport':
            assert len(r['bell_bits'])==2 and all(bit in (0,1) for bit in r['bell_bits'])
    else:
        assert r['timeout'] and r['wrong_output']==0 and r['error']==1
    if spec['profile']=='noise_free':
        assert r['completed'] and r['wrong_output']==0 and abs(fidelity-1)<baseline.TOL
    return dict(spec=spec, state=inp['state'], bell_bits=r['bell_bits'],
                pairing_sha256=d.stable(inp), target_request_ns=inp['target_request_ns'],
                wrong_output=int(r['wrong_output']), unfinished=int(not r['completed']),
                fidelity=fidelity, expected_infidelity=None if fidelity is None else 1-fidelity,
                physical_storage_ns=age, planned_release_ns=planned,
                competitor_service_ns=int(round(service)), simulator_executions=1,
                target_finished_ns=r['target_finished_ns'])


def validate_gate(rows, stage):
    assert [r['spec'] for r in rows]==list(d.specifications(stage))
    if stage=='baseline':
        assert all(r['both_branches_checked'] and r['max_density_error']<baseline.TOL for r in rows)
        return dict(cases=len(rows), simulator_executions=sum(r['simulator_executions'] for r in rows),
                    maximum_density_error=max(r['max_density_error'] for r in rows),
                    status='NetSquid baseline mathematics checked; no hardware execution')
    controls = [r for r in rows if r['spec']['profile']=='noise_free']
    assert len(controls)==1176 and all(r['wrong_output']==r['unfinished']==0 for r in controls)
    coverage = {}
    for arm in d.ARMS:
        actual = {(r['state'],*r['bell_bits']) for r in controls if r['spec']['workload']=='teleport' and r['spec']['arm']==arm}
        expected = {(state,a,b) for state in range(6) for a in (0,1) for b in (0,1)}
        assert actual==expected, ('Missing teleportation pilot branch',arm,sorted(expected-actual))
        coverage[arm]=len(actual)
    assert all(not r['unfinished'] for r in rows)
    return dict(trials=len(rows), noise_free_controls=len(controls), teleportation_branches_per_arm=coverage,
                status='Integration checks passed, including state, actual channel, chronology and equal CPU work; no attack-effect gate')
