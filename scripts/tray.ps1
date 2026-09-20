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

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host "No Windows virtual environment found (.venv\Scripts\python.exe)." -ForegroundColor Red
    if (Test-Path .venv) {
        Write-Host "A .venv folder exists but it isn't a Windows one -- most likely created from WSL2/Linux in this same folder (they use a different layout)."
    }
    Write-Host "Run scripts\start.ps1 (or scripts\install.ps1 first) -- it sets this up and explains what to do if .venv is in the way."
    exit 1
}

& .\.venv\Scripts\python.exe -c "import pystray" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Tray dependencies aren't installed yet. Run this once:" -ForegroundColor Red
    Write-Host "    .\.venv\Scripts\pip.exe install -r scripts\tray_requirements.txt"
    Write-Host "(or just re-run scripts\install.ps1, which installs this by default now)"
    exit 1
}

# A separate check -- tkinter doesn't come from tray_requirements.txt
# (pystray/Pillow) at all, it's bundled with the Python installer
# itself (usually on by default, but not guaranteed for every Python
# install) -- pip can't fix a missing one, so the message has to say
# something different. See install.sh's own macOS/Linux equivalent of
# this check -- confirmed live on a real Mac that this gap is real,
# not just theoretical.
& .\.venv\Scripts\python.exe -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "tkinter isn't installed for this Python. Reinstall Python from https://python.org with the 'tcl/tk' option checked, then try again." -ForegroundColor Red
    exit 1
}

& .\.venv\Scripts\python.exe scripts\tray_app.py
