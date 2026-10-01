# GPU-laptop validation checklist (roadmap items 54–65, 94)

This build was made on a machine with **no NVIDIA GPU and no Gurobi**. Everything below is
prepared so that each item takes one command on the GPU laptop. Tick the items off, and
replace "not tested" wording in the README and roadmap with the numbers produced.

| Step | Command (from `backend/`) | Closes |
|---|---|---|
| 0 | `nvidia-smi` → note the driver version and the max CUDA version | 55 |
| 1 | `pip install cupy-cuda12x` (use the major version from step 0) | 54 |
| 2 | Start the server. The boot log must say `compute: GPU found - <model> (<VRAM> GB) ...` (if it says "no GPU", the reason is on the same line) | 63 |
| 3 | `python -m pytest tests -q`: the whole suite, now **including** `tests/test_gpu_real.py` (8 tests: GPU vs CPU FP64 objectives to 1e-7, refinery MIP on GPU, bit-exact GPU replay) | 54, 60, 14 |
| 4 | `python scripts/gpu_bench.py --sizes 200,500,1000,2000,3000,4000,6000,8000` → the factorization sweep (CPU vs GPU, same matrix) and the size where the GPU starts winning | 58, 59 |
| 5 | Keep raising `--sizes` until the log shows a *GPU->CPU fallback* on `/health` (`gpu_fallbacks`). The solve must still finish on CPU | 57 |
| 6 | `python scripts/gpu_bench.py --minutes 45` with the laptop on mains power and on battery. Look for `throttling_suspected` and the slowdown % | 62 |
| 7 | `python scripts/gpu_bench.py --gpu-cost-hour <laptop or cloud $/h> --cpu-cost-hour <$/h>` → the TCO inputs for roadmap item 19 / 65 | 19, 65 |
| 8 | `pip install gurobipy`, install a free academic or trial licence, then `python scripts/check_gurobi.py`. Record which GPU parameter names were accepted and set `GUROBI_GPU_PARAMS` | 56, 3 |
| 9 | In the UI, run the Benchmark race with all four lanes. **Screen-record it** as a pitch backup | 64 |
| 10 | `ollama pull llama3.1:8b` and a larger quantised model that fits in VRAM, then `python -m sovereign.nlc.evaluate sovereign-local-llm --write ../docs/NL_EVAL_LOCAL_8B.md` (repeat per model via `SOVEREIGN_LOCAL_LLM_MODEL`) | 94 |
| 11 | Fill in the GPU row of `docs/COMPATIBILITY.md` (OS, driver, CUDA, CuPy, GPU, VRAM) | 55, 105 |
| 12 | `python scripts/find_sweet_spot.py`: dense-LP sweep of ours-GPU vs ours-CPU vs HiGHS vs HiGHS IPM, stopping at the VRAM ceiling. Set `SOVEREIGN_GPU_MIN_ROWS` to the recommended value and use the demo-safe size for the GPU lane (dashboard plan Sec. 5) | 57, 58, 59 |
| 13 | In the UI, race a **Dense LP (GPU showcase)** model with Device = Auto. The banner must say "GPU path", and the GPU gauges must move. Then race the refinery: the banner must say "Problem too small for GPU offload" | Sec. 1 |

Outputs land in `docs/GPU_RESULTS.md` / `docs/gpu_results.json` and `docs/GPU_SWEET_SPOT.md` / `docs/gpu_sweet_spot.json`. `gpu_check.ps1` runs rows 1–8 and 12 in one go.

**Not testable on one laptop:** multi-GPU / NCCL (item 61). The codebase contains no multi-GPU
code; it is a Phase-2 roadmap item and must stay labelled "not built" in the pitch.
