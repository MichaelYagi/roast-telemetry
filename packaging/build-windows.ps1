# Builds a standalone, unsigned Windows distributable of Roast Telemetry
# -- no Python/Node installed at all required to *run* the result. Must
# run natively on Windows (PyInstaller has no supported cross-compile
# path) -- run this from PowerShell on the actual Windows machine, not
# WSL2.
#
# Prerequisite: scripts\install.ps1 already run once (sets up the venv with
# every backend + tray dependency). This script only adds pyinstaller on
# top of that and doesn't redo the rest of install.ps1's own setup.
#
# Usage:
#   packaging\build-windows.ps1              # single Roast Telemetry.exe (default)
#   packaging\build-windows.ps1 -Mode onedir # a folder instead -- faster startup, no
#                                             # single-exe antivirus-heuristic risk, but
#                                             # Roast Telemetry.exe needs its _internal\
#                                             # folder alongside it, can't move just the exe
param(
    [ValidateSet("onefile", "onedir")]
    [string]$Mode = "onefile"
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot\..\scripts\venv-path.ps1"

if (-not (Test-Path "$VenvDir\Scripts\python.exe")) {
    Write-Host "No Windows venv found -- run scripts\install.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "== Building frontend =="
Push-Location frontend
npm run build
Pop-Location
if ($LASTEXITCODE -ne 0) {
    Write-Host "Frontend build failed." -ForegroundColor Red
    exit 1
}

Write-Host "== Installing pyinstaller + tray dependencies into $VenvDir =="
# The tray deps (pystray/Pillow) are a separate requirements file from
# backend/requirements.txt (see that file's own comment) -- install.ps1
# already installs them for the from-source workflow, but re-asserting
# it here too means a build can never silently produce a broken
# "ModuleNotFoundError: No module named 'pystray'" exe just because
# the venv happened to be missing them for some other reason (an older
# install.ps1 run, a different venv than expected, etc.) -- confirmed
# live: this exact failure happened before this line existed.
& ".\$VenvDir\Scripts\python.exe" -m pip install pyinstaller
& ".\$VenvDir\Scripts\python.exe" -m pip install -r scripts\tray_requirements.txt

Write-Host "== Running PyInstaller ($Mode) =="
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
$env:PACKAGE_MODE = $Mode
& ".\$VenvDir\Scripts\python.exe" -m PyInstaller packaging\roast-telemetry.spec --distpath dist --workpath build
$buildExitCode = $LASTEXITCODE
Remove-Item Env:\PACKAGE_MODE
if ($buildExitCode -ne 0) {
    Write-Host "PyInstaller build failed -- see output above." -ForegroundColor Red
    exit 1
}

Write-Host ""
if ($Mode -eq "onefile") {
    Write-Host "Built: dist\Roast Telemetry.exe" -ForegroundColor Green
    Write-Host "A genuinely single file -- copy/zip just that, nothing else needed alongside it."
} else {
    Write-Host "Built: dist\Roast Telemetry\" -ForegroundColor Green
    Write-Host "Zip that whole folder to distribute it -- 'Roast Telemetry.exe' inside needs its _internal\ folder alongside it, can't be moved alone."
}
Write-Host "Unsigned: Windows SmartScreen will warn on first run (""Windows protected your PC"") --"
Write-Host "click ""More info"" -> ""Run anyway"". That's expected for an unsigned build, not a build error."
