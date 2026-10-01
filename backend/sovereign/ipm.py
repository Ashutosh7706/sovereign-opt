"""Mehrotra predictor-corrector primal-dual interior point method (bounded form).

Solves      min c'x   s.t.  A x = b,  0 <= x <= u   (u_j may be +inf)
written as  A x = b,  x + w = u (finite u only),  x, w >= 0
dual        A'y + s - z = c,  s, z >= 0,           dual objective  b'y - u'z

Upper bounds are handled implicitly (extra complementarity pair w.z), so the normal
matrix stays m x m:   A Theta A' dy = r,   Theta^-1 = X^-1 S + W^-1 Z.

* Cold start: Lustig-Marsten-Shanno (LMS) heuristic (Sec. 12.4) -> strictly interior (x0, s0),
  extended to the bounded pair by splitting s~ into s0 - z0 and setting w0 = u - x0 (floored).
* Warm start: previous optimal iterate pushed back into the interior (re-optimization).
* Precision: 'fp64' or 'mixed' (FP32 factorization + FP64 refinement, snapping to FP64
  once ||r_b|| / (1 + ||b||) <= 1e-4, Sec. 12.2).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .linalg import FactorizationError, NormalSolver
from .tolerances import TOL


@dataclass
class IPMOptions:
    tol: float = TOL.ipm_opt
    relaxed_tol: float = TOL.ipm_relaxed  # accepted only if progress stalls (reported in the log)
    max_iter: int = 150
    precision: str = "mixed"  # 'mixed' | 'fp64'
    fp64_switch: float = TOL.fp64_switch
    step_eta: float = TOL.step_to_boundary
    use_gpu: bool = False
    dense_threshold: int = 3500
    gpu_mode: str = "auto"      # auto (size/VRAM policy) | force | off
    emit_iterates: bool = False  # attach the raw iterate to callbacks (digital twin, central path)
    algorithm: str = "auto"  # auto (Mehrotra, HSD fallback) | mehrotra | hsd


@dataclass
class IPMResult:
    status: str  # optimal | max_iter | diverged | numerical_error
    x: np.ndarray
    y: np.ndarray
    s: np.ndarray
    iters: int
    log: list = field(default_factory=list)
    seconds: float = 0.0
    fp64_switch_iter: Optional[int] = None
    linear_solver: str = ""
    start: str = "lms"
    w: Optional[np.ndarray] = None
    z: Optional[np.ndarray] = None
    tolerance: float = 1e-8
    gpu_decision: str = ""


def _max_step(v: np.ndarray, dv: np.ndarray) -> float:
    neg = dv < 0
    if not np.any(neg):
        return 1.0
    return float(min(1.0, np.min(-v[neg] / dv[neg])))


def lms_start(ns: NormalSolver, A, b, c):
    """Lustig-Marsten-Shanno starting point.
        x~ = A'(AA')^-1 b,  y~ = (AA')^-1 A c,  s~ = c - A'y~
        dx = max(-1.5 min x~, 0), ds = max(-1.5 min s~, 0)
        dx^ = dx + 0.5 (x^'s^)/(sum s^),  ds^ = ds + 0.5 (x^'s^)/(sum x^)   with x^ = x~ + dx e, s^ = s~ + ds e
        x0 = x~ + dx^ e > 0,  s0 = s~ + ds^ e > 0
    Returns (x0, y~, s0, s~)."""
    n = A.shape[1]
    A, AT = ns.A_op, ns.AT_op
    ns.factorize(np.ones(n), precision="fp64")
    t, _ = ns.solve(b)
    x = AT @ t
    y, _ = ns.solve(A @ c)
    s_t = c - AT @ y
    dx = max(-1.5 * float(x.min()), 0.0)
    ds = max(-1.5 * float(s_t.min()), 0.0)
    xh, sh = x + dx, s_t + ds
    xs = float(xh @ sh)
    if xs <= 0 or not np.isfinite(xs):  # degenerate case (e.g. x~ = s~ = 0)
        dx, ds = max(dx, 1.0), max(ds, 1.0)
        xh, sh = x + dx, s_t + ds
        xs = float(xh @ sh)
    dxh = dx + 0.5 * xs / max(float(sh.sum()), 1e-300)
    dsh = ds + 0.5 * xs / max(float(xh.sum()), 1e-300)
    x0 = np.maximum(x + dxh, 1e-4)
    s0 = np.maximum(s_t + dsh, 1e-4)
    return x0, y, s0, s_t


def solve_std(A, b, c, u: np.ndarray | None = None, opts: IPMOptions | None = None,
              callback: Callable[[dict], None] | None = None,
              warm: tuple | None = None) -> IPMResult:
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        return _solve_std(A, b, c, u, opts or IPMOptions(), callback, warm)


def _solve_std(A, b, c, u, opts: IPMOptions, callback, warm) -> IPMResult:
    t0 = time.perf_counter()
    m, n = A.shape
    u = np.full(n, np.inf) if u is None else np.asarray(u, float)
    U = np.isfinite(u)
    uU = np.where(U, u, 0.0)
    nU = int(U.sum())
    ns = NormalSolver(A, dense_threshold=opts.dense_threshold, use_gpu=opts.use_gpu, gpu_mode=opts.gpu_mode)

    def emit(entry):
        if not callback:
            return
        if opts.emit_iterates:  # the logged entry stays small; only the live callback gets arrays
            callback({**entry, "_x": x.copy(), "_y": y.copy(), "_s": s.copy(), "_w": w.copy(), "_z": z.copy()})
        else:
            callback(entry)
    try:
        if warm is not None:
            x, y, s, w, z = (np.array(v, float) for v in warm)
            shift = 1e-2 * max(1.0, 0.1 * float(np.mean(x) + np.mean(s)))
            x = np.maximum(x, 0) + shift
            s = np.maximum(s, 0) + shift
            w = np.where(U, np.maximum(w, 0) + shift, 0.0)
            z = np.where(U, np.maximum(z, 0) + shift, 0.0)
            start = "warm"
        else:
            x, y, s, s_t = lms_start(ns, A, b, c)
            floor = max(1e-2, float(np.mean(x)) * 1e-2)
            # bounded pair: keep x inside (0, u), split the LMS dual slack into s - z
            x = np.where(U, np.minimum(x, np.maximum(0.9 * uU, 1e-3)), x)
            w = np.where(U, np.maximum(uU - x, floor), 0.0)
            shift_s = float(s.mean()) * 0.1
            s = np.where(U, np.maximum(s_t, 0) + max(shift_s, 1e-2), s)
            z = np.where(U, np.maximum(-s_t, 0) + max(shift_s, 1e-2), 0.0)
            start = "lms"
    except FactorizationError:
        zz = np.zeros
        return IPMResult("numerical_error", zz(n), zz(m), zz(n), 0, [], time.perf_counter() - t0)

    A, AT = ns.A_op, ns.AT_op  # dense arrays for small problems, CSR otherwise
    bn = 1.0 + np.linalg.norm(b) + np.linalg.norm(uU)
    cn = 1.0 + np.linalg.norm(c)
    precision = "fp32" if opts.precision == "mixed" else "fp64"
    switch_iter = None if precision == "fp32" else 0
    log = []
    status = "max_iter"
    best = None  # (merit, it, x, y, s, w, z)
    last_good_it = 0
    mu0 = None
    it = 0
    for it in range(opts.max_iter + 1):
        rp = b - A @ x
        ru = np.where(U, uU - x - w, 0.0)
        rd = c - AT @ y - s + z
        mu = float(x @ s + w @ z) / (n + nU)
        pobj, dobj = float(c @ x), float(b @ y - uU @ z)
        pinf = float(np.sqrt(np.linalg.norm(rp) ** 2 + np.linalg.norm(ru) ** 2)) / bn
        dinf = float(np.linalg.norm(rd)) / cn
        gap = abs(pobj - dobj) / (1.0 + abs(pobj))
        merit = max(pinf, dinf, gap)
        mu0 = mu if mu0 is None else mu0
        if best is None or merit < 0.5 * best[0]:
            last_good_it = it
        if best is None or merit < best[0]:
            best = (merit, it, x, y, s, w, z)
        if precision == "fp32" and pinf <= opts.fp64_switch:
            precision, switch_iter = "fp64", it
        entry = {"iter": it, "pobj": pobj, "dobj": dobj, "pinf": pinf, "dinf": dinf, "gap": gap,
                 "mu": mu, "precision": precision, "t": time.perf_counter() - t0}
        if merit < opts.tol:
            log.append(entry)
            emit(entry)
            status = "optimal"
            break
        # end-game pathology: complementarity has collapsed but feasibility stopped improving
        stalled = mu < 1e-12 * mu0 and it - last_good_it >= 8
        if it == opts.max_iter or stalled:
            log.append(entry)
            if best[0] < opts.relaxed_tol:  # accept best iterate at the relaxed tolerance
                status = "optimal"
                _, bit, x, y, s, w, z = best
                log.append({**log[best[1]], "note": f"stalled; best iterate {bit} accepted at "
                                                    f"tolerance {opts.relaxed_tol:g}"})
            break
        if not (np.isfinite(mu) and np.isfinite(pinf) and np.isfinite(dinf)) or \
                (it > 5 and (np.max(np.abs(x)) > 1e14 or np.max(np.abs(y)) > 1e14)):
            log.append(entry)
            status = "diverged"
            break

        theta = 1.0 / (s / x + np.where(U, z / np.where(U, w, 1.0), 0.0))
        try:
            ns.factorize(theta, precision=precision)
        except FactorizationError:
            status = "numerical_error"
            log.append(entry)
            break
        if precision == "fp32" and ns.precision == "fp64":  # linear solver escalated
            precision, switch_iter = "fp64", it
            entry["precision"] = precision
        wsafe = np.where(U, w, 1.0)

        def newton(rxs, rwz):
            rhat = rd - rxs / x + np.where(U, (rwz - z * ru) / wsafe, 0.0)
            dy, info = ns.solve(rp + A @ (theta * rhat))
            dx_ = theta * (AT @ dy - rhat)
            ds_ = (rxs - s * dx_) / x
            dw_ = np.where(U, ru - dx_, 0.0)
            dz_ = np.where(U, (rwz - z * dw_) / wsafe, 0.0)
            return dx_, dy, ds_, dw_, dz_, info

        def steps(dx_, ds_, dw_, dz_):
            ap_ = min(_max_step(x, dx_), _max_step(w[U], dw_[U]) if nU else 1.0)
            ad_ = min(_max_step(s, ds_), _max_step(z[U], dz_[U]) if nU else 1.0)
            return ap_, ad_

        # predictor (affine scaling)
        dxa, dya, dsa, dwa, dza, info = newton(-x * s, -w * z)
        if precision == "fp32" and info["rel_residual"] > TOL.fp32_accept:
            # FP32 factor too inaccurate even after refinement -> escalate permanently
            precision, switch_iter = "fp64", it
            ns.factorize(theta, precision="fp64")
            dxa, dya, dsa, dwa, dza, info = newton(-x * s, -w * z)
        ap, ad = steps(dxa, dsa, dwa, dza)
        mu_aff = float((x + ap * dxa) @ (s + ad * dsa) + (w + ap * dwa) @ (z + ad * dza)) / (n + nU)
        sigma = (mu_aff / mu) ** 3 if mu > 0 else 0.0
        # corrector (centering + second-order)
        dx, dy, ds, dw, dz, info = newton(sigma * mu - x * s - dxa * dsa,
                                          np.where(U, sigma * mu - w * z - dwa * dza, 0.0))
        eta = min(0.9999, max(opts.step_eta, 1.0 - mu))
        ap, ad = steps(dx, ds, dw, dz)
        ap, ad = min(1.0, eta * ap), min(1.0, eta * ad)
        x = x + ap * dx
        w = w + ap * dw
        y = y + ad * dy
        s = s + ad * ds
        z = z + ad * dz
        entry.update({"alpha_p": ap, "alpha_d": ad, "sigma": sigma, "refine": info["refine_steps"],
                      "cond": ns.cond_estimate, "linear_solver": ns._kind})
        log.append(entry)
        emit(entry)

    tol = opts.tol if status != "optimal" or best[0] < opts.tol else opts.relaxed_tol
    return IPMResult(status, x, y, s, it, log, time.perf_counter() - t0, switch_iter,
                     getattr(ns, "_kind", ""), start, w, z, tol, ns.gpu_decision)
