@echo off
chcp 65001 >nul
title hh-assistant
rem Сервер работает в этом же окне: второе окно консоли больше не открывается.
rem Остановить — Ctrl+C или закрыть окно.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo hh-assistant: http://127.0.0.1:8765
echo Остановить: Ctrl+C или закрыть это окно.
echo.
start "" /b cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:8765"
python tools\run_hh_assistant.py serve
echo.
echo Сервер остановлен.
pause
