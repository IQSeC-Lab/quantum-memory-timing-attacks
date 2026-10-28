"""Descriptive joint outcomes and nuisance quantities; no inferential attack claim."""
import math, statistics
from collections import defaultdict
import q16_fresh_design as d

def joint(row):
    counts=dict(correct_on_time=0,wrong_on_time=0,completed_late=0,unfinished=0)
    for q in row['request_rows']:
        if not q['measured']: continue
        key=('unfinished' if q['finished_ns'] is None else 'completed_late' if q['response_ns']>d.DEADLINE_NS else
             'wrong_on_time' if q['wrong_output'] else 'correct_on_time')
        counts[key]+=1
    if sum(counts.values())!=256: raise ValueError('Lost offered measured request')
    return dict(**counts,Y=counts['wrong_on_time']/256,O=(counts['correct_on_time']+counts['wrong_on_time'])/256)

def summarize(records):
    expected=list(d.specifications());by={d.stable(r['spec']):r for r in records}
    if len(records)!=192 or len(by)!=192 or set(by)!={d.stable(s) for s in expected}:
        raise ValueError('Missing, extra or duplicated planning stream')
    records=[by[d.stable(s)] for s in expected];groups=defaultdict(list);pairing=defaultdict(set);work=defaultdict(set)
    for r in records:
        s=r['spec'];groups[s['arrival'],s['arm']].append((r,joint(r)))
        pairing[s['arrival'],s['stream']].add(r['pairing_sha256'])
        work[s['arrival'],s['stream']].add(r['requested_attacker_service_ns'])
    if any(len(v)!=1 for v in pairing.values()) or any(len(v)!=1 for v in work.values()):
        raise ValueError('Input pairing or requested work differs')
    arms=[];nuisance=[]
    for (arrival,arm),rows in groups.items():
        on_time=[c['O'] for _,c in rows];wrong_on_time=[c['Y'] for _,c in rows]
        total={k:sum(c[k] for _,c in rows) for k in ('correct_on_time','wrong_on_time','completed_late','unfinished')}
        arms.append(dict(arrival=arrival,arm=arm,streams=32,offered_measured=8192,**total,
            observed_on_time_fraction=statistics.mean(on_time),observed_wrong_on_time_fraction=statistics.mean(wrong_on_time),
            stream_on_time_variance=statistics.variance(on_time),stream_wrong_on_time_variance=statistics.variance(wrong_on_time),
            incomplete_application_or_peer=sum(any(q['finished_ns'] is None or q['peer_finished_ns'] is None for q in r['request_rows']) for r,_ in rows),
            incomplete_competitor_jobs=sum(r['incomplete_jobs']['attacker'] for r,_ in rows),
            incomplete_background_jobs=sum(r['incomplete_jobs']['benign'] for r,_ in rows),
            requested_attacker_service_ns=sum(r['requested_attacker_service_ns'] for r,_ in rows),
            executed_attacker_service_ns=sum(r['executed_attacker_service_ns'] for r,_ in rows)))
    for arrival in d.ARRIVALS:
        maps={arm:{r['spec']['stream']:c for r,c in groups[arrival,arm]} for arm in d.ARMS}
        for comparator in ('random','even'):
            # Centered paired variations, not an observed effect used to choose N.
            dy=[maps['selected'][i]['Y']-maps[comparator][i]['Y'] for i in range(32)]
            do=[maps['selected'][i]['O']-maps[comparator][i]['O'] for i in range(32)]
            nuisance.append(dict(arrival=arrival,comparator=comparator,independent_pairs=32,
                centered_Y_difference_variance=statistics.variance(dy),centered_O_difference_variance=statistics.variance(do),
                method='Unbiased sample variance across paired streams. Uncertain at n=32; use sensitivity envelope before final freeze.'))
    incomplete=any(a['incomplete_application_or_peer'] or a['incomplete_competitor_jobs'] or a['incomplete_background_jobs'] for a in arms)
    equal_executed=all(a['requested_attacker_service_ns']==a['executed_attacker_service_ns'] for a in arms)
    return dict(status='PLANNING_COMPLETE' if not incomplete and equal_executed else 'PLANNING_COMPLETE_WITH_SERVICE_LIMITS',
        confirmatory_evaluation=False,executions=192,independent_paired_input_streams=64,all_offered_requests=sum(r['offered_all'] for r in records),
        measured_requests=sum(r['offered_measured'] for r in records),deadline_ns=d.DEADLINE_NS,
        selected_offsets_ns=list(d.SELECTED_OFFSETS_NS),arms=arms,paired_nuisance=nuisance,
        equal_requested_work=True,equal_executed_work=equal_executed,
        max_density_error=max(r['max_density_error'] for r in records),
        max_attacker_period_service_ns=max(r['audit']['max_attacker_period_service_ns'] for r in records),
        next='Use nuisance/service/cost evidence and sensitivity assumptions to freeze final N and confidence method; retain selected policy/deadline.',
        limits=['These 192 simulations are excluded from future confirmatory evaluation.',
                'No post-result policy or deadline optimization.',
                'No hardware CPU calibration or deployment authority established.',
                '32 paired streams per regime do not certify a 99% population service guarantee.'])

def text_report(s):
    lines=['Q16 fresh planning report',s['status'],'Planning only; no confirmatory attack result.',
        'Executions: 192; independent paired input streams: 64; measured requests: 49152.',
        'Deadline: 5 ms; selected job offsets: [0.25,0.25,0.25,0.25] ms.','',
        'Arrival | arm | observed on-time fraction | wrong-and-on-time fraction | late | unfinished']
    for a in s['arms']:
        lines.append('%s | %s | %.8f | %.8f | %d | %d'%(a['arrival'],a['arm'],a['observed_on_time_fraction'],
            a['observed_wrong_on_time_fraction'],a['completed_late'],a['unfinished']))
    lines+=['',s['next'],'']+s['limits']
    return '\n'.join(lines)+'\n'
