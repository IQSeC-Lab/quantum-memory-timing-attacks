"""Prespecified paired Monte Carlo analysis; no schedule selection or filtering."""
from collections import defaultdict
import numpy as np
from scipy.stats import binomtest
import q13_design as d


def contrast(values, bootstrap, family):
    values = np.asarray(values, dtype=float)
    assert values.shape==(d.REPLICATIONS,) and np.isfinite(values).all()
    samples = values[bootstrap].mean(axis=1)
    tail = .05/(2*family)
    lo,hi = np.quantile(samples,[tail,1-tail])*100
    pos,neg = int(np.sum(values>0)),int(np.sum(values<0))
    p = float(binomtest(pos,pos+neg,.5).pvalue) if pos+neg else 1.0
    return dict(independent_replications=len(values), mean_difference_pp=float(values.mean()*100),
                approximate_adjusted_interval_pp=[float(lo),float(hi)], family=family,
                direction_p=p, adjusted_direction_p=min(1.0,p*family),
                positive_replications=pos, negative_replications=neg, ties=int(np.sum(values==0)),
                interval_above_zero=bool(lo>0), interval_above_reporting_reference=bool(lo>d.PRACTICAL_REFERENCE_PP),
                reporting_reference_pp=d.PRACTICAL_REFERENCE_PP)


def loss_bounds(rows):
    known = [r['expected_infidelity'] for r in rows if not r['unfinished'] and r['expected_infidelity'] is not None]
    return [float(sum(known)/len(rows)),float((sum(known)+len(rows)-len(known))/len(rows))]


def analyze(rows):
    assert len(rows)==d.counts()['evaluation']
    expected = {d.stable(s) for s in d.specifications('evaluation')}
    assert {d.stable(r['spec']) for r in rows}==expected
    groups = defaultdict(dict)
    for r in rows:
        s=r['spec']; key=(s['workload'],s['profile'],s['replication'],s['scenario'],s['arm'])
        assert s['shot'] not in groups[key]
        groups[key][s['shot']]=r
    # Channel parameters cannot change exogenous input, policy, or premeasurement timing.
    profiles=list(d.PROFILES)
    for key, rr in groups.items():
        w,p,rep,scenario,arm=key
        other=groups[w,profiles[0],rep,scenario,arm]
        assert set(rr)==set(other)==set(range(d.SHOTS))
        for i,r in rr.items():
            q=other[i]
            assert r['pairing_sha256']==q['pairing_sha256']
            assert r['physical_storage_ns']==q['physical_storage_ns'], 'Memory profiles changed scheduling'
    bootstrap=np.random.default_rng(d.BOOTSTRAP_ROOT).integers(0,d.REPLICATIONS,(20000,d.REPLICATIONS))
    primary,secondary,summaries=[],[],[]
    for w in d.WORKLOADS:
        for profile in d.PROFILES:
            for scenario in d.SCENARIOS:
                differences,storages,errors=[],[],[]
                all_complete=True
                all_fixed,all_random=[],[]
                for rep in range(d.REPLICATIONS):
                    a=groups[w,profile,rep,scenario,'fixed_phase']
                    b=groups[w,profile,rep,scenario,'random_phase']
                    for i in range(d.SHOTS):
                        assert a[i]['pairing_sha256']==b[i]['pairing_sha256']
                        assert a[i]['competitor_service_ns']==b[i]['competitor_service_ns']==d.SERVICE_NS
                    aa,bb=list(a.values()),list(b.values())
                    all_fixed.extend(aa);all_random.extend(bb)
                    complete=all(not r['unfinished'] and r['expected_infidelity'] is not None for r in aa+bb)
                    all_complete=all_complete and complete
                    if complete:
                        differences.append(sum(a[i]['expected_infidelity']-b[i]['expected_infidelity'] for i in range(d.SHOTS))/d.SHOTS)
                        storages.append(sum(a[i]['physical_storage_ns']-b[i]['physical_storage_ns'] for i in range(d.SHOTS))/d.SHOTS/1e6)
                    errors.append(sum(a[i]['wrong_output']-b[i]['wrong_output'] for i in range(d.SHOTS))/d.SHOTS)
                family=d.PRIMARY_FAMILY if scenario=='coarse_500us' else 1
                effect=contrast(differences,bootstrap,family) if all_complete else dict(
                    independent_replications=d.REPLICATIONS,mean_difference_pp=None,
                    approximate_adjusted_interval_pp=None,family=family,
                    adjusted_direction_p=None,interval_above_zero=False,interval_above_reporting_reference=False,
                    reason='At least one compared execution is unfinished or has missing fidelity; no completed-only primary inference')
                lower_a,upper_a=loss_bounds(all_fixed);lower_b,upper_b=loss_bounds(all_random)
                effect.update(workload=w,profile=profile,scenario=scenario,
                              comparison='fixed minus equal-work random expected infidelity',
                              all_compared_targets_completed=all_complete,
                              offered_per_arm=len(all_fixed),fixed_loss_bounds=[lower_a,upper_a],
                              random_loss_bounds=[lower_b,upper_b],
                              per_offered_difference_bounds_pp=[100*(lower_a-upper_b),100*(upper_a-lower_b)],
                              mean_storage_difference_ms=float(np.mean(storages)) if all_complete else None,
                              sampled_wrong_output_difference_pp=100*float(np.mean(errors)),
                              sampled_error_note='Secondary observed verification errors per offered execution; unfinished counts reported separately',
                              inference='Approximate four-comparison simultaneous coverage' if family==d.PRIMARY_FAMILY else 'Exploratory pointwise coverage')
                (primary if scenario=='coarse_500us' else secondary).append(effect)
                for arm in d.ARMS:
                    rr=[groups[w,profile,rep,scenario,arm][i] for rep in range(d.REPLICATIONS) for i in range(d.SHOTS)]
                    finished=[r for r in rr if not r['unfinished'] and r['fidelity'] is not None]
                    summaries.append(dict(workload=w,profile=profile,scenario=scenario,arm=arm,
                        offered=len(rr),unfinished=sum(r['unfinished'] for r in rr),
                        wrong_outputs=sum(r['wrong_output'] for r in rr),
                        wrong_outputs_per_offered=sum(r['wrong_output'] for r in rr)/len(rr),
                        mean_completed_fidelity=float(np.mean([r['fidelity'] for r in finished])) if finished else None,
                        mean_completed_storage_ms=float(np.mean([r['physical_storage_ns'] for r in finished]))/1e6 if finished else None,
                        per_offered_infidelity_bounds=loss_bounds(rr),
                        competitor_service_ns=d.SERVICE_NS if arm!='no_competitor' else 0))
    return dict(version=d.VERSION,primary_contrasts=primary,secondary_contrasts=secondary,summaries=summaries,
                all_four_primary_intervals_above_zero=all(r['interval_above_zero'] and r['all_compared_targets_completed'] for r in primary),
                no_evaluation_selection=True,no_latency_filter=True,
                scope='Parameter-informed sensitivity of ideal P1 workloads, conditional on the declared epoch and placement. Not a hardware attack or whole-system calibration.',
                inference='Density-matrix fidelity is simulator ground truth. Intervals describe random scheduling/message inputs conditional on the model; they do not incorporate uncertainty in fitted hardware parameters. Sign tests concern direction, not mean effect. A positive result does not by itself establish practicality.')
