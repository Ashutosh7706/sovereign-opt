"""Solver hardening (audit #39, #41, #42, #43, #47, #48, #50, #54/#57, #69, #74, #75)."""
import json
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest
import scipy.linalg as sla
import scipy.sparse as sp

from sovereign import device, linalg
from sovereign.bnb import solve_mip
from sovereign.engine import SolverConfig, solve
from sovereign.ipm import IPMOptions
from sovereign.lp import solve_lp
from sovereign.model import Model, ModelBuilder
from sovereign.models.random_lp import random_lp
from sovereign.models.refinery import build, default_params
from sovereign.mps import parse_mps
from sovereign.presolve import presolve
from test_lp import highs, random_general_lp
from test_mip_and_explain import milp_ref

GOLDEN = Path(__file__).parent / "golden.json"


# ------------------------------------------------------------------ HSD (#39) + certificates (#43)
@pytest.mark.parametrize("seed", range(20))
def test_hsd_alone_matches_highs(seed):
    M = random_general_lp(30, 50, seed)
    ref = highs(M)
    s = solve_lp(M, IPMOptions(algorithm="hsd"))
    expected = {0: "optimal", 2: "infeasible", 3: "unbounded"}[ref.status]
    assert s.status == expected
    if expected == "optimal":
        assert abs(s.objective - ref.fun) <= 1e-6 * (1 + abs(ref.fun))


def test_hsd_verified_farkas_certificate():
    # x1 + x2 <= 1 and x1 + x2 >= 3 with x >= 0: infeasible; certificate must name both rows
    b = ModelBuilder("farkas")
    b.var("x1"); b.var("x2")
    b.constr("cap", {"x1": 1, "x2": 1}, "<=", 1, desc="cap")
    b.constr("need", {"x1": 1, "x2": 1}, ">=", 3, desc="need")
    s = solve_lp(b.build(), IPMOptions(algorithm="hsd"))
    assert s.status == "infeasible" and s.certificate["verified"]
    assert {r["row"] for r in s.certificate["rows"]} == {"cap", "need"}


def test_hsd_fallback_rescues_degenerate_boundary():
    p = default_params()
    p.products["VLSFO"].min_demand = 45
    for s in p.specs:
        if s.product == "VLSFO":
            s.value = 0.12179  # feasible set with (almost) empty interior
    M = build(p)
    r = solve_mip(M)
    ref = milp_ref(M)
    assert r.status == "optimal"
    assert abs(r.objective - ref.fun) <= 1e-4 * (1 + abs(ref.fun))


def test_mip_infeasibility_carries_certificate():
    p = default_params()
    p.products["VLSFO"].min_demand = 45
    for s in p.specs:
        if s.product == "VLSFO":
            s.value = 0.05
    r = solve(build(p))
    assert r["status"] == "infeasible" and r["certificate"] is not None


# ------------------------------------------------------------------ presolve singleton rows (#41)
def test_singleton_rows_become_bounds_and_duals_are_restored():
    M = build().relaxed()
    pr = presolve(M)
    assert len(pr.singletons) >= 5  # the demand rows
    s = solve_lp(M)
    ref = highs(M)
    assert abs(s.objective - ref.fun) <= 1e-7 * (1 + abs(ref.fun))
    # HiGHS marginals, mapped to our sign convention, for the singleton rows
    ub_i = [i for i, t in enumerate(M.senses) if t != "E"]
    yh = np.zeros(M.m)
    for k, i in enumerate(ub_i):
        yh[i] = ref.ineqlin.marginals[k] * (1 if M.senses[i] == "L" else -1)
    for i, _, _ in pr.singletons:
        assert abs(s.y[i] - yh[i]) <= 1e-6 * (1 + abs(yh[i])), M.row_names[i]


def test_singleton_row_infeasibility_named():
    b = ModelBuilder("s")
    b.var("x", 0, 5)
    b.constr("too_big", {"x": 2}, ">=", 20)
    s = solve_lp(b.build())
    assert s.status == "infeasible" and "too_big" in s.message


# ------------------------------------------------------------------ ill-conditioned stress (#42)
@pytest.mark.parametrize("seed", range(8))
def test_badly_scaled_rows_and_columns(seed):
    M = random_general_lp(30, 50, 200 + seed, free_frac=0.0)
    r = np.random.default_rng(seed)
    R = 10.0 ** r.uniform(-5, 5, M.m)  # rows scaled over 10 orders of magnitude
    C = 10.0 ** r.uniform(-3, 3, M.n)  # columns over 6
    M.A = (sp.diags(R) @ M.A @ sp.diags(C)).tocsr()
    M.rhs = M.rhs * R
    M.c = M.c * C
    M.lb, M.ub = M.lb / C, M.ub / C
    ref = highs(M)
    s = solve_lp(M)
    expected = {0: "optimal", 2: "infeasible", 3: "unbounded"}[ref.status]
    assert s.status == expected
    if expected == "optimal":
        assert abs(s.objective - ref.fun) <= 1e-6 * (1 + abs(ref.fun))


def test_nearly_parallel_rows():
    b = ModelBuilder("parallel")
    b.var("x", 0, 10); b.var("y", 0, 10)
    b.constr("r1", {"x": 1, "y": 1}, "<=", 10)
    b.constr("r2", {"x": 1, "y": 1 + 1e-9}, "<=", 10 + 1e-9)  # numerically a duplicate
    b.constr("r3", {"x": 1, "y": -1}, "==", 2)
    b.add_obj("x", -1); b.add_obj("y", -2)
    s = solve_lp(b.build())
    assert s.status == "optimal"
    assert abs(s.objective - (-(6 + 2 * 4))) < 1e-6  # x=6, y=4


def test_condition_estimate_reported():
    r = solve(random_lp(100, 150))
    assert r["cond_estimate"] and r["cond_estimate"] >= 1.0 and r["cond_warning"] is False


# ------------------------------------------------------------------ edge cases (#48)
def test_free_variables_and_ranged_rows_by_hand():
    mps = """NAME EDGE
ROWS
 N obj
 L r1
 E r2
COLUMNS
 x obj 1 r1 1
 x r2 1
 y obj -1 r1 1
 y r2 -1
RHS
 rhs r1 10 r2 2
RANGES
 rng r1 4
BOUNDS
 FR bnd x
 MI bnd y
 UP bnd y 5
ENDATA
"""
    # min x - y  s.t. 6 <= x + y <= 10,  x - y = 2,  x free, y <= 5  ->  objective is exactly 2
    m = parse_mps(mps)
    s = solve_lp(m)
    assert s.status == "optimal" and abs(s.objective - 2.0) < 1e-7
    assert 6 - 1e-7 <= s.x[0] + s.x[1] <= 10 + 1e-7


def test_free_variable_unbounded_direction():
    b = ModelBuilder("free")
    b.var("x", -np.inf, np.inf, obj=1.0)
    b.var("y", 0, 1)
    b.constr("r", {"y": 1}, "<=", 1)
    b.constr("link", {"x": 1, "y": 1}, "<=", 5)
    assert solve_lp(b.build()).status == "unbounded"


# ------------------------------------------------------------------ hand-verifiable blend (#75)
def test_two_component_blend_known_by_hand():
    """Component A: 0.1 wt% S at $60/bbl, B: 1.0 wt% S at $40/bbl; make 100 bbl at <= 0.5 wt% S.
    By hand: 0.1(100 - B) + 1.0 B <= 50  ->  B <= 400/9;  cost = (60*500 + 40*400)/9 = 46000/9.
    Shadow price of the sulfur limit: each extra wt%-bbl of allowance saves 20/0.9 = 200/9 $."""
    b = ModelBuilder("blend")
    b.var("A", obj=60.0); b.var("B", obj=40.0)
    b.constr("volume", {"A": 1, "B": 1}, "==", 100)
    b.constr("sulfur", {"A": 0.1, "B": 1.0}, "<=", 50)
    s = solve_lp(b.build())
    assert abs(s.objective - 46000 / 9) <= 1e-8 * 46000 / 9  # IPM relative tolerance
    assert abs(s.x[1] - 400 / 9) < 1e-6 and abs(s.x[0] - 500 / 9) < 1e-6
    assert abs(s.y[1] - (-200 / 9)) < 1e-6  # minimisation: <= row dual is negative


# ------------------------------------------------------------------ B&B tie-break determinism (#47)
def test_bnb_tie_break_is_deterministic():
    """Rule: best bound first; equal bounds -> earlier-created node first (FIFO counter)."""
    b = ModelBuilder("symmetric", maximize=True)
    for k in range(8):  # identical items: many ties in bound and pseudo-cost
        b.var(f"z{k}", 0, 1, obj=10.0, integer=True)
    b.constr("cap", {f"z{k}": 3.0 for k in range(8)}, "<=", 10.0)
    M = b.build()
    r1, r2 = solve_mip(M), solve_mip(M)
    assert r1.x.tobytes() == r2.x.tobytes()
    assert [h.get("branch_var") for h in r1.history] == [h.get("branch_var") for h in r2.history]
    assert M.display_objective(r1.objective) == pytest.approx(30.0)


# ------------------------------------------------------------------ performance budget (#74)
def test_performance_budget():
    t = time.perf_counter()
    assert solve(build())["status"] == "optimal"
    assert time.perf_counter() - t < 5.0, "refinery MIP regressed (> 5 s; ~0.3 s expected)"
    t = time.perf_counter()
    assert solve(random_lp(800, 1200))["status"] == "optimal"
    assert time.perf_counter() - t < 15.0, "800x1200 LP regressed (> 15 s; ~1.5 s expected)"


# ------------------------------------------------------------------ golden-file regression (#69)
def _golden_now():
    return {"refinery_mip": solve(build())["objective"],
            "random_s": solve(random_lp(200, 300))["objective"],
            "refinery_lp": solve(build().relaxed())["objective"]}


def test_golden_objectives():
    """Fails on unintended numeric drift. Regenerate deliberately (from backend/) with:
       python -c "import sys; sys.path[:0]=['.','tests']; import test_solver_hardening as t; t.write_golden()"
    """
    golden = json.loads(GOLDEN.read_text())
    for k, v in _golden_now().items():
        assert abs(v - golden[k]) <= 1e-7 * (1 + abs(golden[k])), (k, v, golden[k])


def write_golden():  # pragma: no cover
    GOLDEN.write_text(json.dumps(_golden_now(), indent=1))


# ------------------------------------------------------------------ GPU code path via a CuPy shim (#54, #57)
def _fake_cupy(free_bytes):
    cp = types.ModuleType("cupy")
    cp.asarray = np.asarray
    cp.asnumpy = np.asarray
    cp.isfinite = np.isfinite
    cp.abs, cp.diag, cp.maximum = np.abs, np.diag, np.maximum
    cp.linalg = types.SimpleNamespace(cholesky=np.linalg.cholesky)
    cp.cuda = types.SimpleNamespace(runtime=types.SimpleNamespace(memGetInfo=lambda: (free_bytes, 8 << 30)))
    cupyx = types.ModuleType("cupyx")
    cupyx_scipy = types.ModuleType("cupyx.scipy")
    cupyx_linalg = types.ModuleType("cupyx.scipy.linalg")
    cupyx_linalg.solve_triangular = sla.solve_triangular
    return cp, {"cupyx": cupyx, "cupyx.scipy": cupyx_scipy, "cupyx.scipy.linalg": cupyx_linalg}


def _install_fake_gpu(monkeypatch, free, chol=None):
    cp, mods = _fake_cupy(free)
    if chol is not None:
        cp.linalg = types.SimpleNamespace(cholesky=chol)
    monkeypatch.setattr(device, "_cupy", cp)
    monkeypatch.setitem(device._gpu, "name", "FakeGPU (NumPy stand-in)")
    for k, v in mods.items():
        monkeypatch.setitem(sys.modules, k, v)
    return cp


def _normal_check(ns, A):
    d = np.random.default_rng(0).uniform(0.1, 10, A.shape[1])
    ns.factorize(d)
    r = np.random.default_rng(1).normal(size=A.shape[0])
    x, _ = ns.solve(r)
    assert np.linalg.norm((A @ sp.diags(d) @ A.T) @ x - r) / np.linalg.norm(r) < 1e-10


def test_gpu_path_forced_runs_assembly_and_cholesky_on_device(monkeypatch):
    """The GPU code path end-to-end with NumPy standing in for CuPy (no CUDA on the build box):
    GEMM assembly + Cholesky on the 'device', FP64-refined answer, full LP through the GPU path."""
    _install_fake_gpu(monkeypatch, 8 << 30)
    A = sp.random(60, 120, density=0.1, random_state=3, format="csr")
    ns = linalg.NormalSolver(A, use_gpu=True, gpu_mode="force")
    assert ns.use_gpu and ns.gpu_decision.startswith("GPU (forced)")
    _normal_check(ns, A)
    assert ns._kind == "gpu_dense"
    s = solve_lp(random_lp(40, 60), IPMOptions(use_gpu=True, gpu_mode="force"))
    assert s.status == "optimal" and s.linear_solver == "gpu_dense"


def test_small_problem_stays_on_cpu_with_stated_reason(monkeypatch):
    """Dashboard plan Sec. 1: a 69-row refinery model must not be sent to the GPU."""
    _install_fake_gpu(monkeypatch, 8 << 30)
    monkeypatch.setitem(linalg.GPU_POLICY, "min_rows", 2000)
    r = solve(build(), SolverConfig(use_gpu=True))
    assert r["device"] == "cpu" and "too small for GPU offload" in r["gpu_decision"]
    assert r["status"] == "optimal"
    monkeypatch.setitem(linalg.GPU_POLICY, "min_rows", 10)  # above the threshold -> GPU
    A = sp.random(60, 120, density=0.1, random_state=3, format="csr")
    assert linalg.NormalSolver(A, use_gpu=True).use_gpu


def test_vram_policy_refuses_oversized_problem(monkeypatch):
    _install_fake_gpu(monkeypatch, 1024)  # 1 KB "free VRAM"
    A = sp.random(60, 120, density=0.1, random_state=3, format="csr")
    ns = linalg.NormalSolver(A, use_gpu=True, gpu_mode="force")
    assert not ns.use_gpu and "VRAM" in ns.gpu_decision
    _normal_check(ns, A)
    assert ns._kind == "cpu_dense"


def test_cuda_error_mid_solve_falls_back_to_cpu(monkeypatch):
    def boom(_m):
        raise RuntimeError("CUDA error: out of memory (simulated)")
    _install_fake_gpu(monkeypatch, 8 << 30, chol=boom)
    before = linalg.GPU_FALLBACKS["count"]
    A = sp.random(60, 120, density=0.1, random_state=3, format="csr")
    ns = linalg.NormalSolver(A, use_gpu=True, gpu_mode="force")
    _normal_check(ns, A)
    assert ns._kind == "cpu_dense" and ns.gpu_decision.startswith("CPU (fallback)")
    assert linalg.GPU_FALLBACKS["count"] == before + 1
