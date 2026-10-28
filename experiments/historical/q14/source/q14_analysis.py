"""Fixed paired-campaign analysis. No selection, filtering or phase retuning."""
from collections import defaultdict
import math
import numpy as np
from scipy.stats import binomtest
import q14_design as d


def mean(xs):
    xs=list(xs)
    return math.fsum(xs)/len(xs)


def bounds(rows):
    known=[r['expected_infidelity'] for r in rows if not r['unfinished'] and r['expected_infidelity'] is not None]
    lower=math.fsum(known)/len(rows)
    return [lower,lower+(len(rows)-len(known))/len(rows)]


def interval(values,draws,family):
    samples=np.asarray(values)[draws].mean(axis=1)
    return [float(x*100) for x in np.quantile(samples,[.025/family,1-.025/family])]


def analyze(rows,frozen,inherited_costs):
    d.validate_policies(frozen)
    assert len(rows)==d.counts()['evaluation']
    assert {d.stable(r['spec']) for r in rows}=={d.stable(s) for s in d.specifications('evaluation')}
    groups=defaultdict(dict)
    for r in rows:
        s=r['spec'];key=(s['workload'],s['profile'],s['campaign'],s['arm'])
        assert s['shot'] not in groups[key]
        assert r['policy_sha256']==d.stable(frozen) and r['selected_phase_ns']==d.phase_for(s,frozen)
        assert r['competitor_service_ns']==(0 if s['arm']=='no_competitor' else d.SERVICE_NS)
        assert r['wrong_output'] in (0,1) and r['unfinished'] in (0,1)
        groups[key][s['shot']]=r
    first=next(iter(d.PROFILES))
    for (w,p,c,arm),rr in groups.items():
        assert set(rr)==set(range(d.SHOTS))
        for i,r in rr.items():
            for other in d.ARMS:assert r['pairing_sha256']==groups[w,p,c,other][i]['pairing_sha256']
            ref=groups[w,first,c,arm][i]
            assert r['pairing_sha256']==ref['pairing_sha256']
            assert r['physical_storage_ns']==ref['physical_storage_ns'],'Memory profile changed timing'
    draws=np.random.default_rng(d.BOOTSTRAP_ROOT).integers(0,d.CAMPAIGNS,(20000,d.CAMPAIGNS))
    primary=[];summaries=[];campaign_values=[]
    for w in d.WORKLOADS:
        for p in d.PROFILES:
            data={a:[groups[w,p,c,a][i] for c in range(d.CAMPAIGNS) for i in range(d.SHOTS)] for a in d.ARMS}
            differences=[];errors=[];failures=[];storage=[];responses=[]
            all_complete=all(not r['unfinished'] and r['expected_infidelity'] is not None for a in ('learned','random_phase') for r in data[a])
            for campaign in range(d.CAMPAIGNS):
                a=groups[w,p,campaign,'learned'];b=groups[w,p,campaign,'random_phase']
                complete=all(not r['unfinished'] and r['expected_infidelity'] is not None for r in list(a.values())+list(b.values()))
                delta=mean(a[i]['expected_infidelity']-b[i]['expected_infidelity'] for i in range(d.SHOTS)) if complete else None
                gap=mean(a[i]['physical_storage_ns']-b[i]['physical_storage_ns'] for i in range(d.SHOTS))/1e6 if complete else None
                response=mean((a[i]['target_finished_ns']-a[i]['target_request_ns'])-(b[i]['target_finished_ns']-b[i]['target_request_ns']) for i in range(d.SHOTS))/1e6 if complete else None
                err=mean(a[i]['wrong_output']-b[i]['wrong_output'] for i in range(d.SHOTS))
                fail=mean(max(a[i]['wrong_output'],a[i]['unfinished'])-max(b[i]['wrong_output'],b[i]['unfinished']) for i in range(d.SHOTS))
                if complete:differences.append(delta);storage.append(gap);responses.append(response)
                errors.append(err);failures.append(fail)
                campaign_values.append(dict(workload=w,profile=p,campaign=campaign,phase_ns=frozen[w+'|'+str(campaign)]['phase_ns'],
                    delta_infidelity=delta,delta_wrong_output=err,delta_wrong_or_unfinished=fail,delta_storage_ms=gap,delta_response_ms=response))
            ci=interval(differences,draws,4) if all_complete else None
            pos=sum(x>0 for x in differences);neg=sum(x<0 for x in differences)
            pval=float(binomtest(pos,pos+neg,.5).pvalue) if all_complete and pos+neg else (1.0 if all_complete else None)
            a_bounds,b_bounds=bounds(data['learned']),bounds(data['random_phase'])
            effect=dict(workload=w,profile=p,scenario='coarse_500us',comparison='learned minus equal-work random expected infidelity',
                independent_campaigns=d.CAMPAIGNS,offered_per_arm=len(data['learned']),all_compared_targets_completed=all_complete,
                mean_difference_pp=100*mean(differences) if all_complete else None,
                approximate_adjusted_interval_pp=ci,family=4,
                direction_p=pval,adjusted_direction_p=min(1.,4*pval) if pval is not None else None,
                positive_campaigns=pos if all_complete else None,negative_campaigns=neg if all_complete else None,
                tied_campaigns=d.CAMPAIGNS-pos-neg if all_complete else None,
                interval_above_zero=bool(all_complete and ci[0]>0),
                reporting_reference_pp=.5,interval_above_reporting_reference=bool(all_complete and ci[0]>.5),
                learned_loss_bounds=a_bounds,random_loss_bounds=b_bounds,
                per_offered_difference_bounds_pp=[100*(a_bounds[0]-b_bounds[1]),100*(a_bounds[1]-b_bounds[0])],
                mean_storage_difference_ms=mean(storage) if all_complete else None,
                mean_response_difference_ms=mean(responses) if all_complete else None,
                sampled_wrong_output_difference_pp=100*mean(errors),
                exploratory_sampled_error_interval_pp=interval(errors,draws,1),
                wrong_or_unfinished_difference_pp=100*mean(failures),
                exploratory_wrong_or_unfinished_interval_pp=interval(failures,draws,1),
                inference='Four-comparison approximate simultaneous primary intervals; observed-error intervals are exploratory pointwise.',
                reason=None if all_complete else 'Missing fidelity or unfinished compared output: primary fidelity-effect inference suppressed.')
            primary.append(effect)
            for arm,rr in data.items():
                finished=[r for r in rr if not r['unfinished'] and r['fidelity'] is not None]
                summaries.append(dict(workload=w,profile=p,arm=arm,offered=len(rr),unfinished=sum(r['unfinished'] for r in rr),
                    missing_fidelity=sum(r['fidelity'] is None for r in rr),wrong_outputs=sum(r['wrong_output'] for r in rr),
                    wrong_or_unfinished=sum(max(r['wrong_output'],r['unfinished']) for r in rr),
                    mean_completed_fidelity=mean(r['fidelity'] for r in finished) if finished else None,
                    per_offered_infidelity_bounds=bounds(rr),
                    mean_completed_storage_ms=mean(r['physical_storage_ns'] for r in finished)/1e6 if finished else None,
                    mean_completed_response_ms=mean(r['target_finished_ns']-r['target_request_ns'] for r in finished)/1e6 if finished else None,
                    competitor_service_ns=d.SERVICE_NS if arm!='no_competitor' else 0))
    support={w:all(r['interval_above_zero'] for r in primary if r['workload']==w) for w in d.WORKLOADS}
    return dict(version=d.VERSION,primary_contrasts=primary,summaries=summaries,campaign_differences=campaign_values,
        per_workload_transfer_support=support,all_four_primary_intervals_above_zero=all(support.values()),
        frozen_policies_sha256=d.stable(frozen),new_calibration_executions=0,inherited_calibration_costs=inherited_costs,
        calibration_cost_note='Costs already incurred in Q12; reused here, not zero-cost learning. Policy library used under both profiles without extra retraining.',
        scope='Unchanged learned-policy transfer represented by all 64 source policies, under the fixed model, shared placement and supplied epoch. Campaign-bootstrap inference treats independent source calibration campaigns as replications of the original calibration procedure; it is not an interval for a single fixed policy. No deployment or physical-parameter uncertainty claim.',
        no_evaluation_selection=True,no_latency_filter=True)
