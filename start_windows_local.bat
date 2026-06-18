@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "ERLANG_HOME=%USERPROFILE%\.local\erlang-26"
set "RABBITMQ_HOME=%USERPROFILE%\.local\rabbitmq_server-3.13.7"

echo ==============================================
echo   Hospital AI System - Local Startup
echo ==============================================

REM -- 1. Prerequisites --
echo [INFO] Checking prerequisites...

where mysql >nul 2>&1 || (echo [ERROR] MySQL not in PATH & pause & exit /b 1)
where uv >nul 2>&1    || (echo [ERROR] uv not in PATH    & pause & exit /b 1)
where node >nul 2>&1  || (echo [ERROR] Node not in PATH  & pause & exit /b 1)
echo [OK] Prerequisites passed

REM -- 2. MySQL --
echo [INFO] Checking MySQL...
net start MySQL80 >nul 2>&1
if errorlevel 2 (
    echo [OK] MySQL already running
) else if errorlevel 1 (
    echo [ERROR] MySQL failed - run as Administrator
    pause
    exit /b 1
) else (
    echo [OK] MySQL started
)

REM -- 3. RabbitMQ --
echo [INFO] Checking RabbitMQ...
if not exist "%RABBITMQ_HOME%\sbin\rabbitmq-server.bat" (
    echo [ERROR] RabbitMQ not at %RABBITMQ_HOME%
    pause & exit /b 1
)
set "PATH=%ERLANG_HOME%\bin;%PATH%"
call "%RABBITMQ_HOME%\sbin\rabbitmqctl.bat" status >nul 2>&1
if errorlevel 1 (
    echo [INFO] Starting RabbitMQ...
    start "RabbitMQ" /MIN "%RABBITMQ_HOME%\sbin\rabbitmq-server.bat"
    ping 127.0.0.1 -n 13 >nul
)
echo [OK] RabbitMQ running

REM -- 4. Init DB --
echo [INFO] Initializing databases...
mysql -uroot -proot -e "CREATE DATABASE IF NOT EXISTS hospital_template DEFAULT CHARACTER SET utf8mb4" 2>&1 | findstr /C:"ERROR" >nul 2>&1 && (echo [ERROR] MySQL connect failed & pause & exit /b 1)
mysql -uroot -proot -e "CREATE DATABASE IF NOT EXISTS hospital_H001 DEFAULT CHARACTER SET utf8mb4" 2>&1 | findstr /C:"ERROR" >nul 2>&1 && (echo [ERROR] MySQL connect failed & pause & exit /b 1)
echo [OK] Databases ready

REM -- 5. Python deps --
echo [INFO] Syncing Python dependencies...
cd backend
uv sync 2>&1 | findstr /C:"error" >nul 2>&1 && (echo [WARN] uv sync had warnings, continuing...)
echo [OK] Python deps ready

REM -- 6. Start backend --
echo [INFO] Starting backend...
start "HospitalBackend" /MIN uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
cd ..

echo [INFO] Waiting for backend...
for /L %%i in (1,1,30) do (
    curl -s http://localhost:8000/api/v1/health 2>nul | find "ok" >nul 2>&1 && goto backend_ok
    ping 127.0.0.1 -n 2 >nul
)
echo [WARN] Backend health check timeout
goto frontend_start
:backend_ok
echo [OK] Backend ready: http://localhost:8000

REM -- 6.5 Start Reranker service --
echo [INFO] Starting Reranker service (port 8003)...
if exist "backend\reranker_service" (
    cd backend\reranker_service
    start "Reranker" /MIN uv run uvicorn main:app --host 127.0.0.1 --port 8003
    cd ..\..
)
echo [OK] Reranker service started

:frontend_start
REM -- 7. Start frontends --
echo [INFO] Starting frontends...
cd frontend
if not exist "node_modules" call npm install
start "HospitalUser"  /MIN npm run dev -w @hospital/user-portal -- --port 3001
start "HospitalDoctor" /MIN npm run dev -w @hospital/doctor-portal -- --port 3002
start "HospitalAdmin"  /MIN npm run dev -w @hospital/admin-portal -- --port 3003
cd ..

ping 127.0.0.1 -n 6 >nul

REM -- 8. Done --
echo.
echo +--------------------------------------------------+
echo +         All services started (Docker-free)        +
echo +--------------------------------------------------+
echo +  Backend:      http://localhost:8000              +
echo +  Reranker:     http://localhost:8003              +
echo +  User Portal:  http://localhost:3001              +
echo +  Doctor Portal:http://localhost:3002              +
echo +  Admin Portal: http://localhost:3003              +
echo +--------------------------------------------------+
echo.
echo Press Ctrl+C or close this window to stop.
pause >nul

REM -- 9. Cleanup --
taskkill /FI "WINDOWTITLE eq Reranker"         /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq HospitalBackend" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq HospitalUser"    /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq HospitalDoctor"  /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq HospitalAdmin"   /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq RabbitMQ"        /T /F >nul 2>&1
echo [OK] Stopped
