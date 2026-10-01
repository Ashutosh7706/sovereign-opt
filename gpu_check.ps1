# Run the whole GPU validation list on the GPU laptop in one go (docs/GPU_VALIDATION.md).
#
#   powershell -ExecutionPolicy Bypass -File gpu_check.ps1                  # ~10 min, includes a 5-min thermal run
#   powershell -ExecutionPolicy Bypass -File gpu_check.ps1 -Minutes 45      # full 45-min thermal run
#   powershell -ExecutionPolicy Bypass -File gpu_check.ps1 -GpuCostHour 0.35 -CpuCostHour 0.10   # adds TCO inputs
#
# Results are collected in the folder gpu_results_<date>\  - send that folder back.
# Takes ~20-40 min in total on an RTX 4050 laptop: plug in the charger and pick the Performance power mode.
param([double]$Minutes = 5, [double]$GpuCostHour = -1, [double]$CpuCostHour = -1, [string]$Sizes = "200,500,1000,2000,3000,4000,6000,8000,12000", [double]$HighsLimit = 60)
Set-Location $PSScriptRoot
$py = if (Test-Path ".venv\Scripts\python.exe") { (Resolve-Path ".venv\Scripts\python.exe").Path } else { "python" }
$out = Join-Path $PSScriptRoot ("gpu_results_" + (Get-Date -Format "yyyyMMdd_HHmm"))
New-Item -ItemType Directory -Force $out | Out-Null
Start-Transcript -Path (Join-Path $out "gpu_check_log.txt") -Force | Out-Null
function Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }

Step "1/7 Driver and GPU (nvidia-smi)"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { nvidia-smi | Tee-Object (Join-Path $out "nvidia-smi.txt") } else { Write-Host "nvidia-smi not found - NVIDIA driver missing" -ForegroundColor Red }

Step "2/7 Boot diagnostic (must say 'GPU found')"
& $py -c "import sys; sys.path.insert(0,'backend'); from sovereign import device; import json; print(device.boot_report()); print(json.dumps(device.describe(), indent=1))" | Tee-Object (Join-Path $out "device.txt")

Step "3/7 Full test suite incl. real-GPU tests (tests/test_gpu_real.py)"
Push-Location backend
& $py -m pytest tests -q -p no:cacheprovider -rs --junitxml="$out\junit.xml" | Tee-Object (Join-Path $out "pytest.txt")
& $py scripts\test_report.py "$out\junit.xml" "$out\TEST_REPORT.html" | Out-Null
Pop-Location

Step "4/7 GPU benchmark: CPU vs GPU factorisation, crossover size, accuracy, thermal run ($Minutes min)"
$bench = @("backend\scripts\gpu_bench.py", "--sizes", $Sizes, "--minutes", "$Minutes")
if ($GpuCostHour -ge 0 -and $CpuCostHour -ge 0) { $bench += @("--gpu-cost-hour", "$GpuCostHour", "--cpu-cost-hour", "$CpuCostHour") }
& $py @bench
Copy-Item docs\GPU_RESULTS.md, docs\gpu_results.json $out -ErrorAction SilentlyContinue

Step "5/7 Sweet spot: where this GPU starts to win, VRAM ceiling, demo-safe size (docs\GPU_SWEET_SPOT.md)"
& $py backend\scripts\find_sweet_spot.py --highs-limit $HighsLimit --skip-highs-default
Copy-Item docs\GPU_SWEET_SPOT.md, docs\gpu_sweet_spot.json $out -ErrorAction SilentlyContinue
Write-Host "Set the recommended threshold before the demo, e.g.:  `$env:SOVEREIGN_GPU_MIN_ROWS = <value from GPU_SWEET_SPOT.md>" -ForegroundColor Yellow

Step "6/7 Gurobi lanes (skipped if gurobipy is not installed)"
& $py -c "import gurobipy" 2>$null
if ($LASTEXITCODE -eq 0) { & $py backend\scripts\check_gurobi.py | Tee-Object (Join-Path $out "gurobi.txt") }
else { "gurobipy not installed - optional: .venv\Scripts\python -m pip install gurobipy + free academic/trial licence" | Tee-Object (Join-Path $out "gurobi.txt") }

Step "7/7 Local LLM benchmark (skipped if Ollama is not running)"
$ollama = $false
try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:11434/api/tags" | Out-Null; $ollama = $true } catch { }
if ($ollama) {
    Push-Location backend
    $env:PYTHONIOENCODING = "utf-8"
    & $py -m sovereign.nlc.evaluate sovereign-local-llm --write "$out\NL_EVAL_LOCAL.md" | Out-Null
    Write-Host "wrote $out\NL_EVAL_LOCAL.md (model: $($env:SOVEREIGN_LOCAL_LLM_MODEL))"
    Pop-Location
} else { "Ollama not running - optional: install from ollama.com, then: ollama pull llama3.1:8b (8 GB+ VRAM) or ollama pull llama3.2:3b (6 GB VRAM), set SOVEREIGN_LOCAL_LLM_MODEL to that name, re-run" | Tee-Object (Join-Path $out "ollama.txt") }

Step "Done"
Write-Host "Results folder: $out" -ForegroundColor Green
Write-Host "Still manual: screen-record one Benchmark race (pitch backup) and fill the GPU row of docs\COMPATIBILITY.md."
Stop-Transcript | Out-Null
