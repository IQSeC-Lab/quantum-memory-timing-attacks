"""Additive diagnostic studies; no historical result or selector is fitted here."""
import hashlib
import json
import random

REVISION = 'p1-additional-diagnostics-v1'
N = {'causal': 512, 'robustness': 256}
POLICIES = ('QUOTA_FCFS', 'QUOTA_RR', 'QUOTA_EDF_NUMERIC', 'QUOTA_PROTECTED', 'QUOTA_NATIVE_CS')
ARMS = ('none', 'random', 'selected')
VARIANTS = ('unoptimized', 'optimized')
STREAM_OFFSET = 2_000_000

def stable(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def base():
    return dict(channel='combined', cost_ns=10000, t2_ns=4_000_000,
                latency_ns=155000, benign_per_epoch=8, budget_ns=1_200_000,
                phase_error_ns=0, phase_ns=250000)

def cells(family):
    if family == 'causal':
        return [dict(base(), name=c, channel=c) for c in ('ideal', 'memory_only', 'gate_only', 'combined')]
    if family != 'robustness':
        raise ValueError(family)
    out = [dict(base(), name='reference')]
    # One factor at a time: this is not a joint worst-case robustness envelope.
    for key, values in (
        ('cost_ns', (15, 1000)),
        ('t2_ns', (13_000_000, 10_000_000_000)),
        ('latency_ns', (1_000_000, 5_000_000)),
        ('benign_per_epoch', (0, 16)),
        ('budget_ns', (600000, 2400000)),
        ('phase_error_ns', (-1000000, -500000, 500000, 1000000)),
    ):
        for v in values:
            c = dict(base(), name=key+'_'+str(v)); c[key] = v; out.append(c)
    return out

def specs(family, index):
    for cell in cells(family):
        for variant in VARIANTS:
            for policy in POLICIES:
                for arm in ARMS:
                    yield dict(stage=REVISION+'-'+family, stream=STREAM_OFFSET+index,
                               profile='ideal' if cell['channel']=='ideal' else 'diagnostic',
                               cost_ns=cell['cost_ns'], variant=variant, policy=policy, arm=arm,
                               phase_ns=(cell['phase_ns']+cell['phase_error_ns']) % 4_000_000,
                               diagnostic_family=family, diagnostic_cell=cell)

def install(spec):
    """Install isolated per-worker design functions, restoring on every call.

    The retained native executor and independent trace auditor are unchanged.
    New settings/offer generators are versioned here and replayed by the audit.
    This is a new study, never a continuation of the old application family.
    """
    import app_design as d
    if not hasattr(d, '_diagnostic_originals'):
        d._diagnostic_originals = (d.settings, d.public_attacker_plan, d.plans, d.rng)
    settings0, public0, plans0, rng0 = d._diagnostic_originals
    cell = spec['diagnostic_cell']
    d.BUDGET_NS = cell['budget_ns']
    d.BLOCK_CAP_NS = 300000
    d.HOST_NS = cell['cost_ns']
    # Uniformly IID fresh streams. The same base RNG keys are used in every
    # cell/policy/arm; no outcome, density, live state or trace is a planner input.
    def rng(*keys):
        return random.Random(int(stable([REVISION, spec['diagnostic_family'], *keys])[:16], 16))
    d.rng = rng
    def settings(s):
        x = settings0(s)
        x.update(t2_ns=0 if cell['channel'] in ('ideal','gate_only') else cell['t2_ns'],
                 gate_fidelity=1.0 if cell['channel'] in ('ideal','memory_only') else .99,
                 latency_ns=cell['latency_ns'])
        return x
    def public(s):
        z = dict(s, stage='evaluation')
        return public0(z)
    def plans(s):
        # Parent plan keys must use evaluation to retain 12 quantum requests,
        # 8 competitor jobs and two periods; randomness is namespaced above.
        z = dict(s, stage='evaluation')
        original = plans0(z)
        original = [r for r in original if r['tenant'] != 'benign']
        r = rng('new-benign-offers', s['stream'])
        for epoch in range(2):
            for j in range(16):
                release = epoch*d.PERIOD_NS+r.randrange(0,8_000_000)
                if j < cell['benign_per_epoch']:
                    original.append(dict(kind='classical',tenant='benign',index=2000+epoch*16+j,
                        release_ns=release,absolute_deadline_ns=release+d.DEADLINE_NS,dataset=100+epoch*16+j))
        return sorted(original,key=lambda x:(x['release_ns'],x['index']))
    d.settings, d.public_attacker_plan, d.plans = settings, public, plans

def contrast_definitions(family):
    out=[]
    for c in cells(family):
        for v in VARIANTS:
            for p in POLICIES:
                for endpoint in ('infidelity','correct_timely','timely'):
                    out.append(dict(cell=c['name'],variant=v,policy=p,endpoint=endpoint,
                                    contrast='selected-minus-random'))
    if family=='causal':
        for v in VARIANTS:
            for p in POLICIES:
                for left,right in (('memory_only','ideal'),('combined','gate_only')):
                    out.append(dict(cell=left,reference_cell=right,variant=v,policy=p,
                                    endpoint='infidelity',contrast='difference-in-differences'))
    return out

def protocol(family, mode):
    return dict(revision=REVISION,family=family,mode=mode,N=2 if mode=='smoke' else N[family],
        full_N=N[family],cells=cells(family),policies=list(POLICIES),arms=list(ARMS),variants=list(VARIANTS),
        independent_unit='IID whole stream; 12 offered quantum requests, two public periods',
        contrasts=contrast_definitions(family),alpha=.05,
        inference='Diagnostic estimation, not a powered confirmation of sub-percentage-point effects; fixed N, no optional stopping',
        scope='Same n=2 rotation reconstruction; no new adaptive/entangled application or full upstream compiler/TUF reproduction',
        memory='Native Markovian phase noise. 13ms here is a sensitivity timescale, not the non-Markovian isotropic hardware-transfer channel.',
        work='Identical 30-op reductions within active-arm comparisons. Cost sweep changes service between cells; report this explicitly.',
        source_status='Native integration pending; no synthetic fallback')
