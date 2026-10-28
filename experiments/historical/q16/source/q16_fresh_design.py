"""Fixed-policy Q16 nuisance-planning study. No final evaluation is implemented."""
import json, math
from pathlib import Path
from q16_design import (stable, Draws, PERIOD_NS, BUDGET_NS, BLOCK_CAP_NS,
                       INSTRUCTIONS, BENIGN_LOAD, LATENCY_NS)
from q16_calibration_design import outcome_category

REVISION='q16-fresh-planning-1'
SEED_NAMESPACE=161320
ARRIVALS=('renewal','periodic_jitter')
ARMS=('selected','random','even')
STREAMS=32
WARMUP=20
MEASURED={'planning':256}
RELEASE_END_NS={'planning':8_000_000_000}
DRAIN_NS=200_000_000
DEADLINE_NS=5_000_000
SELECTED_OFFSETS_NS=(250_000,)*4
EVEN_OFFSETS_NS=(0,2_250_000,4_500_000,6_750_000)
RANDOM_LAST_NS=6_750_000

def seed_for(domain,*identity):
    return int(stable([SEED_NAMESPACE,REVISION,domain,*identity])[:8],16)

def specifications():
    for arrival in ARRIVALS:
        for arm in ARMS:
            for stream in range(STREAMS):
                yield dict(stage='planning',scheduler='QUOTA_FCFS',workload='teleport',arm=arm,
                    template=[0,250_000] if arm=='selected' else None,stream=stream,arrival=arrival,
                    profile='final_si',application_owner='fresh_planning_application')

def check_spec(spec):
    if spec not in list(specifications()): raise ValueError('Outside frozen planning grid')

def attacker_plan(*,arm,seed,release_end_ns):
    if arm not in ARMS or type(seed) is not int or release_end_ns!=8_000_000_000:
        raise ValueError('Invalid public attack-plan inputs')
    rng=Draws(seed);rows=[]
    for period in range(release_end_ns//PERIOD_NS):
        offsets=(SELECTED_OFFSETS_NS if arm=='selected' else EVEN_OFFSETS_NS if arm=='even' else
                 sorted(int(rng.uniform()*(RANDOM_LAST_NS+1)) for _ in range(4)))
        for offset in offsets:
            rows.append(dict(index=len(rows),requested_ns=period*PERIOD_NS+offset,
                release_period=period,instructions=INSTRUCTIONS,requested_service_ns=BLOCK_CAP_NS))
    return rows

def private_inputs(spec):
    check_spec(spec);identity=('planning',spec['arrival'],spec['stream'])
    rng=Draws(seed_for('application-arrivals',*identity));state_rng=Draws(seed_for('application-states',*identity))
    phase=int(rng.uniform()*20_000_000);requests=[];t=0
    for index in range(WARMUP+MEASURED['planning']):
        t=(t+5_000_000+int(-math.log1p(-rng.uniform())*15_000_000) if spec['arrival']=='renewal' else
           phase+5_000_000+index*20_000_000+int(rng.uniform()*10_000_001)-5_000_000)
        if not 0<=t<RELEASE_END_NS['planning']:
            raise ValueError('Arrival outside fixed window; preserve evidence and do not resample')
        requests.append(dict(index=index,offered_ns=t,state=int(state_rng.uniform()*6),measured=index>=WARMUP))
    rng=Draws(seed_for('background-arrivals',*identity));benign=[];t=0
    while True:
        t+=max(1,int(-math.log1p(-rng.uniform())*BLOCK_CAP_NS/BENIGN_LOAD))
        if t>=RELEASE_END_NS['planning']: break
        benign.append(dict(index=len(benign),requested_ns=t,instructions=INSTRUCTIONS,requested_service_ns=BLOCK_CAP_NS))
    return dict(requests=requests,benign=benign,simulator_seed=seed_for('simulator',*identity))

def inputs(spec):
    check_spec(spec)
    public=dict(arm=spec['arm'],seed=seed_for('attacker','planning',spec['stream']),release_end_ns=RELEASE_END_NS['planning'])
    # Victim/background inputs do not exist when the public release plan is built.
    attack=attacker_plan(**public);private=private_inputs(spec)
    return dict(attacker_inputs=public,attacker=attack,private=private,
        attacker_plan_sha256=stable(attack),pairing_sha256=stable(private),
        release_end_ns=RELEASE_END_NS['planning'],horizon_ns=RELEASE_END_NS['planning']+DRAIN_NS)

def binding(): return json.loads((Path(__file__).resolve().parent/'q16_calibration_binding.json').read_text())

def protocol(hashes):
    return dict(revision=REVISION,kind='NUISANCE PLANNING ONLY; NO CONFIRMATORY EVALUATION',source_sha256=hashes,
        calibration_binding=binding(),simulations=192,streams_per_regime=32,independent_paired_input_streams=64,
        arms=list(ARMS),arrival_regimes=list(ARRIVALS),scheduler='QUOTA_FCFS',workload='six-state teleportation',
        warmup=20,measured_per_stream=256,measured_requests=49152,all_requests=52992,
        deadline_ns=DEADLINE_NS,selected_offsets_ns=list(SELECTED_OFFSETS_NS),even_offsets_ns=list(EVEN_OFFSETS_NS),
        random_offsets='Four iid integer offsets in [0,6750000] per period, sorted; own seed only.',
        periods=800,jobs_per_period=4,period_ns=PERIOD_NS,budget_ns=BUDGET_NS,block_cap_ns=BLOCK_CAP_NS,
        release_end_ns=RELEASE_END_NS['planning'],fixed_drain_ns=DRAIN_NS,
        physics='Inherited bridge: 10000ns host instructions, ideal gates/EPR, final_si memory; not hardware calibrated.',
        planned_outputs=['Arm-level service and joint outcome fractions','Centered paired-stream Y/O variance',
                         'Compute, peak memory and raw storage costs','Complete data for sample-size design'],
        policy_selection='Frozen existing policy; no retraining, replacement, new template or deadline adjustment.',
        inference='No significance or confirmatory success gate. Signed effect diagnostics cannot set final N.',
        independent_unit='Whole input stream; 3 arms reuse pairing. Requests within a stream are not independent replicates.',
        final_N=None,final_confidence_method=None,
        future_family='Two FCFS arrival regimes x selected-minus-random/even; requires Y advantage, O>=.99 and deltaO>-.005.',
        stopping='Fixed grid. Stop only for engineering errors; preserve failures, no outcome-driven extension.')
