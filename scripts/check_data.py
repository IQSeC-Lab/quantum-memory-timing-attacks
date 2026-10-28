"""Verify the companion archives before analysis."""
import hashlib
import argparse
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--full-records',action='store_true')
a=p.parse_args()
manifest = json.loads((root / ('data/MANIFEST.json' if a.full_records else 'data/ANALYSIS_MANIFEST.json')).read_text())
for item in manifest['files']:
    path = root / item['path']
    assert path.is_file(), 'Missing companion data: ' + item['path']
    assert path.stat().st_size == item['bytes'], 'Size mismatch: ' + item['path']
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    assert h.hexdigest() == item['sha256'], 'Checksum mismatch: ' + item['path']
print('PASS_DATA_CHECKSUMS:', len(manifest['files']), 'full archives' if a.full_records else 'analysis inputs')
