# Dot-sourced by the other Windows scripts (`. "$PSScriptRoot\venv-path.ps1"`,
# after they've cd'd to the repo root) to pick which virtual environment to
# use: sets $VenvDir.
#
# Windows gets its own folder, .venv-windows, because this repo folder is
# often shared with WSL2, and a Linux venv (.venv/bin) and a Windows venv
# (.venv\Scripts) can't share a name. macOS and Linux keep plain .venv.
#
# An existing Windows venv at plain .venv\Scripts (what older versions of
# install.ps1 created) is still picked up if there's no .venv-windows, so
# nobody has to redo their setup. When neither exists, $VenvDir is
# .venv-windows, which is where install.ps1 will create it.
$VenvDir = ".venv-windows"
if (-not (Test-Path ".venv-windows\Scripts\python.exe") -and (Test-Path ".venv\Scripts\python.exe")) {
    $VenvDir = ".venv"
}
