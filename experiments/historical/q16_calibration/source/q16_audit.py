"""Independent raw-trace reconstruction. Does not import simulator or policy code."""
import math
from collections import Counter, defaultdict
import q16_design as d

CATEGORIES = ('correct_on_time', 'wrong_on_time', 'completed_late', 'unfinished')


def need(condition, message):
    if not condition:
        raise ValueError(message)


def close(a, b, message, tol=1e-6):
    need(a is not None and b is not None and math.isfinite(a) and math.isfinite(b) and abs(a-b)<=tol, message)


def checked_trace(trace, horizon):
    need(len({x['task_id'] for x in trace})==len(trace), 'Duplicate CPU task')
    for row in trace:
        need(0 <= row['start_ns'] <= row['end_ns'] <= horizon, 'CPU interval outside horizon')
    ordered = sorted(trace, key=lambda x:(x['start_ns'], x['end_ns'], x['task_id']))
    for first, second in zip(ordered, ordered[1:]):
        need(second['start_ns']>=first['end_ns']-1e-6, 'Overlapping CPU service')
    return {row['task_id']:row for row in trace}


def reconstruct_dispatch(raw):
    tenants = raw['tenant_by_pid']
    created = {x['task_id']:x for x in raw['created_tasks']}
    need(len(created)==len(raw['created_tasks']), 'Duplicate created task ID')
    cpu = checked_trace(raw['cpu_trace']+raw['active_cpu_trace'], raw['sim_end_ns'])
    for tid, row in cpu.items():
        need(tid in created and all(row[k]==created[tid][k] for k in ('pid','block','task_type')), 'Created/CPU task mismatch')
        need(str(row['pid']) in tenants, 'Executed task has unknown tenant')
    timeline = []
    for row in raw['quantum_events']:
        timeline.append((row['sequence'], row['end_ns'], 'quantum', row))
    for row in raw['lifetime_events']:
        timeline.append((row['sequence'], row['time_ns'], 'free', row))
    for row in raw['dispatch_decisions']:
        timeline.append((row['sequence'], row['time_ns'], 'dispatch', row))
    for row in raw['service_events']:
        timeline.append((row['sequence'], row['time_ns'], 'service', row))
    need(len({x[0] for x in timeline})==len(timeline), 'Duplicate evidence sequence')
    timeline.sort()
    queue, live, selected = [], set(), set()
    cursor, last = 0, -1
    reserved = defaultdict(int)
    starts, ends = {}, {}
    dispatch_times = {}
    deferred = 0
    for _, now, kind, row in timeline:
        need(last <= now <= raw['sim_end_ns'], 'Evidence time reversed')
        last = now
        if kind=='quantum':
            if row['kind']=='epr_ready' or row.get('name')=='init':
                need(row.get('virt') is not None, 'Missing live qubit')
                live.add((row['pid'], row['virt']))
            elif row.get('name')=='meas':
                live.discard((row['pid'], row['virt']))
            continue
        if kind=='free':
            need(row['kind']=='free', 'Unknown lifetime event')
            live.discard((row['pid'], row['virt']))
            continue
        if kind=='service':
            tid = row['task_id']
            need(tid in cpu and row['pid']==cpu[tid]['pid'] and row['tenant']==tenants[str(row['pid'])], 'Service owner mismatch')
            if row['kind']=='start':
                need(tid in selected and tid not in starts, 'Missing or duplicate dispatch/start')
                close(now, dispatch_times[tid], 'Dispatch/start time differs')
                close(now, cpu[tid]['start_ns'], 'Service start differs from backend')
                starts[tid] = now
            else:
                need(row['kind']=='end' and tid in starts and tid not in ends, 'Missing or duplicate service end')
                close(now, cpu[tid]['end_ns'], 'Service end differs from backend')
                ends[tid] = now
            continue
        ready = row['ready']
        need(ready and len(ready)==len(set(ready)), 'Invalid ready set')
        need(not selected.intersection(ready), 'Duplicate dispatch candidate')
        owners, rt = row['ready_owners'], row['ready_tenants']
        need(set(owners)==set(rt)=={str(t) for t in ready}, 'Incomplete ready ownership')
        for tid in ready:
            need(tid in created and owners[str(tid)]==created[tid]['pid'] and created[tid]['created_ns']<=now,
                 'Ready task is unknown or not created yet')
            need(rt[str(tid)]==tenants[str(owners[str(tid)])], 'Wrong tenant in decision')
        need(row['ready']==[t for t in row['roots'] if t not in row['message_blocked'] and str(t) not in row['future_starts_ns']], 'Recorded native readiness differs')
        need(all(value > now for value in row['future_starts_ns'].values()), 'Invalid future start')
        live_pids = sorted({p for p,v in live})
        need(row['live_pids']==live_pids, 'Live ownership differs from lifetime replay')
        need(row['queue_before']==queue, 'Queue order changed between decisions')
        queue = [tid for tid in queue if tid in ready]
        for tid in ready:
            if tid not in queue:
                queue.append(tid)
        period = int(now)//10_000_000
        boundary = (period+1)*10_000_000
        used = reserved[period]
        need(row['period']==period and row['reserved_before_ns']==used, 'Incorrect budget before dispatch')
        need(row['rr_cursor_before']==cursor, 'RR cursor changed between decisions')
        need(set(row['costs_ns'])=={str(t) for t in ready if rt[str(t)]=='attacker'}, 'Uncharged attacker ready task')
        blocked = {}
        for tid in queue:
            if rt[str(tid)]=='attacker':
                need(row['costs_ns'][str(tid)]==300_000, 'Attacker cost differs from complete one-block program')
                reasons = []
                if 300_000 > 1_200_000-used:
                    reasons.append('insufficient_budget')
                if now+300_000 > boundary:
                    reasons.append('period_boundary')
                if reasons:
                    blocked[str(tid)] = reasons
        eligible = [t for t in queue if str(t) not in blocked]
        need(row['blocked']==blocked and row['eligible']==eligible, 'Budget eligibility differs')
        deferred += len(blocked)
        chosen = None
        if eligible:
            if raw['scheduler_policy']=='QUOTA_RR':
                for step in range(3):
                    ix = (cursor+step)%3
                    candidates = [t for t in eligible if rt[str(t)]==('victim','benign','attacker')[ix]]
                    if candidates:
                        chosen = candidates[0]
                        cursor = (ix+1)%3
                        break
            elif raw['scheduler_policy']=='QUOTA_PROTECTED':
                live_ready = [t for t in eligible if owners[str(t)] in live_pids]
                chosen = (live_ready or eligible)[0]
            elif raw['scheduler_policy']=='QUOTA_FCFS':
                chosen = eligible[0]
            else:
                raise ValueError('Unknown policy')
            queue.remove(chosen)
            selected.add(chosen)
            dispatch_times[chosen] = now
            if rt[str(chosen)]=='attacker':
                reserved[period] += 300_000
            expected_status = ['NEXT_TASK']
            need(row['wake_ns'] is None, 'Unexpected dispatch timer')
        else:
            expected_status = ['WAITING_START_TIME']
            if row['blocked_other_core']:
                expected_status.append('WAITING_OTHER_CORE')
            if row['message_blocked']:
                expected_status.append('WAITING_MSG')
            close(row['wake_ns'], min([boundary]+list(row['future_starts_ns'].values())), 'Incorrect budget wake time')
        need(row['selected']==chosen and row['queue_after']==queue, 'Scheduler selection differs')
        need(row['rr_cursor_after']==cursor and row['reserved_after_ns']==reserved[period], 'Incorrect scheduler state after choice')
        need(row['status']==sorted(expected_status), 'Lost wake-up or unexpected scheduler state')
    need(selected==set(starts)==set(cpu), 'Dispatch, service and CPU trace do not cover the same tasks')
    need(set(ends)=={x['task_id'] for x in raw['cpu_trace']}, 'Completed service coverage differs')
    need(len(raw['active_cpu_trace'])<=1, 'More than one active CPU task')
    for row in raw['active_cpu_trace']:
        need(row['task_id'] not in ends and row['incomplete'], 'Invalid active CPU entry')
    need({str(k):v for k,v in reserved.items() if v}==raw['budget_reservations_ns'], 'Budget ledger differs')
    occupancy = defaultdict(float)
    for tid, row in cpu.items():
        if tenants[str(row['pid'])]!='attacker':
            continue
        start, end = row['start_ns'], row['end_ns']
        period = int(start)//10_000_000
        need(end <= (period+1)*10_000_000 and end-start <= 300_000, 'Actual attacker block crossed boundary or cap')
        if not row.get('incomplete', False):
            close(end-start, 300_000, 'Actual attacker cost not covered by reservation')
        # Independent interval integration, rather than summing declared payloads.
        for p in range(int(start)//10_000_000, int(end)//10_000_000+1):
            occupancy[p] += max(0.0, min(end,(p+1)*10_000_000)-max(start,p*10_000_000))
    need(all(v <= 1_200_000+1e-6 and v <= reserved[p]+1e-6 for p,v in occupancy.items()), 'Actual CPU quota exceeded')
    return dict(decisions=len(raw['dispatch_decisions']), deferred_task_observations=deferred,
                max_attacker_period_service_ns=max(occupancy.values(),default=0),
                attacker_period_service_ns={str(k):v for k,v in occupancy.items() if v},
                all_cpu_tasks=len(cpu))


def check_channel(raw, request, channel):
    pid = request['victim']['bob_pid']
    need(channel['pid']==pid and channel['state']==request['state'], 'Wrong channel owner/state')
    events = [e for e in raw['quantum_events'] if e['pid']==pid]
    if raw['spec']['workload']=='plus':
        starts = [e for e in events if e.get('routine')=='prepare_state' and e.get('name')=='h']
        stops = [e for e in events if e.get('routine')=='measure_state' and e.get('name')=='h']
    else:
        starts = [e for e in events if e['kind']=='epr_ready']
        stops = [e for e in events if e.get('routine')=='verify_state' and e.get('name','').startswith('rot_')]
    need(len(starts)==1 and len(stops)<=1, 'Missing/duplicate storage events')
    close(channel['start_ns'], starts[0]['end_ns'], 'Storage start differs')
    if request['completed']:
        need(len(stops)==1, 'Completed output missing verification event')
    if stops:
        close(channel['use_ns'], stops[0]['start_ns'], 'Storage use differs')
    need(request['admitted_ns']<=channel['start_ns']<=channel['use_ns']<=raw['sim_end_ns'], 'Invalid storage chronology')
    age = channel['use_ns']-channel['start_ns']
    close(channel['age_ns'], age, 'Storage age differs')
    factor = 1.0 if raw['spec']['profile']=='noise_free' else math.exp(-(age/13_000_000)**1.67)
    close(channel['contraction'], factor, 'Contraction differs',1e-12)
    matrices = [[[.5,.5],[.5,.5]], [[.5,-.5],[-.5,.5]],
                [[.5,-.5j],[.5j,.5]], [[.5,.5j],[-.5j,.5]],
                [[1.,0.],[0.,0.]], [[0.,0.],[0.,1.]]]
    target = matrices[0 if raw['spec']['workload']=='plus' else request['state']]
    actual = [[complex(channel['density_real'][i][j],channel['density_imag'][i][j]) for j in range(2)] for i in range(2)]
    before = [[complex(channel['before_real'][i][j],channel['before_imag'][i][j]) for j in range(2)] for i in range(2)]
    max_error = 0
    for i in range(2):
        for j in range(2):
            expected = factor*target[i][j]+((1-factor)/2 if i==j else 0)
            need(abs(before[i][j]-target[i][j])<2e-9, 'Incorrect ideal density before channel')
            error = abs(actual[i][j]-expected)
            need(error<2e-9, 'Density/channel mismatch')
            max_error = max(max_error,error)
    fidelity = sum(target[i][j]*actual[j][i] for i in range(2) for j in range(2)).real
    close(channel['fidelity'], fidelity, 'Density-derived fidelity mismatch',2e-9)
    close(channel['expected_infidelity'], 1-fidelity, 'Infidelity mismatch',2e-9)
    return max_error


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
        category = ('unfinished' if not complete else 'completed_late' if finished-request['offered_ns']>20_000_000
                    else 'wrong_on_time' if wrong else 'correct_on_time')
        need(request['category']==category,'Joint outcome category differs')
        channel = channels.get(victim['bob_pid']) if victim is not None else None
        if channel is not None:
            max_density_error = max(max_density_error, check_channel(raw,request,channel))
        if spec['profile']=='noise_free':
            need(complete and wrong==0,'Noise-free correctness control failed')
        compact_requests.append(dict(index=request['index'],measured=request['measured'],state=request['state'],
            bell_bits=bell, category=category, wrong_output=wrong, offered_ns=request['offered_ns'],
            admitted_ns=request['admitted_ns'],finished_ns=finished,
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
    if spec['stage']=='pilot':
        need(sum(counts.values())==8,'Incorrect offered measured denominator')
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


def summarize(records):
    expected = list(d.specifications('controls'))+list(d.specifications('pilot'))
    by_spec = {d.stable(r['spec']):r for r in records}
    need(len(by_spec)==len(records)==len(expected),'Missing/duplicate stream records')
    need(set(by_spec)=={d.stable(s) for s in expected},'Unexpected stream cohort')
    coverage = {}
    arm_coverage = {}
    wanted = {(state,a,b) for state in range(6) for a in (0,1) for b in (0,1)}
    controls = [r for r in records if r['spec']['stage']=='controls']
    for scheduler in d.SCHEDULERS:
        seen = set()
        for arm in d.ARMS:
            subset = [r for r in controls if r['spec']['scheduler']==scheduler and r['spec']['arm']==arm and r['spec']['workload']=='teleport']
            arm_seen = {(q['state'],*q['bell_bits']) for r in subset for q in r['request_rows'] if q['bell_bits'] is not None}
            seen |= arm_seen
            arm_coverage[scheduler+'|'+arm] = sorted(arm_seen)
        coverage[scheduler] = dict(observed=len(seen),missing=sorted(wanted-seen),passed=seen==wanted)
    main = [r for r in records if r['spec']['stage']=='pilot']
    # Validate shared exogenous inputs across arms and schedulers.
    paired = defaultdict(set)
    requested = defaultdict(set)
    for row in main:
        spec = row['spec']
        paired[(spec['workload'],spec['stream'])].add(row['pairing_sha256'])
        if spec['arm']!='none':
            requested[(spec['workload'],spec['stream'])].add(row['requested_attacker_service_ns'])
    need(all(len(v)==1 for v in paired.values()),'Private inputs are not paired')
    need(all(len(v)==1 for v in requested.values()),'Competing arms requested unequal work')
    groups = []
    for scheduler in d.SCHEDULERS:
        for workload in d.WORKLOADS:
            for arrival in ('renewal','periodic_jitter'):
                for arm in d.ARMS:
                    subset = [r for r in main if all(r['spec'][k]==v for k,v in [('scheduler',scheduler),('workload',workload),('arrival',arrival),('arm',arm)])]
                    joint = {key:sum(r['measured_joint_counts'][key] for r in subset) for key in CATEGORIES}
                    groups.append(dict(scheduler=scheduler, workload=workload, arrival=arrival,arm=arm,
                        streams=len(subset),offered_measured=sum(joint.values()),**joint,
                        requested_attacker_service_ns=sum(r['requested_attacker_service_ns'] for r in subset),
                        executed_attacker_service_ns=sum(r['executed_attacker_service_ns'] for r in subset),
                        unfinished_attacker_jobs=sum(r['incomplete_jobs']['attacker'] for r in subset),
                        unfinished_benign_jobs=sum(r['incomplete_jobs']['benign'] for r in subset)))
    equal_work = all(r['requested_attacker_service_ns']==r['executed_attacker_service_ns'] for r in records)
    branch_ok = all(v['passed'] for v in coverage.values())
    return dict(status='PASS' if branch_ok and equal_work else 'NEEDS_REVIEW',
        scope='Engineering validation only; no evidence threshold or claim test.',
        total_streams=len(records), main_streams=len(main), noise_free_streams=len(controls),
        offered_measured=sum(r['offered_measured'] for r in main),
        main_warmup=sum(r['offered_all']-r['offered_measured'] for r in main),
        separate_noise_free_requests=sum(r['offered_all'] for r in controls),
        all_stream_raw_checks_passed=True, raw_replay_branches=coverage,
        arm_level_branch_coverage=arm_coverage, equal_requested_work=True,
        all_requested_attacker_work_completed=equal_work,
        max_density_error=max(r['max_density_error'] for r in records),
        max_attacker_period_service_ns=max(r['audit']['max_attacker_period_service_ns'] for r in records),
        groups=groups, statistical_claims='None. Do not infer attack effectiveness or service guarantees from this pilot.')
