@echo off
REM ServiceNow MCP Server (Pure Python)
REM Runs MCP Server over stdio or HTTP streamable-http / SSE

cd /d "%~dp0"
set PYTHONPATH=%~dp0src;%PYTHONPATH%

echo.
echo ====================================================
echo  ServiceNow Model Context Protocol (MCP) Server
echo  Pure Python Implementation
echo ====================================================
echo.

if "%1"=="--help" goto USAGE
if "%1"=="-h" goto USAGE

python -m servicenow_mcp %*
goto END

:USAGE
echo Usage:
echo   run.bat                     (runs stdio transport for Claude/Cursor/Copilot)
echo   run.bat --list-tools        (prints all 40+ registered ServiceNow tools)
echo   run.bat --test-connection   (tests live connectivity to instance)
echo   run.bat --transport sse --port 8002
echo   run.bat --transport streamable-http --port 8002
echo.

:END
