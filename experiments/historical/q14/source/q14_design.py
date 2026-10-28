"""Q14 frozen transfer protocol: reuse all Q12 phases; no new calibration."""
from pathlib import Path
import hashlib
import itertools
import json
import numpy as np

VERSION='q14-frozen-transfer-1'
PIN='31fa2c9699578d227736557f35cd01a134c4bbde'
MODELS=json.loads(Path(__file__).with_name('hardware_models.json').read_text())
PROFILES=MODELS['profiles']
WORKLOADS=('plus','teleport')
ARMS=('no_competitor','random_phase','learned')
CAMPAIGNS=32
SHOTS=256
BATCH=128
EPOCH_NS=3000000
HALFWIDTH_NS=500000
HORIZON_NS=30000000
JOBS=4
INSTRUCTIONS=30
HOST_INSTRUCTION_NS=10000
SERVICE_NS=JOBS*INSTRUCTIONS*HOST_INSTRUCTION_NS
PHASES_NS=tuple(range(0,4000000,250000))
ROOTS={'pilot':141301,'evaluation':141303}
BOOTSTRAP_ROOT=141304
PRIMARY_FAMILY=4
SOURCE_FILES=('q14.py','q14_design.py','q14_engine.py','q14_analysis.py','q14_prerequisites.py','q14_tests.py',
              'synthetic_records.py','p1_engine.py','q6_reference.py','time_checks.py','hardware_models.json',
              'q13_design.py','q13_raw_checks.py','parent_sources.json','q14_expected_prerequisites.json')


def stable(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def spec(stage,workload,profile,campaign,arm,shot,**extra):
    return dict(stage=stage,workload=workload,profile=profile,campaign=campaign,
                scenario='coarse_500us',arm=arm,shot=shot,**extra)


def specifications(stage):
    if stage=='pilot':
        # Q12 campaign zero's actual frozen phase, not an integration-pilot constant.
        for w,arm in itertools.product(WORKLOADS,ARMS):
            for i in range(8 if w=='plus' else 384):
                yield spec(stage,w,'noise_free',0,arm,i,fixed_state=i//64 if w=='teleport' else 0)
        # All 64 learned policies and both profiles are exercised before evaluation.
        for w,p,c,arm,i in itertools.product(WORKLOADS,PROFILES,range(CAMPAIGNS),ARMS,range(2)):
            yield spec(stage,w,p,c,arm,1000+i)
    elif stage=='evaluation':
        for w,p,c,arm,i in itertools.product(WORKLOADS,PROFILES,range(CAMPAIGNS),ARMS,range(SHOTS)):
            yield spec(stage,w,p,c,arm,i)
    else:
        raise ValueError(stage)


def batches(stage):
    batch=[]
    for item in specifications(stage):
        batch.append(item)
        if len(batch)==BATCH:
            yield batch;batch=[]
    if batch:yield batch


def counts():
    return dict(pilot=1944,evaluation=98304)


def inputs(s):
    # Fresh keys relative to both Q12 and Q13. Arms and profiles are paired.
    key=[ROOTS[s['stage']],WORKLOADS.index(s['workload']),s['campaign'],s['shot']]
    def rng(stream):return np.random.default_rng(np.random.SeedSequence(key+[stream]))
    offset=int(rng(1).integers(-HALFWIDTH_NS,HALFWIDTH_NS+1))
    return dict(seed=int(rng(0).integers(1,2**31-1)),latency_ns=int(rng(2).integers(500000,1500001)),
        state=int(s.get('fixed_state',int(rng(3).integers(0,6)))) if s['workload']=='teleport' else 0,
        target_request_ns=EPOCH_NS+offset,hidden_offset_ns=offset,
        random_phase_ns=int(rng(4).integers(0,4000000)))


def validate_policies(frozen):
    expected={w+'|'+str(c) for w in WORKLOADS for c in range(CAMPAIGNS)}
    assert set(frozen)==expected,'All 64 source campaigns must be retained'
    for item in frozen.values():
        assert set(item)=={'phase_ns','source_observations_sha256'}
        assert type(item['phase_ns']) is int and item['phase_ns'] in PHASES_NS
        assert isinstance(item['source_observations_sha256'],str) and len(item['source_observations_sha256'])==64


def phase_for(s,frozen):
    if s['arm']!='learned':return None
    return frozen[s['workload']+'|'+str(s['campaign'])]['phase_ns']


def release_plan(public_epoch_ns,arm,selected_phase_ns=None,random_phase_ns=None):
    """No victim time, state, outcome, queue or event trace argument is accepted."""
    assert type(public_epoch_ns) is int and public_epoch_ns>=0 and arm in ARMS
    if arm=='no_competitor':return None
    phase=random_phase_ns if arm=='random_phase' else selected_phase_ns
    assert type(phase) is int and 0<=phase<4000000
    if arm=='learned':assert phase in PHASES_NS
    return public_epoch_ns+phase


def protocol(hashes,prerequisites):
    return dict(version=VERSION,code_hashes=hashes,repo_pin=PIN,counts=counts(),
        prerequisites=prerequisites,workloads=list(WORKLOADS),profiles=PROFILES,
        campaigns=CAMPAIGNS,evaluation_trials_per_arm_per_campaign=SHOTS,arms=list(ARMS),
        scheduler='FCFS',epoch_ns=EPOCH_NS,epoch_halfwidth_ns=HALFWIDTH_NS,horizon_ns=HORIZON_NS,
        jobs=JOBS,instructions_per_job=INSTRUCTIONS,competitor_service_ns=SERVICE_NS,
        roots=ROOTS,bootstrap_root=BOOTSTRAP_ROOT,primary_family=PRIMARY_FAMILY,
        primary='Learned minus equal-work random expected infidelity at first verification gate; both workloads and both Q13 memory profiles.',
        transfer='All 32 Q12 calibration campaigns per workload are reused unchanged. No calibration is rerun, no phase is refitted and no campaign is selected using Q12, Q13 or Q14 evaluation outcomes.',
        primary_inference='20,000 bootstrap resamples of 32 paired source-campaign means; Bonferroni four-comparison approximate 95% simultaneous intervals. This treats the independent Q12 calibration campaigns as replications of the original calibration procedure and includes between-campaign variation in learned phases plus fresh evaluation variability. It is not a conditional test-set interval for one fixed phase or an interval for physical-parameter uncertainty. Separate exact sign-test p-values assume independent, exchangeable source campaigns and concern direction rather than the mean.',
        success='Per-workload transfer supported when both profile-specific primary lower bounds exceed zero and every compared execution completes with a fidelity. Broad support requires all four. Failure to satisfy this is retained as inconclusive, workload-specific or negative, not used to retune.',
        secondary='Sampled wrong outputs and wrong-or-unfinished outcomes have exploratory pointwise paired-campaign intervals. Report absolute fidelity, unfinished counts, per-offered loss bounds, storage, response times and no-competitor controls. No extra primary error-rate claim.',
        calibration_cost='Zero new calibration executions. The inherited cost remains 4,608 own-application executions and 17,408 competing jobs (5.2224 seconds competing CPU service) per source campaign; teleportation also uses 4,608 calibration EPR pairs. Record actual inherited CPU/EPR totals from Q12.',
        pilot='1,176 noisefree controls plus 768 noisy checks spanning all source campaigns/profiles/arms. Correctness only; no required positive effect.',
        incomplete='Retain every offered execution. If any compared execution is unfinished or lacks fidelity, suppress fidelity-effect inference and report worst-case per-offered bounds. Never filter on latency.',
        noise='Q13 full-age isotropic channel once before verification, native T1/T2 disabled; ideal local gates/EPR and P1 host/QNOS/link/message assumptions unchanged.',
        capabilities='Assumed shared placement, admission of ordinary competing CPU jobs, workload-class knowledge and a supplied coarse request epoch. No victim outcomes or private state enter the release policy.',
        limitations='Fresh Monte Carlo inputs, but the same workloads and runtime model. Both memory profiles come from one hardware source. No deployment, proof of epoch availability, full platform calibration, nonideal EPR/gates, retraining under the new model or sustained load.',
        chronology='Follow-up motivated by already inspected Q12/Q13 results. This plan is frozen before Q14 execution; it is not claimed to have been preregistered before the earlier results.',
        stopping='Fixed pilot and evaluation grids; no effect-based stopping, sample extension, phase selection or dropping source campaigns. Read-only Q12/Q13 audit prerequisites must pass before simulation.')
