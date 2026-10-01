"""Fair Benchmarking Protocol (Sec. 5): every race shows all three baselines.

  1. HiGHS          - open-source 'free alternative' (via SciPy; never shown alone)
  2. Gurobi CPU     - multithreaded incumbent (needs gurobipy + licence)
  3. Gurobi GPU     - Gurobi's GPU/PDHG mode (needs a Gurobi build with GPU support + CUDA)

A baseline that is not installed is reported as `unavailable` with the reason - it is
never silently dropped from the comparison.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
import scipy.sparse as sp
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

from .model import Model

# Gurobi GPU parameters differ by release; override without code changes, e.g.
#   GUROBI_GPU_PARAMS='{"Method": 6, "PDHGGPU": 1}'
# Check the parameter names against the Gurobi reference manual for your version.
DEFAULT_GPU_PARAMS = {"Method": 6, "PDHGGPU": 1}


def _row_bounds(model: Model):
    lo = np.where(model.senses == "G", model.rhs, -np.inf)
    hi = np.where(model.senses == "L", model.rhs, np.inf)
    eq = model.senses == "E"
    lo[eq] = model.rhs[eq]
    hi[eq] = model.rhs[eq]
    return lo, hi


def _linprog(model: Model, method: str, time_limit: float):
    A = model.A.tocsr()
    L, G, E = model.senses == "L", model.senses == "G", model.senses == "E"
    A_ub = sp.vstack([A[L], -A[G]]).tocsr()
    b_ub = np.concatenate([model.rhs[L], -model.rhs[G]])
    bounds = [(None if not np.isfinite(lo) else lo, None if not np.isfinite(hi) else hi)
              for lo, hi in zip(model.lb, model.ub)]
    return linprog(model.c, A_ub=A_ub if A_ub.shape[0] else None, b_ub=b_ub if A_ub.shape[0] else None,
                   A_eq=A[E] if E.any() else None, b_eq=model.rhs[E] if E.any() else None,
                   bounds=bounds, method=method, options={"time_limit": time_limit})


def run_highs(model: Model, time_limit: float = 120) -> dict:
    """HiGHS as it chooses by default: MIP solver for integer models; for LPs HiGHS picks its own
    algorithm (usually dual simplex)."""
    t0 = time.perf_counter()
    if model.is_mip:
        lo, hi = _row_bounds(model)
        r = milp(model.c, constraints=LinearConstraint(model.A, lo, hi),
                 integrality=model.integer.astype(int), bounds=Bounds(model.lb, model.ub),
                 options={"time_limit": time_limit, "mip_rel_gap": 1e-4})
    else:
        r = _linprog(model, "highs", time_limit)
    dt = time.perf_counter() - t0
    status = {0: "optimal", 1: "time_limit", 2: "infeasible", 3: "unbounded", 4: "error"}.get(r.status, "error")
    obj = float(r.fun + model.obj_const) if r.x is not None and r.fun is not None else None
    return {"lane": "highs", "name": "HiGHS (default)", "status": status, "seconds": dt,
            "objective": model.display_objective(obj) if obj is not None else None,
            "note": "open-source; HiGHS chooses its algorithm" + ("" if model.is_mip else " (simplex for most LPs)")}


def run_highs_ipm(model: Model, time_limit: float = 120) -> dict:
    """HiGHS forced onto its own interior-point method - the like-for-like comparison for an IPM."""
    ipm_model = model
    t0 = time.perf_counter()
    r = _linprog(ipm_model, "highs-ipm", time_limit)
    dt = time.perf_counter() - t0
    status = {0: "optimal", 1: "time_limit", 2: "infeasible", 3: "unbounded", 4: "error"}.get(r.status, "error")
    obj = float(r.fun + model.obj_const) if r.x is not None and r.fun is not None else None
    return {"lane": "highs_ipm", "name": "HiGHS (IPM)", "status": status, "seconds": dt,
            "objective": model.display_objective(obj) if obj is not None else None,
            "note": "HiGHS interior-point + crossover: the like-for-like IPM comparison"}


def _gurobi_model(model: Model, gp, GRB):
    m = gp.Model()
    m.Params.OutputFlag = 0
    vt = [GRB.INTEGER if i else GRB.CONTINUOUS for i in model.integer]
    x = m.addMVar(model.n, lb=model.lb, ub=model.ub, obj=model.c, vtype=vt)
    lo, hi = _row_bounds(model)
    A = model.A.tocsr()
    for s, mask in (("L", model.senses == "L"), ("G", model.senses == "G"), ("E", model.senses == "E")):
        if mask.any():
            sub = A[mask]
            if s == "L":
                m.addConstr(sub @ x <= hi[mask])
            elif s == "G":
                m.addConstr(sub @ x >= lo[mask])
            else:
                m.addConstr(sub @ x == lo[mask])
    m.ObjCon = model.obj_const
    return m


def run_gurobi(model: Model, gpu: bool, time_limit: float = 120) -> dict:
    lane = "gurobi_gpu" if gpu else "gurobi_cpu"
    name = "Gurobi (GPU mode)" if gpu else f"Gurobi (CPU, {os.cpu_count()} threads)"
    try:
        import gurobipy as gp
        from gurobipy import GRB
    except ImportError:
        return {"lane": lane, "name": name, "status": "unavailable", "seconds": None, "objective": None,
                "note": "gurobipy not installed / no licence on this machine"}
    try:
        m = _gurobi_model(model, gp, GRB)
        m.Params.TimeLimit = time_limit
        m.Params.MIPGap = 1e-4
        if gpu:
            params = json.loads(os.environ.get("GUROBI_GPU_PARAMS", json.dumps(DEFAULT_GPU_PARAMS)))
            for k, v in params.items():
                m.setParam(k, v)
        else:
            m.Params.Threads = os.cpu_count() or 1
        t0 = time.perf_counter()
        m.optimize()
        dt = time.perf_counter() - t0
        st = {GRB.OPTIMAL: "optimal", GRB.INFEASIBLE: "infeasible", GRB.UNBOUNDED: "unbounded",
              GRB.TIME_LIMIT: "time_limit"}.get(m.Status, f"status_{m.Status}")
        obj = float(m.ObjVal) if m.SolCount > 0 else None
        return {"lane": lane, "name": name, "status": st, "seconds": dt,
                "objective": model.display_objective(obj) if obj is not None else None,
                "note": "GPU params: " + os.environ.get("GUROBI_GPU_PARAMS", json.dumps(DEFAULT_GPU_PARAMS)) if gpu else ""}
    except Exception as e:  # licence errors, unknown GPU params, no CUDA device ...
        return {"lane": lane, "name": name, "status": "unavailable", "seconds": None, "objective": None,
                "note": f"{type(e).__name__}: {e}"[:300]}


def availability() -> dict:
    try:
        import gurobipy  # noqa: F401
        g = True
    except ImportError:
        g = False
    return {"highs": True, "highs_ipm": True, "gurobi_cpu": g, "gurobi_gpu": g}
