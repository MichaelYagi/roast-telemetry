@echo off
rem Double-click this to run Roast Telemetry -- installs first if this
rem is a fresh clone, otherwise goes straight to the tray icon.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
echo.
pause
