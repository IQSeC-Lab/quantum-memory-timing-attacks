"""Engineering fixtures only. These tests do not execute quantum simulations."""
import argparse
import concurrent.futures as cf
import importlib
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import process_recycling as recycling
import recover_native as recovery

PACKAGE=Path(os.environ.get('P1_FROZEN_PACKAGE',str(Path(__file__).resolve().parents[1]/'native_start_work/P1_Feedback_Closure'))).resolve()

def identify(x):return dict(value=x,pid=os.getpid())
def raise_memory(x):raise MemoryError('deliberate engineering fixture')

def fixture_worker(job,folder):
    # Explicitly synthetic lifecycle fixture in TemporaryDirectory only.
    r,d=recovery.original_modules(PACKAGE);folder=Path(folder);key=d.stable(job)
    raw='raw/'+key+'.json.gz';compact='compact/'+key+'.json.gz'
    r.write(folder/raw,dict(kind='OFFLINE_ENGINEERING_FIXTURE'));r.write(folder/compact,[])
    receipt=dict(job=job,raw=raw,compact=compact,raw_sha256=r.digest(folder/raw),compact_sha256=r.digest(folder/compact),native_executions=1,wall_seconds=.001,status='OFFLINE_ENGINEERING_FIXTURE')
    r.write(folder/'checkpoints'/(key+'.json'),receipt);return receipt

class RecoveryChecks(unittest.TestCase):
    def test_one_fresh_process_per_chunk_and_wait_compatibility(self):
        with recycling.FreshProcessExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn')) as pool:
            futures=[pool.submit(identify,i) for i in range(5)]
            done,pending=cf.wait(futures)
            self.assertFalse(pending);rows=[f.result() for f in futures]
        self.assertEqual([r['value'] for r in rows],list(range(5)))
        self.assertEqual(len({r['pid'] for r in rows}),5)
        self.assertTrue(all(r['pid']!=os.getpid() for r in rows))

    def test_memoryerror_propagates_without_hanging_or_success(self):
        with recycling.FreshProcessExecutor(max_workers=1) as pool:
            future=pool.submit(raise_memory,0)
            with self.assertRaisesRegex(MemoryError,'engineering fixture'):future.result(timeout=30)

    def test_executor_restored_after_exception(self):
        before=cf.ProcessPoolExecutor
        with self.assertRaisesRegex(ValueError,'fixture'):
            with recycling.fresh_workers():
                self.assertIs(cf.ProcessPoolExecutor,recycling.FreshProcessExecutor)
                raise ValueError('fixture')
        self.assertIs(cf.ProcessPoolExecutor,before)

    def test_original_resume_reuses_verified_checkpoints(self):
        r,d=recovery.original_modules(PACKAGE)
        jobs=[dict(fixture_index=i) for i in range(3)]
        before=r.source_hashes()
        with tempfile.TemporaryDirectory() as tmp, patch.object(d,'jobs',return_value=iter(jobs)), patch.object(r,'worker',fixture_worker), patch.object(r,'preflight',return_value={'status':'PASS'}), patch.object(r,'analyze',return_value={'status':'OFFLINE_ENGINEERING_FIXTURE'}):
            with recycling.fresh_workers():r.run('timing','smoke',Path(tmp),2)
            receipts=[r.completed(Path(tmp),job) for job in jobs]
            self.assertTrue(all(receipts))
            # Reset the one-shot fixture iterator, then assert resume launches
            # zero process pools while retaining the existing exact receipts.
            with patch.object(d,'jobs',return_value=iter(jobs)), patch.object(recycling,'REAL_PROCESS_EXECUTOR',side_effect=AssertionError('Resume attempted to rerun a complete chunk')):
                # Use the adapter directly: context restoration check above
                # intentionally requires the real executor's original identity.
                with patch.object(cf,'ProcessPoolExecutor',recycling.FreshProcessExecutor):r.run('timing','smoke',Path(tmp),2)
            self.assertEqual([r.completed(Path(tmp),job) for job in jobs],receipts)
        self.assertEqual(r.source_hashes(),before)

    def test_frozen_source_integrity_and_preflight_fail_closed(self):
        recovery.verify_sources(PACKAGE)
        r,d=recovery.original_modules(PACKAGE);before=r.source_hashes()
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'preflight',return_value={'status':'BLOCKED','errors':['fixture missing dependency']}), patch.object(recovery,'call_original') as launch:
            args=argparse.Namespace(package=PACKAGE,results=Path(tmp)/'results',family='timing',mode='full',workers=8,repo=None)
            with self.assertRaisesRegex(RuntimeError,'preflight blocked'):recovery.launch(args)
            launch.assert_not_called()
        self.assertEqual(r.source_hashes(),before)

if __name__=='__main__':unittest.main(verbosity=2)
