@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 若使用虚拟环境，取消下面一行注释或手动 activate
if exist "%~dp0.venv\Scripts\activate.bat" call "%~dp0.venv\Scripts\activate.bat"

set "PY=python"
where py >nul 2>&1
if not errorlevel 1 set "PY=py -3"

REM 未设置 HF_ENDPOINT 时默认走 hf-mirror，避免仍请求 huggingface.co 导致 WinError 10060。强制官方站: set SQLDOCTOR_HF_OFFICIAL=1
if /i "%SQLDOCTOR_HF_OFFICIAL%"=="1" goto hf_default_done
if defined HF_ENDPOINT goto hf_default_done
set "HF_ENDPOINT=https://hf-mirror.com"
echo [SQL Doctor] Default HF_ENDPOINT=%HF_ENDPOINT% ^(set SQLDOCTOR_HF_OFFICIAL=1 to use huggingface.co^)
:hf_default_done

REM 知识库向量依赖缺失时后端会降级 RAG；启动前检测并自动 pip 安装（与 uvicorn 同一解释器）
REM 若不需要 RAG，可在 .env 设 KB_ENABLED=false，并可选: set SQLDOCTOR_SKIP_KB_PIP=1 跳过本段
if /i "%SQLDOCTOR_SKIP_KB_PIP%"=="1" goto after_kb_deps
echo [SQL Doctor] Checking RAG / vector Python packages ...
%PY% -c "import langchain_huggingface, sentence_transformers, faiss" 2>nul
if errorlevel 1 (
  echo [SQL Doctor] Installing langchain-huggingface, sentence-transformers, faiss-cpu ^(may take a while^) ...
  %PY% -m pip install "langchain-huggingface>=0.1.2" "sentence-transformers>=3.0.0" "faiss-cpu>=1.8.0" "python-dotenv>=1.0.0"
  if errorlevel 1 (
    echo [SQL Doctor] WARNING: pip failed. Check network or run: %PY% -m pip install -r requirements.txt
    echo [SQL Doctor] Or set KB_ENABLED=false in .env to skip knowledge base loading.
  )
)
:after_kb_deps

if not defined API_PORT set "API_PORT=8010"
REM Windows 上 --reload 易触发 WinError 10013（重载进程套接字）；默认关闭，需热重载时先: set SQLDOCTOR_RELOAD=1
if /i "%SQLDOCTOR_RELOAD%"=="1" (
  echo [SQL Doctor] Starting backend on port %API_PORT% with --reload ...
  start "SQLDoctor-Backend" cmd /k "cd /d ""%~dp0"" && %PY% -m uvicorn backend.main:app --host 127.0.0.1 --port %API_PORT% --reload"
) else (
  echo [SQL Doctor] Starting backend on port %API_PORT% ^(no --reload; set SQLDOCTOR_RELOAD=1 to enable^) ...
  start "SQLDoctor-Backend" cmd /k "cd /d ""%~dp0"" && %PY% -m uvicorn backend.main:app --host 127.0.0.1 --port %API_PORT%"
)

echo [SQL Doctor] Waiting for backend http://127.0.0.1:%API_PORT%/api/health ...
set WAIT_N=0
:wait_backend_loop
%PY% -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%API_PORT%/api/health', timeout=3).read()" 2>nul
if not errorlevel 1 goto wait_backend_ok
set /a WAIT_N+=1
if %WAIT_N% GEQ 120 (
  echo [SQL Doctor] WARNING: Backend not responding after 120s. Check window "SQLDoctor-Backend" ^(errors / wrong API_PORT^).
  echo [SQL Doctor] Next.js proxy will fail with ECONNREFUSED until FastAPI is listening on port %API_PORT%.
  goto wait_backend_done
)
timeout /t 1 /nobreak >nul
goto wait_backend_loop
:wait_backend_ok
echo [SQL Doctor] Backend is ready.
:wait_backend_done

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
  echo [SQL Doctor] Starting frontend with pnpm on port 3000 ^(API_PORT=%API_PORT%^) ...
  start "SQLDoctor-Frontend" cmd /k "cd /d ""%~dp0frontend"" && set API_PORT=%API_PORT% && pnpm run dev"
) else (
  echo [SQL Doctor] Starting frontend with npm on port 3000 ^(API_PORT=%API_PORT%^) ...
  start "SQLDoctor-Frontend" cmd /k "cd /d ""%~dp0frontend"" && set API_PORT=%API_PORT% && npm run dev"
)

echo.
echo Two windows were opened. Close each window to stop that service.
echo Backend: http://127.0.0.1:%API_PORT%
echo Frontend: http://127.0.0.1:3000
echo.
REM RAG 向量包启动时已按需自动安装；其余依赖建议: %PY% -m pip install -r requirements.txt
REM 前端若未自动安装: cd frontend ^&^& pnpm install 或 npm install
pause
