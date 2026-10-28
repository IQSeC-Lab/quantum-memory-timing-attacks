"""New additive experiments; never overwrite or continue Q12/Q13/Q16.

A primary IID trial draws one label uniformly from the 32 historically frozen
policies, then independent fresh private inputs. Inference is CONDITIONAL on
this fixed policy ensemble, not on an unseen calibration population.
"""
import hashlib,json,random
from pathlib import Path
from public_policy import ARMS
ROOT=Path(__file__).resolve().parents[1]
REVISION='p1-feedback-closure-v1'
PIN='31fa2c9699578d227736557f35cd01a134c4bbde'
PRIMARY_N=16384
APPLICATION_N=8192
SEED_OFFSET=1_000_000
ALPHA=.05
POLICIES=('QUOTA_FCFS','QUOTA_RR','QUOTA_EDF_NUMERIC','QUOTA_PROTECTED','QUOTA_NATIVE_CS')
APP_ARMS=('none','random','even','selected')
APP_CASES=(('paper_default',15,'unoptimized'),('paper_default',15,'optimized'),('short_memory',10000,'unoptimized'),('short_memory',10000,'optimized'))

def stable(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def seed(*x):return int(stable([REVISION,*x])[:16],16)
def frozen():return json.loads((ROOT/'evidence/q12_frozen_selections.json').read_text())
def public_for(workload,index,arm,mode):
    campaign=random.Random(seed('ensemble-label',workload,index)).randrange(32)
    phase=frozen()[workload+'|'+str(campaign)]['phase_ns']
    public=dict(epoch_ns=3_000_000,window_halfwidth_ns=500_000,arm=arm,
        frozen_phase_ns=phase,own_random_seed=seed('public-planner',workload,index))
    parent=dict(stage='evaluation',workload=workload,scheduler='FCFS',campaign=campaign,
        scenario='primary_0500us',arm='learned',shot=SEED_OFFSET+index,
        noise='noise_free' if mode=='smoke' else 'dephasing')
    if mode=='smoke':parent['fixed_state']=index%6
    return public,parent

def identity(family,mode):
    if family not in ('timing','application') or mode not in ('smoke','full'):raise ValueError('Unknown study')
    return dict(revision=REVISION,family=family,mode=mode,pin=PIN,
        full_N=PRIMARY_N if family=='timing' else APPLICATION_N,
        actual_N=(6 if mode=='smoke' else PRIMARY_N) if family=='timing' else (2 if mode=='smoke' else APPLICATION_N),
        independent_unit='IID fresh paired trial conditional on frozen policy ensemble' if family=='timing' else 'Whole IID fresh application stream',
        fixed_N_no_optional_stopping=True,prior_planning_excluded=True,
        comparison_family=36 if family=='timing' else 6,alpha=ALPHA,
        reference='Maurer and Pontil 2009, arXiv:0907.3740, Theorem 4',
        capability_scope='Executable simulator service-window and job-submission model; no deployed permissions or OS isolation established',
        primary_comparators=list(ARMS),application_cases=list(APP_CASES),
        native_status='PENDING_NATIVE_EXECUTION')

def jobs(family,mode):
    n=identity(family,mode)['actual_N']
    if family=='timing':
        for workload in ('plus','teleport'):
            for start in range(0,n,16):yield dict(family=family,mode=mode,workload=workload,start=start,end=min(n,start+16))
    else:
        for i in range(n):yield dict(family=family,mode=mode,index=i)
