"""Independent calibration raw replay; no simulator/policy imports."""
import math
from collections import defaultdict
from fractions import Fraction
import q16_fresh_design as d
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

