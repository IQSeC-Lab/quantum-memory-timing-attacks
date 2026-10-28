"""Locate and export the exact server auditor matching the completed replay identity.

Run on the original server; reads source only and writes a separate ZIP.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile

EXPECTED = '2f2548544f6a0873fb55aed0ee88e0faa13158b6a88aeaa73eea2c02c358e7dd'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, default=Path.home() / 'P1')
    p.add_argument('--output', type=Path, default=Path.cwd() / 'P1_Executed_Auditor_Source.zip')
    a = p.parse_args()
    excluded = {'raw', 'compact', 'checkpoints', 'failures', '.git', '__pycache__', 'site-packages'}
    matches = []
    for folder, dirs, files in os.walk(a.project.expanduser()):
        dirs[:] = [x for x in dirs if x not in excluded and not x.startswith('.venv')]
        if 'audit_raw.py' not in files or 'REVIEWED_REFERENCE.json' not in files:
            continue
        root = Path(folder)
        sources = {x.name: sha(x) for x in root.glob('*.py')}
        identity = hashlib.sha256(json.dumps({'sources': sources,
            'reference': sha(root / 'REVIEWED_REFERENCE.json')}, sort_keys=True).encode()).hexdigest()
        if identity == EXPECTED:
            matches.append(root)
    if not matches:
        raise SystemExit('No source directory matches the completed auditor identity. No source ZIP was created.')
    root = matches[0]
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(a.output, 'w', zipfile.ZIP_DEFLATED) as z:
        for x in sorted(root.iterdir()):
            if x.is_file() and (x.suffix == '.py' or x.name in {'REVIEWED_REFERENCE.json', 'AUDITOR_SOURCE_HASHES.json'}):
                z.write(x, 'auditor/' + x.name)
        z.writestr('MATCHED_IDENTITY.json', json.dumps({'auditor_identity': EXPECTED,
            'source_directory': str(root), 'matching_directories': len(matches)}, indent=2) + '\n')
    print('Matched completed auditor:', EXPECTED)
    print('Source export:', a.output.resolve())

if __name__ == '__main__':
    main()
