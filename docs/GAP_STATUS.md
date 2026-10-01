# Master Roadmap reconciliation: all 121 items (build v0.2.0)

Status legend:
- **FIXED**: implemented and covered by an automated test or a recorded verification.
- **DONE (v0.1)**: already in the first MVP; re-verified.
- **PARTIAL**: the useful part is built; the remainder is stated.
- **GPU-LAPTOP**: code and tooling are ready; needs a real GPU or Gurobi run (one command each, see GPU_VALIDATION.md).
- **DEFERRED**: consciously not built yet, with the reason.
- **NON-CODE**: business, legal or process work; no software change is appropriate.

Totals by primary status (121 items): FIXED 75 · DONE (v0.1) 6 · PARTIAL 14 · GPU-LAPTOP 12 · DEFERRED 8 · NON-CODE 6.
Evidence: `backend/tests` (741 tests: 733 pass, 0 fail, 8 real-GPU tests skipped on this CPU-only machine) and `docs/TEST_REPORT.html`.

The audit was written from the repository's file listing, not its code. Where its assumption did not match the code, the actual state is noted.

## Part I: strategic, mathematical, systems/legal (1–20)

| # | Item | Status | Evidence / what was done |
|---|---|---|---|
| 1 | Sovereign claim vs CUDA stack | DONE (v0.1) | The 3-tier claim is shown in the product (Sovereignty tab). The CPU FP64 path is always available and the boot diagnostic says which one runs. Tier-3 silicon R&D is NON-CODE. |
| 2 | Competitive analysis (cuOpt, Gurobi GPU) | NON-CODE | Roadmap Sec. 3 table. The product positions on sovereignty, explainability and auditability, not speed. |
| 3 | Three-lane fair benchmark | DONE + GPU-LAPTOP | All three lanes are always shown, with a legend for non-technical viewers (#89). Gurobi lanes need a licence (#56). |
| 4 | MIP in the MVP | FIXED | B&B now has an HSD fallback per node, never prunes a numerically failed node, uses a deterministic tie-break and records an infeasibility proof. `test_mip_and_explain.py`, `test_solver_hardening.py` |
| 5 | NL safety gate | FIXED | v0.1 gate plus supervisor-only safety sign-off, a 5 s undo window, red-team set, injection/relative/time/decimal-comma/homoglyph detection and parser version pinning. `test_nlc.py`, `test_nl_redteam.py` |
| 6 | Business model / GTM | NON-CODE | Roadmap Sec. 7. |
| 7 | Certification pathway | DONE (v0.1) + NON-CODE | The product is advisory-only (no control-loop output) and shadow mode is on by default. IEC 61511 alignment is a Phase-C process. |
| 8 | Resourced plan | NON-CODE | Planning item. |
| 9 | Postsolve / de-scaling | FIXED | v0.1 order kept, plus singleton-row duals restored in postsolve (matches HiGHS to 1e-8). `test_singleton_rows_become_bounds_and_duals_are_restored` |
| 10 | No FP8 | DONE (v0.1) | FP32-minimum factorization with FP64 refinement. `test_mixed_precision_refinement_is_fp64_accurate` |
| 11 | Netlib values never hand-typed | DONE (v0.1) | Reference values are parsed from Netlib's readme ("Load Netlib readme…" button). The STAIR sign discrepancy is flagged in the roadmap update. |
| 12 | LMS cold start | DONE (v0.1) | `test_lms_start_is_strictly_interior` |
| 13 | CVC air-gapped Helm distribution | PARTIAL | `deploy/helm/sovereign-optimizer` has an **egress-deny NetworkPolicy** (only cluster DNS), a non-root read-only pod, a PVC, TLS and GPU toggles. The image moves by `docker save/load`. Written; **not deployed to a cluster** (no Kubernetes or Helm here). |
| 14 | Determinism + replay | FIXED + GPU-LAPTOP | Replay moved to SQLite with a versioned bundle schema. The negative test proves a mismatch is flagged. CPU is bit-exact. The GPU bit-exactness test is written (`test_gpu_real.py::test_gpu_deterministic_replay`). |
| 15 | Two NL modes | FIXED | Plus an SKU-2 hard lock on cloud mode (#99), audit of the LAN relaxation (#96), and automatic fallback with an alert (#98). |
| 16 | Freedom-to-operate study | NON-CODE | Legal; before Series A. The code makes no novelty claim for crossover, SMW or LMS. |
| 17 | Multi-tenant vs single-tenant SKU | PARTIAL | `SOVEREIGN_SKU`. SKU-2 refuses to boot with cloud mode enabled, and the SKU-2 image does not include the Anthropic SDK. There is no shared multi-tenant queue in the code at all, so there is no shared code path to disable. SKU-1 multi-tenant features are not built. |
| 18 | Checkpointing for distributed solves | DEFERRED | Phase-2: the MVP has no multi-GPU/NCCL code (see #61). |
| 19 | TCO table | GPU-LAPTOP | `scripts/gpu_bench.py --gpu-cost-hour X --cpu-cost-hour Y` outputs cost per solve. Real vendor quotes are needed too. |
| 20 | Liability framework | NON-CODE | Legal. The product side (advisory-only, shadow mode, named sign-off, audit) is in place. |

## A. Security & authentication (21–30)

| # | Item | Status | Evidence |
|---|---|---|---|
| 21 | Anyone can type any name | FIXED | `auth.py`: per-user PIN hashed with **scrypt** (stdlib, memory-hard; bcrypt is not needed). Identity comes from the session, never from the request body. `test_security_and_gate_flow` |
| 22 | Plain HTTP | FIXED | `scripts/make_cert.py` (self-signed or CSR for the plant CA) and `run.ps1 -Https` / `run.sh --https`. Secure cookies + HSTS. **Verified:** served over TLS; HSTS, CSP and X-Frame headers present; plain HTTP refused. |
| 23 | No CSRF protection | FIXED | Per-session token, double-submit `X-CSRF-Token` header, and `SameSite=Strict` HttpOnly cookie (own middleware; no extra dependency). Tested: a write without the token returns 403. |
| 24 | No rate limiting | FIXED | Per-user limiter on expensive endpoints and the race WebSocket, per-IP login limiter, and a 5-failure / 15-minute lockout. `test_login_lockout_and_rate_limit` |
| 25 | No RBAC | FIXED | Operator / Supervisor / Admin roles. Safety-tagged approval and promotion need Supervisor+; settings and users need Admin. Enforced in the gate **and** the API. Tests at both levels. |
| 26 | Audit file not locked | FIXED | SQLite with an in-process write lock and a `0600` file mode on POSIX (Windows relies on the service account's profile ACL; documented). |
| 27 | Secrets handling | PARTIAL | `.env`, `keys/` and `certs/` are git-ignored; secrets are never logged; rotation procedure in SECURITY.md. OS-keychain integration is deferred: the SKU-2 build holds no external secrets at all. |
| 28 | Unauthenticated WebSocket | FIXED | Session check plus an Origin check (cross-site WebSocket hijacking) at the handshake; close code 4401. `test_websocket_requires_session` |
| 29 | Upload DoS | FIXED | HTTP 413 above the cap; parser size cap and wall-clock budget; every malformed input becomes a clean `MPSError`. `test_fuzz_mps.py` |
| 30 | Dependency vuln scanning | PARTIAL | `pip-audit --strict` in CI (written, not executed here: no GitHub runner). There is no npm tree; the vendored JS is listed in the SBOM. |

## B. Data persistence & reliability (31–38)

| # | Item | Status | Evidence |
|---|---|---|---|
| 31 | JSON files → SQLite WAL | FIXED | `store.py` (WAL, `synchronous=FULL`, busy timeout). `test_concurrent_race_clients` checks the audit chain after concurrent writes. |
| 32 | Backup / retention | FIXED | `manage.py backup --keep N` (online SQLite backup API); retention policy in OPERATIONS.md. |
| 33 | Disk-full mid-write | FIXED | State and audit entry are written in **one transaction**. On failure nothing is applied or recorded (fail-closed), the API returns 503 and the failure alert fires. `test_storage_failure_is_fail_closed` |
| 34 | Replay schema versioning | FIXED | `schema_version` in bundles with an upgrade on read; database `schema_version` table. `test_replay_reads_v1_bundles` |
| 35 | Log rotation | FIXED (by policy) | The audit chain is a legal ledger and is deliberately never pruned (about 1 KB per event). `manage.py export-audit` archives off-box, backups rotate, and `/health` reports the data size. |
| 36 | External anchoring | FIXED | `manage.py anchor --dest` / "Anchor chain tip" button / `SOVEREIGN_ANCHOR_DIR`. Verification checks anchors on the external medium. `test_external_anchor_detects_full_chain_rewrite` |
| 37 | HA story | PARTIAL | Documented single-writer limit. systemd `Restart=always` plus a `/health` watchdog timer; Kubernetes liveness/readiness probes. Active-active is deferred (it needs a server database). |
| 38 | Migration tooling | FIXED | Numbered, idempotent migrations in `store.py`. Alembic was not adopted for four SQLite tables; switch when a server database lands. |

## C. Numerical / algorithmic (39–53)

| # | Item | Status | Evidence |
|---|---|---|---|
| 39 | HSD | FIXED | `hsd.py` (Xu-Hung-Ye / Andersen-Andersen) runs automatically when Mehrotra fails. It rescues the degenerate-boundary refinery cases that v0.1 reported as "unknown". Matches HiGHS on 20 random LPs run with HSD alone. |
| 40 | Crossover | DEFERRED | Duals are verified KKT-optimal (degenerate multiplicity only). An advisory plan does not need a vertex. About 1 week; next after the GPU validation. |
| 41 | Presolve extensions | PARTIAL | **Singleton rows → bounds with exact dual recovery: FIXED.** Doubleton columns and dual presolve are deferred. |
| 42 | Ill-conditioned stress tests | FIXED | The stress suite (rows scaled over 10 orders of magnitude, near-parallel rows) **found a real weakness**: Ruiz scaling alone is not scale-invariant. Fixed with Curtis-Reid geometric scaling; phase-1 failure no longer reports "infeasible". Real Kennington/Netlib files can be run with "Load MPS…". |
| 43 | MIP infeasibility certificate | FIXED | LP relaxation: Farkas certificate from HSD, **verified in original variable space**. Integer infeasibility: a branch-and-bound exhaustion proof record. `test_hsd_verified_farkas_certificate`, `test_mip_infeasibility_carries_certificate` |
| 44 | Cutting planes | DEFERRED | 1–2 weeks. The refinery MIP closes in under 10 nodes today. |
| 45 | Parallel B&B | DEFERRED | Would break bit-exact replay unless a deterministic parallel scheme is designed. |
| 46 | Warm start in B&B nodes | DEFERRED | IPM warm starts give about 1.3× at best (measured). The right tool is crossover + dual simplex (#40). |
| 47 | Deterministic tie-break | FIXED | Best bound, then FIFO creation order (documented). `test_bnb_tie_break_is_deterministic` |
| 48 | Free / ranged tests | FIXED | `test_free_variables_and_ranged_rows_by_hand`, `test_free_variable_unbounded_direction` |
| 49 | Central tolerances | FIXED | `sovereign/tolerances.py` |
| 50 | Condition-number diagnostic | FIXED | Per-solve estimate, UI warning, and it feeds the failure alert. `test_condition_estimate_reported` |
| 51 | QP | DEFERRED | 2–3 weeks. Blending quality is linearised today (as in PIMS-style LP planning). |
| 52 | SOS / semi-continuous | DEFERRED | Semi-continuous variables can be modelled with a binary today. |
| 53 | Convergence note | FIXED | `docs/CONVERGENCE.md` |

## D. GPU / performance (54–65)

| # | Item | Status | Evidence |
|---|---|---|---|
| 54 | CuPy path never executed | GPU-LAPTOP | The code path runs end to end in tests with a NumPy stand-in for CuPy (`test_gpu_dense_path_and_oom_fallback`). Real run: `pytest tests/test_gpu_real.py`. |
| 55 | CUDA version pin | GPU-LAPTOP | The boot diagnostic and gpu_bench record the CUDA runtime, driver and CuPy versions; fill in COMPATIBILITY.md. `Dockerfile.cuda` pins CUDA 12.6 and CuPy 13.3. |
| 56 | Gurobi GPU params | GPU-LAPTOP | `scripts/check_gurobi.py` reports which parameter names the installed release accepts. |
| 57 | VRAM out-of-memory | FIXED + GPU-LAPTOP | VRAM pre-check and exception fallback to CPU, counted on `/health`. Tested with the stand-in. Test the real limit with `gpu_bench --sizes ...,8000`. |
| 58 | GPU vs CPU factorization | GPU-LAPTOP | `gpu_bench.py` factorization sweep |
| 59 | Crossover size | GPU-LAPTOP | same sweep; reports the first size where the GPU wins |
| 60 | GPU accuracy | GPU-LAPTOP | `gpu_bench.py` accuracy section and `test_gpu_real.py` (1e-7 vs CPU FP64) |
| 61 | NCCL unverified | FIXED (label) | There is **no NCCL / multi-GPU code in this codebase**. It is a Phase-2 roadmap item and is labelled as such everywhere. |
| 62 | Thermal throttling | GPU-LAPTOP | `gpu_bench.py --minutes 30`; logs nvidia-smi clocks and temperatures and flags slowdowns above 15%. |
| 63 | Silent CPU fallback | FIXED | Boot diagnostic line, a `/health` "compute" field, and a header badge tooltip giving the reason. |
| 64 | Screen capture | GPU-LAPTOP | Manual; checklist in GPU_VALIDATION.md. |
| 65 | TCO numbers | GPU-LAPTOP | `gpu_bench.py` cost inputs (see #19). |

## E. Testing & validation (66–75)

| # | Item | Status | Evidence |
|---|---|---|---|
| 66 | Coverage | FIXED | pytest-cov. **87% overall**; IPM 95%, linalg 96%, presolve 97%, HSD 96%, engine 100%, B&B 88%. The CI job publishes coverage.xml. |
| 67 | MPS fuzzing | FIXED | 600 seeded mutation cases plus random-bytes and limit tests (no Hypothesis dependency). It drove parser hardening (undefined rows, NaN coefficients, bad bounds). |
| 68 | Concurrent WebSocket load | PARTIAL | 4 concurrent authenticated race clients plus an audit-chain integrity check. Locust-scale load testing is deferred. |
| 69 | Golden regression | FIXED | `tests/golden.json` plus `test_golden_objectives` |
| 70 | CI | PARTIAL | `.github/workflows/ci.yml` (Windows + Linux, Python 3.11 and 3.13, coverage, pip-audit, licence gate, SBOM, report). Written; runs once the repository is on GitHub. |
| 71 | E2E UI test of the gate | PARTIAL | An API-level end-to-end test drives the whole sign-off flow. The browser flow (login, RBAC block, supervisor approval, **undo within 5 s leaves the proposal pending**) was verified manually in a real browser in this build. A Playwright suite is deferred (not available offline). |
| 72 | One-byte tamper test | FIXED | One byte, a deleted row and a full-chain rewrite are all detected. |
| 73 | Replay must fail on mismatch | FIXED | `test_replay_fails_loudly_on_mismatch` (also a tampered stored model) |
| 74 | Performance regression | FIXED | `test_performance_budget` |
| 75 | Hand-verifiable case | FIXED | Two-component sulfur blend: objective 46000/9, dual 200/9, derived by hand in the test docstring. |

## F. Observability & ops (76–83)

| # | Item | Status | Evidence |
|---|---|---|---|
| 76 | Structured logging | FIXED | JSON log lines (stdlib formatter, no structlog dependency) |
| 77 | /health | FIXED | Database integrity, audit chain + anchors, disk, compute, GPU fallbacks, solver alert and LLM state; "degraded" with reasons. |
| 78 | /metrics | FIXED | Prometheus text: requests, latency, solves by status, solve time, in-flight, audit length, disk, GPU. |
| 79 | Alerting | FIXED | N consecutive failures or ill-conditioned solves produce an ERROR log, an audit event, `/health` degraded, and an optional **on-prem** webhook. |
| 80 | Process supervision | FIXED | `deploy/systemd/*` (hardened unit plus a health-watchdog timer). |
| 81 | Graceful shutdown | FIXED | Lifespan drain (in-flight solves finish; new POSTs get 503), uvicorn `--timeout-graceful-shutdown`, and Kubernetes `terminationGracePeriodSeconds`. `service.stopped` is audited. |
| 82 | Disk monitoring | FIXED | `/health` + `/metrics`, with the threshold set by `SOVEREIGN_DISK_WARN_GB`. |
| 83 | Config validation | FIXED | `sovereign/config.py` (pydantic): refuses to boot on bad values and warns on unknown variables. `test_config_fails_fast` |

## G. Dashboard UX (84–93)

| # | Item | Status | Evidence |
|---|---|---|---|
| 84 | Accessibility | PARTIAL | Manual pass: form labels, ARIA roles (tabs, meter, alert, radiogroup), visible focus, keyboard-operable rows, a high-contrast theme. An automated axe-core audit is deferred (not available offline). |
| 85 | Responsive | DONE (v0.1) | Verified at a 375 px phone width. |
| 86 | High-contrast mode | FIXED | Toggle in the header (7:1+ contrast, thicker focus rings). Verified in the browser. |
| 87 | Status distinction | FIXED | Pending / approved / rejected / blocked banners plus shadow/production chips; "not applied to any plan yet" wording. |
| 88 | Undo window | FIXED | 5 s confirm-with-timeout; nothing reaches the server or the audit log until it expires. Verified in the browser. |
| 89 | Lane legend | FIXED | Tooltips plus a "What do the lanes mean?" panel. |
| 90 | Reconnect handling | FIXED | Health-poll "reconnecting…" banner; a dropped race keeps its partial results and explains how to retry. |
| 91 | i18n | FIXED (scaffold) | `frontend/src/i18n.js`: English complete, Hindi for navigation and key actions. |
| 92 | Print plan | FIXED | Print stylesheet with user, time, replay id and model hash for shift handover. |
| 93 | Warm-start badge | FIXED | "N× fewer iterations with warm start (measured on this change)". |

## H. NL compiler / LLM safety (94–101)

| # | Item | Status | Evidence |
|---|---|---|---|
| 94 | Local LLM accuracy | GPU-LAPTOP | `python -m sovereign.nlc.evaluate sovereign-local-llm` benchmarks any Ollama model (8B vs a quantised 34B) on the labelled set. |
| 95 | Red-team testing | FIXED | 50-item labelled set including injection, SQL, decimal/unit slips, homoglyphs, negation, relative, time-bounded, compound, ambiguous and unknown-entity phrasings. `test_nl_redteam.py` fails the build if any is auto-accepted. |
| 96 | LAN LLM relaxation logged | FIXED | `sovereignty.relaxed` audit event at boot; every proposal carries the relaxation flag. |
| 97 | Confidence calibration | FIXED (caveat) | `docs/NL_CALIBRATION.md`: 100% accuracy in every populated bucket. The set is small and in-house; recalibrate on real operator phrasing during shadow mode. |
| 98 | Ollama down mid-shift | FIXED | Per-request fallback to the offline parser, an ALERT note, an `nl.llm_unavailable` audit event, and `/health` degraded. `test_llm_mode_falls_back_offline_when_unavailable` |
| 99 | Cloud mode not hard-blocked | FIXED | SKU-2 refuses to start with it enabled; the runtime refuses it; the SKU-2 image does not ship the SDK. Tested. |
| 100 | LLM version reproducibility | FIXED | Parser/model id (`rules:1.1.0`, `local:<tag>`, `cloud:<id>`) pinned in every proposal, constraint and audit entry. A "needs re-verification" list appears after a model change. |
| 101 | Compound sentences | FIXED | Rejected with a "enter them one at a time" message. Tests in the NL and red-team suites. |

## I. Packaging & DevOps (102–111)

| # | Item | Status | Evidence |
|---|---|---|---|
| 102 | Docker images | PARTIAL | `Dockerfile` (CPU, non-root, health check) and `Dockerfile.cuda`. Written; **not built here** (Docker daemon not running on this machine). |
| 103 | Pinned versions | FIXED | Exact pins in `requirements.txt`, a full transitive `requirements.lock` (`supply_chain.py lock`), and `requirements-dev.txt`. |
| 104 | Production installer | DEFERRED | The pilot artifacts are the signed release zip, the Docker image and the systemd unit. A PyInstaller/MSI build waits for pilot-site IT requirements. |
| 105 | Compatibility matrix | PARTIAL | `docs/COMPATIBILITY.md`: one verified row (Windows 11 / Python 3.13 / CPU); Linux and GPU rows to fill in from CI and the laptop. |
| 106 | Air-gapped update procedure | FIXED | `scripts/release.py` (Ed25519-signed SHA256SUMS; tamper detected in test) plus `docs/OPERATIONS.md`. |
| 107 | Licence check | FIXED | `supply_chain.py licenses`: fails on copyleft or unknown licences; all 21 components pass. |
| 108 | SBOM | FIXED | `sbom.cdx.json` (CycloneDX 1.5, including the vendored React/htm). |
| 109 | Env vars vs README | FIXED | `manage.py config`, an unknown-variable warning at boot, and the `check_env_docs.py` CI gate (currently in sync). |
| 110 | Multi-platform CI | PARTIAL | Matrix written (see #70). |
| 111 | Data purge | FIXED | `manage.py purge --yes` plus the documented off-box steps. |

## J. Documentation, compliance & business (112–121)

| # | Item | Status | Evidence |
|---|---|---|---|
| 112 | Swagger /docs | FIXED (caveat) | `/docs` and `/openapi.json` are linked from the Sovereignty tab. FastAPI's Swagger page loads its JS from a CDN, so on an air-gapped site use `/openapi.json`. |
| 113 | Exportable test report | FIXED | `docs/TEST_REPORT.html` (`scripts/test_report.py`, no plugin). |
| 114 | Changelog | FIXED | `CHANGELOG.md` |
| 115 | Licence | PARTIAL | A proprietary "all rights reserved" placeholder plus third-party notices. **The proprietary vs open-core decision is yours.** |
| 116 | Reconcile into the roadmap | FIXED | Master roadmap docx updated with a status column and a "Build Reconciliation" section. |
| 117 | Incident response | FIXED | `docs/INCIDENT_RESPONSE.md` |
| 118 | Operator guide | FIXED | `docs/OPERATOR_GUIDE.md` |
| 119 | Data-flow diagram | FIXED | `docs/DATA_FLOW.md` |
| 120 | Tests → roadmap map | FIXED | `backend/tests/README.md` |
| 121 | External review | NON-CODE | Scope proposal in `docs/SECURITY.md` §External review. Budget and schedule it before the funding conversation. |
