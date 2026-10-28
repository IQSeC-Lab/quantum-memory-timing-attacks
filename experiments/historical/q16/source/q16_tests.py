"""Offline engineering tests. These do not substitute for NetSquid controls."""
import ast
import copy
import enum
import inspect
import json
from pathlib import Path
import random
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

import q16_design as d
from q16_policy import Policy
from q16_audit import reconstruct_dispatch, validate_record, check_channel, CATEGORIES
from q16_io import sha, put, read, write_raw, export_full


def synthetic_schedule(policy_name, seed=0):
    """Synthetic CPU-only evidence for exercising the independent replay checker."""
    rng = random.Random(seed)
    tenants, created, remaining, arrivals, lengths = {}, [], {}, {}, {}
    for tid in range(48):
        tenant = ('attacker','benign','victim')[tid%3]
        # Several PIDs per tenant; RR must not reward attacker PID multiplication.
        pid = tid+100
        tenants[str(pid)] = tenant
        remaining[tid] = (pid,tenant)
        arrivals[tid] = rng.randrange(0,25_000_000)
        lengths[tid] = 10_000 if tenant=='victim' else 300_000
        created.append(dict(task_id=tid,pid=pid,block='payload',task_type='HostLocalTask',created_ns=arrivals[tid]))
    policy = Policy(policy_name)
    raw = dict(tenant_by_pid=tenants,created_tasks=created,cpu_trace=[],active_cpu_trace=[],
               quantum_events=[],lifetime_events=[],dispatch_decisions=[],service_events=[],
               scheduler_policy=policy_name,sim_end_ns=100_000_000,budget_reservations_ns={})
    now,sequence = 0,0
    while remaining:
        ready = sorted(t for t in remaining if arrivals[t]<=now)
        if not ready:
            now = min(arrivals[t] for t in remaining)
            continue
        owners = {t:remaining[t] for t in ready}
        costs = {t:300_000 for t in ready if owners[t][1]=='attacker'}
        decision = policy.decide(now,ready,owners,set(),costs)
        decision.update(sequence=sequence,roots=ready,message_blocked=[],future_starts_ns={},blocked_other_core=[],
                        status=['NEXT_TASK'] if decision['selected'] is not None else ['WAITING_START_TIME'])
        sequence += 1
        raw['dispatch_decisions'].append(decision)
        tid = decision['selected']
        if tid is None:
            future = [arrivals[t] for t in remaining if arrivals[t]>now]
            now = min([decision['wake_ns']]+future)
            continue
        pid,tenant = remaining.pop(tid)
        raw['service_events'].append(dict(kind='start',task_id=tid,pid=pid,tenant=tenant,time_ns=now,sequence=sequence))
        sequence += 1
        row = dict(task_id=tid,pid=pid,block='payload',task_type='HostLocalTask',start_ns=now,end_ns=now+lengths[tid])
        raw['cpu_trace'].append(row)
        now = row['end_ns']
        raw['service_events'].append(dict(kind='end',task_id=tid,pid=pid,tenant=tenant,time_ns=now,sequence=sequence))
        sequence += 1
    raw['budget_reservations_ns'] = {str(k):v for k,v in policy.reserved.items()}
    return raw


def unfinished_fixture():
    """Deliberately unfinished synthetic stream, never presented as simulator data."""
    spec = next(d.specifications('pilot'))
    inp = d.inputs(spec)
    jobs,tenants = [],{}
    for index,job in enumerate(inp['private']['benign']):
        pid = index+100
        tenants[str(pid)] = 'benign'
        jobs.append(dict(pid=pid,requested_ns=job['requested_ns'],admitted_ns=job['requested_ns'],
                         instructions=30,kind='benign',plan_index=index,finished_ns=None,x=None))
    requests = [dict(r,actual_offered_ns=r['offered_ns'],admitted_ns=None,victim=None,
                    output_values={},peer_output_values={},finished_ns=None,peer_finished_ns=None,
                    completed=False,wrong_output=None,category='unfinished') for r in inp['private']['requests']]
    return dict(spec=spec,inputs=inp,requests=requests,admission_events=jobs,tenant_by_pid=tenants,
                cpu_trace=[],active_cpu_trace=[],qpu_trace=[],peer_cpu_trace=[],quantum_events=[],
                lifetime_events=[],memory_channel=[],created_tasks=[],dispatch_decisions=[],service_events=[],
                budget_reservations_ns={},actual_cpu_scheduler='CpuFcfsScheduler',scheduler_policy=spec['scheduler'],
                native_t2_ns=0,sim_end_ns=inp['horizon_ns'])


class DesignTests(unittest.TestCase):
    def test_counts_and_fixed_horizons(self):
        specs = list(d.specifications('pilot'))
        self.assertEqual(len(specs),192)
        self.assertEqual(len({d.stable(s) for s in specs}),192)
        primary = [d.inputs(s) for s in specs]
        self.assertEqual(sum(sum(r['measured'] for r in x['private']['requests']) for x in primary),1536)
        self.assertEqual(sum(len(x['private']['requests']) for x in primary),1920)
        controls = [d.inputs(s) for s in d.specifications('controls')]
        self.assertEqual(len(controls),24)
        self.assertEqual(sum(len(x['private']['requests']) for x in controls),1248)
        self.assertEqual({x['horizon_ns'] for x in primary},{800_000_000})
        with self.assertRaises(ValueError):
            list(d.specifications('full'))

    def test_attacker_input_boundary(self):
        self.assertEqual(set(inspect.signature(d.attacker_plan).parameters),{'arm','seed','release_end_ns'})
        for arm in d.ARMS:
            spec = dict(next(d.specifications('pilot')),arm=arm)
            before = d.inputs(spec)
            changed = copy.deepcopy(before['private'])
            changed['requests'][0].update(offered_ns=1,state=5,hidden_pid=9999)
            changed['simulator_seed'] = 12345
            changed['benign'] = []
            with patch.object(d,'private_inputs',return_value=changed):
                after = d.inputs(spec)
            self.assertNotEqual(before['pairing_sha256'],after['pairing_sha256'])
            self.assertEqual(before['attacker_plan_sha256'],after['attacker_plan_sha256'])
            self.assertEqual(before['attacker'],after['attacker'])
            with self.assertRaises(TypeError):
                d.attacker_plan(**before['attacker_inputs'],victim_arrivals=[0])

    def test_equal_requested_work_and_pairing(self):
        for stream in range(8):
            rows = [d.inputs(dict(next(d.specifications('pilot')),stream=stream,arm=arm)) for arm in d.ARMS]
            self.assertEqual(len({r['pairing_sha256'] for r in rows}),1)
            self.assertEqual([len(r['attacker']) for r in rows],[0,240,240,240])
            for row in rows[1:]:
                for period in range(60):
                    jobs = [j for j in row['attacker'] if j['release_period']==period]
                    self.assertEqual(sum(j['requested_service_ns'] for j in jobs),1_200_000)

    def test_joint_denominator_and_deadline(self):
        self.assertEqual(d.outcome_category(False,None,None,0),'unfinished')
        self.assertEqual(d.outcome_category(True,1,20_000_000,0),'wrong_on_time')
        self.assertEqual(d.outcome_category(True,0,20_000_001,0),'completed_late')
        raw = unfinished_fixture()
        result = validate_record(raw,raw['spec'])
        self.assertEqual(result['offered_measured'],8)
        self.assertEqual(result['measured_joint_counts'],dict.fromkeys(CATEGORIES,0)|{'unfinished':8})
        raw['requests'][2]['category'] = 'correct_on_time'
        with self.assertRaises(ValueError):
            validate_record(raw,raw['spec'])

    def test_six_state_channel_and_density_tampering(self):
        import math
        import numpy as np
        from q16_engine import target_vector
        for state in range(6):
            vector=target_vector('teleport',state)
            ideal=np.outer(vector,vector.conj())
            factor=math.exp(-(3_000_000/13_000_000)**1.67)
            after=factor*ideal+(1-factor)*np.eye(2)/2
            raw=dict(spec=dict(workload='teleport',profile='final_si'),sim_end_ns=20_000_000,
                     quantum_events=[dict(kind='epr_ready',pid=1,end_ns=1_000_000),
                                     dict(kind='instruction',pid=1,routine='verify_state',name='rot_y',start_ns=4_000_000)])
            request=dict(victim=dict(bob_pid=1),state=state,completed=True,admitted_ns=0)
            channel=dict(pid=1,state=state,start_ns=1_000_000,use_ns=4_000_000,age_ns=3_000_000,
                         contraction=factor,fidelity=(1+factor)/2,expected_infidelity=(1-factor)/2,
                         before_real=ideal.real.tolist(),before_imag=ideal.imag.tolist(),
                         density_real=after.real.tolist(),density_imag=after.imag.tolist())
            self.assertLess(check_channel(raw,request,channel),2e-9)
            channel['density_real'][0][0]+=0.01
            with self.assertRaises(ValueError): check_channel(raw,request,channel)


class PolicyTests(unittest.TestCase):
    def test_exhaustion_rollover_and_nonblocking(self):
        policy = Policy('QUOTA_FCFS')
        ready = list(range(5))
        owners = {t:(t,'attacker') for t in ready}
        for step in range(4):
            row = policy.decide(step*300_000,ready,owners,set(),{t:300_000 for t in ready})
            self.assertEqual(row['selected'],step)
            ready.remove(step); owners.pop(step)
        owners[10] = (10,'victim'); ready.append(10)
        row = policy.decide(1_200_000,ready,owners,set(),{4:300_000})
        self.assertEqual(row['selected'],10)
        self.assertEqual(row['blocked'],{'4':['insufficient_budget']})
        row = policy.decide(1_210_000,[4],{4:(4,'attacker')},set(),{4:300_000})
        self.assertIsNone(row['selected'])
        self.assertEqual(row['wake_ns'],10_000_000)
        row = policy.decide(10_000_000,[4],{4:(4,'attacker')},set(),{4:300_000})
        self.assertEqual(row['selected'],4)
        self.assertEqual(policy.reserved,{0:1_200_000,1:300_000})

    def test_boundary_exact_and_cap(self):
        for now,expected in [(9_700_000,0),(9_700_001,None),(10_000_000,0)]:
            row = Policy('QUOTA_FCFS').decide(now,[0],{0:(0,'attacker')},set(),{0:300_000})
            self.assertEqual(row['selected'],expected)
        for cost in (0,-1,300_001,None,300000.0):
            with self.assertRaises(ValueError):
                Policy('QUOTA_FCFS').decide(0,[0],{0:(0,'attacker')},set(),{0:cost})

    def test_rr_tenants_not_pids(self):
        policy = Policy('QUOTA_RR')
        ready = [0,1,2,3,4,5]
        owners = {0:(100,'attacker'),1:(101,'attacker'),2:(200,'benign'),
                  3:(201,'benign'),4:(300,'victim'),5:(301,'victim')}
        chosen = []
        for step in range(6):
            row = policy.decide(step*300_000,ready,owners,set(),{t:300_000 for t in ready if owners[t][1]=='attacker'})
            chosen.append(row['selected'])
            ready.remove(row['selected']); owners.pop(row['selected'])
        self.assertEqual(chosen,[4,2,0,5,3,1])
        row = Policy('QUOTA_RR').decide(0,[2],{2:(2,'benign')},{999},{})
        self.assertEqual(row['selected'],2)
        self.assertEqual(row['rr_cursor_after'],2)

    def test_protected_ready_and_eligible_only(self):
        policy = Policy('QUOTA_PROTECTED')
        row = policy.decide(0,[1,2],{1:(1,'attacker'),2:(2,'victim')},{2,999},{1:300_000})
        self.assertEqual(row['selected'],2)
        policy.reserved[0] = 1_200_000
        row = policy.decide(10_000,[1,3],{1:(1,'attacker'),3:(3,'benign')},{1,999},{1:300_000})
        self.assertEqual(row['selected'],3)

    def test_duplicate_dispatch_is_rejected(self):
        policy = Policy('QUOTA_FCFS')
        policy.decide(0,[0],{0:(0,'victim')},set(),{})
        with self.assertRaises(ValueError):
            policy.decide(10_000,[0],{0:(0,'victim')},set(),{})
        with self.assertRaises(ValueError):
            Policy('QUOTA_FCFS').decide(0,[0,0],{0:(0,'victim')},set(),{})

    def test_independent_replay_varied_ready_sets(self):
        for scheduler in d.SCHEDULERS:
            for seed in range(24):
                raw = synthetic_schedule(scheduler,seed)
                result = reconstruct_dispatch(raw)
                self.assertEqual(result['all_cpu_tasks'],48)
                self.assertLessEqual(result['max_attacker_period_service_ns'],1_200_000)

    def test_replay_detects_tampering(self):
        original = synthetic_schedule('QUOTA_FCFS')
        mutations = [
            lambda r:r['dispatch_decisions'][0].update(reserved_before_ns=1),
            lambda r:r['dispatch_decisions'][0].update(selected=999),
            lambda r:r['dispatch_decisions'][0].update(live_pids=[999]),
            lambda r:r['service_events'].pop(),
            lambda r:r['cpu_trace'][0].update(end_ns=r['cpu_trace'][0]['end_ns']+1),
            lambda r:r['budget_reservations_ns'].update({'0':9_999_999}),
        ]
        for mutation in mutations:
            raw = copy.deepcopy(original); mutation(raw)
            with self.assertRaises(ValueError):
                reconstruct_dispatch(raw)


class NativeAdapterTests(unittest.TestCase):
    def test_pinned_native_wait_loop_preserves_wakeups(self):
        # Execute the actual pinned update_status/run methods with a fake event
        # backend. This checks adapter control flow; it is NOT a NetSquid run.
        path = Path(__file__).with_name('backend_cpusched_reference.txt')
        expected = read(Path(__file__).with_name('pinned_source_hashes.json'))
        self.assertEqual(sha(path),expected['qoala/sim/scheduling/cpusched.py'])
        tree = ast.parse(path.read_text())
        cls = next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='CpuScheduler')
        methods = [x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name in ('update_status','run')]
        module = ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+methods,type_ignores=[])
        class Status(enum.Enum):
            GRAPH_EMPTY=1; NEXT_TASK=2; WAITING_OTHER_CORE=3; WAITING_MSG=4; WAITING_START_TIME=5
        class SchedulerStatus:
            def __init__(self,status,params): self.status,self.params=status,params
        class Expr:
            def __init__(self,names=None,**kw): self.names=set(names or ['timer'])
            def __or__(self,other): return Expr(self.names|other.names)
        class Task:
            critical_section = None
            def __init__(self,tid,pid,event=False): self.task_id,self.pid,self.event=tid,pid,event
            def is_event_task(self): return self.event
        class HostLocalTask(Task):
            block_name = 'payload'
        tasks = {1:HostLocalTask(1,1),2:Task(2,2,True),3:Task(3,2),4:Task(4,2)}
        infos = {t:types.SimpleNamespace(task=v,start_time=5_000_000 if t==4 else None) for t,v in tasks.items()}
        class Graph:
            def get_tasks(self): return infos
            def get_roots(self): return [1,2,4]
            def get_tasks_blocked_only_on_external(self): return [3]
            def get_tinfo(self,t): return infos[t]
        log = types.SimpleNamespace(debug=lambda *a:None)
        ns = types.SimpleNamespace(sim_time=lambda:0)
        namespace = dict(ns=ns,Status=Status,SchedulerStatus=SchedulerStatus,EventExpression=Expr,
                         EVENT_WAIT='wait',SIGNAL_TASK_COMPLETED='done',sys=sys,copy=copy)
        exec(compile(ast.fix_missing_locations(module),str(path),'exec'),namespace)
        class CPU:
            @property
            def status(self): return self._status
        cpu = CPU()
        cpu._task_graph=Graph(); cpu._critical_section=None; cpu._task_queue=[]
        cpu._task_logger=log; cpu._logger=log; cpu._other_scheduler='qpu'
        cpu.node_scheduler_in_port='node'; cpu.handle_task=lambda tid:iter(())
        cpu.update_external_predcessors=lambda:None
        cpu.is_message_available=lambda tid:False
        cpu.await_port_input=lambda port:Expr(['node'])
        cpu.await_signal=lambda **kw:Expr(['other_core'])
        timers=[]; cpu._schedule_after=lambda delay,event:timers.append(delay)
        cpu._get_peer_names_waiting_message_from=lambda:['alice']
        cpu._host_interface=types.SimpleNamespace(get_evexpr_for_msg_from=lambda peers:Expr(['message']))
        cpu.update_status=types.MethodType(namespace['update_status'],cpu)
        cpu.run=types.MethodType(namespace['run'],cpu)
        ops=[type('AssignCValueOp',(),{})() for _ in range(2)]+[type('AddCValueOp',(),{})() for _ in range(28)]
        process=types.SimpleNamespace(program=types.SimpleNamespace(blocks=[types.SimpleNamespace(name='payload',instructions=ops)]))
        case=types.SimpleNamespace(ns=ns,bob=types.SimpleNamespace(scheduler=types.SimpleNamespace(cpu_scheduler=cpu),
                                   memmgr=types.SimpleNamespace(get_process=lambda pid:process)),live=set(),decisions=[])
        sequence=iter(range(100)); case.next_sequence=lambda:next(sequence)
        stub=types.ModuleType('qoala.sim.scheduling.procsched')
        stub.Status=Status; stub.SchedulerStatus=SchedulerStatus
        with patch.dict(sys.modules,{'qoala.sim.scheduling.procsched':stub}):
            from q16_engine import install_scheduler
            install_scheduler(case,'QUOTA_FCFS',{1:'attacker',2:'victim'})
        case.policy.reserved[0]=1_200_000
        gen=cpu.run(); event=next(gen)
        self.assertEqual(event.names,{'node','timer','other_core','message'})
        self.assertEqual(timers,[5_000_000])
        self.assertEqual(case.decisions[0]['status'],['WAITING_MSG','WAITING_OTHER_CORE','WAITING_START_TIME'])
        gen.close()
        cpu.is_message_available=lambda tid:True
        cpu._status=SchedulerStatus(set(),{})
        cpu.update_status()
        self.assertEqual(case.decisions[-1]['selected'],2)
        self.assertEqual(cpu.status.status,{Status.NEXT_TASK})


class CheckpointTests(unittest.TestCase):
    def test_raw_export_and_checkpoint_corruption(self):
        from q16 import verified_checkpoint
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'q16-pilot-synthetic'; folder=root/'pilot'/'stream-0000'
            folder.mkdir(parents=True)
            job={'spec':{'synthetic':True}}
            put(root/'protocol.json',{'synthetic_test_only':True})
            write_raw(folder/'one.raw.json.gz',{'synthetic_test_only':True,'trace':[1,2,3]})
            put(folder/'one.compact.json',{'spec':job['spec']})
            put(folder/'one.stats.json',{'synthetic':True})
            marker={'job_sha256':d.stable(job)}
            for kind,name in [('raw','one.raw.json.gz'),('compact','one.compact.json'),('statistics','one.stats.json')]:
                marker[kind+'_file']=name; marker[kind+'_sha256']=sha(folder/name)
            put(folder/'completed.json',marker)
            verified_checkpoint(folder,job)
            archive=export_full(root,Path(tmp)/'exports')
            with zipfile.ZipFile(archive) as z:
                manifest=json.loads(z.read('EXPORT_MANIFEST.json'))
                self.assertEqual(manifest['raw_files'],1)
                self.assertEqual(manifest['raw_files_omitted'],0)
                self.assertEqual(manifest['committed_file_hashes_verified'],3)
                self.assertIsNone(z.testzip())
            put(folder/'one.compact.json',{'tampered':True})
            with self.assertRaises(RuntimeError): verified_checkpoint(folder,job)
            with self.assertRaises(ValueError): export_full(root,Path(tmp)/'exports')


if __name__=='__main__':
    unittest.main(verbosity=2)
