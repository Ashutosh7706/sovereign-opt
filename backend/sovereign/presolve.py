"""Presolve, standard-form conversion, Ruiz equilibration and Postsolve (Sec. 12.1).

Forward pipeline (CPU, strictly FP64):
    original Model --presolve--> reduced Model --to_standard_form--> (A, b, c) with x >= 0
    --ruiz_scale--> A_s = D_r A D_c,  b_s = D_r b / beta,  c_s = D_c c / gamma

Reverse pipeline (Postsolve / De-Scaling Engine), in this exact order:
    1. de-scale:     x = beta * D_c x~,   y = gamma * D_r y~,   s = gamma * D_c^-1 s~
    2. std -> model: x_model = shift + T x_std  (undo bound shifts / free splits), drop slacks
    3. re-inflate:   presolve-eliminated columns restored to their fixed / implied values,
                     eliminated rows get zero duals, reduced costs d = c - A'y recomputed
                     against the ORIGINAL (unscaled) A, b, c.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .model import INF, Model

from .tolerances import TOL

FEAS_TOL = TOL.presolve_feas


# --------------------------------------------------------------------------- presolve
@dataclass
class PresolveResult:
    status: str  # 'ok' | 'infeasible' | 'unbounded'
    message: str
    reduced: Model | None
    kept_cols: np.ndarray
    kept_rows: np.ndarray
    fixed_values: dict[int, float] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    # an empty column whose cost is unbounded makes the LP unbounded ONLY IF the rest is
    # feasible - so it is fixed at a finite value and the verdict is deferred to the solve
    unbounded_if_feasible: str | None = None
    # singleton rows turned into bounds (audit #41): (row, col, coef); bound_src[j] = {"lb": row, "ub": row}
    singletons: list = field(default_factory=list)
    bound_src: dict = field(default_factory=dict)
    lb_final: np.ndarray | None = None
    ub_final: np.ndarray | None = None


def presolve(model: Model) -> PresolveResult:
    n, m = model.n, model.m
    lb, ub = model.lb.copy(), model.ub.copy()
    ints = model.integer
    # integer bound rounding
    lb[ints] = np.ceil(lb[ints] - 1e-9)
    ub[ints] = np.floor(ub[ints] + 1e-9)
    log: list[str] = []
    bad = np.where(lb > ub + FEAS_TOL)[0]
    empty_idx = np.array([], dtype=int)
    if len(bad):
        j = int(bad[0])
        return PresolveResult("infeasible", f"variable '{model.var_names[j]}' has lb {lb[j]:g} > ub {ub[j]:g}",
                              None, empty_idx, empty_idx)

    A = model.A.tocsc()
    rhs = model.rhs.astype(float).copy()
    col_active = np.ones(n, bool)
    row_active = np.ones(m, bool)
    fixed: dict[int, float] = {}
    unbounded_note = None
    singletons: list = []
    bound_src: dict = {}

    def fix(j: int, v: float) -> None:
        start, end = A.indptr[j], A.indptr[j + 1]
        rhs[A.indices[start:end]] -= A.data[start:end] * v
        col_active[j] = False
        fixed[j] = float(v)

    changed = True
    passes = 0
    while changed and passes < 50:
        changed = False
        passes += 1
        # (a) fixed columns
        for j in np.where(col_active & (np.abs(ub - lb) <= 1e-12))[0]:
            fix(int(j), lb[j])
            changed = True
        # (b) empty columns (w.r.t. active rows)
        col_nnz = np.asarray((A[np.where(row_active)[0]] != 0).sum(axis=0)).ravel()
        for j in np.where(col_active & (col_nnz == 0))[0]:
            cj = model.c[j]
            if cj > 0:
                v = lb[j]
            elif cj < 0:
                v = ub[j]
            else:
                v = min(max(0.0, lb[j]), ub[j])
            if not np.isfinite(v):
                unbounded_note = unbounded_note or (f"variable '{model.var_names[j]}' appears in no constraint and "
                                                    f"its cost drives it to {'-' if cj > 0 else '+'}infinity")
                v = min(max(0.0, lb[j]), ub[j]) if np.isfinite(min(max(0.0, lb[j]), ub[j])) else 0.0
            fix(int(j), v)
            changed = True
        # (c) empty rows
        Ar = A.tocsr()
        row_nnz = np.asarray((Ar[:, col_active] != 0).sum(axis=1)).ravel()
        for i in np.where(row_active & (row_nnz == 0))[0]:
            s, r = model.senses[i], rhs[i]
            tol = 1e-7 * (1 + abs(model.rhs[i]))
            ok = (s == "L" and r >= -tol) or (s == "G" and r <= tol) or (s == "E" and abs(r) <= tol)
            if not ok:
                return PresolveResult("infeasible", f"constraint '{model.row_names[i]}' reduces to 0 {s} {r:g} after "
                                      f"fixing variables", None, empty_idx, empty_idx)
            row_active[i] = False
            changed = True
        # (d) singleton rows -> variable bounds (Andersen & Andersen 1995); duals restored in postsolve
        for i in np.where(row_active & (row_nnz == 1))[0]:
            st, en = Ar.indptr[i], Ar.indptr[i + 1]
            cols, vals = Ar.indices[st:en], Ar.data[st:en]
            mask = col_active[cols] & (vals != 0)
            if mask.sum() != 1:
                continue
            j, a = int(cols[mask][0]), float(vals[mask][0])
            v = rhs[i] / a
            sense = model.senses[i]
            upper = sense == "E" or (sense == "L" and a > 0) or (sense == "G" and a < 0)
            lower = sense == "E" or (sense == "L" and a < 0) or (sense == "G" and a > 0)
            src = bound_src.setdefault(j, {})
            if upper:
                vu = np.floor(v + 1e-9) if ints[j] else v
                if vu < ub[j]:
                    ub[j] = vu
                    src["ub"] = int(i)
            if lower:
                vl = np.ceil(v - 1e-9) if ints[j] else v
                if vl > lb[j]:
                    lb[j] = vl
                    src["lb"] = int(i)
            if lb[j] > ub[j]:
                if lb[j] - ub[j] <= 1e-9 * (1 + abs(v)):
                    ub[j] = lb[j]
                else:
                    return PresolveResult("infeasible", f"constraint '{model.row_names[i]}' forces "
                                          f"'{model.var_names[j]}' outside its bounds [{lb[j]:g}, {ub[j]:g}]",
                                          None, empty_idx, empty_idx)
            singletons.append((int(i), j, a))
            row_active[i] = False
            changed = True

    kept_cols = np.where(col_active)[0]
    kept_rows = np.where(row_active)[0]
    log.append(f"presolve: removed {n - len(kept_cols)} cols ({len(fixed)} fixed/empty), "
               f"{m - len(kept_rows)} rows ({len(singletons)} singleton -> bound) in {passes} passes")
    obj_const = model.obj_const + float(sum(model.c[j] * v for j, v in fixed.items()))
    red = Model(
        name=model.name, var_names=[model.var_names[j] for j in kept_cols],
        c=model.c[kept_cols], lb=lb[kept_cols], ub=ub[kept_cols], integer=ints[kept_cols],
        A=model.A.tocsr()[kept_rows][:, kept_cols].tocsr(), senses=model.senses[kept_rows],
        rhs=rhs[kept_rows], row_names=[model.row_names[i] for i in kept_rows],
        row_meta=[model.row_meta[i] for i in kept_rows] if model.row_meta else [],
        obj_const=obj_const, maximize=model.maximize, meta=model.meta,
    )
    return PresolveResult("ok", "ok", red, kept_cols, kept_rows, fixed, log, unbounded_note,
                          singletons, bound_src, lb, ub)


def postsolve(pres: PresolveResult, original: Model, x_red: np.ndarray, y_red: np.ndarray):
    """Re-inflate presolve-eliminated columns/rows; reduced costs vs ORIGINAL A, c."""
    x = np.zeros(original.n)
    x[pres.kept_cols] = x_red
    for j, v in pres.fixed_values.items():
        x[j] = v
    y = np.zeros(original.m)
    y[pres.kept_rows] = y_red
    if pres.singletons:
        # a singleton row whose bound is active at the solution carries the reduced cost of its
        # variable:  y_i = (c_j - sum_{k != i} a_kj y_k) / a_ij   (reverse elimination order)
        Ac = original.A.tocsc()
        for i, j, a in reversed(pres.singletons):
            src = pres.bound_src.get(j, {})
            tol = 1e-7 * (1 + abs(x[j]))
            active = (src.get("ub") == i and abs(x[j] - pres.ub_final[j]) <= tol) or                      (src.get("lb") == i and abs(x[j] - pres.lb_final[j]) <= tol)
            if active:
                col = Ac.getcol(j)
                y[i] = 0.0
                y[i] = (original.c[j] - float((col.T @ y)[0])) / a
    d = original.c - original.A.T @ y
    return x, y, d


# --------------------------------------------------------------------------- standard form
@dataclass
class StdForm:
    A: sp.csr_matrix
    b: np.ndarray
    c: np.ndarray
    u: np.ndarray  # upper bounds of the standard-form columns (inf = none); handled inside the IPM
    obj_const: float
    T: sp.csr_matrix  # n_model x n_struct
    shift: np.ndarray
    m_rows: int
    n_struct: int


def to_standard_form(model: Model) -> StdForm:
    """min c'x, Ax = b, 0 <= x <= u. Lower bounds are shifted out, x <= ub stays an upper
    bound (no extra rows), -inf..ub columns are mirrored, free columns are split x+ - x-."""
    n, m = model.n, model.m
    lb, ub = model.lb, model.ub
    T_rows, T_cols, T_vals = [], [], []
    shift = np.zeros(n)
    ucol = []
    k = 0
    for j in range(n):
        lo, hi = lb[j], ub[j]
        if np.isfinite(lo):
            T_rows.append(j); T_cols.append(k); T_vals.append(1.0)
            shift[j] = lo
            ucol.append(hi - lo)
            k += 1
        elif np.isfinite(hi):
            T_rows.append(j); T_cols.append(k); T_vals.append(-1.0)
            shift[j] = hi
            ucol.append(np.inf)
            k += 1
        else:  # free: x = x+ - x-
            T_rows += [j, j]; T_cols += [k, k + 1]; T_vals += [1.0, -1.0]
            ucol += [np.inf, np.inf]
            k += 2
    n_struct = k
    T = sp.csr_matrix((T_vals, (T_rows, T_cols)), shape=(n, n_struct))
    AT = (model.A @ T).tocsr()
    b = model.rhs - model.A @ shift
    slack_rows = [i for i in range(m) if model.senses[i] != "E"]
    ns = len(slack_rows)
    S = sp.csr_matrix(([1.0 if model.senses[i] == "L" else -1.0 for i in slack_rows],
                       (slack_rows, np.arange(ns))), shape=(m, ns))
    A_std = sp.hstack([AT, S]).tocsr()
    c_std = np.concatenate([T.T @ model.c, np.zeros(ns)])
    u_std = np.concatenate([np.array(ucol, float), np.full(ns, np.inf)])
    obj_const = model.obj_const + float(model.c @ shift)
    return StdForm(A_std, b, c_std, u_std, obj_const, T, shift, m, n_struct)


def from_standard_form(std: StdForm, x_std: np.ndarray, y_std: np.ndarray):
    x = std.shift + std.T @ x_std[: std.n_struct]
    y = y_std[: std.m_rows]
    return x, y


# --------------------------------------------------------------------------- Ruiz scaling
@dataclass
class Scaling:
    Dr: np.ndarray
    Dc: np.ndarray
    beta: float  # rhs scale
    gamma: float  # cost scale

    def unscale(self, xs, ys, ss, ws=None, zs=None):
        """x* = beta D_c x~*,  y* = gamma D_r y~*,  s* = gamma D_c^-1 s~*
        (upper-bound slack w scales like x, its dual z like s)."""
        out = (self.beta * self.Dc * xs, self.gamma * self.Dr * ys, self.gamma * ss / self.Dc)
        if ws is not None:
            out += (self.beta * self.Dc * ws, self.gamma * zs / self.Dc)
        return out

    def scale_point(self, x, y, s, w, z):
        return (x / (self.beta * self.Dc), y / (self.gamma * self.Dr), s * self.Dc / self.gamma,
                w / (self.beta * self.Dc), z * self.Dc / self.gamma)

    def scale_upper(self, u):
        return u / (self.beta * self.Dc)


def curtis_reid(A: sp.csr_matrix) -> tuple[np.ndarray, np.ndarray]:
    """Curtis-Reid geometric scaling: choose log-scales rho_i, gamma_j minimising
    sum_ij (log2|a_ij| + rho_i + gamma_j)^2 (sparse least squares). Its optimum is invariant
    to any prior diagonal row/column scaling, which the max-norm Ruiz pass alone is not
    (audit #42: rows spanning 10 orders of magnitude)."""
    m, n = A.shape
    coo = A.tocoo()
    keep = coo.data != 0
    r, c, v = coo.row[keep], coo.col[keep], np.log2(np.abs(coo.data[keep]))
    k = len(v)
    if k == 0:
        return np.ones(m), np.ones(n)
    E = sp.csr_matrix((np.ones(2 * k), (np.r_[np.arange(k), np.arange(k)], np.r_[r, m + c])), shape=(k, m + n))
    sol = spla.lsqr(E, -v, atol=1e-10, btol=1e-10, iter_lim=max(200, 4 * (m + n)))[0]
    rho, gam = sol[:m], sol[m:]
    shift = np.mean(gam) if n else 0.0  # the fit is defined up to rho + t, gamma - t
    rho, gam = rho + shift, gam - shift
    return 2.0 ** np.round(rho), 2.0 ** np.round(gam)  # powers of two: scaling is exact in binary FP


def ruiz_scale(A: sp.csr_matrix, b: np.ndarray, c: np.ndarray, iters: int = 25, tol: float = 1e-3):
    """Geometric (Curtis-Reid) scaling followed by Ruiz max-norm equilibration."""
    m, n = A.shape
    Dr, Dc = curtis_reid(A)
    coo = A.tocoo()
    row, col, val = coo.row, coo.col, np.abs(coo.data.astype(float))
    for _ in range(iters):
        v = val * Dr[row] * Dc[col]
        rmax, cmax = np.zeros(m), np.zeros(n)
        np.maximum.at(rmax, row, v)
        np.maximum.at(cmax, col, v)
        rmax[rmax == 0] = 1.0
        cmax[cmax == 0] = 1.0
        if max(np.max(np.abs(1 - rmax), initial=0), np.max(np.abs(1 - cmax), initial=0)) < tol:
            break
        Dr /= np.sqrt(rmax)
        Dc /= np.sqrt(cmax)
    As = (sp.diags(Dr) @ A @ sp.diags(Dc)).tocsr()
    bs, cs = Dr * b, Dc * c
    beta = max(1.0, float(np.max(np.abs(bs)))) if len(bs) else 1.0
    gamma = max(1.0, float(np.max(np.abs(cs)))) if len(cs) else 1.0
    return As, bs / beta, cs / gamma, Scaling(Dr, Dc, beta, gamma)
