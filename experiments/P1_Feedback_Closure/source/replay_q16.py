"""Review this Q16 export without NetSquid. Never changes the supplied run.

Usage: python review_q16.py /path/to/extracted/export --workers 6 --output receipt.json
The export directory must contain EXPORT_MANIFEST.json and the run folder.
Raw records are reconstructed by the run's frozen simulator-independent auditor.
This checks evidence consistency; it is not a fresh simulation or a calibration rerun.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import sys


def replay_one(task):
    root_name, source_name, index, spec, hashes = task
    sys.path.insert(0, source_name)
    from q16_fresh import verified_checkpoint
    from q16_fresh_raw_audit import validate_record
    from q16_io import read_raw
    folder = Path(root_name) / 'planning' / ('stream-%04d' % index)
    saved, stats, marker = verified_checkpoint(folder, dict(spec=spec, source_sha256=hashes))
    rebuilt = validate_record(read_raw(folder / marker['raw_file']), spec)
    if rebuilt != saved:
        raise ValueError('Raw-to-compact mismatch at stream %d' % index)
    return rebuilt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export', type=Path)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = args.export.resolve()
    manifest = json.loads((base / 'EXPORT_MANIFEST.json').read_text())
    roots = list(base.glob('q16-fresh-planning-*'))
    if len(roots) != 1:
        raise ValueError('Expected exactly one Q16 planning run')
    root = roots[0]
    source = root / 'source'
    sys.path.insert(0, str(source))
    import q16_fresh_design as d
    from q16_fresh_analysis import summarize, text_report
    from q16_io import read, sha, source_hashes, normalize
    declared = set()
    for entry in manifest['files']:
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe export path')
        if entry['path'] in declared:
            raise ValueError('Duplicate export path')
        declared.add(entry['path'])
        path = root / relative
        if path.stat().st_size != entry['bytes'] or sha(path) != entry['sha256']:
            raise ValueError('Export hash mismatch: ' + entry['path'])
    actual = {str(p.relative_to(root)) for p in root.rglob('*')
              if p.is_file() and '__pycache__' not in p.parts}
    if actual != declared:
        raise ValueError('Export inventory mismatch')
    hashes = source_hashes(source)
    protocol = read(root / 'protocol.json')
    complete = read(root / 'COMPLETE.json')
    if protocol != d.protocol(hashes) or complete['source_sha256'] != hashes:
        raise ValueError('Frozen protocol/source mismatch')
    binding = read(root / 'CALIBRATION_BINDING_VERIFIED.json')
    if binding['file_sha256'] != d.binding()['file_sha256']:
        raise ValueError('Calibration binding metadata differs')
    specs = list(d.specifications())
    tasks = [(str(root), str(source), index, spec, hashes) for index, spec in enumerate(specs)]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        records = []
        for record in pool.map(replay_one, tasks):
            records.append(record)
            if len(records) % 16 == 0:
                print('Replayed %d/%d raw streams' % (len(records), len(specs)), flush=True)
    summary = normalize(summarize(records))
    if summary != read(root / 'planning_summary.json'):
        raise ValueError('Summary reconstruction mismatch')
    if text_report(summary) != (root / 'PLANNING_REPORT.txt').read_text():
        raise ValueError('Text report reconstruction mismatch')
    if complete['status'] != summary['status']:
        raise ValueError('Completion status mismatch')
    for field, filename in [('protocol', 'protocol.json'), ('summary', 'planning_summary.json'), ('report', 'PLANNING_REPORT.txt')]:
        if complete[field + '_sha256'] != sha(root / filename):
            raise ValueError('Completion hash mismatch: ' + filename)
    checkpoints = [dict(folder='stream-%04d' % i, **read(root / 'planning' / ('stream-%04d' % i) / 'completed.json')) for i in range(len(specs))]
    if checkpoints != read(root / 'planning' / 'manifest.json'):
        raise ValueError('Checkpoint manifest mismatch')
    receipt = dict(status='PASS', run=root.name, inventory_files_verified=len(declared),
        source_files_verified=len(hashes), raw_streams_replayed=len(records),
        measured_requests=summary['measured_requests'], all_requests=summary['all_offered_requests'],
        checkpoint_manifest_verified=True, raw_to_compact_exact=True, summary_exact=True,
        report_exact=True, completion_hashes_verified=True,
        calibration_binding_metadata_verified=True,
        calibration_original_files_reaudited=False, fresh_native_execution_performed=False,
        protocol_sha256=sha(root / 'protocol.json'), source_sha256=hashes)
    args.output.write_text(json.dumps(receipt, indent=2) + '\n')
    print('PASS: all raw records and summary reconstructed exactly', flush=True)


if __name__ == '__main__':
    main()
