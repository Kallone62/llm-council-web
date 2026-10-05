@echo off
setlocal
cd /d "%~dp0"
title Create LLM Council Desktop Shortcut

powershell -NoProfile -ExecutionPolicy Bypass -Command "$desktop=[Environment]::GetFolderPath('Desktop'); $shell=New-Object -ComObject WScript.Shell; $shortcut=$shell.CreateShortcut((Join-Path $desktop 'LLM Council.lnk')); $shortcut.TargetPath='%~dp0START_LLM_COUNCIL.bat'; $shortcut.WorkingDirectory='%~dp0'; $shortcut.Description='Start local LLM Council'; $shortcut.Save()"

if errorlevel 1 (
  echo [ERROR] Could not create the desktop shortcut.
  pause
  exit /b 1
)

echo Desktop shortcut created: LLM Council
pause
