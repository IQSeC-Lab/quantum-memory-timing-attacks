"""Pure scheduling policy; independent trace replay is in q16_audit.py."""
import math
from q16_design import PERIOD_NS, BUDGET_NS, BLOCK_CAP_NS, TENANTS, SCHEDULERS


def integer_time(value):
    if not math.isfinite(value) or value < 0 or abs(value-round(value)) > 1e-5:
        raise ValueError('Expected nonnegative integral simulation nanoseconds')
    return int(round(value))


class Policy:
    def __init__(self, name):
        if name not in SCHEDULERS:
            raise ValueError(name)
        self.name = name
        self.queue = []
        self.cursor = 0
        self.reserved = {}
        self.selected = set()

    def decide(self, now, ready, owners, live_pids, costs):
        now = integer_time(now)
        if not ready or len(ready) != len(set(ready)) or set(owners) != set(ready):
            raise ValueError('Invalid ready set or ownership')
        if self.selected.intersection(ready):
            raise ValueError('A dispatched task became ready again')
        if any(tenant not in TENANTS for pid, tenant in owners.values()):
            raise ValueError('Unknown tenant')
        period = now // PERIOD_NS
        boundary = (period+1)*PERIOD_NS
        used = self.reserved.get(period, 0)
        before = list(self.queue)
        cursor_before = self.cursor
        self.queue = [tid for tid in self.queue if tid in ready]
        self.queue.extend(tid for tid in ready if tid not in self.queue)
        blocked = {}
        for tid in self.queue:
            if owners[tid][1] == 'attacker':
                cost = costs.get(tid)
                if type(cost) is not int or not 0 < cost <= BLOCK_CAP_NS:
                    raise ValueError('Unverified or oversized attacker block')
                reasons = []
                if cost > BUDGET_NS-used:
                    reasons.append('insufficient_budget')
                if now+cost > boundary:
                    reasons.append('period_boundary')
                if reasons:
                    blocked[str(tid)] = reasons
        eligible = [tid for tid in self.queue if str(tid) not in blocked]
        chosen = None
        if eligible:
            if self.name == 'QUOTA_RR':
                for offset in range(len(TENANTS)):
                    index = (self.cursor+offset) % len(TENANTS)
                    candidates = [tid for tid in eligible if owners[tid][1] == TENANTS[index]]
                    if candidates:
                        chosen = candidates[0]
                        self.cursor = (index+1) % len(TENANTS)
                        break
            elif self.name == 'QUOTA_PROTECTED':
                candidates = [tid for tid in eligible if owners[tid][0] in live_pids]
                chosen = (candidates or eligible)[0]
            else:
                chosen = eligible[0]
            self.queue.remove(chosen)
            self.selected.add(chosen)
            if owners[chosen][1] == 'attacker':
                self.reserved[period] = used+costs[chosen]
        return dict(time_ns=now, ready=list(ready),
                    ready_owners={str(t):owners[t][0] for t in ready},
                    ready_tenants={str(t):owners[t][1] for t in ready},
                    costs_ns={str(t):costs[t] for t in ready if t in costs},
                    live_pids=sorted(live_pids), queue_before=before, queue_after=list(self.queue),
                    eligible=eligible, blocked=blocked, selected=chosen,
                    period=period, reserved_before_ns=used,
                    reserved_after_ns=self.reserved.get(period, 0),
                    rr_cursor_before=cursor_before, rr_cursor_after=self.cursor,
                    wake_ns=boundary if chosen is None else None)
