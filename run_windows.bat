@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 (echo Install Python 3.11+ and retry.& pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not exist ".env" copy ".env.example" ".env" >nul
echo After the server starts, open http://127.0.0.1:8000 in your browser.
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
