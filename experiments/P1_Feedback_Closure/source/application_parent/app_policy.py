"""Small extension of the reviewed Q16 policy; reference equivalence is tested.

EDF choice is also checked against the actual native EDF method at runtime.
Critical-section readiness is supplied by the unchanged native scheduler.
"""
import math
import app_design as d


def integer_time(value):
    if not math.isfinite(value) or value < 0 or abs(value-round(value)) > 1e-5:
        raise ValueError('Expected nonnegative integral simulation nanoseconds')
    return int(round(value))


class Policy:
    def __init__(self, name):
        if name not in d.POLICIES:
            raise ValueError(name)
        self.name = name
        self.queue = []
        self.cursor = 0
        self.reserved = {}
        self.selected = set()

    def decide(self, now, ready, owners, live_pids, costs, absolute_deadlines):
        now = integer_time(now)
        if not ready or len(ready) != len(set(ready)) or set(owners) != set(ready):
            raise ValueError('Invalid ready set or ownership')
        if self.selected.intersection(ready):
            raise ValueError('A dispatched task became ready again')
        if any(tenant not in d.TENANTS for pid, tenant in owners.values()):
            raise ValueError('Unknown tenant')
        if set(absolute_deadlines) != set(ready) or any(type(v) is not int for v in absolute_deadlines.values()):
            raise ValueError('Explicit integer deadlines required for every ready task')
        period = now//d.PERIOD_NS
        boundary = (period+1)*d.PERIOD_NS
        used = self.reserved.get(period, 0)
        before, cursor_before = list(self.queue), self.cursor
        self.queue = [t for t in self.queue if t in ready]
        self.queue.extend(t for t in ready if t not in self.queue)
        blocked = {}
        for tid in self.queue:
            if owners[tid][1] == 'attacker':
                cost = costs.get(tid)
                if type(cost) is not int or not 0 < cost <= d.BLOCK_CAP_NS:
                    raise ValueError('Unverified or oversized attacker block')
                reasons = []
                if cost > d.BUDGET_NS-used:
                    reasons.append('insufficient_budget')
                if now+cost > boundary:
                    reasons.append('period_boundary')
                if reasons:
                    blocked[str(tid)] = reasons
        eligible = [t for t in self.queue if str(t) not in blocked]
        chosen = None
        if eligible:
            if self.name == 'QUOTA_RR':
                for offset in range(len(d.TENANTS)):
                    index = (self.cursor+offset)%len(d.TENANTS)
                    candidates = [t for t in eligible if owners[t][1] == d.TENANTS[index]]
                    if candidates:
                        chosen = candidates[0]
                        self.cursor = (index+1)%len(d.TENANTS)
                        break
            elif self.name == 'QUOTA_PROTECTED':
                candidates = [t for t in eligible if owners[t][0] in live_pids]
                chosen = (candidates or eligible)[0]
            elif self.name == 'QUOTA_EDF_NUMERIC':
                chosen = min(eligible, key=lambda t:absolute_deadlines[t])
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
            eligible=eligible, blocked=blocked, selected=chosen, period=period,
            reserved_before_ns=used, reserved_after_ns=self.reserved.get(period,0),
            rr_cursor_before=cursor_before, rr_cursor_after=self.cursor,
            wake_ns=boundary if chosen is None else None,
            absolute_deadlines_ns={str(t):absolute_deadlines[t] for t in ready})
