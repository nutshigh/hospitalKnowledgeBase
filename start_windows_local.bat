@echo off
setlocal enabledelayedexpansion

REM ==============================================
REM  Hospital AI System - Windows Local Startup
REM ==============================================

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "ERLANG_HOME=%USERPROFILE%\.local\erlang-26"
set "RABBITMQ_HOME=%USERPROFILE%\.local\rabbitmq_server-3.13.7"

echo.
echo ==============================================
echo   Hospital AI System - Docker-free Startup
echo ==============================================
echo.

REM -- 1. Check prerequisites --

echo [INFO] Checking prerequisites...

where mysql >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] MySQL not installed or not in PATH
    pause
    exit /b 1
)

where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] uv not installed or not in PATH
    pause
    exit /b 1
)

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not installed or not in PATH
    pause
    exit /b 1
)

echo [OK] Prerequisites check passed

REM -- 2. Start MySQL --

echo.
echo [INFO] Checking MySQL service...

sc query MySQL80 | find "RUNNING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] MySQL is already running
) else (
    echo [INFO] Starting MySQL...
    net start MySQL80 >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] MySQL failed to start. Run as Administrator.
        pause
        exit /b 1
    )
    echo [OK] MySQL started
)

REM -- 3. Start RabbitMQ --

echo.
echo [INFO] Checking RabbitMQ...

if not exist "%RABBITMQ_HOME%\sbin\rabbitmq-server.bat" (
    echo [ERROR] RabbitMQ not found: %RABBITMQ_HOME%
    pause
    exit /b 1
)

if not exist "%ERLANG_HOME%\bin\erl.exe" (
    echo [ERROR] Erlang 26 not found: %ERLANG_HOME%
    pause
    exit /b 1
)

set "PATH=%ERLANG_HOME%\bin;%PATH%"

call "%RABBITMQ_HOME%\sbin\rabbitmqctl.bat" status >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Starting RabbitMQ with Erlang 26...
    start "RabbitMQ" /MIN "%RABBITMQ_HOME%\sbin\rabbitmq-server.bat"
    timeout /t 12 /nobreak >nul
    call "%RABBITMQ_HOME%\sbin\rabbitmqctl.bat" status >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] RabbitMQ failed to start
        pause
        exit /b 1
    )
    echo [OK] RabbitMQ started
) else (
    echo [OK] RabbitMQ is already running
)

REM -- 4. Ensure databases exist --

echo.
echo [INFO] Checking databases...

mysql -uroot -proot -N -e "SELECT 1" 2>nul | find "1" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Cannot connect to MySQL (root/root)
    pause
    exit /b 1
)

mysql -uroot -proot -e "CREATE DATABASE IF NOT EXISTS hospital_template DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;" 2>nul
mysql -uroot -proot -e "CREATE DATABASE IF NOT EXISTS hospital_H001 DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;" 2>nul
echo [OK] Databases ready

REM -- 5. Install dependencies --

echo.
echo [INFO] Checking Python dependencies...
cd /d "%BACKEND%"
uv sync --quiet 2>&1
echo [OK] Python dependencies ready

echo [INFO] Checking frontend dependencies...
cd /d "%FRONTEND%"
if not exist "node_modules" (
    echo [INFO] Installing frontend dependencies...
    call npm install --silent 2>&1
)
echo [OK] Frontend dependencies ready

REM -- 6. Start backend --

echo.
echo [INFO] Starting backend on port 8000...
cd /d "%BACKEND%"
start "Hospital-Backend" /MIN uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

echo [INFO] Waiting for backend to be ready...
for /L %%i in (1,1,30) do (
    curl -s http://localhost:8000/api/v1/health 2>nul | find "ok" >nul 2>&1
    if !errorlevel! equ 0 goto :backend_ready
    timeout /t 1 /nobreak >nul
)
echo [WARN] Backend may not be ready yet, continuing...
:backend_ready
curl -s http://localhost:8000/api/v1/health 2>nul | find "ok" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Backend ready: http://localhost:8000
) else (
    echo [WARN] Backend health check failed, check http://localhost:8000
)

REM -- 7. Start frontends --

echo.
echo [INFO] Starting frontends...
cd /d "%FRONTEND%"

start "Hospital-User" /MIN npm run dev -w @hospital/user-portal -- --port 3001
start "Hospital-Doctor" /MIN npm run dev -w @hospital/doctor-portal -- --port 3002
start "Hospital-Admin" /MIN npm run dev -w @hospital/admin-portal -- --port 3003

timeout /t 6 /nobreak >nul

REM -- 8. Summary --

echo.
echo +--------------------------------------------------+
echo ¦         All services started (Docker-free)        ¦
echo +--------------------------------------------------+
echo ¦  Backend API:   http://localhost:8000             ¦
echo ¦  User Portal:   http://localhost:3001             ¦
echo ¦  Doctor Portal: http://localhost:3002             ¦
echo ¦  Admin Portal:  http://localhost:3003             ¦
echo +--------------------------------------------------+
echo ¦  MySQL:    localhost:3306 (root/root)             ¦
echo ¦  RabbitMQ: localhost:5672 (guest/guest)           ¦
echo +--------------------------------------------------+
echo.
echo Register test user:
echo   curl -X POST http://localhost:8000/api/v1/auth/register -H "Content-Type: application/json" -d "{\"username\":\"user1\",\"password\":\"123456\",\"role\":\"user\",\"hospital_id\":\"H001\"}"
echo.
echo Press any key to stop all services...
pause >nul

REM -- 9. Cleanup --

echo.
echo [INFO] Stopping services...
taskkill /FI "WINDOWTITLE eq Hospital-Backend" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Hospital-User" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Hospital-Doctor" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Hospital-Admin" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq RabbitMQ" /T /F >nul 2>&1
echo [OK] Services stopped
exit /b 0
