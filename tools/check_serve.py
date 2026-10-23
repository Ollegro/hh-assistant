# -*- coding: utf-8 -*-
"""Проверка реального запуска `serve` (тот же путь, что в Запустить hh-assistant.bat).

Запуск: python tools/check_serve.py
"""
import os
import subprocess
import sys
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "http://127.0.0.1:8765/"

proc = subprocess.Popen([sys.executable, "cli.py", "serve"], cwd=APP,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(4)
    with urllib.request.urlopen(URL, timeout=10) as resp:
        body = resp.read()
    buttons = [name for name in ("/run/collect", "/run/score", "/run/letters", "/export")
               if name.encode("utf-8") in body]
    print("GET %s -> %s, %d байт" % (URL, resp.status, len(body)))
    print("кнопки на странице: %s" % ", ".join(buttons) if buttons else "кнопки не найдены")
except Exception as exc:  # noqa: BLE001
    print("ошибка: %s" % exc)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        proc.kill()
    print("сервер остановлен")
