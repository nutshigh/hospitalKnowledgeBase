@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: ──────────────────────────────────────────────
::  Hospital AI System — Windows 本地快速启动
:: ──────────────────────────────────────────────

set ROOT=%~dp0
set BACKEND=%ROOT%backend
set FRONTEND=%ROOT%frontend
set ERLANG_HOME=%USERPROFILE%\.local\erlang-26
set RABBITMQ_HOME=%USERPROFILE%\.local\rabbitmq_server-3.13.7

echo.
echo ==============================================
echo   Hospital AI System — 本地启动 (Docker-free)
echo ==============================================
echo.

:: ── 1. Check prerequisites ──────────────────────

echo [INFO] 检查前置依赖...

where mysql >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] MySQL 未安装或未加入 PATH
    exit /b 1
)

where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] uv 未安装或未加入 PATH
    exit /b 1
)

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js 未安装或未加入 PATH
    exit /b 1
)

echo [OK] 依赖检查通过

:: ── 2. Start MySQL ──────────────────────────────

echo.
echo [INFO] 检查 MySQL 服务...

sc query MySQL80 | find "RUNNING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] MySQL 已在运行
) else (
    echo [INFO] 正在启动 MySQL...
    net start MySQL80 >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] MySQL 启动失败，请以管理员身份运行此脚本
        exit /b 1
    )
    echo [OK] MySQL 已启动
)

:: ── 3. Start RabbitMQ ───────────────────────────

echo.
echo [INFO] 检查 RabbitMQ...

if not exist "%RABBITMQ_HOME%\sbin\rabbitmq-server.bat" (
    echo [ERROR] RabbitMQ 未找到: %RABBITMQ_HOME%
    echo        请先安装 RabbitMQ 便携版到 %USERPROFILE%\.local\rabbitmq_server-3.13.7
    exit /b 1
)

if not exist "%ERLANG_HOME%\bin\erl.exe" (
    echo [ERROR] Erlang 26 未找到: %ERLANG_HOME%
    echo        请先安装 Erlang 26 到 %USERPROFILE%\.local\erlang-26
    exit /b 1
)

"%RABBITMQ_HOME%\sbin\rabbitmqctl.bat" status >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] 正在启动 RabbitMQ (Erlang 26)...
    set PATH=%ERLANG_HOME%\bin;%PATH%
    start "RabbitMQ" /B "%RABBITMQ_HOME%\sbin\rabbitmq-server.bat" >nul 2>&1
    timeout /t 10 /nobreak >nul
    "%RABBITMQ_HOME%\sbin\rabbitmqctl.bat" status >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] RabbitMQ 启动失败
        exit /b 1
    )
    echo [OK] RabbitMQ 已启动
) else (
    echo [OK] RabbitMQ 已在运行
)

:: ── 4. Ensure databases exist ───────────────────

echo.
echo [INFO] 检查数据库...

mysql -uroot -proot -N -e "SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='hospital_template'" 2>nul | find "1" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] 创建数据库...
    mysql -uroot -proot -e "CREATE DATABASE IF NOT EXISTS hospital_template DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;" 2>nul
    mysql -uroot -proot -e "CREATE DATABASE IF NOT EXISTS hospital_H001 DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;" 2>nul
)
echo [OK] 数据库就绪

:: ── 5. Install dependencies ─────────────────────

echo.
echo [INFO] 检查 Python 依赖...
cd /d "%BACKEND%"
uv sync --quiet 2>&1
echo [OK] Python 依赖就绪

echo [INFO] 检查前端依赖...
cd /d "%FRONTEND%"
if not exist "node_modules" (
    echo [INFO] 正在安装前端依赖...
    call npm install --silent 2>&1
)
echo [OK] 前端依赖就绪

:: ── 6. Start backend ────────────────────────────

echo.
echo [INFO] 启动后端 (port 8000)...
cd /d "%BACKEND%"
start "Hospital-Backend" /B uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 >nul 2>&1

:: Wait for backend
echo [INFO] 等待后端就绪...
for /L %%i in (1,1,20) do (
    curl -s http://localhost:8000/api/v1/health 2>nul | find "ok" >nul 2>&1
    if !errorlevel! equ 0 goto :backend_ready
    timeout /t 1 /nobreak >nul
)
:backend_ready
echo [OK] 后端就绪: http://localhost:8000

:: ── 7. Start frontends ──────────────────────────

echo.
echo [INFO] 启动前端...
cd /d "%FRONTEND%"

start "Hospital-User-Portal" /B npm run dev -w @hospital/user-portal -- --port 3001 >nul 2>&1
start "Hospital-Doctor-Portal" /B npm run dev -w @hospital/doctor-portal -- --port 3002 >nul 2>&1
start "Hospital-Admin-Portal" /B npm run dev -w @hospital/admin-portal -- --port 3003 >nul 2>&1

timeout /t 5 /nobreak >nul

:: ── 8. Summary ──────────────────────────────────

echo.
echo ╔══════════════════════════════════════════════╗
echo ║          所有服务已启动 (Docker-free)          ║
echo ╠══════════════════════════════════════════════╣
echo ║  后端 API:     http://localhost:8000          ║
echo ║  用户端:       http://localhost:3001          ║
echo ║  医生端:       http://localhost:3002          ║
echo ║  管理后台:     http://localhost:3003          ║
echo ╠══════════════════════════════════════════════╣
echo ║  MySQL:        localhost:3306 (root/root)     ║
echo ║  RabbitMQ:     localhost:5672 (guest/guest)  ║
echo ║  管理面板:     http://localhost:15672         ║
echo ╠══════════════════════════════════════════════╣
echo ║  注册测试用户:                                 ║
echo ║  curl -X POST localhost:8000/api/v1/auth/register -H "Content-Type: application/json" -d "{\"username\":\"user1\",\"password\":\"123456\",\"role\":\"user\",\"hospital_id\":\"H001\"}" ║
echo ╚══════════════════════════════════════════════╝
echo.
echo 按任意键关闭所有服务窗口...
pause >nul

:: ── 9. Cleanup on exit ──────────────────────────

echo [INFO] 正在关闭服务...
taskkill /FI "WINDOWTITLE eq Hospital-Backend" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Hospital-User-Portal" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Hospital-Doctor-Portal" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Hospital-Admin-Portal" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq RabbitMQ" /T /F >nul 2>&1
echo [OK] 已关闭
exit /b 0
