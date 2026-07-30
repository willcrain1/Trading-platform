# start.ps1 - Install/update dependencies and launch the Trading Platform.
# Usage:  .\start.ps1           (install deps + start)
#         .\start.ps1 -NoDeps   (skip dep install, just start)

param([switch]$NoDeps)

$ErrorActionPreference = "Stop"
$root     = $PSScriptRoot
$backend  = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

Write-Host ""
Write-Host "  Crown-Style Trading Platform" -ForegroundColor White
Write-Host "  ---------------------------------" -ForegroundColor DarkGray

# ── pre-flight checks ─────────────────────────────────────────────────────────

Write-Host "`n[1/3] Checking prerequisites..." -ForegroundColor Cyan

try {
    $py = python --version 2>&1
    Write-Host "  Python : $py" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Python not found. Install from python.org" -ForegroundColor Red
    exit 1
}

try {
    $nd = node --version 2>&1
    Write-Host "  Node   : $nd" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Node.js not found. Install from nodejs.org" -ForegroundColor Red
    exit 1
}

# ── dependencies ──────────────────────────────────────────────────────────────

if (-not $NoDeps) {
    Write-Host "`n[2/3] Installing/updating dependencies..." -ForegroundColor Cyan

    Write-Host "  pip install..." -ForegroundColor DarkGray
    Set-Location $backend
    python -m pip install --upgrade pip --quiet
    python -m pip install -r requirements.txt --upgrade --quiet
    Write-Host "  Python packages OK" -ForegroundColor Green

    Write-Host "  npm install..." -ForegroundColor DarkGray
    Set-Location $frontend
    npm install --silent
    Write-Host "  Node packages OK" -ForegroundColor Green
} else {
    Write-Host "`n[2/3] Skipping dependency install (-NoDeps)" -ForegroundColor DarkGray
}

# ── launch servers ────────────────────────────────────────────────────────────

Write-Host "`n[3/3] Starting servers..." -ForegroundColor Cyan

$backendCmd  = "Set-Location '$backend'; Write-Host 'Backend running on http://localhost:8000' -ForegroundColor Cyan; uvicorn app.main:app --reload --port 8000"
$frontendCmd = "Set-Location '$frontend'; Write-Host 'Frontend running on http://localhost:5173' -ForegroundColor Cyan; npm run dev"

Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd
Write-Host "  Backend  started (http://localhost:8000)" -ForegroundColor Green

Start-Sleep -Seconds 2

Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd
Write-Host "  Frontend started (http://localhost:5173)" -ForegroundColor Green

# ── done ──────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ---------------------------------" -ForegroundColor DarkGray
Write-Host "  App : http://localhost:5173" -ForegroundColor White
Write-Host "  API : http://localhost:8000/docs" -ForegroundColor White
Write-Host "  ---------------------------------" -ForegroundColor DarkGray
Write-Host "  Close the two server windows to stop." -ForegroundColor DarkGray
Write-Host ""

Start-Sleep -Seconds 5
Start-Process "http://localhost:5173"
