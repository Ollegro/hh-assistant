# -*- coding: utf-8 -*-
"""Тесты картинки «идёт сбор»: книга с перелистываемыми страницами и лупа.

Проверяется, что анимация есть только во время сбора, не ломает страницу и
не тянет внешние файлы; для prefers-reduced-motion движение отключается.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401

from hhassist import queue, storage, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402


def offline_settings(**over):
    data = {"profile": dict(DEFAULT_PROFILE), "daily_limit": 200,
            "collect_limit": 40, "collect_total": 200, "collect_pages": 1,
            "collect_queries": 0}
    data.update(over)
    return Settings(**data)


class Base(unittest.TestCase):
    """Временная база; состояние RUN сохраняется и возвращается после теста."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH",
                                    os.path.join(self.tmp.name, "test.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = storage.connect()
        self.addCleanup(self.conn.close)
        saved_run = dict(web.RUN)
        self.addCleanup(lambda: (web.RUN.clear(), web.RUN.update(saved_run)))
        settings = offline_settings()
        for module in (web, queue):
            p = mock.patch.object(module, "get_settings", lambda: settings)
            p.start()
            self.addCleanup(p.stop)

    def collecting(self, kind="collect", done=7):
        web.RUN.update({"kind": kind, "title": "сбор вакансий", "done": done,
                        "started": "2026-10-28 09:20", "report": "", "error": "",
                        "running": True})


class IndexIndicatorTest(Base):
    MARK = "<div class='collect-visual'"

    def test_shown_while_collecting(self):
        self.collecting(done=7)
        page = web.render_index(self.conn, 10)
        self.assertIn(self.MARK, page)
        self.assertIn("class='book'", page)
        self.assertIn("lens-orbit", page)
        self.assertIn("идёт сбор вакансий", page)
        self.assertIn("обработано карточек: 7", page)

    def test_hidden_when_idle(self):
        self.assertNotIn(self.MARK, web.render_index(self.conn, 10))

    def test_hidden_for_other_actions(self):
        """Кнопки «Оценить» и «Письма» книгу не показывают — она про сбор."""
        self.collecting(kind="score")
        self.assertNotIn(self.MARK, web.render_index(self.conn, 10))

    def test_hidden_after_finish(self):
        self.collecting()
        web.RUN["running"] = False
        web.RUN["report"] = "собрано вакансий: 5 из 9 найденных"
        self.assertNotIn(self.MARK, web.render_index(self.conn, 10))

    def test_page_still_auto_refreshes(self):
        self.collecting()
        self.assertIn("http-equiv='refresh'", web.render_index(self.conn, 10))


class CardIndicatorTest(Base):
    def test_card_shows_indicator_too(self):
        storage.upsert_vacancy(self.conn, sample_vacancy(id="1"))
        self.collecting()
        page = web.render_card(self.conn, "1")
        self.assertIn("<div class='collect-visual'", page)


class VisualMarkupTest(unittest.TestCase):
    """Анимация самодостаточна: только CSS, доступна и уважает reduced motion."""

    def test_css_has_keyframes(self):
        for rule in ("@keyframes page-flip", "@keyframes lens-spin",
                     "@keyframes lens-spin-rev", ".collect-visual"):
            self.assertIn(rule, web.CSS)

    def test_motion_can_be_disabled(self):
        self.assertIn("prefers-reduced-motion", web.CSS)

    def test_no_external_images_or_scripts(self):
        saved_run = dict(web.RUN)
        self.addCleanup(lambda: (web.RUN.clear(), web.RUN.update(saved_run)))
        web.RUN.update({"kind": "collect", "running": True, "done": 0})
        block = web.run_indicator()
        self.assertNotIn("<img", block)
        self.assertNotIn("<script", block)
        self.assertNotIn("url(", block)
        self.assertNotIn("http", block)

    def test_accessible_status(self):
        saved_run = dict(web.RUN)
        self.addCleanup(lambda: (web.RUN.clear(), web.RUN.update(saved_run)))
        web.RUN.update({"kind": "collect", "running": True, "done": 0})
        block = web.run_indicator()
        self.assertIn("role='status'", block)
        self.assertIn("aria-label='идёт сбор вакансий'", block)
        self.assertIn("aria-hidden='true'", block)


if __name__ == "__main__":
    unittest.main()
