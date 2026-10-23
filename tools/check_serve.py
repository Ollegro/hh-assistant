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


def fetch():
    """(status, body) главной страницы; None, если сервер не отвечает."""
    try:
        with urllib.request.urlopen(URL, timeout=10) as resp:
            return resp.status, resp.read()
    except Exception:  # noqa: BLE001
        return None


answer = fetch()
proc = None
if answer:
    # Порт занят: скорее всего, сервер уже запущен (например, окно .bat). Проверяем его,
    # но честно предупреждаем: в работающем процессе код мог остаться от прошлого запуска.
    print("порт 8765 уже занят: проверяю запущенный сервер "
          "(его код может быть старее текущих файлов — перезапустите .bat)")
else:
    proc = subprocess.Popen([sys.executable, "cli.py", "serve"], cwd=APP,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(10):
        time.sleep(1)
        answer = fetch()
        if answer:
            break
try:
    if not answer:
        raise RuntimeError("сервер не ответил за 10 секунд")
    status, body = answer
    text = body.decode("utf-8", "replace")
    buttons = [name for name in ("/run/collect", "/run/score", "/run/letters", "/export", "/funnel")
               if name in text]
    print("GET %s -> %s, %d байт" % (URL, status, len(body)))
    print("кнопки на странице: %s" % (", ".join(buttons) if buttons else "кнопки не найдены"))
    print("редактор поисковых запросов: %s" % ("есть" if "name='queries'" in text else "НЕТ"))
    if "name='queries'" in text:
        print("кнопка сохранения запросов: %s" % (
            "есть" if "Сохранить запросы" in text else "НЕТ"))
except Exception as exc:  # noqa: BLE001
    print("ошибка: %s" % exc)
finally:
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        print("сервер остановлен")

