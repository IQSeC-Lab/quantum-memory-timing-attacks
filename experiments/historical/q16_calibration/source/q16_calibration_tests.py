"""Offline falsification tests; synthetic records are not simulator evidence."""
import ast
import copy
import inspect
import json
from pathlib import Path
import unittest
from unittest import mock

import q16_calibration_design as d
from q16_calibration_audit import (nearest_rank_p99, rounded_deadline_ns,
    calibrate_deadline, select_policy, choose_template, joint_counts, summarize)

HERE = Path(__file__).resolve().parent


def synthetic_record(spec, wrong=0, response_ns=1_000_000):
    """Compact fixture ONLY, to test aggregate decision rules and rejection."""
    count = d.WARMUP+d.MEASURED[spec['stage']]
    requests = []
    for i in range(count):
        measured = i >= d.WARMUP
        incorrect = int(measured and i-d.WARMUP < wrong)
        offered = i*20_000_000
        requests.append(dict(index=i, measured=measured, state=i%6, bell_bits=[0, 0],
            category='completed_wrong' if incorrect else 'completed_correct',
            wrong_output=incorrect, offered_ns=offered, admitted_ns=offered,
            finished_ns=offered+response_ns, peer_finished_ns=offered+response_ns,
            response_ns=response_ns, storage_ns=0, expected_infidelity=0))
    attack = 0 if spec['stage']=='benign' else 360_000_000
    return dict(spec=spec, request_rows=requests, offered_measured=d.MEASURED[spec['stage']],
        offered_all=count, incomplete_jobs={'attacker':0, 'benign':0},
        requested_attacker_service_ns=attack, executed_attacker_service_ns=attack,
        requested_benign_service_ns=300_000, executed_benign_service_ns=300_000,
        pairing_sha256=d.stable([spec['stage'],spec['arrival'],spec['stream']]),
        audit={'max_attacker_period_service_ns':1_200_000 if attack else 0}, max_density_error=0)


class CalibrationTests(unittest.TestCase):
    def test_minimal_driver_change_and_no_full_mode(self):
        def function(path):
            return next(n for n in ast.parse(path.read_text()).body
                        if isinstance(n,ast.FunctionDef) and n.name=='run')
        old = ast.unparse(function(HERE/'q16_engine.py'))
        new = ast.unparse(function(HERE/'q16_calibration_engine.py'))
        expected = old.replace('    global CURRENT_CASE\n    CURRENT_CASE = None',
                               '    parent.CURRENT_CASE = None').replace('    CURRENT_CASE = c','    parent.CURRENT_CASE = c')
        self.assertEqual(new, expected)
        with self.assertRaises(ValueError):
            list(d.specifications('full'))
        with self.assertRaises(ValueError):
            list(d.specifications('evaluation'))

    def test_all_fixed_private_inputs_fit_without_resampling(self):
        specs = list(d.specifications())
        self.assertEqual(len(specs),1216)
        self.assertEqual(sum(d.MEASURED[s['stage']] for s in specs),114688)
        self.assertEqual(sum(d.WARMUP+d.MEASURED[s['stage']] for s in specs),139008)
        cache = {}
        for spec in specs:
            key = (spec['stage'],spec['arrival'],spec['stream'])
            if key not in cache:
                cache[key] = d.private_inputs(spec)
        self.assertEqual(len(cache),128)
        self.assertEqual(len({r['simulator_seed'] for r in cache.values()}),128)
        states = set()
        for (stage,arrival,stream), value in cache.items():
            requests = value['requests']
            self.assertEqual(len(requests),d.WARMUP+d.MEASURED[stage])
            self.assertEqual(sum(r['measured'] for r in requests),d.MEASURED[stage])
            self.assertTrue(all(0<=r['offered_ns']<d.RELEASE_END_NS[stage] for r in requests))
            self.assertTrue(all(b['offered_ns']>a['offered_ns'] for a,b in zip(requests,requests[1:])))
            self.assertTrue(all(0<=j['requested_ns']<d.RELEASE_END_NS[stage] for j in value['benign']))
            states.update(r['state'] for r in requests)
        self.assertEqual(states,set(range(6)))
        # Candidate identity is excluded from all application/background seeds.
        first = next(d.specifications('own_policy'))
        other = dict(first, template=list(d.TEMPLATES[-1]))
        self.assertEqual(d.private_inputs(first),d.private_inputs(other))

    def test_attack_input_boundary_and_plan_before_private_draws(self):
        self.assertEqual(set(inspect.signature(d.attacker_plan).parameters),
                         {'template','seed','release_end_ns'})
        spec = next(d.specifications('own_policy'))
        events = []
        real = d.attacker_plan
        def attack(**kwargs):
            events.append('attack')
            return real(**kwargs)
        def private(_):
            self.assertEqual(events,['attack'])
            events.append('private')
            return {'requests':[{'state':5,'offered_ns':123}], 'simulator_seed':999,
                    'benign':[], 'mutation':'Synthetic mutation; not executable simulator input'}
        baseline = d.inputs(spec)
        with mock.patch.object(d,'attacker_plan',side_effect=attack), mock.patch.object(d,'private_inputs',side_effect=private):
            mutated = d.inputs(spec)
        self.assertEqual(events,['attack','private'])
        self.assertEqual(baseline['attacker'],mutated['attacker'])
        self.assertEqual(baseline['attacker_plan_sha256'],mutated['attacker_plan_sha256'])
        self.assertNotEqual(baseline['pairing_sha256'],mutated['pairing_sha256'])
        for template in d.TEMPLATES:
            plan = real(template=template,seed=1,release_end_ns=d.RELEASE_END_NS['own_policy'])
            self.assertEqual(len(plan),1200)
            self.assertEqual(sum(j['requested_service_ns'] for j in plan),360_000_000)
            self.assertTrue(all(j['requested_ns']<d.RELEASE_END_NS['own_policy'] for j in plan))
            self.assertTrue(all(j['requested_ns']//d.PERIOD_NS==j['release_period'] for j in plan))

    def test_nearest_rank_and_upward_deadline_are_exact(self):
        values = list(range(1,257))
        self.assertEqual(nearest_rank_p99(values),{'n':256,'rank':254,'p99_ns':254})
        self.assertEqual(rounded_deadline_ns(1_000_000),1_200_000)
        self.assertEqual(rounded_deadline_ns(1_000_001),1_300_000)
        for invalid in ([],[None],[float('nan')],[1.5],[-1]):
            with self.assertRaises(ValueError):
                nearest_rank_p99(invalid)

    def test_deadline_is_benign_only_and_completion_gate_is_not_dropping(self):
        benign = [synthetic_record(s) for s in d.specifications('benign')]
        own = synthetic_record(next(d.specifications('own_policy')),wrong=64,response_ns=2_000_000_000)
        deadline = calibrate_deadline(benign+[own])
        self.assertEqual(deadline['deadline_ns'],1_200_000)
        self.assertEqual(deadline['status'],'FROZEN')
        benign[0]['request_rows'][0]['peer_finished_ns'] = None
        failed = calibrate_deadline(benign)
        self.assertEqual(failed['status'],'FAILED')
        self.assertIsNone(failed['deadline_ns'])
        self.assertEqual(len(failed['per_stream']),192)
        self.assertEqual(len(failed['failures']),1)
        with self.assertRaises(ValueError):
            calibrate_deadline(benign[:-1])

    def test_joint_counts_keep_all_offered_and_exact_deadline(self):
        row = synthetic_record(next(d.specifications('own_policy')),wrong=3)
        q = row['request_rows'][d.WARMUP]
        q.update(finished_ns=None,response_ns=None,wrong_output=None)
        q = row['request_rows'][d.WARMUP+1]
        q['response_ns'] = 1_000_001
        counts = joint_counts(row,1_000_000)
        self.assertEqual(sum(counts.values()),64)
        self.assertEqual(counts,dict(correct_on_time=61,wrong_on_time=1,completed_late=1,unfinished=1))

    def test_selection_eligibility_is_per_regime_and_tie_is_lexical(self):
        records = [synthetic_record(s,wrong=1) for s in d.specifications('own_policy')]
        deadline = {'status':'FROZEN','deadline_ns':1_200_000}
        result = select_policy(records,deadline)
        self.assertTrue(result['selection_success'])
        self.assertEqual(result['template'],[0,0])
        self.assertFalse(result['full_evaluation_ready'])
        # First candidate remains high-scoring, but misses21/2048 in one regime.
        first = records[0]
        for q in first['request_rows'][d.WARMUP:d.WARMUP+21]:
            q['response_ns']=2_000_000
        rejected = select_policy(records,deadline)
        self.assertFalse(rejected['candidates'][0]['eligible'])
        self.assertTrue(rejected['selection_success'])
        self.assertEqual(rejected['template'],[0,250000])
        # Twenty late /2048 still meets observed0.99; this is not a CI gate.
        first['request_rows'][d.WARMUP+20]['response_ns']=1_000_000
        boundary = select_policy(records,deadline)
        self.assertTrue(boundary['candidates'][0]['eligible'])
        fallback = choose_template([dict(template=[0,0],eligible=False,score_numerator=1,score_denominator=1)])
        self.assertFalse(fallback['selection_success'])
        self.assertFalse(fallback['selected_learning_success'])
        self.assertFalse(fallback['full_evaluation_ready'])
        self.assertEqual(fallback['offsets_ns'],list(d.FALLBACK_OFFSETS_NS))
        with self.assertRaises(ValueError):
            select_policy(records[:-1],deadline)

    def test_full_summary_retains_failed_conditions_and_canonical_json(self):
        records = [synthetic_record(s,wrong=1) for s in d.specifications()]
        summary = summarize(records)
        self.assertEqual(summary['status'],'READY_FOR_FREEZE_REVIEW')
        self.assertFalse(summary['full_evaluation_ready'])
        self.assertEqual(summary,json.loads(json.dumps(summary)))
        first_own = next(r for r in records if r['spec']['stage']=='own_policy')
        first_own['executed_attacker_service_ns']-=300_000
        summary = summarize(records)
        self.assertEqual(len(summary['incomplete_streams']),1)
        self.assertFalse(summary['own_policy']['candidates'][0]['eligible'])
        records[0]['request_rows'][0]['finished_ns']=None
        records[0]['request_rows'][0]['response_ns']=None
        summary = summarize(records)
        self.assertEqual(summary['status'],'CALIBRATION_INCOMPLETE')
        self.assertEqual(summary['own_policy']['status'],'BLOCKED')
        self.assertIsNone(summary['own_policy']['offsets_ns'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
