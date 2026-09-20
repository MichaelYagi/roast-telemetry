# Launches the system tray icon (scripts/tray_app.py) that starts/stops
# the server with a click -- native Windows, same venv as run-server.ps1 (see venv-path.ps1).
#
# scripts\install.ps1 already installs the tray deps by default -- the
# check below is only a safety net for a venv created before that
# (install.ps1 predates tray_requirements.txt existing at all).
#
# Usage: scripts\tray.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot\venv-path.ps1"

if (-not (Test-Path "$VenvDir\Scripts\python.exe")) {
    Write-Host "No Windows virtual environment found ($VenvDir\Scripts\python.exe)." -ForegroundColor Red
    Write-Host "Run scripts\install.ps1 first -- it creates it."
    exit 1
}

& ".\$VenvDir\Scripts\python.exe" -c "import pystray" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Tray dependencies aren't installed yet. Run this once:" -ForegroundColor Red
    Write-Host "    .\$VenvDir\Scripts\python.exe -m pip install -r scripts\tray_requirements.txt"
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
& ".\$VenvDir\Scripts\python.exe" -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "tkinter isn't installed for this Python. Reinstall Python from https://python.org with the 'tcl/tk' option checked, then try again." -ForegroundColor Red
    exit 1
}

& ".\$VenvDir\Scripts\python.exe" scripts\tray_app.py
