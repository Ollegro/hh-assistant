@echo off
chcp 65001 >nul
title hh-assistant: проверка LLM
rem Diagnose the LLM loop: .env settings -> connection -> scoring -> letter,
rem then compare rule scores with model scores on vacancies from the database.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist "out" mkdir "out"
set REPORT1=out\llm-check.txt
set REPORT2=out\score-compare.txt
echo === 1. Диагностика LLM (check_llm.py) ===
echo.
python tools\check_llm.py > "%REPORT1%" 2>&1
type "%REPORT1%"
echo.
echo === 2. Сравнение скоринга (compare_scores.py) ===
echo.
python tools\compare_scores.py --limit 15 > "%REPORT2%" 2>&1
type "%REPORT2%"
echo.
echo Отчёты сохранены: %REPORT1%, %REPORT2%
echo Если выше ошибка — пришлите один из этих файлов.
pause
