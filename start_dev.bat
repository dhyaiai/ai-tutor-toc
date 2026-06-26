@echo off
echo ========================================
echo   AI Tutor - Dev Mode Startup
echo ========================================
echo.

cd /d "%~dp0"

:: ---- Backend ----
echo [1/4] Installing backend dependencies...
cd backend
pip install -r requirements.txt -q
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install backend dependencies
    pause
    exit /b 1
)

echo [2/4] Starting backend (port 8000)...
start "AI Tutor Backend" cmd /c "cd /d %cd% && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
cd ..

:: ---- Frontend ----
echo [3/4] Installing frontend dependencies...
cd frontend
call npm install
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install frontend dependencies
    pause
    exit /b 1
)

echo [4/4] Starting frontend (port 5173)...
start "AI Tutor Frontend" cmd /c "cd /d %cd% && npm run dev"
cd ..

echo.
echo ========================================
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:5173
echo   API Docs: http://localhost:8000/docs
echo ========================================
echo.
echo Close this window or press Ctrl+C in each terminal to stop.
pause
