"""Recompute statistical results from the small independent-unit dataset.

This does not replace full compact-record integrity checks or raw replay.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import numpy as np
from scipy.stats import binomtest

ROOT=Path(__file__).resolve().parents[1]

def close(a,b):
    if not np.allclose(a,b,rtol=0,atol=1e-12):raise AssertionError((a,b))

def load(name):
    path=ROOT/'data/analysis'/(name+'.npz')
    return np.load(path,allow_pickle=False)

def check_manifest():
    manifest=json.loads((ROOT/'data/ANALYSIS_MANIFEST.json').read_text())
    for item in manifest['files']:
        p=ROOT/item['path']
        assert p.stat().st_size==item['bytes']
        assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']

def fresh(name):
    with load(name) as z:
        units=z['units'];ref=json.loads(str(z['reference']))
    assert ref==json.loads((ROOT/'analysis/evidence'/(name+'_analysis.json')).read_text()), 'Figure reference differs from the statistical reference'
    rows=ref['comparisons'];assert len(units)==len(rows)
    outputs=[]
    for x,old in zip(units,rows):
        n=len(x);assert n==old['n']
        span=4 if 'reference_cell' in old else 2
        m=statistics.mean(x.tolist());v=statistics.variance(x.tolist())
        log=math.log(4*len(rows)/.05)
        radius=math.sqrt(2*v*log/n)+7*span*log/(3*(n-1))
        result=dict(mean=m,variance=v,radius=radius,lower=max(-span/2,m-radius),upper=min(span/2,m+radius),n=n)
        for k,value in result.items():close(value,old[k])
        outputs.append(result)
    save(name,{'status':'PASS_INDEPENDENT_UNIT_REANALYSIS','comparisons':outputs,
               'scope':'Recomputes inference; full record/checkpoint verification is separate.'})
    return len(rows)

def bootstrap(x,seed,family,draws=20000):
    indexes=np.random.default_rng(int(seed)).integers(0,len(x),(draws,len(x)))
    samples=x[indexes].mean(axis=1);tail=.05/(2*family)
    return x.mean(),np.quantile(samples,[tail,1-tail])

def historical(name):
    with load(name) as z:
        x=z['units'];ref=json.loads(str(z['reference']))
        seed=int(z['seed']) if 'seed' in z else None
        if name=='q12':
            own=z['own_bits'];selected=json.loads(str(z['selections']))
            phases=own.sum(axis=-1).argmax(axis=-1)*250000
            for wi,w in enumerate(['plus','teleport']):
                for campaign in range(32):assert int(phases[wi,campaign])==selected[w+'|'+str(campaign)]['phase_ns']
            rows=ref['primary_contrasts']+ref['secondary_contrasts']
            for values,old in zip(x,rows):
                mean,interval=bootstrap(values,seed,old['family'])
                close(100*mean,old['mean_difference_pp']);close(100*interval,old['approximate_bootstrap95_interval_pp'])
                pos=int((values>0).sum());neg=int((values<0).sum())
                p=binomtest(pos,pos+neg,.5).pvalue if pos+neg else 1.
                close(p,old['exact_direction_p'])
        elif name=='q14':
            rows=ref['primary_contrasts']
            plots=json.loads((ROOT/'analysis/evidence/q14_expected.json').read_text())['primary_contrasts']
            for plot,old in zip(plots,rows):
                close(plot['effect_pp'],old['mean_difference_pp'])
                close(plot['interval_pp'],old['approximate_adjusted_interval_pp'])
                close(plot['sampled_error_pp'],old['sampled_wrong_output_difference_pp'])
                close(plot['sampled_error_interval_pp'],old['exploratory_sampled_error_interval_pp'])
            for values,old in zip(x,rows):
                for i,mean_key,interval_key in [(0,'mean_difference_pp','approximate_adjusted_interval_pp'),(1,'sampled_wrong_output_difference_pp','exploratory_sampled_error_interval_pp'),(2,'wrong_or_unfinished_difference_pp','exploratory_wrong_or_unfinished_interval_pp')]:
                    mean,interval=bootstrap(values[i],seed,4 if i==0 else 1)
                    close(100*mean,old[mean_key]);close(100*interval,old[interval_key])
                close(values[3].mean(),old['mean_storage_difference_ms'])
        elif name=='e1':
            rows=ref['contrasts']
            for values,old in zip(x,rows):
                mean,interval=bootstrap(values,seed,2 if old['primary'] else 1)
                close(100*mean,old['campaign_mean_difference_pp']);close(100*interval,old['interval_pp'])
            policies=json.loads(str(z['policies']))
            with load('q12') as parent: own=parent['own_bits'][1]
            for campaign in range(32):
                for budget,key in [(256,'q12_full256'),(8,'low08'),(32,'low32'),(64,'low64')]:
                    assert int(own[campaign,:,:budget].sum(axis=-1).argmax())*250000==policies[str(campaign)][key]
                pooled=own.sum(axis=(0,2))-own[campaign].sum(axis=-1)
                assert int(pooled.argmax())*250000==policies[str(campaign)]['global_loo']
        elif name=='application_earlier':
            rows=ref['primary']
            for values,old,seed in zip(x,rows,z['seeds']):
                mean,interval=bootstrap(values,seed,1,draws=10000)
                radius=math.sqrt(2*math.log(2*len(rows)/.05)/len(values))
                close(mean,old['mean_difference']);close(interval,old['exploratory_pointwise_bootstrap_95'])
                close([max(-1.,mean-radius),min(1.,mean+radius)],old['conservative_simultaneous_95'])
        elif name=='q16':
            keys=json.loads(str(z['keys']));assert x.shape==(192,4) and (x.sum(axis=1)==256).all()
            lookup={tuple(key):row for key,row in zip(keys,x)}
            for old in ref['arms']:
                group=np.asarray([lookup[old['arrival'],old['arm'],i] for i in range(32)])
                for j,key in enumerate(['correct_on_time','wrong_on_time','completed_late','unfinished']):close(group[:,j].sum(),old[key])
                close((group[:,:2].sum(axis=1)/256).mean(),old['observed_on_time_fraction'])
                close((group[:,1]/256).mean(),old['observed_wrong_on_time_fraction'])
            for old in ref['paired_nuisance']:
                a=np.asarray([lookup[old['arrival'],'selected',i] for i in range(32)])/256
                b=np.asarray([lookup[old['arrival'],old['comparator'],i] for i in range(32)])/256
                close(np.var(a[:,1]-b[:,1],ddof=1),old['centered_Y_difference_variance'])
                close(np.var(a[:,:2].sum(axis=1)-b[:,:2].sum(axis=1),ddof=1),old['centered_O_difference_variance'])
            rows=ref['arms']
    save(name,{'status':'PASS_INDEPENDENT_UNIT_REANALYSIS','comparison_groups':len(rows),
               'scope':'Campaign/stream statistical units; full request records and provenance checks remain separate.'})
    return len(rows)

def save(name,result):
    output=ROOT/'outputs/analysis';output.mkdir(parents=True,exist_ok=True)
    (output/(name+'.json')).write_text(json.dumps(result,indent=2)+'\n')
    print(name,result['status'],flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--family',choices=['all','timing','application','diagnostics','historical','q12','q14','e1','application_earlier','q16'],default='all')
    a=p.parse_args();check_manifest()
    tasks=(['timing','application','causal','robustness','q12','q14','e1','application_earlier','q16'] if a.family=='all' else
           ['causal','robustness'] if a.family=='diagnostics' else ['q12','q14','e1','application_earlier','q16'] if a.family=='historical' else [a.family])
    for task in tasks:
        if task in ['timing','application','causal','robustness']:fresh(task)
        else:historical(task)

if __name__=='__main__':main()
