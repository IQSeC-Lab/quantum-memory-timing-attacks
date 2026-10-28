"""Resume E1 v1 without rewriting completed stage timing metadata.

This launcher imports the original, hash-pinned p1_e1.py unchanged. On a
completed stage it validates all original batch checkpoint hashes, manifest,
and stable resource totals, then returns the original resources.json bytes.
An incomplete stage still uses the original checkpointed runner. No source
or results are rewritten to fix this bug.
"""
from __future__ import annotations
import argparse
import math
import signal
from pathlib import Path
import p1_e1 as e1

EXPECTED_E1_SHA256 = 'e75bf81ee0298f39531cf40c4549af078b24b97c2f88207fd031516f3f444a74'
_ORIGINAL_EXECUTE_STAGE = e1.execute_stage


def validated_completed_stage(stage, root, source, protocol, policies):
    root = Path(root)
    manifest_path = root / stage / 'manifest.json'
    resources_path = root / stage / 'resources.json'
    # No previously finished stage; let the original runner do checkpointed work.
    if not manifest_path.exists() and not resources_path.exists():
        return None
    # A complete manifest can precede writing resources after a crash. The
    # original runner can regenerate resources if and only if they do not exist.
    if manifest_path.exists() and not resources_path.exists():
        return None
    if not manifest_path.exists() or not resources_path.exists():
        raise RuntimeError('Corrupt completed stage (missing manifest): ' + str(root/stage))
    if e1.filehash(e1.HERE/'p1_e1.py') != EXPECTED_E1_SHA256:
        raise RuntimeError('Original E1 source hash changed; refusing to resume')
    if protocol['source_sha256'] != EXPECTED_E1_SHA256:
        raise RuntimeError('Run protocol does not match the pinned original E1 source')
    if protocol['policies_sha256'] != e1.digest(policies):
        raise RuntimeError('Policy hash mismatch')

    tasks_per_batch = e1.task_chunks(stage)
    manifest = e1.read(manifest_path)
    if len(tasks_per_batch) != len(manifest):
        raise RuntimeError('Completed stage manifest batch count mismatch: '+stage)
    original_protocol_sha = e1.digest(protocol)
    expected_manifest = []
    for i, tasks in enumerate(tasks_per_batch):
        folder = root/stage/('batch-%05d'%i)
        ckeys = sorted({str(t['campaign']) for t in tasks}, key=int)
        expected_job = dict(
            stage=stage, tasks=tasks,
            phases={key:policies[key] for key in ckeys},
            q12_source_dir=str(source),
            q12_code_hashes=protocol['q12']['q12_source_hashes'],
            e1_code_hash=protocol['source_sha256'],
            run_protocol_sha256=original_protocol_sha,
        )
        if e1.read(folder/'job.json') != expected_job:
            raise RuntimeError('Batch job differs from frozen protocol: '+str(folder))
        # Includes hashes of the original raw traces, compact records and
        # worker stats. Checks that records match the job and trial count.
        rows, stats, checkpoint = e1.checkpoint(folder, expected_job)
        if len(rows) != len(tasks):
            raise RuntimeError('Batch record count mismatch: '+str(folder))
        expected_manifest.append(dict(index=i, **checkpoint, stats=stats))
        del rows
    if manifest != expected_manifest:
        raise RuntimeError('Completed stage manifest differs from checkpoints: '+stage)
    resources = e1.read(resources_path)
    expected_fixed = dict(
        trials=sum(map(len,tasks_per_batch)),
        batches=len(tasks_per_batch),
        worker_cpu_s=sum(m['stats']['cpu_seconds'] for m in manifest),
        worker_wall_s=sum(m['stats']['wall_seconds'] for m in manifest),
        max_worker_ram_mib=max(m['stats']['peak_rss_mib'] for m in manifest),
        stage_manifest_sha256=e1.filehash(manifest_path),
    )
    if set(resources) != set(expected_fixed) | {'real_wall_s'}:
        raise RuntimeError('Unexpected resource-summary fields: '+str(resources_path))
    for key, expected_value in expected_fixed.items():
        if resources[key] != expected_value:
            raise RuntimeError('Resource summary differs from checkpoints ('+key+'): '+stage)
    observed_wall = resources['real_wall_s']
    if (not isinstance(observed_wall,(int,float)) or isinstance(observed_wall,bool)
            or not math.isfinite(observed_wall) or observed_wall < 0):
        raise RuntimeError('Invalid recorded stage runtime: '+stage)
    print(f'{stage}: reusing verified completed stage: {resources["trials"]:,} trials; '
          f'{len(manifest)} hashed checkpoints; preserved original wall time '
          f'{observed_wall:.1f} s',flush=True)
    return resources


def safe_execute_stage(stage, root, repo, source, protocol, policies, workers):
    recovered = validated_completed_stage(stage,root,source,protocol,policies)
    if recovered is not None:
        return recovered
    return _ORIGINAL_EXECUTE_STAGE(stage,root,repo,source,protocol,policies,workers)


def main():
    p=argparse.ArgumentParser(description='Hash-safe E1 v1 resume without resource rewrites')
    p.add_argument('--project',type=Path,required=True)
    p.add_argument('--q12-run',type=Path,required=True)
    p.add_argument('--q12-code',type=Path)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--mode',choices=['pilot','full'],default='full')
    p.add_argument('--workers',type=int,default=8)
    args=p.parse_args()
    if args.workers < 1: p.error('--workers must be positive')
    if e1.filehash(e1.HERE/'p1_e1.py') != EXPECTED_E1_SHA256:
        p.error('Original E1 code changed; refuses to rewrite pinned experiment')
    def interrupted(signum,frame):
        e1.STOP.set()
        raise KeyboardInterrupt('Resume interrupted; committed batches remain reusable')
    signal.signal(signal.SIGINT,interrupted)
    signal.signal(signal.SIGTERM,interrupted)
    e1.execute_stage = safe_execute_stage
    e1.run(args)


if __name__=='__main__': main()
