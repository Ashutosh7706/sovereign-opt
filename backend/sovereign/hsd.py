"""Homogeneous self-dual (HSD) interior point method - robustness fallback (audit #19).

Xu-Hung-Ye / Andersen-Andersen homogeneous model for  min c'x, Ax = b, x >= 0:

      A x - b tau          = 0
     -A'y - s + c tau      = 0
      b'y - c'x - kappa    = 0          x, s, tau, kappa >= 0

The homogeneous model always has a strictly complementary solution, so it does not need
the LP itself to have an interior. At the limit either tau > 0 (x/tau, y/tau is optimal)
or kappa > 0 and (y or x) is a Farkas certificate of primal or dual infeasibility.

Used when the main Mehrotra IPM fails (e.g. a feasible set with (almost) empty interior).
Upper bounds are turned into explicit rows here: this is the robust path, not the fast one.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp

from .linalg import FactorizationError, NormalSolver
from .tolerances import TOL


@dataclass
class HSDResult:
    status: str  # optimal | primal_infeasible | dual_infeasible | max_iter | numerical_error
    x: np.ndarray
    y: np.ndarray
    s: np.ndarray
    w: np.ndarray | None
    z: np.ndarray | None
    iters: int
    log: list = field(default_factory=list)
    seconds: float = 0.0
    certificate: np.ndarray | None = None  # Farkas ray (y for primal infeasibility, x for dual)


def _step(v, dv):
    neg = dv < 0
    return float(np.min(-v[neg] / dv[neg])) if np.any(neg) else np.inf


def solve_hsd(A, b, c, u=None, tol: float = TOL.ipm_opt, max_iter: int = 200,
              dense_threshold: int = 3500, callback=None) -> HSDResult:
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        return _solve(A, b, c, u, tol, max_iter, dense_threshold, callback)


def _solve(A0, b0, c0, u, tol, max_iter, dense_threshold, callback) -> HSDResult:
    t0 = time.perf_counter()
    m0, n0 = A0.shape
    u = np.full(n0, np.inf) if u is None else np.asarray(u, float)
    U = np.where(np.isfinite(u))[0]
    k = len(U)
    if k:  # x_U + w = u_U as explicit rows
        B = sp.csr_matrix((np.ones(k), (np.arange(k), U)), shape=(k, n0))
        A = sp.vstack([sp.hstack([A0, sp.csr_matrix((m0, k))]),
                       sp.hstack([B, sp.identity(k, format="csr")])]).tocsr()
        b = np.concatenate([b0, u[U]])
        c = np.concatenate([c0, np.zeros(k)])
    else:
        A, b, c = A0.tocsr(), np.asarray(b0, float), np.asarray(c0, float)
    m, n = A.shape
    ns = NormalSolver(A, dense_threshold=dense_threshold)
    Aop, ATop = ns.A_op, ns.AT_op
    x, s, y = np.ones(n), np.ones(n), np.zeros(m)
    tau = kappa = 1.0
    bn, cn = 1.0 + np.linalg.norm(b), 1.0 + np.linalg.norm(c)
    log = []
    status = "max_iter"
    cert = None

    def result(st):
        xs, ss, ys = (x / tau, s / tau, y / tau) if st == "optimal" else (x, s, y)
        w = xs[n0:] if k else None
        z = None
        if k:
            z = np.zeros(n0)
            z[U] = -ys[m0:]
        return HSDResult(st, xs[:n0], ys[:m0], ss[:n0], _full_w(w), z, it, log, time.perf_counter() - t0, cert)

    def _full_w(w):
        if not k:
            return None
        full = np.zeros(n0)
        full[U] = w
        return full

    it = 0
    for it in range(max_iter + 1):
        rp = b * tau - Aop @ x
        rd = c * tau - ATop @ y - s
        rg = kappa + c @ x - b @ y
        mu = (x @ s + tau * kappa) / (n + 1)
        pres = np.linalg.norm(rp) / tau / bn
        dres = np.linalg.norm(rd) / tau / cn
        pobj, dobj = c @ x / tau, b @ y / tau
        gap = abs(pobj - dobj) / (1 + abs(dobj))
        entry = {"iter": it, "pinf": float(pres), "dinf": float(dres), "gap": float(gap), "mu": float(mu),
                 "tau": float(tau), "kappa": float(kappa), "algo": "hsd"}
        log.append(entry)
        if callback:
            callback(entry)
        if pres < tol and dres < tol and gap < tol:
            status = "optimal"
            break
        # infeasibility certificates (normalised rays)
        by, cx = b @ y, c @ x
        if by > 1e-12 and np.linalg.norm(ATop @ y + s) / by < tol and tau < 1e-3 * kappa:
            status, cert = "primal_infeasible", y.copy()
            break
        if cx < -1e-12 and np.linalg.norm(Aop @ x) / -cx < tol and tau < 1e-3 * kappa:
            status, cert = "dual_infeasible", x.copy()
            break
        if it == max_iter or not np.isfinite(mu):
            break

        d = x / s
        try:
            ns.factorize(d, precision="fp64")
        except FactorizationError:
            status = "numerical_error"
            break
        # M q = A D c + b  is shared by predictor and corrector
        q, _ = ns.solve(Aop @ (d * c) + b)

        def newton(eta, rxs, rtk):
            p, _ = ns.solve(eta * rp - Aop @ (d * (rxs / x - eta * rd)))
            uu = d * (ATop @ p - eta * rd + rxs / x)
            vv = d * (ATop @ q - c)
            dtau = (eta * rg + c @ uu - b @ p + rtk / tau) / (-(c @ vv) + b @ q + kappa / tau)
            dy = p + q * dtau
            dx = uu + vv * dtau
            ds = (rxs - s * dx) / x
            dkappa = (rtk - kappa * dtau) / tau
            return dx, dy, ds, dtau, dkappa

        def alpha(dx, ds, dtau, dkappa):
            a = min(_step(x, dx), _step(s, ds),
                    -tau / dtau if dtau < 0 else np.inf, -kappa / dkappa if dkappa < 0 else np.inf)
            return a

        dxa, dya, dsa, dta, dka = newton(1.0, -x * s, -tau * kappa)
        aa = min(1.0, alpha(dxa, dsa, dta, dka))
        mu_a = ((x + aa * dxa) @ (s + aa * dsa) + (tau + aa * dta) * (kappa + aa * dka)) / (n + 1)
        sigma = min(1.0, (mu_a / mu) ** 3) if mu > 0 else 0.0
        dx, dy, ds, dt, dk = newton(1.0 - sigma, -x * s + sigma * mu - dxa * dsa,
                                    -tau * kappa + sigma * mu - dta * dka)
        a = min(1.0, 0.99 * alpha(dx, ds, dt, dk))
        x, y, s = x + a * dx, y + a * dy, s + a * ds
        tau, kappa = tau + a * dt, kappa + a * dk
        entry.update({"alpha": a, "sigma": sigma})
    return result(status)
