# -*- coding: utf-8 -*-
"""Проверка веб-сервера: поднимает локальный сервер, делает запросы, останавливает.

Запуск: python tools/check_hh_server.py
"""
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from hhassist import storage, web  # noqa: E402

PORT = 8799
server = ThreadingHTTPServer(("127.0.0.1", PORT), web.Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
time.sleep(0.7)

lines = []
try:
    for path in ("/", "/export"):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=10) as r:
                body = r.read()
                lines.append("%-10s -> %s, %d байт%s" % (
                    path, r.status, len(body),
                    ", статус LLM=%s" % (b"LLM" in body) if path == "/" else ""))
        except Exception as e:
            lines.append("%-10s -> ошибка %s" % (path, e))

    # проверка кнопок веб-интерфейса: POST /run/score (безопасная переоценка)
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        """Не идём по редиректу, чтобы увидеть сам код 303 и Location."""

        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/run/score" % PORT, data=b"", method="POST")
        with opener.open(req, timeout=120) as r:
            lines.append("%-10s -> %s, location=%s" % ("/run/score", r.status, r.headers.get("Location")))
    except urllib.error.HTTPError as e:
        lines.append("%-10s -> %s, location=%s" % ("/run/score", e.code, e.headers.get("Location")))
    except Exception as e:
        lines.append("%-10s -> ошибка %s" % ("/run/score", e))

    # кнопка генерации писем (офлайн-режим, без сети)
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/run/letters" % PORT, data=b"", method="POST")
        with opener.open(req, timeout=180) as r:
            lines.append("%-12s -> %s, location=%s" % ("/run/letters", r.status, r.headers.get("Location")))
    except urllib.error.HTTPError as e:
        lines.append("%-12s -> %s, location=%s" % ("/run/letters", e.code, e.headers.get("Location")))
    except Exception as e:
        lines.append("%-12s -> ошибка %s" % ("/run/letters", e))

    conn = storage.connect()
    lines.append("писем в базе: %d" % storage.stats(conn)["letters"])
    row = conn.execute("SELECT vacancy_id FROM letters LIMIT 1").fetchone()
    conn.close()
    if row:
        path = "/v/%s" % row["vacancy_id"]
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=10) as r:
            body = r.read()
            lines.append("%-10s -> %s, %d байт, textarea=%s, профиль=%s" % (
                "/v/…", r.status, len(body), b"textarea" in body,
                "Сопоставление с профилем".encode("utf-8") in body))
finally:
    server.shutdown()
    server.server_close()

out = os.path.join(tempfile.gettempdir(), "hh_server_check.txt")
open(out, "w", encoding="utf-8").write("\n".join(lines))
print("written:", out)
