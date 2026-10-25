# -*- coding: utf-8 -*-
"""Тесты руководства пользователя: содержание страницы и маршрут /help."""
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP  # noqa: E402,F401

from hhassist import storage, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402

ANCHORS = ("what", "login", "start", "main", "filters", "card",
           "profile", "buttons", "limits", "problems", "daily")


def offline_settings(**over):
    data = {"profile": dict(DEFAULT_PROFILE), "daily_limit": 200,
            "collect_limit": 20, "collect_pages": 1, "collect_queries": 0}
    data.update(over)
    return Settings(**data)


class HelpContentTest(unittest.TestCase):
    """Страница руководства: разделы, оглавление и понятные формулировки."""

    @classmethod
    def setUpClass(cls):
        cls.page = web.render_help()

    def test_has_title_and_intro(self):
        self.assertIn("Руководство пользователя", self.page)
        self.assertIn("Отклики отправляете", self.page)

    def test_all_sections_have_anchors_and_toc_links(self):
        for anchor in ANCHORS:
            self.assertIn("id='%s'" % anchor, self.page)
            self.assertIn("href='#%s'" % anchor, self.page)

    def test_describes_key_actions(self):
        for text in ("Собрать вакансии", "Оценить", "Сгенерировать письма",
                     "Сбросить фильтры", "Очистить базу", "Профиль соискателя",
                     "Применить к профилю"):
            self.assertIn(text, self.page)

    def test_mentions_limits_and_manual_sending(self):
        self.assertIn("дневной лимит", self.page.lower())
        self.assertIn("сами на hh.ru", self.page)

    def test_no_secrets_in_guide(self):
        for secret in ("WEB_AUTH_PASSWORD=", "HH_APP_TOKEN=", "LLM_API_KEY=", "sk-"):
            self.assertNotIn(secret, self.page)

    def test_print_styles_hide_navigation(self):
        self.assertIn("@media print", web.CSS)
        self.assertIn(".guide .toc", web.CSS)


class HelpRouteTest(unittest.TestCase):
    """GET /help отдаёт страницу (вход проверяется общим обработчиком)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH",
                                    os.path.join(self.tmp.name, "test.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        settings = offline_settings()
        p = mock.patch.object(web, "get_settings", lambda: settings)
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

    def test_help_page_opens(self):
        with urllib.request.urlopen(self.base + "/help", timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
        self.assertEqual(resp.status, 200)
        self.assertIn("Руководство пользователя", body)
        self.assertIn("id='start'", body)


if __name__ == "__main__":
    unittest.main()
