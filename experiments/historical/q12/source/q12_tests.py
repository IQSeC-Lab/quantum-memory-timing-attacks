"""Meaningful offline Q12 checks. These never claim NetSquid execution."""
from pathlib import Path
import gzip
import hashlib
import inspect
import json
import math
import tempfile
import numpy as np
import q12_design as d
import q12_engine as e
import q12 as runner
from time_checks import check_payload
from p1_engine import ordered_cpu_trace


def rejects(fn):
    try:
        fn()
    except (AssertionError, RuntimeError, ValueError, TypeError):
        return
    raise AssertionError('Expected rejection did not occur')


def observations():
    return [dict(workload='plus', campaign=0, candidate=candidate, shot=i, completed=True,
                 returned_bit=int(candidate in ('phase_03','phase_05') and i < 80))
            for candidate in d.CANDIDATES for i in range(d.CAL_SHOTS)]


def test_complete_design_and_budgets():
    expected = d.counts()
    for stage in ['pilot','calibration','evaluation']:
        assert sum(1 for _ in d.specifications(stage)) == expected[stage]
        assert sum(len(x) for x in d.batches(stage)) == expected[stage]
    assert expected == dict(pilot=1296, calibration=294912, evaluation=98304, primary_evaluation=49152)
    assert d.BURST_SERVICE_NS == 1200000
    assert len(d.CANDIDATES)*d.CAL_SHOTS == 4608
    assert set(d.ROOTS.values()) == {121301,121302,121303}


def test_pairing_and_independent_calibration_draws():
    for w in d.WORKLOADS:
        for scenario in d.SCENARIOS:
            for i in [0, 17, 63]:
                rr = [d.inputs(d.specification('evaluation',w,3,scenario,arm,i)) for arm in d.ARMS]
                assert rr[0] == rr[1] == rr[2]
                assert abs(rr[0]['hidden_offset_ns']) <= d.SCENARIOS[scenario]['halfwidth_ns']
    calibration = [d.inputs(d.specification('calibration','plus',0,'own_calibration',candidate,0)) for candidate in d.CANDIDATES]
    assert len({r['seed'] for r in calibration}) == len(d.CANDIDATES)
    assert all(r['target_request_ns'] == d.EPOCH_NS and r['target_owner']=='attacker_calibration' for r in calibration)
    assert len({r['latency_ns'] for r in calibration}) > 1


def test_selection_ties_and_owner_boundary():
    obs = observations()
    result = d.select_phase(obs)
    assert result['phase_ns'] == 750000 and result['own_trials'] == 4608
    contaminated = [dict(x) for x in obs]
    contaminated[0]['victim_start_ns'] = 123
    rejects(lambda:d.select_phase(contaminated))
    rejects(lambda:d.select_phase(obs[:-1]))
    raw = dict(spec=d.specification('evaluation','plus',0,'primary_0500us','learned',0),
               inputs=dict(target_owner='victim'), completed=True, wrong_output=1)
    rejects(lambda:d.calibration_observation(raw))


def test_incomplete_calibration_is_not_dropped():
    obs = observations()
    obs[100]['completed'] = False
    obs[100]['returned_bit'] = None
    rejects(lambda:d.select_phase(obs))


def test_no_hidden_timing_in_release_policy():
    assert set(inspect.signature(d.release_plan).parameters) == {'public_epoch_ns','arm','selected_phase_ns','random_phase_ns'}
    assert d.release_plan(3000000,'learned',750000) == 3750000
    assert d.release_plan(3000000,'no_competitor') is None
    assert d.release_plan(3000000,'random_phase',None,3900000) == 6900000
    rejects(lambda:d.release_plan(3000000,'learned',123456))
    rejects(lambda:d.release_plan(3000000,'learned',750000,target_request_ns=3500000))


def test_timing_roundoff_and_actual_causality():
    j = dict(pid=4,kind='competitor',requested_ns=2473233.8426181152,
             admitted_ns=2473233.842618115,instructions=30)
    t = dict(start_ns=j['admitted_ns'],end_ns=j['admitted_ns']+300000)
    check_payload(j,t)
    rejects(lambda:check_payload(j,dict(t,start_ns=j['admitted_ns']-1e-7)))
    rejects(lambda:check_payload(j,dict(t,end_ns=t['end_ns']+1)))


def test_trace_boundary_and_true_overlap():
    rows = [dict(task_id=8,start_ns=0.,end_ns=10.), dict(task_id=4,start_ns=10.,end_ns=20.),
            dict(task_id=9,start_ns=10.,end_ns=10.)]
    assert [r['task_id'] for r in ordered_cpu_trace(rows)] == [8,9,4]
    rejects(lambda:ordered_cpu_trace([rows[0],dict(task_id=5,start_ns=5.,end_ns=6.)]))


def test_pilot_gate_and_correction_coverage():
    rows = []
    for s in d.specifications('pilot'):
        state = s.get('fixed_state',0)
        bits = [s['shot']%2,(s['shot']//2)%2] if s['workload']=='teleport' else None
        rows.append(dict(spec=s,wrong_output=0,unfinished=0,state=state,bell_bits=bits))
    assert e.validate_pilot(rows)['noise_free_controls'] == 1176
    for r in rows:
        if r['spec']['workload']=='teleport' and r['spec']['arm']=='learned' and r['state']==0:
            r['bell_bits']=[0,0]
    rejects(lambda:e.validate_pilot(rows))


def test_primary_analysis_uses_campaigns():
    rows = []
    for s in d.specifications('evaluation'):
        bit = int(s['arm']=='learned' and s['shot']%128==0)
        rows.append(dict(spec=s,wrong_output=bit,unfinished=0,application_failure=bit,state=0,
                         target_request_ns=d.EPOCH_NS,physical_storage_ns=2000000,
                         competitor_service_ns=0 if s['arm']=='no_competitor' else d.BURST_SERVICE_NS))
    result = runner.analyze(rows,{}, {})
    assert result['both_workloads_meet_prespecified_criterion']
    assert len(result['primary_contrasts'])==2 and len(result['secondary_contrasts'])==8
    for r in result['primary_contrasts']:
        assert r['independent_campaigns']==32 and r['family']==2
        assert math.isclose(r['mean_difference_pp'],100/128)
    # One unfinished learned result invalidates the integrity-success flag even
    # though the positive wrong-output contrast remains. It is not omitted.
    target = next(r for r in rows if r['spec']['workload']=='plus' and r['spec']['scenario']=='primary_0500us' and r['spec']['arm']=='learned')
    target['unfinished']=1
    result = runner.analyze(rows,{}, {})
    assert not result['both_workloads_meet_prespecified_criterion']
    assert not result['primary_contrasts'][0]['all_compared_targets_completed']


def test_immutable_metadata_and_checkpoint_hashes():
    with tempfile.TemporaryDirectory() as temp:
        p=Path(temp)
        runner.put(p/'protocol.json',dict(version=1))
        runner.put(p/'protocol.json',dict(version=1))
        rejects(lambda:runner.put(p/'protocol.json',dict(version=2)))
        spec=d.specification('evaluation','plus',0,'primary_0500us','learned',0)
        job=dict(specs=[spec])
        for name in ['raw','compact']:
            with gzip.open(p/(name+'.gz'),'wt') as f:f.write(json.dumps(dict(spec=spec))+'\n')
        runner.put(p/'stats.json',dict(trials=1,validation='Q12 full raw-record validation passed'))
        marker=dict(job_hash=d.stable(job))
        for kind,name in [('raw','raw.gz'),('compact','compact.gz'),('statistics','stats.json')]:
            marker[kind+'_file']=name;marker[kind+'_sha256']=runner.sha(p/name)
        runner.put(p/'completed.json',marker)
        assert len(runner.verified_checkpoint(p,job)[0])==1
        with (p/'raw.gz').open('ab') as f:f.write(b'changed')
        rejects(lambda:runner.verified_checkpoint(p,job))


def test_reused_sources_unchanged():
    parent=json.loads((Path(__file__).parent/'parent_sources.json').read_text())
    for name,digest in parent['sha256'].items():
        assert runner.sha(Path(__file__).parent/name)==digest


def test_runtime_is_deferred():
    # Every simulation dependency remains behind a server-only function call.
    import sys
    assert 'netsquid' not in sys.modules and 'qoala' not in sys.modules


if __name__=='__main__':
    assert __debug__, 'Run without Python -O'
    tests=[value for name,value in list(globals().items()) if name.startswith('test_')]
    for fn in tests:
        fn()
        print('PASS',fn.__name__,flush=True)
    print(f'{len(tests)} local design/analysis tests passed. NetSquid runtime NOT executed.',flush=True)
