"""Independent read-only checks of recorded Q12 events. No simulation imports."""
import hashlib
import json
import math
from pathlib import Path

import q12_design as d

VERSION = 'q12-raw-audit-1'
TIME_TOL_NS = 1e-6
SERVICE_SUM_TOL_NS = 1e-5


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
    need(isinstance(name, str) and Path(name).name == name and name not in ('', '.', '..'), 'Invalid checkpoint basename')
    result = Path(folder)/name
    need(result.is_file() and not result.is_symlink(), 'Missing file or symlink: '+str(result))
    return result


def close(a, b, label, tol=TIME_TOL_NS):
    need(isinstance(a, (int, float)) and isinstance(b, (int, float)), label+': nonnumeric value')
    need(math.isfinite(a) and math.isfinite(b), label+': nonfinite value')
    need(abs(a-b) <= tol, f'{label}: observed={a!r}, expected={b!r}, delta={a-b!r} ns')


def trace(rows, label, horizon, serial=False):
    need(isinstance(rows, list) and rows, label+': missing trace')
    need(len({r['task_id'] for r in rows}) == len(rows), label+': duplicate task id')
    for r in rows:
        a, b = r['start_ns'], r['end_ns']
        need(math.isfinite(a) and math.isfinite(b) and 0 <= a <= b <= horizon, label+': invalid interval '+repr(r))
    if serial:
        ordered = sorted(rows, key=lambda r: (r['start_ns'], r['end_ns'], r['task_id']))
        need(rows == ordered, label+': noncanonical boundary order')
        maximum_end = 0.
        for r in ordered:
            need(r['start_ns'] >= maximum_end-TIME_TOL_NS, label+': overlapping CPU service '+repr(r))
            maximum_end = max(maximum_end, r['end_ns'])
    return sum(r['end_ns']-r['start_ns'] for r in rows)


def blank_metrics():
    return dict(trials=0, completed=0, wrong_outputs=0, instruction_events=0, epr_events=0,
        competitor_jobs=0, benign_jobs=0, measured_competitor_service_ns=0.,
        max_abs_admission_delta_ns=0., max_abs_payload_service_error_ns=0.,
        max_abs_competitor_budget_error_ns=0., max_abs_storage_reconstruction_error_ns=0.,
        min_start_minus_actual_admission_ns=None, max_abs_compact_float_delta_ns=0.)


def merge_metrics(total, part):
    for k, v in part.items():
        if k.startswith('max_'):
            total[k] = max(total[k], v)
        elif k.startswith('min_'):
            if v is not None:
                total[k] = v if total[k] is None else min(total[k], v)
        else:
            total[k] += v


def validate_record(r, compact, spec, frozen):
    """Reconstruct compact fields and check recorded event causality.

    This does not replay the quantum state or infer unrecorded peer messages.
    Absolute tolerances match the delivered Q12 worker validators.
    """
    need(r['spec'] == spec == compact['spec'], 'Raw/compact/job specification mismatch')
    inp = d.inputs(spec)
    need(r['inputs'] == inp, 'Seeded raw inputs differ from frozen design')
    phase = d.phase_for(spec, frozen)
    planned = d.release_plan(d.EPOCH_NS, spec['arm'], phase, inp['blind_phase_ns'])
    need(r['public_epoch_ns'] == d.EPOCH_NS and r['selected_phase_ns'] == phase and r['planned_release_ns'] == planned,
         'Release plan uses unexpected values')
    need(r['actual_cpu_scheduler'] == 'CpuFcfsScheduler', 'Unexpected scheduler')
    need(r['sim_end_ns'] == d.HORIZON_NS, 'Unexpected simulation horizon')
    admitted, pid = r['target_admitted_ns'], r['target_pid']
    close(admitted, inp['target_request_ns'], 'Target planned/actual admission')
    cpu, peer, qpu = r['cpu_trace'], r['peer_cpu_trace'], r['qpu_trace']
    cpu_service = trace(cpu, 'Bob CPU', d.HORIZON_NS, serial=True)
    peer_service = trace(peer, 'Alice CPU', d.HORIZON_NS, serial=True)
    trace(qpu, 'Bob QPU', d.HORIZON_NS)
    need(all(t['start_ns'] >= admitted for t in cpu if t['pid'] == pid), 'Target CPU starts before actual admission')
    need(all(t['start_ns'] >= admitted for t in peer), 'Peer CPU starts before target admission')
    need(all(t['pid'] == pid and t['start_ns'] >= admitted for t in qpu), 'Unexpected QPU owner or pre-admission execution')
    jobs = r['admission_events']
    need(len({j['pid'] for j in jobs}) == len(jobs) and pid not in {j['pid'] for j in jobs}, 'Duplicate/target classical-job pid')
    payloads = [t for t in cpu if t['block'] == 'payload']
    need(len(payloads) == len(jobs) and len({t['pid'] for t in payloads}) == len(payloads), 'Duplicate/missing payload')
    bypid = {t['pid']: t for t in payloads}
    need(set(bypid) == {j['pid'] for j in jobs}, 'Payload/job ownership mismatch')
    need({t['pid'] for t in cpu} == {pid, *bypid}, 'Unexpected Bob CPU owner')
    competitors = [j for j in jobs if j['kind'] == 'competitor']
    benign = [j for j in jobs if j['kind'] == 'benign']
    need(len(competitors) == (d.JOBS if planned is not None else 0), 'Competitor job count mismatch')
    need([j['requested_ns'] for j in benign] == inp['background'], 'Background admission schedule mismatch')
    metrics = blank_metrics()
    metrics.update(trials=1, completed=int(bool(r['completed'])), wrong_outputs=r['wrong_output'],
                   competitor_jobs=len(competitors), benign_jobs=len(benign))
    for j in jobs:
        t = bypid[j['pid']]
        need(j['kind'] in ('competitor','benign'), 'Unknown classical job kind')
        need(j['instructions'] == (d.INSTRUCTIONS if j['kind'] == 'competitor' else 10), 'Wrong instruction budget')
        for v in (j['requested_ns'], j['admitted_ns'], t['start_ns'], t['end_ns']):
            need(math.isfinite(v), 'Nonfinite payload time')
        delta = j['admitted_ns']-j['requested_ns']
        close(j['admitted_ns'], j['requested_ns'], 'Classical planned/actual admission')
        # Do not round away a start before actual admission.
        slack = t['start_ns']-j['admitted_ns']
        need(slack >= 0, 'Payload starts before actual admission')
        service_error = (t['end_ns']-t['start_ns'])-j['instructions']*d.HOST_INSTRUCTION_NS
        need(abs(service_error) < TIME_TOL_NS, 'Payload duration differs from instruction budget')
        metrics['max_abs_admission_delta_ns'] = max(metrics['max_abs_admission_delta_ns'], abs(delta))
        metrics['max_abs_payload_service_error_ns'] = max(metrics['max_abs_payload_service_error_ns'], abs(service_error))
        metrics['min_start_minus_actual_admission_ns'] = slack if metrics['min_start_minus_actual_admission_ns'] is None else min(metrics['min_start_minus_actual_admission_ns'], slack)
        if j['kind'] == 'competitor':
            need(j['requested_ns'] == planned, 'Competitor release differs from frozen plan')
    measured = sum(bypid[j['pid']]['end_ns']-bypid[j['pid']]['start_ns'] for j in competitors)
    expected = 0 if planned is None else d.BURST_SERVICE_NS
    need(abs(measured-expected) < SERVICE_SUM_TOL_NS, 'Measured competing work differs from equal-work budget')
    metrics['measured_competitor_service_ns'] = measured
    metrics['max_abs_competitor_budget_error_ns'] = abs(measured-expected)
    events = r['quantum_events']
    instructions = [e for e in events if e['kind'] == 'instruction']
    eprs = [e for e in events if e['kind'] == 'epr_ready']
    need(len(instructions)+len(eprs) == len(events), 'Unknown quantum event type')
    routine_block = {'prepare_state':'prepare', 'measure_state':'measure', 'corrections':'correct', 'verify_state':'measure'}
    for e in events:
        need(e['pid'] == pid, 'Unexpected quantum-event owner')
        need(math.isfinite(e['end_ns']) and admitted <= e['end_ns'] <= d.HORIZON_NS, 'Invalid quantum-event end')
        if e['kind'] == 'instruction':
            need(math.isfinite(e['start_ns']) and admitted <= e['start_ns'] <= e['end_ns'], 'Invalid quantum instruction interval')
            need(e['routine'] in routine_block, 'Unexpected target routine')
            block = routine_block[e['routine']]
            containers = [t for t in qpu if t['block'] == block and t['task_type'] == 'LocalRoutineTask'
                          and t['start_ns']-TIME_TOL_NS <= e['start_ns'] and e['end_ns'] <= t['end_ns']+TIME_TOL_NS]
            need(len(containers) == 1, 'Instruction is not inside its unique recorded QPU routine')
        else:
            need(e.get('virt') == 0, 'Unexpected received EPR virtual qubit')
            need(any(t['block'] == 'epr' and t['start_ns']-TIME_TOL_NS <= e['end_ns'] <= t['end_ns']+TIME_TOL_NS for t in qpu),
                 'EPR readiness is outside the recorded request task')
    metrics['instruction_events'], metrics['epr_events'] = len(instructions), len(eprs)
    need(isinstance(r['completed'], bool) and isinstance(r['timeout'], bool), 'Invalid completion flags')
    physical = None
    if r['completed']:
        need(not r['timeout'] and r['wrong_output'] in (0,1) and r['error'] == r['wrong_output'], 'Inconsistent completed output')
        finish = r['target_finished_ns']
        need(isinstance(finish, (int,float)) and admitted <= finish <= d.HORIZON_NS, 'Invalid target completion')
        target_output = [t for t in cpu if t['pid'] == pid and t['block'] == 'output']
        need(len(target_output) == 1, 'Missing/duplicate target output task')
        close(finish, target_output[0]['end_ns'], 'Target completion versus output task')
        need(all(e['end_ns'] <= finish+TIME_TOL_NS for e in events), 'Quantum event follows recorded completion')
        if spec['workload'] == 'plus':
            need(r['bell_bits'] is None and not eprs, 'Unexpected plus-state entanglement record')
            starts = [e for e in instructions if e['routine'] == 'prepare_state' and e['name'] == 'h']
            ends = [e for e in instructions if e['routine'] == 'measure_state' and e['name'] == 'h']
            meas = [e for e in instructions if e['routine'] == 'measure_state' and e['name'] == 'meas']
            init = [e for e in instructions if e['routine'] == 'prepare_state' and e['name'] == 'init']
            need(len(init) == 1 and len(starts) == 1 and init[0]['end_ns'] <= starts[0]['start_ns']+TIME_TOL_NS, 'Plus-state preparation sequence mismatch')
        else:
            bits = r['bell_bits']
            need(isinstance(bits, list) and len(bits) == 2 and all(b in (0,1) for b in bits), 'Invalid recorded Bell bits')
            starts = eprs
            ends = [e for e in instructions if e['routine'] == 'verify_state' and e['name'].startswith('rot_')]
            meas = [e for e in instructions if e['routine'] == 'verify_state' and e['name'] == 'meas']
            corrections = [e for e in instructions if e['routine'] == 'corrections']
            need(corrections and len(starts) == len(ends) == 1, 'Missing teleportation correction/verification sequence')
            for gate, bit in [('z',bits[0]),('x',bits[1])]:
                need(sum(e['name'] == gate for e in corrections) == bit, 'Pauli correction branch differs from recorded Bell bit: '+gate)
            need(all(starts[0]['end_ns']-TIME_TOL_NS <= e['start_ns'] and e['end_ns'] <= ends[0]['start_ns']+TIME_TOL_NS for e in corrections),
                 'Correction occurs outside EPR-to-verification interval')
        need(len(starts) == len(ends) == len(meas) == 1, 'Missing/duplicate physical storage boundaries or measurement')
        physical = ends[0]['start_ns']-starts[0]['end_ns']
        need(physical >= 0 and ends[0]['end_ns'] <= meas[0]['start_ns']+TIME_TOL_NS, 'State used before ready or verification follows measurement')
        close(r['state_use_ns'], ends[0]['start_ns'], 'State-use boundary')
        close(r['physical_storage_ns'], physical, 'Physical storage reconstruction')
        metrics['max_abs_storage_reconstruction_error_ns'] = abs(r['physical_storage_ns']-physical)
    else:
        need(r['timeout'] and r['error'] == 1 and r['wrong_output'] == 0, 'Inconsistent unfinished handling')
        need(r['state_use_ns'] is None and r['physical_storage_ns'] is None, 'Unfinished output has a storage interval')
    if spec['noise'] == 'noise_free':
        need(r['completed'] and r['wrong_output'] == 0, 'Noise-free correctness failure')
    reconstructed = dict(spec=spec, wrong_output=int(r['wrong_output']), unfinished=int(not r['completed']),
        application_failure=int(r['error']), state=inp['state'], bell_bits=r['bell_bits'], physical_storage_ns=physical,
        target_finished_ns=r['target_finished_ns'], target_request_ns=inp['target_request_ns'], selected_phase_ns=phase,
        planned_release_ns=planned, competitor_service_ns=expected, bob_cpu_service_ns=cpu_service,
        alice_cpu_service_ns=peer_service, qnos_instruction_count=len(instructions), completed_epr_pairs=len(eprs))
    if spec['stage'] == 'calibration':
        reconstructed['own_observation'] = dict(workload=spec['workload'], campaign=spec['campaign'], candidate=spec['arm'],
            shot=spec['shot'], completed=r['completed'], returned_bit=int(r['wrong_output']) if r['completed'] else None)
    need(set(compact) == set(reconstructed), 'Compact schema differs from reconstructed schema')
    for k, value in reconstructed.items():
        if k in ('bob_cpu_service_ns','alice_cpu_service_ns','physical_storage_ns','target_finished_ns') and value is not None:
            close(compact[k], value, 'Compact '+k)
            metrics['max_abs_compact_float_delta_ns'] = max(metrics['max_abs_compact_float_delta_ns'], abs(compact[k]-value))
        else:
            need(compact[k] == value, 'Compact reconstruction mismatch: '+k)
    return metrics
