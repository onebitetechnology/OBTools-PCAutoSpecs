@echo off
:: PC AutoSpec — Portable Launcher
:: Double-click to run. No installation needed.

setlocal
cd /d "%~dp0"

:: Unblock files if downloaded from internet (removes SmartScreen flag)
powershell -NoProfile -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" 2>nul

:: Launch
"%~dp0python\python.exe" "%~dp0run.py"
if errorlevel 1 pause
