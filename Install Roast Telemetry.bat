@echo off
rem Double-click to run scripts\install.ps1 without opening PowerShell
rem yourself or dealing with its execution-policy prompt (-ExecutionPolicy
rem Bypass here only affects this one launch, not your system setting).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
echo.
pause
