"""Simulator-independent checks of the new runtime-reduction timing adapter."""
from public_policy import plan

def primary(raw):
    public=raw['public'];parent=raw['parent_spec'];out=raw['outcomes'];pid=raw['target']['bob_pid']
    if raw['public_plan']!=plan(public):raise ValueError('Public plan mismatch')
    if abs(raw['target_admitted_ns']-raw['private_inputs']['target_request_ns'])>1e-6:raise ValueError('Target admission')
    if raw['sim_end_ns']!=30_000_000:raise ValueError('Horizon')
    trace=raw['cpu_trace']
    if any(t['end_ns']<t['start_ns'] for t in trace):raise ValueError('Negative duration')
    jobs=raw['jobs'];offers=raw['public_plan']
    if len(jobs)!=len(offers):raise ValueError('Missing competing job')
    service=0
    for job,offer in zip(jobs,offers):
        if job['requested_ns']!=offer['release_ns'] or abs(job['admitted_ns']-offer['release_ns'])>1e-6:raise ValueError('Attacker admission')
        parts=[t for t in trace if t['pid']==job['pid']]
        if len(parts)!=1 or parts[0]['block']!='payload':raise ValueError('Unexpected classical execution')
        t=parts[0]
        if t['start_ns']<job['admitted_ns'] or abs(t['end_ns']-t['start_ns']-300_000)>1e-5:raise ValueError('Unequal service')
        service+=t['end_ns']-t['start_ns']
        if raw['job_results'][str(job['pid'])].get('total')!=sum(raw['job_inputs'][str(job['pid'])].values()):raise ValueError('Runtime reduction output')
        if any(t['pid']==job['pid'] for t in raw['qpu_trace']):raise ValueError('Classical competitor used QPU')
    if abs(service-(0 if public['arm']=='none' else 1_200_000))>1e-5:raise ValueError('Service sum')
    age=None
    if out['completed']:
        if raw['target_finished_ns'] is None or raw['target_finished_ns']<raw['target_admitted_ns']:raise ValueError('Target completion')
        events=[e for e in raw['quantum_events'] if e['pid']==pid]
        if parent['workload']=='plus':
            starts=[e for e in events if e.get('routine')=='prepare_state' and e.get('name')=='h']
            uses=[e for e in events if e.get('routine')=='measure_state' and e.get('name')=='h']
        else:
            starts=[e for e in events if e.get('kind')=='epr_ready']
            uses=[e for e in events if e.get('routine')=='verify_state' and e.get('name','').startswith('rot_')]
        if len(starts)!=1 or len(uses)!=1:raise ValueError('Storage boundary')
        age=uses[0]['start_ns']-starts[0]['end_ns']
        if age<0:raise ValueError('Negative age')
    if parent['noise']=='noise_free' and (not out['completed'] or out['wrong_output']):raise ValueError('Noise-free functional failure')
    return dict(arm=public['arm'],completed=bool(out['completed']),wrong_output=int(out['wrong_output']) if out['completed'] else None,
        storage_ns=age,service_ns=service,state=raw['private_inputs']['state'],seed=raw['private_inputs']['seed'],
        target_request_ns=raw['private_inputs']['target_request_ns'],campaign=parent['campaign'])
