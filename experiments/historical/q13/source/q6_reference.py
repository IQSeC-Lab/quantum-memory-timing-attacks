"""Q6 submission-path diagnostic. NetSquid runtime validation pending."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import subprocess
import sys
import time

PIN = '31fa2c9699578d227736557f35cd01a134c4bbde'
BLOCK_NS = 300_000
T2_NS = 4_000_000

VICTIM = '''META_START
    name: bob
    parameters: alice_id
    csockets: 0 -> alice
    epr_sockets:
META_END

^setup {type = CL}:
    csocket = assign_cval() : 0
    request = assign_cval() : 1

^prepare {type = QL}:
    run_subroutine() : prepare_state

^send {type = CL}:
    send_cmsg(csocket, request)

^receive {type = CC}:
    reply = recv_cmsg(csocket)

^measure {type = QL}:
    tuple<m> = run_subroutine() : measure_state

^output {type = CL}:
    return_result(m)
    return_result(reply)

SUBROUTINE prepare_state
    params:
    returns:
    uses: 0
    keeps: 0
    request:
  NETQASM_START
    set Q0 0
    init Q0
    h Q0
  NETQASM_END

SUBROUTINE measure_state
    params:
    returns: m
    uses: 0
    keeps:
    request:
  NETQASM_START
    set Q0 0
    h Q0
    meas Q0 M0
    store M0 @output[0]
  NETQASM_END
'''

PEER = '''META_START
    name: alice
    parameters: bob_id
    csockets: 0 -> bob
    epr_sockets:
META_END

^setup {type = CL}:
    csocket = assign_cval() : 0

^receive {type = CC}:
    request = recv_cmsg(csocket)

^reply {type = CL}:
    send_cmsg(csocket, request)
'''

COMPETITOR = f'''META_START
    name: competitor
    parameters:
    csockets:
    epr_sockets:
META_END

^payload {{type = CL}}:
    busy() : {BLOCK_NS}
'''



VICTIM_DEADLINES = VICTIM
for old, new in [
    ("^prepare {type = QL}:", "^prepare {type = QL; deadlines = [setup: 500000]}:"),
    ("^send {type = CL}:", "^send {type = CL; deadlines = [prepare: 500000]}:"),
    ("^receive {type = CC}:", "^receive {type = CC; deadlines = [send: 2500000]}:"),
    ("^measure {type = QL}:", "^measure {type = QL; deadlines = [receive: 500000]}:"),
]:
    assert VICTIM_DEADLINES.count(old) == 1
    VICTIM_DEADLINES = VICTIM_DEADLINES.replace(old, new)


def wilson(k, n):
    """Marginal 95% Wilson interval for independent Bernoulli trials."""
    z = 1.959963984540054
    p = k / n
    d = 1 + z*z/n
    center = (p + z*z/(2*n))/d
    half = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return [max(0., center-half), min(1., center+half)]


def competitor_program(block_count):
    if block_count not in [1, 2, 4, 8, 16]:
        raise ValueError('Unsupported block count')
    block_ns = 1_200_000 // block_count
    assert block_ns * block_count == 1_200_000
    return COMPETITOR.replace(f'busy() : {BLOCK_NS}', f'busy() : {block_ns}')



def payload_program(payload):
    if payload == 'busy':
        return competitor_program(4)
    if payload != 'instructions':
        raise ValueError(payload)
    instructions = ['    x = assign_cval() : 0', '    one = assign_cval() : 1']
    instructions += ['    x = add_cval_c(x, one)'] * 28
    # 30 ordinary instructions x 10 us = 0.3 ms per job, four jobs = 1.2 ms.
    return COMPETITOR[:COMPETITOR.index('    busy()')] + '\n'.join(instructions) + '\n'

def run_static(phase_ns, t2_ns, seed, scheduler, one_way_ns, block_count=4, payload="busy"):
    if block_count not in [1, 2, 4, 8, 16]:
        raise ValueError('Unsupported block count')
    block_ns = 1_200_000 // block_count
    import netsquid as ns
    from qoala.lang.ehi import UnitModule
    from qoala.lang.parse import QoalaParser
    from qoala.runtime.config import (ClassicalConnectionConfig, LatenciesConfig,
        NtfConfig, ProcNodeConfig, ProcNodeNetworkConfig, TopologyConfigBuilder)
    from qoala.runtime.program import ProgramInput
    from qoala.runtime.task import LocalRoutineTask
    from qoala.runtime.taskbuilder import TaskGraphBuilder
    from qoala.sim.build import build_network_from_config
    from qoala.sim.scheduling.cpusched import CpuFcfsScheduler, CpuEdfScheduler
    from qoala.util.runner import create_batch

    assert block_count == 4
    ns.sim_reset()
    ns.set_qstate_formalism(ns.QFormalism.DM)
    ns.set_random_state(seed=int(seed))

    def node(name, node_id):
        topology = (TopologyConfigBuilder().uniform_topology().num_qubits(1)
            .qubit_t1(0).qubit_t2(t2_ns).default_generic_gates()
            .zero_gate_durations().perfect_gate_fidelities().build())
        return ProcNodeConfig(node_name=name, node_id=node_id, topology=topology,
            latencies=LatenciesConfig(host_instr_time=10_000, qnos_instr_time=1_000),
            ntf=NtfConfig.from_cls_name('GenericNtf'),
            fcfs=(name == 'alice' or scheduler == 'FCFS'),
            use_deadlines=(name == 'bob' and scheduler != 'FCFS'))

    config = ProcNodeNetworkConfig.from_nodes_perfect_links(
        nodes=[node('bob', 0), node('alice', 1)], link_duration=1_000)
    # Fixed or trial-jittered propagation; the same latency applies both ways.
    config.cconns = [ClassicalConnectionConfig.from_nodes(0, 1, int(one_way_ns))]
    network = build_network_from_config(config)
    bob, alice = network.nodes['bob'], network.nodes['alice']
    expected_class = CpuFcfsScheduler if scheduler == 'FCFS' else CpuEdfScheduler
    assert isinstance(bob.scheduler.cpu_scheduler, expected_class)
    assert isinstance(alice.scheduler.cpu_scheduler, CpuFcfsScheduler)

    bob_um = UnitModule.from_full_ehi(bob.memmgr.get_ehi())
    alice_um = UnitModule.from_full_ehi(alice.memmgr.get_ehi())
    victim_text = VICTIM if scheduler == 'EDF_UNDATED' else VICTIM_DEADLINES
    bob_batch = bob.submit_batch(create_batch(QoalaParser(victim_text).parse(), bob_um,
        [ProgramInput({'alice_id': 1})], 1))
    alice_batch = alice.submit_batch(create_batch(QoalaParser(PEER).parse(), alice_um,
        [ProgramInput({'bob_id': 0})], 1))

    const_batch = None
    if phase_ns is not None:
        const_batch = bob.submit_const_batch(create_batch(
            QoalaParser(payload_program(payload)).parse(), bob_um,
            [ProgramInput.empty() for _ in range(block_count)], block_count))

    bob.initialize_processes({bob_batch.batch_id:
        [p.pid for p in alice_batch.instances]})
    alice.initialize_processes({alice_batch.batch_id:
        [p.pid for p in bob_batch.instances]})

    competitor_pids = set()
    if const_batch is not None:
        # Same static workload-injection API as upstream runner.py. No attack
        # deadlines are set and no runtime selection method is replaced.
        graphs = []
        counter = bob.scheduler._task_from_block_builder._task_id_counter
        for i, inst in enumerate(const_batch.instances):
            competitor_pids.add(inst.pid)
            graph = TaskGraphBuilder.from_program(const_batch.info.program,
                inst.pid, bob.scheduler._local_ehi, bob.scheduler._network_ehi,
                first_task_id=counter,
                prog_input=const_batch.info.inputs[i].values)
            counter += len(graph.get_tasks())
            assert len(graph.get_tasks()) == 1, 'Expected one CPU task per payload'
            for info in graph.get_tasks().values():
                assert info.deadline is None, 'Competitor must not get a deadline'
                info.start_time = int(phase_ns)
            graphs.append(graph)
        bob.scheduler._task_from_block_builder._task_id_counter = counter
        # OnlineNodeScheduler's constant-batch path creates processes but does
        # not initialize its next-block cursor. Its completion loop reads that
        # cursor even for a constant process. Every block in these one-block
        # programs has already been compiled and submitted above, so the next
        # block index is len(blocks), matching normal online submission.
        # This only repairs harness bookkeeping; no scheduler ordering changes.
        for inst in const_batch.instances:
            bob.scheduler._curr_blk_idx[inst.pid] = len(inst.program.blocks)
        bob.scheduler.upload_task_graph(TaskGraphBuilder.merge(graphs))

    # Read-only graph observation: capture deadlines at task creation, before
    # scheduler execution subtracts elapsed task service from them.
    emitted = []
    builder = bob.scheduler._task_from_block_builder
    original_build = builder.build
    def observe_build(*args, **kwargs):
        graph = original_build(*args, **kwargs)
        for tid, info in graph.get_tasks().items():
            emitted.append(dict(task_id=tid, pid=info.task.pid,
                block=info.task.block_name, deadline=info.deadline,
                task_type=type(info.task).__name__, estimated_duration_ns=info.task.duration, created_ns=float(ns.sim_time())))
        return graph
    builder.build = observe_build

    network.start()
    ns.sim_run()
    if phase_ns is not None and payload == 'instructions':
        assert all(bob.memmgr.get_process(pid).host_mem.read('x') == 28
                   for pid in competitor_pids)
    values = bob.scheduler.get_batch_results()[bob_batch.batch_id].results[0].values
    assert values['reply'] == 1, 'Victim did not receive the correct response'
    annotated = [t for t in emitted if t['block'] in ['prepare','send','receive','measure']]
    assert len(annotated) == 8, 'Expected eight tasks in four annotated victim blocks'
    if scheduler == 'EDF_UNDATED':
        assert all(t['deadline'] is None for t in emitted)
    else:
        assert all(t['deadline'] == 0 for t in annotated), 'Native deadline semantics changed'
    outcome = int(values['m'])
    assert outcome in [0, 1]
    if t2_ns == 0:
        assert outcome == 0, 'Noise-free |+> measured in X must return zero'

    stats = bob.scheduler.get_statistics()
    cpu = []
    for tid, task in stats._cpu_tasks_executed.items():
        cpu.append(dict(task_id=tid, pid=task.pid, block=task.block_name,
            task_type=type(task).__name__, start_ns=stats._cpu_task_starts[tid],
            end_ns=stats._cpu_task_ends[tid]))
    cpu.sort(key=lambda x: x['start_ns'])
    for a, b in zip(cpu, cpu[1:]):
        assert b['start_ns'] >= a['end_ns'] - 1e-6, 'CPU tasks overlap'
    own = [t for t in cpu if t['pid'] in competitor_pids]
    expected = block_count if phase_ns is not None else 0
    assert len(own) == expected
    for task in own:
        assert task['block'] == 'payload'
        assert task['start_ns'] >= phase_ns - 1e-6
        assert abs(task['end_ns'] - task['start_ns'] - block_ns) < 1e-6
    service = sum(t['end_ns'] - t['start_ns'] for t in own)
    assert abs(service - expected*block_ns) < 1e-6

    qpu = []
    for tid, task in stats._qpu_tasks_executed.items():
        if isinstance(task, LocalRoutineTask):
            qpu.append(dict(task_id=tid, pid=task.pid, block=task.block_name,
                start_ns=stats._qpu_task_starts[tid], end_ns=stats._qpu_task_ends[tid]))
    prepared = [t for t in qpu if t['block'] == 'prepare']
    measured = [t for t in qpu if t['block'] == 'measure']
    assert len(prepared) == len(measured) == 1
    prep_end = prepared[0]['end_ns']
    measure_start = measured[0]['start_ns']
    assert measure_start >= prep_end
    overlap = sum(max(0., min(t['end_ns'], measure_start) -
                      max(t['start_ns'], prep_end)) for t in own)
    pre_call = [t for t in cpu if t['pid'] == 0 and t['block'] == 'measure' and t['task_type'] == 'PreCallTask']
    receive = [t for t in cpu if t['pid'] == 0 and t['block'] == 'receive']
    assert len(pre_call) == len(receive) == 1
    created = {t['task_id']: t['created_ns'] for t in emitted}
    assert pre_call[0]['task_id'] in created
    precall_wait = pre_call[0]['start_ns'] - created[pre_call[0]['task_id']]
    handoff = pre_call[0]['start_ns'] - receive[0]['end_ns']
    assert precall_wait >= 0 and handoff >= 0
    return dict(block_count=block_count if phase_ns is not None else 0,
        block_duration_ns=block_ns if phase_ns is not None else 0,
        measurement_precall_created_ns=created[pre_call[0]['task_id']],
        measurement_precall_wait_after_creation_ns=precall_wait,
        receive_end_to_measurement_precall_start_ns=handoff,
        measurement_precall_start_ns=pre_call[0]['start_ns'],
        receive_end_ns=receive[0]['end_ns'],
        x_error=outcome, phase_ns=phase_ns, t2_ns=t2_ns, seed=int(seed),
        scheduler=scheduler, actual_cpu_scheduler=type(bob.scheduler.cpu_scheduler).__name__,
        one_way_classical_latency_ns=int(one_way_ns), returned_reply=int(values['reply']),
        created_task_deadlines=emitted,
        mean_competitor_response_ns=(sum(t['end_ns']-phase_ns for t in own)/len(own) if own else None),
        max_competitor_response_ns=(max(t['end_ns']-phase_ns for t in own) if own else None),
        qpu_routine_gap_ns=measure_start-prep_end,
        competitor_service_ns=service,
        competitor_overlap_with_routine_gap_ns=overlap,
        trial_total_duration_ns=float(ns.sim_time()),
        cpu_trace=cpu, qpu_trace=qpu)

def run_dynamic(phase_ns, t2_ns, seed, scheduler, one_way_ns, block_count=4, payload="busy"):
    if block_count not in [1, 2, 4, 8, 16]:
        raise ValueError('Unsupported block count')
    block_ns = 1_200_000 // block_count
    import netsquid as ns
    from qoala.lang.ehi import UnitModule
    from qoala.lang.parse import QoalaParser
    from qoala.runtime.config import (ClassicalConnectionConfig, LatenciesConfig,
        NtfConfig, ProcNodeConfig, ProcNodeNetworkConfig, TopologyConfigBuilder)
    from qoala.runtime.program import ProgramInput
    from qoala.runtime.task import LocalRoutineTask
    from qoala.runtime.taskbuilder import TaskGraphBuilder
    from qoala.sim.build import build_network_from_config
    from qoala.sim.scheduling.cpusched import CpuFcfsScheduler, CpuEdfScheduler
    from qoala.util.runner import create_batch

    assert block_count == 4
    ns.sim_reset()
    ns.set_qstate_formalism(ns.QFormalism.DM)
    ns.set_random_state(seed=int(seed))

    def node(name, node_id):
        topology = (TopologyConfigBuilder().uniform_topology().num_qubits(1)
            .qubit_t1(0).qubit_t2(t2_ns).default_generic_gates()
            .zero_gate_durations().perfect_gate_fidelities().build())
        return ProcNodeConfig(node_name=name, node_id=node_id, topology=topology,
            latencies=LatenciesConfig(host_instr_time=10_000, qnos_instr_time=1_000),
            ntf=NtfConfig.from_cls_name('GenericNtf'),
            fcfs=(name == 'alice' or scheduler == 'FCFS'),
            use_deadlines=(name == 'bob' and scheduler != 'FCFS'))

    config = ProcNodeNetworkConfig.from_nodes_perfect_links(
        nodes=[node('bob', 0), node('alice', 1)], link_duration=1_000)
    # Fixed or trial-jittered propagation; the same latency applies both ways.
    config.cconns = [ClassicalConnectionConfig.from_nodes(0, 1, int(one_way_ns))]
    network = build_network_from_config(config)
    bob, alice = network.nodes['bob'], network.nodes['alice']
    expected_class = CpuFcfsScheduler if scheduler == 'FCFS' else CpuEdfScheduler
    assert isinstance(bob.scheduler.cpu_scheduler, expected_class)
    assert isinstance(alice.scheduler.cpu_scheduler, CpuFcfsScheduler)

    bob_um = UnitModule.from_full_ehi(bob.memmgr.get_ehi())
    alice_um = UnitModule.from_full_ehi(alice.memmgr.get_ehi())
    victim_text = VICTIM if scheduler == 'EDF_UNDATED' else VICTIM_DEADLINES
    bob_batch = bob.submit_batch(create_batch(QoalaParser(victim_text).parse(), bob_um,
        [ProgramInput({'alice_id': 1})], 1))
    alice_batch = alice.submit_batch(create_batch(QoalaParser(PEER).parse(), alice_um,
        [ProgramInput({'bob_id': 0})], 1))

    bob.initialize_processes({bob_batch.batch_id:
        [p.pid for p in alice_batch.instances]})
    alice.initialize_processes({alice_batch.batch_id:
        [p.pid for p in bob_batch.instances]})
    competitor_pids = set()
    submission = []

    # Simulator-side admission event. It submits ordinary program instances;
    # it is not evidence of a deployed, unprivileged tenant API.
    from netsquid.protocols import Protocol
    class Admission(Protocol):
        def run(self):
            if phase_ns > 0:
                yield self.await_timer(phase_ns)
            batch = bob.submit_batch(create_batch(
                QoalaParser(payload_program(payload)).parse(), bob_um,
                [ProgramInput.empty() for _ in range(4)], 4))
            for inst in batch.instances:
                competitor_pids.add(inst.pid)
                bob.scheduler.submit_program_instance(inst)
            submission.append(dict(requested_ns=int(phase_ns),
                admitted_ns=float(ns.sim_time()), pids=sorted(competitor_pids)))
            # Wake the stock online compiler/scheduler for these new instances.
            bob.scheduler.schedule_all()

    # Read-only graph observation: capture deadlines at task creation, before
    # scheduler execution subtracts elapsed task service from them.
    emitted = []
    builder = bob.scheduler._task_from_block_builder
    original_build = builder.build
    def observe_build(*args, **kwargs):
        graph = original_build(*args, **kwargs)
        for tid, info in graph.get_tasks().items():
            emitted.append(dict(task_id=tid, pid=info.task.pid,
                block=info.task.block_name, deadline=info.deadline,
                task_type=type(info.task).__name__, estimated_duration_ns=info.task.duration, created_ns=float(ns.sim_time())))
        return graph
    builder.build = observe_build

    network.start()
    admission = None
    if phase_ns is not None:
        admission = Admission()
        admission.start()
    ns.sim_run()
    if phase_ns is not None:
        assert len(submission) == 1 and submission[0]['admitted_ns'] == phase_ns
        assert all(bob.scheduler.is_prog_inst_finished(pid) for pid in competitor_pids)
        if payload == 'instructions':
            assert all(bob.memmgr.get_process(pid).host_mem.read('x') == 28
                       for pid in competitor_pids)
        generated = [t for t in emitted if t['pid'] in competitor_pids]
        assert len(generated) == 4 and all(t['deadline'] is None for t in generated)

    if phase_ns is not None and payload == 'instructions':
        assert all(bob.memmgr.get_process(pid).host_mem.read('x') == 28
                   for pid in competitor_pids)
    values = bob.scheduler.get_batch_results()[bob_batch.batch_id].results[0].values
    assert values['reply'] == 1, 'Victim did not receive the correct response'
    annotated = [t for t in emitted if t['block'] in ['prepare','send','receive','measure']]
    assert len(annotated) == 8, 'Expected eight tasks in four annotated victim blocks'
    if scheduler == 'EDF_UNDATED':
        assert all(t['deadline'] is None for t in emitted)
    else:
        assert all(t['deadline'] == 0 for t in annotated), 'Native deadline semantics changed'
    outcome = int(values['m'])
    assert outcome in [0, 1]
    if t2_ns == 0:
        assert outcome == 0, 'Noise-free |+> measured in X must return zero'

    stats = bob.scheduler.get_statistics()
    cpu = []
    for tid, task in stats._cpu_tasks_executed.items():
        cpu.append(dict(task_id=tid, pid=task.pid, block=task.block_name,
            task_type=type(task).__name__, start_ns=stats._cpu_task_starts[tid],
            end_ns=stats._cpu_task_ends[tid]))
    cpu.sort(key=lambda x: x['start_ns'])
    for a, b in zip(cpu, cpu[1:]):
        assert b['start_ns'] >= a['end_ns'] - 1e-6, 'CPU tasks overlap'
    own = [t for t in cpu if t['pid'] in competitor_pids]
    expected = block_count if phase_ns is not None else 0
    assert len(own) == expected
    for task in own:
        assert task['block'] == 'payload'
        assert task['start_ns'] >= phase_ns - 1e-6
        assert abs(task['end_ns'] - task['start_ns'] - block_ns) < 1e-6
    service = sum(t['end_ns'] - t['start_ns'] for t in own)
    assert abs(service - expected*block_ns) < 1e-6

    qpu = []
    for tid, task in stats._qpu_tasks_executed.items():
        if isinstance(task, LocalRoutineTask):
            qpu.append(dict(task_id=tid, pid=task.pid, block=task.block_name,
                start_ns=stats._qpu_task_starts[tid], end_ns=stats._qpu_task_ends[tid]))
    prepared = [t for t in qpu if t['block'] == 'prepare']
    measured = [t for t in qpu if t['block'] == 'measure']
    assert len(prepared) == len(measured) == 1
    prep_end = prepared[0]['end_ns']
    measure_start = measured[0]['start_ns']
    assert measure_start >= prep_end
    overlap = sum(max(0., min(t['end_ns'], measure_start) -
                      max(t['start_ns'], prep_end)) for t in own)
    pre_call = [t for t in cpu if t['pid'] == 0 and t['block'] == 'measure' and t['task_type'] == 'PreCallTask']
    receive = [t for t in cpu if t['pid'] == 0 and t['block'] == 'receive']
    assert len(pre_call) == len(receive) == 1
    created = {t['task_id']: t['created_ns'] for t in emitted}
    assert pre_call[0]['task_id'] in created
    precall_wait = pre_call[0]['start_ns'] - created[pre_call[0]['task_id']]
    handoff = pre_call[0]['start_ns'] - receive[0]['end_ns']
    assert precall_wait >= 0 and handoff >= 0
    return dict(admission_events=submission, block_count=block_count if phase_ns is not None else 0,
        block_duration_ns=block_ns if phase_ns is not None else 0,
        measurement_precall_created_ns=created[pre_call[0]['task_id']],
        measurement_precall_wait_after_creation_ns=precall_wait,
        receive_end_to_measurement_precall_start_ns=handoff,
        measurement_precall_start_ns=pre_call[0]['start_ns'],
        receive_end_ns=receive[0]['end_ns'],
        x_error=outcome, phase_ns=phase_ns, t2_ns=t2_ns, seed=int(seed),
        scheduler=scheduler, actual_cpu_scheduler=type(bob.scheduler.cpu_scheduler).__name__,
        one_way_classical_latency_ns=int(one_way_ns), returned_reply=int(values['reply']),
        created_task_deadlines=emitted,
        mean_competitor_response_ns=(sum(t['end_ns']-phase_ns for t in own)/len(own) if own else None),
        max_competitor_response_ns=(max(t['end_ns']-phase_ns for t in own) if own else None),
        qpu_routine_gap_ns=measure_start-prep_end,
        competitor_service_ns=service,
        competitor_overlap_with_routine_gap_ns=overlap,
        trial_total_duration_ns=float(ns.sim_time()),
        cpu_trace=cpu, qpu_trace=qpu)

# Q6 frozen diagnostic orchestration; no confirmatory inference.
import os, resource, socket
from datetime import datetime, timezone
from collections import defaultdict

ROUTES=['static_busy','static_instructions','dynamic_busy','dynamic_instructions']
POLICIES=['FCFS','EDF_NATIVE']
TIMINGS=['fixed','message_jitter']
ARMS=['no_competitor','selected','random_phase']
ROOT_SEED=61001

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def stable_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def write_new(path,value):
    with Path(path).open('x') as f:json.dump(value,f,indent=2);f.write('\n')

def inputs_for(spec):
    import numpy as np
    def stream(k):return np.random.default_rng(np.random.SeedSequence([ROOT_SEED,k,spec['run']]))
    seed=int(stream(1).integers(1,2**31-1))
    latency=1_000_000 if spec['timing']=='fixed' else int(stream(2).integers(500_000,1_500_001))
    phase=None
    if spec['condition']=='selected':
        phase=2_000_000 if spec['timing']=='fixed' else {'FCFS':1_250_000,'EDF_NATIVE':1_750_000}[spec['scheduler']]
    elif spec['condition']=='random_phase':phase=int(stream(3).integers(0,4_000_000))
    return phase,latency,seed

def make_jobs():
    jobs=[]
    for timing in TIMINGS:
        for scheduler in POLICIES:
            for arm in ARMS:
                for route in ROUTES:
                    specs=[]
                    for noise,n in [('noise_free',4),('dephasing',32)]:
                        for i in range(n):
                            specs.append(dict(route=route,timing=timing,scheduler=scheduler,
                                condition=arm,noise=noise,run=i))
                    jobs.append(dict(id=f'{timing}-{scheduler}-{arm}-{route}',specs=specs))
    return jobs

def check_repo(repo):
    if not __debug__:raise RuntimeError('Assertions must remain enabled')
    repo=Path(repo).resolve()
    head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(repo),'diff','HEAD','--name-only'],text=True).strip()
    if head!=PIN or dirty:raise RuntimeError('Expected a clean pinned Qoala checkout')
    sys.path.insert(0,str(repo))
    import qoala
    if not Path(qoala.__file__).resolve().is_relative_to(repo):raise RuntimeError('Wrong Qoala import')
    expected={'netsquid':'1.1.8','pydynaa':'1.0.2','qoala':'2.1.0','numpy':'1.26.4','netqasm':'2.3.0'}
    versions={k:importlib.metadata.version(k) for k in expected}
    if sys.version_info[:2]!=(3,10) or versions!=expected:
        raise RuntimeError(f'Validated environment required: Python 3.10 and {expected}; found {sys.version} {versions}')
    return dict(python=sys.version,versions=versions,repo_head=head)

def worker(job_path,output,repo):
    check_repo(repo)
    job=json.loads(Path(job_path).read_text())
    if job['source_sha256']!=digest(__file__):raise RuntimeError('Source hash mismatch')
    started=time.monotonic();cpu_start=time.process_time()
    with Path(output).open('x') as f:
        for i,spec in enumerate(job['specs'],1):
            phase,latency,seed=inputs_for(spec)
            mode,payload=spec['route'].split('_',1)
            trial=run_static if mode=='static' else run_dynamic
            row=trial(phase,0 if spec['noise']=='noise_free' else T2_NS,seed,
                spec['scheduler'],latency,payload=payload)
            row.update(spec)
            f.write(json.dumps(row,separators=(',',':'))+'\n');f.flush()
            if i%12==0:print(f"{job['id']}: {i}/{len(job['specs'])}",flush=True)
    return dict(trials=len(job['specs']),wall_seconds=time.monotonic()-started,
        cpu_seconds=time.process_time()-cpu_start,peak_worker_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)

def verify_batch(raw_path,job):
    rows=[json.loads(line) for line in Path(raw_path).read_text().splitlines()]
    if len(rows)!=len(job['specs']):raise RuntimeError('Incomplete batch')
    for r,s in zip(rows,job['specs']):
        if any(r[k]!=v for k,v in s.items()):raise RuntimeError('Batch specification mismatch')
        phase,latency,seed=inputs_for(s)
        if (r['phase_ns'],r['one_way_classical_latency_ns'],r['seed'])!=(phase,latency,seed):
            raise RuntimeError('Unpaired or modified inputs')
        if r['x_error'] not in [0,1] or r['returned_reply']!=1:raise RuntimeError('Invalid victim outcome')
        if s['noise']=='noise_free' and r['x_error']!=0:raise RuntimeError('Noise-free error')
        budget=0 if phase is None else 1_200_000
        if abs(r['competitor_service_ns']-budget)>1e-6:raise RuntimeError('Service budget mismatch')
    return rows

def baseline_signature(r):
    # Ignore route labels and internal task identifiers, retain all observed timing.
    return dict(x_error=r['x_error'],gap=r['qpu_routine_gap_ns'],
        cpu=[{k:t[k] for k in ['pid','block','task_type','start_ns','end_ns']} for t in r['cpu_trace']],
        qpu=[{k:t[k] for k in ['pid','block','start_ns','end_ns']} for t in r['qpu_trace']])

def summarize(rows):
    grouped=defaultdict(list)
    for r in rows:grouped[tuple(r[k] for k in ['route','timing','scheduler','condition','noise'])].append(r)
    out=[]
    for key,rr in sorted(grouped.items()):
        n=len(rr)
        out.append(dict(zip(['route','timing','scheduler','condition','noise'],key),trials=n,
            errors=sum(r['x_error'] for r in rr),x_error_rate=sum(r['x_error'] for r in rr)/n,
            mean_gap_ms=sum(r['qpu_routine_gap_ns'] for r in rr)/n/1e6,
            mean_competitor_service_ms=sum(r['competitor_service_ns'] for r in rr)/n/1e6))
    return out

def compare_routes(rows):
    idx={(r['route'],r['timing'],r['scheduler'],r['condition'],r['noise'],r['run']):r for r in rows}
    out=[]
    pairs=[('dynamic_busy','static_busy'),('dynamic_instructions','dynamic_busy'),
           ('static_instructions','static_busy'),('dynamic_instructions','static_instructions')]
    for timing in TIMINGS:
        for scheduler in POLICIES:
            for arm in ARMS:
                for a,b in pairs:
                    rr=[(idx[(a,timing,scheduler,arm,'dephasing',i)],idx[(b,timing,scheduler,arm,'dephasing',i)]) for i in range(32)]
                    out.append(dict(timing=timing,scheduler=scheduler,condition=arm,
                        candidate=a,reference=b,trials=32,
                        error_difference_pp=100*sum(x['x_error']-y['x_error'] for x,y in rr)/32,
                        different_outcomes=sum(x['x_error']!=y['x_error'] for x,y in rr),
                        different_routine_gaps=sum(abs(x['qpu_routine_gap_ns']-y['qpu_routine_gap_ns'])>1e-6 for x,y in rr),
                        mean_gap_difference_ms=sum(x['qpu_routine_gap_ns']-y['qpu_routine_gap_ns'] for x,y in rr)/32/1e6,
                        inference='Diagnostic only; no significance or equivalence claim'))
    return out

def coordinate(repo,root):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    lock=root/'RUNNING.lock'
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise RuntimeError('Run lock exists. Verify no worker is active before removing a stale lock.')
    try:
        with os.fdopen(fd,'w') as f:f.write(f'{socket.gethostname()} {os.getpid()}\n')
        env_info=check_repo(repo)
        from qoala.lang.parse import QoalaParser
        for payload in ['busy','instructions']:
            parsed=QoalaParser(payload_program(payload)).parse()
            assert len(parsed.blocks)==1
            assert len(parsed.blocks[0].instructions)==(1 if payload=='busy' else 30)
        protocol=dict(revision='q6-diagnostic-1',source_sha256=digest(__file__),pin=PIN,
            root_seed=ROOT_SEED,trials=1728,noisy_per_cell=32,controls_per_cell=4,
            routes=ROUTES,schedulers=POLICIES,timings=TIMINGS,arms=ARMS,
            inference='Diagnostic, not confirmatory',known_start=True,
            scope='Ordinary simulator program admission; not a deployed tenant privilege claim')
        if (root/'protocol.json').exists():
            if json.loads((root/'protocol.json').read_text())!=protocol:raise RuntimeError('Protocol/source changed; use a new run folder')
        else:write_new(root/'protocol.json',protocol)
        source=root/'experiment_source.py'
        if source.exists() and digest(source)!=digest(__file__):raise RuntimeError('Source snapshot differs')
        if not source.exists():source.write_bytes(Path(__file__).read_bytes())
        if not (root/'environment.json').exists():write_new(root/'environment.json',env_info)
        for payload in ['busy','instructions']:
            path=root/(payload+'.iqoala')
            if not path.exists():path.write_text(payload_program(payload))
        env=os.environ.copy()
        for k in ['PYTHONPATH','PYTHONHOME','PYTHONOPTIMIZE']:env.pop(k,None)
        env.update(PYTHONNOUSERSITE='1',PYTHONUNBUFFERED='1',PYTHONHASHSEED='0')
        all_rows=[];manifests=[]
        jobs=make_jobs()
        for j,base in enumerate(jobs,1):
            job=dict(base,source_sha256=digest(__file__))
            folder=root/'batches'/job['id'];folder.mkdir(parents=True,exist_ok=True)
            marker=folder/'completed.json'
            jobpath=folder/'job.json'
            if jobpath.exists():
                if json.loads(jobpath.read_text())!=job:raise RuntimeError('Job changed')
            else:write_new(jobpath,job)
            print(f"Batch {j}/{len(jobs)}: {job['id']}",flush=True)
            if marker.exists():
                m=json.loads(marker.read_text());raw=folder/m['raw_file']
                if m['job_sha256']!=stable_hash(job) or m['raw_sha256']!=digest(raw):raise RuntimeError('Completed batch modified')
                print('Reusing verified batch',flush=True)
            else:
                stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                raw=folder/(stamp+'.jsonl');stats=folder/(stamp+'-stats.json')
                cmd=[sys.executable,'-u',__file__,'worker','--repo',str(repo),'--job',str(jobpath),'--output',str(raw),'--stats',str(stats)]
                with (folder/(stamp+'.log')).open('w') as log:
                    p=subprocess.Popen(cmd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
                    try:
                        for line in p.stdout:print(line,end='',flush=True);log.write(line);log.flush()
                        if p.wait()!=0:raise RuntimeError('Worker failed; export this run folder including logs')
                    finally:
                        if p.poll() is None:p.terminate();p.wait()
                verify_batch(raw,job)
                m=dict(job_id=job['id'],job_sha256=stable_hash(job),raw_file=raw.name,
                    raw_sha256=digest(raw),statistics=json.loads(stats.read_text()))
                write_new(marker,m)
            all_rows+=verify_batch(raw,job);manifests.append(m)
            st=m['statistics'];print(f"CPU {st['cpu_seconds']:.2f}s | peak worker RAM {st['peak_worker_rss_mib']:.1f} MiB",flush=True)
        baselines=defaultdict(list)
        for r in all_rows:
            if r['condition']=='no_competitor':baselines[(r['timing'],r['scheduler'],r['noise'],r['run'])].append(baseline_signature(r))
        for key,values in baselines.items():
            if len(values)!=4 or any(v!=values[0] for v in values[1:]):raise RuntimeError(f'No-competitor baseline mismatch: {key}')
        outputs={'summary.json':summarize(all_rows),'route_comparisons.json':compare_routes(all_rows),
            'manifest.json':manifests,'COMPLETE.json':dict(trials=len(all_rows),controls_zero=True,
                baseline_agreement=True,equal_service_verified=True,inference='Diagnostic only',
                source_sha256=digest(__file__))}
        for name,value in outputs.items():
            path=root/name
            if path.exists():
                if json.loads(path.read_text())!=value:raise RuntimeError(f'Existing summary differs: {name}')
            else:write_new(path,value)
        print('Q6 diagnostic completed. Export the ZIP for independent analysis.',flush=True)
    finally:
        lock.unlink(missing_ok=True)

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='mode',required=True)
    r=sub.add_parser('run');r.add_argument('--repo',type=Path,required=True);r.add_argument('--run-dir',type=Path,required=True)
    w=sub.add_parser('worker')
    for a in ['repo','job','output','stats']:w.add_argument('--'+a,type=Path,required=True)
    args=p.parse_args()
    if args.mode=='worker':write_new(args.stats,worker(args.job,args.output,args.repo))
    else:coordinate(args.repo,args.run_dir)

if __name__=='__main__':main()
