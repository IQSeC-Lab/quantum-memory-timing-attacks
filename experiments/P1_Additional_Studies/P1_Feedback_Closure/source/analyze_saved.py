"""Derive descriptive Q16 rows and a variance-only fresh-replication plan.

Q16 native raw replay is a separate operation (replay_q16.py). This script
requires its PASS receipt; it never converts planning data to confirmation.
"""
import argparse
import csv
import gzip
import json
from pathlib import Path
import statistics
from closure_stats import planned_radius
from closure_runner import APP_CONTRASTS,write,digest
import closure_design as d

def main():
    p=argparse.ArgumentParser();p.add_argument('--rotation',type=Path,required=True)
    args=p.parse_args();e=d.ROOT/'evidence'
    receipt=json.loads((e/'q16_replay_receipt.json').read_text())
    if receipt['status']!='PASS' or not receipt['raw_to_compact_exact'] or receipt['raw_streams_replayed']!=192:raise ValueError('Q16 replay gate')
    summary=json.loads((e/'q16_planning_summary.json').read_text());rows=summary['arms']
    assert sum(r['offered_measured'] for r in rows)==49152
    out=[]
    for r in rows:
        n=r['offered_measured']
        assert r['correct_on_time']+r['wrong_on_time']+r['completed_late']+r['unfinished']==n
        out.append(dict(arrival=r['arrival'],arm=r['arm'],offered=n,
            correct_timely_pct=100*r['correct_on_time']/n,wrong_timely_pct=100*r['wrong_on_time']/n,
            on_time_pct=100*(n-r['completed_late']-r['unfinished'])/n,
            late_pct=100*r['completed_late']/n,unfinished=r['unfinished']))
    with (e/'q16_service_rows.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
    comparisons=[]
    for arrival in ('renewal','periodic_jitter'):
        selected=next(r for r in out if r['arrival']==arrival and r['arm']=='selected')
        for arm in ('random','even'):
            comparator=next(r for r in out if r['arrival']==arrival and r['arm']==arm)
            delta_y=selected['wrong_timely_pct']-comparator['wrong_timely_pct']
            delta_o=selected['on_time_pct']-comparator['on_time_pct']
            comparisons.append(dict(arrival=arrival,comparator=arm,delta_wrong_timely_pp=delta_y,
                delta_on_time_pp=delta_o,point_service_gate=selected['on_time_pct']>=99 and delta_o>-.5))
    write(e/'q16_descriptive.json',dict(planning_only=True,confirmatory=False,rows=out,comparisons=comparisons,
        replay_receipt_sha256=digest(e/'q16_replay_receipt.json'),population_guarantee=False))
    with gzip.open(args.rotation,'rt') as f:rotation=list(csv.DictReader(f))
    lookup={(int(r['stream']),r['profile'],int(r['cost_ns']),r['variant'],r['policy'],r['arm']):r for r in rotation}
    variances=[]
    for c in APP_CONTRASTS:
        profile,cost,variant,lp,la,rp,ra,endpoint=c
        key='infidelity_lower' if endpoint=='infidelity' else 'correct_on_time'
        diffs=[float(lookup[(i,profile,cost,variant,lp,la)][key])-float(lookup[(i,profile,cost,variant,rp,ra)][key]) for i in range(32)]
        variances.append(dict(contrast=c,planning_variance=statistics.variance(diffs)))
    envelope=min(1.,4*max(r['planning_variance'] for r in variances))
    plan=dict(kind='Fixed-N precision planning using historical variances only; not power or success assurance',
        planning_streams_per_cell=32,comparison_family=6,alpha=.05,variance_multiplier=4,
        variance_envelope=envelope,N=d.APPLICATION_N,target_halfwidth_pp=2,
        predicted_halfwidth_pp=100*planned_radius(d.APPLICATION_N,envelope,6),comparisons=variances,
        historical_results_excluded_from_future_intervals=True,
        timing_N=d.PRIMARY_N,timing_family=36,
        timing_worst_case_halfwidth_pp=100*planned_radius(d.PRIMARY_N,1.,36),
        native_executions_full=dict(timing=40*d.PRIMARY_N,application=80*d.APPLICATION_N))
    write(e/'precision_plan.json',plan)
    print(json.dumps(dict(q16_rows=len(out),application_planned_halfwidth_pp=plan['predicted_halfwidth_pp'],
        native_execution='NOT_PERFORMED'),indent=2))

if __name__=='__main__':main()
