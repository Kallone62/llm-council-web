@echo off
setlocal
cd /d "%~dp0"
title LLM Council Launcher

echo ========================================
echo            LLM Council v1.0
echo ========================================
echo.

where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] uv is not installed or not on PATH.
  echo Install with:
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  echo Then close and reopen this window.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js/npm is not installed or not on PATH.
  echo Install Node.js LTS and run this file again.
  pause
  exit /b 1
)

if not exist ".env" (
  echo [SETUP] Creating local .env file...
  copy /Y ".env.example" ".env" >nul
)

if not exist ".venv\Scripts\python.exe" (
  echo [SETUP] Python environment not found. Running uv sync...
  uv sync
  if errorlevel 1 goto :setup_failed
)

if not exist "frontend\node_modules" (
  echo [SETUP] Frontend dependencies not found. Running npm install...
  pushd frontend
  call npm install
  if errorlevel 1 (
    popd
    goto :setup_failed
  )
  popd
)

echo [START] Backend...
start "LLM Council Backend" cmd /k call "%~dp0scripts\windows\backend.cmd"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(25); do { try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8001/' -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 500 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
  echo [WARN] Backend did not answer within 25 seconds. Check the Backend window.
)

echo [START] Frontend...
start "LLM Council Frontend" cmd /k call "%~dp0scripts\windows\frontend.cmd"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(25); do { try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:5173/' -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 500 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
  echo [WARN] Frontend did not answer within 25 seconds. Check the Frontend window.
)

echo [OPEN] http://localhost:5173
start "" "http://localhost:5173"

echo.
echo LLM Council is running.
echo - Configure/change your OpenRouter key from Settings.
echo - Change council models and engine settings from Settings.
echo - Run STOP_LLM_COUNCIL.bat when you are done.
timeout /t 4 /nobreak >nul
exit /b 0

:setup_failed
echo.
echo [ERROR] Automatic setup failed. See the output above.
pause
exit /b 1
