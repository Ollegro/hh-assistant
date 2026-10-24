# -*- coding: utf-8 -*-
"""Тесты оформления: фон интерфейса (hhassist/static) и его выдача сервером.

Часть проверок — чистые функции (белый список имён и типов), часть — реальный
локальный сервер на петлевом адресе: статус, MIME-тип, размер и заголовок кэша.
"""
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP  # noqa: E402,F401

from hhassist import queue, storage, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402


def no_password_settings():
    """Без пароля: запросы с 127.0.0.1 проходят без заголовка Authorization."""
    return Settings(profile=dict(DEFAULT_PROFILE))


class StaticFileTest(unittest.TestCase):
    """Белый список: только простые имена с известным расширением."""

    def test_background_exists(self):
        path = web.static_file("bg.jpg")
        self.assertTrue(path)
        self.assertTrue(os.path.isfile(path))
        self.assertGreater(os.path.getsize(path), 10000)

    def test_traversal_and_unknown_rejected(self):
        for name in ("../web.py", "..\\web.py", "static/bg.jpg", "", None,
                     "bg.exe", "bg.jpg.py", "nope.jpg", "storage.py"):
            self.assertIsNone(web.static_file(name), name)

    def test_mime_types(self):
        self.assertEqual(web.STATIC_TYPES[".jpg"], "image/jpeg")
        self.assertEqual(web.STATIC_TYPES[".png"], "image/png")
        self.assertEqual(web.STATIC_TYPES[".svg"], "image/svg+xml")

    def test_css_uses_background(self):
        self.assertIn("/static/bg.jpg", web.CSS)
        self.assertIn("backdrop-filter", web.CSS)

    def test_background_url_is_versioned(self):
        """Версия в ?v= обязательна: иначе браузер отдаст старую картинку из кэша."""
        self.assertRegex(web.CSS, r"/static/bg\.jpg\?v=\d+")


class ServeStaticTest(unittest.TestCase):
    """Локальный сервер: фон отдаётся с MIME-типом и кэшем, мусор — 404."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH",
                                    os.path.join(self.tmp.name, "test.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        for module in (web, queue):
            p = mock.patch.object(module, "get_settings", no_password_settings)
            p.start()
            self.addCleanup(p.stop)
        quiet = mock.patch.object(web.Handler, "log_message", lambda *a, **k: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as response:
            return response.status, response.headers, response.read()

    def test_background_served_with_cache_headers(self):
        status, headers, body = self.get("/static/bg.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/jpeg")
        self.assertTrue(body.startswith(b"\xff\xd8"))          # JPEG: начало файла
        self.assertEqual(int(headers.get("Content-Length")), len(body))
        self.assertIn("max-age", headers.get("Cache-Control", ""))

    def test_unknown_static_is_404(self):
        for path in ("/static/nope.jpg", "/static/web.py", "/static/"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(path)
            self.assertEqual(ctx.exception.code, 404, path)

    def test_index_page_uses_background_and_viewport(self):
        status, _headers, body = self.get("/")
        self.assertEqual(status, 200)
        text = body.decode("utf-8", "replace")
        self.assertIn("/static/bg.jpg", text)
        self.assertIn("width=device-width", text)


if __name__ == "__main__":
    unittest.main()

