@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-AiosImage.ps1" %*
exit /b %errorlevel%
