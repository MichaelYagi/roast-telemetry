@echo off
rem Double-click to launch the tray icon (scripts\tray.ps1) without
rem opening PowerShell yourself. This window stays open (minimize it)
rem for as long as the tray icon is running -- closing the window closes
rem the tray icon and stops the server too.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\tray.ps1"
echo.
pause
