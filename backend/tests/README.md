# Tests → roadmap items (roadmap #120)

Run: `python -m pytest tests -q` (from `backend/`). Oracle: HiGHS via SciPy, an independent solver.

| File | Tests | Validates roadmap items |
|---|---|---|
| `test_lp.py` | 48 | 9 postsolve · 10 mixed precision · 12 LMS · SMW dense columns · warm start · presolve infeasibility naming. 40 random LPs vs HiGHS, including infeasible and unbounded cases |
| `test_solver_hardening.py` | 43 | 39 HSD · 41 singleton presolve + dual recovery · 42 ill-conditioning stress · 43 Farkas / MIP certificates · 47 tie-break · 48 free/ranged · 50 condition estimate · 54/57 GPU path + VRAM fallback (CuPy stand-in) · 69 golden · 74 performance budget · 75 hand-verifiable blend |
| `test_mip_and_explain.py` | 5 | 4 B&B vs HiGHS · infeasibility explainer and repair · never a false "infeasible" at the degenerate boundary |
| `test_audit_replay_mps.py` | 9 | 11 MPS / Netlib readme · 14 bit-exact replay · 34 bundle schema · 36 external anchors · 72 one-byte / deletion tamper · 73 replay must fail on mismatch |
| `test_fuzz_mps.py` | 602 | 29 upload limits · 67 parser fuzzing (600 seeded mutations + random bytes) |
| `test_nlc.py` | 19 | 5 verification gate · 25 RBAC · 33 fail-closed storage · 98 LLM fallback · local-LLM loopback guard · 101 compound sentences |
| `test_nl_redteam.py` | 2 | 95 red team · 97 calibration · 101 compound (labelled set `nlc_eval.jsonl`) |
| `test_api.py` | 5 | 21 auth · 23 CSRF · 24 rate limit + lockout · 25 RBAC over HTTP · 28 WebSocket auth · 29 upload caps · 68 concurrent WebSocket clients · 71 API-level gate E2E · 77/78 health & metrics · 83 config fail-fast · 99 SKU-2 cloud lock |
| `test_gpu_real.py` | 8 (skip without CUDA) | 54 real CuPy run · 60 GPU accuracy · 14 GPU bit-exact replay |

Data files:
- `golden.json` holds the regression objectives. Regenerate only deliberately; see `test_golden_objectives`.
- `nlc_eval.jsonl` is the labelled NL set. Add every real-world misparse here.
