"""Fixed engineering fixtures; no policy search or scientific effect estimation."""
from q16_design import SCHEDULERS, stable, seed_for

REVISION = 'q16-runtime-stress-1'
CASES = ('exhaustion_new_job', 'boundary_new_job', 'exhaustion_plus', 'exhaustion_teleport')
HORIZON_NS = 30_000_000
PARENT_BUNDLE = '5cf47efdc05692414e728edc6e30cb00f7eb45e45ae2abeda31ba0e2a2151729'


def specifications(stage='stress'):
    if stage != 'stress':
        raise ValueError('Only the fixed stress suite is available')
    for scheduler in SCHEDULERS:
        for case in CASES:
            yield dict(stage='stress', scheduler=scheduler, case=case,
                       workload='teleport' if case.endswith('teleport') else 'plus',
                       profile='noise_free')


def inputs(spec):
    if spec not in list(specifications()):
        raise ValueError('Unknown stress fixture')
    case = spec['case']
    boundary = case == 'boundary_new_job'
    attacker = [dict(index=i, requested_ns=9_800_000 if boundary else 0,
                     instructions=30, requested_service_ns=300_000)
                for i in range(1 if boundary else 6)]
    benign = ([dict(index=0, requested_ns=9_850_000 if boundary else 1_300_000,
                    instructions=30, requested_service_ns=300_000)]
              if case.endswith('new_job') else [])
    requests = ([] if case.endswith('new_job') else
                [dict(index=0, offered_ns=1_300_000, state=0, measured=False)])
    return dict(attacker=attacker,
                private=dict(benign=benign, requests=requests,
                             simulator_seed=seed_for('runtime-stress', case)),
                horizon_ns=HORIZON_NS,
                purpose='Deliberately exceed offered quota or straddle a boundary to exercise enforcement. '
                        'Fixed harness timings are engineering fixtures, not timing-blind attack evidence.')


def protocol(hashes):
    return dict(revision=REVISION, parent_source_bundle_sha256=PARENT_BUNDLE,
                source_sha256=hashes, specifications=list(specifications()),
                input_sha256=[stable(inputs(s)) for s in specifications()],
                period_ns=10_000_000, budget_ns=1_200_000, block_cap_ns=300_000,
                horizon_ns=HORIZON_NS, executions=12, victim_requests=6,
                requested_attacker_jobs=57, benign_jobs=6, noise='noise_free',
                gate='All 12 cases must pass independent raw replay and their fixed progress checks. '
                     'Both message and QPU wait/resume paths are required in each quantum fixture. '
                     'Missing coverage fails; there is no automatic resampling.',
                interpretation='Implementation validation only. No attack-effect, deadline, power, '
                               'hardware, or conference-readiness claim is tested.')
