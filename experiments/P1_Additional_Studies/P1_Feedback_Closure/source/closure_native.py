"""New native adapters, using frozen parents through normal program submission.

The imported NetSquid engine is mandatory: no analytic/synthetic fallback.
All instrumentation is evaluator-only. The planner executes before private
input generation and cannot receive target traces, states or results.
"""
import sys
from pathlib import Path
import closure_design as d
from public_policy import plan
ROOT=Path(__file__).resolve().parent

def primary(public,parent):
    # Order is intentional: construct the attacker plan before private inputs.
    offers=plan(public)
    sys.path.insert(0,str(ROOT/'primary_parent'))
    import q12_design as old
    from p1_engine import Case,ordered_cpu_trace
    from netsquid.protocols import Protocol
    inp=old.inputs(parent)
    c=Case('FCFS',inp['latency_ns'],0 if parent['noise']=='noise_free' else old.T2_NS,inp['seed'],parent['workload'])
    target={};admissions=[];job_inputs={}
    names=['v%d'%i for i in range(28)]
    program=('META_START\n name: runtime_reduction\n parameters: '+', '.join(names)+'\n csockets:\n epr_sockets:\nMETA_END\n^payload {type = CL}:\n'+
      '\n'.join('    '+x for x in ['total = assign_cval() : 0']+['total = add_cval_c(total, '+k+')' for k in names]+['return_result(total)'])+'\n')
    class Target(Protocol):
        def run(self):
            yield self.await_timer(inp['target_request_ns'])
            admissions.append(float(c.ns.sim_time()));target.update(c.victim(inp['state'],dynamic=True))
    class Competing(Protocol):
        def run(self):
            for offer in offers:
                delay=offer['release_ns']-c.ns.sim_time()
                if delay<0:raise ValueError('Missed public release')
                if delay:yield self.await_timer(delay)
                values={name:int(d.stable(['runtime-input',public['own_random_seed'],offer['job_index'],name])[:8],16)%20001-10000 for name in names}
                batch=c._batch(c.bob,program,[values]);instance=batch.instances[0]
                c.bob.scheduler.submit_program_instance(instance)
                c.jobs.append(dict(pid=instance.pid,requested_ns=offer['release_ns'],admitted_ns=float(c.ns.sim_time()),instructions=30,kind='competitor'))
                job_inputs[str(instance.pid)]=values
                c.bob.scheduler.schedule_all()
    c.net.start();protocols=[Target(),Competing()]
    for protocol in protocols:protocol.start()
    c.ns.sim_run(duration=old.HORIZON_NS)
    if not target or len(admissions)!=1:raise ValueError('Missing target')
    out=c.outcome(target);traces=c.traces()
    peer=c.alice.scheduler.get_statistics()
    peer_cpu=ordered_cpu_trace([dict(task_id=t,pid=v.pid,block=v.block_name,task_type=type(v).__name__,
        start_ns=float(peer._cpu_task_starts[t]),end_ns=float(peer._cpu_task_ends[t])) for t,v in peer._cpu_tasks_executed.items()])
    results={str(j['pid']):dict(c.bob.memmgr.get_process(j['pid']).result.values) for j in c.jobs}
    return dict(parent_spec=parent,public=public,public_plan=offers,private_inputs=inp,target=target,
        outcomes=out,target_admitted_ns=admissions[0],target_finished_ns=c.finished_pids['bob'].get(target['bob_pid']),
        quantum_events=c.events,cpu_trace=traces['cpu'],qpu_trace=traces['qpu'],peer_cpu_trace=peer_cpu,
        jobs=c.jobs,job_inputs=job_inputs,job_results=results,sim_end_ns=float(c.ns.sim_time()),
        target_program_parent='unchanged Q12 programs',competitor_program=program)

def application(spec):
    sys.path.insert(0,str(ROOT/'application_parent'))
    from app_native import run
    from app_audit import validate_record
    raw=run(spec)
    return raw,validate_record(raw,spec)
