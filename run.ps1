# Start Sovereign Optimizer (run setup_laptop.ps1 once first).
#
#   powershell -ExecutionPolicy Bypass -File run.ps1              # this laptop only: http://127.0.0.1:8000
#   powershell -ExecutionPolicy Bypass -File run.ps1 -Https       # this laptop only, over TLS
#   powershell -ExecutionPolicy Bypass -File run.ps1 -Lan -Https  # other devices on the network can connect
#
# First boot prints a one-time admin PIN and writes backend\data\BOOTSTRAP_ADMIN.txt.
param([switch]$Https, [switch]$Lan, [int]$Port = 8000, [string]$BindHost = "")
Set-Location $PSScriptRoot
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
if (-not $BindHost) { $BindHost = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" } }
if ($Lan -and -not $Https) { Write-Warning "Serving on the network without -Https sends PINs in clear text. Add -Https." }

$ips = @()
if ($Lan) {
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
             Select-Object -ExpandProperty IPAddress)
}
$scheme = if ($Https) { "https" } else { "http" }
$uv = @("-m", "uvicorn", "app:app", "--app-dir", "backend", "--host", $BindHost, "--port", "$Port", "--timeout-graceful-shutdown", "30")
if ($Https) {
    if (-not (Test-Path "certs\server.crt")) {
        $certArgs = @("backend\scripts\make_cert.py", "--host", "localhost", "--host", $env:COMPUTERNAME, "--ip", "127.0.0.1")
        foreach ($ip in $ips) { $certArgs += @("--ip", $ip) }
        & $py @certArgs
    }
    $env:SOVEREIGN_HTTPS = "1"
    $uv += @("--ssl-keyfile", "certs\server.key", "--ssl-certfile", "certs\server.crt")
}
Write-Host ""
Write-Host "Open on this laptop:  ${scheme}://localhost:$Port" -ForegroundColor Green
foreach ($ip in $ips) { Write-Host "Open from other devices: ${scheme}://${ip}:$Port   (allow port $Port in Windows Firewall - see SETUP_NEW_LAPTOP.md 6)" -ForegroundColor Green }
Write-Host "Stop with Ctrl+C."
Write-Host ""
& $py @uv
