"""Real-GPU checks (audit #34/#54, #40/#60). Skipped automatically when no CUDA device is present -
run `python -m pytest tests/test_gpu_real.py -v` on the GPU laptop after `pip install cupy-cuda12x`."""
import pytest

from sovereign import device
from sovereign.engine import SolverConfig, solve
from sovereign.ipm import IPMOptions
from sovereign.lp import solve_lp
from sovereign.models.random_lp import random_lp
from sovereign.models.refinery import build

pytestmark = pytest.mark.skipif(not device.gpu_available(), reason="no CUDA device / CuPy on this machine")


@pytest.mark.parametrize("size", [(200, 300), (800, 1200), (2000, 3000)])
@pytest.mark.parametrize("precision", ["mixed", "fp64"])
def test_gpu_matches_cpu_fp64(size, precision):
    m = random_lp(*size)
    cpu = solve_lp(m, IPMOptions(precision="fp64", use_gpu=False))
    gpu = solve_lp(m, IPMOptions(precision=precision, use_gpu=True, gpu_mode="force"))
    assert gpu.status == cpu.status == "optimal"
    assert gpu.linear_solver == "gpu_dense"
    assert abs(gpu.objective - cpu.objective) <= 1e-7 * (1 + abs(cpu.objective))


def test_refinery_mip_on_gpu():
    r = solve(build(), SolverConfig(use_gpu=True, gpu_mode="force"))
    ref = solve(build(), SolverConfig(use_gpu=False))
    assert r["status"] == "optimal" and r["device"] == "gpu"
    assert abs(r["objective"] - ref["objective"]) <= 1e-6 * (1 + abs(ref["objective"]))


def test_gpu_deterministic_replay():
    m = build()
    a = solve(m, SolverConfig(use_gpu=True, deterministic=True, gpu_mode="force"))
    b = solve(m, SolverConfig(use_gpu=True, deterministic=True, gpu_mode="force"))
    assert a["x_sha256"] == b["x_sha256"], "GPU run is not bit-reproducible - see roadmap item 14"


def test_auto_policy_keeps_small_model_on_cpu_and_large_on_gpu():
    """Dashboard plan Sec. 1: the 69-row refinery must NOT be offloaded (that was the 9.72 s demo);
    a model above the row threshold must be."""
    r = solve(build(), SolverConfig(use_gpu=True, gpu_mode="auto"))
    assert r["device"] == "cpu" and "too small for GPU offload" in r["gpu_decision"]
    big = solve_lp(random_lp(2500, 3750, density=0.05), IPMOptions(use_gpu=True, gpu_mode="auto"))
    assert big.status == "optimal" and big.linear_solver == "gpu_dense", big.gpu_decision
