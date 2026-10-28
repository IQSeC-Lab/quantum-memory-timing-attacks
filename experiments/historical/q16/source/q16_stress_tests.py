"""Offline falsification checks. Synthetic evidence is never simulator evidence."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest

import q16_stress_design as d
from q16_policy import Policy
from q16_audit import reconstruct_dispatch
from q16_stress_audit import validate_record, wake_evidence, summarize

HERE = Path(__file__).resolve().parent


def cpu_fixture(spec):
    """Synthetic fixed CPU replay, useful only for testing the checker itself."""
    inp=d.inputs(spec)
    jobs=[]; tenants={}; created=[]; remaining={}
    for tenant,plan in [('attacker',inp['attacker']),('benign',inp['private']['benign'])]:
        for planned in plan:
            tid=len(jobs);pid=tid+100
            tenants[str(pid)]=tenant
            jobs.append(dict(pid=pid,requested_ns=planned['requested_ns'],admitted_ns=planned['requested_ns'],
                             instructions=30,kind=tenant,plan_index=planned['index']))
            created.append(dict(task_id=tid,pid=pid,block='payload',task_type='HostLocalTask',created_ns=planned['requested_ns']))
            remaining[tid]=dict(pid=pid,tenant=tenant,arrival=planned['requested_ns'])
    raw=dict(spec=spec,inputs=inp,admission_events=jobs,tenant_by_pid=tenants,created_tasks=created,
             cpu_trace=[],active_cpu_trace=[],qpu_trace=[],peer_cpu_trace=[],quantum_events=[],lifetime_events=[],
             memory_channel=[],requests=[],dispatch_decisions=[],service_events=[],finished_pids={'bob':{},'alice':{}},
             scheduler_policy=spec['scheduler'],actual_cpu_scheduler='CpuFcfsScheduler',native_t2_ns=0,
             sim_end_ns=30_000_000,budget_reservations_ns={})
    policy=Policy(spec['scheduler']);now=0;seq=0
    while remaining:
        ready=[t for t,v in remaining.items() if v['arrival']<=now]
        if not ready:
            now=min(v['arrival'] for v in remaining.values());continue
        owners={t:(remaining[t]['pid'],remaining[t]['tenant']) for t in ready}
        decision=policy.decide(now,ready,owners,set(),{t:300_000 for t in ready if owners[t][1]=='attacker'})
        decision.update(sequence=seq,roots=ready,message_blocked=[],future_starts_ns={},blocked_other_core=[],
                        status=['NEXT_TASK'] if decision['selected'] is not None else ['WAITING_START_TIME'])
        seq+=1;raw['dispatch_decisions'].append(decision)
        tid=decision['selected']
        if tid is None:
            future=[v['arrival'] for v in remaining.values() if v['arrival']>now]
            now=min([decision['wake_ns']]+future);continue
        owner=remaining.pop(tid);pid=owner['pid'];tenant=owner['tenant']
        raw['service_events'].append(dict(kind='start',task_id=tid,pid=pid,tenant=tenant,time_ns=now,sequence=seq));seq+=1
        raw['cpu_trace'].append(dict(task_id=tid,pid=pid,block='payload',task_type='HostLocalTask',start_ns=now,end_ns=now+300_000))
        now+=300_000
        raw['service_events'].append(dict(kind='end',task_id=tid,pid=pid,tenant=tenant,time_ns=now,sequence=seq));seq+=1
        jobs[tid].update(finished_ns=now,x=28)
        raw['finished_pids']['bob'][str(pid)]=now
    raw['budget_reservations_ns']={str(k):v for k,v in policy.reserved.items()}
    return raw


class StressTests(unittest.TestCase):
    def test_parent_source_and_minimal_driver_difference(self):
        names=['backend_cpusched_reference.txt','hardware_models.json','p1_engine.py','parent_sources.json',
               'pinned_source_hashes.json','q15_case.py','q16.py','q16_audit.py','q16_design.py',
               'q16_engine.py','q16_io.py','q16_policy.py','q16_tests.py','q6_reference.py']
        source={name:(HERE/name).read_text() for name in names}
        digest=hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(digest,d.PARENT_BUNDLE)
        def run_ast(path):
            tree=ast.parse(path.read_text())
            return next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        parent=ast.unparse(run_ast(HERE/'q16_engine.py'))
        child=ast.unparse(run_ast(HERE/'q16_stress_engine.py'))
        expected=parent.replace('    global CURRENT_CASE\n    CURRENT_CASE = None','    parent.CURRENT_CASE = None')
        expected=expected.replace('inp = d.inputs(spec)','inp = fixtures.inputs(spec)').replace('    CURRENT_CASE = c','    parent.CURRENT_CASE = c')
        self.assertEqual(child,expected)

    def test_fixed_scope_counts(self):
        specs=list(d.specifications());plans=[d.inputs(s) for s in specs]
        self.assertEqual(len(specs),12)
        self.assertEqual(sum(len(x['attacker']) for x in plans),57)
        self.assertEqual(sum(len(x['private']['benign']) for x in plans),6)
        self.assertEqual(sum(len(x['private']['requests']) for x in plans),6)
        self.assertEqual(len({x['private']['simulator_seed'] for x in plans}),4)
        with self.assertRaises(ValueError):list(d.specifications('full'))
        with self.assertRaises(ValueError):d.inputs(dict(specs[0],case='retuned'))

    def test_cpu_stress_replay_and_rejection(self):
        for spec in d.specifications():
            if not spec['case'].endswith('new_job'):continue
            raw=cpu_fixture(spec)
            row=validate_record(raw,spec)
            self.assertEqual(row['status'],'PASS')
            for field,value in [('x',27),('admitted_ns',99),('finished_ns',None)]:
                bad=copy.deepcopy(raw);bad['admission_events'][0][field]=value
                with self.assertRaises((ValueError,TypeError)):validate_record(bad,spec)
            bad=copy.deepcopy(raw)
            next(r for r in bad['dispatch_decisions'] if r['selected'] is None)['status']=[]
            with self.assertRaises(ValueError):validate_record(bad,spec)
            bad=copy.deepcopy(raw);bad['cpu_trace'][0]['end_ns']+=1
            with self.assertRaises(ValueError):reconstruct_dispatch(bad)

    def test_wait_flags_without_timely_progress_cannot_pass(self):
        for status,field in [('WAITING_MSG','message_blocked'),('WAITING_OTHER_CORE','blocked_other_core')]:
            raw=dict(cpu_trace=[dict(task_id=9,pid=2,start_ns=2_000_000)],dispatch_decisions=[
                dict(selected=None,status=['WAITING_START_TIME',status],sequence=7,
                     time_ns=1_500_000,wake_ns=10_000_000,**{field:[9]})])
            self.assertEqual(len(wake_evidence(raw,status,field,2)),1)
            for start in (1_500_000,10_000_000,10_000_001):
                bad=copy.deepcopy(raw);bad['cpu_trace'][0]['start_ns']=start
                with self.assertRaises(ValueError):wake_evidence(bad,status,field,2)
            bad=copy.deepcopy(raw);bad['cpu_trace'][0]['pid']=3
            with self.assertRaises(ValueError):wake_evidence(bad,status,field,2)
            bad=copy.deepcopy(raw);bad['dispatch_decisions'][0][field]=[]
            with self.assertRaises(ValueError):wake_evidence(bad,status,field,2)

    def test_summary_rejects_missing_duplicate_or_failed_cases(self):
        with self.assertRaises(ValueError):summarize([])
        records=[dict(spec=s,status='FAIL') for s in d.specifications()]
        with self.assertRaises(ValueError):summarize(records)
        records[0]=records[1]
        with self.assertRaises(ValueError):summarize(records)


if __name__=='__main__':
    unittest.main(verbosity=2)
