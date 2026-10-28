"""Q12 raw-record audit coordinator. Reads the completed run; writes a separate audit directory."""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
import resource
import sys
import time
import traceback
import zipfile

import numpy as np
import q12_design as d
from q12_raw_checks import VERSION, AuditError, need, sha_file, read_json, member, blank_metrics, merge_metrics, validate_record

HERE = Path(__file__).resolve().parent
CODE_NAMES = ('q12_raw_audit.py','q12_raw_checks.py','q12_raw_tests.py','q12_design.py','q12_expected.json')


def code_hashes():
    return {n: sha_file(HERE/n) for n in CODE_NAMES}


def write_json(path, value, immutable=False):
    """Only called for new audit output, never the original experiment directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        need(read_json(path) == value, 'Existing audit metadata differs: '+str(path)+'. Use a new AUDIT_DIR; do not edit Q12.')
        return
    temp = path.with_name(path.name+'.tmp-'+str(os.getpid()))
    with temp.open('w') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)


def validate_locations(run_dir, out):
    run_dir, out = Path(run_dir).resolve(), Path(out).resolve()
    need(out != run_dir and run_dir not in out.parents and out not in run_dir.parents,
         'AUDIT_DIR must be separate from, and not a parent of, the original Q12 run.')
    need(out != HERE and HERE not in out.parents and out not in HERE.parents,
         'AUDIT_DIR must be separate from the audit code folder.')
    need(not out.is_symlink(), 'Audit output cannot be a symlink')
    return run_dir, out


def snapshot(paths):
    return {str(p): (p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ino) for p in paths}


def fixed_sample(spec):
    # Frozen, outcome-independent selection of 12 primary evaluation records.
    return (spec['stage'] == 'evaluation' and spec['scenario'] == 'primary_0500us'
            and (spec['campaign'],spec['shot']) in ((0,0),(31,255)))


def verify_files(folder, marker, job):
    need(marker['job_hash'] == d.stable(job), 'Checkpoint job hash mismatch')
    paths = {kind: member(folder, marker[kind+'_file']) for kind in ('raw','compact','statistics')}
    for kind, path in paths.items():
        need(sha_file(path) == marker[kind+'_sha256'], f'{kind} checksum mismatch: {path}')
    stat = read_json(paths['statistics'])
    need(stat['trials'] == len(job['specs']) and stat['validation'] == 'Q12 full raw-record validation passed', 'Original worker gate missing')
    return paths


def audit_batch(task):
    folder, out, entry, expected_job, code_id = task
    folder, out = Path(folder), Path(out)
    stage, batch = expected_job['stage'], entry['batch']
    receipt_path = out/'checkpoints'/stage/(batch+'.json')
    sample_path = out/'samples'/stage/(batch+'.jsonl.gz')
    row_number, spec, raw_record, compact_record = None, None, None, None
    started = time.monotonic()
    try:
        job = read_json(member(folder, 'job.json'))
        marker = read_json(member(folder, 'completed.json'))
        need(job == expected_job, 'Job differs from frozen prespecified grid')
        need(marker == {k:v for k,v in entry.items() if k not in ('batch','stats')}, 'Manifest/checkpoint mismatch')
        observed = snapshot([member(folder,marker[k+'_file']) for k in ('raw','compact','statistics')])
        paths = verify_files(folder, marker, job)
        need(read_json(paths['statistics']) == entry['stats'], 'Manifest/worker-statistics mismatch')
        identity = dict(audit_code_sha256=code_id, stage=stage, batch=batch, marker=marker)
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            need(receipt.get('receipt_sha256') == d.stable({k:v for k,v in receipt.items() if k != 'receipt_sha256'}), 'Audit checkpoint checksum mismatch')
            need(receipt['identity'] == identity and receipt['status'] == 'PASS', 'Audit checkpoint identity mismatch')
            need(receipt['metrics']['trials'] == len(job['specs']), 'Audit checkpoint count mismatch')
            if receipt['sample_count']:
                need(sample_path.is_file() and sha_file(sample_path) == receipt['sample_sha256'], 'Audit sample checksum mismatch')
            need(snapshot(paths.values()) == observed, 'Original files changed during resume verification')
            return dict(receipt=receipt, reused=True, seconds=time.monotonic()-started)
        metrics, samples = blank_metrics(), []
        with gzip.open(paths['raw'],'rt') as raw, gzip.open(paths['compact'],'rt') as compact:
            for row_number, spec in enumerate(job['specs']):
                raw_record, compact_record = None, None
                raw_line, compact_line = raw.readline(), compact.readline()
                need(raw_line.strip() and compact_line.strip(), 'Missing/truncated raw or compact record')
                r, c = json.loads(raw_line), json.loads(compact_line)
                raw_record, compact_record = r, c
                m = validate_record(r,c,spec,job['frozen'])
                merge_metrics(metrics,m)
                if fixed_sample(spec):
                    samples.append(dict(stage=stage,batch=batch,line_index_zero_based=row_number,
                        source_raw_sha256=marker['raw_sha256'],source_compact_sha256=marker['compact_sha256'],raw=r,compact=c))
            need(not raw.read().strip() and not compact.read().strip(), 'Unexpected extra raw or compact records')
        need(snapshot(paths.values()) == observed, 'Original files changed during audit')
        sample_hash = None
        if samples:
            sample_path.parent.mkdir(parents=True,exist_ok=True)
            temp = sample_path.with_name(sample_path.name+'.tmp-'+str(os.getpid()))
            with gzip.open(temp,'wt',compresslevel=3) as f:
                for s in samples:
                    f.write(json.dumps(s,separators=(',',':'),allow_nan=False)+'\n')
            os.replace(temp,sample_path)
            sample_hash = sha_file(sample_path)
        receipt = dict(identity=identity,status='PASS',metrics=metrics,sample_count=len(samples),sample_sha256=sample_hash)
        receipt['receipt_sha256'] = d.stable(receipt)
        write_json(receipt_path,receipt,immutable=True)
        return dict(receipt=receipt,reused=False,seconds=time.monotonic()-started)
    except BaseException as exc:
        failure = dict(stage=stage,batch=batch,line_index_zero_based=row_number,spec=spec,
            error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc(),
            raw_record=raw_record,compact_record=compact_record,
            original_files_unchanged_by_auditor=True)
        write_json(out/'failures'/stage/(batch+'.json'),failure)
        raise AuditError(f'{stage}/{batch}, raw line {row_number}: {exc}') from exc


def preflight(run_dir, out):
    expected = read_json(HERE/'q12_expected.json')
    need(run_dir.is_dir(), 'Q12 directory not found: '+str(run_dir))
    for name, sha in expected['header_sha256'].items():
        path = run_dir/name
        need(path.is_file() and not path.is_symlink(), 'Missing reviewed metadata: '+str(path))
        need(sha_file(path) == sha, 'This run differs from the reviewed Q12 metadata: '+name)
    for name, sha in expected['source_hashes'].items():
        need(sha_file(member(run_dir/'source',name)) == sha, 'Original source identity mismatch: '+name)
    need(sha_file(HERE/'q12_design.py') == expected['source_hashes']['q12_design.py'], 'Embedded design changed')
    need(read_json(run_dir/'protocol.json') == d.protocol(expected['source_hashes']), 'Original protocol changed')
    frozen = read_json(run_dir/'frozen_selections.json')
    tasks = []
    code_id = d.stable(code_hashes())
    worker_env = read_json(run_dir/'environment.json')
    worker_env.pop('scipy',None)
    for stage in ('pilot','calibration','evaluation'):
        entries = read_json(run_dir/stage/'manifest.json')
        chunks = list(d.batches(stage))
        need(len(entries) == len(chunks), 'Manifest grid size mismatch')
        stage_frozen = frozen if stage == 'evaluation' else {}
        for index, (entry, chunk) in enumerate(zip(entries,chunks)):
            need(entry['batch'] == 'batch-%05d'%index, 'Manifest batch order mismatch')
            need(entry['stats']['environment'] == worker_env, 'Manifest worker environment mismatch')
            keys = {s['workload']+'|'+str(s['campaign']) for s in chunk if stage == 'evaluation' and s['arm'] == 'learned'}
            job = dict(stage=stage,specs=chunk,code_hashes=expected['source_hashes'],
                       frozen={k:frozen[k] for k in sorted(keys)},all_selections_sha256=d.stable(stage_frozen))
            tasks.append((str(run_dir/stage/entry['batch']),str(out),entry,job,code_id))
    need(len(tasks) == expected['committed_batches'], 'Unexpected total batch count')
    identity = dict(version=VERSION,run_dir=str(run_dir),review_archive_sha256=expected['review_archive_sha256'],
        expected_header_sha256=expected['header_sha256'],expected_source_hashes=expected['source_hashes'],
        audit_code_hashes=code_hashes(),audit_code_sha256=code_id,
        numpy_version=np.__version__,counts=expected['counts'],
        note='Original run files opened read-only. Audit outputs and checkpoints are separate.')
    write_json(out/'audit_identity.json',identity,immutable=True)
    source_dir=out/'audit_source';source_dir.mkdir(exist_ok=True)
    for n in CODE_NAMES:
        destination=source_dir/n
        if destination.exists():
            need(destination.read_bytes()==(HERE/n).read_bytes(),'Saved audit source differs')
        else:
            destination.write_bytes((HERE/n).read_bytes())
    return expected,tasks,identity


def make_report(out, report):
    passed = report['status'] == 'PASS'
    rows=''.join('<tr><td>'+html.escape(stage)+'</td><td>'+f"{m['trials']:,}"+'</td><td>'+f"{m['completed']:,}"+'</td><td>'+f"{m['wrong_outputs']:,}"+'</td></tr>' for stage,m in report.get('stage_metrics',{}).items())
    details=html.escape(json.dumps(report,indent=2))
    text='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Q12 raw trace audit</title><style>body{font:16px/1.6 system-ui,sans-serif;color:#173041;max-width:1000px;margin:40px auto;padding:0 24px}h1{line-height:1.2}table{border-collapse:collapse;width:100%}td,th{padding:10px;text-align:left;border-bottom:1px solid #ddd}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f7;padding:20px;font-size:12px}.note{padding:16px;background:#edf5f4}code{overflow-wrap:anywhere}</style></head><body>'''
    text+='<h1>Q12 raw trace audit — '+report['status']+'</h1>'
    text+='<p>Original run: <code>'+html.escape(report['run_dir'])+'</code></p>'
    text+='<p class="note">'+('All committed raw files matched their recorded hashes, and all recorded trials passed the independent event and compact-reconstruction checks.' if passed else 'The audit is incomplete or a check failed. See the diagnostics below; preserve the original Q12 run.')+'</p>'
    text+='<table><tr><th>Stage</th><th>Audited trials</th><th>Completed outputs</th><th>Wrong outputs</th></tr>'+rows+'</table>'
    text+='<h2>What was checked</h2><p>Frozen source and metadata identity; committed raw/compact/statistics hashes; complete job specifications; seeded inputs; CPU chronology with boundary events; admission causality; measured per-job CPU work; equality of competing service budgets; instruction containment in recorded QPU tasks; EPR/prepare-to-verification storage intervals; recorded teleportation Pauli branches; and every compact field reconstructed from the raw record. Only committed batches were used. Twelve fixed primary records are exported for review, selected by workload, arm, campaign and shot before inspecting outcomes.</p>'
    text+='<h2>Scope</h2><p>This is recorded-data validation. It runs no simulator and does not reconstruct the full density matrix. Q12 did not retain peer message values or a separate channel-message trace, so receipt correctness against the peer output remains a runtime assertion in the original experiment. This audit checks the recorded Bell bits against recorded correction instructions. It does not independently prove physical hardware behavior, operating-system isolation, or attacker access to the coarse epoch. It does not rerun or change the statistical analysis; it links verified raw records to the exact compact outcomes and analysis already independently reviewed.</p>'
    text+='<details><summary>Machine-readable report</summary><pre>'+details+'</pre></details></body></html>'
    (out/'Q12_Raw_Audit_Report.html').write_text(text)


def package(out):
    """Explicit audit-only allowlist: never traverses or copies the original run."""
    files=[]
    for n in ('audit_identity.json','audit_report.json','Q12_Raw_Audit_Report.html','AUDIT_COMPLETE.json','AUDIT_FAILED.json','audit_receipts.jsonl.gz','fixed_raw_samples.jsonl.gz','README.txt'):
        if (out/n).is_file():files.append(out/n)
    for dirname in ('audit_source','failures'):
        base=out/dirname
        if base.is_dir():files.extend(p for p in base.rglob('*') if p.is_file() and not p.is_symlink())
    files.extend(p for p in out.glob('launcher-*.log') if p.is_file())
    target=out/'Q12_Raw_Audit_Review.zip'
    temp=target.with_name(target.name+'.tmp')
    print(f'Exporting {len(files)} audit files; original simulation traces stay in place.',flush=True)
    with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for p in sorted(set(files)):
            z.write(p,arcname=str(Path(out.name)/p.relative_to(out)))
    os.replace(temp,target)
    print('AUDIT ZIP:',target,flush=True)
    return target


def run(args):
    run_dir,out=validate_locations(args.run_dir,args.audit_dir)
    out.mkdir(parents=True,exist_ok=True)
    with ExitStack() as stack:
        lock=stack.enter_context((out/'AUDIT_RUNNING.lock').open('a+'))
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise AuditError('This audit already has an active coordinator. Wait for it or stop that audit first.')
        if (run_dir/'RUNNING.lock').is_file():
            # Read-only shared lock blocks if the original experiment coordinator is active.
            original_lock=stack.enter_context((run_dir/'RUNNING.lock').open('r'))
            try:fcntl.flock(original_lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError:raise AuditError('The original Q12 coordinator is active. Wait for it to stop; the audit has not altered Q12.')
        print('Q12 RUN DIRECTORY:',run_dir,flush=True)
        print('AUDIT DIRECTORY:',out,flush=True)
        print('Read-only original data; no NetSquid imports or simulation calls.',flush=True)
        expected,tasks,identity=preflight(run_dir,out)
        print(f'Preflight passed: {len(tasks):,} committed batches; {sum(expected["counts"].values()):,} raw records; workers={args.workers}.',flush=True)
        (out/'README.txt').write_text('Q12 raw trace audit review export.\nOriginal experiment files were read, not modified.\nRead Q12_Raw_Audit_Report.html and audit_report.json. PASS concerns record integrity and event invariants, not a positive attack effect or hardware reproduction.\naudit_receipts.jsonl.gz identifies every checked committed batch and its raw/compact hashes. fixed_raw_samples.jsonl.gz contains 12 records selected by fixed labels (primary; campaigns 0 and 31; shots 0 and 255; both workloads; all three arms), without selecting on outcomes.\nNo full quantum-state replay or independent peer-message trace is possible from the retained schema.\n')
        started=time.monotonic();done={};pending={};next_index=0;reused=0;last_print=started
        totals={s:blank_metrics() for s in ('pilot','calibration','evaluation')}
        failure=None
        pool=ProcessPoolExecutor(max_workers=args.workers)
        try:
            while next_index<len(tasks) or pending:
                while next_index<len(tasks) and len(pending)<2*args.workers:
                    f=pool.submit(audit_batch,tasks[next_index]);pending[f]=next_index;next_index+=1
                completed,_=wait(pending,timeout=10,return_when=FIRST_COMPLETED)
                for f in completed:
                    index=pending.pop(f)
                    result=f.result();receipt=result['receipt'];done[index]=receipt
                    reused+=int(result['reused'])
                    merge_metrics(totals[receipt['identity']['stage']],receipt['metrics'])
                now=time.monotonic()
                if len(done)==len(tasks) or (completed and (len(done)<=args.workers or len(done)%32==0)) or now-last_print>=15:
                    records=sum(v['trials'] for v in totals.values())
                    elapsed=now-started
                    eta=elapsed*(len(tasks)-len(done))/len(done) if done else None
                    eta_text=f'{eta/60:.1f} min' if eta is not None else 'estimating'
                    print(f'Raw audit: {len(done):,}/{len(tasks):,} batches | {records:,} records | {reused:,} reused | elapsed {elapsed/60:.1f} min | rough remaining {eta_text}',flush=True)
                    last_print=now
        except BaseException as exc:
            failure=exc
            for f in pending:f.cancel()
        finally:
            pool.shutdown(wait=True,cancel_futures=True)
        need(failure is None, 'Raw audit stopped: '+str(failure))
        # Recheck all reviewed headers after the scan. Checkpoints remain audit-only.
        for name,h in expected['header_sha256'].items():
            need(sha_file(run_dir/name)==h,'Original metadata changed during audit: '+name)
        for name,h in expected['source_hashes'].items():
            need(sha_file(member(run_dir/'source',name))==h,'Original source changed during audit: '+name)
        for stage,count in expected['counts'].items():
            need(totals[stage]['trials']==count,'Incomplete audited stage: '+stage)
        receipts_path=out/'audit_receipts.jsonl.gz'
        samples_path=out/'fixed_raw_samples.jsonl.gz'
        sample_count=0
        with gzip.open(receipts_path,'wt',compresslevel=3) as receipts,gzip.open(samples_path,'wt',compresslevel=3) as samples:
            for index in sorted(done):
                receipt=done[index];receipts.write(json.dumps(receipt,separators=(',',':'),allow_nan=False)+'\n')
                if receipt['sample_count']:
                    p=out/'samples'/receipt['identity']['stage']/(receipt['identity']['batch']+'.jsonl.gz')
                    need(sha_file(p)==receipt['sample_sha256'],'Saved sample changed')
                    with gzip.open(p,'rt') as source:
                        for line in source:
                            samples.write(line);sample_count+=1
        need(sample_count==12,'Fixed evidence sample count mismatch')
        report=dict(version=VERSION,status='PASS',run_dir=str(run_dir),audit_dir=str(out),
            completed_utc=datetime.now(timezone.utc).isoformat(),audit_code_sha256=identity['audit_code_sha256'],
            reviewed_archive_sha256=expected['review_archive_sha256'],committed_batches=len(tasks),
            audited_trials=sum(v['trials'] for v in totals.values()),stage_metrics=totals,
            reused_audit_checkpoints=reused,wall_seconds=time.monotonic()-started,
            python=sys.version,numpy=np.__version__,audit_receipts_sha256=sha_file(receipts_path),
            fixed_sample_count=sample_count,fixed_raw_samples_sha256=sha_file(samples_path),
            checks=dict(original_review_identity=True,all_committed_file_hashes=True,exact_spec_grid=True,
                seeded_inputs_and_frozen_plans=True,cpu_nonoverlap=True,actual_admission_causality=True,
                measured_equal_service=True,quantum_event_containment=True,physical_storage_reconstruction=True,
                recorded_bell_bits_match_pauli_branches=True,all_compact_fields_reconstructed=True),
            not_performed=['No NetSquid runtime execution','No full density-matrix replay','No independent peer message-value audit; those values were not retained','No new statistical analysis or change of Q12 selections'],
            reviewed_primary_contrasts=expected['primary_contrasts'])
        write_json(out/'audit_report.json',report)
        write_json(out/'AUDIT_COMPLETE.json',dict(status='PASS',report_sha256=sha_file(out/'audit_report.json'),audited_trials=report['audited_trials']))
        (out/'AUDIT_FAILED.json').unlink(missing_ok=True)
        make_report(out,report)
        print(f'RAW AUDIT PASSED: {report["audited_trials"]:,} records in {len(tasks):,} committed batches.',flush=True)
        print('Original Q12 results unchanged. Statistical conclusions remain as previously reviewed.',flush=True)
        package(out)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--audit-dir',type=Path,required=True)
    p.add_argument('--workers',type=int,default=2)
    p.add_argument('--export-only',action='store_true')
    args=p.parse_args()
    if args.workers<1:p.error('--workers must be positive')
    # Reject invalid output locations before creating any files, including failure output.
    run_dir,out=validate_locations(args.run_dir,args.audit_dir)
    if args.export_only:
        need(out.is_dir(),'No audit directory to export')
        package(out);return
    try:
        run(args)
    except BaseException as exc:
        # Avoid writes into an existing active audit when its lock rejected us.
        out.mkdir(parents=True,exist_ok=True)
        try:
            with (out/'AUDIT_RUNNING.lock').open('a+') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                failure=dict(version=VERSION,status='FAILED',run_dir=str(run_dir),audit_dir=str(out),
                    error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc(),
                    note='Original experiment files were not modified. Run the notebook export cell and share the small audit ZIP.')
                write_json(out/'AUDIT_FAILED.json',failure)
                (out/'AUDIT_COMPLETE.json').unlink(missing_ok=True)
                write_json(out/'audit_report.json',failure);make_report(out,failure)
                package(out)
        except BlockingIOError:
            pass
        traceback.print_exc()
        sys.exit(1)


if __name__=='__main__':
    main()
