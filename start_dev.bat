@echo off
setlocal
echo ========================================
echo   AI Tutor - Dev Mode Startup
echo ========================================
echo.

REM Project requires Python 3.10 (PaddleOCR/PyTorch compatibility;
REM Python 3.14 fails to start backend due to DLL issues).
REM Do NOT rely on PATH python; pin the 3.10 interpreter explicitly.
set "PYTHON=C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe"
if not exist "%PYTHON%" (
    echo ERROR: Python 3.10 not found at %PYTHON%
    echo Please install Python 3.10 and update the PYTHON path in this script.
    pause
    exit /b 1
)

REM ---- Backend ----
cd /d "%~dp0backend"

REM Skip pip install if core deps are already present (faster startup)
echo [1/4] Checking backend dependencies...
"%PYTHON%" -c "import uvicorn, fastapi, sqlalchemy, aiomysql" >nul 2>&1
if errorlevel 1 (
    echo Installing backend dependencies...
    "%PYTHON%" -m pip install -r requirements.txt -q
    if errorlevel 1 (
        echo ERROR: Failed to install backend dependencies
        pause
        exit /b 1
    )
) else (
    echo Backend dependencies already installed.
)

echo [2/4] Starting backend (port 8000)...
start "AI Tutor Backend" cmd /k "%PYTHON% -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
cd /d "%~dp0"

REM ---- Frontend ----
cd /d "%~dp0frontend"

REM Skip npm install if node_modules exists (rerun manually after package.json changes)
echo [3/4] Checking frontend dependencies...
if not exist node_modules (
    call npm install
    if errorlevel 1 (
        echo ERROR: Failed to install frontend dependencies
        pause
        exit /b 1
    )
) else (
    echo node_modules already exists, skipping npm install
)

echo [4/4] Starting frontend (port 5173)...
start "AI Tutor Frontend" cmd /k "npm run dev"
cd /d "%~dp0"

echo.
echo ========================================
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:5173
echo   API Docs: http://localhost:8000/docs
echo ========================================
echo.
echo Close this window or press Ctrl+C in each terminal to stop.
pause
