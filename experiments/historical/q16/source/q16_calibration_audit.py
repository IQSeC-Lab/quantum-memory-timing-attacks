"""Independent calibration raw replay; no simulator/policy imports."""
import math
from collections import defaultdict
from fractions import Fraction
import q16_calibration_design as d
from q16_audit import need, close, checked_trace, reconstruct_dispatch, check_channel

CATEGORIES = ('completed_correct','completed_wrong','unfinished')


def validate_record(raw, spec):
    need(raw['spec']==spec, 'Specification mismatch')
    inp = d.inputs(spec)
    need(raw['inputs']==inp, 'Release/input reconstruction differs')
    close(raw['sim_end_ns'],inp['horizon_ns'],'Incorrect fixed horizon')
    need(raw['native_t2_ns']==0 and raw['actual_cpu_scheduler']=='CpuFcfsScheduler' and raw['scheduler_policy']==spec['scheduler'], 'Backend/model mismatch')
    dispatch = reconstruct_dispatch(raw)
    checked_trace(raw['peer_cpu_trace'],raw['sim_end_ns'])
    cpu = raw['cpu_trace']
    cpu_by_pid, peer_by_pid, active_by_pid, created_by_pid = [defaultdict(list) for _ in range(4)]
    for source,target in [(cpu,cpu_by_pid),(raw['peer_cpu_trace'],peer_by_pid),
                          (raw['active_cpu_trace'],active_by_pid),(raw['created_tasks'],created_by_pid)]:
        for task in source:
            target[task['pid']].append(task)
    need(len(raw['requests'])==len(inp['private']['requests']), 'Lost offered requests')
    pid_to_request = {}
    compact_requests = []
    max_density_error = 0
    previous = None
    channels = {q['pid']:q for q in raw['memory_channel']}
    need(len(channels)==len(raw['memory_channel']), 'Channel applied more than once')
    for request, planned in zip(raw['requests'],inp['private']['requests']):
        need(all(request[k]==v for k,v in planned.items()), 'Request metadata differs')
        close(request['actual_offered_ns'],planned['offered_ns'],'Offer lost or delayed')
        victim = request['victim']
        finished = None
        bell = None
        if victim is not None:
            pid = victim['bob_pid']
            need(pid not in pid_to_request and raw['tenant_by_pid'].get(str(pid))=='victim','Victim PID reused or misowned')
            pid_to_request[pid] = request
            need(request['admitted_ns']>=request['offered_ns'], 'Admission before offer')
            if previous is not None:
                need(previous['finished_ns'] is not None and previous['peer_finished_ns'] is not None, 'Serial victim admitted before predecessor finished')
                need(request['admitted_ns']>=max(previous['finished_ns'],previous['peer_finished_ns']), 'Overlapping victim admissions')
            own = cpu_by_pid[pid]
            peer = peer_by_pid[victim['alice_pid']]
            need(all(t['start_ns']>=request['admitted_ns'] for t in own+peer),'Service before admission')
            outputs = [t for t in own if t['block']=='output']
            need(len(outputs)<=1,'Duplicate output task')
            finished = outputs[0]['end_ns'] if outputs else None
            need(request['finished_ns']==finished,'Finished timestamp differs from output block')
            peer_last = 'reply' if spec['workload']=='plus' else 'output'
            peer_outputs = [t for t in peer if t['block']==peer_last]
            need(len(peer_outputs)<=1 and request['peer_finished_ns']==(peer_outputs[0]['end_ns'] if peer_outputs else None),'Peer completion mismatch')
            previous = request
        else:
            need(request['admitted_ns'] is None and request['finished_ns'] is None and request['peer_finished_ns'] is None,'Unadmitted request has service')
            need(not request['output_values'] and not request['peer_output_values'],'Unadmitted request has an output')
        complete = finished is not None
        need(complete==request['completed'], 'Completion not supported by CPU trace')
        wrong = None
        if complete:
            values = request['output_values']
            need(values.get('m') in (0,1), 'Missing/invalid quantum output')
            wrong = int(values['m'])
            need(pid in channels,'Completed output has no channel snapshot')
            if spec['workload']=='plus':
                need(values.get('reply')==1,'Incorrect echo reply')
            else:
                bell = [values.get('m1'), values.get('m2')]
                need(all(x in (0,1) for x in bell),'Invalid Bell bits')
                need(all(values[k]==request['peer_output_values'].get(k) for k in ('m1','m2')),'Correction message differs from sender')
        need(wrong==request['wrong_output'],'Wrong-output marker differs')
        # Independent joint outcome classification; missing never becomes correct.
        category = ('unfinished' if not complete else 'completed_wrong' if wrong else 'completed_correct')
        need(request['category']==category,'Joint outcome category differs')
        channel = channels.get(victim['bob_pid']) if victim is not None else None
        if channel is not None:
            max_density_error = max(max_density_error, check_channel(raw,request,channel))
        if spec['profile']=='noise_free':
            need(complete and wrong==0,'Noise-free correctness control failed')
        compact_requests.append(dict(index=request['index'],measured=request['measured'],state=request['state'],
            bell_bits=bell, category=category, wrong_output=wrong, offered_ns=request['offered_ns'],
            admitted_ns=request['admitted_ns'],finished_ns=finished,
            peer_finished_ns=request['peer_finished_ns'],
            response_ns=None if finished is None else finished-request['offered_ns'],
            storage_ns=None if channel is None else channel['age_ns'],
            expected_infidelity=None if channel is None else channel['expected_infidelity']))
    need(set(channels)<=set(pid_to_request),'Unowned channel snapshot')
    job_ids = set()
    service = {}
    incomplete = {}
    for tenant, plan in [('attacker',inp['attacker']), ('benign',inp['private']['benign'])]:
        jobs = [j for j in raw['admission_events'] if j['kind']==tenant]
        need(len(jobs)==len(plan),'Lost competing job')
        total = 0.0
        incomplete[tenant] = 0
        for job, expected in zip(jobs,plan):
            pid = job['pid']
            need(pid not in job_ids and pid not in pid_to_request and raw['tenant_by_pid'].get(str(pid))==tenant,'Job PID duplicate or wrong tenant')
            job_ids.add(pid)
            need(job['plan_index']==expected['index'] and job['instructions']==expected['instructions'],'Job contents differ')
            close(job['requested_ns'],expected['requested_ns'],'Requested release differs')
            close(job['admitted_ns'],expected['requested_ns'],'Attacker/benign release delayed by harness')
            created = created_by_pid[pid]
            need(len(created)<=1 and all(t['block']=='payload' and t['task_type']=='HostLocalTask' for t in created),'Uncharged setup/output or unexpected job task')
            done = cpu_by_pid[pid]
            active = active_by_pid[pid]
            need(len(done)+len(active)<=1,'Job executed more than one block')
            if done:
                task = done[0]
                need(task['start_ns']>=job['admitted_ns'],'Competing service before release')
                close(task['end_ns']-task['start_ns'],300_000,'Complete job cost differs')
                close(job['finished_ns'],task['end_ns'],'Job completion differs')
                need(job['x']==28,'Ordinary instruction computation failed')
                total += task['end_ns']-task['start_ns']
            else:
                need(job['finished_ns'] is None and job['x'] is None,'Unfinished job marked complete')
                incomplete[tenant] += 1
                total += sum(t['end_ns']-t['start_ns'] for t in active)
        service[tenant] = total
    need(len(job_ids)==len(raw['admission_events']),'Unknown competing tenant')
    need(set(raw['tenant_by_pid'])=={str(p) for p in job_ids|set(pid_to_request)},'Unaccounted tenant PID')
    measured = [r for r in compact_requests if r['measured']]
    counts = {key:sum(r['category']==key for r in measured) for key in CATEGORIES}
    need(sum(counts.values())==d.MEASURED[spec['stage']],'Incorrect offered measured denominator')
    return dict(spec=spec, pairing_sha256=inp['pairing_sha256'],
        attacker_plan_sha256=inp['attacker_plan_sha256'], request_rows=compact_requests,
        measured_joint_counts=counts, offered_measured=len(measured),
        offered_all=len(compact_requests), incomplete_jobs=incomplete,
        requested_attacker_service_ns=len(inp['attacker'])*300_000,
        executed_attacker_service_ns=service['attacker'],
        requested_benign_service_ns=len(inp['private']['benign'])*300_000,
        executed_benign_service_ns=service['benign'],
        realized_benign_offered_load=len(inp['private']['benign'])*300_000/inp['release_end_ns'],
        max_density_error=max_density_error, audit=dispatch)


def cohort(records, stage):
    expected = list(d.specifications(stage))
    rows = [r for r in records if r['spec']['stage'] == stage]
    by_spec = {d.stable(r['spec']): r for r in rows}
    need(len(rows) == len(by_spec) == len(expected), 'Missing/duplicate calibration streams')
    need(set(by_spec) == {d.stable(s) for s in expected}, 'Unexpected calibration cohort')
    return [by_spec[d.stable(s)] for s in expected]


def completion_issues(row):
    issues = []
    if any(q['finished_ns'] is None or q['peer_finished_ns'] is None for q in row['request_rows']):
        issues.append('unfinished_application_request_or_peer')
    for tenant in ('attacker', 'benign'):
        if row['incomplete_jobs'][tenant]:
            issues.append('unfinished_' + tenant + '_jobs')
        if row['requested_' + tenant + '_service_ns'] != row['executed_' + tenant + '_service_ns']:
            issues.append('incomplete_' + tenant + '_service')
    return issues


def nearest_rank_p99(responses):
    need(bool(responses), 'Empty response cohort')
    need(all(x is not None and math.isfinite(x) and x >= 0 and float(x).is_integer()
             for x in responses), 'Response must be a completed integral nanosecond duration')
    rank = (99*len(responses) + 99)//100
    return dict(n=len(responses), rank=rank, p99_ns=int(sorted(responses)[rank-1]))


def rounded_deadline_ns(p99_ns):
    need(type(p99_ns) is int and p99_ns >= 0, 'Invalid p99')
    return ((6*p99_ns + 500_000-1)//500_000)*100_000


def calibrate_deadline(records):
    rows = cohort(records, 'benign')
    per_stream = []
    failures = []
    for row in rows:
        measured = [q for q in row['request_rows'] if q['measured']]
        need(len(measured) == 256, 'Benign measured denominator differs')
        issues = completion_issues(row)
        if issues:
            failures.append(dict(spec=row['spec'], issues=issues))
        responses = [q['response_ns'] for q in measured]
        quantile = None if any(t is None for t in responses) else nearest_rank_p99(responses)
        per_stream.append(dict(spec=row['spec'], quantile=quantile,
                               offered_measured=256, completed_measured=sum(t is not None for t in responses)))
    if failures:
        return dict(status='FAILED', deadline_ns=None, per_stream=per_stream, failures=failures,
                    interpretation='Fixed-horizon benign calibration did not meet completion gates. '
                                   'No deadline or policy is frozen; all raw outcomes remain available.')
    maximum = max(x['quantile']['p99_ns'] for x in per_stream)
    return dict(status='FROZEN', deadline_ns=rounded_deadline_ns(maximum),
                maximum_per_stream_p99_ns=maximum, per_stream=per_stream, failures=[],
                rule='Nearest rank ceil(.99*n);1.2*maximum across192streams;round upward100000ns.',
                interpretation='Research deadline under the inherited bridge model; not an operational SLA.')


def joint_counts(row, deadline_ns):
    counts = {name: 0 for name in ('correct_on_time', 'wrong_on_time', 'completed_late', 'unfinished')}
    for request in row['request_rows']:
        if not request['measured']:
            continue
        if request['finished_ns'] is None:
            category = 'unfinished'
        elif request['response_ns'] > deadline_ns:
            category = 'completed_late'
        else:
            need(request['wrong_output'] in (0, 1), 'Missing completed correctness output')
            category = 'wrong_on_time' if request['wrong_output'] else 'correct_on_time'
        counts[category] += 1
    need(sum(counts.values()) == d.MEASURED[row['spec']['stage']], 'Lost offered request in classification')
    return counts


def choose_template(candidates):
    """Exact rational ranking; tie-break does not depend on file/job order."""
    eligible = [c for c in candidates if c['eligible']]
    if not eligible:
        return dict(selection_success=False, selected_learning_success=False,
                    full_evaluation_ready=False, template=None, offsets_ns=list(d.FALLBACK_OFFSETS_NS),
                    selected_arm='prespecified_even_fallback',
                    reason='No template passed the prespecified descriptive training constraints.')
    chosen = min(eligible, key=lambda c: (-Fraction(c['score_numerator'], c['score_denominator']),
                                         tuple(c['template'])))
    g, o = chosen['template']
    return dict(selection_success=True, selected_learning_success=True,
                full_evaluation_ready=False, template=chosen['template'],
                offsets_ns=[o+i*g for i in range(4)], selected_arm='frozen_selected_template',
                selected_score_numerator=chosen['score_numerator'],
                selected_score_denominator=chosen['score_denominator'],
                tie_break='Ascending(g_ns,offset_ns); exact rational comparison.',
                reason='Highest equal-weight two-regime calibration Y among eligible templates.')


def select_policy(records, deadline):
    rows = cohort(records, 'own_policy')
    if deadline['status'] != 'FROZEN':
        return dict(status='BLOCKED', selection_success=False, selected_learning_success=False,
                    full_evaluation_ready=False, template=None, offsets_ns=None,
                    candidates=[], reason='Benign-only deadline calibration failed; no scoring threshold available.')
    candidates = []
    per_stream_counts = []
    for template in d.TEMPLATES:
        regimes = []
        issues = []
        for arrival in d.ARRIVALS:
            subset = [r for r in rows if tuple(r['spec']['template']) == template and r['spec']['arrival'] == arrival]
            need(len(subset) == 32, 'Incomplete own-template calibration cell')
            total = {k: 0 for k in ('correct_on_time', 'wrong_on_time', 'completed_late', 'unfinished')}
            for row in subset:
                row_issues = completion_issues(row)
                if row_issues:
                    issues.append(dict(spec=row['spec'], issues=row_issues))
                counts = joint_counts(row, deadline['deadline_ns'])
                per_stream_counts.append(dict(spec=row['spec'], offered_measured=64, **counts))
                for k, value in counts.items():
                    total[k] += value
            offered = sum(total.values())
            need(offered == 2048, 'Own regime offered denominator differs')
            on_time = total['correct_on_time'] + total['wrong_on_time']
            service_ok = 100*on_time >= 99*offered
            if not service_ok:
                issues.append(dict(arrival=arrival, issues=['observed_on_time_fraction_below_0.99']))
            regimes.append(dict(arrival=arrival, streams=32, offered_measured=offered, **total,
                                Y=total['wrong_on_time']/offered, O=on_time/offered,
                                descriptive_service_constraint_passed=service_ok))
        score = sum((Fraction(r['wrong_on_time'], r['offered_measured']) for r in regimes), Fraction())/2
        candidates.append(dict(template=list(template), name=d.template_name(template), eligible=not issues,
            eligibility_issues=issues, score_numerator=score.numerator, score_denominator=score.denominator,
            score=float(score), regimes=regimes))
    selected = choose_template(candidates)
    return dict(status='SELECTED' if selected['selection_success'] else 'NO_ELIGIBLE_TEMPLATE', **selected,
                deadline_ns=deadline['deadline_ns'], training_scheduler='QUOTA_FCFS',
                candidates=candidates, per_stream_joint_counts=per_stream_counts,
                interpretation='Own-application calibration only. This score is selected on training data, '
                               'not an unbiased attack-effect estimate or a certified service guarantee.')


def summarize(records):
    need(len(records) == 1216, 'Expected all1216 calibration executions')
    benign = cohort(records, 'benign')
    own = cohort(records, 'own_policy')
    paired = defaultdict(set)
    requested = defaultdict(set)
    for row in records:
        spec = row['spec']
        paired[(spec['stage'], spec['arrival'], spec['stream'])].add(row['pairing_sha256'])
        if spec['stage'] == 'own_policy':
            requested[(spec['arrival'], spec['stream'])].add(row['requested_attacker_service_ns'])
    need(all(len(v) == 1 for v in paired.values()), 'Calibration exogenous pairing differs')
    need(all(len(v) == 1 for v in requested.values()), 'Candidates request unequal work')
    deadline = calibrate_deadline(records)
    policy = select_policy(records, deadline)
    status = ('CALIBRATION_INCOMPLETE' if deadline['status'] != 'FROZEN' else
              'READY_FOR_FREEZE_REVIEW' if policy['selection_success'] else 'NO_ELIGIBLE_TEMPLATE')
    return dict(status=status, full_evaluation_ready=False,
        revision=d.REVISION, executions=len(records), benign_streams=len(benign), own_policy_streams=len(own),
        benign_offered_measured=sum(r['offered_measured'] for r in benign),
        own_offered_measured=sum(r['offered_measured'] for r in own),
        all_offered_requests=sum(r['offered_all'] for r in records),
        independent_raw_checks_passed=True, pairing_checks_passed=True,
        max_attacker_period_service_ns=max(r['audit']['max_attacker_period_service_ns'] for r in records),
        max_density_error=max(r['max_density_error'] for r in records),
        incomplete_streams=[dict(spec=r['spec'], issues=completion_issues(r)) for r in records if completion_issues(r)],
        deadline=deadline, own_policy=policy,
        model_scope='Inherited bridge model only:10us host instructions, ideal gates/EPR, final_si memory. '
                    'Published-configuration/application validation is a separate unresolved task.',
        next_freeze_requirements=['Review full raw calibration ZIP and exclusions/fallback, if any.',
            'Resolve final inferential family, multiplicity and confidence procedures.',
            'Resolve cluster sample size and service-claim power before fresh evaluation seeds exist.',
            'Freeze all arms, seeds, outcome denominators and final drain rule in a separate evaluation notebook.',
            'Complete separate published-configuration reproduction and application relevance assessment.'],
        claims='Calibration only. No final victim evaluation has been run and no stronger attack claim is established.')


def text_report(summary):
    deadline, policy = summary['deadline'], summary['own_policy']
    lines = ['Q16 calibration report', 'Status: '+summary['status'], '',
             'Executions: %d; benign measured: %d; own measured: %d; all including warmup: %d.' % (
                 summary['executions'], summary['benign_offered_measured'], summary['own_offered_measured'],
                 summary['all_offered_requests']),
             'Deadline status: '+deadline['status'],
             'Deadline (ns): '+str(deadline['deadline_ns']),
             'Policy status: '+policy['status'],
             'Selected template (g_ns,offset_ns): '+str(policy.get('template')),
             'Selected/fallback offsets (ns): '+str(policy.get('offsets_ns')), '',
             'Full candidate results are in own_policy_selection.json and calibration_summary.json.',
             'Descriptive training scores and service constraints do not certify a final attack or service claim.',
             summary['model_scope'], '', 'Before final evaluation:']
    lines.extend('- '+x for x in summary['next_freeze_requirements'])
    lines.extend(['', summary['claims'], ''])
    return '\n'.join(lines)
