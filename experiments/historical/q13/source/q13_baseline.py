"""NetSquid branch-level check against independently computed NumPy DQC states."""
import hashlib
import numpy as np
import q13_design as d
import physics_reference as ref

TOL = 2e-9


def one(s, seed):
    import netsquid as ns
    from netsquid.protocols import Protocol
    from netsquid.qubits import qubitapi as qapi, depolarize
    from netsquid.qubits.operators import X, create_rotation_op
    ns.sim_reset()
    ns.set_qstate_formalism(ns.QFormalism.DM)
    ns.set_random_state(seed=int(seed))
    matrix = ref.RHO_EPR_ROUNDED if s['matrix']=='rounded_si' else np.array(d.MODELS['exact_epr_matrix'], dtype=complex)
    matrix = matrix / np.trace(matrix).real
    ref.check_density(matrix)
    alpha = (np.pi/2, np.pi)[s['alpha_index']]
    theta = (np.pi/4, np.pi/2, np.pi)[s['theta_index']]
    pars = d.PROFILES[s['profile']]
    client, server = qapi.create_qubits(2)
    qapi.assign_qstate([client, server], matrix)
    qapi.operate(server, X)
    # Native chronological client gates, rather than importing a NumPy final state.
    for angle, axis in [(np.pi/2,(0,1,0)), (theta,(1,0,0)), (np.pi,(1,0,0))]:
        qapi.operate(client, create_rotation_op(angle, axis))
    mc, prob = qapi.measure(client)
    mc = int(mc)
    duration_ns = int(round(s['age_ms'] * 1e6))
    endpoints = [duration_ns] if s['partition']=='whole' else sorted(set([duration_ns//3, 2*duration_ns//3, duration_ns]))
    applied = []
    class Memory(Protocol):
        def run(self):
            previous = 0
            for endpoint in endpoints:
                if endpoint > ns.sim_time():
                    yield self.await_timer(endpoint - ns.sim_time())
                a, b = previous / 1e6, endpoint / 1e6
                exponent = (b/pars['t_ms'])**pars['exponent'] - (a/pars['t_ms'])**pars['exponent']
                contraction = float(np.exp(-exponent))
                depolarize(server, 1.0-contraction)
                applied.append(dict(start_age_ns=previous, end_age_ns=endpoint,
                                    sim_time_ns=float(ns.sim_time()), contraction=contraction))
                previous = endpoint
    memory = Memory()
    memory.start()
    ns.sim_run()
    assert abs(ns.sim_time()-duration_ns) < 1e-6
    delta = alpha-theta+mc*np.pi
    for angle, axis in [(np.pi/2,(0,1,0)), (delta,(1,0,0)), (np.pi,(1,0,0))]:
        qapi.operate(server, create_rotation_op(angle, axis))
    observed = qapi.reduced_dm([server])
    branches = {m: (p, state) for m,p,state in ref.delegated_branches(alpha,theta,matrix)}
    expected_prob, pure_branch = branches[mc]
    expected = ref.depolarize_between(pure_branch, 0, s['age_ms'], pars['t_ms'], pars['exponent'])
    error = float(np.max(np.abs(observed-expected)))
    target = ref.ideal_target(alpha)
    observed_fidelity = float(qapi.fidelity(server, target.reshape(-1,1), squared=True))
    expected_fidelity = float((target.conj() @ expected @ target).real)
    assert abs(float(prob)-expected_prob) < TOL, ('Client branch probability', prob, expected_prob)
    assert error < TOL and abs(observed_fidelity-expected_fidelity)<TOL, (s, error)
    ref.check_density(observed, TOL)
    return dict(seed=int(seed), branch=mc, branch_probability=float(prob),
                expected_branch_probability=float(expected_prob), max_density_error=error,
                fidelity=observed_fidelity, reference_fidelity=expected_fidelity,
                observed_density_real=observed.real.tolist(), observed_density_imag=observed.imag.tolist(),
                applied_intervals=applied, sim_end_ns=float(ns.sim_time()))


def run(s):
    base = int(hashlib.sha256(d.stable(s).encode()).hexdigest()[:8], 16)
    records, branches = [], set()
    for index in range(64):
        seed = int(np.random.SeedSequence([d.ROOTS['baseline'],base,index]).generate_state(1)[0] % (2**31-1)) + 1
        r = one(s, seed)
        records.append(r)
        branches.add(r['branch'])
        if branches == {0,1}:
            break
    assert branches == {0,1}, ('Both client branches not exercised', s)
    return dict(spec=s, baseline_records=records, simulator_executions=len(records),
                max_density_error=max(r['max_density_error'] for r in records),
                both_branches_checked=True, tolerance=TOL,
                scope='NetSquid numerical/circuit check against source-derived mathematics; not a new hardware observation')


def validate(r, s):
    assert r['spec'] == s and r['both_branches_checked']
    records = r['baseline_records']
    assert 2 <= len(records) <= 64 and {x['branch'] for x in records} == {0,1}
    assert r['max_density_error'] == max(x['max_density_error'] for x in records) < TOL
    assert all(abs(x['fidelity']-x['reference_fidelity']) < TOL for x in records)
    return dict(spec=s, simulator_executions=len(records), both_branches_checked=True,
                max_density_error=r['max_density_error'],
                max_fidelity_error=max(abs(x['fidelity']-x['reference_fidelity']) for x in records))
