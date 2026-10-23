@echo off
title hh-assistant
rem Start hh-assistant: web UI on http://127.0.0.1:8765
cd /d "%~dp0"
echo Starting hh-assistant...
start "" /min cmd /c "python tools\run_hh_assistant.py serve"
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8765
echo.
echo Server runs in a separate minimized window. Close it to stop.
echo Browser should open at http://127.0.0.1:8765
pause
