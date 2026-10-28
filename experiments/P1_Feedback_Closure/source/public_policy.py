"""Declared attacker information boundary; no victim/private module is imported.

This pure planner can be run as a separate JSON-in/JSON-out process. Its
interface models a published service window, not a deployed provider or an
OS sandbox. No statement of malicious-process isolation is made.
"""
import json
import random
import sys

PHASES=tuple(range(0,4_000_000,250_000))
ARMS=('none','random','even','learned')+tuple('fixed_%02d'%i for i in range(16))

def plan(public):
    allowed={'epoch_ns','window_halfwidth_ns','arm','frozen_phase_ns','own_random_seed'}
    if set(public)!=allowed:raise ValueError('Unexpected/private planner field')
    if type(public['epoch_ns']) is not int or public['epoch_ns']<500_000:raise ValueError('Epoch')
    if public['window_halfwidth_ns']!=500_000:raise ValueError('Window')
    arm=public['arm']
    if arm not in ARMS:raise ValueError('Arm')
    if type(public['own_random_seed']) is not int:raise ValueError('Own RNG seed')
    if arm=='none':offsets=[]
    elif arm=='even':offsets=[0,1_000_000,2_000_000,3_000_000]
    elif arm=='random':offsets=[random.Random(public['own_random_seed']).randrange(4_000_000)]*4
    else:
        phase=PHASES[int(arm[6:])] if arm.startswith('fixed_') else public['frozen_phase_ns']
        if phase not in PHASES:raise ValueError('Frozen phase outside declared grid')
        offsets=[phase]*4
    return [dict(release_ns=public['epoch_ns']+v,job_index=i,operations=30) for i,v in enumerate(offsets)]

if __name__=='__main__':
    print(json.dumps(plan(json.load(sys.stdin)),sort_keys=True))
