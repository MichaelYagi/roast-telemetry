# Runs the backend -- PowerShell equivalent of run-server.sh, for running
# natively on Windows (not WSL) -- the one real reason to: a real
# roaster's actual COM port needs direct Windows access, which WSL
# doesn't give you without extra setup (usbipd-win, still awkward).
#
# Requires a venv created natively on Windows (python -m venv .venv from
# a Windows Python, not the WSL .venv directory -- the two aren't
# interchangeable, their executables target different platforms):
#   python -m venv .venv
#   .\.venv\Scripts\Activate.ps1
#   pip install -r backend\requirements.txt
#   cd frontend; npm install; cd ..
#
# If Activate.ps1 refuses to run ("running scripts is disabled on this
# system"), either run once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# or just invoke this script itself with: powershell -ExecutionPolicy Bypass -File scripts\run-server.ps1
#
# Rebuilds the frontend first by default -- the backend serves whatever's
# already sitting in frontend\dist\ and never rebuilds it for you, so a
# stale build silently keeps serving old code (auth/login behavior
# included) with no error of any kind. Pass -SkipBuild once you know
# your build is current, for faster restarts.
#
# Usage:
#   scripts\run-server.ps1                    # rebuild, then port 8000
#   scripts\run-server.ps1 -Port 7890
#   scripts\run-server.ps1 -Reload             # auto-restart on backend code changes
#   scripts\run-server.ps1 -SkipBuild -Port 7890
param(
    [int]$Port = 8000,
    [switch]$Reload,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not $SkipBuild) {
    Write-Host "Building frontend (pass -SkipBuild to skip this)..."
    Push-Location frontend
    npm run build
    Pop-Location
}

$env:PYTHONPATH = "."
Write-Host "Starting Roast Telemetry on http://localhost:$Port (Ctrl+C to stop)"
$uvicornArgs = @("backend.app.main:app", "--port", $Port)
if ($Reload) { $uvicornArgs += "--reload" }
& .\.venv\Scripts\uvicorn.exe @uvicornArgs
