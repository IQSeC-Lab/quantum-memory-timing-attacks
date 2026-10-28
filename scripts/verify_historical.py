"""Recompute campaign, transfer and public-clock summaries from retained compact records."""
import argparse
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/archives'

def compare(a, b, path='result'):
    if isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), path
        for k in a: compare(a[k], b[k], path + '/' + k)
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)): compare(x, y, path + '/' + str(i))
    elif isinstance(a, float):
        assert isinstance(b, (int, float)) and abs(a-b) <= 1e-12, (path, a, b)
    else:
        assert a == b, (path, a, b)

def run(family):
    sys.path.insert(0, str(ROOT / 'experiments/historical' / family / 'source'))
    pattern = ('*Q12*zip' if family == 'q12' else '*Q14*zip' if family == 'q14'
               else 'P1_RQ13*zip' if family == 'application_earlier' else 'q16-fresh-planning-compact.zip')
    archive = next(DATA.glob(pattern))
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        prefix = next(n.rsplit('/', 1)[0] + '/' for n in names if n.count('/') == 1 and n.endswith('/COMPLETE.json'))
        read = lambda name: json.loads(z.read(prefix + name))
        rows = []
        if family in ('q12', 'q14'):
            for name in sorted(n for n in names if '/evaluation/' in n and n.endswith('/completed.json')):
                marker = json.loads(z.read(name))
                payload = z.read(name.rsplit('/', 1)[0] + '/' + marker['compact_file'])
                assert hashlib.sha256(payload).hexdigest() == marker['compact_sha256'], name
                rows.extend(json.loads(line) for line in gzip.decompress(payload).splitlines() if line.strip())
            if family == 'q12':
                module = importlib.import_module('q12')
                result = module.analyze(rows, read('frozen_selections.json'), read('calibration_costs.json'))
            else:
                module = importlib.import_module('q14_analysis')
                result = module.analyze(rows, read('frozen_policies.json'), read('inherited_calibration_costs.json'))
            compare(result, read('analysis.json'))
        elif family == 'application_earlier':
            for name in sorted(n for n in names if '/evaluation/' in n and n.endswith('.compact.json')):
                rows.append(json.loads(z.read(name)))
            d = importlib.import_module('app_design')
            phase = read('FROZEN_SELECTION.json')['selected_phase_ns']
            lookup = {d.stable(r['spec']): r for r in rows}
            assert len(lookup) == len(rows) == 7680
            rows = [lookup[d.stable(s)] for s in d.evaluation_specs('full', phase)]
            result = importlib.import_module('app_analysis').summarize(rows, 'full', phase)
            compare(result, read('application_summary.json'))
        else:
            for name in sorted(n for n in names if '/planning/' in n and n.endswith('.compact.json')):
                rows.append(json.loads(z.read(name)))
            result = importlib.import_module('q16_fresh_analysis').summarize(rows)
            # Summaries are compared against the archived planning summary.
            candidates = [n for n in names if n.count('/') == 1 and n.endswith('.json') and 'summary' in n.lower()]
            if not candidates:
                candidates = [n for n in names if n.count('/') == 1 and n.endswith('/planning_summary.json')]
            assert len(candidates) == 1, candidates
            compare(result, json.loads(z.read(candidates[0])))
    output = ROOT / 'outputs/historical'
    output.mkdir(parents=True, exist_ok=True)
    (output / (family + '.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'family': family, 'status': 'PASS_COMPACT_RECOMPUTATION', 'records': len(rows), 'numeric_tolerance': 1e-12}))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--family', choices=['all', 'q12', 'q14', 'q16', 'e1', 'application_earlier'], default='all')
    a = p.parse_args()
    if a.family == 'all':
        for family in ['q12', 'q14', 'q16', 'e1', 'application_earlier']:
            subprocess.run([sys.executable, str(Path(__file__).resolve()), '--family', family], cwd=ROOT, check=True)
    elif a.family == 'e1':
        output = ROOT / 'outputs/historical'
        output.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(ROOT / 'audit/e1/E1_Compact_Audit_Reproduce.py'),
                        str(DATA / 'E1_Review_complete_safe_resume.zip')], cwd=output, check=True)
    else:
        run(a.family)

if __name__ == '__main__':
    main()
