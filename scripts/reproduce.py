"""Portable entry point for retained-evidence reproduction; never launches simulations."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def receipts(full_records=False):
    if not full_records:
        report = json.loads((ROOT / 'docs/full-REPORT.json').read_text())
        assert report['status'] == 'PASS_FULL_RAW_TO_COMPACT_REPLAY'
        assert report['total_full_jobs'] == 11008
        assert sum(x['records'] for x in report['totals'].values()) == 1487360
        print(json.dumps({'status': 'PASS_RETAINED_REPLAY_REPORT',
                          'scope': 'Checks the completed report; use --full-records to inspect every checkpoint receipt.'}))
        return
    archive = ROOT / 'data/archives/P1_Full_Raw_Replay_LATEST_review.zip'
    expected = {'timing': (2048, 655360), 'application': (8192, 655360),
                'causal': (512, 61440), 'robustness': (256, 115200)}
    with zipfile.ZipFile(archive) as z:
        report = json.loads(z.read('full-REPORT.json'))
        assert report['status'] == 'PASS_FULL_RAW_TO_COMPACT_REPLAY'
        counts = {k: [0, 0] for k in expected}
        for name in z.namelist():
            if not name.startswith('checkpoints/') or not name.endswith('.json'):
                continue
            r = json.loads(z.read(name))
            assert r['status'] == 'PASS_RAW_TO_COMPACT', name
            assert r['binding']['auditor_identity'] == report['auditor_identity'], name
            family = r['binding']['job']['study']
            counts[family][0] += 1
            counts[family][1] += r['records']
        assert {k: tuple(v) for k, v in counts.items()} == expected
        assert report['total_full_jobs'] == report['expected_jobs'] == 11008
        for family, (jobs, records) in expected.items():
            assert report['totals'][family]['jobs'] == jobs
            assert report['totals'][family]['records'] == records
    print(json.dumps({'status': 'PASS_RECEIPT_INVENTORY', 'counts': counts,
                      'scope': 'Checks retained replay receipts; does not rerun raw replay.'}))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('task', choices=['figures', 'timing', 'application', 'diagnostics', 'receipts', 'historical', 'engineering', 'q12', 'q14', 'e1', 'application_earlier', 'q16', 'all'])
    p.add_argument('--full-records', action='store_true', help='Use the optional original compact archives and full checkpoint checks')
    a = p.parse_args()
    out = ROOT / 'outputs/validation'
    out.mkdir(parents=True, exist_ok=True)
    if a.task == 'receipts':
        receipts(a.full_records)
        return
    tasks = (['engineering', 'timing', *([] if a.full_records else ['application']), 'diagnostics', 'historical', 'receipts', 'figures'] if a.task == 'all' else [a.task])
    for task in tasks:
        if not a.full_records and task in ('timing','application','diagnostics','historical','q12','q14','e1','application_earlier','q16'):
            command = [sys.executable, 'scripts/reproduce_analysis.py', '--family', task]
        elif task in ('timing', 'application'):
            command = [sys.executable, 'scripts/verify_main.py']
        elif task == 'diagnostics':
            command = [sys.executable, 'scripts/verify_diagnostics.py']
        elif task == 'historical':
            command = [sys.executable, 'scripts/verify_historical.py']
        elif task in ('q12','q14','e1','application_earlier','q16'):
            command = [sys.executable, 'scripts/verify_historical.py', '--family', task]
        elif task == 'receipts':
            command = [sys.executable, 'scripts/reproduce.py', 'receipts']
            if a.full_records: command.append('--full-records')
        elif task == 'engineering':
            command = [sys.executable, 'scripts/check_engineering.py']
        else:
            command = [sys.executable, 'analysis/build_figures.py']
        start = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(result.stdout, end='')
        (out / (task + '.log')).write_text(result.stdout)
        (out / (task + '.json')).write_text(json.dumps({'task': task, 'command': command,
            'status': 'PASS' if result.returncode == 0 else 'FAIL',
            'elapsed_seconds': time.monotonic() - start, 'returncode': result.returncode}, indent=2) + '\n')
        if result.returncode:
            raise SystemExit(result.returncode)

if __name__ == '__main__':
    main()
