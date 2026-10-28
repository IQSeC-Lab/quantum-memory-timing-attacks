import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import audit_raw as a

class AuditChecks(unittest.TestCase):
    def test_stream_array_across_large_records_and_unicode(self):
        rows=[{'value':'a'*150000,'unicode':'qubit \u03c8'}, {'value':list(range(1000))}]
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'raw.gz'
            with gzip.open(p,'wt') as f:json.dump(rows,f,ensure_ascii=False)
            self.assertEqual(list(a.array_records(p)),rows)
    def test_truncation_trailing_data_and_trailing_comma_rejected(self):
        for text in ('[{"a":1}', '[{}] {}', '[{},]'):
            with tempfile.TemporaryDirectory() as t:
                p=Path(t)/'x.gz'
                with gzip.open(p,'wt') as f:f.write(text)
                with self.assertRaises(ValueError):list(a.array_records(p))
    def test_jsonl_has_exact_record_inventory(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'x.gz'
            with gzip.open(p,'wt') as f:f.write('{"a":1}\n{"a":2}\n')
            self.assertEqual(list(a.jsonl_records(p)),[{'a':1},{'a':2}])
    def test_paths_cannot_escape_study_directory(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):a.safe(t,'../outside')
    def test_changed_raw_fails_before_auditor_import_and_never_passes(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t)/'study';folder.mkdir();(folder/'raw').write_bytes(b'changed')
            (folder/'compact').write_text('[]');receipt=dict(raw='raw',compact='compact',raw_sha256='incorrect',compact_sha256=a.sha(folder/'compact'))
            a.write(folder/'checkpoints/x.json',receipt)
            job=dict(study='timing',kind='main',folder=str(folder),package=str(folder),receipt='x.json',receipt_sha256=a.sha(folder/'checkpoints/x.json'))
            with patch.object(a.importlib,'import_module',side_effect=AssertionError('Must not import an auditor after a checksum failure')):
                with self.assertRaisesRegex(ValueError,'checksum'):a.worker((job,str(Path(t)/'audit'),'fixture'))
            self.assertFalse((Path(t)/'audit/checkpoints/timing/x.json').exists())
            self.assertEqual(a.read(Path(t)/'audit/failures/timing/x.json')['status'],'FAIL_RAW_REPLAY')
    def test_missing_raw_blocks_indexing(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);package=root/'P1_Feedback_Closure';package.mkdir();folder=root/'results/timing-full'
            a.write(folder/'protocol.json',{'source_hashes':{}});a.write(folder/'COMPLETE.json',{})
            a.write(folder/'checkpoints/x.json',dict(raw='raw/missing.gz',compact='compact/missing.gz'))
            ref=dict(kind='main',protocol=a.read(folder/'protocol.json'),protocol_sha256=a.sha(folder/'protocol.json'),complete_sha256=a.sha(folder/'COMPLETE.json'),checkpoints={'x.json':a.sha(folder/'checkpoints/x.json')})
            with self.assertRaisesRegex(FileNotFoundError,'raw file missing'):a.index_study('timing',ref,root,root)

if __name__=='__main__':unittest.main(verbosity=2)
