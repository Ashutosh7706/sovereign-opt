"""RHS-elastic slack relaxer + plain-English dual-slack attribution.

Given an infeasible model:
 1. Elastic model: every *relaxable* row gets a non-negative slack on its RHS
       <=  :  a'x - n <= b        >=  :  a'x + p >= b        =  :  a'x + p - n = b
    and we minimise  sum_i w_i (p_i + n_i)  with w_i = priority / magnitude, so the
    cheapest *relative* repair wins. Physical balances (meta hard=True) never relax.
 2. Attribution: rows with non-zero slack are the minimum-weight repair; rows with a
    non-zero dual in the elastic LP (integers fixed) form the conflict set, ranked by |y_i|.
 3. Alternatives: for the top conflict rows, re-solve allowing ONLY that row to relax,
    giving single-knob repairs ("either relax the sulfur spec to X, or cut demand to Y").
"""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp

from .bnb import MIPOptions, solve_mip
from .ipm import IPMOptions
from .lp import solve_lp
from .model import Model


def _relaxable(model: Model) -> np.ndarray:
    meta = model.row_meta or [{}] * model.m
    return np.array([not mt.get("hard", False) for mt in meta], bool)


def _row_scale(model: Model, i: int) -> float:
    mt = model.row_meta[i] if model.row_meta else {}
    if mt.get("kind") == "spec":
        return max(abs(mt.get("value", 1.0)), 1e-6) * 50.0  # quality x typical volume
    return 1.0 + abs(mt.get("value", model.rhs[i]))


def build_elastic(model: Model, allowed: np.ndarray):
    rows, cols, vals, names, cost = [], [], [], [], []
    slack_of = []  # (row i, direction +1 for 'p' (raises lhs) / -1 for 'n')
    n0 = model.n
    k = 0
    for i in np.where(allowed)[0]:
        s = model.senses[i]
        mt = model.row_meta[i] if model.row_meta else {}
        w = float(mt.get("weight", 1.0)) / _row_scale(model, i)
        dirs = [+1] if s == "G" else [-1] if s == "L" else [+1, -1]
        for d in dirs:
            rows.append(i); cols.append(k); vals.append(float(d))
            names.append(f"elastic[{model.row_names[i]},{'+' if d > 0 else '-'}]")
            cost.append(w)
            slack_of.append((int(i), d))
            k += 1
    E = sp.csr_matrix((vals, (rows, cols)), shape=(model.m, k))
    A = sp.hstack([model.A, E]).tocsr()
    c = np.concatenate([np.zeros(n0), cost])
    em = Model(model.name + " (elastic)", model.var_names + names, c,
               np.concatenate([model.lb, np.zeros(k)]), np.concatenate([model.ub, np.full(k, np.inf)]),
               np.concatenate([model.integer, np.zeros(k, bool)]), A, model.senses, model.rhs,
               model.row_names, model.row_meta, 0.0, False, model.meta)
    return em, slack_of


def _solve(em: Model, ipm: IPMOptions):
    if em.is_mip:
        r = solve_mip(em, ipm, MIPOptions(node_limit=400, time_limit=30, gap_rel=1e-6))
        return r.status in ("optimal", "feasible"), r.x, r.y, r.objective
    r = solve_lp(em, ipm)
    return r.status == "optimal", r.x, r.y, r.objective


def _clean(v: float) -> float:
    return 0.0 if abs(v) < 1e-6 else float(v)


def _significant(r: dict) -> bool:
    old, new = r.get("old"), r.get("new")
    if old is None or new is None or not np.isfinite(new):
        return True
    return abs(new - old) > 1e-4 * (1 + abs(old))


def _repair_text(model: Model, i: int, delta_lhs: float, x: np.ndarray) -> dict:
    """Translate an elastic slack on row i into a natural-units repair suggestion."""
    mt = model.row_meta[i] if model.row_meta else {}
    s = model.senses[i]
    label = mt.get("label") or model.row_names[i]
    unit = mt.get("unit", "")
    old = mt.get("value")
    if mt.get("kind") == "spec" and mt.get("volume_var") in model.var_names:
        vol = float(x[model.var_names.index(mt["volume_var"])])
        change = delta_lhs / vol if vol > 1e-9 else float("nan")
        new = _clean(old + change if s == "L" else old - change)
        text = f"Relax {label} from {old:g} to {new:.4g} {unit}" if np.isfinite(new) else \
            f"{label} cannot be met at any blend volume"
        return {"row": model.row_names[i], "label": label, "kind": "spec", "old": old, "new": new, "unit": unit,
                "safety": bool(mt.get("safety")), "text": text}
    if old is not None:
        new = _clean(old + delta_lhs if s == "L" else old - delta_lhs)
        if s == "L":
            text = f"Increase {label} from {old:g} to {new:.4g} {unit}"
        else:
            text = f"Reduce {label} from {old:g} to {new:.4g} {unit}"
        return {"row": model.row_names[i], "label": label, "kind": mt.get("kind", "row"), "old": old, "new": new,
                "unit": unit, "safety": bool(mt.get("safety")), "text": text}
    new = model.rhs[i] + (delta_lhs if s == "L" else -delta_lhs)
    return {"row": model.row_names[i], "label": label, "kind": "row", "old": float(model.rhs[i]), "new": float(new),
            "unit": unit, "safety": False, "text": f"Relax constraint '{label}' RHS from {model.rhs[i]:g} to {new:.6g}"}


def explain_infeasibility(model: Model, ipm: IPMOptions | None = None, alternatives: int = 3) -> dict:
    ipm = ipm or IPMOptions(precision="fp64")
    allowed = _relaxable(model)
    em, slack_of = build_elastic(model, allowed)
    ok, x, y, _ = _solve(em, ipm)
    if not ok or x is None:
        hard = [model.row_meta[i].get("desc", model.row_names[i]) for i in range(model.m)
                if model.row_meta and model.row_meta[i].get("hard")]
        return {"repairable": False,
                "summary": "No relaxation of the soft constraints restores feasibility - the conflict lies in "
                           "hard physical balances or variable bounds.", "hard_rows": hard[:10],
                "repairs": [], "conflict": [], "alternatives": []}
    n0 = model.n
    xs, slacks = x[:n0], x[n0:]
    repairs = []
    for (i, d), v in zip(slack_of, slacks):
        if v > 1e-6 * (1 + abs(model.rhs[i])) and v > 1e-7:
            r = _repair_text(model, i, float(v), xs)
            r["amount"] = float(v)
            if _significant(r):
                repairs.append(r)
    # conflict set from elastic duals
    conflict = []
    if y is not None:
        order = np.argsort(-np.abs(y))
        for i in order[:12]:
            if abs(y[i]) < 1e-9:
                break
            mt = model.row_meta[i] if model.row_meta else {}
            conflict.append({"row": model.row_names[i], "label": mt.get("label") or mt.get("desc") or model.row_names[i],
                             "desc": mt.get("desc", model.row_names[i]), "hard": bool(mt.get("hard")),
                             "dual_weight": float(abs(y[i]))})
    # single-knob alternatives
    alts = []
    candidates = [c["row"] for c in conflict if not c["hard"]]
    for r in repairs:
        if r["row"] not in candidates:
            candidates.insert(0, r["row"])
    for rn in candidates[:alternatives + 2]:
        if len(alts) >= alternatives:
            break
        i = model.row_names.index(rn)
        only = np.zeros(model.m, bool)
        only[i] = True
        em1, so1 = build_elastic(model, only)
        ok1, x1, _, _ = _solve(em1, ipm)
        if ok1 and x1 is not None:
            v = float(x1[n0:].sum())
            if v > 1e-7:
                a = _repair_text(model, i, v, x1[:n0])
                a["amount"] = v
                if _significant(a):
                    alts.append(a)
    if repairs:
        summary = "The plan is infeasible. Minimum-change repair: " + "; ".join(r["text"] for r in repairs) + "."
    else:
        summary = "Elastic model found no violated soft constraint (numerical infeasibility)."
    if alts:
        summary += " Single-knob alternatives: " + " OR ".join(a["text"] for a in alts) + "."
    if any(r["safety"] for r in repairs + alts):
        summary += " Note: at least one option relaxes a safety-tagged spec and needs process-safety sign-off."
    return {"repairable": True, "summary": summary, "repairs": repairs, "conflict": conflict,
            "alternatives": alts}
