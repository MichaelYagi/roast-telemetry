# Sets up everything needed to run Roast Telemetry from a fresh clone,
# natively on Windows -- PowerShell equivalent of install.sh. Installs
# Python and Node.js via winget if either is missing (asks first), then
# creates the venv and installs the backend/frontend dependencies exactly
# as docs/getting-started.html's Windows-PowerShell steps do by hand.
#
# Also offers to set up WSL2 for scripts/fake-hardware.sh (the fake
# FZ-94/meter used for testing without real hardware) if WSL2 is present
# on this machine -- skipped entirely, no prompt, if it isn't. The app
# itself never needs WSL2; only that one testing tool does, and only
# because it depends on socat, which has no native-Windows build.
#
# If this script itself refuses to run ("running scripts is disabled on
# this system"), either run once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# or invoke it with: powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# Usage: scripts\install.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Confirm-Action($message) {
    $reply = Read-Host "$message [y/N]"
    return $reply -match '^[Yy]'
}

Write-Host "== Roast Telemetry setup =="
Write-Host ""

# --- Python 3.10+ ---
$havePython = $false
if (Get-Command python -ErrorAction SilentlyContinue) {
    $verOk = & python -c "import sys; print(1 if sys.version_info >= (3,10) else 0)" 2>$null
    if ($verOk -eq "1") { $havePython = $true }
}

if (-not $havePython) {
    Write-Host "Python 3.10+ not found on PATH."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Confirm-Action "Install it now via winget (Python.Python.3.13)?") {
            winget install --id Python.Python.3.13 -e --source winget
            Write-Host "Installed -- close and reopen PowerShell, then re-run this script (PATH won't pick it up in this session)."
            exit 0
        }
    } else {
        Write-Host "winget isn't available -- install Python 3.10+ from https://python.org (check 'Add python.exe to PATH' during setup), then re-run this script."
    }
    Write-Host "Python is required -- install it, then re-run this script." -ForegroundColor Red
    exit 1
}
Write-Host "Using $(python --version)"

# --- Node.js / npm ---
$haveNode = (Get-Command node -ErrorAction SilentlyContinue) -and (Get-Command npm -ErrorAction SilentlyContinue)
if (-not $haveNode) {
    Write-Host ""
    Write-Host "Node.js/npm not found on PATH (needed to build the frontend)."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Confirm-Action "Install it now via winget (OpenJS.NodeJS.LTS)?") {
            winget install --id OpenJS.NodeJS.LTS -e --source winget
            Write-Host "Installed -- close and reopen PowerShell, then re-run this script (PATH won't pick it up in this session)."
            exit 0
        }
    } else {
        Write-Host "winget isn't available -- install Node.js LTS from https://nodejs.org, then re-run this script."
    }
    Write-Host "Node.js/npm is required -- install it, then re-run this script." -ForegroundColor Red
    exit 1
}
Write-Host "Using node $(node --version), npm $(npm --version)"

# --- Backend venv ---
Write-Host ""
if (Test-Path .venv\Scripts\python.exe) {
    Write-Host "Reusing existing .venv"
} elseif (Test-Path .venv) {
    # The folder exists but isn't a native-Windows venv -- almost always
    # because a .venv was created from WSL2/Linux in this same repo
    # folder (they use .venv/bin/python, not .venv\Scripts\python.exe --
    # a different, incompatible internal layout; see run-server.ps1's
    # own header comment on this exact mismatch). Bailing out here with
    # a clear explanation instead of either silently reusing something
    # broken or guessing at whether `python -m venv .venv` can safely
    # write into a folder with unknown existing contents.
    Write-Host ".venv exists but isn't a native-Windows venv (no .venv\Scripts\python.exe) -- most likely created from WSL2/Linux in this same folder, which uses a different, incompatible layout." -ForegroundColor Red
    Write-Host "Rename or delete it, then re-run this script, e.g.:"
    Write-Host "    Rename-Item .venv .venv-wsl2"
    exit 1
} else {
    Write-Host "Creating .venv..."
    python -m venv .venv
}
Write-Host "Installing backend dependencies..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\pip.exe install -r backend\requirements.txt
Write-Host "Installing tray icon dependencies..."
& .\.venv\Scripts\pip.exe install -r scripts\tray_requirements.txt

# --- Frontend deps ---
Write-Host ""
Write-Host "Installing frontend dependencies (npm install)..."
Push-Location frontend
npm install
Pop-Location

# --- Optional: WSL2, for scripts/fake-hardware.sh ---
Write-Host ""
$wslAvailable = $false
if (Get-Command wsl -ErrorAction SilentlyContinue) {
    wsl --status *> $null
    if ($LASTEXITCODE -eq 0) { $wslAvailable = $true }
}

if (-not $wslAvailable) {
    Write-Host "WSL2 not found -- skipping (only needed for scripts/fake-hardware.sh's fake FZ-94/meter; the app itself runs fine without it)."
} elseif (Confirm-Action "WSL2 found. Set it up too, for scripts/fake-hardware.sh (fake hardware for testing)?") {
    # wsl.exe's own argument marshaling treats backslashes as shell
    # escape characters when passing them through to the Linux-side
    # command, silently stripping every one -- confirmed live:
    # C:\Users\Michael\...\roast-telemetry arrived at wslpath as
    # "C:UsersMichaelDocumentsdevelopmentroast-telemetry", which then
    # failed to parse as a path at all. wslpath accepts forward slashes
    # just as well, so converting first sidesteps the stripping
    # entirely instead of fighting escaping rules.
    $winPathForwardSlash = $PWD.Path -replace '\\', '/'
    $wslPath = wsl wslpath -u $winPathForwardSlash
    if (-not $wslPath) {
        Write-Host "Couldn't resolve this folder's WSL path -- skipping. You can run scripts/install.sh from inside WSL2 yourself instead." -ForegroundColor Yellow
    } else {
        Write-Host "Setting up $wslPath inside WSL2 (this repo's own install.sh -- may ask for your WSL sudo password)..."
        wsl bash -lc "cd '$wslPath' && ./scripts/install.sh"
    }
}

Write-Host ""
Write-Host "Done. Next:"
Write-Host "  scripts\run-server.ps1               # build + start the app at http://localhost:8000"
Write-Host "  scripts\tray.ps1                     # optional -- a tray icon instead of the terminal"
Write-Host "  scripts\fake-hardware.sh fz94 --tcp  # optional, run from WSL2 -- test without real hardware"
