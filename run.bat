@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 若使用虚拟环境，取消下面一行注释或手动 activate
if exist "%~dp0.venv\Scripts\activate.bat" call "%~dp0.venv\Scripts\activate.bat"

set "PY=python"
where py >nul 2>&1
if not errorlevel 1 set "PY=py -3"

if not defined API_PORT set "API_PORT=8010"
REM Windows 上 --reload 易触发 WinError 10013（重载进程套接字）；默认关闭，需热重载时先: set SQLDOCTOR_RELOAD=1
if /i "%SQLDOCTOR_RELOAD%"=="1" (
  echo [SQL Doctor] Starting backend on port %API_PORT% with --reload ...
  start "SQLDoctor-Backend" cmd /k "cd /d ""%~dp0"" && %PY% -m uvicorn backend.main:app --host 127.0.0.1 --port %API_PORT% --reload"
) else (
  echo [SQL Doctor] Starting backend on port %API_PORT% ^(no --reload; set SQLDOCTOR_RELOAD=1 to enable^) ...
  start "SQLDoctor-Backend" cmd /k "cd /d ""%~dp0"" && %PY% -m uvicorn backend.main:app --host 127.0.0.1 --port %API_PORT%"
)

timeout /t 2 /nobreak >nul

REM 未安装依赖时 next 会报「不是内部或外部命令」；先补齐 node_modules
if not exist "%~dp0frontend\node_modules\next\dist\bin\next" (
  echo [SQL Doctor] frontend\node_modules 缺失，正在安装前端依赖 ...
  pushd "%~dp0frontend"
  where pnpm >nul 2>&1
  if not errorlevel 1 (
    call pnpm install
  ) else (
    call npm install
  )
  popd
)

where pnpm >nul 2>&1
if not errorlevel 1 (
  echo [SQL Doctor] Starting frontend with pnpm on port 3000 ...
  start "SQLDoctor-Frontend" cmd /k "cd /d ""%~dp0frontend"" && pnpm run dev"
) else (
  echo [SQL Doctor] Starting frontend with npm on port 3000 ...
  start "SQLDoctor-Frontend" cmd /k "cd /d ""%~dp0frontend"" && npm run dev"
)

echo.
echo Two windows were opened. Close each window to stop that service.
echo Backend: http://127.0.0.1:%API_PORT%
echo Frontend: http://127.0.0.1:3000
echo.
REM 以下仅为说明，不会执行 install，避免 echo 行被 cmd 误解析
REM 1. Python: py -3 -m pip install -r requirements.txt
REM 2. Frontend: cd frontend , then pnpm install or npm install
pause
