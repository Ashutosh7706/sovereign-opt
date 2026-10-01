import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from sovereign.baselines import run_highs
from sovereign.bnb import solve_mip
from sovereign.engine import solve
from sovereign.infeasibility import explain_infeasibility
from sovereign.models.refinery import build, default_params


def milp_ref(M):
    lo = np.where(M.senses == "G", M.rhs, -np.inf)
    hi = np.where(M.senses == "L", M.rhs, np.inf)
    eq = M.senses == "E"
    lo[eq] = hi[eq] = M.rhs[eq]
    return milp(M.c, constraints=LinearConstraint(M.A, lo, hi), integrality=M.integer.astype(int),
                bounds=Bounds(M.lb, M.ub))


def test_refinery_mip_matches_highs():
    M = build()
    r = solve_mip(M)
    ref = milp_ref(M)
    assert r.status == "optimal"
    assert abs(r.objective - ref.fun) <= 1e-5 * (1 + abs(ref.fun))
    assert r.max_violation < 1e-6
    assert np.allclose(r.x[M.integer], np.round(r.x[M.integer]))


def test_perturbed_refinery_mips_match_highs():
    rng = np.random.default_rng(0)
    for seed in range(6):
        p = default_params()
        for pr in p.products.values():  # different prices -> different branching trees
            pr.price *= rng.uniform(0.9, 1.1)
        for c in p.crudes:
            c.price *= rng.uniform(0.95, 1.05)
        M = build(p)
        r = solve_mip(M)
        ref = milp_ref(M)
        assert (r.status == "optimal") == (ref.status == 0)
        if ref.status == 0:
            assert abs(r.objective - ref.fun) <= 1e-4 * (1 + abs(ref.fun)), seed


def test_infeasible_plan_is_explained_and_repair_works():
    p = default_params()
    for s in p.specs:
        if s.product == "VLSFO":
            s.value = 0.05
    p.products["VLSFO"].min_demand = 45
    M = build(p)
    assert solve(M)["status"] == "infeasible"
    exp = explain_infeasibility(M)
    assert exp["repairable"]
    alt = next(a for a in exp["alternatives"] if a["row"] == "spec[VLSFO,sulfur,max]")
    assert alt["safety"]
    for s in p.specs:  # apply the suggested repair (+2% margin off the exact boundary) -> feasible
        if s.product == "VLSFO":
            s.value = alt["new"] * 1.02
    assert solve(build(p))["status"] == "optimal"


def test_highs_baseline_agrees():
    M = build()
    b = run_highs(M)
    r = solve(M)
    assert b["status"] == "optimal"
    assert abs(b["objective"] - r["objective"]) <= 1e-5 * (1 + abs(r["objective"]))


def test_never_false_infeasible_near_degenerate_boundary():
    # near the exact feasibility boundary the IPM can fail on node LPs; B&B must then say
    # "not proven" instead of pruning the node and wrongly reporting infeasible
    p = default_params()
    p.products["VLSFO"].min_demand = 45
    for v in (0.1218, 0.12179, 0.122):
        for s in p.specs:
            if s.product == "VLSFO":
                s.value = v
        M = build(p)
        assert milp_ref(M).status == 0
        r = solve_mip(M)
        assert r.status in ("optimal", "feasible", "unknown"), (v, r.status)
        if r.x is not None:
            assert abs(r.objective - milp_ref(M).fun) <= 1e-4 * (1 + abs(r.objective))
