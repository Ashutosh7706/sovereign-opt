# Changelog

Versions track the roadmap documents: v0.1 = MVP from Roadmap v1.3, v0.2 = Master Roadmap hardening, v0.3 = Dashboard Advanced Live Upgrade Plan + 3D digital twin guide.

## 0.3.0 (live 3D dashboard build: Dashboard_Advanced_Live_Upgrade_Plan + refinery_3d_digital_twin)
### Added
- **3D digital twin tab (Three.js r128, vendored for air-gapped use).**
  - The refinery is drawn as crude tanks → CDU → Reformer / FCC / DHDS-1 / DHDS-2 → product tanks.
  - Tank levels come from x\*. Bottleneck glow comes from the shadow prices y\* in $/bbl: cyan = slack, green = active, amber > $2, red ≥ $8 with a pulse.
  - Pipe particles move at the solved flow rates.
  - Controls: click for details, drag to rotate, scroll to zoom, auto-rotate, reset view.
  - Streams over `/ws/twin` while the solve runs: first the live root-LP iterate, then each integer plan found, then the final plan with exact duals.
  - A "where the plan is constrained" ranking sits beside the view. There is a fallback message when WebGL is off.
- **Live race:**
  - true per-iteration streaming;
  - an animated objective counter in model units;
  - ranked racing bars;
  - a typewriter solver log;
  - a central-path plot of live (log x, log s) pairs against x·s = μ;
  - a live branch-and-bound tree (open / branched / pruned / infeasible / integral);
  - an "optimal" burst;
  - a HiGHS-IPM lane (the like-for-like IPM comparison);
  - a device selector (Auto / Force GPU / CPU only).
- **Real hardware telemetry:** `/ws/telemetry` streams `nvidia-smi` GPU load, VRAM, temperature, SM clock and power at 2 Hz, plus the solver process's CPU share. When there is no NVIDIA driver it says so, and nothing is simulated.
- **Audit-chain pulse:** the header badge flashes when a new hash-chained entry is written, and shows the entry count.
- **3D look:**
  - a perspective-grid backdrop;
  - panel depth;
  - a rotating logo cube;
  - tilt cards.
  - All motion is disabled under `prefers-reduced-motion` and in the high-contrast theme.
- **`scripts/find_sweet_spot.py`:** a dense-LP size sweep of ours-GPU vs ours-CPU vs HiGHS vs HiGHS-IPM.
  - It stops before the VRAM ceiling and logs peak temperature and minimum SM clock.
  - It recommends `SOVEREIGN_GPU_MIN_ROWS` and a demo-safe size.
  - It writes the README GPU sentence with measured numbers only.
  - It is wired into `gpu_check.ps1` as step 5/7.
- Dense-LP "GPU showcase" models (`dense-m/l/xl`).

### Changed
- **Size-based GPU policy (plan Sec. 1).**
  - In auto mode, a model goes to the GPU only when it has ≥ `SOVEREIGN_GPU_MIN_ROWS` rows (default 2000) and its normal matrix fits in 80 % of free VRAM.
  - Every solve reports its device decision, which the dashboard shows.
  - The GPU path now assembles the normal matrix on the device, uploads A once, and warms up its kernels at boot.
  - This is the fix for the 9.72 s vs 24.8 ms refinery demo on the RTX 4050.
- `gpu_bench.py` and `tests/test_gpu_real.py` force the GPU path explicitly, so the size policy does not quietly turn their small GPU cases into CPU runs. There is a new real-GPU test for the auto policy.
- Dashboard files are served with `Cache-Control: no-cache` (ETag revalidation), so an upgrade shows up without a hard refresh.

### Fixed
- The first telemetry message was empty until the first sample was taken. Static facts are now sent at once.

## 0.2.0 (pilot-hardening build, Master Roadmap items 1–121)
### Added
- **Homogeneous self-dual IPM fallback** with verified Farkas infeasibility certificates, and a B&B infeasibility proof record.
- **Security:**
  - operator accounts (scrypt PINs), roles (operator / supervisor / admin), sessions, CSRF, rate limits and login lockout;
  - authenticated WebSocket with an Origin check, and security headers;
  - HTTPS tooling.
- **Persistence:** SQLite WAL store, with state and audit written in one transaction (fail-closed); schema migrations; versioned replay bundles; external audit-chain anchoring; backup with retention; purge (`manage.py`).
- **Ops:** JSON logs, `/health`, `/metrics`, failure alerts, disk check, graceful shutdown, validated configuration (fail fast), GPU boot diagnostic and VRAM-aware CPU fallback.
- **NL gate:**
  - supervisor-only safety sign-off, and a 5 s undo window;
  - detection of relative, time-bounded, decimal-comma, homoglyph and instruction/injection wording;
  - parser version pinning with a re-verification list, and LAN-LLM relaxation auditing;
  - automatic LLM→offline fallback with an alert, and a hard SKU-2 cloud lock;
  - a labelled red-team set and a calibration report.
- **Numerics:** singleton-row presolve with dual recovery, Curtis-Reid scaling, central tolerance module, condition-number estimate.
- **UI:** login, role-aware actions, high-contrast theme, print view, lane legend, reconnect banner, warm-start badge, i18n scaffold (English + Hindi), user admin.
- **Tests (77 → 741):** HSD, ill-conditioning stress, MPS fuzzing (600 cases), golden regression, performance budget, a hand-verifiable blend, tamper/anchor/negative-replay tests, API security, concurrent WebSocket clients, and a GPU code path via a CuPy stand-in (real-GPU tests skip without CUDA).
- **Packaging:**
  - Docker (CPU + CUDA) and a Helm chart with an egress-deny NetworkPolicy;
  - systemd units;
  - pinned requirements + lockfile, SBOM, licence gate, and signed air-gapped releases;
  - a CI workflow and an HTML test report.
- **Docs:** gap status (all 121 items), operator guide, operations, security, incident response, data flow, convergence note, GPU validation checklist, compatibility matrix.

### Fixed
- Badly scaled LPs (rows spanning 10 orders of magnitude) failed to converge. Curtis-Reid scaling now precedes Ruiz.
- A failed phase-1 check was reported as "infeasible". It now reports "feasibility unknown".
- Presolve called an LP "unbounded" from an empty column before checking that the rest was feasible.
- Degenerate-boundary refinery MIPs that v0.1 reported as "unknown" now solve to optimality via the HSD fallback.

### Changed
- The operator name is no longer typed into a text box; identity comes from the login session.
- Persistence moved from JSON/JSONL files to SQLite. v0.1 data is not migrated: start with a fresh data directory.

## 0.1.0 (MVP from Roadmap v1.3)
- Mehrotra IPM (LMS start, mixed precision, SMW), presolve/Ruiz/postsolve, heuristic B&B, elastic infeasibility explainer, NL compiler with verification gate, hash-chained audit, bit-exact replay, three-lane race, and dashboard.
