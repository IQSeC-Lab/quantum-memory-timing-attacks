"""Frozen Q12 design. This module has no NetSquid dependency.

Q12 is a simulator study of self-calibration and transfer conditional on a
coarse execution epoch. It does not implement or validate a deployed service.
"""
import hashlib
import itertools
import json
import math
import numpy as np

VERSION = 'q12-selfcal-1'
PIN = '31fa2c9699578d227736557f35cd01a134c4bbde'
WORKLOADS = ('plus', 'teleport')
CAMPAIGNS = 32
CAL_SHOTS = 256
PRIMARY_SHOTS = 256
SECONDARY_SHOTS = 64
BATCH = 128
PHASES_NS = tuple(range(0, 4000000, 250000))
ARMS = ('no_competitor', 'random_phase', 'learned')
SCENARIOS = {
    'primary_0500us': {'halfwidth_ns': 500000, 'background_load': 0.0, 'delay': 'uniform'},
    'uncertainty_1000us': {'halfwidth_ns': 1000000, 'background_load': 0.0, 'delay': 'uniform'},
    'uncertainty_2000us': {'halfwidth_ns': 2000000, 'background_load': 0.0, 'delay': 'uniform'},
    'background_005': {'halfwidth_ns': 500000, 'background_load': 0.05, 'delay': 'uniform'},
    'delay_shift': {'halfwidth_ns': 500000, 'background_load': 0.0, 'delay': 'two_point'},
}
EPOCH_NS = 3000000  # declared coarse application-request epoch, not quantum preparation time
HORIZON_NS = 30000000
T2_NS = 4000000
JOBS = 4
INSTRUCTIONS = 30
HOST_INSTRUCTION_NS = 10000
BURST_SERVICE_NS = JOBS * INSTRUCTIONS * HOST_INSTRUCTION_NS
ROOTS = {'pilot': 121301, 'calibration': 121302, 'evaluation': 121303}
BOOTSTRAP_ROOT = 121304
CANDIDATES = tuple('phase_%02d' % i for i in range(len(PHASES_NS))) + ('no_competitor', 'random_phase')
SOURCE_FILES = ('q12_design.py', 'q12_engine.py', 'q12.py', 'q12_tests.py',
                'p1_engine.py', 'q6_reference.py', 'time_checks.py', 'parent_sources.json')


def stable(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def specification(stage, workload, campaign, scenario, arm, shot, **extra):
    return dict(stage=stage, workload=workload, scheduler='FCFS', campaign=campaign,
                scenario=scenario, arm=arm, shot=shot, noise='dephasing', **extra)


def specifications(stage):
    if stage == 'pilot':
        # 1,176 noise-free controls, including all six teleportation input states.
        # The server also requires all 24 state/Bell-correction combinations per arm.
        for w, arm in itertools.product(WORKLOADS, ARMS):
            for i in range(8 if w == 'plus' else 384):
                s = specification(stage, w, 0, 'primary_0500us', arm, i)
                s.update(noise='noise_free', fixed_state=(i // 64 if w == 'teleport' else 0))
                yield s
        # 120 diagnostic noisy trials, entirely separate from training/evaluation.
        for w, scenario, arm, i in itertools.product(WORKLOADS, SCENARIOS, ARMS, range(4)):
            yield specification(stage, w, 1, scenario, arm, 1000 + i)
    elif stage == 'calibration':
        for w, campaign, candidate, i in itertools.product(WORKLOADS, range(CAMPAIGNS), CANDIDATES, range(CAL_SHOTS)):
            yield specification(stage, w, campaign, 'own_calibration', candidate, i)
    elif stage == 'evaluation':
        for w, campaign, scenario, arm in itertools.product(WORKLOADS, range(CAMPAIGNS), SCENARIOS, ARMS):
            n = PRIMARY_SHOTS if scenario == 'primary_0500us' else SECONDARY_SHOTS
            for i in range(n):
                yield specification(stage, w, campaign, scenario, arm, i)
    else:
        raise ValueError(stage)


def batches(stage):
    chunk = []
    for s in specifications(stage):
        chunk.append(s)
        if len(chunk) == BATCH:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def counts():
    return dict(pilot=1296, calibration=len(WORKLOADS)*CAMPAIGNS*len(CANDIDATES)*CAL_SHOTS,
                evaluation=len(WORKLOADS)*CAMPAIGNS*len(ARMS)*(PRIMARY_SHOTS+4*SECONDARY_SHOTS),
                primary_evaluation=len(WORKLOADS)*CAMPAIGNS*len(ARMS)*PRIMARY_SHOTS)


def inputs(s):
    """Evaluator-only seeded inputs. Calibration candidates use independent draws.

    Evaluation arms share a pairing key, not an attacker-controlled quantum RNG.
    Scenario and workload streams are distinct; campaigns are independent.
    """
    stage = s['stage']
    w = WORKLOADS.index(s['workload'])
    scenario_index = 0 if stage == 'calibration' else list(SCENARIOS).index(s['scenario'])
    candidate_index = CANDIDATES.index(s['arm']) + 1 if stage == 'calibration' else 0
    key = [ROOTS[stage], w, s['campaign'], scenario_index, s['shot'], candidate_index,
           0 if s['noise'] == 'noise_free' else 1]
    def rng(stream):
        return np.random.default_rng(np.random.SeedSequence(key + [stream]))
    cfg = dict(halfwidth_ns=0, background_load=0.0, delay='uniform') if stage == 'calibration' else SCENARIOS[s['scenario']]
    width = cfg['halfwidth_ns']
    offset = int(rng(1).integers(-width, width+1)) if width else 0
    latency = int(rng(2).integers(500000, 1500001)) if cfg['delay'] == 'uniform' else (500000 if rng(2).random() < 0.8 else 3000000)
    state = int(s.get('fixed_state', int(rng(3).integers(0, 6)))) if s['workload'] == 'teleport' else 0
    background = []
    if cfg['background_load']:
        t = 0.0
        g = rng(6)
        while True:
            t += float(g.exponential(100000 / cfg['background_load']))
            if t >= 12000000:
                break
            background.append(t)
    return dict(seed=int(rng(0).integers(1, 2**31-1)), latency_ns=latency, state=state,
                target_request_ns=EPOCH_NS+offset, hidden_offset_ns=offset,
                blind_phase_ns=int(rng(4).integers(0, 4000000)), background=background,
                target_owner='attacker_calibration' if stage == 'calibration' else 'victim')


def release_plan(public_epoch_ns, arm, selected_phase_ns=None, random_phase_ns=None):
    """Only public epoch, frozen phase, and the attacker's private draw are accepted.

    There is deliberately no target timestamp, state, outcome, queue, or trace
    argument. This models an information boundary, not an OS security sandbox.
    """
    assert isinstance(public_epoch_ns, int) and public_epoch_ns >= 0
    if arm == 'no_competitor':
        return None
    phase = random_phase_ns if arm == 'random_phase' else selected_phase_ns
    assert isinstance(phase, int) and 0 <= phase < 4000000
    if arm != 'random_phase':
        assert phase in PHASES_NS
    return public_epoch_ns + phase


def phase_for(s, frozen):
    if s['arm'].startswith('phase_'):
        return PHASES_NS[int(s['arm'][6:])]
    if s['arm'] != 'learned':
        return None
    if s['stage'] == 'pilot':
        return 1250000 if s['workload'] == 'plus' else 500000
    return int(frozen[s['workload']+'|'+str(s['campaign'])]['phase_ns'])


def calibration_observation(record):
    """Project only the owner's returned bit and its public trial labels."""
    s = record['spec']
    assert s['stage'] == 'calibration' and record['inputs']['target_owner'] == 'attacker_calibration'
    return dict(workload=s['workload'], campaign=s['campaign'], candidate=s['arm'], shot=s['shot'],
                completed=bool(record['completed']), returned_bit=int(record['wrong_output']) if record['completed'] else None)


def select_phase(observations):
    """Maximize own verification error; deterministic earliest-phase tie rule.

    Random/no-competitor calibration arms are reported controls, not selected.
    No evaluation records or evaluator-only timing information is accepted.
    """
    allowed = {'workload', 'campaign', 'candidate', 'shot', 'completed', 'returned_bit'}
    assert observations and all(set(r) == allowed for r in observations)
    cfgs = {(r['workload'], r['campaign']) for r in observations}
    assert len(cfgs) == 1
    summary = {}
    for candidate in CANDIDATES:
        rr = [r for r in observations if r['candidate'] == candidate]
        assert len(rr) == CAL_SHOTS and {r['shot'] for r in rr} == set(range(CAL_SHOTS))
        # Incomplete calibration is retained, but cannot be turned into a
        # fidelity-ranking policy. Abort this design rather than silently drop it.
        assert all(r['completed'] and r['returned_bit'] in (0, 1) for r in rr), 'Incomplete own calibration; export diagnostics'
        summary[candidate] = dict(trials=len(rr), errors=sum(r['returned_bit'] for r in rr))
    index = min(range(len(PHASES_NS)), key=lambda i: (-summary['phase_%02d' % i]['errors'], i))
    return dict(phase_ns=PHASES_NS[index], candidate='phase_%02d' % index,
                observations_sha256=stable(observations), own_trials=len(observations),
                own_arm_counts=summary, selection_rule='maximum own returned verification errors; earliest phase breaks ties')


def protocol(code_hashes):
    return dict(version=VERSION, code_hashes=code_hashes, repo_pin=PIN, counts=counts(),
                workloads=list(WORKLOADS), scheduler='FCFS', independent_calibration_campaigns=CAMPAIGNS,
                own_trials_per_candidate=CAL_SHOTS, phases_ns=list(PHASES_NS), calibration_controls=['no_competitor','random_phase'],
                primary_trials_per_arm_per_campaign=PRIMARY_SHOTS, secondary_trials_per_arm_per_campaign=SECONDARY_SHOTS,
                scenarios=SCENARIOS, epoch_ns=EPOCH_NS, horizon_ns=HORIZON_NS, t2_ns=T2_NS,
                attack_jobs=JOBS, instructions_per_job=INSTRUCTIONS, classical_attack_service_ns=BURST_SERVICE_NS,
                roots=ROOTS, bootstrap_root=BOOTSTRAP_ROOT, primary_family=2,
                primary_endpoint='wrong output per offered victim execution; unfinished outputs reported separately',
                primary_comparison='learned minus equal-work random in primary_0500us, one contrast per workload',
                analysis='32 independently calibrated campaign means; 20000 paired-campaign bootstrap resamples with Bonferroni2 tail adjustment (approximate coverage); separate exact sign test for direction, Bonferroni2',
                secondary_analysis='four challenge conditions per workload; pointwise exploratory intervals, no new confirmatory claims',
                selection='own returned verification bits only; maximum phase error count, earliest phase breaks ties; freeze every campaign before any evaluation',
                capabilities='Assumed shared placement; normal program submissions; attacker owns calibration application and its test inputs/outputs; knows victim workload class and a declared coarse request epoch. No exact victim start, victim output, victim qubit or scheduler/queue access is exposed to release_plan.',
                timing_assumption='The experiment injects a declared coarse epoch and independently jitters victim request time. No deployed mechanism for obtaining that epoch is claimed or implemented.',
                limitations='Same workload class and simulator hardware during calibration/transfer; new tenant identity, inputs and randomness, not arbitrary unseen programs. FCFS only. Ideal local gates/EPR; exponential memory dephasing. Reset per execution, not sustained simultaneous calibration. Hardware-baseline reproduction remains separate.',
                scientific_success='Both primary intervals above zero AND adjusted direction tests below .05 AND all primary victim executions complete; otherwise report workload-specific, inconclusive or negative findings. Interval coverage is approximate.',
                stopping='Fixed grid. Pilot validates integration only. No evaluation-guided retuning or sample-size extension.')
