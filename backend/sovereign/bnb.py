"""Lightweight heuristic Branch-and-Bound for the MVP (Sec. 4.2).

Plain best-first B&B with pseudo-cost branching and a round-and-fix primal heuristic.
Every node LP is solved by our own IPM pipeline. (GNN-guided pruning is Phase-2.)
"""
from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .ipm import IPMOptions
from .lp import LPSolution, max_violation, solve_lp
from .model import Model
from .tolerances import TOL


@dataclass
class MIPOptions:
    gap_rel: float = TOL.mip_gap_rel
    gap_abs: float = TOL.mip_gap_abs
    node_limit: int = 3000
    time_limit: float = 120.0
    int_tol: float = TOL.int_tol
    heuristic_every: int = 15


@dataclass
class MIPResult:
    status: str  # optimal | feasible | infeasible | unbounded | node_limit | time_limit
    objective: float
    bound: float
    x: np.ndarray | None
    y: np.ndarray | None
    nodes: int
    lp_solves: int
    lp_iters: int
    seconds: float
    gap: float
    root: LPSolution | None = None
    history: list = field(default_factory=list)
    message: str = ""
    max_violation: float = float("nan")
    certificate: dict | None = None  # LP-level Farkas certificate or B&B exhaustion proof (audit #43)


def _fractional(x: np.ndarray, ints: np.ndarray, tol: float) -> np.ndarray:
    idx = np.where(ints)[0]
    f = np.abs(x[idx] - np.round(x[idx]))
    return idx[f > tol]


class _PseudoCosts:
    def __init__(self, n):
        self.dn_sum = np.zeros(n); self.dn_cnt = np.zeros(n)
        self.up_sum = np.zeros(n); self.up_cnt = np.zeros(n)

    def update(self, j, direction, delta_obj, frac):
        if not np.isfinite(delta_obj) or frac <= 1e-9:
            return
        per_unit = max(delta_obj, 0.0) / frac
        if direction == "down":
            self.dn_sum[j] += per_unit; self.dn_cnt[j] += 1
        else:
            self.up_sum[j] += per_unit; self.up_cnt[j] += 1

    def score(self, j, xj):
        f = xj - math.floor(xj)
        known_dn = self.dn_sum[self.dn_cnt > 0] / self.dn_cnt[self.dn_cnt > 0]
        known_up = self.up_sum[self.up_cnt > 0] / self.up_cnt[self.up_cnt > 0]
        avg_dn = known_dn.mean() if len(known_dn) else 1.0
        avg_up = known_up.mean() if len(known_up) else 1.0
        pd = self.dn_sum[j] / self.dn_cnt[j] if self.dn_cnt[j] else avg_dn
        pu = self.up_sum[j] / self.up_cnt[j] if self.up_cnt[j] else avg_up
        return max(pd * f, 1e-6) * max(pu * (1 - f), 1e-6)


def solve_mip(model: Model, ipm: IPMOptions | None = None, opts: MIPOptions | None = None,
              callback: Callable[[dict], None] | None = None,
              root_callback: Callable[[dict], None] | None = None) -> MIPResult:
    ipm = ipm or IPMOptions()
    opts = opts or MIPOptions()
    t0 = time.perf_counter()
    ints = model.integer
    relaxed = model.relaxed()
    stats = {"lp": 0, "iters": 0, "unresolved": 0}
    robust = IPMOptions(tol=ipm.tol, max_iter=max(300, ipm.max_iter), precision="fp64",
                        use_gpu=ipm.use_gpu, dense_threshold=ipm.dense_threshold)

    def lp(lb, ub, cb=None) -> LPSolution:
        stats["lp"] += 1
        s = solve_lp(relaxed.with_bounds(lb, ub), ipm, cb)
        stats["iters"] += s.iters
        if s.status not in ("optimal", "infeasible", "unbounded"):
            # never prune on a numerical failure: retry in FP64 with a longer iteration budget
            stats["lp"] += 1
            s = solve_lp(relaxed.with_bounds(lb, ub), robust)
            stats["iters"] += s.iters
            if s.status not in ("optimal", "infeasible", "unbounded"):
                stats["unresolved"] += 1
        return s

    root = lp(model.lb.copy(), model.ub.copy(), root_callback)
    if root.status in ("infeasible", "unbounded"):
        cert = None
        if root.status == "infeasible":
            cert = {"type": "farkas (LP relaxation)", **root.certificate} if root.certificate else                 {"type": "phase-1 elastic LP (LP relaxation)", "verified": True}
        return MIPResult(root.status, math.nan, math.nan, None, None, 0, stats["lp"], stats["iters"],
                         time.perf_counter() - t0, math.inf, root, [], root.message, certificate=cert)
    if root.status != "optimal":
        return MIPResult("numerical_error", math.nan, math.nan, None, None, 0, stats["lp"], stats["iters"],
                         time.perf_counter() - t0, math.inf, root, [], root.message)

    inc_obj, inc_x = math.inf, None
    history = []
    pc = _PseudoCosts(model.n)
    counter = 0
    heap: list = []

    tree_ids = iter(range(1, 10 ** 9))

    def tree(tid, parent, state, label, bound):
        """Live search-tree events for the dashboard (not stored in history)."""
        if callback:
            callback({"type": "tree", "id": tid, "parent": parent, "state": state, "label": label,
                      "bound": bound, "t": time.perf_counter() - t0})

    def emit(kind, node_id, bound, depth, extra=None, live=None):
        glob = min([h[0] for h in heap] + [bound]) if heap else bound
        glob = min(glob, inc_obj)
        gap = (inc_obj - glob) / max(1.0, abs(inc_obj)) if math.isfinite(inc_obj) else math.inf
        ev = {"type": kind, "node": node_id, "bound": glob, "incumbent": inc_obj if math.isfinite(inc_obj) else None,
              "gap": gap if math.isfinite(gap) else None, "depth": depth, "open": len(heap),
              "t": time.perf_counter() - t0, "lp_solves": stats["lp"]}
        if extra:
            ev.update(extra)
        history.append(ev)
        if callback:
            callback({**ev, **live} if live else ev)

    def try_incumbent(x, source):
        nonlocal inc_obj, inc_x
        xr = x.copy()
        xr[ints] = np.round(xr[ints])
        if max_violation(model, xr) > TOL.incumbent_violation * (1 + np.max(np.abs(model.rhs), initial=1.0)):
            return False
        obj = float(model.c @ xr + model.obj_const)
        if obj < inc_obj - 1e-9:
            inc_obj, inc_x = obj, xr
            emit("incumbent", -1, inc_obj, 0, {"source": source}, {"x": xr})
            return True
        return False

    def round_and_fix(sol: LPSolution, lb, ub):
        lb2, ub2 = lb.copy(), ub.copy()
        r = np.clip(np.round(sol.x[ints]), lb[ints], ub[ints])
        lb2[ints] = r; ub2[ints] = r
        s = lp(lb2, ub2)
        if s.status == "optimal":
            try_incumbent(s.x, "round-and-fix")

    def push(sol: LPSolution, lb, ub, depth, tid=0):
        nonlocal counter
        counter += 1
        heapq.heappush(heap, (sol.objective, counter, depth, lb, ub, sol, tid))

    frac = _fractional(root.x, ints, opts.int_tol)
    if len(frac) == 0:
        tree(0, None, "integral", "root LP", root.objective)
        try_incumbent(root.x, "root LP")
    else:
        tree(0, None, "open", "root LP", root.objective)
        round_and_fix(root, model.lb, model.ub)
        push(root, model.lb.copy(), model.ub.copy(), 0, 0)
    emit("root", 0, root.objective, 0)

    nodes = 0
    status = "optimal"
    while heap:
        if nodes >= opts.node_limit:
            status = "node_limit"; break
        if time.perf_counter() - t0 > opts.time_limit:
            status = "time_limit"; break
        bound, _, depth, lb, ub, sol, tid = heapq.heappop(heap)
        if bound >= inc_obj - max(opts.gap_abs, opts.gap_rel * max(1.0, abs(inc_obj))):
            tree(tid, None, "pruned", "", bound)
            continue  # pruned by bound
        nodes += 1
        frac = _fractional(sol.x, ints, opts.int_tol)
        if len(frac) == 0:
            tree(tid, None, "integral", "", bound)
            try_incumbent(sol.x, "integral node")
            continue
        tree(tid, None, "branched", "", bound)
        j = max(frac, key=lambda k: pc.score(k, sol.x[k]))
        xj = sol.x[j]
        f = xj - math.floor(xj)
        for direction in ("down", "up"):
            lb2, ub2 = lb.copy(), ub.copy()
            if direction == "down":
                ub2[j] = math.floor(xj)
            else:
                lb2[j] = math.ceil(xj)
            child = lp(lb2, ub2)
            cid = next(tree_ids)
            label = f"{model.var_names[j]} {'<=' if direction == 'down' else '>='} " \
                    f"{math.floor(xj) if direction == 'down' else math.ceil(xj)}"
            if child.status != "optimal":
                tree(cid, tid, "infeasible", label, None)
                continue
            pc.update(j, direction, child.objective - sol.objective, f if direction == "down" else 1 - f)
            if child.objective >= inc_obj - opts.gap_abs:
                tree(cid, tid, "pruned", label, child.objective)
                continue
            cfrac = _fractional(child.x, ints, opts.int_tol)
            if len(cfrac) == 0:
                tree(cid, tid, "integral", label, child.objective)
                try_incumbent(child.x, "integral node")
            else:
                tree(cid, tid, "open", label, child.objective)
                push(child, lb2, ub2, depth + 1, cid)
        if nodes % opts.heuristic_every == 0:
            round_and_fix(sol, lb, ub)
        emit("node", nodes, bound, depth, {"branch_var": model.var_names[j], "value": float(xj)})
        # global gap check
        if heap and math.isfinite(inc_obj):
            glob = min(h[0] for h in heap)
            if inc_obj - glob <= max(opts.gap_abs, opts.gap_rel * max(1.0, abs(inc_obj))):
                break

    glob = min([h[0] for h in heap] + [inc_obj]) if heap else inc_obj
    unresolved = stats["unresolved"]
    if inc_x is None:
        st = "infeasible" if status == "optimal" else status
        msg = "no integer-feasible point exists" if st == "infeasible" else ""
        cert = None
        if st == "infeasible":
            cert = {"type": "branch-and-bound exhaustion", "verified": True, "nodes": nodes,
                    "lp_solves": stats["lp"],
                    "detail": f"LP relaxation feasible, but all {nodes} explored nodes ended infeasible or "
                              f"bound-pruned with no incumbent; no node LP failed numerically"}
            msg += f" (proof: exhaustive search of {nodes} nodes)"
        if unresolved and st == "infeasible":
            st, msg = "unknown", f"{unresolved} node LP(s) failed numerically - infeasibility NOT proven"
        return MIPResult(st, math.nan, glob, None, None, nodes, stats["lp"], stats["iters"],
                         time.perf_counter() - t0, math.inf, root, history, msg, certificate=cert)
    gap = (inc_obj - glob) / max(1.0, abs(inc_obj))
    if status != "optimal":
        status = "feasible" if gap > opts.gap_rel else "optimal"
    msg = ""
    if unresolved and status == "optimal":
        status, msg = "feasible", f"{unresolved} node LP(s) failed numerically - optimality NOT proven"
    # duals of the LP with integer decisions fixed (used for explanations / sensitivities)
    lbf, ubf = model.lb.copy(), model.ub.copy()
    lbf[ints] = inc_x[ints]; ubf[ints] = inc_x[ints]
    fixed = lp(lbf, ubf)
    x = inc_x
    if fixed.status == "optimal" and fixed.objective <= inc_obj + 1e-7 * (1 + abs(inc_obj)):
        x = fixed.x.copy(); x[ints] = inc_x[ints]
        inc_obj = min(inc_obj, float(model.c @ x + model.obj_const))
    emit("done", nodes, glob, 0)
    return MIPResult(status, inc_obj, glob, x, fixed.y if fixed.status == "optimal" else None, nodes,
                     stats["lp"], stats["iters"], time.perf_counter() - t0, max(gap, 0.0), root, history,
                     msg, max_violation(model, x))
