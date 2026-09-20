# One entry point: installs (scripts\install.ps1) if this is a fresh
# clone, then starts the tray icon (scripts\tray.ps1) -- so there's one
# thing to run regardless of whether setup has happened yet.
#
# install.ps1/tray.ps1 themselves are untouched and still work
# standalone -- this only adds a combined entry point on top.
#
# Usage: scripts\start.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot\venv-path.ps1"

function TrayDepsReady {
    # python.exe inside the venv specifically, not just the bare folder.
    if (-not (Test-Path "$VenvDir\Scripts\python.exe")) { return $false }
    & ".\$VenvDir\Scripts\python.exe" -c "import pystray" 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    # pystray and tkinter are two separate dependencies (tkinter doesn't
    # come from pystray/Pillow at all) -- checked independently so a
    # a venv with pystray already installed but tkinter missing (the
    # real bug found on macOS's Homebrew Python, which splits Tk support
    # into its own formula -- see install.sh) doesn't read as "ready"
    # here too and skip straight to a crash in tray.ps1 instead of
    # routing through install.ps1.
    & ".\$VenvDir\Scripts\python.exe" -c "import tkinter" 2>$null
    return ($LASTEXITCODE -eq 0)
}

if (-not (TrayDepsReady)) {
    Write-Host "First-time setup..."
    & .\scripts\install.ps1
    . "$PSScriptRoot\venv-path.ps1"   # re-pick: install.ps1 may just have created it
    # Re-check rather than trust install.ps1's own exit code -- it exits
    # 0 early after a winget install too ("close and reopen PowerShell,
    # then re-run"), which is success-ish but genuinely hasn't finished
    # setting up the venv yet. Checking the actual condition instead of the
    # exit code catches that case correctly either way.
    if (-not (TrayDepsReady)) {
        Write-Host "Setup isn't finished yet (see above -- if Python/Node.js were just installed, close this window, reopen PowerShell, and run this again)." -ForegroundColor Yellow
        exit 1
    }
}

& .\scripts\tray.ps1
