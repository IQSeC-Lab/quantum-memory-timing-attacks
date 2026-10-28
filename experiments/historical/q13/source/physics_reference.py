"""Deterministic reference for final QNodeOS paper Fig. 4 and supplement 4.4.

This is a density-matrix calculation, not a NetSquid or hardware execution.
The supplement's rounded rho_EPR is normalized (trace 1.001 before normalization).
Qubit ordering is (client, server), matching deposited epr_with_explicit_state.
Raw Psi+ is corrected to Phi+ on server.
The local circuit follows final Fig. 4a; ideal target is H Rz(alpha) |+>.
The depolarizing envelope is interpreted as Bloch contraction exp[-(t/13ms)^1.67].
The deposited simulation calls depolarize(1-D); this NumPy calculation
implements that isotropic mixture algebra without executing the NetSquid API.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np

I = np.eye(2, dtype=complex)
X = np.array([[0, 1], [1, 0]], dtype=complex)
Y = np.array([[0, -1j], [1j, 0]], dtype=complex)
Z = np.diag([1., -1.]).astype(complex)
H = (X + Z) / np.sqrt(2.)
RHO_EPR_ROUNDED = np.array([[.049, 0, 0, 0], [0, .437, .284, 0],
                          [0, .284, .454, 0], [0, 0, 0, .061]], dtype=complex)
T_COH_MS = 13.0
STRETCH_EXPONENT = 1.67


def rotation(pauli, angle):
    return np.cos(angle / 2) * I - 1j * np.sin(angle / 2) * pauli


def check_density(rho, tolerance=1e-11):
    assert np.allclose(rho, rho.conj().T, atol=tolerance, rtol=0)
    assert abs(np.trace(rho) - 1) < tolerance
    assert np.linalg.eigvalsh(rho).min() >= -tolerance


def coherence(age_ms, t_coh_ms=T_COH_MS, exponent=STRETCH_EXPONENT):
    if age_ms < 0:
        raise ValueError("Qubit age must be nonnegative")
    return np.exp(-(age_ms / t_coh_ms) ** exponent)


def depolarize_between(rho, start_age_ms, end_age_ms, t_coh_ms=T_COH_MS, exponent=STRETCH_EXPONENT):
    """Apply absolute-age channel increment, never reset memory age at events."""
    if start_age_ms < 0 or end_age_ms < start_age_ms:
        raise ValueError("Ages must be ordered and nonnegative")
    factor = np.exp(-((end_age_ms / t_coh_ms) ** exponent -
                      (start_age_ms / t_coh_ms) ** exponent))
    return factor * rho + (1.0 - factor) * I / 2


def delegated_branches(alpha, theta, rho_epr=None):
    raw = RHO_EPR_ROUNDED if rho_epr is None else np.asarray(rho_epr, dtype=complex)
    rho = raw / np.trace(raw).real
    check_density(rho)
    correction = np.kron(I, X)
    rho = correction @ rho @ correction.conj().T
    client = rotation(X, np.pi) @ rotation(X, theta) @ rotation(Y, np.pi / 2)
    unitary = np.kron(client, I)
    rho = unitary @ rho @ unitary.conj().T
    branches = []
    for mc in (0, 1):
        idx = [2 * mc, 2 * mc + 1]
        conditional = rho[np.ix_(idx, idx)]
        probability = np.trace(conditional).real
        if probability <= 0:
            continue
        conditional = conditional / probability
        delta = alpha - theta + mc * np.pi
        server = rotation(X, np.pi) @ rotation(Y, np.pi / 2) @ rotation(Z, delta)
        final = server @ conditional @ server.conj().T
        check_density(final)
        branches.append((mc, float(probability), final))
    assert abs(sum(p for _, p, _ in branches) - 1) < 1e-11
    return branches


def ideal_target(alpha):
    return H @ rotation(Z, alpha) @ np.array([1, 1], dtype=complex) / np.sqrt(2)


def fidelity(alpha, theta, age_ms, rho_epr=None, t_coh_ms=T_COH_MS, exponent=STRETCH_EXPONENT):
    target = ideal_target(alpha)
    return float(sum(probability * (target.conj() @ depolarize_between(state, 0, age_ms, t_coh_ms, exponent) @ target).real
                     for _, probability, state in delegated_branches(alpha, theta, rho_epr)))


def reference_summary():
    psi_plus = np.array([0, 1, 1, 0], dtype=complex) / np.sqrt(2)
    normalized = RHO_EPR_ROUNDED / np.trace(RHO_EPR_ROUNDED).real
    settings = [(a, th) for a in (np.pi / 2, np.pi) for th in (np.pi / 4, np.pi / 2, np.pi)]
    rows = []
    for alpha, theta in settings:
        f0 = fidelity(alpha, theta, 0)
        ratio = (.667 - .5) / (f0 - .5)
        crossing = T_COH_MS * (-np.log(ratio)) ** (1 / STRETCH_EXPONENT) if 0 < ratio <= 1 else None
        rows.append(dict(alpha_over_pi=alpha/np.pi, theta_over_pi=theta/np.pi,
                         fidelity_at_0ms=f0,
                         fidelity_at_4_8ms=fidelity(alpha, theta, 4.8),
                         fidelity_at_8_95ms=fidelity(alpha, theta, 8.95),
                         first_crossing_of_0_667_ms=crossing,
                         client_branch_probabilities=[p for _, p, _ in delegated_branches(alpha,theta)]))
    return dict(stage="deterministic_density_matrix_reference",
                execution="NumPy linear algebra only; NetSquid and hardware NOT executed",
                source="Nature 639, 321-328 (2025), final Fig.4a and supplement sections4.1-4.4",
                source_doi="10.1038/s41586-025-08704-w",
                raw_rounded_matrix_trace=float(np.trace(RHO_EPR_ROUNDED).real),
                normalized_epr_fidelity_psi_plus=float((psi_plus.conj()@normalized@psi_plus).real),
                channel_interpretation="Bloch contraction D(t)=exp[-(t/13ms)^1.67]; depolarization rho -> D*rho+(1-D)*I/2",
                age_policy="Use total absolute memory age, or conditional contraction D(end)/D(start)",
                validation_scope="All-six-setting deterministic extension of final circuit; deposited simulation sweeps only alpha=pi/2, theta=pi/4. Not independent hardware validation",
                settings=rows)


if __name__ == "__main__":
    result = reference_summary()
    output = Path(__file__).with_name("physics_reference_results.json")
    output.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps(result, indent=2, allow_nan=False))
