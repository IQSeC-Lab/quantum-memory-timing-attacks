"""Stream-level analysis. Never treats requests as independent replications."""
import math
import numpy as np
import app_design as d

def measures(record):
    rows=record['outcomes'];n=len(rows)
    known=[r['infidelity'] for r in rows if r['infidelity'] is not None]
    counts={k:sum(r['category']==k for r in rows) for k in ('correct_on_time','wrong_on_time','late','unfinished')}
    if sum(counts.values())!=n:raise ValueError('Outcome partition differs')
    done=[r['response_ns'] for r in record['benign'] if r['complete']]
    return dict(offered=n,**{k:v/n for k,v in counts.items()},
        infidelity_lower=sum(known)/n,infidelity_upper=(sum(known)+n-len(known))/n,
        infidelity_missing=n-len(known),benign_unfinished=sum(not r['complete'] for r in record['benign']),
        benign_p95_ns=float(np.quantile(done,.95)) if done else None,
        benign_max_ns=max(done) if done else None,
        on_time=(counts['correct_on_time']+counts['wrong_on_time'])/n)

def freeze_selection(records,mode):
    expected=list(d.discovery_specs(mode))
    if [r['spec'] for r in records]!=expected:raise ValueError('Discovery grid differs')
    scores=[]
    for phase in d.PHASES_NS:
        group=[r for r in records if r['spec']['phase_ns']==phase]
        if any(not r['all_jobs_completed'] for r in group):raise ValueError('Own calibration did not finish; no silent fallback')
        # Only the competitor's own returned verification bits drive selection.
        scores.append(dict(phase_ns=phase,errors=sum(o['wrong'] for r in group for o in r['outcomes']),
            own_executions=sum(len(r['outcomes']) for r in group)))
    best=min(scores,key=lambda r:(-r['errors'],r['phase_ns']))
    return dict(selected_phase_ns=best['phase_ns'],scores=scores,
        decision_inputs='Own sampled output bits only. No density, trace or target data.',
        discovery_records_sha256=d.stable(records),mode=mode,revision=d.REVISION)

def contrast(differences,key,family_size):
    x=np.asarray(differences,dtype=float);n=len(x)
    if n<2 or not np.isfinite(x).all():raise ValueError('Insufficient or invalid paired streams')
    r=np.random.default_rng(int(d.stable(key)[:8],16))
    # Approximate pointwise intervals are explicitly exploratory. The bounded
    # simultaneous interval is also shown even when it is uninformative.
    means=x[r.integers(0,n,size=(10000,n))].mean(axis=1)
    point=np.quantile(means,[.025,.975]).tolist()
    radius=math.sqrt(2*math.log(2*family_size/.05)/n)
    estimate=float(x.mean())
    return dict(streams=n,mean_difference=estimate,exploratory_pointwise_bootstrap_95=point,
        conservative_simultaneous_95=[max(-1.,estimate-radius),min(1.,estimate+radius)],
        bounded_interval_method='Hoeffding for independent stream differences in [-1,1]; Bonferroni over declared primary family.',
        interpretation='No powered confirmatory claim; bootstrap coverage is approximate, pointwise, and not family-controlled.')

def summarize(records,mode,phase):
    expected=list(d.evaluation_specs(mode,phase))
    if [r['spec'] for r in records]!=expected:raise ValueError('Missing, duplicate, or reordered main grid')
    groups={};pairing={}
    for r in records:
        s=r['spec'];key=(s['profile'],s['cost_ns'],s['variant'],s['policy'],s['arm'])
        groups.setdefault(key,[]).append(r)
        pairing.setdefault(s['stream'],set()).add(r['private_plan_sha256'])
    if any(len(x)!=1 for x in pairing.values()):raise ValueError('Private inputs differ across comparison cells')
    # Same public submissions and actual completed work are required across
    # policies and all three active arms within each cost/profile/variant.
    service={}
    for r in records:
        if r['spec']['arm']=='none':continue
        s=r['spec'];key=(s['profile'],s['cost_ns'],s['variant'],s['stream'])
        service.setdefault(key,[]).append(r)
    equal_work=all(len({r['executed_attacker_service_ns'] for r in g})==1 and
        all(r['all_attacker_work_completed'] for r in g) for g in service.values())
    arms=[]
    for key,group in groups.items():
        ms=[measures(r) for r in group]
        means={k:float(np.mean([m[k] for m in ms])) for k in
            ('correct_on_time','wrong_on_time','late','unfinished','on_time','infidelity_lower','infidelity_upper')}
        benign=[x['response_ns'] for r in group for x in r['benign'] if x['complete']]
        arms.append(dict(profile=key[0],cost_ns=key[1],variant=key[2],policy=key[3],arm=key[4],
            streams=len(group),offered=sum(m['offered'] for m in ms),**means,
            missing_density=sum(m['infidelity_missing'] for m in ms),
            benign_unfinished=sum(m['benign_unfinished'] for m in ms),
            benign_p95_ns=float(np.quantile(benign,.95)) if benign else None,
            max_live_owners=max(r['max_live_owners'] for r in group)))
    primary=[];secondary=[];screens=[]
    def paired(key1,key2,metric):
        a,b=groups[key1],groups[key2]
        if [r['spec']['stream'] for r in a]!=[r['spec']['stream'] for r in b]:raise ValueError('Stream pairing differs')
        return [measures(x)[metric]-measures(y)[metric] for x,y in zip(a,b)]
    family=len(d.cells(mode))
    for profile,cost,variant in d.cells(mode):
        selected=(profile,cost,variant,'QUOTA_FCFS','selected')
        random=(profile,cost,variant,'QUOTA_FCFS','random')
        x=groups[selected]+groups[random]
        if all(measures(r)['infidelity_missing']==0 for r in x):
            primary.append(dict(profile=profile,cost_ns=cost,variant=variant,
                contrast='FCFS selected minus random expected infidelity',
                **contrast(paired(selected,random,'infidelity_lower'),list(selected)+['primary'],family)))
        else:
            primary.append(dict(profile=profile,cost_ns=cost,variant=variant,status='MISSING_DENSITY_NO_COMPLETE_CASE_INFERENCE'))
        for arm in ('random','even'):
            other=(profile,cost,variant,'QUOTA_FCFS',arm)
            for metric in ('correct_on_time','wrong_on_time','on_time','late','unfinished'):
                secondary.append(dict(profile=profile,cost_ns=cost,variant=variant,
                    contrast='FCFS selected minus '+arm,metric=metric,
                    mean_difference=float(np.mean(paired(selected,other,metric)))))
        sms=[measures(r) for r in groups[selected]]
        ontime=float(np.mean([m['on_time'] for m in sms]))
        losses={a:float(-np.mean(paired(selected,(profile,cost,variant,'QUOTA_FCFS',a),'on_time'))) for a in ('random','even')}
        screens.append(dict(profile=profile,cost_ns=cost,variant=variant,on_time=ontime,losses=losses,
            passes_point_estimate_screen=ontime>=.99 and all(v<.005 for v in losses.values()),
            inference='Descriptive feasibility screen only; not a population service guarantee.'))
        for policy in d.POLICIES:
            if policy=='QUOTA_EDF_NUMERIC':continue
            a=(profile,cost,variant,policy,'selected');b=(profile,cost,variant,'QUOTA_EDF_NUMERIC','selected')
            for metric in ('correct_on_time','wrong_on_time','on_time','infidelity_lower','infidelity_upper'):
                secondary.append(dict(profile=profile,cost_ns=cost,variant=variant,
                    contrast=policy+' minus numerical EDF (selected arm)',metric=metric,
                    mean_difference=float(np.mean(paired(a,b,metric)))))
    return dict(status='APPLICATION_COMPARISON_COMPLETE',revision=d.REVISION,mode=mode,
        simulations=len(records),quantum_requests=sum(len(r['outcomes']) for r in records),
        equal_executed_attacker_work_verified=equal_work,arms=arms,primary=primary,secondary=secondary,
        service_screens=screens,physical_timing_validated=False,
        scientific_interpretation='Exploratory application evidence conditional on modeled CPU costs, source reconstruction, public epoch, admission contract and native noise. No Q16 confirmatory inference.',
        noisy_physics_audit='Native density snapshots and valid states checked; noisy gate/memory evolution is not independently resimulated.')
