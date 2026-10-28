"""Offline tests for E1 methods. These do not execute NetSquid simulations."""
from pathlib import Path
import os
import sys
import tempfile
from collections import defaultdict

import p1_e1 as e
sys.path.insert(0,str(Path(os.environ.get("P1_Q12_CODE_DIR",Path(__file__).parent / "q12_source"))))
assert e.PILOT_SHOTS >= 6

def fail(fn):
    try:fn()
    except (AssertionError,ValueError,RuntimeError):return
    raise AssertionError('Expected failure')


def test_design():
    assert e.counts()['pilot']==1152
    assert e.counts()['evaluation']==73728
    assert e.counts()['eval_batches']==576
    assert set(e.ARMS)=={'q12_full256','low08','low32','low64','global_loo','fixed_0ms','fixed_2ms','random_phase','no_competitor'}
    assert e.NEW_EVAL_ROOT not in {121301,121302,121303,e.NEW_PILOT_ROOT}
    for stage in ('pilot','evaluation'):
        tasks=list(e.iter_tasks(stage))
        assert len(tasks)==e.counts()[stage]
        assert sum(map(len,e.task_chunks(stage)))==len(tasks)
        assert len({(t['campaign'],t['policy'],t['shot']) for t in tasks})==len(tasks)
        assert all(t['fixed_state'] in (None,0,1,2,3,4,5) for t in tasks)


def sample_data():
    # All 256 own calibration records for 16 phases x 32 campaigns.
    obs=defaultdict(dict)
    for c in range(e.CAMPAIGNS):
        for i in range(16):
            for shot in range(256):
                obs[c,'phase_%02d'%i][shot]=int((i==((c+1)%16) and shot>=32) or (i==0 and shot<32))
    full={('teleport|'+str(c)):dict(phase_ns=((c+1)%16)*250000) for c in range(e.CAMPAIGNS)}
    return obs,full


def test_policy_freezing():
    p, full=sample_data()
    import q12_design as d
    policies=e.build_policies(p,full,d)
    assert len(policies)==32
    for c in range(32):
        v=policies[str(c)]
        assert v['q12_full256']==((c+1)%16)*250000
        assert v['low08']==0
        assert v['fixed_2ms']==2000000
        assert v['fixed_0ms']==0
        assert v['random_phase'] is None and v['no_competitor'] is None
        assert v['low32']==0
        assert v['low64'] in {0,((c+1)%16)*250000}
    wrong=dict(full);wrong['teleport|0']={'phase_ns':0}
    fail(lambda:e.build_policies(p,wrong,d))


def test_input_seed_pairing():
    import q12_design as d
    old=d.ROOTS['evaluation']
    try:
        d.ROOTS['evaluation']=e.NEW_EVAL_ROOT
        for c in (0,10,31):
            for i in (0,42,255):
                spec=d.specification('evaluation','teleport',c,'primary_0500us','learned',i)
                r=d.inputs(spec)
                for arm in ('learned','random_phase','no_competitor'):
                    s=d.specification('evaluation','teleport',c,'primary_0500us',arm,i)
                    assert d.inputs(s)==r
                assert abs(r['hidden_offset_ns'])<=500000
    finally:
        d.ROOTS['evaluation']=old


def test_immutability():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'test.json'
        e.immutable(p,dict(x=1))
        e.immutable(p,dict(x=1))
        fail(lambda:e.immutable(p,dict(x=2)))


def test_source_identity():
    d, _, original, _=e.installed_original(Path(os.environ.get('P1_Q12_CODE_DIR',Path(__file__).parent/'q12_source')))
    assert d.PIN==e.Q12_PIN
    assert all(len(v)==64 for v in original.code_hashes().values())


if __name__=='__main__':
    for name,fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('PASS',name,flush=True)
    print('E1 offline tests passed. No NetSquid executed.',flush=True)
