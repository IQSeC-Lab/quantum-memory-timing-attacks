"""Validate planned admission separately from actual simulator causality.
No scheduling, timestamp modification, or relative tolerances.
"""
import math
ADMISSION_TOL_NS=1e-6
SERVICE_TOL_NS=1e-6

def check_admission(j):
    delta=j['admitted_ns']-j['requested_ns']
    assert math.isfinite(delta) and abs(delta)<=ADMISSION_TOL_NS, f'Admission mismatch: job={j!r}; delta_ns={delta!r}; tolerance_ns={ADMISSION_TOL_NS}'
    return delta

def check_payload(j,t):
    requested=j['requested_ns'];admitted=j['admitted_ns'];start=t['start_ns'];end=t['end_ns']
    d=dict(pid=j['pid'],kind=j['kind'],requested_ns=requested,admitted_ns=admitted,start_ns=start,end_ns=end,admission_delta_ns=admitted-requested,start_minus_requested_ns=start-requested,start_minus_admitted_ns=start-admitted,service_error_ns=(end-start)-j['instructions']*10000)
    assert all(math.isfinite(v) for v in [requested,admitted,start,end]), f'Nonfinite timing: {d!r}'
    check_admission(j)
    # Enforce actual causality exactly, even for tiny negative differences.
    assert start>=admitted, f'CPU starts before actual admission: {d!r}'
    assert end>=start, f'Negative CPU duration: {d!r}'
    assert abs(d['service_error_ns'])<SERVICE_TOL_NS, f'Incorrect CPU service: {d!r}'
    return d
