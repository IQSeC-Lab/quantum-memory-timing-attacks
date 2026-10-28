"""Independent stress gates, using the audited parent raw-dispatch reconstruction."""
from collections import Counter
import q16_stress_design as d
from q16_audit import need, close, checked_trace, reconstruct_dispatch, check_channel


def wake_evidence(raw, status, blocked_field, victim_pid):
    """Require an actually blocked victim task to execute before the quota timer.

    Evidence proves timely resumption of the blocked task. It does not identify
    which pydynaa event term triggered when signals occur at the same timestamp.
    """
    tasks = {t['task_id']: t for t in raw['cpu_trace']}
    evidence = []
    for row in raw['dispatch_decisions']:
        if row['selected'] is not None or status not in row['status']:
            continue
        for tid in row[blocked_field]:
            task = tasks.get(tid)
            if (task is not None and task['pid'] == victim_pid and
                    row['time_ns'] < task['start_ns'] < row['wake_ns']):
                evidence.append(dict(wait_sequence=row['sequence'], wait_ns=row['time_ns'],
                                     timer_ns=row['wake_ns'], resumed_task_id=tid,
                                     resumed_ns=task['start_ns']))
    need(evidence, 'No actual pre-timer victim resumption from '+status)
    return evidence


def validate_record(raw, spec):
    need(raw['spec'] == spec, 'Stress specification differs')
    inp = d.inputs(spec)
    need(raw['inputs'] == inp, 'Fixed stress inputs differ')
    close(raw['sim_end_ns'], 30_000_000, 'Wrong fixed stress horizon')
    need(raw['native_t2_ns'] == 0 and raw['actual_cpu_scheduler'] == 'CpuFcfsScheduler'
         and raw['scheduler_policy'] == spec['scheduler'], 'Backend/model differs')
    dispatch = reconstruct_dispatch(raw)
    need(not raw['active_cpu_trace'], 'Unfinished CPU work at stress horizon')
    checked_trace(raw['peer_cpu_trace'], raw['sim_end_ns'])
    checked_trace(raw['qpu_trace'], raw['sim_end_ns'])
    traces = raw['cpu_trace']
    jobs = raw['admission_events']
    need(len(jobs) == len(inp['attacker'])+len(inp['private']['benign']), 'Lost or added CPU jobs')
    need(len({j['pid'] for j in jobs}) == len(jobs), 'CPU PID reused')
    claimed = set()
    for tenant, plan in [('attacker',inp['attacker']),('benign',inp['private']['benign'])]:
        group = sorted((j for j in jobs if j['kind'] == tenant), key=lambda j:j['plan_index'])
        need(len(group) == len(plan), 'Incorrect tenant job count')
        for actual, planned in zip(group,plan):
            need(actual['plan_index'] == planned['index'] and actual['instructions'] == 30,
                 'Wrong CPU program or release index')
            close(actual['requested_ns'], planned['requested_ns'], 'Wrong release plan')
            close(actual['admitted_ns'], planned['requested_ns'], 'Late or early CPU admission')
            pid = actual['pid']
            need(raw['tenant_by_pid'].get(str(pid)) == tenant, 'Wrong CPU tenant')
            own = [t for t in traces if t['pid'] == pid]
            need(len(own) == 1 and own[0]['block'] == 'payload'
                 and own[0]['task_type'] == 'HostLocalTask', 'CPU job is not one complete payload')
            task = own[0]
            need(task['start_ns'] >= actual['admitted_ns'], 'CPU service before admission')
            close(task['end_ns']-task['start_ns'], 300_000, 'Unexpected actual CPU cost')
            close(actual['finished_ns'], task['end_ns'], 'CPU completion not trace-supported')
            close(raw['finished_pids']['bob'].get(str(pid)), task['end_ns'], 'Finish ledger differs')
            need(actual['x'] == 28, 'Incorrect ordinary CPU payload output')
            claimed.add(pid)
    attacker = [t for t in traces if raw['tenant_by_pid'][str(t['pid'])] == 'attacker']
    waits = [r for r in raw['dispatch_decisions'] if r['selected'] is None]
    need(waits, 'Quota wait path did not execute')
    reason = 'period_boundary' if spec['case'] == 'boundary_new_job' else 'insufficient_budget'
    relevant = [r for r in waits if any(reason in v for v in r['blocked'].values())]
    need(relevant, 'Required quota deferral reason not exercised: '+reason)
    need(all(r['wake_ns'] > r['time_ns'] for r in waits), 'Nonpositive quota wait')
    need(all('WAITING_START_TIME' in r['status'] for r in waits), 'Quota timer lost')
    periods = Counter(int(t['start_ns'])//10_000_000 for t in attacker)
    evidence = dict(required_reason=reason, quota_waits=len(waits),
                    deferral_sequences=[r['sequence'] for r in relevant],
                    attacker_jobs_by_start_period={str(k):v for k,v in sorted(periods.items())})
    if spec['case'] == 'boundary_new_job':
        need(periods == {1:1}, 'Boundary job was not deferred into period one')
        first = min(relevant,key=lambda r:r['sequence'])
        close(first['time_ns'], 9_800_000, 'Boundary wait not observed at fixed release')
        close(first['reserved_before_ns'], 0, 'Boundary fixture accidentally exhausted budget')
        expected_benign_start, expected_attacker_restart = 9_850_000, 10_150_000
    else:
        need(periods == {0:4,1:2}, 'Quota not exhausted and replenished as designed')
        first = min(relevant,key=lambda r:r['sequence'])
        close(first['time_ns'], 1_200_000, 'Exhaustion wait not observed after four blocks')
        close(first['reserved_before_ns'], 1_200_000, 'Period budget not exhausted')
        expected_benign_start, expected_attacker_restart = 1_300_000, 10_000_000
    close(first['wake_ns'], 10_000_000, 'Wrong first replenishment boundary')
    restart = min(t['start_ns'] for t in attacker if t['start_ns'] >= 10_000_000)
    close(restart, expected_attacker_restart, 'Deferred work did not resume at first eligible time')
    evidence['attacker_restart_ns'] = restart
    if inp['private']['benign']:
        benign = [t for t in traces if raw['tenant_by_pid'][str(t['pid'])] == 'benign']
        need(len(benign) == 1, 'Missing benign new-job wake-up')
        close(benign[0]['start_ns'], expected_benign_start, 'New work waited behind throttled attacker')
        need(first['time_ns'] < benign[0]['start_ns'] < first['wake_ns'], 'New-job pre-timer progress not exercised')
        evidence['benign_start_ns'] = benign[0]['start_ns']
    need(len(raw['requests']) == len(inp['private']['requests']), 'Wrong stress victim count')
    channels = {q['pid']:q for q in raw['memory_channel']}
    need(len(channels) == len(raw['memory_channel']) == len(raw['requests']), 'Channel coverage differs')
    request_rows = []
    for request, planned in zip(raw['requests'], inp['private']['requests']):
        need(all(request[k] == v for k,v in planned.items()), 'Victim fixture differs')
        close(request['actual_offered_ns'], 1_300_000, 'Victim not offered at fixture time')
        close(request['admitted_ns'], 1_300_000, 'Victim admission delayed')
        need(request['victim'] is not None, 'Victim not admitted')
        pid, peer_pid = request['victim']['bob_pid'], request['victim']['alice_pid']
        need(pid not in claimed and raw['tenant_by_pid'].get(str(pid)) == 'victim', 'Victim ownership differs')
        claimed.add(pid)
        own = [t for t in traces if t['pid'] == pid]
        peer = [t for t in raw['peer_cpu_trace'] if t['pid'] == peer_pid]
        need(own and peer and all(t['start_ns'] >= request['admitted_ns'] for t in own+peer), 'Victim chronology differs')
        outputs = [t for t in own if t['block'] == 'output']
        peer_last = 'reply' if spec['workload'] == 'plus' else 'output'
        peer_outputs = [t for t in peer if t['block'] == peer_last]
        need(len(outputs) == len(peer_outputs) == 1, 'Victim did not complete both peers')
        close(request['finished_ns'], outputs[0]['end_ns'], 'Victim completion not trace-supported')
        close(request['peer_finished_ns'], peer_outputs[0]['end_ns'], 'Peer completion differs')
        close(raw['finished_pids']['bob'].get(str(pid)), request['finished_ns'], 'Victim finish ledger differs')
        close(raw['finished_pids']['alice'].get(str(peer_pid)), request['peer_finished_ns'], 'Peer finish ledger differs')
        need(request['completed'] and request['wrong_output'] == 0 and request['output_values'].get('m') == 0
             and request['category'] == 'correct_on_time', 'Noiseless victim returned wrong/missing output')
        need(max(request['finished_ns'], request['peer_finished_ns']) < 10_000_000,
             'Victim completion waited for attacker quota replenishment')
        bell = None
        if spec['workload'] == 'plus':
            need(request['output_values'].get('reply') == 1, 'Echo reply differs')
        else:
            bell = [request['output_values'].get(k) for k in ('m1','m2')]
            need(all(x in (0,1) for x in bell), 'Invalid Bell result')
            need(all(request['output_values'][k] == request['peer_output_values'].get(k)
                     for k in ('m1','m2')), 'Correction message differs from sender')
        density_error = check_channel(raw,request,channels[pid])
        evidence['message_resumptions'] = wake_evidence(raw,'WAITING_MSG','message_blocked',pid)
        evidence['qpu_resumptions'] = wake_evidence(raw,'WAITING_OTHER_CORE','blocked_other_core',pid)
        request_rows.append(dict(pid=pid, completed_ns=request['finished_ns'], bell_bits=bell,
                                 maximum_density_error=density_error))
    need(set(raw['tenant_by_pid']) == {str(p) for p in claimed}, 'Unexplained tenant PID')
    need({t['pid'] for t in traces} == claimed, 'Missing or unexplained CPU owner')
    return dict(spec=spec,status='PASS',dispatch=dispatch,evidence=evidence,
                attacker_jobs=len(attacker),benign_jobs=len(inp['private']['benign']),
                request_rows=request_rows)


def summarize(records):
    need([r['spec'] for r in records] == list(d.specifications()), 'Missing, duplicate or reordered stress case')
    need(all(r['status'] == 'PASS' for r in records), 'A stress gate failed')
    return dict(status='PASS', revision=d.REVISION, executions=len(records),
                attacker_jobs=sum(r['attacker_jobs'] for r in records),
                benign_jobs=sum(r['benign_jobs'] for r in records),
                victim_requests=sum(len(r['request_rows']) for r in records),
                quota_waits=sum(r['evidence']['quota_waits'] for r in records),
                max_attacker_period_service_ns=max(r['dispatch']['max_attacker_period_service_ns'] for r in records),
                cases=records,
                interpretation='Fixed-fixture quota enforcement and timely victim resumption passed. '
                               'This does not establish a Q16 attack effect. '
                               'Calibrate the deadline and own-workload policy; then resolve power and freeze the study.')
