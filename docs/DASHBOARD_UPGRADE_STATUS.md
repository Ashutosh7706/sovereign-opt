# Dashboard upgrade status: build v0.3.0

This maps every item in **Dashboard_Advanced_Live_Upgrade_Plan.docx** and **refinery_3d_digital_twin.docx** to what was built.

Every visual shows real solver or hardware data; the animation only changes how that data arrives on screen.
Items marked **GPU-LAPTOP** need the RTX 4050 machine, because this build machine has no NVIDIA GPU.

## Section 1: the GPU result that was slower than HiGHS

| Item | Status | Where / evidence |
|---|---|---|
| **Root cause** (9.72 s vs 24.8 ms on the 69-row refinery) | **FIXED** | The GPU was used on a tiny model. CuPy kernels compiled on first use, and every factorisation re-uploaded data. Now there is a size policy (`linalg.NormalSolver._gpu_policy`), a boot warm-up (`gpu_warmup`), on-device assembly with A uploaded once, and the decision is stated for each solve |
| **Size-based auto-fallback** | **FIXED** | Auto mode uses the GPU only when rows ≥ `SOVEREIGN_GPU_MIN_ROWS` (default 2000) and the need is ≤ 80 % of free VRAM. Tests: `test_live.py::test_small_problem_reports_correct_engineering_choice` and `test_gpu_real.py::test_auto_policy_keeps_small_model_on_cpu_and_large_on_gpu` |
| **Dashboard message** "Problem too small for GPU offload — running CPU path (this is the correct engineering choice, not a limitation)" | **DONE** | Race tab banner (`race.js · gpuBanner`). It shows before the solve, when the model is below the threshold, and after it, from the solver's own decision |
| **GPU demo lane on a large problem** | **DONE** | `Dense LP 1500×2250 / 3000×4500 / 5000×7500 (GPU showcase)` models, with a device selector (Auto / Force GPU / CPU only) |

## Section 2: are the charts live?

| Item | Status | Where / evidence |
|---|---|---|
| **Charts stream per iteration** | **CONFIRMED** | `/ws/race` sends an `iter` event for every IPM iteration and `node` / `tree` events for branch-and-bound. The charts re-render on each WebSocket message and are not drawn at the end. Test: `test_live.py::test_live_websockets` |

## Features 1–9

| # | Feature | Status | Where |
|---|---|---|---|
| 1 | True streaming charts | **DONE** | Race tab: convergence and B&B charts append per message |
| 2 | Animated objective counter | **DONE** | `fx.js · Ticker`. The race hero counter shows the live objective in model units (`obj_model`), and the twin shows the margin in $k/day |
| 3 | Racing bars | **DONE** | Ranked lanes (#1 badge), a leader glow, a live timer while running, and a HiGHS-IPM lane |
| 4 | Audit-chain pulse | **DONE** | The header badge flashes when `/health` reports a new chained entry, and shows the entry count |
| 5 | Live solver log (typewriter) | **DONE** | `fx.js · TerminalLog`: iterations, incumbents (with their source), lane results and the device decision |
| 6 | Real GPU utilisation gauge | **DONE (real data)** / **GPU-LAPTOP** to see values | `telemetry.py`: `nvidia-smi -lms 500` provides GPU load, VRAM, temperature, SM clock and power. Solver CPU share is measured too. With no driver, the panel says so and shows no simulated values |
| 7 | Animated B&B tree | **DONE** | `viz.js · BnBTree` is drawn live from `tree` events, capped at 4000 events per race |
| 8 | Central-path trajectory | **DONE** | `viz.js · CentralPath`: 160 sampled (log x, log s) pairs per iteration against the line x·s = μ, with a fading trail |
| 9 | Optimal completion animation | **DONE** | `fx.js · Burst`, on the race tab and the twin. Suppressed under reduced motion |

## Section 5: RTX 4050 procedure

| Item | Status | Where |
|---|---|---|
| **VRAM ceiling test** | **SCRIPT READY**, GPU-LAPTOP | `scripts/find_sweet_spot.py` stops before the need exceeds 80 % of free VRAM and records the size where it stopped. On 6 GB this is expected somewhere around 10–12k rows; the script measures it rather than assuming it |
| **Sweet spot** (where the GPU starts to win) | **SCRIPT READY**, GPU-LAPTOP | The same script compares ours-GPU, ours-CPU, HiGHS default and HiGHS IPM on the same model. It recommends `SOVEREIGN_GPU_MIN_ROWS` and a demo-safe size |
| **Thermal run** | **SCRIPT READY**, GPU-LAPTOP | Peak temperature and minimum SM clock are recorded per size (sweet spot), plus a sustained run in `gpu_bench.py --minutes N`. Both are run by `gpu_check.ps1` |
| **Honest README wording, no extrapolation** | **DONE** | README "GPU: what may be claimed". The script writes the sentence into `docs/GPU_SWEET_SPOT.md` with the measured numbers filled in. A100/H100 is labelled as a projection |

## 3D digital twin (refinery_3d_digital_twin.docx)

| Item | Status | Where |
|---|---|---|
| **Three.js r128 + OrbitControls** | **DONE** | `frontend/vendor/three.js` and `orbit.js`, vendored so there is no CDN dependency and it works air-gapped |
| **Refinery layout** | **DONE** | `sovereign/twin.py · layout`: crude tanks → CDU → Reformer / FCC / DHDS-1 / DHDS-2 → 7 product tanks, with the real stream topology including FCC LCO to the DHDS units |
| **Tank levels from x\*** | **DONE** | `twin.state`: the level is the solved throughput ÷ capacity, eased in 3D |
| **Bottleneck glow from duals** | **DONE** | Colour is set by the shadow price in $/bbl: cyan = slack, green = active, amber > 2, red ≥ 8, with a pulse on red. For the MRPL-synthetic plan the CDU is red at ≈ $10.16/bbl |
| **Pipe particles, speed ∝ flow** | **DONE** | Bezier tube pipes carrying 12 additive particles each, moving at the solved kbbl/d |
| **Click tooltip / drag rotate / scroll zoom** | **DONE** | Detail card showing level, feed, in/out, shadow price, binding constraint and the value of one more kbbl/d |
| **WebSocket live updates** | **DONE** | `/ws/twin`: layout, then the live root-LP iterates (throttled to 30 ms), then each integer plan, then the final plan with exact duals, recorded as audit event `twin.solved` |
| **"3D Digital Twin" tab** | **DONE** | Second tab. It auto-solves on first open, and offers production / shadow plans, auto-rotate and reset view |
| **Offline** | **DONE** | Everything is served by the app itself; CSP stays `default-src 'self'` |

## Also made more 3D and engaging

* A perspective-grid backdrop, panels with depth shadows, a rotating 3D logo cube, and tilt-on-hover verdict cards.
* All of these are disabled in the high-contrast theme and under `prefers-reduced-motion`.

## Verified here (CPU machine, 30 Sep 2026)

* **Tests:** the full suite passes (`python -m pytest tests -q`).
  * New tests are in `tests/test_live.py`.
  * The real-GPU tests were exercised against the CuPy stand-in.
* **Browser check:**
  * The twin rendered and solved live: margin 1,704.6 $k/day, the CDU red at $10.16/bbl, and the click card correct.
  * The race streamed its log, central path (108 points), B&B tree (19 nodes), gauges and device decision.
* **Still to do on the RTX 4050:** run `gpu_check.ps1`, set `SOVEREIGN_GPU_MIN_ROWS` from `docs/GPU_SWEET_SPOT.md`, and screen-record one Dense-LP race.
