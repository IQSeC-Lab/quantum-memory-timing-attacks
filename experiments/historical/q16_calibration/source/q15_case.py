"""Reviewed protection rule plus observational lifetime/dispatch evidence."""
from p1_engine import Case as ParentCase

class EventLog(list):
    def __init__(self,case):
        super().__init__();self.case=case
    def append(self,event):
        event=dict(event,sequence=self.case.next_sequence())
        super().append(event)

class Case(ParentCase):
    def next_sequence(self):
        value=self.sequence;self.sequence+=1;return value
    def _observe(self,node):
        self.sequence=0;self.lifetime_events=[];self.events=EventLog(self)
        super()._observe(node)
        original=node.memmgr.free
        def free(pid,virt_id,*args,**kwargs):
            result=original(pid,virt_id,*args,**kwargs)
            self.lifetime_events.append(dict(kind='free',pid=pid,virt=virt_id,
                time_ns=float(self.ns.sim_time()),sequence=self.next_sequence()))
            return result
        node.memmgr.free=free
    def _protect(self):
        # Delegate actual scheduling to the unchanged reviewed implementation.
        super()._protect()
        cpu=self.bob.scheduler.cpu_scheduler;original=cpu.choose_next_task
        def choose(ready):
            ready=list(ready);before=list(cpu._task_queue)
            owners={str(t):cpu._task_graph.get_tinfo(t).task.pid for t in ready}
            original(ready)
            self.decisions[-1].update(sequence=self.next_sequence(),queue_before=before,
                queue_after=list(cpu._task_queue),ready_owners=owners)
        cpu.choose_next_task=choose
