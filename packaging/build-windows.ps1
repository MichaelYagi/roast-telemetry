# Builds a standalone, unsigned Windows distributable of Roast Telemetry
# -- a folder you can zip up and hand to someone with no Python/Node
# installed at all. Must run natively on Windows (PyInstaller has no
# supported cross-compile path) -- run this from PowerShell on the
# actual Windows machine, not WSL2.
#
# Prerequisite: scripts\install.ps1 already run once (sets up .venv with
# every backend + tray dependency). This script only adds pyinstaller on
# top of that and doesn't redo the rest of install.ps1's own setup.
#
# Usage: packaging\build-windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host "No .venv found -- run scripts\install.ps1 first." -ForegroundColor Red
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

Write-Host "== Installing pyinstaller into .venv =="
& .\.venv\Scripts\pip.exe install pyinstaller

Write-Host "== Running PyInstaller =="
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& .\.venv\Scripts\pyinstaller.exe packaging\roast-telemetry.spec --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller build failed -- see output above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Built: dist\Roast Telemetry\" -ForegroundColor Green
Write-Host "Zip that whole folder to distribute it -- 'Roast Telemetry.exe' inside is the double-clickable launcher."
Write-Host "Unsigned: Windows SmartScreen will warn on first run (""Windows protected your PC"") --"
Write-Host "click ""More info"" -> ""Run anyway"". That's expected for an unsigned build, not a build error."
