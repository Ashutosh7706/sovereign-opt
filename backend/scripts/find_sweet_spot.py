"""Find where THIS GPU starts to win, and the biggest problem it can demo safely
(Dashboard_Advanced_Live_Upgrade_Plan Sec. 5: VRAM ceiling, sweet spot, thermal behaviour).

Run on the GPU laptop from backend/ (after `pip install cupy-cuda12x`):

    python scripts/find_sweet_spot.py                      # default sweep, ~10-25 min on an RTX 4050
    python scripts/find_sweet_spot.py --sizes 1000,2000,4000 --highs-limit 60

For each dense LP size (n = 1.5 m) it solves the SAME model four ways and records wall time,
objective agreement, VRAM use and GPU temperature / SM clock:

    ours on the GPU (forced) | ours on the CPU | HiGHS default | HiGHS IPM

It stops before the VRAM ceiling (estimated need > 80 % of free VRAM) instead of crashing, and
writes ../docs/GPU_SWEET_SPOT.md + ../docs/gpu_sweet_spot.json with:
  * the recommended SOVEREIGN_GPU_MIN_ROWS (smallest size from which the GPU wins every larger size),
  * the demo-safe size (largest size whose GPU solve fits the --demo-seconds budget with VRAM headroom),
  * the README sentence with the measured numbers filled in - nothing extrapolated.
On a machine without a GPU it still runs the CPU lanes and says the GPU lanes were not measured.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sovereign import baselines, device  # noqa: E402
from sovereign.ipm import IPMOptions  # noqa: E402
from sovereign.linalg import GPU_FALLBACKS, gpu_warmup  # noqa: E402
from sovereign.lp import solve_lp  # noqa: E402
from sovereign.models.random_lp import random_lp  # noqa: E402
from sovereign.telemetry import Telemetry  # noqa: E402

DOCS = Path(__file__).resolve().parents[2] / "docs"


def density_for(m: int) -> float:
    """Same family as the dashboard's dense-m/l/xl showcase models (1500 @ 10 %, 3000 @ 5 %, 5000 @ 3 %)."""
    return min(0.10, 150.0 / m)


def vram_need_bytes(m: int, n: int) -> int:
    return 8 * (3 * m * m + m * n)  # normal matrix + factor + workspace + A, FP64 worst case (linalg policy)


class PeakWatch:
    """Samples the telemetry stream while one solve runs: peak GPU util / VRAM / temp, min SM clock."""

    def __init__(self, tel: Telemetry):
        self.tel, self.peak, self._stop = tel, {}, threading.Event()

    def __enter__(self):
        self.peak = {"gpu_util_max": None, "vram_used_mb_max": None, "temp_c_max": None, "sm_clock_mhz_min": None}
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            s = self.tel.snapshot()
            for key, src, fn in (("gpu_util_max", "gpu_util", max), ("vram_used_mb_max", "vram_used_mb", max),
                                 ("temp_c_max", "temp_c", max), ("sm_clock_mhz_min", "sm_clock_mhz", min)):
                v = s.get(src)
                if v is not None:
                    self.peak[key] = v if self.peak[key] is None else fn(self.peak[key], v)
            time.sleep(0.25)

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=2)


def ours(model, gpu: bool, precision: str) -> dict:
    t = time.perf_counter()
    r = solve_lp(model, IPMOptions(precision=precision, use_gpu=gpu, gpu_mode="force" if gpu else "off"))
    return {"seconds": time.perf_counter() - t, "status": r.status, "objective": r.objective,
            "iters": r.iters, "kind": r.linear_solver, "decision": r.gpu_decision}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sizes", default="500,1000,1500,2000,3000,4000,5000,6000,8000,10000,12000",
                    help="constraint rows m to try (n = 1.5 m)")
    ap.add_argument("--precision", default="mixed", choices=["mixed", "fp64"],
                    help="mixed = FP32 factorisation + FP64 refinement (consumer GPUs are slow at FP64)")
    ap.add_argument("--highs-limit", type=float, default=120.0, help="seconds per HiGHS run before giving up")
    ap.add_argument("--max-seconds", type=float, default=300.0, help="stop the sweep once one of our solves exceeds this")
    ap.add_argument("--demo-seconds", type=float, default=20.0, help="a 'demo-safe' GPU solve must finish within this")
    ap.add_argument("--skip-highs-default", action="store_true", help="dual simplex gets slow on dense LPs")
    a = ap.parse_args()

    have_gpu = device.gpu_available()
    print(device.boot_report())
    if have_gpu:
        gpu_warmup()  # compile kernels once, so size 1 is not charged for it
    tel = Telemetry(interval_ms=500)
    tel.start()
    time.sleep(1.2)
    env = {"platform": platform.platform(), "device": device.describe(), "precision": a.precision,
           "nvidia_smi": tel.snapshot()}

    f = lambda v: "-" if v is None else f"{v:.3f}"  # noqa: E731
    rows, stop_reason = [], None
    highs_default_dead = a.skip_highs_default
    for m in [int(x) for x in a.sizes.split(",")]:
        n = int(1.5 * m)
        need = vram_need_bytes(m, n)
        free = device.gpu_free_bytes() if have_gpu else None
        row = {"m": m, "n": n, "density": density_for(m), "vram_need_gb": round(need / 1e9, 2),
               "vram_free_gb": round(free / 1e9, 2) if free else None}
        if have_gpu and free and need > 0.8 * free:
            stop_reason = (f"VRAM ceiling: {m} rows needs ~{need / 1e9:.1f} GB, only {free / 1e9:.1f} GB free "
                           f"(80 % rule) - largest size this GPU can hold is below {m} rows")
            print("  " + stop_reason)
            break
        model = random_lp(m, n, density=density_for(m))
        print(f"m={m} n={n} nnz={model.A.nnz}")
        if have_gpu:
            with PeakWatch(tel) as pw:
                row["gpu"] = ours(model, True, a.precision)
            row["gpu"].update(pw.peak)
            print(f"  ours GPU  {row['gpu']['seconds']:.3f}s  ({row['gpu']['kind']})  "
                  f"peak {pw.peak['temp_c_max']} C, min clock {pw.peak['sm_clock_mhz_min']} MHz")
        row["cpu"] = ours(model, False, a.precision)
        print(f"  ours CPU  {row['cpu']['seconds']:.3f}s")
        row["highs_ipm"] = baselines.run_highs_ipm(model, time_limit=a.highs_limit)
        print(f"  HiGHS IPM {f(row['highs_ipm'].get('seconds'))}s  {row['highs_ipm']['status']}")
        if not highs_default_dead:
            row["highs"] = baselines.run_highs(model, time_limit=a.highs_limit)
            print(f"  HiGHS def {f(row['highs'].get('seconds'))}s  {row['highs']['status']}")
            if row["highs"]["status"] not in ("optimal",):
                highs_default_dead = True  # hit the limit: do not wait for it on even bigger sizes
        ref = row["cpu"]["objective"]
        for k in ("gpu", "highs_ipm", "highs"):
            o = row.get(k, {}).get("objective")
            if o is not None and ref is not None:
                row[k]["agrees"] = abs(o - ref) <= 1e-6 * (1 + abs(ref))
        rows.append(row)
        if max(row.get("gpu", {}).get("seconds", 0), row["cpu"]["seconds"]) > a.max_seconds:
            stop_reason = f"stopped after m={m}: a solve took longer than --max-seconds {a.max_seconds}"
            break
    tel.stop()

    # ---------------------------------------------------------------- analysis (measured only)
    def best_cpu(r):  # the strongest CPU competitor at this size: ours-CPU or any HiGHS lane that finished
        ts = [r["cpu"]["seconds"]] + [r[k]["seconds"] for k in ("highs_ipm", "highs")
                                      if r.get(k, {}).get("status") == "optimal" and r[k].get("seconds")]
        return min(ts)

    wins = [bool(r.get("gpu")) and r["gpu"]["status"] == "optimal" and r["gpu"]["seconds"] < best_cpu(r) for r in rows]
    crossover = None
    for i, r in enumerate(rows):
        if all(wins[i:]) and wins[i]:
            crossover = r
            break
    demo = None  # a showcase size: the GPU wins there, inside the time budget, with VRAM headroom
    for r, w in zip(rows, wins):
        g = r.get("gpu")
        if w and g["seconds"] <= a.demo_seconds and \
                (r["vram_free_gb"] is None or r["vram_need_gb"] <= 0.7 * r["vram_free_gb"]):
            demo = r
    temps = [r["gpu"].get("temp_c_max") for r in rows if r.get("gpu") and r["gpu"].get("temp_c_max") is not None]
    clocks = [r["gpu"].get("sm_clock_mhz_min") for r in rows if r.get("gpu") and r["gpu"].get("sm_clock_mhz_min")]

    gpu_name = env["device"].get("gpu") or "GPU"
    vram = env["device"].get("vram_gb")
    if crossover:
        wording = (f"Benchmarked on a consumer {gpu_name} ({vram} GB), not datacenter hardware. "
                   f"GPU wins over CPU/HiGHS above ~{crossover['n']:,} variables ({crossover['m']:,} constraints) "
                   f"on dense LPs; below that we automatically run the CPU path, which is the correct "
                   f"engineering choice. A100/H100 performance is a projection, not a measured claim.")
    elif have_gpu:
        wording = (f"Benchmarked on a consumer {gpu_name} ({vram} GB), not datacenter hardware. In this sweep the "
                   f"GPU did not beat the best CPU lane at any size tried, so the CPU path is used - do not "
                   f"claim a GPU speed-up. A100/H100 performance is a projection, not a measured claim.")
    else:
        wording = "No GPU on this machine - GPU lanes not measured. Run this script on the GPU laptop."
    result = {"env": env, "rows": rows, "stop_reason": stop_reason, "gpu_fallbacks": GPU_FALLBACKS,
              "recommended_gpu_min_rows": crossover["m"] if crossover else None,
              "demo_safe": {"m": demo["m"], "n": demo["n"], "gpu_seconds": demo["gpu"]["seconds"]} if demo else None,
              "peak_temp_c": max(temps) if temps else None, "min_sm_clock_mhz": min(clocks) if clocks else None,
              "readme_wording": wording}
    DOCS.mkdir(exist_ok=True)
    (DOCS / "gpu_sweet_spot.json").write_text(json.dumps(result, indent=1, default=str))

    md = ["# GPU sweet spot - measured on this machine", "",
          f"* Machine: `{env['platform']}`", f"* Device: {gpu_name} ({vram} GB VRAM) · precision `{a.precision}`",
          f"* Generated {time.strftime('%Y-%m-%d %H:%M')} by `scripts/find_sweet_spot.py`", "",
          "| rows m | vars n | VRAM need GB | ours GPU s | ours CPU s | HiGHS IPM s | HiGHS default s | GPU wins? | peak temp C | min SM MHz |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r, w in zip(rows, wins):
        g = r.get("gpu") or {}
        md.append(f"| {r['m']:,} | {r['n']:,} | {r['vram_need_gb']} | {f(g.get('seconds'))} | {f(r['cpu']['seconds'])} | "
                  f"{f(r['highs_ipm'].get('seconds'))} | {f(r.get('highs', {}).get('seconds'))} | "
                  f"{'yes' if w else ('n/a' if not g else 'no')} | {g.get('temp_c_max') or '-'} | {g.get('sm_clock_mhz_min') or '-'} |")
    md += ["", "## Results", ""]
    md.append(f"* **Recommended `SOVEREIGN_GPU_MIN_ROWS` = {crossover['m']}** (GPU faster than the best CPU lane "
              f"at this size and every larger size tried)." if crossover else
              "* No stable GPU win measured - keep the default threshold; the auto policy will run the CPU path.")
    md.append(f"* **Demo-safe size: {demo['m']:,} x {demo['n']:,}** - GPU solve {demo['gpu']['seconds']:.1f} s "
              f"(budget {a.demo_seconds:.0f} s), VRAM need within 70 % of free." if demo else
              "* No demo-safe GPU showcase size found in this sweep (the GPU must win within the time budget).")
    if stop_reason:
        md.append(f"* Sweep stopped: {stop_reason}.")
    if temps:
        md.append(f"* Thermal: peak {max(temps):.0f} C, lowest SM clock {min(clocks):.0f} MHz during solves "
                  f"(a falling clock at high temperature = thermal throttling; plug in the charger and use the "
                  f"Performance power mode for demos).")
    md += ["", "## README wording (measured numbers filled in)", "", f"> {wording}", "",
           "Every number above was measured by this script on this machine. Do not extrapolate them to other "
           "GPUs; re-run the script on the target hardware instead."]
    (DOCS / "GPU_SWEET_SPOT.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md[-8:]))
    print(f"\nwrote {DOCS / 'GPU_SWEET_SPOT.md'}")


if __name__ == "__main__":
    main()
