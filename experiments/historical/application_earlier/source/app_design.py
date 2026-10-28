"""Prospective application comparison. No earlier result directory is read.

The public release plan is generated independently of private victim inputs.
Published parameters and deliberately changed parameters are named separately.
"""
import hashlib
import random

from app_io import normalize
import json

REVISION = 'p1-rq13-rotation-application-v1'
PIN = '31fa2c9699578d227736557f35cd01a134c4bbde'
POLICIES = ('QUOTA_FCFS','QUOTA_RR','QUOTA_EDF_NUMERIC','QUOTA_PROTECTED','QUOTA_NATIVE_CS')
TENANTS = ('victim','benign','attacker')
ARMS = ('none','random','even','selected')
VARIANTS = ('unoptimized','optimized')
PROFILES = ('paper_default','short_memory')
COSTS = (15,1000,10000)
PERIOD_NS = 10_000_000
BUDGET_NS = 1_200_000
BLOCK_CAP_NS = 300_000
HOST_NS = 10000  # Set from the specification in each isolated native worker.
DEADLINE_NS = 5_000_000
HORIZON_NS = 70_000_000
PHASES_NS = tuple(range(0,4_000_000,250_000))
STATES = ('Z+','Z-','X+','X-','Y+','Y-')

def stable(value):
    return hashlib.sha256(json.dumps(normalize(value),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def rng(*keys):
    return random.Random(int(stable([REVISION,*keys])[:16],16))

def settings(spec):
    return dict(host_ns=spec['cost_ns'],qnos_ns=50000,gate_ns=26600,
        gate_fidelity=1.0 if spec['profile']=='ideal' else .99,
        t2_ns=0 if spec['profile']=='ideal' else (10_000_000_000 if spec['profile']=='paper_default' else 4_000_000),
        latency_ns=155000,host_peer_ns=150,internal_sched_ns=60,num_qubits=6)

def seed(spec):
    # Matched exogenous streams; native measurement RNG consumption can differ
    # with scheduling. We do not claim paired Bernoulli random-number identity.
    return int(stable([spec['stage'],spec['stream'],spec.get('trial',0)])[:8],16)

def cells(mode):
    profiles = PROFILES if mode=='full' else ('short_memory',)
    costs = COSTS if mode=='full' else (10000,)
    return [(p,c,v) for p in profiles for c in costs for v in VARIANTS]

def preflight_specs():
    for profile in ('ideal','paper_default'):
        for variant in VARIANTS:
            for state in STATES:
                for angle in (1,8,15,31):
                    yield dict(stage='preflight',stream=0,profile=profile,cost_ns=15,variant=variant,
                        policy='QUOTA_FCFS',arm='none',state=state,angle=angle,phase_ns=0)

def discovery_specs(mode):
    trials = 24 if mode=='full' else 6
    for phase in PHASES_NS:
        for trial in range(trials):
            yield dict(stage='discovery',stream=trial,trial=trial,profile='short_memory',cost_ns=10000,
                variant='unoptimized',policy='QUOTA_FCFS',arm='selected',phase_ns=phase)

def evaluation_specs(mode,phase):
    streams = 32 if mode=='full' else 2
    for profile,cost,variant in cells(mode):
        for stream in range(streams):
            for policy in POLICIES:
                for arm in ARMS:
                    yield dict(stage='evaluation',stream=stream,profile=profile,cost_ns=cost,variant=variant,
                        policy=policy,arm=arm,phase_ns=phase)

def public_attacker_plan(spec):
    """This function cannot read private victim releases, states, or outputs."""
    if spec['arm']=='none': return []
    periods = 2 if spec['stage']=='evaluation' else 1
    out=[]
    r=rng('public-attacker',spec['stage'],spec['stream'])
    for epoch in range(periods):
        if spec['arm']=='random': offsets=[r.randrange(0,4_000_000)]*4
        elif spec['arm']=='even': offsets=[0,2_500_000,5_000_000,7_500_000]
        else: offsets=[spec['phase_ns']]*4
        for j,offset in enumerate(offsets):
            out.append(dict(kind='classical',tenant='attacker',index=1000+epoch*4+j,
                release_ns=epoch*PERIOD_NS+offset,absolute_deadline_ns=epoch*PERIOD_NS+20_000_000,
                dataset=epoch*4+j))
    return out

def plans(spec):
    # Build and hash public offers first; only then draw private workloads.
    out=public_attacker_plan(spec)
    r=rng('private',spec['stage'],spec['stream'])
    if spec['stage']=='preflight':
        private=[(0,spec['state'],spec['angle'])]
    elif spec['stage']=='discovery':
        private=[(r.randrange(0,4_000_000),STATES[spec['trial']%6],r.randrange(1,32))]
    else:
        private=[]
        for epoch in range(2):
            states=list(STATES);r.shuffle(states)
            for state in states:
                private.append((epoch*PERIOD_NS+r.randrange(0,4_000_000),state,r.randrange(1,32)))
    for i,(release,state,a) in enumerate(private):
        out.append(dict(kind='quantum',tenant='victim',index=i,release_ns=release,
            absolute_deadline_ns=release+DEADLINE_NS,state=state,angle=a))
    if spec['stage']=='evaluation':
        for epoch in range(2):
            for j in range(8):
                release=epoch*PERIOD_NS+r.randrange(0,8_000_000)
                out.append(dict(kind='classical',tenant='benign',index=2000+epoch*8+j,release_ns=release,
                    absolute_deadline_ns=release+DEADLINE_NS,dataset=100+epoch*8+j))
    return sorted(out,key=lambda x:(x['release_ns'],x['index']))

def input_map(row):
    return {'v%d'%i:int(stable(['sum-input',row['dataset'],i])[:8],16)%20001-10000 for i in range(28)}

def classical_program(row=None):
    params=', '.join('v%d'%i for i in range(28))
    ops=['total = assign_cval() : 0']+['total = add_cval_c(total, v%d)'%i for i in range(28)]+['return_result(total)']
    return 'META_START\n name: runtime_reduction\n parameters: '+params+'\n csockets:\n epr_sockets:\nMETA_END\n^reduce_0 {type = CL}:\n'+'\n'.join('    '+o for o in ops)+'\n'

def state_gates(state):
    prep={'Z+':[], 'Z-':['x Q0'], 'X+':['h Q0'], 'X-':['x Q0','h Q0'],
          'Y+':['rot_x Q0 3 1'],'Y-':['rot_x Q0 1 1']}[state]
    inverse={'Z+':[], 'Z-':['x Q0'], 'X+':['h Q0'], 'X-':['h Q0','x Q0'],
             'Y+':['rot_x Q0 1 1'],'Y-':['rot_x Q0 3 1']}[state]
    return prep,inverse

def rotation_programs(spec,row):
    """Independent implementation of the published n=2 protocol, not source copy.

    Both angle values arrive through actual classical messages. The optimized
    server sums them before it initializes its quantum state.
    """
    prep,inverse=state_gates(row['state'])
    meta='META_START\n name: bob\n parameters: alice_id\n csockets: 0 -> alice\n epr_sockets:\nMETA_END\n'
    blocks=[('setup','CL',['csocket = assign_cval() : 0'])]
    routines=[]
    def rt(name,body,params='',returns='',keep=True):
        return ('SUBROUTINE '+name+'\n    params: '+params+'\n    returns: '+returns+
            '\n    uses: 0\n    keeps: '+('0' if keep else '')+'\n    request:\n  NETQASM_START\n'+
            '\n'.join('    '+x for x in body)+'\n  NETQASM_END\n')
    load=['set C15 0','load R0 @input[C15]','rot_x Q0 R0 4']
    finish=inverse+['meas Q0 M0','store M0 @output[0]']
    if spec['variant']=='unoptimized':
        blocks.append(('prepare','QL',['run_subroutine() : prepare_state']))
        routines.append(rt('prepare_state',['set Q0 0','init Q0']+prep))
        for i in range(2):
            blocks.append(('receive%d'%i,'CC',['a%d = recv_cmsg(csocket)'%i]))
            blocks.append(('rotate%d'%i,'QL',['run_subroutine(tuple<a%d>) : rotate_state'%i]))
        routines.append(rt('rotate_state',['set Q0 0']+load,params='angle'))
        blocks.append(('verify','QL',['tuple<m> = run_subroutine() : verify_state']))
        routines.append(rt('verify_state',['set Q0 0']+finish,returns='m',keep=False))
    else:
        for i in range(2):blocks.append(('receive%d'%i,'CC',['a%d = recv_cmsg(csocket)'%i]))
        blocks.append(('sum_angles','CL',['theta = add_cval_c(a0, a1)']))
        blocks.append(('merged','QL',['tuple<m> = run_subroutine(tuple<theta>) : merged_state']))
        routines.append(rt('merged_state',['set Q0 0','init Q0']+prep+load+finish,params='angle',returns='m',keep=False))
    blocks.append(('output','CL',['return_result(m)']))
    use_cs=spec['policy']=='QUOTA_NATIVE_CS'
    protected={'prepare','receive0','rotate0','receive1','rotate1','verify'} if spec['variant']=='unoptimized' else {'merged'}
    if use_cs:meta=meta.replace('META_END',' critical_sections: 0 -> E\nMETA_END')
    for name,typ,ops in blocks:
        cs='; critical_section = 0' if use_cs and name in protected else ''
        meta+='^'+name+' {type = '+typ+cs+'}:\n'+'\n'.join('    '+o for o in ops)+'\n'
    server=meta+''.join(routines)
    client='''META_START
 name: alice
 parameters: bob_id, a0, a1
 csockets: 0 -> bob
 epr_sockets:
META_END
^send {type = CL}:
    csocket = assign_cval() : 0
    send_cmsg(csocket, a0)
    send_cmsg(csocket, a1)
'''
    return server,client

def protocol(mode,hashes):
    return dict(revision=REVISION,mode=mode,kind='EXPLORATORY MAIN APPLICATION COMPARISON; NOT Q16 CONFIRMATION',
        source_sha256=hashes,policies=list(POLICIES),arms=list(ARMS),cells=cells(mode),
        streams_per_cell=32 if mode=='full' else 2,requests_per_stream=12,
        preflight_simulations=96,discovery_simulations=384 if mode=='full' else 96,
        evaluation_simulations=len(cells(mode))*20*(32 if mode=='full' else 2),
        horizon_ns=HORIZON_NS,deadline_ns=DEADLINE_NS,period_ns=PERIOD_NS,budget_ns=BUDGET_NS,block_cap_ns=BLOCK_CAP_NS,
        own_calibration='16 phases; own sampled wrong-output counts only; earliest-phase tie. One fixed short-memory/10000ns/unoptimized/FCFS training setting, transferred unchanged.',
        information='Public 10ms epoch; no individual victim releases, states, traces or outputs in release-plan generation.',
        work='Identical 28-input reduction (30 host operations), 8 attacker jobs per evaluation stream. Service varies across costs; never padded or resized.',
        EDF='Native numerical application absolute deadline minus now; stable FIFO tie; attacker deadline fixed public epoch+20ms.',
        CS='Native E section covers live-state part; output is outside. Annotation is a manual compilation strategy; no original compiler executable is run.',
        baseline='Independent n=2 rotation-application reconstruction, Table I/Appendix A parameter slice (15ns CPS,50us QNOS,26.6us gates,fidelity .99,T2 10s,155us link,150ns message processing,60ns scheduler messaging). Not original figure replication.',
        sensitivity='1000/10000ns host costs and 4ms memory are separate model assumptions, not source hardware configurations or measurements.',
        inference='Stream-paired exploratory effect estimates and simultaneous conservative bounded-mean intervals. No powered confirmatory claim or automatic success from p-values.',
        service='Prospective diagnostic requirement: on-time>=.99 and loss<.005 against both random/even. This does not alter or reuse Q16 confirmation.',
        physical_timing_validated=False,native_validation_at_delivery='PENDING HPC')
