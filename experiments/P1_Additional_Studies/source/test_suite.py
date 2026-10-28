import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'source'),str(ROOT/'P1_Feedback_Closure/source'),str(ROOT/'P1_Feedback_Closure/source/application_parent')]
import study_design as d
import study_runner as runner
import app_design as parent

class DesignSafety(unittest.TestCase):
    def test_channel_interventions_retain_costs(self):
        got={}
        for s in d.specs('causal',0):
            if s['variant']=='unoptimized' and s['policy']=='QUOTA_FCFS' and s['arm']=='selected':
                d.install(s);got[s['diagnostic_cell']['channel']]=parent.settings(s)
        self.assertEqual({(v['gate_ns'],v['qnos_ns'],v['latency_ns'],v['host_ns']) for v in got.values()},{(26600,50000,155000,10000)})
        self.assertEqual((got['ideal']['t2_ns'],got['ideal']['gate_fidelity']),(0,1.))
        self.assertEqual((got['memory_only']['t2_ns'],got['memory_only']['gate_fidelity']),(4_000_000,1.))
        self.assertEqual((got['gate_only']['t2_ns'],got['gate_only']['gate_fidelity']),(0,.99))

    def test_public_plan_invariant_to_private_parameters(self):
        s=next(x for x in d.specs('causal',0) if x['arm']=='selected');d.install(s)
        p=parent.public_attacker_plan(s)
        other=dict(s,state='Y-',angle=31,private_victim_release=123)
        self.assertEqual(parent.public_attacker_plan(other),p)
        self.assertEqual(len(p),8)

    def test_active_arms_and_policies_match_private_offers(self):
        by_cell={}
        for s in d.specs('robustness',3):
            d.install(s);plan=parent.plans(s)
            victim=[r for r in plan if r['tenant']=='victim']
            private=[r for r in plan if r['tenant']!='attacker']
            self.assertEqual(len(victim),12)
            self.assertEqual(len([r for r in plan if r['tenant']=='benign']),2*s['diagnostic_cell']['benign_per_epoch'])
            by_cell.setdefault(s['diagnostic_cell']['name'],set()).add(d.stable(private))
        self.assertTrue(all(len(x)==1 for x in by_cell.values()))

    def test_module_state_does_not_leak_between_cells(self):
        source=list(d.specs('causal',4));a=source[0];b=source[-1]
        d.install(a);plan=parent.plans(a);settings=parent.settings(a)
        d.install(b);parent.plans(b)
        d.install(a)
        self.assertEqual(parent.plans(a),plan);self.assertEqual(parent.settings(a),settings)

    def test_missing_endpoints_never_drop_offered_requests(self):
        row=dict(outcomes=[dict(infidelity=None if i==0 else .1,category='unfinished' if i==0 else 'correct_on_time') for i in range(12)])
        self.assertIsNone(runner.value(row,'infidelity'))
        self.assertEqual(runner.value(row,'correct_timely'),11/12)
        self.assertEqual(runner.value(row,'timely'),11/12)

    def test_tampered_raw_checkpoint_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);runner.write(p/'raw.json.gz',[]);runner.write(p/'compact.json.gz',[])
            c=dict(index=0,raw='raw.json.gz',compact='compact.json.gz',raw_sha256=runner.digest(p/'raw.json.gz'),compact_sha256=runner.digest(p/'compact.json.gz'))
            runner.write(p/'checkpoints/000000.json',c);self.assertIsNotNone(runner.checkpoint(p,0))
            runner.write(p/'raw.json.gz',[{'changed':True}])
            with self.assertRaises(ValueError):runner.checkpoint(p,0)

    def test_worker_streams_raw_and_binds_checkpoint(self):
        import app_native, app_audit
        s=next(d.specs('causal',0))
        with tempfile.TemporaryDirectory() as t, patch.object(d,'specs',return_value=iter([s])), patch.object(app_native,'run',return_value={'raw_marker':7}), patch.object(app_audit,'validate_record',return_value={'compact_marker':8}):
            c=runner.worker(('causal',0,t));p=Path(t)
            with runner.gzip.open(p/c['raw'],'rt') as f:
                self.assertEqual(json.loads(f.readline()),{'raw_marker':7});self.assertEqual(f.readline(),'')
            self.assertEqual(runner.read(p/c['compact']),[{'compact_marker':8}])
            self.assertEqual(runner.checkpoint(p,0),c)

    def test_partial_native_failure_never_creates_success_checkpoint(self):
        import app_native, app_audit
        s=next(d.specs('causal',0))
        with tempfile.TemporaryDirectory() as t, patch.object(d,'specs',return_value=iter([s])), patch.object(app_native,'run',return_value={'status':'FAILED'}), patch.object(app_audit,'validate_record',side_effect=ValueError('deliberate failure')):
            with self.assertRaises(ValueError):runner.worker(('causal',0,t))
            self.assertIsNone(runner.checkpoint(Path(t),0))
            self.assertTrue((Path(t)/'failures/000000.json.gz').exists())

    def test_missing_native_stack_cannot_launch_simulations(self):
        with tempfile.TemporaryDirectory() as t, patch('closure_runner.importlib.import_module',side_effect=ImportError('missing')), patch.object(runner,'worker') as worker:
            with self.assertRaisesRegex(RuntimeError,'BLOCKED'):runner.run('causal','smoke',Path(t),1)
            worker.assert_not_called()

    def test_smoke_analysis_keeps_all_offered_and_never_infers_effect(self):
        # Offline compact fixtures exercise aggregation only, never physics.
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);p=d.protocol('causal','smoke');p['source_hashes']=runner.hashes()
            runner.write(folder/'protocol.json',p)
            for i in range(2):
                rows=[]
                for spec in d.specs('causal',i):
                    rows.append(dict(spec=spec,outcomes=[dict(category='correct_on_time',wrong=0,infidelity=0.) for j in range(12)],
                        attacker=[dict(complete=True) for j in range(8)],benign=[],max_live_owners=1,
                        all_attacker_work_completed=True,executed_attacker_service_ns=2400000,private_plan_sha256='offline-fixture'))
                rp='raw/%06d.json.gz'%i;cp='compact/%06d.json.gz'%i
                runner.write(folder/rp,[]);runner.write(folder/cp,rows)
                runner.write(folder/'checkpoints'/('%06d.json'%i),dict(index=i,raw=rp,compact=cp,raw_sha256=runner.digest(folder/rp),compact_sha256=runner.digest(folder/cp)))
            runner.write(folder/'COMPLETE.json',dict(protocol_sha256=runner.digest(folder/'protocol.json')))
            result=runner.analyze(folder)
            self.assertEqual(len(result['comparisons']),140)
            self.assertTrue(all(x['status']=='SMOKE_NO_EFFECT_INFERENCE' and 'lower' not in x for x in result['comparisons']))
            self.assertEqual(sum(x['offered'] for x in result['all_offered']),2880)

    def test_missing_compiler_is_explicit_block_not_comparison(self):
        import compiler_gate
        with tempfile.TemporaryDirectory() as t, patch('compiler_gate.shutil.which',return_value=None):
            r=compiler_gate.gate(Path(t))
            self.assertEqual(r['status'],'BLOCKED_MISSING_COMPILER');self.assertFalse(r['native_effect_comparison'])

if __name__=='__main__':unittest.main(verbosity=2)
