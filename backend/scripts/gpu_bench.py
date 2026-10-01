"""GPU-laptop validation + benchmark (audit #38/#58 CPU-vs-GPU, #39/#59 crossover, #40/#60 accuracy,
#42/#62 sustained load / throttling, #45/#65 TCO inputs). Run on the GPU machine from backend/:

    pip install cupy-cuda12x            # match your CUDA major version (nvidia-smi shows it)
    python scripts/gpu_bench.py --minutes 30 --gpu-cost-hour 0.35 --cpu-cost-hour 0.10

Writes ../docs/GPU_RESULTS.md and ../docs/gpu_results.json. Every number in the README/roadmap that
says "not tested on GPU" should be replaced with a number from that file.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sovereign import device  # noqa: E402
from sovereign.ipm import IPMOptions  # noqa: E402
from sovereign.linalg import GPU_FALLBACKS, NormalSolver  # noqa: E402
from sovereign.lp import solve_lp  # noqa: E402
from sovereign.models.random_lp import random_lp  # noqa: E402

DOCS = Path(__file__).resolve().parents[2] / "docs"


def nvidia_smi() -> dict:
    if not shutil.which("nvidia-smi"):
        return {}
    q = "name,driver_version,temperature.gpu,clocks.sm,clocks.max.sm,power.draw,utilization.gpu,memory.used"
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
        vals = [v.strip() for v in out.split(",")]
        return dict(zip(q.split(","), vals))
    except Exception:
        return {}


def factor_sweep(sizes, reps=3) -> list[dict]:
    """Dense normal-matrix Cholesky + solve, CPU vs GPU, same matrix: where does the GPU start winning?"""
    rows = []
    rng = np.random.default_rng(0)
    for m in sizes:
        n = int(1.5 * m)
        A = sp.random(m, n, density=min(0.05, 20 / m), random_state=1, format="csr")
        d = rng.uniform(0.1, 10, n)
        r = rng.normal(size=m)
        out = {"m": m}
        # consumer GeForce/RTX laptop GPUs run FP64 at ~1/64 of FP32: time both precisions
        for label, gpu, prec in (("cpu", False, "fp64"), ("gpu", True, "fp64"), ("gpu_fp32", True, "fp32")):
            if gpu and not device.gpu_available():
                out[label] = None
                continue
            ns = NormalSolver(A, dense_threshold=10 ** 9, use_gpu=gpu, gpu_mode="force" if gpu else "off")  # bypass size policy
            ns.factorize(d, precision=prec)  # warm-up (kernel compile / allocator)
            ns.solve(r)
            t = time.perf_counter()
            for _ in range(reps):
                ns.factorize(d, precision=prec)
                x, _ = ns.solve(r)  # refined to FP64 accuracy either way
            out[label] = (time.perf_counter() - t) / reps
            out[f"{label}_kind"] = ns._kind  # 'cpu_dense' here for a GPU label = VRAM fallback happened
        out["speedup"] = (out["cpu"] / out["gpu"]) if out.get("gpu") else None
        out["speedup_fp32"] = (out["cpu"] / out["gpu_fp32"]) if out.get("gpu_fp32") else None
        rows.append(out)
        g = lambda k: "n/a" if out.get(k) is None else f"{out[k]:.4f}s ({out.get(k + '_kind')})"
        print(f"  m={m:5d}  cpu {out['cpu']:.4f}s  gpu-fp64 {g('gpu')}  gpu-fp32 {g('gpu_fp32')}")
    return rows


def accuracy(models) -> list[dict]:
    rows = []
    for name, m in models:
        cpu = solve_lp(m, IPMOptions(precision="fp64", use_gpu=False))
        res = {"model": name, "cpu_fp64_obj": cpu.objective, "cpu_s": cpu.seconds}
        if device.gpu_available():
            for prec in ("mixed", "fp64"):
                g = solve_lp(m, IPMOptions(precision=prec, use_gpu=True, gpu_mode="force"))
                res[f"gpu_{prec}_obj"] = g.objective
                res[f"gpu_{prec}_s"] = g.seconds
                res[f"gpu_{prec}_relerr"] = abs(g.objective - cpu.objective) / (1 + abs(cpu.objective))
                res[f"gpu_{prec}_kind"] = g.linear_solver
        rows.append(res)
        print(f"  {name}: {json.dumps({k: v for k, v in res.items() if 'relerr' in k or k.endswith('_s')})}")
    return rows


def sustained(minutes: float) -> dict:
    """Repeat a 2000x3000 LP solve for `minutes`; a rising solve time with falling SM clocks = throttling."""
    m = random_lp(2000, 3000)
    samples = []
    t_end = time.time() + minutes * 60
    while time.time() < t_end:
        t = time.perf_counter()
        s = solve_lp(m, IPMOptions(use_gpu=device.gpu_available()))
        samples.append({"t": round(time.time(), 1), "solve_s": round(time.perf_counter() - t, 4),
                        "status": s.status, **nvidia_smi()})
    times = [x["solve_s"] for x in samples]
    k = max(1, len(times) // 10)
    first, last = float(np.median(times[:k])), float(np.median(times[-k:]))
    return {"minutes": minutes, "solves": len(samples), "first_decile_median_s": first,
            "last_decile_median_s": last, "slowdown_pct": round(100 * (last / first - 1), 1),
            "throttling_suspected": last > 1.15 * first, "samples": samples}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0.0, help="sustained-load duration (30-60 recommended)")
    ap.add_argument("--gpu-cost-hour", type=float, default=None, help="$ per hour for the GPU box (TCO)")
    ap.add_argument("--cpu-cost-hour", type=float, default=None, help="$ per hour for a CPU-only box (TCO)")
    ap.add_argument("--sizes", default="200,500,1000,2000,3000,4000,6000,8000,12000")
    a = ap.parse_args()
    env = {"platform": platform.platform(), "python": platform.python_version(), "device": device.describe(),
           "nvidia_smi": nvidia_smi(), "boot_report": device.boot_report()}
    print(env["boot_report"])
    print("factorization sweep (CPU vs GPU):")
    sweep = factor_sweep([int(x) for x in a.sizes.split(",")])
    print("accuracy regression (GPU vs CPU FP64):")
    acc = accuracy([("random 200x300", random_lp(200, 300)), ("random 800x1200", random_lp(800, 1200)),
                    ("random 2000x3000", random_lp(2000, 3000))])
    sus = sustained(a.minutes) if a.minutes > 0 else None
    crossover = next((r["m"] for r in sweep if max(r.get("speedup") or 0, r.get("speedup_fp32") or 0) > 1.0), None)
    tco = None
    if a.gpu_cost_hour is not None and a.cpu_cost_hour is not None and acc and acc[-1].get("gpu_mixed_s"):
        big = acc[-1]
        tco = {"gpu_cost_per_solve": a.gpu_cost_hour * big["gpu_mixed_s"] / 3600,
               "cpu_cost_per_solve": a.cpu_cost_hour * big["cpu_s"] / 3600,
               "note": "per 2000x3000 LP solve; multiply by re-optimisations/day for daily cost"}
    result = {"env": env, "factor_sweep": sweep, "gpu_crossover_m": crossover, "accuracy": acc,
              "sustained": sus, "tco": tco, "gpu_fallbacks": GPU_FALLBACKS}
    DOCS.mkdir(exist_ok=True)
    (DOCS / "gpu_results.json").write_text(json.dumps(result, indent=1, default=str))
    md = ["# GPU validation results", "", f"* Machine: `{env['platform']}`", f"* {env['boot_report']}",
          f"* nvidia-smi: `{env['nvidia_smi']}`", "", "## Factorization: CPU vs GPU (same matrix)", "",
          "| m | CPU FP64 s | GPU FP64 s | GPU FP32 s | speedup FP64 | speedup FP32 | GPU path used |",
          "|---|---|---|---|---|---|---|"]
    for r in sweep:
        f4 = lambda v: "-" if v is None else f"{v:.4f}"
        fx = lambda v: "-" if not v else f"{v:.2f}x"
        md.append(f"| {r['m']} | {r['cpu']:.4f} | {f4(r['gpu'])} | {f4(r['gpu_fp32'])} | {fx(r['speedup'])} | "
                  f"{fx(r['speedup_fp32'])} | {r.get('gpu_kind', '-')} / {r.get('gpu_fp32_kind', '-')} |")
    md += ["", f"GPU starts winning at m = **{crossover}** rows" if crossover else "GPU never won in this sweep "
           "(or no GPU) - small problems are faster on CPU because of transfer/launch overhead.", "",
           "## Accuracy (objective vs CPU FP64)", "", "```", json.dumps(acc, indent=1), "```"]
    if sus:
        md += ["", "## Sustained load", "", f"{sus['solves']} solves in {sus['minutes']} min; median solve "
               f"{sus['first_decile_median_s']:.3f}s -> {sus['last_decile_median_s']:.3f}s "
               f"({sus['slowdown_pct']}%); throttling suspected: **{sus['throttling_suspected']}**"]
    if tco:
        md += ["", "## TCO inputs", "", "```", json.dumps(tco, indent=1), "```"]
    (DOCS / "GPU_RESULTS.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"wrote {DOCS / 'GPU_RESULTS.md'}")


if __name__ == "__main__":
    main()
