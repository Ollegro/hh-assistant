@echo off
chcp 65001 >nul
title hh-assistant: тесты и регресс
rem Run the unittest suite, then the health checks and the DB stats.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist "out" mkdir "out"
set REPORT=out\regress.txt
if exist "%REPORT%" del "%REPORT%"
echo === 1. Тесты (unittest) ===
echo.
python -m unittest discover -s tests -p "test_*.py" >> "%REPORT%" 2>&1
echo === 2. Сервер и кнопки (tools\check_hh_server.py) ===
python tools\check_hh_server.py >> "%REPORT%" 2>&1
echo === 3. Запуск как в .bat (tools\check_serve.py) ===
python tools\check_serve.py >> "%REPORT%" 2>&1
echo === 4. База (stats) ===
python tools\run_hh_assistant.py stats >> "%REPORT%" 2>&1
type "%REPORT%"
echo.
echo Отчёт сохранён: %REPORT%
echo Ожидается: Ran 108 tests ... OK, проверки сервера зелёные.
pause
