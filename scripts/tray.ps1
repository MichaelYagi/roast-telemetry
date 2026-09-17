# Launches the system tray icon (scripts/tray_app.py) that starts/stops
# the server with a click -- native Windows, same venv as run-server.ps1.
#
# scripts\install.ps1 already installs the tray deps by default -- the
# check below is only a safety net for a .venv created before that
# (install.ps1 predates tray_requirements.txt existing at all).
#
# Usage: scripts\tray.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

& .\.venv\Scripts\python.exe -c "import pystray" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Tray dependencies aren't installed yet. Run this once:" -ForegroundColor Red
    Write-Host "    .\.venv\Scripts\pip.exe install -r scripts\tray_requirements.txt"
    Write-Host "(or just re-run scripts\install.ps1, which installs this by default now)"
    exit 1
}

& .\.venv\Scripts\python.exe scripts\tray_app.py
