"""End-to-end LP pipeline: presolve -> standard form -> Ruiz -> IPM -> postsolve."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import scipy.sparse as sp

from .hsd import solve_hsd
from .ipm import IPMOptions, IPMResult, solve_std
from .model import Model
from .tolerances import TOL
from .presolve import from_standard_form, postsolve, presolve, ruiz_scale, to_standard_form


@dataclass
class LPSolution:
    status: str  # optimal | infeasible | unbounded | numerical_error | max_iter
    objective: float  # internal (minimization) objective incl. constant
    x: np.ndarray
    y: np.ndarray  # row duals (original rows)
    d: np.ndarray  # reduced costs vs original A, c
    iters: int = 0
    seconds: float = 0.0
    log: list = field(default_factory=list)
    message: str = ""
    max_violation: float = 0.0
    fp64_switch_iter: Optional[int] = None
    linear_solver: str = ""
    start: str = "lms"
    presolve_log: list = field(default_factory=list)
    algorithm: str = "mehrotra"
    certificate: Optional[dict] = None  # verified Farkas certificate when infeasible
    gpu_decision: str = ""


class WarmStartCache:
    """Keeps the last optimal standard-form iterate per matrix structure so that
    re-optimizations after RHS / cost / bound-value changes start near the old optimum."""

    def __init__(self):
        self._store: dict[str, tuple] = {}

    @staticmethod
    def key(red: Model) -> str:
        h = hashlib.sha256()
        A = red.A.tocsr()
        for arr in (A.data, A.indices, A.indptr, np.isfinite(red.lb), np.isfinite(red.ub)):
            h.update(np.ascontiguousarray(arr).tobytes())
        h.update("".join(red.senses.tolist()).encode())
        return h.hexdigest()

    def get(self, k):
        return self._store.get(k)

    def put(self, k, point):
        self._store[k] = point
        if len(self._store) > 64:
            self._store.pop(next(iter(self._store)))


def max_violation(model: Model, x: np.ndarray) -> float:
    ax = model.A @ x
    v = 0.0
    for sense, fn in (("L", lambda r, b: r - b), ("G", lambda r, b: b - r), ("E", lambda r, b: np.abs(r - b))):
        mask = model.senses == sense
        if mask.any():
            v = max(v, float(np.max(fn(ax[mask], model.rhs[mask]), initial=0.0)))
    v = max(v, float(np.max(model.lb - x, initial=0.0)), float(np.max(x - model.ub, initial=0.0)))
    return v


def _phase1_infeasible(As, bs, us, opts: IPMOptions) -> tuple[bool | None, float]:
    """Elastic phase-1 on the scaled standard form: min 1'(p+q) s.t. A x + p - q = b, 0 <= x <= u."""
    m, n = As.shape
    I = sp.identity(m, format="csr")
    Ae = sp.hstack([As, I, -I]).tocsr()
    ce = np.concatenate([np.zeros(n), np.ones(2 * m)])
    ue = np.concatenate([us, np.full(2 * m, np.inf)])
    r = solve_std(Ae, bs, ce, ue, IPMOptions(tol=1e-9, max_iter=120, precision="fp64",
                                             dense_threshold=opts.dense_threshold))
    if r.status != "optimal":  # phase-1 itself failed: NO conclusion about feasibility
        return None, float("nan")
    infeas = float(ce @ r.x)
    return infeas > TOL.phase1_infeasible * (1 + np.linalg.norm(bs, 1)), infeas


def _ray_test(As, bs, cs, us, opts: IPMOptions, box: float = 1e7) -> bool:
    """Unboundedness check: cap every column at `box`; if the capped LP's optimum pushes
    some column onto the artificial cap, the original objective is unbounded."""
    ub = np.minimum(us, box)
    r = solve_std(As, bs, cs, ub, IPMOptions(tol=1e-9, max_iter=150, precision="fp64",
                                             dense_threshold=opts.dense_threshold))
    if r.status != "optimal":
        return False
    capped = np.isinf(us) & (r.x > 0.5 * box)
    return bool(capped.any())


def verify_farkas(model: Model, mu: np.ndarray, top: int = 8) -> dict:
    """Check an infeasibility certificate in ORIGINAL space: multipliers mu (>=0 on <= rows,
    <=0 on >= rows) give the valid inequality (mu'A) x <= mu'b; the model is infeasible if even
    the smallest value of (mu'A) x over the variable box exceeds mu'b."""
    scale = float(np.max(np.abs(mu))) or 1.0
    mu = mu / scale
    L, G = model.senses == "L", model.senses == "G"
    sign_ok = bool((mu[L] >= -1e-7).all() and (mu[G] <= 1e-7).all())
    mu = np.where(L, np.maximum(mu, 0), np.where(G, np.minimum(mu, 0), mu))
    g = model.A.T @ mu
    g[np.abs(g) < 1e-9 * (np.max(np.abs(g), initial=0) + 1e-300)] = 0.0
    lo = np.where(g > 0, model.lb, np.where(g < 0, model.ub, 0.0))
    if not np.isfinite(lo[g != 0]).all():
        box_min = -np.inf
    else:
        box_min = float(g[g != 0] @ lo[g != 0])
    rhs = float(mu @ model.rhs)
    verified = sign_ok and box_min > rhs + 1e-7 * (1 + abs(rhs))
    order = np.argsort(-np.abs(mu))[:top]
    rows = [{"row": model.row_names[i], "multiplier": float(mu[i]),
             "desc": (model.row_meta[i].get("desc") if model.row_meta else None) or model.row_names[i]}
            for i in order if abs(mu[i]) > 1e-9]
    return {"verified": bool(verified), "min_lhs_over_box": box_min, "rhs": rhs, "rows": rows}


def _model_space_callback(user_cb, model: Model, pres, std, scaling, n_pairs: int = 160):
    """Turns raw scaled standard-form iterates into model-space values for the live dashboard:
    x (plan), y (duals), the objective, and a fixed sample of complementarity pairs (x_j, s_j)
    that trace the central path (x_j * s_j = mu is a line of slope -1 in log-log space)."""
    idx_cache = {}

    def cb(e):
        if "_x" not in e:  # e.g. HSD fallback iterations
            user_cb(e)
            return
        e = dict(e)
        xi, yi, si = e.pop("_x"), e.pop("_y"), e.pop("_s")
        e.pop("_w", None)
        e.pop("_z", None)
        xs_, ys_, _ = scaling.unscale(xi, yi, si)
        xr, yr = from_standard_form(std, xs_, ys_)
        xm, ym, _ = postsolve(pres, model, xr, yr)
        e["x_model"], e["y_model"] = xm, ym
        e["obj_model"] = model.display_objective(float(model.c @ xm + model.obj_const))
        n = len(xi)
        if n not in idx_cache:
            idx_cache[n] = np.unique(np.linspace(0, n - 1, min(n, n_pairs)).astype(int))
        k = idx_cache[n]
        lx = np.log10(np.maximum(xi[k], 1e-300))
        ls = np.log10(np.maximum(si[k], 1e-300))
        e["xs_pairs"] = np.round(np.stack([lx, ls], 1), 3).tolist()
        user_cb(e)

    return cb


def solve_lp(model: Model, opts: IPMOptions | None = None,
             callback: Callable[[dict], None] | None = None,
             warm_cache: WarmStartCache | None = None,
             use_warm: bool = True) -> LPSolution:
    opts = opts or IPMOptions()
    t0 = time.perf_counter()
    pres = presolve(model)
    n, m = model.n, model.m
    if pres.status != "ok":
        return LPSolution(pres.status, np.nan, np.full(n, np.nan), np.zeros(m), np.zeros(n),
                          message=pres.message, seconds=time.perf_counter() - t0)
    red = pres.reduced
    if red.n == 0:  # everything fixed by presolve
        x, y, d = postsolve(pres, model, np.zeros(0), np.zeros(0))
        if pres.unbounded_if_feasible:
            return LPSolution("unbounded", np.nan, x, y, d, message=pres.unbounded_if_feasible,
                              seconds=time.perf_counter() - t0)
        return LPSolution("optimal", float(model.c @ x + model.obj_const), x, y, d,
                          seconds=time.perf_counter() - t0, max_violation=max_violation(model, x),
                          presolve_log=pres.log)
    std = to_standard_form(red)
    As, bs, cs, scaling = ruiz_scale(std.A, std.b, std.c)
    us = scaling.scale_upper(std.u)

    warm = None
    wkey = WarmStartCache.key(red) if warm_cache is not None else None
    if warm_cache is not None and use_warm:
        prev = warm_cache.get(wkey)
        if prev is not None and prev[0].shape[0] == As.shape[1]:
            warm = scaling.scale_point(*prev)

    if callback is not None and opts.emit_iterates:
        callback = _model_space_callback(callback, model, pres, std, scaling)
    res = None
    algorithm = "mehrotra"
    hsd_status = None
    certificate = None
    if opts.algorithm != "hsd":
        res = solve_std(As, bs, cs, us, opts, callback, warm)
        if warm is not None and res.status != "optimal":  # warm start went bad: retry cold
            res = solve_std(As, bs, cs, us, opts, callback, None)
    if opts.algorithm == "hsd" or (opts.algorithm == "auto" and res.status != "optimal"):
        # robustness fallback: homogeneous self-dual model (needs no interior, certifies infeasibility)
        h = solve_hsd(As, bs, cs, us, tol=opts.tol, dense_threshold=opts.dense_threshold, callback=callback)
        hsd_status = h.status
        prev_iters, prev_log = (res.iters, res.log) if res is not None else (0, [])
        nz = np.zeros(As.shape[1])
        if h.status == "optimal" or res is None:
            algorithm = "hsd" if res is None else "mehrotra -> hsd fallback"
            res = IPMResult("optimal" if h.status == "optimal" else h.status, h.x, h.y, h.s,
                            prev_iters + h.iters, prev_log + h.log, h.seconds,
                            getattr(res, "fp64_switch_iter", 0), "", "hsd",
                            h.w if h.w is not None else nz, h.z if h.z is not None else nz,
                            gpu_decision="CPU: homogeneous self-dual fallback runs on the CPU")
        if h.status == "primal_infeasible":
            ycert = scaling.gamma * scaling.Dr * h.certificate[: As.shape[0]]
            _, y_red_c = from_standard_form(std, np.zeros(As.shape[1]), ycert)
            _, y_c, _ = postsolve(pres, model, np.zeros(red.n), y_red_c)
            certificate = verify_farkas(model, -y_c)

    xs, ys, ss, ws, zs = scaling.unscale(res.x, res.y, res.s, res.w, res.z)
    x_red, y_red = from_standard_form(std, xs, ys)
    x, y, d = postsolve(pres, model, x_red, y_red)
    status, msg = res.status, ""
    if res.status == "optimal":
        if warm_cache is not None:
            warm_cache.put(wkey, (xs, ys, ss, ws, zs))
    elif certificate is not None and certificate["verified"]:
        status = "infeasible"
        msg = "HSD Farkas certificate (verified in original space): " +               "; ".join(r["desc"] for r in certificate["rows"][:4])
    else:
        infeasible, amount = _phase1_infeasible(As, bs, us, opts)
        if infeasible:
            status, msg = "infeasible", f"phase-1 elastic LP: minimum total violation {amount:.3g} (scaled) > 0"
        elif infeasible is None:
            status = "numerical_error"
            msg = f"IPM stopped with status {res.status}; phase-1 check also failed - feasibility UNKNOWN"
        elif res.status == "diverged" or hsd_status == "dual_infeasible" or _ray_test(As, bs, cs, us, opts):
            status, msg = "unbounded", "primal feasible and the objective improves without limit along a ray"
        else:
            msg = f"IPM stopped with status {res.status}"
    if pres.unbounded_if_feasible and status == "optimal":
        status, msg = "unbounded", pres.unbounded_if_feasible + " (rest of the model is feasible)"
    obj = float(model.c @ x + model.obj_const)
    return LPSolution(status, obj, x, y, d, res.iters, time.perf_counter() - t0, res.log, msg,
                      max_violation(model, x), res.fp64_switch_iter, res.linear_solver, res.start, pres.log,
                      algorithm, certificate, res.gpu_decision)
