#!/usr/bin/env python3
"""Independent archive-only cross-check for E1 audit receipts.

This checks raw-audit receipt claims against the compact E1 review archive.
The server-side raw simulator traces are NOT in either review archive, so this
script cannot independently hash or inspect those raw traces.
"""
from __future__ import annotations
import collections
import hashlib
import json
import sys
import zipfile
from pathlib import Path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(raw_audit_zip: Path, compact_zip: Path) -> None:
    with zipfile.ZipFile(raw_audit_zip) as a, zipfile.ZipFile(compact_zip) as c:
        report = json.loads(a.read('E1_RAW_AUDIT_full.json'))
        receipts = json.loads(a.read('E1_BATCH_RECEIPTS_full.json'))
        assert report['status'] == 'PASS_FULL_RAW'
        assert report['mode'] == 'full'
        assert report['version'] == 'e1-raw-independent-audit-v1.1'
        assert len(receipts) == 585
        for key, name in (
            ('input_protocol_sha256', 'protocol.json'),
            ('input_analysis_sha256', 'analysis.json'),
            ('frozen_policy_file_sha256', 'frozen_policies.json'),
        ):
            assert sha(c.read(name)) == report[key], f'{name}: hash mismatch'
        seen = set()
        totals = collections.Counter()
        for r in receipts:
            stage, index = r['stage'], r['index']
            assert stage in ('pilot', 'evaluation')
            key = (stage, index)
            assert key not in seen, f'Duplicate receipt {key}'
            seen.add(key)
            marker = json.loads(c.read(f'{stage}/batch-{index:05d}/complete.json'))
            for kind in ('raw', 'compact', 'stats'):
                assert r[f'{kind}_sha256'] == marker[f'{kind}_sha256'], f'{key}: {kind} digest mismatch'
            assert r['records'] == 128
            compact_bytes = c.read(f'{stage}/batch-{index:05d}/{marker["compact_file"]}')
            assert sha(compact_bytes) == r['compact_sha256'], f'{key}: compact byte hash mismatch'
            totals[stage] += r['records']
        for stage, nbatches in (('pilot', 9), ('evaluation', 576)):
            assert {i for s, i in seen if s == stage} == set(range(nbatches))
        assert totals == {'pilot': 1152, 'evaluation': 73728}
        assert sum(totals.values()) == report['verified_records'] == 74880
        assert len(seen) == report['verified_batches'] == 585
        assert sum(v['trials'] for v in report['evaluation']['policy_summaries'].values()) == 73728
        print('PASS: 585 receipts, complete contiguous batch index sets, 74,880 records')
        print('PASS: receipt digests match all archived checkpoint markers')
        print('PASS: every compact batch matches its SHA-256 receipt')
        print('PASS: source-analysis-policy identities match compact review archive')
        print('Scope: does not independently verify original server raw trace bytes')


if __name__ == '__main__':
    raw = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/mnt/data/E1_Raw_Audit_Review_full.zip')
    compact = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('/mnt/data/E1_Review_complete_safe_resume.zip')
    main(raw, compact)
