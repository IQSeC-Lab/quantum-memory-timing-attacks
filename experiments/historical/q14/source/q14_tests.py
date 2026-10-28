"""Offline Q14 design, analysis, raw-validation and checkpoint tests.

All test data are synthetic temporary fixtures, not NetSquid observations.
"""
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import numpy as np
import q14_design as d
import q14_engine as engine
import q14_analysis as analysis
import q14_prerequisites as prereq
import q14 as runner
from q13_raw_checks import AuditError,sha_file
from synthetic_records import fixture


def policies():
    return {w+'|'+str(c):dict(phase_ns=d.PHASES_NS[(c+(0 if w=='plus' else 1))%16],source_observations_sha256='0'*64)
            for w in d.WORKLOADS for c in range(d.CAMPAIGNS)}


def rejects(fn,fragment=None):
    try:fn()
    except (AssertionError,ValueError,RuntimeError) as exc:
        if fragment is not None:assert fragment in str(exc),(fragment,str(exc))
    else:raise AssertionError('Expected a rejection')


def test_complete_grid_and_separate_roots():
    observed={stage:sum(1 for _ in d.specifications(stage)) for stage in ('pilot','evaluation')}
    assert observed==d.counts()=={'pilot':1944,'evaluation':98304}
    for stage in observed:
        grid=list(d.specifications(stage))
        assert len({d.stable(s) for s in grid})==len(grid)
        assert sum(map(len,d.batches(stage)))==len(grid)
    assert set(d.ROOTS.values()).isdisjoint({121301,121302,121303,131301,131302,131303})
    assert len({(s['workload'],s['campaign']) for s in d.specifications('pilot') if s['arm']=='learned'})==64


def test_all_policy_retention_and_information_boundary():
    p=policies();d.validate_policies(p)
    broken=deepcopy(p);broken.pop('plus|31');rejects(lambda:d.validate_policies(broken))
    broken=deepcopy(p);broken['plus|0']['phase_ns']=1234;rejects(lambda:d.validate_policies(broken))
    assert list(inspect.signature(d.release_plan).parameters)==['public_epoch_ns','arm','selected_phase_ns','random_phase_ns']
    for w in d.WORKLOADS:
        for c in range(d.CAMPAIGNS):
            s=d.spec('evaluation',w,'final_si',c,'learned',0)
            assert d.phase_for(s,p)==p[w+'|'+str(c)]['phase_ns']
            assert d.release_plan(d.EPOCH_NS,'learned',d.phase_for(s,p))==d.EPOCH_NS+p[w+'|'+str(c)]['phase_ns']


def test_paired_inputs_and_fixed_work():
    p=policies()
    for w in d.WORKLOADS:
        for campaign in (0,31):
            for shot in (0,255):
                records=[d.inputs(d.spec('evaluation',w,profile,campaign,arm,shot)) for profile in d.PROFILES for arm in d.ARMS]
                assert all(x==records[0] for x in records)
                assert d.EPOCH_NS-500000<=records[0]['target_request_ns']<=d.EPOCH_NS+500000
                assert 500000<=records[0]['latency_ns']<=1500000
    assert d.SERVICE_NS==1200000


def test_engine_compact_and_independent_raw_checks():
    p=policies()
    for workload in d.WORKLOADS:
        for profile in ('noise_free',*d.PROFILES):
            for arm in d.ARMS:
                for state in range(6) if workload=='teleport' else [0]:
                    for bits in ((0,0),(0,1),(1,0),(1,1)):
                        s=d.spec('pilot',workload,profile,0,arm,0,fixed_state=state)
                        raw,compact,_=fixture(s,p,bits)
                        assert engine.validate(raw,s,p)==compact
    s=d.spec('pilot','plus','final_si',0,'learned',0)
    r,_,_=fixture(s,p);r['policy_sha256']='f'*64
    rejects(lambda:engine.validate(r,s,p),'policy identity')
    r,_,_=fixture(s,p);r['memory_channel'][0]['density_real'][0][1]=0
    rejects(lambda:engine.validate(r,s,p))


def test_pilot_gate_all_campaigns_and_branches():
    p=policies();rows=[]
    for s in d.specifications('pilot'):
        bits=(s['shot']%2,(s['shot']//2)%2)
        raw,c,_=fixture(s,p,bits);rows.append(engine.validate(raw,s,p))
    gate=engine.validate_gate(rows,'pilot')
    assert gate['noise_free_controls']==1176 and gate['learned_policies_exercised']==64
    assert all(n==24 for n in gate['teleportation_branches_per_arm'].values())
    bad=deepcopy(rows);bad[0]['wrong_output']=1;rejects(lambda:engine.validate_gate(bad,'pilot'))


def synthetic_evaluation(p):
    policy_id=d.stable(p);rows=[]
    for s in d.specifications('evaluation'):
        effect=.002 if s['arm']=='learned' else 0.
        error=int(s['shot']<(3 if s['arm']=='learned' else 2))
        rows.append(dict(spec=s,policy_sha256=policy_id,selected_phase_ns=d.phase_for(s,p),
            pairing_sha256=f"synthetic|{s['workload']}|{s['campaign']}|{s['shot']}",
            competitor_service_ns=0 if s['arm']=='no_competitor' else 1200000,
            fidelity=.98-effect,expected_infidelity=.02+effect,physical_storage_ns=2000000+(100000 if effect else 0),
            target_request_ns=3000000,target_finished_ns=6000000+(100000 if effect else 0),wrong_output=error,unfinished=0))
    return rows


def test_analysis_known_effect_and_incomplete_bounds():
    p=policies();rows=synthetic_evaluation(p);result=analysis.analyze(rows,p,{})
    assert result['all_four_primary_intervals_above_zero']
    for effect in result['primary_contrasts']:
        assert abs(effect['mean_difference_pp']-.2)<1e-12
        assert all(abs(x-.2)<1e-12 for x in effect['approximate_adjusted_interval_pp'])
        assert effect['positive_campaigns']==32 and abs(effect['mean_storage_difference_ms']-.1)<1e-12
        assert abs(effect['sampled_wrong_output_difference_pp']-100/256)<1e-12
    # An unfinished execution stays in the offered denominator and suppresses
    # the fidelity inference; it must not be silently dropped.
    changed=next(r for r in rows if r['spec']['workload']=='plus' and r['spec']['profile']=='final_si' and r['spec']['arm']=='learned')
    changed.update(unfinished=1,wrong_output=0,target_finished_ns=None)
    result=analysis.analyze(rows,p,{})
    effect=result['primary_contrasts'][0]
    assert effect['mean_difference_pp'] is None and effect['approximate_adjusted_interval_pp'] is None
    assert not effect['interval_above_zero'] and effect['offered_per_arm']==8192
    assert abs(effect['learned_loss_bounds'][1]-effect['learned_loss_bounds'][0]-1/8192)<1e-12
    assert not result['all_four_primary_intervals_above_zero']
    assert analysis.bounds([dict(unfinished=0,expected_infidelity=.02)]*10)==[.02,.02]
    assert analysis.bounds([dict(unfinished=1,expected_infidelity=None)]*10)==[0.,1.]


def test_null_and_negative_statistics():
    draws=np.random.default_rng(1).integers(0,32,(20000,32))
    assert analysis.interval([0.]*32,draws,4)==[0.,0.]
    assert all(abs(x+.2)<1e-12 for x in analysis.interval([-.002]*32,draws,4))
    assert not any(n=='netsquid' or n.startswith('netsquid.') for n in sys.modules)


def test_checkpoint_integrity_and_worker_diagnostics():
    p=policies();old_check=runner.q6.check_repo;old_run=engine.run
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp);folder=base/'batch';folder.mkdir()
        specs=[d.spec('pilot','plus','noise_free',0,arm,0) for arm in d.ARMS]
        job=dict(stage='pilot',specs=specs,code_hashes=runner.code_hashes(),frozen=p,fixed_configuration_sha256=d.stable(p))
        jobfile=folder/'job.json';runner.put(jobfile,job)
        args=SimpleNamespace(repo=base,job=jobfile,raw=folder/'fixture.raw.jsonl.gz',compact=folder/'fixture.compact.jsonl.gz',statistics=folder/'fixture.stats.json')
        try:
            runner.q6.check_repo=lambda _:dict(fixture=True)
            engine.run=lambda s,f:fixture(s,f)[0]
            runner.worker(args)
            marker=dict(job_hash=d.stable(job))
            for kind,path in [('raw',args.raw),('compact',args.compact),('statistics',args.statistics)]:
                marker[kind+'_file']=path.name;marker[kind+'_sha256']=sha_file(path)
            runner.put(folder/'completed.json',marker)
            before={f.name:f.read_bytes() for f in folder.iterdir()}
            rows,_,_=runner.verified_checkpoint(folder,job)
            assert len(rows)==3 and before=={f.name:f.read_bytes() for f in folder.iterdir()}
            args.raw.write_bytes(args.raw.read_bytes()+b'corrupt')
            rejects(lambda:runner.verified_checkpoint(folder,job),'checksum mismatch')
            rejects(lambda:runner.put(jobfile,dict(changed=True)),'metadata differs')
            # Failed validation produces diagnostics without a completion marker.
            failed=base/'failed';failed.mkdir();args.raw=failed/'bad.raw.jsonl.gz';args.compact=failed/'bad.compact.jsonl.gz';args.statistics=failed/'stats.json'
            def bad_run(s,f):
                raw=fixture(s,f)[0];raw['native_t2_ns']=4000000;return raw
            engine.run=bad_run
            rejects(lambda:runner.worker(args))
            assert (failed/'bad.raw.jsonl.gz.failed-spec.json').is_file()
            assert (failed/'bad.raw.jsonl.gz.failed-record.json').is_file()
            assert not args.statistics.exists() and not (failed/'completed.json').exists()
        finally:
            runner.q6.check_repo=old_check;engine.run=old_run


def prerequisite_fixture(base):
    # Tiny synthetic attestation used only to exercise reader/identity checks.
    run=base/'run';audit=base/'audit';(run/'source').mkdir(parents=True);(audit/'audit_source').mkdir(parents=True)
    (run/'COMPLETE.json').write_text('{}');(run/'source/example.py').write_text('# fixture\n');(audit/'audit_source/check.py').write_text('# fixture auditor\n')
    expected=dict(header_sha256={'COMPLETE.json':sha_file(run/'COMPLETE.json')},source_hashes={'example.py':sha_file(run/'source/example.py')},
        review_archive_sha256='a'*64,audit_version='test-fixture',audit_code_sha256='b'*64,
        audit_code_hashes={'check.py':sha_file(audit/'audit_source/check.py')},counts={'pilot':1},committed_batches=1)
    identity=dict(version=expected['audit_version'],audit_code_sha256=expected['audit_code_sha256'],audit_code_hashes=expected['audit_code_hashes'],
        expected_header_sha256=expected['header_sha256'],expected_source_hashes=expected['source_hashes'],run_dir=str(run))
    for name in ('audit_receipts.jsonl.gz','fixed_raw_samples.jsonl.gz'):
        with gzip.open(audit/name,'wt') as f:f.write('{}\n')
    report=dict(version=expected['audit_version'],status='PASS',audit_code_sha256=expected['audit_code_sha256'],reviewed_archive_sha256=expected['review_archive_sha256'],
        run_dir=str(run),audited_trials=1,committed_batches=1,stage_metrics={'pilot':{'trials':1}},
        audit_receipts_sha256=sha_file(audit/'audit_receipts.jsonl.gz'),fixed_raw_samples_sha256=sha_file(audit/'fixed_raw_samples.jsonl.gz'))
    runner.put(audit/'audit_identity.json',identity);runner.put(audit/'audit_report.json',report)
    runner.put(audit/'AUDIT_COMPLETE.json',dict(status='PASS',report_sha256=sha_file(audit/'audit_report.json'),audited_trials=1))
    return run,audit,expected


def test_prerequisite_missing_failed_and_tampered_audit():
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp);run,audit,expected=prerequisite_fixture(base)
        stable,_=prereq.verify_parent('q12',run,audit,expected);assert stable['audited_trials']==1
        (audit/'AUDIT_FAILED.json').write_text('{}')
        rejects(lambda:prereq.verify_parent('q12',run,audit,expected),'reports failure')
        (audit/'AUDIT_FAILED.json').unlink()
        (audit/'audit_report.json').write_text('{}')
        # Hash is tested before report contents are accepted.
        rejects(lambda:prereq.verify_parent('q12',run,audit,expected),'report hash mismatch')
        (audit/'AUDIT_COMPLETE.json').unlink()
        rejects(lambda:prereq.verify_parent('q12',run,audit,expected),'raw-audit notebook first')


def test_unchanged_parent_modules_and_no_simulator_import():
    root=Path(__file__).resolve().parent
    parent=json.loads((root/'parent_sources.json').read_text())
    for name in ('p1_engine.py','q6_reference.py','time_checks.py','hardware_models.json','q13_design.py'):
        assert sha_file(root/name)==parent['q13_source_hashes'][name]
    assert sha_file(root/'q13_raw_checks.py')==parent['raw_checker_sha256']
    assert not any(n=='netsquid' or n.startswith('netsquid.') or n=='qoala' or n.startswith('qoala.') for n in sys.modules)


def main():
    tests=[v for k,v in globals().items() if k.startswith('test_')]
    for fn in tests:
        fn();print('PASS',fn.__name__,flush=True)
    print(f'{len(tests)} local test groups passed. Synthetic fixtures only. NetSquid runtime NOT executed.',flush=True)


if __name__=='__main__':main()
