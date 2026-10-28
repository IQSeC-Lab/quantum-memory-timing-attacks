#!/usr/bin/env python3
"""Independent, read-only raw-event audit of P1 E1.

Reads the original E1 run and server-side raw.jsonl.gz traces. Does not import
Qoala/NetSquid, run simulations, mutate source, or rewrite experiment records.
Writes only to a separate report directory. Python 3.10+ standard library.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import zip_longest
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time
import traceback
import zipfile

VERSION = 'e1-raw-independent-audit-v1.1'
EXPECTED_RUN = 'e1-20261025T040643708320Z'
E1_VERSION = 'p1-e1-calibration-cost-v1'
EXPECTED_SOURCE_SHA = 'e75bf81ee0298f39531cf40c4549af078b24b97c2f88207fd031516f3f444a74'
EXPECTED_FROZEN_SHA = 'ae93c00467b67e239b85cc7799d0b3ef21913ca6c6092689c634d6e187853b96'
EPOCH = 3_000_000
HORIZON = 30_000_000
SERVICE_PER_JOB_NS = 300_000
TOTAL_SERVICE_NS = 1_200_000
TOL = 1e-6
ARMS = (
    'q12_full256','low08','low32','low64','global_loo',
    'fixed_0ms','fixed_2ms','random_phase','no_competitor'
)
EXPECTED_BATCHES = {'pilot': 9, 'evaluation': 576}
EXPECTED_RECORDS = {'pilot': 1152, 'evaluation': 73728}

class AuditError(Exception):
    pass

def ensure(ok, message):
    if not ok:
        raise AuditError(str(message))

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def stable(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True,
                        separators=(',',':'), allow_nan=False).encode()).hexdigest()

def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while True:
            b = f.read(1024*1024)
            if not b: break
            h.update(b)
    return h.hexdigest()

def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)

def near(a,b, tol=TOL):
    return finite(a) and finite(b) and abs(a-b)<=tol

def verify_file_hash(path, expected, label):
    ensure(path.is_file(), f'{label}: missing {path}')
    actual=file_sha(path)
    ensure(actual==expected, f'{label}: SHA-256 mismatch for {path}, expected {expected}, got {actual}')
    return actual

def cpu_chronology(rows, label):
    ensure(isinstance(rows,list), label+': non-list trace')
    ordered = sorted(rows,key=lambda t:(t['start_ns'],t['end_ns'],t['task_id']))
    ensure(rows==ordered, label+': CPU trace is not ordered')
    ids = [r['task_id'] for r in rows]
    ensure(len(ids)==len(set(ids)), label+': duplicate CPU task IDs')
    for i,r in enumerate(ordered):
        a,b=r['start_ns'],r['end_ns']
        ensure(finite(a) and finite(b) and 0<=a<=b<=HORIZON, label+': invalid CPU interval')
        if i:
            ensure(a>=ordered[i-1]['end_ns']-TOL,
                   label+': CPU intervals overlap or chronology inconsistent')
    return sum(t['end_ns']-t['start_ns'] for t in rows)

def simple_trace(rows,label):
    ensure(isinstance(rows,list),label+': non-list trace')
    for t in rows:
        ensure(finite(t['start_ns']) and finite(t['end_ns']) and
               0<=t['start_ns']<=t['end_ns']<=HORIZON,label+': invalid interval')

def check_record(wrapper, compact, task, phases, stage, idx, case):
    tag=f'{stage}/batch-{idx:05d}/record-{case}: '
    ensure(isinstance(wrapper,dict) and 'q12_raw' in wrapper,tag+'invalid raw wrapper')
    ensure(wrapper.get('policy')==task['policy'] and wrapper.get('task')==task,
           tag+'raw task/policy differs from job')
    ensure(compact.get('e1_task')==task and compact.get('e1_policy')==task['policy'],
           tag+'compact task/policy differs from job')
    r=wrapper['q12_raw']; spec=r['spec']; inp=r['inputs']; arm=task['policy']
    ensure(spec['stage']=='evaluation' and spec['workload']=='teleport' and
           spec['scheduler']=='FCFS' and spec['scenario']=='primary_0500us' and
           spec['campaign']==task['campaign'] and spec['shot']==task['shot'] and
           spec['noise']==task['noise'],tag+'raw specification mismatch')
    spec_arm='no_competitor' if arm=='no_competitor' else 'random_phase' if arm=='random_phase' else 'learned'
    ensure(spec['arm']==spec_arm and spec.get('fixed_state')==task['fixed_state'],tag+'raw simulator arm/fixed state mismatch')
    ensure(compact['spec']==spec and inp.get('target_owner')=='victim',tag+'spec or owner mismatch')
    ensure(r['actual_cpu_scheduler']=='CpuFcfsScheduler',tag+'unexpected scheduler')
    ensure(r['public_epoch_ns']==EPOCH and near(r['sim_end_ns'],HORIZON),tag+'epoch/horizon mismatch')
    ensure(isinstance(inp['state'],int) and 0<=inp['state']<6,tag+'invalid prepared state')
    ensure(near(r['target_admitted_ns'],inp['target_request_ns']),tag+'target admission differs from requested time')
    ensure(near(inp['target_request_ns'],compact['target_request_ns']),tag+'request time differs from compact')
    ensure(-500000<=inp['target_request_ns']-EPOCH<=500000,tag+'request-time uncertainty exceeded')
    ensure(compact['state']==inp['state'],tag+'state differs from compact')
    selected=phases[str(task['campaign'])][arm]
    ensure(r['selected_phase_ns']==selected and compact['selected_phase_ns']==selected,
           tag+'selected phase differs from frozen policies')
    planned=(None if arm=='no_competitor' else EPOCH+(inp['blind_phase_ns'] if arm=='random_phase' else selected))
    ensure(r['planned_release_ns']==planned and compact['planned_release_ns']==planned,
           tag+'actual planned release differs from frozen plan')
    ensure(r['target_pid'] is not None,tag+'target pid missing')
    cpu_ns=cpu_chronology(r['cpu_trace'],tag+'bob CPU')
    peer_cpu_ns=cpu_chronology(r['peer_cpu_trace'],tag+'alice CPU')
    simple_trace(r['qpu_trace'],tag+'bob QPU')
    ensure(near(cpu_ns,compact['bob_cpu_service_ns']) and
           near(peer_cpu_ns,compact['alice_cpu_service_ns']),tag+'CPU service differs from compact')
    for t in r['cpu_trace']:
        if t['pid']==r['target_pid']:
            ensure(t['start_ns']>=r['target_admitted_ns']-TOL,tag+'target CPU before admission')
    jobs=r['admission_events']
    ensure(isinstance(jobs,list),tag+'missing admission events')
    ids=[j['pid'] for j in jobs]
    ensure(len(ids)==len(set(ids)),tag+'reused job pids')
    payloads={t['pid']:t for t in r['cpu_trace'] if t.get('block')=='payload'}
    ensure(set(payloads)==set(ids) and len(payloads)==len(ids),tag+'missing/extra CPU payload')
    competitors=[j for j in jobs if j['kind']=='competitor']
    benign=[j for j in jobs if j['kind']=='benign']
    ensure(len(competitors)==(0 if arm=='no_competitor' else 4),tag+'incorrect competitor job count')
    ensure([j['requested_ns'] for j in benign]==inp['background'],tag+'benign release requests differ')
    actual_competitor_service=0.0
    for j in jobs:
        t=payloads[j['pid']]
        ensure(j['kind'] in ('competitor','benign'),tag+'unexpected admission kind')
        ensure(j['instructions']==(30 if j['kind']=='competitor' else 10),tag+'unexpected job instructions')
        ensure(all(finite(v) for v in [j['requested_ns'],j['admitted_ns'],t['start_ns'],t['end_ns']]),tag+'nonfinite service timing')
        ensure(near(j['requested_ns'],j['admitted_ns']),tag+'admitted time differs from job request')
        ensure(t['start_ns']>=j['admitted_ns']-TOL,tag+'payload starts before admission')
        service=t['end_ns']-t['start_ns']
        ensure(near(service,j['instructions']*10000),tag+'actual instructions/service mismatch')
        if j['kind']=='competitor':
            ensure(j['requested_ns']==planned,tag+'competitor release differs from schedule')
            actual_competitor_service+=service
    expected_total=0 if arm=='no_competitor' else TOTAL_SERVICE_NS
    ensure(near(actual_competitor_service,expected_total),tag+'wrong actually executed competitor CPU budget')
    ensure(near(compact['competitor_service_ns'],actual_competitor_service),tag+'compact service differs from event service')
    ev=r['quantum_events']
    ensure(isinstance(ev,list),tag+'missing quantum events')
    instr=[e for e in ev if e['kind']=='instruction']
    eprs=[e for e in ev if e['kind']=='epr_ready']
    ensure(len(instr)==compact['qnos_instruction_count'] and
           len(eprs)==compact['completed_epr_pairs'],tag+'quantum event count differs from compact')
    for e in ev:
        if 'start_ns' in e:
            ensure(finite(e['start_ns']) and finite(e.get('end_ns')) and
                   0<=e['start_ns']<=e['end_ns']<=HORIZON,tag+'invalid quantum instruction interval')
        elif 'end_ns' in e:
            ensure(finite(e['end_ns']) and 0<=e['end_ns']<=HORIZON,tag+'invalid quantum readiness timestamp')
    ensure(r['completed'] is True and r['timeout'] is False,tag+'incomplete raw result')
    ensure(compact['unfinished']==0 and compact['application_failure']==int(r['error']) and
           compact['wrong_output']==int(r['wrong_output'])==int(r['error']) and
           r['wrong_output'] in (0,1),tag+'outcome/status differs from compact')
    ensure(r['bell_bits']==compact['bell_bits'] and len(r['bell_bits'])==2 and
           all(v in (0,1) for v in r['bell_bits']),tag+'teleportation bell bits inconsistent')
    ensure(r['target_finished_ns'] is not None and
           r['target_finished_ns']>=r['target_admitted_ns'] and
           r['target_finished_ns']<=HORIZON,tag+'invalid completion')
    ensure(near(r['target_finished_ns'],compact['target_finished_ns']),tag+'completion time mismatch')
    bob_eprs=[e for e in eprs if e['pid']==r['target_pid']]
    verify=[e for e in instr if e['pid']==r['target_pid'] and
            e.get('routine')=='verify_state' and str(e.get('name','')).startswith('rot_')]
    ensure(len(bob_eprs)==len(verify)==1,tag+'expected exactly one receiver EPR and verification rotation')
    physical=verify[0]['start_ns']-bob_eprs[0]['end_ns']
    ensure(bob_eprs[0]['end_ns']>=r['target_admitted_ns']-TOL and
           verify[0]['end_ns']<=r['target_finished_ns']+TOL,
           tag+'quantum EPR/readout events are outside the target execution interval')
    ensure(physical>=0 and near(r['physical_storage_ns'],physical) and
           near(compact['physical_storage_ns'],physical) and
           near(r['state_use_ns'],verify[0]['start_ns']),tag+'physical state-storage interval mismatch')
    if task['noise']=='noise_free':
        ensure(r['wrong_output']==0,tag+'noise-free control incorrect')
    return dict(campaign=task['campaign'],shot=task['shot'],policy=arm,
                state=inp['state'],request_ns=inp['target_request_ns'],
                planned_release_ns=planned,wrong=int(r['wrong_output']),
                storage_ns=physical,actual_competitor_service_ns=actual_competitor_service,
                completed=1)

def validate_identity(root):
    ensure(root.name==EXPECTED_RUN,f'Unexpected E1 run {root.name}; expected {EXPECTED_RUN}')
    proto=read_json(root/'protocol.json')
    ensure(proto['version']==E1_VERSION, 'Wrong E1 protocol version')
    ensure(proto['source_sha256']==EXPECTED_SOURCE_SHA,'Wrong frozen E1 source identity')
    ensure(proto['counts']['arms']==list(ARMS) and proto['counts']['evaluation']==73728,
           'E1 arms or count differs from expected protocol')
    frozen=read_json(root/'frozen_policies.json')
    ensure(stable(frozen)==proto['policies_sha256'],'Frozen policy digest mismatch')
    ensure(file_sha(root/'frozen_policies.json')==EXPECTED_FROZEN_SHA,
           'Frozen policy file hash differs from original reported experiment')
    ensure(file_sha(root/'analysis.json')==read_json(root/'COMPLETE.json')['analysis_sha256'],
           'Analysis differs from COMPLETE')
    ensure(file_sha(root/'protocol.json')==read_json(root/'COMPLETE.json')['protocol_sha256'],
           'Protocol differs from COMPLETE')
    pilotpass=read_json(root/'PILOT_PASSED.json')
    ensure(pilotpass['trials']==1152 and
           pilotpass['pilot_resources']==read_json(root/'pilot'/'resources.json'),
           'Unexpected pilot receipt or resource summary')
    ensure(read_json(root/'COMPLETE.json')['status'].startswith('All E1 simulations completed'),
           'E1 COMPLETE status is not final')
    return proto,frozen

def verify_sources(root, proto):
    """Verify server code by hash without importing or executing it."""
    project=root.parent.parent
    q12_path=project/'experiment_code'/'q12-470078ab4b2c014d'
    original=project/'experiment_code'/'e1-b1a9477d54794bab'/'p1_e1.py'
    ensure(original.is_file(),'Missing installed E1 source: '+str(original))
    verify_file_hash(original, proto['source_sha256'],'E1 source')
    for name,sha in proto['q12']['q12_source_hashes'].items():
        verify_file_hash(q12_path/name,sha,'Q12 source')
    q12root=Path(proto['q12']['q12_run_dir']).expanduser().resolve()
    q12checks={
        'protocol.json':'parent_protocol_sha256',
        'calibration/manifest.json':'calibration_manifest_sha256',
        'frozen_selections.json':'frozen_selections_sha256',
        'COMPLETE.json':'q12_complete_sha256',
    }
    for rel,key in q12checks.items():
        verify_file_hash(q12root/rel,proto['q12'][key],'Q12 frozen calibration identity')
    return {'e1_source':str(original),'q12_source_dir':str(q12_path),
            'q12_parent_run':str(q12root),
            'source_files_verified':1+len(proto['q12']['q12_source_hashes']),
            'q12_parent_identity_files_verified':len(q12checks)}

def stage_metadata(root,stage):
    path=root/stage
    manifest=read_json(path/'manifest.json')
    resources=read_json(path/'resources.json')
    ensure(len(manifest)==EXPECTED_BATCHES[stage],stage+': wrong manifest batch count')
    ensure(resources['batches']==EXPECTED_BATCHES[stage] and
           resources['trials']==EXPECTED_RECORDS[stage],stage+': wrong resource counts')
    ensure(resources['stage_manifest_sha256']==file_sha(path/'manifest.json'),
           stage+': manifest SHA mismatch')
    jobs=[]
    for i,item in enumerate(manifest):
        ensure(item['index']==i,stage+': out-of-order manifest index')
        folder=path/f'batch-{i:05d}'
        marker=read_json(folder/'complete.json')
        ensure({k:v for k,v in item.items() if k not in ('index','stats')}==marker,
               stage+': checkpoint marker differs from manifest')
        job=read_json(folder/'job.json')
        ensure(stable(job)==marker['job_hash'],stage+': job hash mismatch')
        ensure(job['stage']==stage,stage+': unexpected stage in job')
        ensure(len(job['tasks'])==128 and all(t['policy'] in ARMS for t in job['tasks']),
               stage+': job size/policy differs from frozen design')
        jobs.append((stage,i,folder,job,marker,item['stats']))
    ensure(sum(len(j[3]['tasks']) for j in jobs)==EXPECTED_RECORDS[stage],
           stage+': task total mismatch')
    ensure(near(sum(v['stats']['cpu_seconds'] for v in manifest),resources['worker_cpu_s']) and
           near(sum(v['stats']['wall_seconds'] for v in manifest),resources['worker_wall_s']),
           stage+': CPU/wall summaries disagree with manifest')
    ensure(near(max(v['stats']['peak_rss_mib'] for v in manifest),
                resources['max_worker_ram_mib']),stage+': peak RSS disagrees')
    return jobs, resources

def audit_batch(args):
    stage,idx,folder,job,marker,manifest_stats=args
    for kind in ('raw','compact','stats'):
        fn=marker[kind+'_file']
        ensure(Path(fn).name==fn and '/' not in fn, f'{stage}/batch-{idx:05d}: unsafe file basename')
        verify_file_hash(folder/fn,marker[kind+'_sha256'],f'{stage}/batch-{idx:05d}/{kind}')
    stats=read_json(folder/marker['stats_file'])
    ensure(stats==manifest_stats and stats['trials']==len(job['tasks']),
           f'{stage}/batch-{idx:05d}: worker stats mismatch')
    results=[]
    with gzip.open(folder/marker['raw_file'],'rt',encoding='utf-8') as fr,\
         gzip.open(folder/marker['compact_file'],'rt',encoding='utf-8') as fc:
        for i,(raw_line,compact_line) in enumerate(zip_longest(fr,fc)):
            ensure(raw_line is not None and compact_line is not None,
                   f'{stage}/batch-{idx:05d}: raw and compact record counts differ')
            ensure(i<len(job['tasks']),f'{stage}/batch-{idx:05d}: too many records')
            raw=json.loads(raw_line)
            compact=json.loads(compact_line)
            results.append(check_record(raw,compact,job['tasks'][i],job['phases'],stage,idx,i))
    ensure(len(results)==len(job['tasks']),f'{stage}/batch-{idx:05d}: incomplete case count')
    return (stage,idx,results,{'stage':stage,'index':idx,'records':len(results),
             'raw_sha256':marker['raw_sha256'],
             'compact_sha256':marker['compact_sha256'],
             'stats_sha256':marker['stats_sha256']})

def evaluate_cross_pairs(rows,mode):
    counts=Counter();wrong=Counter();sums=Counter();paired=defaultdict(dict)
    for r in rows:
        a=r['policy'];counts[a]+=1;wrong[a]+=r['wrong'];sums[a]+=r['storage_ns']
        key=(r['campaign'],r['shot'])
        ensure(a not in paired[key],f'duplicate paired record {key},{a}')
        paired[key][a]=r
    issues=[]
    for key,p in paired.items():
        # In sample mode, not every policy appears for each (campaign,shot).
        rr=list(p.values())
        state=rr[0]['state'];request=rr[0]['request_ns']
        for r in rr[1:]:
            if r['state']!=state or r['request_ns']!=request:
                issues.append(f'Unpaired state/request at {key}: {r["policy"]}')
        # Distinct policies with exactly the same release timing should have
        # identical outcomes when the simulator's paired inputs are the same.
        seen={}
        for r in rr:
            phase=r['planned_release_ns']
            if phase in seen:
                s=seen[phase]
                if r['wrong']!=s['wrong'] or not near(r['storage_ns'],s['storage_ns']):
                    issues.append(f'Same-release non-determinism at {key}: {s["policy"]}, {r["policy"]}')
            else:seen[phase]=r
    ensure(not issues,'; '.join(issues[:5]))
    if mode=='full':
        ensure(len(paired)==8192,'Wrong campaign/shot pair total')
        for key,p in paired.items():
            ensure(set(p)==set(ARMS),f'Missing policy for paired evaluation {key}')
        ensure(all(counts[a]==8192 for a in ARMS),'Wrong count per policy')
    return {'policy_summaries':{a:{'trials':counts[a],'wrong':wrong[a],
                                'error_percent':100*wrong[a]/counts[a] if counts[a] else None,
                                'mean_storage_ms':sums[a]/counts[a]/1e6 if counts[a] else None}
                                for a in ARMS},
            'paired_request_count':len(paired),'pairing_checks':'PASS'}

def ensure_outside(root,out):
    ensure(out!=root and root not in out.parents,'Output directory must be outside immutable E1 run')
    ensure(out!=root.parent and out!=root.parent.parent,
           'Output path must be a separate audit subdirectory')
    project=root.parent.parent
    if project==out or project in out.parents:
        ensure((project/'audits') in out.parents,
               'Within P1, output must be under P1/audits/<new-audit-folder>')

def atomic_json(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n')
    tmp.replace(path)

def format_md(report):
    lines=[f'# E1 Independent Raw Event Audit — {report["status"]}',
           '',f'- Run: `{report["run_directory"]}`',
           f'- Audit version: `{VERSION}`',
           f'- Mode: `{report["mode"]}`',
           f'- Verified raw batches: **{report["verified_batches"]}**',
           f'- Verified simulator executions: **{report["verified_records"]:,}**',
           f'- Wall time: **{report["audit_wall_s"]:.1f} seconds**',
           '', '## Checks',
           'Original-file SHA-256, protocol and source identity, job hash, checkpoint manifests, raw/compact one-to-one alignment, CPU chronology and nonoverlap, actual admitted and executed CPU service, target admission, entanglement-to-verification storage interval, outcome agreement, and paired frozen-policy consistency.',
           '', '## Interpretation',
           'Raw event consistency is not independent experimental hardware validation or independent reproduction of the underlying quantum noise dynamics.',
           '', '## E1 frozen primary setup',
           'Teleportation, FCFS, four 30-instruction classical jobs (1.2 ms total), source-default simulation assumptions, and supplied coarse epoch uncertainty of +/-0.5 ms.',
           '']
    for stage in ('pilot','evaluation'):
        lines.append(f'- {stage}: {report["stage_counts"].get(stage,0):,} verified records')
    lines+=['','## Evaluation policy summary','','| Policy | Evaluations | Wrong | Error rate | Mean physical storage (ms) |','|---|---:|---:|---:|---:|']
    for a,s in report['evaluation'].get('policy_summaries',{}).items():
        rate=f'{s["error_percent"]:.3f}%' if s['error_percent'] is not None else 'n/a'
        storage=f'{s["mean_storage_ms"]:.4f}' if s['mean_storage_ms'] is not None else 'n/a'
        lines.append(f'| {a} | {s["trials"]:,} | {s["wrong"]:,} | {rate} | {storage} |')
    lines+=['','## Audit scope and limitations',
            '- This independently reconstructs event chronology and service/storage intervals from recorded data; it does not execute NetSquid or prove simulator physics correctness.',
            '- A full-pass receipt requires every original pilot and evaluation raw file; compact exports alone cannot pass.',
            '- In sample mode, only selected batches are inspected, and the outcome is PARTIAL, not full PASS.',
            '- Experiment files and Q12 sources are read only. Reports are written to the separate audit directory.',
            '']
    return '\n'.join(lines)

def run_audit(run_dir,out_dir,workers=2,mode='full'):
    started=time.monotonic()
    root=Path(run_dir).expanduser().resolve()
    out=Path(out_dir).expanduser().resolve()
    ensure(mode in ('sample','full'),'mode must be sample or full')
    ensure(1<=workers<=16,'workers must be between 1 and 16')
    ensure(root.is_dir(),'Missing E1 results directory: '+str(root))
    ensure_outside(root,out)
    protocol,policies=validate_identity(root)
    source_receipt=verify_sources(root,protocol)
    alljobs={}
    resource_receipts={}
    for stage in ('pilot','evaluation'):
        jobs,res=stage_metadata(root,stage)
        for name,_,folder,job,marker,_ in jobs:
            ensure(job['run_protocol_sha256']==stable(protocol),str(folder)+': protocol digest mismatch')
            ensure(job['e1_code_hash']==protocol['source_sha256'],str(folder)+': source identity mismatch')
            ensure(job['q12_code_hashes']==protocol['q12']['q12_source_hashes'],str(folder)+': Q12 source mismatch')
            for task in job['tasks']:
                ensure(job['phases'][str(task['campaign'])]==policies[str(task['campaign'])],
                       str(folder)+': frozen policy differs')
        alljobs[stage]=jobs
        resource_receipts[stage]=res
    selected=[]
    for stage,jobs in alljobs.items():
        if mode=='sample':
            chosen={0,len(jobs)//2,len(jobs)-1}
            selected.extend(jobs[i] for i in sorted(chosen))
        else:selected.extend(jobs)
    # Deliberately fail before opening reports if raw traces are absent.
    missing=[str(folder/marker['raw_file']) for _,_,folder,_,marker,_ in selected
             if not (folder/marker['raw_file']).is_file()]
    if missing:
        raise AuditError(
            f'Missing {len(missing)} raw traces. First missing: {missing[0]}. '
            'Use the original server E1 run directory; the compact review ZIP intentionally omits raw traces.'
        )
    all_results={'pilot':[],'evaluation':[]}
    receipts=[]
    print(f'E1 {mode} raw audit: {len(selected)} batches; read-only; {workers} workers',flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map={pool.submit(audit_batch,j):(j[0],j[1]) for j in selected}
        for k,f in enumerate(as_completed(future_map),1):
            stage,idx=future_map[f]
            try: st,num,records,receipt=f.result()
            except Exception as e:
                raise AuditError(f'Audit failed at {stage}/batch-{idx:05d}: {e}') from e
            all_results[st].extend(records)
            receipts.append(receipt)
            if k==1 or k%40==0 or k==len(selected):
                print(f'  validated {k}/{len(selected)} batches; '+
                      f'{len(all_results["pilot"])+len(all_results["evaluation"]):,} records',flush=True)
    if mode=='full':
        ensure(len(all_results['pilot'])==1152 and len(all_results['evaluation'])==73728,
               'Unexpected total audited execution count')
        controls=[r for r in all_results['pilot'] if r['shot']<8]
        ensure(len(controls)==576 and sum(r['wrong'] for r in controls)==0,
               'Incorrect or missing noise-free pilot controls')
    cross=evaluate_cross_pairs(all_results['evaluation'],mode)
    if mode=='full':
        reported=read_json(root/'analysis.json')
        observed={s['policy']:s for s in reported['summaries']}
        for policy,summary in cross['policy_summaries'].items():
            ensure(summary['wrong']==observed[policy]['wrong_output'] and
                   summary['trials']==observed[policy]['offered'],
                   'Analysis mismatch for '+policy)
            ensure(abs(summary['mean_storage_ms']-observed[policy]['mean_storage_ms'])<1e-8,
                   'Mean-storage analysis mismatch for '+policy)
    report={'version':VERSION,'run_directory':str(root),'mode':mode,
            'status':'PASS_FULL_RAW' if mode=='full' else 'PASS_PARTIAL_SAMPLE',
            'verified_batches':len(receipts),
            'verified_records':sum(map(len,all_results.values())),
            'stage_counts':{k:len(v) for k,v in all_results.items()},
            'audit_wall_s':time.monotonic()-started,
            'input_protocol_sha256':file_sha(root/'protocol.json'),
            'input_analysis_sha256':file_sha(root/'analysis.json'),
            'frozen_policy_file_sha256':file_sha(root/'frozen_policies.json'),
            'source_integrity':source_receipt,'resources':resource_receipts,
            'evaluation':cross,
            'scope':'Independent read-only raw-record audit, not simulator/hardware reproduction.'}
    out.mkdir(parents=True,exist_ok=True)
    atomic_json(out/('E1_RAW_AUDIT_'+mode+'.json'),report)
    atomic_json(out/('E1_BATCH_RECEIPTS_'+mode+'.json'),sorted(receipts,key=lambda r:(r['stage'],r['index'])))
    (out/('E1_RAW_AUDIT_'+mode+'.md')).write_text(format_md(report),encoding='utf-8')
    zipname=out/('E1_Raw_Audit_Review_'+mode+'.zip')
    with zipfile.ZipFile(zipname,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in [out/('E1_RAW_AUDIT_'+mode+'.json'),out/('E1_RAW_AUDIT_'+mode+'.md'),
                  out/('E1_BATCH_RECEIPTS_'+mode+'.json')]:
            z.write(p,arcname=p.name)
    print('AUDIT RESULT:',report['status'],flush=True)
    print('Review package:',zipname,flush=True)
    return report,zipname

def main():
    ap=argparse.ArgumentParser(description='Read-only E1 raw-event auditor; no simulation reruns')
    ap.add_argument('--run-dir',required=True)
    ap.add_argument('--output-dir',required=True)
    ap.add_argument('--workers',type=int,default=2)
    ap.add_argument('--mode',choices=('sample','full'),default='full')
    args=ap.parse_args()
    try:
        run_audit(args.run_dir,args.output_dir,args.workers,args.mode)
    except Exception as e:
        print('AUDIT FAILED:',e,file=sys.stderr)
        traceback.print_exc()
        return 1
    return 0

if __name__=='__main__':
    sys.exit(main())
