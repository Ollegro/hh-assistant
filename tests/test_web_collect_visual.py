# -*- coding: utf-8 -*-
"""Тесты картинок прогона: у сбора, оценки и писем своя анимация.

Сбор — книга с перелистываемыми страницами и лупа; оценка — стопка листов с
галочками; письма — лист с бегущими строками и карандаш. Проверяется, что
картинка появляется только у своего действия, не ломает страницу, не тянет
внешние файлы и уважает prefers-reduced-motion.
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

    def running(self, kind, done=7):
        web.RUN.update({"kind": kind, "title": web.RUN_TITLES.get(kind, kind),
                        "done": done, "started": "2026-10-28 09:20", "report": "",
                        "error": "", "running": True, "stop": False})


class IndexIndicatorTest(Base):
    def test_collecting_shows_book_and_lens(self):
        self.running("collect", done=7)
        page = web.render_index(self.conn, 10)
        self.assertIn("class='run-visual collect-visual'", page)
        self.assertIn("class='book'", page)
        self.assertIn("lens-orbit", page)
        self.assertIn("идёт сбор вакансий", page)
        self.assertIn("обработано карточек: 7", page)

    def test_scoring_shows_sheets_with_ticks(self):
        self.running("score", done=12)
        page = web.render_index(self.conn, 10)
        self.assertIn("class='run-visual score-visual'", page)
        self.assertIn("class='sheets'", page)
        self.assertIn("идёт оценка вакансий", page)
        self.assertIn("обработано вакансий: 12", page)
        self.assertNotIn("<div class='book'", page)   # чужая картинка не примешивается

    def test_letters_show_writing_and_pencil(self):
        self.running("letters", done=3)
        page = web.render_index(self.conn, 10)
        self.assertIn("class='run-visual letters-visual'", page)
        self.assertIn("class='writing'", page)
        self.assertIn("class='pencil'", page)
        self.assertIn("идёт генерация писем", page)
        self.assertIn("готово писем: 3", page)

    def test_hidden_when_idle(self):
        self.assertNotIn("<div class='run-visual", web.render_index(self.conn, 10))

    def test_hidden_after_finish(self):
        self.running("score")
        web.RUN["running"] = False
        web.RUN["report"] = "оценено новых: 5"
        self.assertNotIn("<div class='run-visual", web.render_index(self.conn, 10))

    def test_page_still_auto_refreshes(self):
        self.running("collect")
        self.assertIn("http-equiv='refresh'", web.render_index(self.conn, 10))


class CardIndicatorTest(Base):
    def test_card_shows_indicator_too(self):
        storage.upsert_vacancy(self.conn, sample_vacancy(id="1"))
        self.running("letters")
        page = web.render_card(self.conn, "1")
        self.assertIn("class='run-visual letters-visual'", page)


class StopButtonTest(Base):
    """Кнопка «Остановить»: видна только во время прогона и ставит флаг."""

    def test_button_visible_only_while_running(self):
        self.assertNotIn("action='/run/stop'", web.render_index(self.conn, 10))
        self.running("collect")
        page = web.render_index(self.conn, 10)
        self.assertIn("action='/run/stop'", page)
        self.assertIn("Остановить сбор вакансий", page)

    def test_stop_run_sets_flag_and_reports(self):
        self.running("collect")
        message = web.stop_run()
        self.assertTrue(web.RUN["stop"])
        self.assertIn("останавливаю", message)
        # повторный запрос не ломает состояние
        self.assertIn("уже запрошена", web.stop_run())
        self.assertIn("останавливаю…", web.run_progress())

    def test_stop_without_run_is_harmless(self):
        web.RUN.update({"running": False, "stop": False})
        self.assertIn("нечего останавливать", web.stop_run())
        self.assertFalse(web.RUN["stop"])

    def test_button_disabled_after_request(self):
        self.running("collect")
        web.RUN["stop"] = True
        self.assertIn("disabled", web.render_index(self.conn, 10))


class VisualMarkupTest(unittest.TestCase):
    """Анимации самодостаточны: только CSS, доступны и уважают reduced motion."""

    def setUp(self):
        saved_run = dict(web.RUN)
        self.addCleanup(lambda: (web.RUN.clear(), web.RUN.update(saved_run)))

    def scene(self, kind="collect"):
        web.RUN.update({"kind": kind, "running": True, "done": 0})
        return web.run_indicator()

    def test_css_has_keyframes_for_every_action(self):
        for rule in ("@keyframes page-flip", "@keyframes lens-spin",
                     "@keyframes tick-in", "@keyframes ink-write",
                     "@keyframes pencil-move", ".run-visual"):
            self.assertIn(rule, web.CSS)

    def test_motion_can_be_disabled(self):
        self.assertIn("prefers-reduced-motion", web.CSS)
        css = web.CSS.replace("\n", "")
        for selector in (".book .flip", ".lens-orbit", ".sheets .row::after",
                         ".writing .ink", ".writing .pencil"):
            self.assertIn(selector, css)

    def test_no_external_images_or_scripts(self):
        for kind in ("collect", "score", "letters"):
            block = self.scene(kind)
            self.assertNotIn("<img", block)
            self.assertNotIn("<script", block)
            self.assertNotIn("url(", block)
            self.assertNotIn("http", block)

    def test_accessible_status_for_every_action(self):
        labels = {"collect": "идёт сбор вакансий", "score": "идёт оценка вакансий",
                  "letters": "идёт генерация писем"}
        for kind, label in labels.items():
            block = self.scene(kind)
            self.assertIn("role='status'", block)
            self.assertIn("aria-label='%s" % label, block)
            self.assertIn("aria-hidden='true'", block)


if __name__ == "__main__":
    unittest.main()
