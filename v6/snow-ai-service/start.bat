@echo off
echo Starting SoftProdigy Snow Agent Backend (port 8001)...
python -m uvicorn api:app --host 127.0.0.1 --port 8001 --reload
pause
