# Setting up Sovereign Optimizer on the GPU laptop

This guide targets a Windows 11 laptop with a **13th Gen Intel Core i7-13650HX** (14 cores / 20 threads) and an
NVIDIA GeForce RTX GPU. It installs the exact platform built so far (solver, dashboard, audit log, NL
compiler and all tests), hosts it for yourself or your team, and runs the GPU validation list.

Time needed: about 30 minutes, plus 10–60 minutes for the GPU tests. You need internet during setup
(section 8 covers a fully offline install).

---

## 1. What to copy to the laptop

Put these on a USB stick or cloud drive:

| File | Why |
|---|---|
| `Sovereign_Optimizer_v0.2.zip` | the whole platform: code, dashboard, tests, scripts, docs |
| `Sovereign_Optimization_MASTER_ROADMAP_v0.2.docx` | the roadmap with the build status of all 121 items |
| `GAP_STATUS.md` | item-by-item evidence (also inside the zip, under `docs/`) |
| `SETUP_NEW_LAPTOP.docx` (this guide) | also inside the zip as `SETUP_NEW_LAPTOP.md` |

Nothing else is needed: no database, no model files and no licence keys. The refinery model is built into
the code, and the first start creates a fresh, empty database.

## 2. Prepare the laptop (one time, about 10 minutes)

1. **Plug in the charger** and set the laptop to maximum performance:
   - Windows Settings → System → Power → Power mode → **Best performance**.
   - In the vendor app (Lenovo Vantage "Performance", Fn+Q; ASUS Armoury Crate "Turbo"; HP OMEN "Performance"), pick the highest mode.
   - On battery, laptop GPUs run far slower, so results would be misleading.
2. **Update the NVIDIA driver** (GeForce Experience / NVIDIA App, or nvidia.com/drivers). Then open PowerShell and run:
   ```
   nvidia-smi
   ```
   Note three things from the top box:
   - the **GPU name** (e.g. "NVIDIA GeForce RTX 4060 Laptop GPU");
   - the **memory** (e.g. 8188MiB = 8 GB VRAM);
   - **"CUDA Version: 12.x"**.

   If `nvidia-smi` is not found, the driver is missing. Install it first.
3. **Install Python 3.12** from python.org (Windows installer, 64-bit). On the first screen, **tick "Add python.exe to PATH"**. Python 3.11 or 3.13 also work, but 3.12 has the best GPU-library support.
4. **Unzip to a short folder:** right-click the zip → Extract All → `C:\sovopt`.

   Keep the path short. Windows limits file paths to 260 characters, and deep folders such as `Downloads\Telegram Desktop\...` can break the install.

After extracting, `C:\sovopt\sovereign-opt\` contains `setup_laptop.ps1`, `run.ps1`, `gpu_check.ps1`, `README.md`, `backend\`, `frontend\` and `docs\`.

## 3. Install (one command, about 5–10 minutes)

Open **PowerShell** (Start → type PowerShell) and run:
```
cd C:\sovopt\sovereign-opt
powershell -ExecutionPolicy Bypass -File setup_laptop.ps1
```
What it does, in order:
1. Finds Python 3.12 and creates a private environment in `.venv`. Your system Python is left untouched.
2. Installs the exact pinned versions of NumPy, SciPy, FastAPI and the other dependencies.
3. **Detects the NVIDIA GPU**, installs the matching CuPy (`cupy-cuda12x` for a CUDA 12 driver), and proves the GPU works by running a Cholesky factorisation on it.
4. Checks the configuration.
5. Runs the **full test suite** (about 1–2 minutes) and writes `docs\TEST_REPORT.html`.

Expected ending:
```
733 passed ... (and, with a working GPU, the 8 GPU tests also pass instead of being skipped)
GPU path: ENABLED
```
Everything is logged to `setup_log.txt`.

**If it says "CuPy is installed but cannot run CUDA kernels":**
1. Install the **NVIDIA CUDA Toolkit 12.x** from developer.nvidia.com/cuda-downloads (Windows → x86_64 → 11 → exe local).
2. Close PowerShell, open a new window, and run `setup_laptop.ps1` again.

The platform still works on the CPU in the meantime.

## 4. Start it and sign in

```
cd C:\sovopt\sovereign-opt
powershell -ExecutionPolicy Bypass -File run.ps1
```
Open **http://localhost:8000** in Chrome or Edge.

**First start only:** the console prints a one-time admin PIN, also saved in
`backend\data\BOOTSTRAP_ADMIN.txt`. Sign in as `admin` with that PIN, then:
1. **Sovereignty & admin** tab → create named users:
   - an `operator` for daily use;
   - a `supervisor` for sulfur/pressure/temperature sign-offs;
   - optionally a second `admin`.
2. **Change my PIN** (bottom of the same tab) → give admin a new PIN.
3. Delete `backend\data\BOOTSTRAP_ADMIN.txt`.

Check that the header shows the green **GPU <your card>** badge. If it shows "CPU fallback", hover over it for the reason.

Stop the server with **Ctrl+C** in the PowerShell window.

## 5. Try the full demo flow (5 minutes)

1. **Benchmark race** tab → Start race on the refinery MIP, with Device set to **Auto**.
   - The Sovereign lane and HiGHS must show the same objective (1,704.64).
   - The Gurobi lanes show "unavailable" unless you install Gurobi (section 7). "HiGHS (IPM)" shows "not applicable" for this integer model.
   - The blue banner must say *"Problem too small for GPU offload — running CPU path (this is the correct engineering choice, not a limitation)"*. The refinery model has only 69 rows, and sending it to the GPU is what made the first demo take 9.72 s.
   - Watch these update live while it runs: the objective counter, the solver log, the central-path plot, the branch-and-bound tree and the GPU gauges.
2. **GPU lane:** choose **Dense LP 3000×4500 (GPU showcase)**, or the demo-safe size that `gpu_check.ps1` reports (section 7), and Start race again.
   - The banner should now say **GPU path**, and the GPU load gauge should climb.
   - Compare the Sovereign lane with **HiGHS (IPM)**; that is the like-for-like comparison.
3. **3D digital twin** tab: it solves the plan on first open and animates live.
   - Tanks fill to the solved levels, and pipe particles move at the solved flow rates.
   - The CDU glows **red**: it is the bottleneck at about $10/bbl.
   - Click it for details. Drag to rotate, and scroll to zoom.
4. **Constraint compiler** (as operator) → `Tank 3 sulfur limit ≤ 0.2%` → it needs a supervisor.
5. Sign out, sign in as the supervisor, and approve it. You have 5 seconds to press Undo.
6. **Refinery plan** → the production and shadow plans appear side by side (the shadow plan is about −298 $k/day).
7. **Audit & replay** → the chain is intact. The audit badge at the top flashes whenever a new entry is chained. Press Replay on a bundle and check for **bit-exact match**.

## 6. Let other people on the network use it

1. Start it for the network, with encryption:
   ```
   powershell -ExecutionPolicy Bypass -File run.ps1 -Lan -Https
   ```
   The first run creates a certificate for this laptop. The console prints the address to share, for example `https://192.168.1.23:8000`.
2. Allow the port through Windows Firewall (once; run PowerShell **as Administrator**):
   ```
   New-NetFirewallRule -DisplayName "Sovereign Optimizer" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```
   Set the Wi-Fi/LAN network to **Private** (Settings → Network → your network → Private). Do not open the port on public networks.
3. Other devices will see a certificate warning the first time, because the certificate is self-signed. Choose "Advanced → continue". For a permanent setup, install `certs\server.crt` as trusted on those devices, or have IT issue a certificate (`python backend\scripts\make_cert.py --csr`).
4. **Keep it running after reboots:**
   - Task Scheduler → Create Task → "Run whether user is logged on or not".
   - Trigger: *At startup*.
   - Action: `powershell.exe -ExecutionPolicy Bypass -File C:\sovopt\sovereign-opt\run.ps1 -Lan -Https`.
   - Start in: `C:\sovopt\sovereign-opt`.
5. **Backups:** `.venv\Scripts\python backend\manage.py backup --keep 30`. Put it in a daily scheduled task; details in `docs\OPERATIONS.md`.

## 7. GPU validation (roadmap items 54–65, 94)

Run everything in one go:
```
powershell -ExecutionPolicy Bypass -File gpu_check.ps1
```
That takes about 20–40 minutes. It includes a 5-minute heat test and the **sweet-spot sweep**. Plug in the charger and choose the *Performance* power mode first. For the full 45-minute heat test, and cost-per-solve numbers, use:
```
powershell -ExecutionPolicy Bypass -File gpu_check.ps1 -Minutes 45 -GpuCostHour 0.35 -CpuCostHour 0.10
```
It produces a folder `gpu_results_<date-time>\` containing:
- `nvidia-smi.txt` and `device.txt`: the exact GPU, driver, CUDA and CuPy versions (items 55, 63);
- `pytest.txt` and `TEST_REPORT.html`: the whole suite, including the real-GPU tests (items 54, 60, 14);
- `GPU_RESULTS.md` and `gpu_results.json`:
  - CPU vs GPU speed at sizes 200 → 12,000 rows, in FP64 and FP32;
  - the size where the GPU starts to win;
  - GPU accuracy;
  - the heat/slow-down result;
  - cost inputs (items 58, 59, 60, 62, 65, 19).
- `GPU_SWEET_SPOT.md` and `gpu_sweet_spot.json`: the same dense LPs solved four ways (ours-GPU, ours-CPU, HiGHS, HiGHS IPM). They give:
  - **where this GPU starts to win**;
  - **the VRAM ceiling** (the size at which the sweep stops);
  - **the demo-safe size**;
  - peak temperature and SM clock at each size;
  - the README sentence, filled in with the measured numbers.
- `gurobi.txt` and `NL_EVAL_LOCAL.md`, if those optional parts are installed (items 56, 94).

**After the run, set the measured threshold** so that Auto mode uses the GPU exactly where it wins. Put the number from `GPU_SWEET_SPOT.md` in place of 3000:
```
$env:SOVEREIGN_GPU_MIN_ROWS = "3000"
powershell -ExecutionPolicy Bypass -File run.ps1
```
To make it permanent: `setx SOVEREIGN_GPU_MIN_ROWS 3000`, then open a new PowerShell window.

**What you may say in the pitch** is only what `GPU_SWEET_SPOT.md` measured. For example: *"Benchmarked on a consumer RTX 4050 Laptop GPU (6 GB), not datacenter hardware. GPU wins over CPU/HiGHS above ~N variables; below that we automatically run the CPU path, which is the correct engineering choice. A100/H100 performance is a projection, not a measured claim."* Do not extrapolate.

**Send the whole `gpu_results_...` folder back.** Its numbers replace every "not tested on GPU" line in the roadmap.

What to expect on this hardware (so the results are read correctly):
- **FP64 vs FP32 on GeForce RTX cards:** they do double-precision (FP64) maths at only ~1/64 of their single-precision speed. On small and medium problems the i7-13650HX CPU can beat the GPU in FP64. The GPU should do better in the FP32 phase, which the solver uses while it is far from optimal. The benchmark reports both honestly: quote whichever is true, not what we hoped.
- **VRAM limit test (item 57):** the solver needs roughly 8 × (3m² + m·n) bytes of VRAM for m rows. With the RTX 4050's 6 GB, the ceiling is expected around 10,000–11,000 rows; `find_sweet_spot.py` measures it and stops before it. Above that ceiling, Auto mode runs on the CPU and says why. To trigger the automatic GPU→CPU fallback on purpose, add a size just above that, e.g. `gpu_check.ps1 -Sizes "1000,4000,8000,12000,16000"` (needs 16 GB of system RAM). A result of `cpu_dense` in the "GPU path used" column means the fallback happened. That is the expected, correct behaviour.
- **Heat test:** laptop GPUs slow down when hot. Run it on the charger, in performance mode, on a hard surface.

Manual items still to do:
- **Screen-record** one benchmark race (Win+Alt+R starts Xbox Game Bar recording) as a pitch backup (item 64).
- Fill the "GPU laptop" row in `docs\COMPATIBILITY.md` from `device.txt` (items 55, 105).

### Optional add-ons
- **Gurobi lanes (item 56):**
  1. Run `.venv\Scripts\python -m pip install gurobipy`. The pip package includes a free size-limited licence (about 2,000 variables and constraints). That is enough for the refinery model and the 200×300 LP.
  2. For larger models, or if GPU mode is refused, get a free academic or trial licence at gurobi.com and install it following Gurobi's instructions.
  3. Re-run `gpu_check.ps1`. `gurobi.txt` shows the licence status and which GPU parameter names your Gurobi version accepts.
- **Offline local LLM (item 94):**
  1. Install Ollama (ollama.com).
  2. Pull a model that fits your VRAM: `ollama pull llama3.1:8b` for 8 GB, or `ollama pull llama3.2:3b` for 6 GB.
  3. Run `$env:SOVEREIGN_LOCAL_LLM_MODEL="llama3.1:8b"`, then `gpu_check.ps1`. `NL_EVAL_LOCAL.md` shows how that model scores against the 50 test sentences.
  4. To use it in the app, an admin selects "Sovereign · on-prem open-weight LLM" in Sovereignty & admin.

## 8. Fully offline (air-gapped) install

On any internet-connected Windows PC with the same Python version:
```
pip download -r requirements-dev.txt -d wheels --only-binary=:all: --python-version 3.12 --platform win_amd64
pip download cupy-cuda12x -d wheels --only-binary=:all: --python-version 3.12 --platform win_amd64
```
Copy the `wheels` folder next to the zip. Then, on the laptop:
```
powershell -ExecutionPolicy Bypass -File setup_laptop.ps1 -Wheelhouse C:\sovopt\wheels
```

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| "running scripts is disabled on this system" | Always start scripts with `powershell -ExecutionPolicy Bypass -File ...` as shown |
| "Python 3.11, 3.12 or 3.13 not found" | Install Python 3.12 with "Add python.exe to PATH" ticked, then open a **new** PowerShell |
| Odd "file not found" errors during setup | The folder path is too long: move to `C:\sovopt` |
| Header shows **CPU fallback** | Hover over it for the reason; re-run `setup_laptop.ps1` after installing the driver or CUDA Toolkit |
| "Address already in use" | Something else uses port 8000: `run.ps1 -Port 8010` |
| Forgot a PIN / account locked | `.venv\Scripts\python backend\manage.py reset-pin <user>` (locks also clear after 15 minutes) |
| Other devices cannot connect | Firewall rule (section 6), network set to Private, both devices on the same Wi-Fi, started with `-Lan` |
| A test fails | Send `setup_log.txt` and `docs\TEST_REPORT.html` back |
| Start completely fresh | Stop the server, then `.venv\Scripts\python backend\manage.py purge --yes` |

## 10. Where the other documents are (inside the zip)

- `README.md`: overview and configuration table.
- `docs\GAP_STATUS.md`: all 121 roadmap items with evidence.
- `docs\OPERATOR_GUIDE.md`: for control-room users (non-technical).
- `docs\OPERATIONS.md`: backups, anchoring, updates, purge.
- `docs\SECURITY.md` and `docs\INCIDENT_RESPONSE.md`: security model; what to do if something goes wrong.
- `docs\GPU_VALIDATION.md`: the GPU checklist in detail.
- `docs\DATA_FLOW.md` and `docs\CONVERGENCE.md`: for sovereignty and technical-diligence reviewers.
- `deploy\`, `Dockerfile*`: Linux, Docker and Kubernetes routes, if the laptop is later replaced by a server.
