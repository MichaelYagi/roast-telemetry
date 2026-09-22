# Runs the backend -- PowerShell equivalent of run-server.sh, for running
# natively on Windows (not WSL) -- the one real reason to: a real
# roaster's actual COM port needs direct Windows access, which WSL
# doesn't give you without extra setup (usbipd-win, still awkward).
#
# Requires a venv created natively on Windows -- scripts\install.ps1 makes
# one at .venv-windows (an older .venv\Scripts one is still picked up; see
# venv-path.ps1). It can't share the plain .venv name with a WSL2/Linux venv
# -- their executables target different platforms. By hand:
#   python -m venv .venv-windows
#   .\.venv-windows\Scripts\Activate.ps1
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
# Also runs `npm install` first, on its own equivalent staleness check,
# whenever a build is about to happen -- otherwise a `git pull` that added
# a new frontend dependency (without a matching `npm install`) fails the
# build with a bare Rollup "failed to resolve import" error that gives no
# hint what to actually do about it. Same fix as run-server.sh.
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
. "$PSScriptRoot\venv-path.ps1"

# No venv at all (a fresh clone, install.ps1 never run) -- without this,
# the uvicorn invocation at the bottom fails with a bare "cannot find
# path" error and no hint what to actually do about it. Same check
# tray.ps1 already has.
if (-not (Test-Path "$VenvDir\Scripts\python.exe")) {
    Write-Host "No virtual environment found ($VenvDir\Scripts\python.exe missing)." -ForegroundColor Red
    Write-Host "Run scripts\install.ps1 first -- it creates it."
    exit 1
}

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

# Same idea as Test-FrontendStale, for whether `npm install` itself needs
# to run first. npm writes node_modules\.package-lock.json on every
# install -- a snapshot of the lockfile it actually installed from -- so
# comparing that against package.json/package-lock.json (not
# node_modules\ itself, which doesn't change just because its contents
# are stale) is what catches "the lockfile moved on since the last install".
function Test-NodeModulesStale {
    $marker = "frontend\node_modules\.package-lock.json"
    if (-not (Test-Path "frontend\node_modules")) { return $true }
    if (-not (Test-Path $marker)) { return $true }
    $markerTime = (Get-Item $marker).LastWriteTime
    foreach ($f in @("frontend\package.json", "frontend\package-lock.json")) {
        if ((Test-Path $f) -and ((Get-Item $f).LastWriteTime -gt $markerTime)) { return $true }
    }
    return $false
}

function Invoke-NpmInstall {
    Write-Host "frontend\package.json changed since the last npm install -- installing dependencies..."
    Push-Location frontend
    npm install
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) {
        Write-Host "npm install failed -- not starting the server." -ForegroundColor Red
        exit 1
    }
}

$willBuild = $false
if ($SkipBuild) {
    Write-Host "Skipping frontend build (-SkipBuild)."
} elseif ($ForceBuild) {
    $willBuild = $true
} elseif (Test-FrontendStale) {
    $willBuild = $true
}

if ($willBuild) {
    if (Test-NodeModulesStale) { Invoke-NpmInstall }
    if ($ForceBuild) {
        Write-Host "Building frontend (-ForceBuild)..."
    } else {
        Write-Host "Frontend changed since the last build (or was never built) -- rebuilding..."
    }
    Invoke-FrontendBuild
} elseif (-not $SkipBuild) {
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
& ".\$VenvDir\Scripts\python.exe" -m uvicorn @uvicornArgs
