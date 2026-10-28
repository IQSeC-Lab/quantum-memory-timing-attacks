"""Offline checks of scientific invariants, audit rejection and recovery.

Native application execution is deliberately not mocked as a successful run.
"""
import ast
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import app_design as d
from app_policy import Policy
from app_io import put,sha,source_hashes
from app_cli import verified_checkpoint,smoke_specs
from app_scheduler_audit import validate_scheduler
from app_analysis import freeze_selection,measures,summarize
from app_audit import target,density

HERE=Path(__file__).parent

def legacy():
    raw=json.loads((HERE/'legacy_scheduler_fixture.json').read_text())
    owners={r['pid']:r for r in raw['admissions']}
    costs={}
    for p in raw['programs']:
        if p['node']=='bob' and owners[p['pid']]['kind']=='classical':
            for b in p['blocks']:costs[p['pid'],b['name']]=len(b['op_types'])*10000
    return raw,owners,costs

def gate(text):
    I=np.eye(2);X=np.array([[0,1],[1,0]]);H=np.array([[1,1],[1,-1]])/np.sqrt(2)
    if text.startswith('x '):return X
    if text.startswith('h '):return H
    _,_,n,k=text.split();a=int(n)*math.pi/2**int(k)
    return math.cos(a/2)*I-1j*math.sin(a/2)*X

class Checks(unittest.TestCase):
    def test_full_grid(self):
        specs=list(d.evaluation_specs('full',250000))
        self.assertEqual(len(specs),7680)
        self.assertEqual(len({d.stable(s) for s in specs}),len(specs))
        self.assertEqual(len(list(d.preflight_specs())),96)
        self.assertEqual(len(list(d.discovery_specs('full'))),384)
        self.assertEqual(len(smoke_specs()),10)

    def test_pilot_separate(self):
        self.assertEqual(len(list(d.evaluation_specs('pilot',0))),80)
        self.assertNotEqual(d.protocol('full',{}),d.protocol('pilot',{}))

    def test_private_pairing(self):
        hashes=set()
        for s in d.evaluation_specs('full',250000):
            if s['stream']!=3:continue
            hashes.add(d.stable([r for r in d.plans(s) if r['tenant']!='attacker']))
        self.assertEqual(len(hashes),1)

    def test_public_plan_blind(self):
        original=d.rng
        def public_rng(*keys):
            if keys[0]!='public-attacker':raise AssertionError('Private information accessed')
            return original(*keys)
        spec=next(s for s in d.evaluation_specs('full',250000) if s['arm']=='random')
        with patch.object(d,'rng',public_rng):self.assertEqual(len(d.public_attacker_plan(spec)),8)

    def test_equal_offered_work(self):
        for cost in d.COSTS:
            groups=[]
            for arm in ('selected','random','even'):
                s=dict(stage='evaluation',stream=1,arm=arm,phase_ns=250000)
                rows=d.public_attacker_plan(s)
                groups.append([(r['index'],d.input_map(r),d.classical_program(r)) for r in rows])
                self.assertLessEqual(30*cost,d.BLOCK_CAP_NS)
            self.assertEqual(groups[0],groups[1]);self.assertEqual(groups[1],groups[2])

    def test_no_padding_or_runtime_constants(self):
        text=d.classical_program()
        self.assertEqual(text.count('add_cval_c'),28)
        self.assertNotIn('busy',text.lower());self.assertNotIn('sleep',text.lower())
        a=d.input_map(dict(dataset=0));b=d.input_map(dict(dataset=1))
        self.assertNotEqual(a,b);self.assertNotEqual(sum(a.values()),sum(b.values()))

    def test_ideal_rotation_oracle(self):
        for state in d.STATES:
            prep,inv=d.state_gates(state);psi=np.array([1,0],complex)
            for g in prep:psi=gate(g)@psi
            self.assertGreater(abs(np.vdot(target(state),psi))**2,1-1e-12)
            for a in range(1,32):
                for angles in ([a,32-a],[32]):
                    x=psi.copy()
                    for v in angles:x=gate('rot_x Q0 %d 4'%v)@x
                    for g in inv:x=gate(g)@x
                    self.assertGreater(abs(x[0])**2,1-1e-12)

    def test_published_slice(self):
        p=d.settings(dict(profile='paper_default',cost_ns=15))
        self.assertEqual([p[k] for k in ('t2_ns','qnos_ns','gate_ns','latency_ns','host_peer_ns','internal_sched_ns')],
            [10000000000,50000,26600,155000,150,60])
        self.assertEqual(p['gate_fidelity'],.99)

    def test_hand_constructed_compiler_semantics(self):
        row=dict(state='X+')
        a,_=d.rotation_programs(dict(variant='unoptimized',policy='QUOTA_FCFS'),row)
        b,_=d.rotation_programs(dict(variant='optimized',policy='QUOTA_NATIVE_CS'),row)
        self.assertLess(a.index('^prepare'),a.index('^receive0'))
        self.assertLess(b.index('^receive1'),b.index('^merged'))
        self.assertIn('theta = add_cval_c(a0, a1)',b)
        self.assertIn('^merged {type = QL; critical_section = 0}:',b)
        self.assertIn('^output {type = CL}:',b)

    def test_numeric_edf_and_budget(self):
        d.HOST_NS=10000
        p=Policy('QUOTA_EDF_NUMERIC')
        x=p.decide(0,[1,2],{1:(1,'attacker'),2:(2,'victim')},set(),{1:300000},{1:9000000,2:5000000})
        self.assertEqual(x['selected'],2)
        p=Policy('QUOTA_FCFS')
        for i in range(4):
            row=p.decide(i*300000,[i],{i:(i,'attacker')},set(),{i:300000},{i:20000000})
            self.assertEqual(row['selected'],i)
        x=p.decide(1200000,[9],{9:(9,'attacker')},set(),{9:300000},{9:20000000})
        self.assertIsNone(x['selected']);self.assertEqual(x['wake_ns'],10000000)

    def test_legacy_native_scheduler_replay(self):
        d.HOST_NS=10000
        raw,owners,costs=legacy();validate_scheduler(raw,owners,costs)

    def test_corrupt_deadline_rejected(self):
        raw,o,c=legacy();raw['decisions'][0]['absolute_deadlines_ns'][str(raw['decisions'][0]['ready'][0])]+=1
        with self.assertRaises(ValueError):validate_scheduler(raw,o,c)

    def test_corrupt_cpu_service_rejected(self):
        raw,o,c=legacy();raw['cpu_trace'][0]['end_ns']+=1
        with self.assertRaises(ValueError):validate_scheduler(raw,o,c)

    def test_corrupt_ownership_rejected(self):
        raw,o,c=legacy();raw['decisions'][0]['live_pids']=[999]
        with self.assertRaises(ValueError):validate_scheduler(raw,o,c)

    def test_density_rejects_nonphysical(self):
        with self.assertRaises(ValueError):density(dict(real=[[1.2,0],[0,-.2]],imag=[[0,0],[0,0]]))

    def test_missing_density_not_dropped(self):
        r=dict(outcomes=[dict(category='correct_on_time',infidelity=.1),dict(category='unfinished',infidelity=None)],benign=[])
        m=measures(r);self.assertEqual(m['infidelity_lower'],.05);self.assertEqual(m['infidelity_upper'],.55)
        self.assertEqual(m['unfinished'],.5)

    def test_discovery_uses_own_bits_only(self):
        records=[]
        for s in d.discovery_specs('pilot'):
            records.append(dict(spec=s,all_jobs_completed=True,outcomes=[dict(wrong=int(s['phase_ns']==500000))]))
        result=freeze_selection(records,'pilot');self.assertEqual(result['selected_phase_ns'],500000)
        for r in records:r['outcomes'][0]['wrong']=0
        self.assertEqual(freeze_selection(records,'pilot')['selected_phase_ns'],0)
        records[0]['all_jobs_completed']=False
        with self.assertRaises(ValueError):freeze_selection(records,'pilot')

    def test_checkpoint_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);job=dict(spec={'x':1})
            for kind,data in [('raw',{}),('compact',dict(spec=job['spec'])),('statistics',{})]:put(root/kind,data)
            marker=dict(job_sha256=d.stable(job))
            for kind in ('raw','compact','statistics'):
                marker[kind+'_file']=kind;marker[kind+'_sha256']=sha(root/kind)
            put(root/'completed.json',marker);verified_checkpoint(root,job)
            put(root/'compact',dict(spec={'x':2}))
            with self.assertRaises(RuntimeError):verified_checkpoint(root,job)

    def test_main_analysis_and_incomplete_work_flag(self):
        records=[]
        for s in d.evaluation_specs('pilot',250000):
            plan=d.plans(s)
            records.append(dict(spec=s,outcomes=[dict(category='correct_on_time',infidelity=.01,
                complete=True,response_ns=1000,live_age_ns=500) for _ in range(12)],
                benign=[dict(complete=True,response_ns=1000)],max_live_owners=2,
                private_plan_sha256=d.stable([r for r in plan if r['tenant']!='attacker']),
                executed_attacker_service_ns=0 if s['arm']=='none' else 2400000,
                all_attacker_work_completed=True))
        result=summarize(records,'pilot',250000)
        self.assertTrue(result['equal_executed_attacker_work_verified'])
        self.assertEqual(result['quantum_requests'],960)
        self.assertEqual(len(result['primary']),2)
        active=next(r for r in records if r['spec']['arm']=='selected')
        active['all_attacker_work_completed']=False
        self.assertFalse(summarize(records,'pilot',250000)['equal_executed_attacker_work_verified'])

    def test_python310_syntax(self):
        for p in HERE.glob('*.py'):ast.parse(p.read_text(),feature_version=(3,10))

if __name__=='__main__':unittest.main(verbosity=2)
