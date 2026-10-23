@echo off
REM SoftProdigy Snow Agent - one-click start (installs on first run)
REM Starts both the main API (port 8001) and the MCP server (port 8002)

cd /d "%~dp0snow-ai-service"
where python >nul 2>nul || (echo Python is not installed or not on PATH. Install Python 3.11+ from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" (
  echo First run: creating the Python environment...
  python -m venv .venv || (echo Could not create the environment. & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
if not exist ".venv\installed.ok" (
  echo Installing requirements - this takes a few minutes the first time...
  python -m pip install --upgrade pip >nul
  pip install -r requirements.txt || (echo Install failed - send the error above. & pause & exit /b 1)
  echo ok> .venv\installed.ok
)
if not exist ".venv\playwright.ok" (
  echo Installing the Flow Designer browser engine ^(Playwright + Chromium^) for flows and subflows...
  pip install "playwright>=1.40" && python -m playwright install chromium && echo ok> .venv\playwright.ok
  if not exist ".venv\playwright.ok" echo WARNING: Playwright install failed - the browser fallback for flows/subflows will not be available.
)
if "%AGENT_PORT%"=="" set AGENT_PORT=8001
if "%AGENT_HOST%"=="" set AGENT_HOST=127.0.0.1

REM Open the browser after a short delay
start "" cmd /c "timeout /t 8 >nul & start http://localhost:%AGENT_PORT%/ui"

REM Start the Flow Server in a separate window (port 3000): builds flows & subflows automatically with the
REM ServiceNow SDK. Needs Node.js 20+. Without it Build App uses the Flow Designer browser only.
set "FLOWSRV=%~dp0flow-server"
where node >nul 2>nul
if errorlevel 1 (
  echo WARNING: Node.js not found - flows/subflows will be built in the browser only. Install Node.js 20+ from nodejs.org.
) else (
  if not exist "%FLOWSRV%\node_modules\" (
    echo Installing the Flow Server ^(ServiceNow SDK^) - first run only, takes a few minutes...
    pushd "%FLOWSRV%"
    call npm.cmd install --no-audit --no-fund
    popd
  )
  echo Starting Flow Server on http://127.0.0.1:3000 ...
  start "Snow Agent Flow Server" cmd /k "cd /d "%FLOWSRV%" && npm.cmd run dev"
)

REM Start the MCP server in a separate window (port 8002)
echo Starting MCP server on http://127.0.0.1:8002/mcp ...
start "Snow Agent MCP Server" cmd /k "cd /d "%~dp0snow-ai-service" && call .venv\Scripts\activate.bat && python snow_mcp.py --host 127.0.0.1 --port 8002 --transport streamable-http"

REM Start the main API
echo Starting main API on http://localhost:%AGENT_PORT% ...
python api.py
pause
