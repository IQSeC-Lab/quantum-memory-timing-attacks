"""Engineering tests only; passing does not validate native execution."""
import copy
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import unittest

import closure_design as d
import closure_runner as runner
from closure_stats import interval,planned_radius
from public_policy import plan,ARMS,PHASES

class EngineeringChecks(unittest.TestCase):
    def public(self,arm='learned'):
        return dict(epoch_ns=3000000,window_halfwidth_ns=500000,arm=arm,
            frozen_phase_ns=250000,own_random_seed=127)

    def test_public_schema(self):
        p=self.public();before=copy.deepcopy(p)
        for arm in ARMS:
            p['arm']=arm;offers=plan(p)
            self.assertEqual(len(offers),0 if arm=='none' else 4)
            self.assertTrue(all(x['operations']==30 and x['release_ns']>=3000000 for x in offers))
        p=before;p['target_state']='X+'
        with self.assertRaises(ValueError):plan(p)

    def test_standalone_planner(self):
        p=self.public()
        out=subprocess.check_output([sys.executable,str(d.ROOT/'source/public_policy.py')],input=json.dumps(p),text=True)
        self.assertEqual(json.loads(out),plan(p))

    def test_fixed_grid(self):
        for i,phase in enumerate(PHASES):
            offsets={r['release_ns']-3000000 for r in plan(self.public('fixed_%02d'%i))}
            self.assertEqual(offsets,{phase})

    def test_private_pairing_and_fresh_namespace(self):
        sys.path.insert(0,str(d.ROOT/'source/primary_parent'))
        import q12_design as old
        for w in ('plus','teleport'):
            for i in range(12):
                inputs=[]
                for arm in ARMS:
                    public,parent=d.public_for(w,i,arm,'full')
                    self.assertGreaterEqual(parent['shot'],1000000)
                    self.assertIn(public['frozen_phase_ns'],PHASES)
                    inputs.append(old.inputs(parent))
                self.assertTrue(all(x==inputs[0] for x in inputs))

    def test_interval_formula(self):
        x=[-1.,0.,1.,.5];k=6;z=math.log(4*k/.05)
        expected=math.sqrt(2*statistics.variance(x)*z/4)+14*z/(3*3)
        r=interval(x,k)
        self.assertAlmostEqual(r['radius'],expected)
        self.assertEqual((r['lower'],r['upper']),(-1.,1.))
        self.assertGreater(planned_radius(1000,.1,36),planned_radius(1000,.1,6))
        for bad in ([float('nan'),0],[1.01,0],[0]):
            with self.assertRaises(ValueError):interval(bad,k)

    def test_protocol_roundtrip_and_counts(self):
        for family in ('timing','application'):
            for mode in ('smoke','full'):
                p=runner.protocol(family,mode)
                self.assertEqual(json.loads(json.dumps(p)),p)
                self.assertTrue(p['fixed_N_no_optional_stopping'])
                n=p['actual_N']
                jobs=list(d.jobs(family,mode))
                expected=2*math.ceil(n/16) if family=='timing' else n
                self.assertEqual(len(jobs),expected)
        self.assertEqual(len(list(runner.app_specs(1))),80)
        self.assertEqual(len(runner.APP_CONTRASTS),6)

    def test_checkpoint_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);job={'family':'test','index':0};key=d.stable(job)
            self.assertIsNone(runner.completed(folder,job))
            runner.write(folder/'raw/x.json.gz',[{'test_fixture':True}])
            runner.write(folder/'compact/x.json.gz',[{'fixture':True}])
            receipt=dict(job=job,raw='raw/x.json.gz',compact='compact/x.json.gz',
                raw_sha256=runner.digest(folder/'raw/x.json.gz'),compact_sha256=runner.digest(folder/'compact/x.json.gz'))
            runner.write(folder/'checkpoints'/(key+'.json'),receipt)
            self.assertEqual(runner.completed(folder,job),receipt)
            runner.write(folder/'compact/x.json.gz',[{'changed':True}])
            with self.assertRaises(ValueError):runner.completed(folder,job)

    def test_native_scalar_serialization(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'native.json.gz'
            runner.write(p,{'count':np.int64(3),'time':np.float64(2.5)})
            self.assertEqual(runner.read(p),{'count':3,'time':2.5})

    def test_application_missingness_and_offered_denominator(self):
        row={'outcomes':[dict(category='correct_on_time',infidelity=.1,complete=True) for _ in range(12)]}
        self.assertEqual(runner.app_value(row,'correct_timely'),1)
        row['outcomes'][0]=dict(category='unfinished',infidelity=None,complete=False)
        self.assertIsNone(runner.app_value(row,'infidelity'))
        self.assertAlmostEqual(runner.app_value(row,'correct_timely'),11/12)
        row['outcomes'].pop()
        with self.assertRaises(ValueError):runner.app_value(row,'correct_timely')

    def test_application_offer_noninterference(self):
        sys.path.insert(0,str(d.ROOT/'source/application_parent'))
        import app_design as app
        specs=list(runner.app_specs(6))
        private=[d.stable([x for x in app.plans(s) if x['tenant']!='attacker']) for s in specs]
        self.assertEqual(len(set(private)),1)
        self.assertTrue(all(s['stream']>=1000000 for s in specs))
        spec=specs[1];public=app.public_attacker_plan(spec)
        changed=dict(spec,state='Z-',angle=31)
        self.assertEqual(public,app.public_attacker_plan(changed))

    def test_all_offered_partition(self):
        spec=next(runner.app_specs(4))
        outcomes=[dict(category='correct_on_time',wrong=0,infidelity=.1) for _ in range(10)]
        outcomes += [dict(category='late',wrong=1,infidelity=.2),dict(category='unfinished',wrong=None,infidelity=None)]
        result=runner.descriptive([dict(spec=spec,outcomes=outcomes,benign=[])],'application')[0]
        self.assertEqual((result['offered'],result['unfinished'],result['wrong_all'],result['wrong_and_late']),(12,1,1,1))
        self.assertAlmostEqual(result['mean_infidelity_bounds'][0],.1)
        self.assertAlmostEqual(result['mean_infidelity_bounds'][1],2.2/12)

if __name__=='__main__':unittest.main(verbosity=2)
