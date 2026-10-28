"""Offline checks. These never substitute for the mandatory NetSquid server gate."""
from pathlib import Path
import gzip
import hashlib
import inspect
import json
import tempfile
import numpy as np
import q13_design as d
import q13_engine as engine
import physics_reference as ref
from q13_analysis import analyze


def rejects(function):
    try:
        function()
    except (AssertionError,ValueError,RuntimeError):
        return
    raise AssertionError('Invalid input was accepted')


def test_grid_budget_and_stage_independence():
    for stage,count in d.counts().items():
        ss=list(d.specifications(stage))
        assert len(ss)==count and len({d.stable(s) for s in ss})==count
        assert sum(len(b) for b in d.batches(stage))==count
    assert len(set(d.ROOTS.values()))==3 and d.SERVICE_NS==1200000
    assert d.PRIMARY_FAMILY==len(d.WORKLOADS)*len(d.PROFILES)==4
    assert d.SHOTS*d.REPLICATIONS==2048


def test_policy_boundary_and_exogenous_pairing():
    assert set(inspect.signature(d.release_plan).parameters)=={'epoch_ns','workload','arm','random_phase_ns'}
    for w in d.WORKLOADS:
        candidates=[d.spec('evaluation',w,p,7,'coarse_500us',a,21) for p in d.PROFILES for a in d.ARMS]
        ii=[d.inputs(s) for s in candidates]
        assert all(i==ii[0] for i in ii)
        for i in ii:
            assert abs(i['target_request_ns']-d.EPOCH_NS)<=500000
        assert d.release_plan(d.EPOCH_NS,w,'fixed_phase',0)==d.EPOCH_NS+d.FIXED_PHASE_NS[w]
        assert d.release_plan(d.EPOCH_NS,w,'fixed_phase',3999999)==d.EPOCH_NS+d.FIXED_PHASE_NS[w]
        assert d.release_plan(d.EPOCH_NS,w,'no_competitor',0) is None
    a=d.spec('pilot','plus','final_si',1,'coarse_500us','fixed_phase',2)
    b=dict(a,stage='evaluation')
    assert d.inputs(a)!=d.inputs(b)


def test_reference_models_match_audited_values():
    for row in d.MODELS['known_reference_rows']:
        matrix=ref.RHO_EPR_ROUNDED if row['matrix']=='final_text_rounded' else np.array(d.MODELS['exact_epr_matrix'])
        normalized=matrix/np.trace(matrix)
        ref.check_density(normalized)
        for age,key in [(0,'zero_memory_fidelity'),(4.8,'fidelity_at_4_8ms'),(8.95,'fidelity_at_8_95ms')]:
            actual=ref.fidelity(np.pi/2,np.pi/4,age,matrix,row['t_coh_ms'],row['exponent'])
            assert abs(actual-row[key])<1e-12
    assert abs(np.trace(ref.RHO_EPR_ROUNDED)-1.001)<1e-12
    exact=np.array(d.MODELS['exact_epr_matrix'])
    assert abs(np.trace(exact)-1)<1e-12


def test_age_preservation_and_unitary_covariance():
    rng=np.random.default_rng(13337)
    state=np.array([1,1j])/np.sqrt(2);rho=np.outer(state,state.conj())
    for profile,p in d.PROFILES.items():
        a=ref.depolarize_between(rho,0,4.8,p['t_ms'],p['exponent'])
        b=ref.depolarize_between(ref.depolarize_between(rho,0,1.7,p['t_ms'],p['exponent']),1.7,4.8,p['t_ms'],p['exponent'])
        assert np.max(np.abs(a-b))<1e-12
        reset=ref.depolarize_between(ref.depolarize_between(rho,0,1.7,p['t_ms'],p['exponent']),0,3.1,p['t_ms'],p['exponent'])
        assert np.max(np.abs(a-reset))>1e-4
        for _ in range(8):
            h=rng.normal(size=(2,2))+1j*rng.normal(size=(2,2));u,_=np.linalg.qr(h)
            lhs=u@a@u.conj().T
            rhs=ref.depolarize_between(u@rho@u.conj().T,0,4.8,p['t_ms'],p['exponent'])
            assert np.max(np.abs(lhs-rhs))<1e-12
        assert abs(engine.contraction(profile,4800000)-ref.coherence(4.8,p['t_ms'],p['exponent']))<1e-12
    rejects(lambda:engine.contraction('final_si',-1))


def bob_channel(rho,factor):
    paulis=(ref.I,ref.X,ref.Y,ref.Z)
    weights=((1+3*factor)/4,)+( (1-factor)/4,)*3
    return sum(w*(np.kron(ref.I,op)@rho@np.kron(ref.I,op).conj().T) for w,op in zip(weights,paulis))


def test_remote_measurement_channel_commutation():
    # Full bipartite channel versus conditional single-qubit channel, including
    # a nonmaximally entangled state and unequal branch probabilities.
    rng=np.random.default_rng(13001)
    v=rng.normal(size=4)+1j*rng.normal(size=4);v=v/np.linalg.norm(v)
    rho=np.outer(v,v.conj())
    for factor in (0.0,.37,1.0):
        noisy=bob_channel(rho,factor)
        for branch in (0,1):
            sl=slice(2*branch,2*branch+2)
            raw=rho[sl,sl];p=np.trace(raw).real
            actual=noisy[sl,sl]/np.trace(noisy[sl,sl]).real
            expected=factor*raw/p+(1-factor)*np.eye(2)/2
            assert abs(np.trace(noisy[sl,sl]).real-p)<1e-12
            assert np.max(np.abs(actual-expected))<1e-12


def test_six_state_targets_and_all_dqc_branches():
    psi=np.array([0,1,1,0],complex)/np.sqrt(2)
    for alpha in (np.pi/2,np.pi):
        for theta in (np.pi/4,np.pi/2,np.pi):
            target=ref.ideal_target(alpha)
            branches=ref.delegated_branches(alpha,theta,np.outer(psi,psi.conj()))
            assert {x[0] for x in branches}=={0,1}
            assert all(abs((target.conj()@state@target).real-1)<1e-12 for _,_,state in branches)
    for state in range(6):
        v=engine.target_vector('teleport',state)
        assert abs(np.vdot(v,v)-1)<1e-12
        rho=np.outer(v,v.conj());factor=.71
        noisy=factor*rho+(1-factor)*np.eye(2)/2
        assert abs((v.conj()@noisy@v).real-(1+factor)/2)<1e-12


def synthetic_rows():
    # Only analysis test fixtures; no simulator output is fabricated or exported.
    rows=[]
    for s in d.specifications('evaluation'):
        extra=(s['replication']+1)*.0001 if s['arm']=='fixed_phase' else 0
        loss=.1+extra if s['arm']!='no_competitor' else .08
        rows.append(dict(spec=s,pairing_sha256=str((s['workload'],s['replication'],s['scenario'],s['shot'])),
                         unfinished=0,fidelity=1-loss,expected_infidelity=loss,wrong_output=int(s['shot']%10==0),
                         physical_storage_ns=1000000+int(round(extra*1e7)),
                         competitor_service_ns=0 if s['arm']=='no_competitor' else d.SERVICE_NS))
    return rows


def test_paired_inference_and_incomplete_policy():
    rows=synthetic_rows();result=analyze(rows)
    assert len(result['primary_contrasts'])==4 and len(result['secondary_contrasts'])==8
    for r in result['primary_contrasts']:
        assert r['independent_replications']==32 and r['family']==4
        assert abs(r['mean_difference_pp']-.165)<1e-12
        assert r['interval_above_zero'] and not r['interval_above_reporting_reference']
    target=next(r for r in rows if r['spec']['workload']=='plus' and r['spec']['profile']=='final_si'
                and r['spec']['scenario']=='coarse_500us' and r['spec']['arm']=='fixed_phase')
    target['unfinished']=1;target['wrong_output']=0
    result=analyze(rows)
    r=result['primary_contrasts'][0]
    assert not r['all_compared_targets_completed'] and r['mean_difference_pp'] is None
    assert r['fixed_loss_bounds'][1]>r['fixed_loss_bounds'][0]
    assert not result['all_four_primary_intervals_above_zero']
    target['pairing_sha256']='altered'
    rejects(lambda:analyze(rows))


def test_integrity_and_resume_rejection():
    import q13
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);job={'stage':'pilot','specs':[{'fixture':True}]}
        q13.put(root/'protocol.json',{'frozen':1});q13.put(root/'protocol.json',{'frozen':1})
        rejects(lambda:q13.put(root/'protocol.json',{'frozen':2}))
        for name,value in [('raw',[{'fixture':'raw'}]),('compact',[{'spec':{'fixture':True}}])]:
            with gzip.open(root/(name+'.gz'),'wt') as f:
                for row in value:f.write(json.dumps(row)+'\n')
        q13.put(root/'statistics.json',{'trials':1,'validation':'Q13 full raw-record validation passed'})
        marker={'job_hash':d.stable(job)}
        for kind,file in [('raw','raw.gz'),('compact','compact.gz'),('statistics','statistics.json')]:
            marker[kind+'_file']=file;marker[kind+'_sha256']=q13.sha(root/file)
        q13.put(root/'completed.json',marker)
        rr,_,_=q13.verified_checkpoint(root,job);assert len(rr)==1
        rejects(lambda:q13.verified_checkpoint(root,dict(job,changed=True)))
        with (root/'raw.gz').open('ab') as f:f.write(b'changed')
        rejects(lambda:q13.verified_checkpoint(root,job))


def test_parent_identity_and_no_q12_result_dependency():
    here=Path(__file__).resolve().parent
    metadata=json.loads((here/'source_provenance.json').read_text())
    for name,item in metadata['unchanged_parent_sources'].items():
        assert hashlib.sha256((here/name).read_bytes()).hexdigest()==item['sha256']
    assert metadata['q12_result_files_consumed']==[]
    assert not any('q12' in name.lower() for name in d.SOURCE_FILES)
    assert len(set(d.SOURCE_FILES))==len(d.SOURCE_FILES)
    for name in d.SOURCE_FILES:
        assert (here/name).is_file()


def main():
    tests=[test_grid_budget_and_stage_independence,test_policy_boundary_and_exogenous_pairing,
           test_reference_models_match_audited_values,test_age_preservation_and_unitary_covariance,
           test_remote_measurement_channel_commutation,test_six_state_targets_and_all_dqc_branches,
           test_paired_inference_and_incomplete_policy,test_integrity_and_resume_rejection,
           test_parent_identity_and_no_q12_result_dependency]
    for test in tests:
        test();print('PASS',test.__name__,flush=True)
    print(f'{len(tests)} local tests passed. NetSquid runtime NOT executed by these tests.',flush=True)


if __name__=='__main__':
    main()
