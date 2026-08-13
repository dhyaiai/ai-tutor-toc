@echo off
title AI Tutor - Public Mode (Cloudflare Tunnel)
echo ========================================
echo   AI Tutor - Public Startup (Plan A)
echo ========================================
echo.

cd /d "%~dp0"

:: ---- 0. MySQL (auto-start service; start manually if not running) ----
sc query MySQL80 | find "RUNNING" >nul 2>&1 || (
    echo [0/3] Starting MySQL service...
    net start MySQL80 >nul 2>&1
)

:: ---- 1. Backend (port 8000) ----
netstat -ano | findstr /C:":8000 " | findstr "LISTENING" >nul 2>&1 || (
    echo [1/3] Starting backend on port 8000...
    start "AI Tutor Backend" cmd /k "cd /d %~dp0backend && C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
) && echo [1/3] Backend already running.

:: ---- 2. Frontend (port 5173) ----
netstat -ano | findstr /C:":5173 " | findstr "LISTENING" >nul 2>&1 || (
    echo [2/3] Starting frontend on port 5173...
    start "AI Tutor Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"
) && echo [2/3] Frontend already running.

:: ---- 3. Cloudflare Tunnel (quick tunnel, new URL every start) ----
echo [3/3] Starting Cloudflare tunnel...
echo.
echo    Wait for the "trycloudflare.com" URL below, then share it.
echo    Closing this window stops the tunnel.
echo.
cloudflared tunnel --url http://localhost:5173

pause
