@echo off
setlocal
title Stop LLM Council

echo Stopping LLM Council processes on ports 8001 and 5173...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ports = 8001,5173; foreach ($p in $ports) { Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }"

echo Done. You can close any remaining LLM Council terminal windows.
timeout /t 2 /nobreak >nul
