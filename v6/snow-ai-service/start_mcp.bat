@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  SoftProdigy Snow Agent — MCP Server
REM  Starts the FastMCP server on http://127.0.0.1:8002/mcp
REM
REM  Add this URL to any MCP client (e.g. Claude Desktop):
REM    http://127.0.0.1:8002/mcp
REM
REM  Required HTTP headers per request:
REM    x-servicenow-instance-url  : https://your-instance.service-now.com
REM    x-servicenow-username      : your_username
REM    x-servicenow-password      : your_password
REM
REM  Optional: set MCP_API_KEY in the environment below to enable API-key auth.
REM ─────────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating the project virtual environment...
  python -m venv .venv || (echo Could not create the environment. & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
if not exist ".venv\installed.ok" (
  echo Installing dependencies...
  python -m pip install -r requirements.txt || (echo Install failed. & pause & exit /b 1)
  echo ok>.venv\installed.ok
)

echo.
echo  ====================================================
echo   SoftProdigy Snow Agent - MCP Server
echo   Endpoint: http://127.0.0.1:8002/mcp
echo   Transport: streamable-http
echo  ====================================================
echo.

python snow_mcp.py --host 127.0.0.1 --port 8002 --transport streamable-http --log-level INFO

pause
