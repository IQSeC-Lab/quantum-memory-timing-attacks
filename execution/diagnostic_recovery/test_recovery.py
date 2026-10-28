"""Host-process fixtures; no native quantum execution in these tests."""
import argparse
import concurrent.futures as cf
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import process_recycling as cycling
import recover_additional as recovery

PACKAGE=Path(os.environ.get('P1_ADDITIONAL_PACKAGE',str(Path(__file__).resolve().parents[1]/'comparison_suite/P1_Additional_Studies'))).resolve()

def identity(x):return dict(value=x,pid=os.getpid())
def failure(x):raise MemoryError('deliberate engineering fixture')
def fixture_worker(args):
    family,i,folder=args;r,d=recovery.original_modules(PACKAGE);folder=Path(folder)
    rp='raw/%06d.json.gz'%i;cp='compact/%06d.json.gz'%i
    r.write(folder/rp,{'kind':'OFFLINE_ENGINEERING_FIXTURE'});r.write(folder/cp,[])
    c=dict(index=i,raw=rp,compact=cp,raw_sha256=r.digest(folder/rp),compact_sha256=r.digest(folder/cp),native_executions=1,wall_seconds=.001,status='OFFLINE_ENGINEERING_FIXTURE')
    r.write(folder/'checkpoints'/('%06d.json'%i),c);return c

class RecoveryChecks(unittest.TestCase):
    def test_ordered_map_is_bounded_and_uses_distinct_processes(self):
        consumed=[]
        def source():
            for i in range(5):consumed.append(i);yield i
        with cycling.FreshProcessExecutor(max_workers=2) as pool:
            result=pool.map(identity,source(),chunksize=1)
            first=next(result);self.assertEqual(len(consumed),2)
            rows=[first,*result]
        self.assertEqual([x['value'] for x in rows],list(range(5)))
        self.assertEqual(len({x['pid'] for x in rows}),5)

    def test_map_memoryerror_propagates(self):
        with cycling.FreshProcessExecutor(max_workers=1) as pool:
            with self.assertRaisesRegex(MemoryError,'engineering fixture'):list(pool.map(failure,[0],chunksize=1))

    def test_original_executor_restored_after_exception(self):
        original=cf.ProcessPoolExecutor
        with self.assertRaises(ValueError):
            with cycling.fresh_workers():raise ValueError('fixture')
        self.assertIs(cf.ProcessPoolExecutor,original)

    def test_original_stream_resume_reuses_checkpoints(self):
        r,d=recovery.original_modules(PACKAGE);before=r.hashes()
        p=d.protocol('causal','smoke');p['N']=3
        with tempfile.TemporaryDirectory() as t, patch.object(d,'protocol',return_value=p), patch.object(r,'parent_preflight',return_value={'status':'PASS'}), patch.object(r,'worker',fixture_worker), patch.object(r,'analyze',return_value={'status':'OFFLINE_ENGINEERING_FIXTURE'}):
            folder=Path(t)
            with cycling.fresh_workers():r.run('causal','smoke',folder,2)
            old=[r.checkpoint(folder,i) for i in range(3)]
            self.assertTrue(all(old))
            with patch.object(cycling,'REAL_PROCESS_EXECUTOR',side_effect=AssertionError('Resume attempted to rerun a completed stream')):
                with patch.object(cf,'ProcessPoolExecutor',cycling.FreshProcessExecutor):r.run('causal','smoke',folder,2)
            self.assertEqual([r.checkpoint(folder,i) for i in range(3)],old)
        self.assertEqual(r.hashes(),before)

    def test_original_worker_preserves_interrupted_partial(self):
        r,d=recovery.original_modules(PACKAGE)
        import app_native,app_audit
        spec=next(d.specs('causal',0))
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);partial=folder/'raw/000000.jsonl.gz.partial';partial.parent.mkdir()
            partial.write_bytes(b'old interrupted engineering fixture')
            with patch.object(d,'specs',return_value=iter([spec])), patch.object(app_native,'run',return_value={'kind':'OFFLINE_ENGINEERING_FIXTURE'}), patch.object(app_audit,'validate_record',return_value={'kind':'OFFLINE_ENGINEERING_FIXTURE'}):
                receipt=r.worker(('causal',0,str(folder)))
            saved=list((folder/'failures').glob('*-interrupted-*.jsonl.gz'))
            self.assertEqual(len(saved),1);self.assertEqual(saved[0].read_bytes(),b'old interrupted engineering fixture')
            self.assertIsNotNone(r.checkpoint(folder,0))

    def test_missing_native_preflight_cannot_launch_stages(self):
        r,d=recovery.original_modules(PACKAGE);recovery.verify_sources(r);before=r.hashes()
        with tempfile.TemporaryDirectory() as t, patch.object(r,'parent_preflight',return_value={'status':'BLOCKED','errors':['fixture missing dependency']}), patch.object(recovery,'call_original') as invoke:
            a=argparse.Namespace(package=PACKAGE,results=Path(t)/'results',family='causal',mode='full',workers=8,repo=None)
            with self.assertRaisesRegex(RuntimeError,'preflight blocked'):recovery.launch(a)
            invoke.assert_not_called()
        self.assertEqual(r.hashes(),before)

if __name__=='__main__':unittest.main(verbosity=2)
