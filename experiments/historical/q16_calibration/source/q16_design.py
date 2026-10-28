"""Frozen engineering pilot only. No confirmatory evaluation or policy selection."""
import hashlib
import json
import math

REVISION = 'q16-engineering-pilot-1'
SCHEDULERS = ('QUOTA_FCFS', 'QUOTA_RR', 'QUOTA_PROTECTED')
ARMS = ('none', 'random', 'even', 'fixed_burst')
WORKLOADS = ('plus', 'teleport')
TENANTS = ('victim', 'benign', 'attacker')
PERIOD_NS = 10_000_000
BUDGET_NS = 1_200_000
BLOCK_CAP_NS = 300_000
HOST_INSTR_NS = 10_000
INSTRUCTIONS = 30
JOBS_PER_PERIOD = 4
BENIGN_LOAD = 0.4
LATENCY_NS = 1_000_000
DEADLINE_NS = 20_000_000  # Engineering label, NOT an operational SLO.
DRAIN_NS = 200_000_000
PILOT_RELEASE_END_NS = 600_000_000
CONTROL_RELEASE_END_NS = 2_200_000_000
PILOT_WARMUP = 2
PILOT_MEASURED = 8
CONTROL_REQUESTS = 96
PLUS_CONTROL_REQUESTS = 8
PROFILE = {'t_ms': 13.0, 'exponent': 1.67}


def stable(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def seed_for(domain, *identity):
    return int(stable([161306, domain, *identity])[:8], 16)


class Draws:
    """Specified hash-counter uniforms; independent named streams, no global RNG."""
    def __init__(self, seed):
        self.seed, self.counter = int(seed), 0

    def uniform(self):
        value = int(stable([self.seed, self.counter])[:13], 16)
        self.counter += 1
        return (value + 0.5) / 2**52


def specifications(stage):
    if stage == 'pilot':
        for scheduler in SCHEDULERS:
            for workload in WORKLOADS:
                for arm in ARMS:
                    for stream in range(8):
                        yield dict(stage=stage, scheduler=scheduler, workload=workload,
                                   arm=arm, stream=stream,
                                   arrival='renewal' if stream < 4 else 'periodic_jitter',
                                   profile='final_si')
    elif stage == 'controls':
        # Four independent control seeds per scheduler, one for each arm.
        # Coverage is required across all four arms for each scheduler.
        for scheduler in SCHEDULERS:
            for workload in WORKLOADS:
                for stream, arm in enumerate(ARMS):
                    yield dict(stage=stage, scheduler=scheduler, workload=workload,
                               arm=arm, stream=stream, arrival='periodic_jitter',
                               profile='noise_free')
    else:
        raise ValueError('Only controls and the engineering pilot are implemented')


def timing(stage, workload='teleport'):
    if stage == 'pilot':
        return PILOT_RELEASE_END_NS, PILOT_WARMUP + PILOT_MEASURED, PILOT_WARMUP
    if stage == 'controls':
        if workload == 'plus':
            return PILOT_RELEASE_END_NS, PLUS_CONTROL_REQUESTS, 0
        return CONTROL_RELEASE_END_NS, CONTROL_REQUESTS, 0
    raise ValueError(stage)


def attacker_plan(*, arm, seed, release_end_ns):
    """Attacker boundary: arm, own seed, fixed public window. No victim arguments."""
    if arm not in ARMS or release_end_ns not in (PILOT_RELEASE_END_NS, CONTROL_RELEASE_END_NS):
        raise ValueError('Unknown public attacker configuration')
    rng = Draws(seed)
    rows = []
    if arm == 'none':
        return rows
    for period in range(release_end_ns // PERIOD_NS):
        if arm == 'random':
            offsets = sorted(int(rng.uniform() * 6_750_001) for _ in range(4))
        elif arm == 'even':
            offsets = [0, 2_250_000, 4_500_000, 6_750_000]
        else:
            offsets = [0, 0, 0, 0]
        for offset in offsets:
            rows.append(dict(index=len(rows), requested_ns=period*PERIOD_NS + offset,
                             release_period=period, instructions=INSTRUCTIONS,
                             requested_service_ns=BLOCK_CAP_NS))
    return rows


def private_inputs(spec):
    end, count, warmup = timing(spec['stage'], spec['workload'])
    identity = (spec['stage'], spec['workload'], spec['stream'])
    rng = Draws(seed_for('victim-arrivals', *identity))
    requests = []
    phase = int(rng.uniform()*20_000_000)
    t = 0
    for index in range(count):
        if spec['arrival'] == 'renewal':
            t += 5_000_000 + int(-math.log1p(-rng.uniform())*15_000_000)
        elif spec['arrival'] == 'periodic_jitter':
            # First slot centered 5 ms after the private phase avoids clipping.
            t = phase + 5_000_000 + index*20_000_000 + int(rng.uniform()*10_000_001)-5_000_000
        else:
            raise ValueError(spec['arrival'])
        if not 0 <= t < end:
            raise ValueError('Prespecified request falls outside fixed release window')
        requests.append(dict(index=index, offered_ns=t,
                             state=0 if spec['workload']=='plus' else (index+spec['stream']) % 6,
                             measured=spec['stage']=='pilot' and index >= warmup))
    rng = Draws(seed_for('benign-arrivals', *identity))
    benign = []
    t = 0
    while True:
        t += max(1, int(-math.log1p(-rng.uniform())*BLOCK_CAP_NS/BENIGN_LOAD))
        if t >= end:
            break
        benign.append(dict(index=len(benign), requested_ns=t, instructions=INSTRUCTIONS,
                           requested_service_ns=BLOCK_CAP_NS))
    return dict(requests=requests, benign=benign,
                simulator_seed=seed_for('simulator', *identity))


def inputs(spec):
    end, _, _ = timing(spec['stage'], spec['workload'])
    # Construct the complete attacker plan first, before private inputs exist.
    attacker_inputs = dict(arm=spec['arm'],
                           seed=seed_for('attacker', spec['stage'], spec['workload'], spec['stream']),
                           release_end_ns=end)
    attack = attacker_plan(**attacker_inputs)
    private = private_inputs(spec)
    return dict(attacker_inputs=attacker_inputs, attacker=attack, private=private,
                attacker_plan_sha256=stable(attack), pairing_sha256=stable(private),
                release_end_ns=end, horizon_ns=end+DRAIN_NS)


def outcome_category(completed, wrong, finished_ns, offered_ns):
    if not completed:
        return 'unfinished'
    if wrong not in (0, 1) or finished_ns is None or finished_ns < offered_ns:
        raise ValueError('Invalid completed output')
    if finished_ns-offered_ns > DEADLINE_NS:
        return 'completed_late'
    return 'wrong_on_time' if wrong else 'correct_on_time'


def protocol(hashes):
    return dict(revision=REVISION, kind='ENGINEERING PILOT ONLY', source_sha256=hashes,
        main_streams=192, measured_per_stream=8, measured_requests=1536,
        warmup_per_stream=2, warmup_requests=384,
        separate_noise_free_streams=24, separate_noise_free_requests=1248,
        total_simulator_executions=216, total_victim_requests=3168,
        arrival_assignment='Four renewal and four periodic-jitter streams per main cell; not a crossed 16-stream design.',
        controls='Teleport: 96 requests per arm/scheduler (1152 total). Plus: 8 per arm/scheduler (96 total). All six states and four Bell branches required across the four teleport arms of each scheduler; arm-level coverage also reported.',
        scheduler_policies=list(SCHEDULERS), arms=list(ARMS), workloads=list(WORKLOADS),
        attacker_period_ns=PERIOD_NS, attacker_budget_ns=BUDGET_NS, attacker_block_cap_ns=BLOCK_CAP_NS,
        tenants=list(TENANTS), quota_scope='Attacker tenant across all its PIDs. Victim and benign are separate, unbudgeted tenants.',
        cost_model='Each job is one CL block: 2 assignments + 28 adds, 10 us each, total 0.3 ms. No separate setup/output blocks. ALL tasks of an attacker PID are checked and charged. Unknown/oversized attacker tasks stop validation.',
        overhead='Submission, admission and dispatch have zero simulated cost in the inherited model; no hardware calibration is claimed.',
        scheduler_rule='FCFS readiness-discovery order; RR cyclic victim/benign/attacker, one eligible task per tenant turn, FIFO within tenant; PROTECTED first eligible ready live-state PID then FCFS.',
        enforcement='Reserve full verified cost at dispatch. Defer if tokens insufficient or block would cross period boundary. Preserve message/other-core wake-ups. Integrate actual CPU occupancy independently.',
        budget_note='Periodic accounting, not a sliding-window quota or a cgroup/CBS implementation.',
        attacker_boundary='Only own seed, arm and public fixed release window. No victim arrivals, PID, queue, state, measurement, deadline or storage event enters release generation. Scheduler may use live state only under QUOTA_PROTECTED.',
        template='fixed_burst = offsets 0,0,0,0. Engineering template, not learned/selected/optimal.',
        comparator_offsets_ns={'even':[0,2250000,4500000,6750000], 'random':'4 discrete uniforms on [0,6750000] ns, sorted per period'},
        benchmark=dict(one_way_latency_ns=LATENCY_NS, host_instruction_ns=HOST_INSTR_NS,
                       qnos_instruction_ns=1000, link_ns=1000, benign_offered_load=BENIGN_LOAD,
                       operations='Ideal gates and EPR; native T1/T2 off', primary_memory_profile=PROFILE,
                       state_assignment='Plus: +X. Teleport: six-state cycle; engineering coverage, not random population sampling.',
                       admission='FIFO serial victim admission until both peers finish; response starts at OFFER, including admission queue time.'),
        timing=dict(main_release_end_ns=PILOT_RELEASE_END_NS, control_release_end_ns=CONTROL_RELEASE_END_NS,
                    fixed_drain_ns=DRAIN_NS, deadline_ns=DEADLINE_NS,
                    deadline_scope='Inherited 20 ms engineering label. No operational SLO or calibrated Q16 deadline.'),
        gate='Implementation invariants and noiseless correctness only. No positive-effect, p-value or statistical power gate.',
        missing_data='Retain late and unfinished requests in offered denominators. Report incomplete/equal-work failures without dropping streams.',
        scope='No policy learning, confirmatory test, full-Q16 mode, or manuscript claim update. Existing Q12-Q15 folders are not modified.')
