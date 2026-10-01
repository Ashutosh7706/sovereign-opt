# One-time setup of Sovereign Optimizer on a Windows laptop (see SETUP_NEW_LAPTOP.md).
#
#   powershell -ExecutionPolicy Bypass -File setup_laptop.ps1            # auto-detects an NVIDIA GPU
#   powershell -ExecutionPolicy Bypass -File setup_laptop.ps1 -NoGpu     # CPU only
#   powershell -ExecutionPolicy Bypass -File setup_laptop.ps1 -Wheelhouse D:\wheels   # offline / air-gapped install
#
# Creates .venv, installs pinned dependencies (+ CuPy when a GPU is found), checks the
# configuration, runs the full test suite and writes everything to setup_log.txt.
param(
    [switch]$NoGpu,
    [switch]$NoTests,
    [string]$Wheelhouse = "",
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Start-Transcript -Path (Join-Path $PSScriptRoot "setup_log.txt") -Force | Out-Null

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "SETUP FAILED: $msg" -ForegroundColor Red; Stop-Transcript | Out-Null; exit 1 }

if ($PSScriptRoot.Length -gt 80) {
    Write-Warning "This folder path is long ($($PSScriptRoot.Length) chars). Windows limits paths to 260 chars; unzip to a short folder such as C:\sovopt if anything fails."
}

# ---------------------------------------------------------------- 1. Python 3.11-3.13
Step "Finding Python 3.11 - 3.13 (3.12 recommended)"
$candidates = @()
if ($Python) { $candidates += ,@($Python) }
if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += ,@("py", "-3.12"); $candidates += ,@("py", "-3.13"); $candidates += ,@("py", "-3.11") }
if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += ,@("python") }
$py = $null
foreach ($c in $candidates) {
    try {
        $exe = $c[0]; $pre = @($c | Select-Object -Skip 1)
        $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and @("3.11", "3.12", "3.13") -contains "$v".Trim()) { $py = $c; Write-Host "using: $($c -join ' ') (Python $v)"; break }
    } catch { }
}
if (-not $py) { Fail "Python 3.11, 3.12 or 3.13 not found. Install Python 3.12 from python.org (tick 'Add python.exe to PATH') and re-run." }

# ---------------------------------------------------------------- 2. virtual environment + dependencies
Step "Creating virtual environment .venv"
$exe = $py[0]; $pre = @($py | Select-Object -Skip 1)
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $exe @pre -m venv .venv; if ($LASTEXITCODE -ne 0) { Fail "venv creation failed" } }
$vpy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Step "Installing pinned dependencies"
$pipArgs = @("-m", "pip", "install", "--disable-pip-version-check")
if ($Wheelhouse) { $pipArgs += @("--no-index", "--find-links", $Wheelhouse) } else { & $vpy -m pip install --quiet --upgrade pip }
& $vpy @pipArgs -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { Fail "pip install failed (no internet? use -Wheelhouse, see SETUP_NEW_LAPTOP.md section 8)" }

# ---------------------------------------------------------------- 3. GPU (optional)
$gpuOk = $false
if (-not $NoGpu) {
    Step "Looking for an NVIDIA GPU"
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) {
        Write-Host "nvidia-smi not found -> no NVIDIA driver visible. Continuing CPU-only (install the NVIDIA driver and re-run to enable the GPU)." -ForegroundColor Yellow
    } else {
        $smiOut = (& nvidia-smi) -join "`n"
        $name = (& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader) -join "; "
        $m = [regex]::Match($smiOut, "CUDA Version:\s*(\d+)\.(\d+)")
        Write-Host "GPU: $name"
        if (-not $m.Success) { Write-Host "Could not read the driver's CUDA version; assuming 12." -ForegroundColor Yellow; $cuda = 12 } else { $cuda = [int]$m.Groups[1].Value; Write-Host "Driver supports CUDA $($m.Groups[1].Value).$($m.Groups[2].Value)" }
        if ($cuda -ge 12) { $pkg = "cupy-cuda12x" } elseif ($cuda -eq 11) { $pkg = "cupy-cuda11x" } else { $pkg = $null; Write-Host "Driver too old for CuPy (needs CUDA 11+). Update the NVIDIA driver." -ForegroundColor Yellow }
        if ($pkg) {
            Step "Installing $pkg"
            & $vpy @pipArgs $pkg
            if ($LASTEXITCODE -ne 0) { Write-Host "CuPy install failed - continuing CPU-only." -ForegroundColor Yellow }
            else {
                $probe = "import cupy as cp; a=cp.random.rand(400,400); a=a@a.T+400*cp.eye(400); cp.linalg.cholesky(a); cp.cuda.Stream.null.synchronize(); print('CuPy OK on', cp.cuda.runtime.getDeviceProperties(0)['name'].decode())"
                & $vpy -c $probe
                if ($LASTEXITCODE -eq 0) { $gpuOk = $true }
                else { Write-Host "CuPy is installed but cannot run CUDA kernels. Install the NVIDIA CUDA Toolkit $cuda.x (developer.nvidia.com/cuda-downloads), open a NEW PowerShell window and re-run this script. The platform works CPU-only meanwhile." -ForegroundColor Yellow }
            }
        }
    }
}

# ---------------------------------------------------------------- 4. configuration + tests
Step "Checking configuration"
& $vpy backend\manage.py config
if ($LASTEXITCODE -ne 0) { Fail "configuration invalid (see message above)" }
& $vpy -c "import sys; sys.path.insert(0,'backend'); from sovereign import device; print(device.boot_report())"

if (-not $NoTests) {
    Step "Running the test suite (about 1-2 minutes)"
    Push-Location backend
    & $vpy -m pytest tests -q -p no:cacheprovider --junitxml=..\docs\junit.xml
    $testExit = $LASTEXITCODE
    & $vpy scripts\test_report.py ..\docs\junit.xml ..\docs\TEST_REPORT.html | Out-Null
    Pop-Location
    if ($testExit -ne 0) { Write-Host "Some tests FAILED - see above and docs\TEST_REPORT.html. Send setup_log.txt back." -ForegroundColor Red }
    else { Write-Host "All tests passed. Report: docs\TEST_REPORT.html" -ForegroundColor Green }
}

Step "Done"
Write-Host "GPU path: $(if ($gpuOk) {'ENABLED'} else {'not enabled (CPU FP64 path)'})"
Write-Host "Start the platform:        powershell -ExecutionPolicy Bypass -File run.ps1"
Write-Host "Share on your network:     powershell -ExecutionPolicy Bypass -File run.ps1 -Lan -Https"
if ($gpuOk) { Write-Host "Run the GPU test list:     powershell -ExecutionPolicy Bypass -File gpu_check.ps1" }
Write-Host "Full log: setup_log.txt"
Stop-Transcript | Out-Null
