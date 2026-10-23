# -*- coding: utf-8 -*-
"""Запуск hh-assistant из ASCII-пути (кириллица в пути ломает консоль Windows).

Примеры:
    python tools/run_hh_assistant.py stats
    python tools/run_hh_assistant.py collect --query-file config/queries.txt --pages 1 --limit 5
    python tools/run_hh_assistant.py score
    python tools/run_hh_assistant.py letters
    python tools/run_hh_assistant.py serve
"""
import os
import runpy
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.chdir(APP)
sys.path.insert(0, APP)
runpy.run_path(os.path.join(APP, "cli.py"), run_name="__main__")
