# Sovereign Optimization Platform: v0.3.0 (live 3D dashboard build)

An on-prem LP/MIP decision-support platform built from the **Sovereign GPU-Native Optimization
Master Roadmap**. It includes our own interior-point solver with a homogeneous self-dual fallback,
branch-and-bound, an infeasibility explainer, a plain-English constraint compiler behind a human
verification gate, a hash-chained audit log with external anchoring, bit-exact replay, and a
fair three-lane benchmark. The dashboard is authenticated and role-based. By default the
platform makes **zero outbound network calls**.

Item-by-item status against all 121 roadmap items: **[docs/GAP_STATUS.md](docs/GAP_STATUS.md)**.

**New in v0.3.0:** a live, 3D dashboard. It adds a **3D digital twin** tab, where the refinery is drawn in
Three.js, tank levels come from x\*, bottleneck glow comes from the shadow prices y\*, and pipe particles
move at the solved flow rates, all streamed live while the solver runs. The benchmark race now streams
charts, a live objective counter, a solver log, a central-path plot and the branch-and-bound tree, and
shows real `nvidia-smi` GPU gauges. A **size-based GPU policy** sends small models to the CPU and states
why. Status of every item from the dashboard upgrade plan and the 3D guide:
**[docs/DASHBOARD_UPGRADE_STATUS.md](docs/DASHBOARD_UPGRADE_STATUS.md)**.

## Quick start

**New laptop / GPU laptop:** follow [SETUP_NEW_LAPTOP.md](SETUP_NEW_LAPTOP.md). It covers `setup_laptop.ps1`, which installs everything and detects the GPU; `run.ps1`, which hosts the platform; and `gpu_check.ps1`, which runs the GPU test list.

```
run.ps1            # Windows  (run.ps1 -Https for TLS with a generated self-signed certificate)
./run.sh           # Linux / macOS  (./run.sh --https)
```
Open http://127.0.0.1:8000. **First boot** creates user `admin` with a one-time PIN, which is
printed in the console and written to `backend/data/BOOTSTRAP_ADMIN.txt`. Sign in, create named
users (Sovereignty & admin tab, or `python backend/manage.py create-user NAME ROLE`), change the
admin PIN, then delete that file.

| role | can |
|---|---|
| operator | propose constraints, approve **non-safety** items, reject, run solves and races |
| supervisor | + approve **safety-tagged** constraints (sulfur, pressure, temperature), promote shadow → production, retire, anchor the audit chain |
| admin | + platform settings, user management, audit export |

Other entry points:
- API docs: `/docs` (OpenAPI/Swagger).
- Health: `/health`.
- Prometheus metrics: `/metrics`.
- Tests: `cd backend && python -m pytest tests -q`. That is 751 tests: 600 are MPS fuzz cases, and 9 real-GPU tests skip without CUDA.

Other install routes:
- Docker: `docker build -t sovereign-optimizer:0.3.0 .` for CPU, or `-f Dockerfile.cuda` for GPU.
- Helm: `deploy/helm/sovereign-optimizer`, with an egress-deny NetworkPolicy.
- systemd: `deploy/systemd/`.

## What maps to which roadmap item

| Roadmap | Where | Notes |
|---|---|---|
| Mehrotra IPM, mixed precision (10) | `sovereign/ipm.py`, `linalg.py` | Upper bounds handled implicitly. FP32 factorization, then FP64 refinement; never FP8 |
| HSD fallback (39) | `sovereign/hsd.py` | Runs when the main IPM fails. Also yields a verified Farkas certificate for infeasible LPs |
| LMS cold start (12) | `ipm.lms_start` | Exactly the doc's formulas |
| Presolve / postsolve (9, 41) | `presolve.py` | Fixed/empty columns, empty rows, **singleton rows → bounds with dual recovery**. Curtis-Reid + Ruiz scaling |
| Ill-conditioning (42, 50) | `presolve.curtis_reid`, `engine.py` | Handles rows scaled over 10 orders of magnitude. Condition estimate per solve, with a UI warning |
| Tolerances (49) | `sovereign/tolerances.py` | Single source of truth |
| B&B (4, 43, 47) | `bnb.py` | Best-first, pseudo-costs, deterministic FIFO tie-break. Never prunes a numerically failed node. Infeasibility proof attached |
| Infeasibility explainer | `infeasibility.py` | Minimum repair, conflict set, single-knob alternatives |
| Fair benchmark (3) | `baselines.py`, race tab | HiGHS + Gurobi CPU + Gurobi GPU are always shown. Missing lanes show "unavailable" with a legend |
| NL gate (5, 15, 94–101) | `nlc/` | Typed IR; validator; relative/time/decimal-comma/homoglyph/injection wording flagged. Safety-tagged items need a supervisor. Parser version pinned in audit. SKU-2 hard-locks cloud mode |
| Security (21–30) | `auth.py`, `app.py` | scrypt PINs, sessions, CSRF, rate limits, WebSocket auth + Origin check, upload caps, security headers, HTTPS option |
| Persistence (26, 31–38) | `store.py`, `audit.py`, `replay.py`, `manage.py` | SQLite WAL. State and audit in **one transaction** (fail-closed). Backups with retention. External chain anchors. Versioned bundles and schema |
| Ops (76–83) | `ops.py`, `config.py`, `app.py` | JSON logs, `/health`, `/metrics`, failure alerts, disk check, graceful shutdown, fail-fast config |
| GPU (54–65) | `device.py`, `scripts/gpu_bench.py`, `tests/test_gpu_real.py` | Boot diagnostic, VRAM-aware CPU fallback. Benchmark and validation scripts ready for the GPU laptop |
| Live / 3D dashboard (v0.3) | `twin.py`, `telemetry.py`, `frontend/src/twin*.js`, `fx.js`, `viz.js` | 3D digital twin driven by x\* / y\*, streaming race visuals, real GPU telemetry. Three.js r128 vendored for air-gapped use |
| GPU size policy (v0.3) | `linalg.NormalSolver._gpu_policy`, `scripts/find_sweet_spot.py` | Auto CPU/GPU choice by size and free VRAM, stated for every solve. The sweet-spot script measures the threshold |
| Supply chain (102–111) | `Dockerfile*`, `deploy/`, `scripts/` | Pinned deps + lock, SBOM, licence gate, signed releases, CI matrix, data purge |

## Honest results (this machine: Windows 11, 12-thread CPU, **no GPU, no Gurobi**)

| Model | Size | Ours | HiGHS | Agreement |
|---|---|---|---|---|
| MRPL-synthetic refinery (MIP) | 69×88, 9 integers | 0.43 s | 0.025 s | 1e-10 rel |
| Random sparse LP | 200×300 | 0.05 s | 0.009 s | 1e-9 |
| Random sparse LP | 800×1200 | 1.4 s | 0.10 s | 2e-8 |
| Random sparse LP | 2000×3000 | 2.3 s | 0.77 s | 3e-9 |

**HiGHS is 3–15× faster on CPU on these sparse models.** The race tab states this plainly (roadmap Sec. 5).

Dense LPs (the `Dense LP … (GPU showcase)` models, same machine, CPU only) are a different picture:

| Dense LP | Ours (CPU) | HiGHS default | HiGHS IPM |
|---|---|---|---|
| 800×1200 | 1.83 s | 3.05 s | 1.97 s |
| 1500×2250 | 7.75 s | 43.7 s | 11.56 s |

### GPU: what may be claimed, and how

The refinery model is 69 rows. On a GPU, the transfer and kernel-launch overhead on a model that size
exceeds the solve itself. That is why the first RTX 4050 demo took 9.72 s against 24.8 ms for HiGHS. In
**auto** mode (the default) models below `SOVEREIGN_GPU_MIN_ROWS` rows now run on the CPU, and the
dashboard says: *"Problem too small for GPU offload — running CPU path (this is the correct engineering
choice, not a limitation)."* Use a Dense LP model for the GPU lane.

On the GPU laptop, `gpu_check.ps1` runs `backend/scripts/find_sweet_spot.py`. That script measures where
the GPU starts to win, the VRAM ceiling and the demo-safe size. It then writes
`docs/GPU_SWEET_SPOT.md` with this sentence, filled in with the **measured** numbers. Nothing in it is
extrapolated:

> Benchmarked on a consumer RTX 4050 Laptop GPU (6 GB), not datacenter hardware. GPU wins over CPU/HiGHS
> above ~[measured] variables on dense LPs; below that we automatically run the CPU path, which is the
> correct engineering choice. A100/H100 performance is a projection, not a measured claim.

Until that file exists, do not quote a GPU speed-up.
Test coverage of the solver package is 87% overall, with 95–100% on the IPM, linear algebra, presolve, HSD and engine.
The NL compiler scores 100% parse accuracy on its 50-item labelled/red-team set, with zero adversarial phrases auto-accepted
([docs/NL_CALIBRATION.md](docs/NL_CALIBRATION.md)). That set was written by the same team, so field data is still needed.

## Known limitations

* **GPU and Gurobi are not verified on real hardware.** The GPU path is tested with a NumPy stand-in for CuPy. On the GPU laptop, run `gpu_check.ps1`, which covers `gpu_bench.py`, `find_sweet_spot.py`, `check_gurobi.py` and `tests/test_gpu_real.py` ([docs/GPU_VALIDATION.md](docs/GPU_VALIDATION.md)).
* **No crossover, no cutting planes, no QP/SOS, single-threaded B&B.** These are roadmap items 40, 44–46, 51 and 52, deferred with reasons in GAP_STATUS.
* **Single instance.** SQLite means one writer; resilience comes from restart plus a persistent volume, not active-active HA.
* **Docker images and the Helm chart are written but not built or deployed here.** Docker Desktop and Helm were not available on this machine.
* **Refinery data is synthetic.**

## Configuration

Every variable is validated at boot; an invalid value refuses to start. `python backend/manage.py config` prints the effective values.

| env var | default | meaning |
|---|---|---|
| `SOVEREIGN_DATA` | `backend/data` | SQLite database, backups, bootstrap file |
| `SOVEREIGN_SKU` | `SKU-2` | `SKU-1` Enterprise Multi-Tenant / `SKU-2` Sovereign Single-Tenant |
| `SOVEREIGN_ALLOW_CLOUD_LLM` | `0` | `1` enables cloud-llm mode; **refused at boot for SKU-2** |
| `SOVEREIGN_DISABLE_GPU` | `0` | `1` forces the CPU path |
| `SOVEREIGN_GPU_MIN_ROWS` | `2000` | auto mode sends a model to the GPU only at or above this many constraint rows. Set it from `docs/GPU_SWEET_SPOT.md` on the GPU machine |
| `SOVEREIGN_LOCAL_LLM_URL` | `http://127.0.0.1:11434/api/chat` | on-prem LLM (Ollama API) |
| `SOVEREIGN_LOCAL_LLM_MODEL` | `llama3.1:8b` | pinned in every audit record |
| `SOVEREIGN_ALLOW_LAN_LLM` | `0` | `1` allows a non-loopback on-prem LLM host (logged as a sovereignty relaxation) |
| `SOVEREIGN_CLOUD_MODEL` | `claude-opus-5-5` | cloud-llm model (SKU-1 only; `pip install anthropic`) |
| `GUROBI_GPU_PARAMS` | `{"Method": 6, "PDHGGPU": 1}` | verify names with `scripts/check_gurobi.py` |
| `SOVEREIGN_ANCHOR_DIR` | unset | off-box / write-once dir for audit-chain anchors |
| `SOVEREIGN_ALERT_WEBHOOK` | unset | optional **on-prem** webhook for failure alerts |
| `SOVEREIGN_ALERT_THRESHOLD` | `3` | consecutive solver failures before an alert |
| `SOVEREIGN_DISK_WARN_GB` | `2` | `/health` degrades below this free space |
| `SOVEREIGN_MAX_UPLOAD_MB` | `50` | MPS/readme upload cap |
| `SOVEREIGN_SESSION_HOURS` | `12` | login session lifetime |
| `SOVEREIGN_HTTPS` | `0` | set by `run -Https`; Secure cookies + HSTS |
| `SOVEREIGN_RATE_PER_MIN` | `30` | expensive calls per user per minute |
| `SOVEREIGN_LOG_FORMAT` | `json` | `json` or `text` |

## Documents

| | |
|---|---|
| [docs/GAP_STATUS.md](docs/GAP_STATUS.md) | all 121 roadmap items: fixed / partial / needs GPU laptop / deferred, with evidence |
| [docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md) | non-technical quick start for control-room users |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | install, backup/retention, anchoring, HA, secrets, updates, purge |
| [docs/SECURITY.md](docs/SECURITY.md) | security model and threat notes |
| [docs/INCIDENT_RESPONSE.md](docs/INCIDENT_RESPONSE.md) | tampered audit log / bad recommendation runbook |
| [docs/DATA_FLOW.md](docs/DATA_FLOW.md) | what leaves the box in each NL-compiler mode |
| [docs/CONVERGENCE.md](docs/CONVERGENCE.md) | algorithmic basis and deviations, for technical diligence |
| [docs/GPU_VALIDATION.md](docs/GPU_VALIDATION.md) | GPU-laptop checklist (items 54–65) |
| [docs/DASHBOARD_UPGRADE_STATUS.md](docs/DASHBOARD_UPGRADE_STATUS.md) | live / 3D dashboard upgrade: what was built, item by item |
| [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) | OS / hardware matrix |
| [docs/TEST_REPORT.html](docs/TEST_REPORT.html) | latest test report (shareable) |
| [backend/tests/README.md](backend/tests/README.md) | which test validates which roadmap item |
| [CHANGELOG.md](CHANGELOG.md) | version history |
