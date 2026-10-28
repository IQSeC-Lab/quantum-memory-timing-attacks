"""Reconstruct scheduler eligibility, dispatch, quotas and task execution.

Independent of app_policy and all simulator imports. Readiness uses recorded
message availability and dependency graphs; it is not an independent network
simulation. This generalizes the already audited RQ3 conformance checker.
"""
import app_design as d

def need(condition, message):
    if not condition:
        raise ValueError(message)


def ready_from_graph(graph, active, now):
    roots = [r for r in graph if not r['internal_precedences'] and not r['external_precedences']]
    external = [r for r in graph if not r['internal_precedences'] and r['external_precedences']]
    if active is not None:
        same = lambda r:r['pid']==active['pid'] and r['critical_section']==active['cs_id']
        roots = [r for r in roots if same(r)]
        external = [r for r in external if same(r)]
    blocked = [r for r in roots if r['is_event'] and not r['message_available']]
    future = [r for r in roots if r['start_ns'] is not None and r['start_ns']>now]
    excluded = {r['task_id'] for r in blocked+future}
    return [r['task_id'] for r in roots if r['task_id'] not in excluded],external,blocked,future




def validate_scheduler(raw,owners,costs_by_block):
    spec=raw['spec']
    all_events = []
    for name in ('decisions','status_events','service_events','quantum_events',
                 'host_instructions','created_tasks','admissions','density_snapshots'):
        all_events.extend(raw[name])
    sequences = [r['sequence'] for r in all_events]
    need(len(sequences)==len(set(sequences)) and set(sequences)==set(range(len(sequences))), 'Event coverage/sequence differs')
    created = {r['task_id']:r for r in raw['created_tasks']}
    need(len(created)==len(raw['created_tasks']), 'Duplicate task creation')
    cpu_types = {'HostLocalTask','HostEventTask','PreCallTask','PostCallTask'}
    for frame in raw['decisions']+raw['status_events']:
        seq = frame['sequence']
        pending = {t for t,c in created.items() if c['sequence']<seq and c['task_type'] in cpu_types}
        pending -= {r['task_id'] for r in raw['service_events'] if r['kind']=='end' and r['sequence']<seq}
        need({r['task_id'] for r in frame['graph']}==pending and len(frame['graph'])==len(pending), 'CPU graph coverage differs')
        for task in frame['graph']:
            need(all(task[k]==created[task['task_id']][k] for k in ('pid','block','task_type','critical_section')), 'CPU graph task identity differs')
    live_at = {}
    live = set()
    max_live = 0
    for kind,row in sorted([('q',r) for r in raw['quantum_events']]+[('d',r) for r in raw['decisions']],key=lambda x:x[1]['sequence']):
        if kind=='q':
            pair = (row['pid'],row['virt'])
            if row['kind']=='free' or row.get('name')=='meas':
                live.discard(pair)
            elif row.get('name')=='init':
                live.add(pair)
            max_live = max(max_live,len({p for p,v in live}))
        else:
            live_at[row['sequence']] = sorted({p for p,v in live})
    need(sorted([list(x) for x in live])==raw['final_live'],'Final live-state replay differs')
    queue, cursor, reservations, dispatched = [],0,{},set()
    changed_fifo = 0
    for row in raw['decisions']:
        now = row['time_ns']
        need(type(now) is int and now>=0,'Invalid dispatch time')
        ready,external,blocked_message,future = ready_from_graph(row['graph'],row['active_cs'],now)
        need(row['ready']==ready and ready,'Native ready-set mismatch')
        need(row['live_pids']==live_at[row['sequence']], 'Runtime ownership/lifetime mismatch')
        graph = {r['task_id']:r for r in row['graph']}
        for tid in ready:
            pid = graph[tid]['pid']
            need(row['ready_owners'][str(tid)]==pid and row['ready_tenants'][str(tid)]==owners[pid]['tenant'],'Ownership mismatch')
        need(not dispatched.intersection(ready), 'Dispatched task reappeared')
        need(row['queue_before']==queue and row['rr_cursor_before']==cursor,'Policy history differs')
        queue = [t for t in queue if t in ready]
        queue += [t for t in ready if t not in queue]
        period = now//d.PERIOD_NS
        used = reservations.get(period,0)
        costs, deadlines, blocked = {},{},{}
        for tid in ready:
            pid = graph[tid]['pid']
            deadlines[str(tid)] = owners[pid]['absolute_deadline_ns']
            if owners[pid]['tenant']=='attacker':
                c = costs_by_block[(pid,graph[tid]['block'])]
                need(0<c<=d.BLOCK_CAP_NS, 'Oversized competing block')
                costs[str(tid)] = c
        for tid in queue:
            if str(tid) in costs:
                c = costs[str(tid)]
                reasons = []
                if used+c>d.BUDGET_NS:reasons.append('insufficient_budget')
                if now+c>(period+1)*d.PERIOD_NS:reasons.append('period_boundary')
                if reasons:blocked[str(tid)] = reasons
        eligible = [t for t in queue if str(t) not in blocked]
        chosen = None
        if eligible:
            if spec['policy']=='QUOTA_RR':
                for offset in range(len(d.TENANTS)):
                    i = (cursor+offset)%len(d.TENANTS)
                    candidate = next((t for t in eligible if owners[graph[t]['pid']]['tenant']==d.TENANTS[i]),None)
                    if candidate is not None:
                        chosen, cursor = candidate,(i+1)%len(d.TENANTS)
                        break
            elif spec['policy']=='QUOTA_EDF_NUMERIC':
                chosen = sorted(eligible,key=lambda t:deadlines[str(t)])[0]
            elif spec['policy']=='QUOTA_PROTECTED':
                chosen = next((t for t in eligible if graph[t]['pid'] in row['live_pids']),eligible[0])
            else:
                chosen = eligible[0]
            changed_fifo += chosen!=eligible[0]
            queue.remove(chosen)
            dispatched.add(chosen)
            if str(chosen) in costs:reservations[period]=used+costs[str(chosen)]
        need(row['selected']==chosen and row['eligible']==eligible and row['blocked']==blocked,'Dispatch/eligibility differs')
        need(row['queue_after']==queue and row['rr_cursor_after']==cursor,'Policy update differs')
        need(row['period']==period and row['reserved_before_ns']==used and row['reserved_after_ns']==reservations.get(period,0),'Budget ledger differs')
        need(row['costs_ns']==costs and row['absolute_deadlines_ns']==deadlines, 'Cost or deadline proof differs')
        numeric = {t:deadline-now for t,deadline in deadlines.items()} if spec['policy']=='QUOTA_EDF_NUMERIC' and chosen is not None else {}
        need(row['native_remaining_deadlines_ns']==numeric,'Native EDF numeric deadlines differ')
        if chosen is not None:
            need(row['statuses']==['NEXT_TASK'] and row['wake_ns'] is None,'Dispatch status differs')
        else:
            status = ['WAITING_START_TIME']
            if external:status.append('WAITING_OTHER_CORE')
            if blocked_message:status.append('WAITING_MSG')
            need(row['statuses']==sorted(status),'Quota wait lost a wake source')
            need(row['wake_ns']==min([(period+1)*d.PERIOD_NS]+[r['start_ns'] for r in future]), 'Quota wake differs')

    # Check every native update, including deliberate CS stalls and empty graphs.
    seen_decisions = set()
    cs_stalls = 0
    active_cs = None
    timeline = [('status',r) for r in raw['status_events']]+[('service',r) for r in raw['service_events'] if r['kind']=='start']
    for kind,row in sorted(timeline,key=lambda x:x[1]['sequence']):
        if kind=='service':
            task = created[row['task_id']]
            if task['critical_section'] is not None:
                active_cs = dict(pid=task['pid'],cs_id=task['critical_section'])
            continue
        need(row['active_before']==active_cs, 'Critical-section owner history differs')
        graph = row['graph']
        if active_cs is not None and not any(r['pid']==active_cs['pid'] and r['critical_section']==active_cs['cs_id'] for r in graph):
            active_cs = None
        need(row['active_after']==active_cs, 'Critical-section release differs')
        ready,external,messages,future = ready_from_graph(graph,active_cs,row['time_ns'])
        i = row['decision_index']
        if ready:
            need(type(i) is int and 0<=i<len(raw['decisions']) and i not in seen_decisions,'Decision missing or duplicate')
            seen_decisions.add(i)
            decision = raw['decisions'][i]
            need(decision['graph']==graph and decision['active_cs']==active_cs and decision['time_ns']==row['time_ns'],'Status/dispatch snapshot differs')
            need(decision['statuses']==row['statuses'],'Native status differs from decision')
            expected_params = {'task_id':decision['selected']} if decision['selected'] is not None else {'start_time':decision['wake_ns']}
            need(row['params']==expected_params,'Native task or wake differs')
        else:
            need(i is None,'Decision with empty native ready set')
            status = []
            params = {}
            if not graph:status=['GRAPH_EMPTY']
            else:
                if external:status.append('WAITING_OTHER_CORE')
                if messages:status.append('WAITING_MSG')
                if future:
                    status.append('WAITING_START_TIME');params={'start_time':min(r['start_ns'] for r in future)}
            need(row['statuses']==sorted(status) and row['params']==params,'Native waiting or readiness differs')
            unfiltered,_,_,_ = ready_from_graph(graph,None,row['time_ns'])
            cs_stalls += bool(active_cs and unfiltered)
    need(seen_decisions==set(range(len(raw['decisions']))),'Unpaired dispatch decision')
    starts = [r for r in raw['service_events'] if r['kind']=='start']
    ends = [r for r in raw['service_events'] if r['kind']=='end']
    selected = [r for r in raw['decisions'] if r['selected'] is not None]
    need([r['task_id'] for r in starts]==[r['selected'] for r in selected], 'Service did not follow dispatch')
    need(len(ends)==len(raw['cpu_trace']) and len(ends)<=len(starts)<=len(ends)+1, 'CPU trace coverage differs')
    trace = {r['task_id']:r for r in raw['cpu_trace']}
    need(len(trace)==len(ends),'Duplicate CPU trace task')
    for start,end in zip(starts,ends):
        need(start['task_id']==end['task_id'],'Nonpreemptive service pairing differs')
        task = trace[start['task_id']]
        need((task['pid'],task['block'],task['start_ns'],task['end_ns'])==(start['pid'],start['block'],start['time_ns'],end['time_ns']),'CPU service timestamps differ')
        need(task['start_ns']>=owners[task['pid']]['release_ns'] and task['end_ns']>=task['start_ns'],'Invalid service time')
        if owners[task['pid']]['kind']=='classical':
            need(task['end_ns']-task['start_ns']==costs_by_block[(task['pid'],task['block'])], 'Actual classical cost differs')
    ordered = sorted(raw['cpu_trace'],key=lambda r:(r['start_ns'],r['end_ns'],r['task_id']))
    need(all(b['start_ns']>=a['end_ns'] for a,b in zip(ordered,ordered[1:])), 'CPU overlap/preemption')
    qpu = {r['task_id']:r for r in raw['qpu_trace']}
    need(not set(qpu).intersection(trace) and set(trace)|set(qpu)<=set(created), 'Created/executed task coverage differs')
    remaining={r['task_id'] for r in raw['remaining_cpu_tasks']}|set(raw['remaining_qpu_tasks'])
    need(set(created)==set(trace)|set(qpu)|remaining,'Unaccounted created tasks')
    for tid,t in {**trace,**qpu}.items():
        c = created[tid]
        need(all(c[k]==t[k] for k in ('pid','block','task_type','critical_section')),'Task identity differs')
        need(c['native_creation_deadline'] is None, 'Unexpected priority-zero annotation')
    return ordered,trace,qpu,max_live,cs_stalls,reservations,selected
