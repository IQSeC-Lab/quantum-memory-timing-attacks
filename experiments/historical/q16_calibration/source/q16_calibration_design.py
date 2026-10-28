"""Frozen Q16 calibration inputs only. There is no final evaluation mode."""
import math
from q16_design import (stable, Draws, SCHEDULERS, PERIOD_NS, BUDGET_NS,
                        BLOCK_CAP_NS, INSTRUCTIONS, BENIGN_LOAD, LATENCY_NS)

REVISION = 'q16-calibration-1'
PARENT_BUNDLE = '5cf47efdc05692414e728edc6e30cb00f7eb45e45ae2abeda31ba0e2a2151729'
ARRIVALS = ('renewal', 'periodic_jitter')
STAGES = ('benign', 'own_policy')
STREAMS = 32
WARMUP = 20
MEASURED = {'benign': 256, 'own_policy': 64}
RELEASE_END_NS = {'benign': 8_000_000_000, 'own_policy': 3_000_000_000}
DRAIN_NS = 200_000_000
TEMPLATES = tuple((g, o) for g in (0, 500_000, 1_000_000, 2_000_000)
                  for o in (0, 250_000, 500_000, 750_000))
FALLBACK_OFFSETS_NS = (0, 2_250_000, 4_500_000, 6_750_000)


def seed_for(domain, *identity):
    # Separate from all preceding pilot/stress/evaluation seed namespaces.
    return int(stable([161314, REVISION, domain, *identity])[:8], 16)


def template_name(template):
    return 'g%07d-o%07d' % tuple(template)


def specifications(stage=None):
    if stage is None:
        # All benign jobs are queued first, then own jobs in the SAME 8-worker
        # pool. Once a worker frees, it may start own simulation before the last
        # benign stream finishes. Scoring still waits for a frozen deadline.
        for which in STAGES:
            yield from specifications(which)
        return
    if stage not in STAGES:
        raise ValueError('Only benign and own_policy calibration are implemented')
    schedulers = SCHEDULERS if stage == 'benign' else ('QUOTA_FCFS',)
    templates = (None,) if stage == 'benign' else TEMPLATES
    for scheduler in schedulers:
        for arrival in ARRIVALS:
            for template in templates:
                for stream in range(STREAMS):
                    yield dict(stage=stage, scheduler=scheduler, workload='teleport',
                               arm='none' if template is None else 'template',
                               template=None if template is None else list(template),
                               stream=stream, arrival=arrival, profile='final_si',
                               application_owner='benign_calibration' if stage == 'benign'
                                                 else 'calibrator_own_application')


def check_spec(spec):
    stage = spec.get('stage')
    if stage not in STAGES or not 0 <= spec.get('stream', -1) < STREAMS:
        raise ValueError('Unknown calibration specification')
    expected = dict(stage=stage, scheduler=spec['scheduler'], workload='teleport',
                    arm='none' if stage == 'benign' else 'template',
                    template=spec.get('template'), stream=spec['stream'],
                    arrival=spec['arrival'], profile='final_si',
                    application_owner='benign_calibration' if stage == 'benign'
                                      else 'calibrator_own_application')
    if spec != expected or spec['arrival'] not in ARRIVALS:
        raise ValueError('Calibration schema differs')
    if stage == 'benign':
        if spec['scheduler'] not in SCHEDULERS or spec['template'] is not None:
            raise ValueError('Invalid benign cell')
    elif spec['scheduler'] != 'QUOTA_FCFS' or tuple(spec['template']) not in TEMPLATES:
        raise ValueError('Own policy training must use the fixed FCFS family')


def attacker_plan(*, template, seed, release_end_ns):
    """Only a public template, own seed, and public fixed window enter here."""
    if release_end_ns not in RELEASE_END_NS.values() or type(seed) is not int:
        raise ValueError('Invalid public plan configuration')
    if template is None:
        return []
    if tuple(template) not in TEMPLATES:
        raise ValueError('Template outside frozen family')
    g, o = template
    rows = []
    for period in range(release_end_ns // PERIOD_NS):
        for position in range(4):
            rows.append(dict(index=len(rows), requested_ns=period*PERIOD_NS+o+position*g,
                             release_period=period, instructions=INSTRUCTIONS,
                             requested_service_ns=BLOCK_CAP_NS))
    return rows


def private_inputs(spec):
    check_spec(spec)
    identity = (spec['stage'], spec['arrival'], spec['stream'])
    # Paired across candidate templates and across schedulers within a stage;
    # stages/regimes/streams have independent named seeds. State draws are iid
    # uniform over six target states and separate from arrival draws.
    rng = Draws(seed_for('application-arrivals', *identity))
    state_rng = Draws(seed_for('application-states', *identity))
    count = WARMUP + MEASURED[spec['stage']]
    end = RELEASE_END_NS[spec['stage']]
    phase = int(rng.uniform()*20_000_000)
    t = 0
    requests = []
    for index in range(count):
        if spec['arrival'] == 'renewal':
            t += 5_000_000 + int(-math.log1p(-rng.uniform())*15_000_000)
        else:
            t = phase+5_000_000+index*20_000_000+int(rng.uniform()*10_000_001)-5_000_000
        if not 0 <= t < end:
            raise ValueError('Fixed calibration arrival outside public window; do not resample')
        requests.append(dict(index=index, offered_ns=t, state=int(state_rng.uniform()*6),
                             measured=index >= WARMUP))
    rng = Draws(seed_for('background-arrivals', *identity))
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
    check_spec(spec)
    end = RELEASE_END_NS[spec['stage']]
    public = dict(template=spec['template'], seed=seed_for('attacker', spec['stage'], spec['stream']),
                  release_end_ns=end)
    # The complete classical release plan is generated BEFORE private arrivals,
    # states or simulator seeds exist. No deadline is an input to either plan.
    attack = attacker_plan(**public)
    private = private_inputs(spec)
    return dict(attacker_inputs=public, attacker=attack, private=private,
                attacker_plan_sha256=stable(attack), pairing_sha256=stable(private),
                release_end_ns=end, horizon_ns=end+DRAIN_NS)


def outcome_category(completed, wrong, finished_ns, offered_ns):
    # Deadline-independent recording: classification occurs after benign-only
    # calibration. The old engineering 20 ms label is intentionally absent.
    if not completed:
        return 'unfinished'
    if wrong not in (0, 1) or finished_ns is None or finished_ns < offered_ns:
        raise ValueError('Invalid completed output')
    return 'completed_wrong' if wrong else 'completed_correct'


def protocol(hashes):
    return dict(revision=REVISION, kind='CALIBRATION ONLY; NO FINAL EVALUATION',
        source_sha256=hashes, parent_pilot_bundle_sha256=PARENT_BUNDLE,
        simulator_executions=1216, benign_streams=192, own_policy_streams=1024,
        benign_measured_requests=49152, own_measured_requests=65536,
        warmup_requests=24320, total_requests=139008,
        streams_per_cell=STREAMS, warmup_per_stream=WARMUP, measured_per_stream=MEASURED,
        stages=list(STAGES), arrival_regimes=list(ARRIVALS), scheduler_policies=list(SCHEDULERS),
        primary_workload='Six-state teleportation; iid uniform state draws, serial admission.',
        own_training_scheduler='QUOTA_FCFS only; one selected template transfers unchanged later.',
        templates_ns=[list(t) for t in TEMPLATES], fallback_offsets_ns=list(FALLBACK_OFFSETS_NS),
        quota=dict(period_ns=PERIOD_NS, budget_ns=BUDGET_NS, block_cap_ns=BLOCK_CAP_NS,
                   scope='All attacker PIDs; unbudgeted separate benign/application tenants.'),
        inherited_model=dict(host_instruction_ns=10000, instructions_per_job=30,
            qnos_instruction_ns=1000, link_ns=1000, one_way_latency_ns=LATENCY_NS,
            operations='Ideal gates/EPR; native T1/T2 off; inherited final_si channel13ms exponent1.67.',
            overhead='Admission, submission and dispatch zero modeled cost; no hardware calibration.',
            nominal_benign_load=BENIGN_LOAD),
        fixed_timing=dict(release_end_ns=RELEASE_END_NS, drain_ns=DRAIN_NS,
            note='No deadline-dependent horizon, early stopping, reseeding or outcome-based expansion.'),
        information_boundary='Own template/seed/public window only; plan precedes private arrivals. '
            'Calibration labels concern the calibrator own application, never final victim evaluation.',
        pairing='Within each stage/arrival/stream, common arrivals, states, background and simulator seed '
                'across templates or schedulers. Different stages use disjoint seed domains.',
        deadline_rule=dict(cohort='All192 benign streams only;256 measured responses per stream.',
            empirical_p99='Nearest rank x_(ceil(0.99*n)), one-indexed; n=256, rank=254.',
            rule='ceil((6/5)*max(per-stream p99)/100000)*100000 ns.',
            completion='Every offered calibration request including warmup and every background job '
                       'must finish by fixed horizon; otherwise deadline_status=FAILED.',
            interpretation='Research threshold, not an operational SLA.'),
        own_selection=dict(score='Equal-weight mean of the two regimes wrong-and-on-time/offered rates.',
            denominator='All64 offered measured own requests in every stream; fixed32 streams per regime.',
            eligibility='Independent audits pass; all offered requests including warmup and all jobs finish; '
                        'exact requested/executed attacker work; empirical on-time fraction>=0.99 in EACH regime.',
            service_scope='Descriptive training constraint only; no inferential final service certification.',
            tie_break='Ascending(g_ns,offset_ns); exact integer score comparison.',
            no_feasible='Record selection_success=false; selected offsets=prespecified even fallback.'),
        stopping='Any code/source/audit violation halts with raw evidence preserved. Valid incomplete '
                 'outcomes are retained and calibration reports failure/ineligibility; no automatic retuning.',
        deliverable='Deadline receipt, full candidate table, selected-policy receipt and next-freeze checklist.',
        claims='No confirmatory attack-effect inference, hardware validation, acceptance prediction or final study launch.')
