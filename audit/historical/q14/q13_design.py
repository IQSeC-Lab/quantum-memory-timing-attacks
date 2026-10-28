"""Frozen Q13 protocol. No Q12 output is read and no evaluation chooses a phase."""
from pathlib import Path
import hashlib
import itertools
import json
import numpy as np

VERSION = 'q13-memory-robustness-1'
PIN = '31fa2c9699578d227736557f35cd01a134c4bbde'
MODELS = json.loads(Path(__file__).with_name('hardware_models.json').read_text())
PROFILES = MODELS['profiles']
WORKLOADS = ('plus', 'teleport')
ARMS = ('no_competitor', 'random_phase', 'fixed_phase')
SCENARIOS = {'coarse_500us': 500000, 'exact_epoch': 0, 'coarse_1000us': 1000000}
REPLICATIONS = 32
SHOTS = 64
BATCH = 128
EPOCH_NS = 3000000
HORIZON_NS = 30000000
JOBS = 4
INSTRUCTIONS = 30
HOST_INSTRUCTION_NS = 10000
SERVICE_NS = JOBS * INSTRUCTIONS * HOST_INSTRUCTION_NS
FIXED_PHASE_NS = {'plus': 1250000, 'teleport': 500000}
ROOTS = {'baseline': 131301, 'pilot': 131302, 'evaluation': 131303}
BOOTSTRAP_ROOT = 131304
PRIMARY_FAMILY = 4
PRACTICAL_REFERENCE_PP = 0.5  # Reporting reference, not a community acceptance threshold.
BASELINE_AGES_MS = (0.0, 0.1, 1.0, 4.8, 8.95, 13.0, 26.0)
SOURCE_FILES = ('q13.py', 'q13_design.py', 'q13_engine.py', 'q13_baseline.py',
                'q13_analysis.py', 'q13_tests.py', 'p1_engine.py', 'q6_reference.py',
                'time_checks.py', 'physics_reference.py', 'hardware_models.json', 'source_provenance.json')


def stable(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def spec(stage, workload, profile, replication, scenario, arm, shot, **extra):
    return dict(stage=stage, workload=workload, profile=profile, replication=replication,
                scenario=scenario, arm=arm, shot=shot, **extra)


def specifications(stage):
    if stage == 'baseline':
        for matrix, profile, ai, ti, age, partition in itertools.product(
                ('rounded_si', 'deposited_exact'), PROFILES, range(2), range(3), BASELINE_AGES_MS, ('whole', 'split')):
            yield dict(stage=stage, matrix=matrix, profile=profile, alpha_index=ai,
                       theta_index=ti, age_ms=age, partition=partition)
    elif stage == 'pilot':
        for w, arm in itertools.product(WORKLOADS, ARMS):
            for i in range(8 if w == 'plus' else 384):
                yield spec(stage, w, 'noise_free', 0, 'coarse_500us', arm, i,
                           fixed_state=i//64 if w == 'teleport' else 0)
        for w, p, scenario, arm, i in itertools.product(WORKLOADS, PROFILES, SCENARIOS, ARMS, range(4)):
            yield spec(stage, w, p, 1, scenario, arm, i)
    elif stage == 'evaluation':
        for w, p, rep, scenario, arm, i in itertools.product(WORKLOADS, PROFILES, range(REPLICATIONS), SCENARIOS, ARMS, range(SHOTS)):
            yield spec(stage, w, p, rep, scenario, arm, i)
    else:
        raise ValueError(stage)


def batches(stage):
    chunk = []
    for item in specifications(stage):
        chunk.append(item)
        if len(chunk) == BATCH:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def counts():
    return dict(baseline=336, pilot=1320, evaluation=73728)


def inputs(s):
    # Both scheduling arms and both physical parameterizations share the same
    # exogenous inputs. The phase does not depend on any hidden input below.
    key = [ROOTS[s['stage']], WORKLOADS.index(s['workload']), s['replication'],
           list(SCENARIOS).index(s['scenario']), s['shot']]
    def rng(stream):
        return np.random.default_rng(np.random.SeedSequence(key + [stream]))
    width = SCENARIOS[s['scenario']]
    offset = int(rng(1).integers(-width, width+1)) if width else 0
    return dict(seed=int(rng(0).integers(1, 2**31-1)),
                latency_ns=int(rng(2).integers(500000, 1500001)),
                state=int(s.get('fixed_state', int(rng(3).integers(0, 6)))) if s['workload']=='teleport' else 0,
                target_request_ns=EPOCH_NS+offset,
                hidden_offset_ns=offset,
                random_phase_ns=int(rng(4).integers(0, 4000000)))


def release_plan(epoch_ns, workload, arm, random_phase_ns):
    assert isinstance(epoch_ns, int) and epoch_ns >= 0
    assert arm in ARMS and workload in WORKLOADS
    if arm == 'no_competitor':
        return None
    phase = random_phase_ns if arm == 'random_phase' else FIXED_PHASE_NS[workload]
    assert isinstance(phase, int) and 0 <= phase < 4000000
    return epoch_ns + phase


def protocol(hashes):
    return dict(version=VERSION, code_hashes=hashes, repo_pin=PIN, counts=counts(),
                profiles=PROFILES, workloads=list(WORKLOADS), scenarios=SCENARIOS,
                replications=REPLICATIONS, trials_per_arm_per_replication=SHOTS,
                fixed_phases_ns=FIXED_PHASE_NS,
                phase_provenance='Existing Q12 integration-pilot constants, frozen independently of any Q12 evaluation. They are not claimed optimal or learned in this study.',
                competitor_jobs=JOBS, instructions_per_job=INSTRUCTIONS,
                competitor_cpu_service_ns=SERVICE_NS, roots=ROOTS, bootstrap_root=BOOTSTRAP_ROOT,
                baseline='336 parameter/circuit/age/partition cases, each requiring both client measurement branches, at most 64 seeded attempts per case. Branch search validates mathematics; it is not experimental data.',
                primary='Fixed minus equal-work random expected infidelity, at first verification gate, at +/-0.5 ms request uncertainty. Four contrasts: two workloads x two physical profiles.',
                primary_family=PRIMARY_FAMILY,
                inference='32 independent Monte Carlo replication means, 20,000 paired bootstrap resamples; Bonferroni four-comparison intervals with approximate coverage. Exact sign tests describe direction separately. Uncertainty is Monte Carlo variability conditional on the fixed model, not physical-parameter uncertainty.',
                reporting_reference_pp=PRACTICAL_REFERENCE_PP,
                reporting_reference_note='0.5 percentage points is a disclosed reporting reference only; it does not establish practical significance by itself.',
                secondary='Exact epoch and +/-1 ms are exploratory pointwise contrasts. Sampled verification error, unfinished fraction, latency, and no-competitor arms are all retained.',
                noise_implementation='Native memory noise disabled. Full-age isotropic depolarization is applied once immediately before verification, then the actual NetSquid measurement samples its output. Equivalent to distributed storage noise only for these ideal local gates, remote measurements, and nonadaptive Bob corrections; the pilot checks state and channel equality.',
                timing_layers='The coherence envelope is source-derived. Qoala host instruction 10 us, QNOS instruction 1 us, ideal gates/EPR, 1 us EPR link, and 0.5–1.5 ms one-way messages remain P1 modelling assumptions. The source QNPU 10 us tick is not a host-instruction measurement. Storage endpoints differ from the source trace endpoint and are labelled.',
                capabilities='Assumed co-location, normal competing CPU jobs, known workload class and a supplied coarse request epoch. Release policy receives no victim outcome, actual preparation time, quantum state, or trace.',
                limitations='No deployment, admission-policy proof, learned attack, calibrated full QNodeOS model, finite-temperature relaxation, gate/readout noise, sustained traffic, or imperfect-EPR transfer in the Qoala experiment. Baseline alone uses the source EPR matrices.',
                incomplete='Never drop unfinished executions. Report per-offered loss bounds; suppress fidelity-effect inference if either compared arm has any missing fidelity or unfinished output.',
                stopping='Fixed samples and phases. Baseline and pilot are correctness gates, never positive-effect gates. No Q12 output is read; no evaluation-guided retuning or sample extension.',
                no_latency_filter=True)
