"""IPM + presolve/postsolve correctness against HiGHS as an independent oracle."""
import numpy as np
import pytest
import scipy.sparse as sp
from scipy.optimize import linprog

from sovereign.ipm import IPMOptions, lms_start
from sovereign.linalg import NormalSolver
from sovereign.lp import WarmStartCache, solve_lp
from sovereign.model import Model
from sovereign.models.random_lp import random_lp
from sovereign.presolve import presolve, ruiz_scale, to_standard_form


def random_general_lp(m, n, seed, free_frac=0.05):
    r = np.random.default_rng(seed)
    A = sp.random(m, n, density=0.2, random_state=seed, format="csr") * 10
    x0 = r.uniform(0, 5, n)
    lb = np.where(r.random(n) < 0.1, -np.inf, 0.0)
    ub = np.where(r.random(n) < 0.4, x0 + r.uniform(0, 3, n), np.inf)
    free = r.random(n) < free_frac
    lb[free], ub[free] = -np.inf, np.inf
    senses = r.choice(["L", "G", "E"], m, p=[.5, .3, .2])
    ax = A @ x0
    rhs = np.where(senses == "L", ax + r.uniform(0, 2, m), np.where(senses == "G", ax - r.uniform(0, 2, m), ax))
    return Model("r", [f"x{j}" for j in range(n)], r.normal(size=n), lb, ub, np.zeros(n, bool), A,
                 np.array(senses, dtype="<U1"), rhs, [f"r{i}" for i in range(m)], [{}] * m)


def highs(M):
    A = M.A.toarray()
    ub_rows = [(A[i], M.rhs[i]) if s == "L" else (-A[i], -M.rhs[i]) for i, s in enumerate(M.senses) if s != "E"]
    eq_rows = [(A[i], M.rhs[i]) for i, s in enumerate(M.senses) if s == "E"]
    bounds = [(None if not np.isfinite(l) else l, None if not np.isfinite(u) else u) for l, u in zip(M.lb, M.ub)]
    return linprog(M.c, A_ub=np.array([r[0] for r in ub_rows]) if ub_rows else None,
                   b_ub=[r[1] for r in ub_rows] or None,
                   A_eq=np.array([r[0] for r in eq_rows]) if eq_rows else None,
                   b_eq=[r[1] for r in eq_rows] or None, bounds=bounds, method="highs")


@pytest.mark.parametrize("seed", range(20))
@pytest.mark.parametrize("precision", ["fp64", "mixed"])
def test_matches_highs_status_and_objective(seed, precision):
    M = random_general_lp(30, 50, seed)
    ref = highs(M)
    s = solve_lp(M, IPMOptions(precision=precision))
    expected = {0: "optimal", 2: "infeasible", 3: "unbounded"}[ref.status]
    assert s.status == expected
    if expected == "optimal":
        assert abs(s.objective - ref.fun) <= 1e-6 * (1 + abs(ref.fun))
        assert s.max_violation < 1e-5


def test_sparse_lu_path_matches_dense():
    M = random_lp(300, 500, seed=3)
    ref = highs(M)
    s = solve_lp(M, IPMOptions(dense_threshold=10))  # force the sparse LU path
    assert s.linear_solver == "cpu_sparse"
    assert s.status == "optimal" and ref.status == 0
    assert abs(s.objective - ref.fun) <= 1e-6 * (1 + abs(ref.fun))


def test_smw_dense_columns_exact():
    A = sp.random(100, 200, density=0.03, random_state=1, format="lil")
    A[:, 5] = 1.0
    A = A.tocsr()
    ns = NormalSolver(A)
    assert 5 in ns.dense_cols
    d = np.random.default_rng(0).uniform(0.1, 10, 200)
    ns.factorize(d)
    r = np.random.default_rng(1).normal(size=100)
    x, _ = ns.solve(r)
    M = (A @ sp.diags(d) @ A.T).toarray()
    assert np.linalg.norm(M @ x - r) / np.linalg.norm(r) < 1e-10


def test_mixed_precision_refinement_is_fp64_accurate():
    A = sp.random(80, 160, density=0.1, random_state=2, format="csr")
    ns = NormalSolver(A)
    d = np.random.default_rng(3).uniform(1e-3, 1e3, 160)
    ns.factorize(d, precision="fp32")
    assert ns.precision == "fp32"
    r = np.random.default_rng(4).normal(size=80)
    x, info = ns.solve(r)
    M = (A @ sp.diags(d) @ A.T).toarray() + ns.delta * np.eye(80)
    assert np.linalg.norm((A @ sp.diags(d) @ A.T) @ x - r) / np.linalg.norm(r) < 1e-9
    assert info["refine_steps"] >= 1


def test_lms_start_is_strictly_interior():
    M = random_lp(50, 80, seed=11)
    std = to_standard_form(presolve(M).reduced)
    As, bs, cs, _ = ruiz_scale(std.A, std.b, std.c)
    x0, _, s0, _ = lms_start(NormalSolver(As), As, bs, cs)
    assert (x0 > 0).all() and (s0 > 0).all()


def test_ruiz_unscale_roundtrip():
    rng = np.random.default_rng(5)
    A = sp.random(20, 30, density=0.3, random_state=5, format="csr") * 1000
    b, c = rng.normal(size=20) * 50, rng.normal(size=30) * 0.01
    As, bs, cs, sc = ruiz_scale(A, b, c)
    assert np.allclose(As.toarray(), np.diag(sc.Dr) @ A.toarray() @ np.diag(sc.Dc))
    xs, ys, ss = rng.uniform(size=30), rng.normal(size=20), rng.uniform(size=30)
    x, y, s = sc.unscale(xs, ys, ss)
    # residuals of the unscaled point are exactly the de-scaled residuals of the scaled point
    assert np.allclose(A @ x - b, sc.beta * (As @ xs - bs) / sc.Dr)
    assert np.allclose(A.T @ y + s - c, sc.gamma * (As.T @ ys + ss - cs) / sc.Dc)


def test_presolve_postsolve_fixed_and_empty():
    # a fixed at 2; d appears in no row; row r2 only contains a -> empty after fixing
    A = sp.csr_matrix(np.array([[1., 1., 0., 0.], [0., 1., 1., 0.], [2., 0., 0., 0.]]))
    M = Model("p", ["a", "b", "c", "d"], np.array([1., 2., 3., -1.]), np.array([2., 0, 0, 0]),
              np.array([2., 10, 10, 4]), np.zeros(4, bool), A, np.array(["G", "G", "L"], dtype="<U1"),
              np.array([3., 2., 5.]), ["r0", "r1", "r2"], [{}] * 3)
    pr = presolve(M)
    assert pr.status == "ok" and pr.fixed_values[0] == 2.0 and pr.fixed_values[3] == 4.0
    assert 2 not in pr.kept_rows
    s = solve_lp(M)
    ref = highs(M)
    assert s.status == "optimal" and abs(s.objective - ref.fun) < 1e-7
    assert s.x[0] == 2.0 and s.x[3] == 4.0


def test_presolve_detects_trivial_infeasibility():
    A = sp.csr_matrix(np.array([[1.0, 0.0]]))
    M = Model("i", ["a", "b"], np.zeros(2), np.array([1.0, 0]), np.array([1.0, 1]), np.zeros(2, bool), A,
              np.array(["G"], dtype="<U1"), np.array([2.0]), ["need_two"], [{}])
    s = solve_lp(M)
    assert s.status == "infeasible" and "need_two" in s.message


def test_warm_start_reaches_same_optimum():
    M = random_lp(120, 200, seed=4)
    cache = WarmStartCache()
    solve_lp(M, warm_cache=cache)
    M.c = M.c + np.random.default_rng(9).normal(size=M.n) * 0.05
    cold = solve_lp(M)
    warm = solve_lp(M, warm_cache=cache)
    assert warm.start == "warm" and warm.status == "optimal"
    assert abs(warm.objective - cold.objective) <= 1e-6 * (1 + abs(cold.objective))
