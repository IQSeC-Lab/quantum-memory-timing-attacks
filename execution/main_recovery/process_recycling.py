"""Python 3.10-compatible fresh-process executor for unchanged frozen workers.

Each manager thread owns exactly one spawned one-worker ProcessPoolExecutor.
It joins that executor before returning its result. Consequently at most W
native workers exist, and no native worker handles more than one chunk.
This external orchestration adapter does not edit any experiment source.
"""
import concurrent.futures as cf
from contextlib import contextmanager
import json
import multiprocessing
import os
import resource
import time

REAL_PROCESS_EXECUTOR = cf.ProcessPoolExecutor

def invoke(function,args,kwargs):
    started=time.perf_counter()
    try:return function(*args,**kwargs)
    finally:
        # Linux reports ru_maxrss in KiB. This is a per-process high-water mark,
        # not a measurement of the complete job/session or a cgroup limit.
        print(json.dumps(dict(event='fresh_worker_exit',pid=os.getpid(),
            peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            wall_seconds=time.perf_counter()-started)),flush=True)

def one_process(context,function,args,kwargs):
    with REAL_PROCESS_EXECUTOR(max_workers=1,mp_context=context) as executor:
        result=executor.submit(invoke,function,args,kwargs).result()
    # Process shutdown completes before the outer future becomes successful.
    return result

class FreshProcessExecutor:
    def __init__(self,max_workers=None,mp_context=None,**kwargs):
        if kwargs:raise TypeError('Unsupported executor options: '+str(sorted(kwargs)))
        self.context=mp_context or multiprocessing.get_context('spawn')
        if self.context.get_start_method()!='spawn':raise ValueError('spawn is required')
        self.threads=cf.ThreadPoolExecutor(max_workers=max_workers)
    def submit(self,function,*args,**kwargs):
        return self.threads.submit(one_process,self.context,function,args,kwargs)
    def __enter__(self):return self
    def __exit__(self,kind,value,tb):
        self.threads.shutdown(wait=True,cancel_futures=kind is not None)
        return False

@contextmanager
def fresh_workers():
    original=cf.ProcessPoolExecutor
    if original is not REAL_PROCESS_EXECUTOR:raise RuntimeError('Unexpected executor already installed')
    cf.ProcessPoolExecutor=FreshProcessExecutor
    try:yield
    finally:cf.ProcessPoolExecutor=original
