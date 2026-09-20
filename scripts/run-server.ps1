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
# Rebuilds the frontend first *when it's out of date* -- the backend
# serves whatever's already sitting in frontend\dist\ and never rebuilds
# it for you, so a stale build silently keeps serving old code (auth/login
# behavior included) with no error of any kind. "Out of date" = dist\ is
# missing, or anything under frontend\src, public, index.html,
# vite.config.js or package.json is newer than dist\index.html -- same
# check run-server.sh and the tray icon do, so a restart with nothing
# changed doesn't pay for a rebuild. -ForceBuild rebuilds regardless
# (e.g. after `npm install` changed dependencies without touching
# package.json), -SkipBuild never does.
#
# -Lan (or -BindHost 0.0.0.0) makes the server reachable from other
# devices on your network, same as run-server.sh's --lan/--host. Named
# -BindHost, not -Host: $Host is a reserved PowerShell variable. Windows
# may ask to allow Python through its firewall the first time.
#
# Usage:
#   scripts\run-server.ps1                    # rebuild if stale, then port 8000, localhost only
#   scripts\run-server.ps1 -Port 7890
#   scripts\run-server.ps1 -Reload             # auto-restart on backend code changes
#   scripts\run-server.ps1 -SkipBuild -Port 7890
#   scripts\run-server.ps1 -ForceBuild         # rebuild even if it looks current
#   scripts\run-server.ps1 -Lan                # reachable from other devices on your LAN
#   scripts\run-server.ps1 -BindHost 0.0.0.0   # same as -Lan, spelled out
param(
    [int]$Port = 8000,
    [switch]$Reload,
    [switch]$SkipBuild,
    [switch]$ForceBuild,
    [switch]$Lan,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if ($Lan) { $BindHost = "0.0.0.0" }

function Test-FrontendStale {
    $distIndex = "frontend\dist\index.html"
    if (-not (Test-Path $distIndex)) { return $true }
    $distTime = (Get-Item $distIndex).LastWriteTime
    foreach ($f in @("frontend\index.html", "frontend\vite.config.js", "frontend\package.json")) {
        if ((Test-Path $f) -and ((Get-Item $f).LastWriteTime -gt $distTime)) { return $true }
    }
    foreach ($dir in @("frontend\src", "frontend\public")) {
        if (Test-Path $dir) {
            $newer = Get-ChildItem $dir -Recurse -File | Where-Object { $_.LastWriteTime -gt $distTime } | Select-Object -First 1
            if ($newer) { return $true }
        }
    }
    return $false
}

function Invoke-FrontendBuild {
    Push-Location frontend
    npm run build
    $code = $LASTEXITCODE
    Pop-Location
    # Don't fall through to serving a stale/missing build after a failed
    # one -- run-server.sh gets this for free from `set -e`.
    if ($code -ne 0) {
        Write-Host "Frontend build failed -- not starting the server." -ForegroundColor Red
        exit 1
    }
}

if ($ForceBuild) {
    Write-Host "Building frontend (-ForceBuild)..."
    Invoke-FrontendBuild
} elseif ($SkipBuild) {
    Write-Host "Skipping frontend build (-SkipBuild)."
} elseif (Test-FrontendStale) {
    Write-Host "Frontend changed since the last build (or was never built) -- rebuilding..."
    Invoke-FrontendBuild
} else {
    Write-Host "Frontend build is current -- skipping rebuild (-ForceBuild to rebuild anyway)."
}

$env:PYTHONPATH = "."
Write-Host "Starting Roast Telemetry on http://localhost:$Port (Ctrl+C to stop)"
if ($BindHost -eq "0.0.0.0") {
    # Best-effort LAN IP so there's something concrete to type on another
    # device. Uses the adapter that owns the default route -- "first
    # non-loopback address" picked WSL's virtual NIC (192.168.64.1) over
    # the real Ethernet adapter on a real machine, which is exactly the
    # wrong answer to hand someone. Still a guess (a VPN can own the
    # default route), hence "likely". Silent on failure, like the bash
    # version: a convenience hint isn't worth failing the launch over.
    $lanIp = $null
    try {
        $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction Stop | Sort-Object RouteMetric | Select-Object -First 1
        if ($route) {
            $lanIp = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 -ErrorAction Stop |
                Where-Object { $_.IPAddress -notlike "169.254.*" } |
                Select-Object -First 1 -ExpandProperty IPAddress
        }
    } catch { }
    if ($lanIp) {
        Write-Host "Also reachable from other devices on your LAN at (likely): http://${lanIp}:$Port"
    } else {
        Write-Host "Also reachable from other devices on your LAN -- find this machine's own IP (ipconfig) and use http://<that IP>:$Port"
    }
}
$uvicornArgs = @("backend.app.main:app", "--host", $BindHost, "--port", $Port)
if ($Reload) { $uvicornArgs += "--reload" }
& .\.venv\Scripts\uvicorn.exe @uvicornArgs
