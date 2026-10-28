# -*- coding: utf-8 -*-
"""Тесты времени последнего сбора вакансий.

Сервер живёт в UTC, поэтому время из базы (03:11) показывается по Москве (06:11)
с подписью пояса. Сеть не используется: временная база и заглушка настроек.
"""
import os
import sys
import tempfile
import unittest
from datetime import timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401

from hhassist import queue, storage, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402


def offline_settings(**over):
    """Настройки без ключа LLM: страница не ходит в модель."""
    data = {"profile": dict(DEFAULT_PROFILE), "daily_limit": 200,
            "collect_limit": 20, "collect_pages": 1, "collect_queries": 0}
    data.update(over)
    return Settings(**data)


class MoscowTimeTest(unittest.TestCase):
    """Хранимое «наивное» время сервера -> время по Москве с подписью."""

    def test_utc_server_time_becomes_moscow(self):
        self.assertEqual(storage.fmt_moscow("2026-10-28T03:11:43", assume_tz=timezone.utc),
                         "28.10.2026 06:11 (МСК)")

    def test_label_can_be_skipped(self):
        self.assertEqual(
            storage.fmt_moscow("2026-10-28T03:11:43", with_label=False, assume_tz=timezone.utc),
            "28.10.2026 06:11")

    def test_space_separator_is_accepted(self):
        value = storage.to_moscow("2026-10-28 03:11:43", assume_tz=timezone.utc)
        self.assertEqual((value.hour, value.minute), (6, 11))

    def test_broken_and_empty_time(self):
        self.assertEqual(storage.fmt_moscow(""), "")
        self.assertEqual(storage.fmt_moscow("не дата"), "")
        self.assertIsNone(storage.to_moscow(None))

    def test_collect_summary(self):
        self.assertEqual(storage.collect_summary({"saved": 89, "found": 209}),
                         "сохранено 89 из 209 найденных")
        self.assertEqual(storage.collect_summary({}), "")
        self.assertEqual(storage.collect_summary(None), "")
        self.assertEqual(storage.collect_summary({"saved": "много"}), "")


class Base(unittest.TestCase):
    """Временная база: data/ проекта не трогаем."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH",
                                    os.path.join(self.tmp.name, "test.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = storage.connect()
        self.addCleanup(self.conn.close)
        settings = offline_settings()
        for module in (web, queue):
            p = mock.patch.object(module, "get_settings", lambda: settings)
            p.start()
            self.addCleanup(p.stop)


class LastCollectTest(Base):
    def test_empty_database_has_no_time(self):
        self.assertIsNone(storage.last_collect(self.conn))

    def test_collect_event_is_the_source(self):
        storage.log_event(self.conn, "collect", {"saved": 5, "found": 9})
        found = storage.last_collect(self.conn)
        self.assertTrue(found["ts"])
        self.assertEqual(found["payload"]["saved"], 5)

    def test_fallback_to_vacancies_when_journal_cleared(self):
        storage.upsert_vacancy(self.conn, sample_vacancy(id="1"), commit=True)
        found = storage.last_collect(self.conn)
        self.assertIsNotNone(found)
        self.assertEqual(found["payload"], {})
        self.assertTrue(found["ts"])


class IndexPageTimeTest(Base):
    def test_no_time_without_collection(self):
        self.assertNotIn("последний сбор вакансий", web.render_index(self.conn, 10))

    def test_time_with_region_and_summary(self):
        storage.log_event(self.conn, "collect",
                          {"saved": 89, "found": 209, "source": "html"})
        page = web.render_index(self.conn, 10)
        self.assertIn("последний сбор вакансий", page)
        self.assertIn("(МСК)", page)
        self.assertIn("сохранено 89 из 209 найденных", page)

    def test_payload_without_numbers_is_not_shown(self):
        storage.log_event(self.conn, "collect", {"saved": "<b>1</b>", "found": 2})
        page = web.render_index(self.conn, 10)
        self.assertIn("последний сбор вакансий", page)
        self.assertNotIn("сохранено", page)
        self.assertNotIn("<b>1</b>", page)


class NoticeTest(Base):
    """Сообщение о запуске действия — в шапке, на самом верху страницы."""

    def test_message_goes_to_header_top(self):
        page = web.render_index(self.conn, 10,
                                msg="запущено: сбор вакансий — страница обновится сама")
        header = page.split("<header>", 1)[1].split("</header>", 1)[0]
        self.assertIn("class='notice'", header)
        self.assertIn("запущено: сбор вакансий", header)
        self.assertNotIn("<div class='card'>запущено", page)   # прежней карточки нет

    def test_no_notice_without_message(self):
        self.assertNotIn("class='notice'", web.render_index(self.conn, 10))

    def test_message_is_escaped(self):
        page = web.render_index(self.conn, 10, msg="<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", page)


if __name__ == "__main__":
    unittest.main()
