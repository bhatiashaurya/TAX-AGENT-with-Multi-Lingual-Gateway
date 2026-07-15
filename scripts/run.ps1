<#
  Windows quick-start for the NTH Voice Gateway.
  Usage:   powershell -ExecutionPolicy Bypass -File scripts\run.ps1
  Port:    8080 by default; override with  $env:GATEWAY_PORT = 8081
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$port = if ($env:GATEWAY_PORT) { [int]$env:GATEWAY_PORT } else { 8080 }
$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $ownerPid = $existing[0].OwningProcess
    $owner = (Get-Process -Id $ownerPid -ErrorAction SilentlyContinue).ProcessName
    Write-Host "Port $port is already in use by '$owner' (PID $ownerPid)." -ForegroundColor Yellow
    Write-Host "If that's the gateway, it's already running: http://127.0.0.1:$port/ui/"
    Write-Host "Otherwise stop it, or pick another port:  `$env:GATEWAY_PORT = 8081; .\scripts\run.ps1"
    exit 1
}

if (-not (Test-Path "$root\.venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv "$root\.venv"
    & "$root\.venv\Scripts\python.exe" -m pip install --upgrade pip
    & "$root\.venv\Scripts\python.exe" -m pip install -r "$root\backend\requirements.txt"
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Write-Host "Starting gateway on http://127.0.0.1:$port  (UI at /ui/)" -ForegroundColor Green
Push-Location "$root\backend"
try {
    & "$root\.venv\Scripts\python.exe" -m uvicorn main:app --reload --host 127.0.0.1 --port $port
} finally {
    Pop-Location
}
