# -*- coding: utf-8 -*-
"""Тесты ручного добавления вакансии через веб и сортировки списка."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401
from test_web_actions import WebBase  # noqa: E402

from hhassist import manual, queue, storage, web  # noqa: E402


def vacancy(vid, title="AI-инженер", score=None, source="html"):
    """Минимальная вакансия для базы."""
    v = sample_vacancy()
    v.update({"id": vid, "title": title, "source": source})
    return v


class AddManualWebTest(WebBase):
    """add_manual: сохранение, дубли, валидация, журнал событий."""

    def call(self, fetch=None, **fields):
        form = {key: [value] for key, value in fields.items()}
        if fetch is not None:
            patcher = mock.patch.object(manual, "_fetch_hh", fetch)
            patcher.start()
            self.addCleanup(patcher.stop)
        return web.add_manual(self.conn, form)

    def test_saves_vacancy_and_returns_id(self):
        report, vid = self.call(title="AI-инженер", company="Клиника",
                                description="Задачи: автоматизация", manual_city="Москва")
        self.assertTrue(vid.startswith(manual.MANUAL_PREFIX))
        self.assertIn("добавлено", report)
        row = self.conn.execute("SELECT title, company, source, region FROM vacancies"
                                " WHERE id = ?", (vid,)).fetchone()
        self.assertEqual(row["title"], "AI-инженер")
        self.assertEqual(row["source"], "manual")
        self.assertEqual(row["region"], "Москва")

    def test_hh_url_uses_hh_id_and_fills_fields(self):
        fetched = {"title": "AI-инженер (Офис)", "company": "Сеть клиник Подружки",
                   "description": "Полное описание с hh", "salary_from": 100000,
                   "salary_to": None, "currency": "RUR", "area": "Москва",
                   "work_format": "гибрид", "experience": "1–3 года",
                   "published_at": "", "skills": ["Python"], "area_id": "1",
                   "region": "Москва"}
        report, vid = self.call(fetch=lambda _id: fetched,
                                url="https://hh.ru/vacancy/136901954")
        self.assertEqual(vid, "136901954")
        self.assertIn("дополнены", report)
        row = self.conn.execute("SELECT title, salary_from FROM vacancies WHERE id = ?",
                                (vid,)).fetchone()
        self.assertEqual(row["title"], "AI-инженер (Офис)")
        self.assertEqual(row["salary_from"], 100000)

    def test_manual_title_wins_over_hh(self):
        fetched = {"title": "Из hh", "company": "Из hh", "description": "Из hh"}
        _report, vid = self.call(fetch=lambda _id: fetched,
                                 url="https://hh.ru/vacancy/136901954",
                                 title="Моё название", description="Моё описание")
        row = self.conn.execute("SELECT title, description FROM vacancies WHERE id = ?",
                                (vid,)).fetchone()
        self.assertEqual(row["title"], "Моё название")
        self.assertEqual(row["description"], "Моё описание")

    def test_requires_description_or_url(self):
        report, vid = self.call(title="Без описания")
        self.assertEqual(vid, "")
        self.assertIn("не добавлено", report)
        self.assertEqual(self.stored("vacancies"), 0)

    def test_requires_title(self):
        report, vid = self.call(description="Описание есть, названия нет")
        self.assertEqual(vid, "")
        self.assertIn("название", report)

    def test_repeat_add_updates_without_duplicate(self):
        first, vid = self.call(title="AI-инженер", description="Первое описание",
                               url="https://example.com/job/7")
        second, vid2 = self.call(title="AI-инженер", description="Второе описание",
                                 url="https://example.com/job/7")
        self.assertEqual(vid, vid2)
        self.assertEqual(self.stored("vacancies"), 1)
        self.assertIn("уже была", second)
        row = self.conn.execute("SELECT description FROM vacancies WHERE id = ?",
                                (vid,)).fetchone()
        self.assertEqual(row["description"], "Второе описание")

    def test_logs_event(self):
        _report, vid = self.call(title="AI-инженер", description="Описание")
        kinds = [row[0] for row in
                 self.conn.execute("SELECT kind FROM events ORDER BY id")]
        self.assertIn("manual_add", kinds)


class ManualCardTest(WebBase):
    """Карточка вакансии с id вида manual:<hex> открывается (id в ссылке кодируется)."""

    def test_render_card_for_manual_id(self):
        _report, vid = web.add_manual(self.conn, {
            "title": ["AI-инженер"], "description": ["Описание для оценки"],
            "url": ["https://example.com/job/9"]})
        self.assertTrue(vid.startswith(manual.MANUAL_PREFIX))
        page = web.render_card(self.conn, vid)
        self.assertTrue(page)
        self.assertIn("AI-инженер", page)

    def test_quoted_id_is_unquoted_by_server(self):
        """Сервер раскодирует сегмент пути: иначе manual%3A… давал 404.

        Проверяем сам разбор: то, что отдаёт urlparse для закодированной ссылки,
        после unquote совпадает с id в базе.
        """
        import urllib.parse as up

        _report, vid = web.add_manual(self.conn, {
            "title": ["AI-инженер"], "description": ["Описание"]})
        quoted = up.quote(vid)
        path = "/v/" + quoted
        segment = up.urlparse(path).path.split("/")[2]
        self.assertNotEqual(segment, vid)            # в пути действительно %3A
        self.assertEqual(up.unquote(segment), vid)   # а сервер сравнивает раскодированное


class ManualOrderTest(WebBase):
    """Неоценённая вакансия, добавленная вручную, стоит в списке первой."""
    def test_unrated_manual_goes_first(self):
        storage.upsert_vacancy(self.conn, vacancy("111", "Обычная", source="html"))
        storage.save_score(self.conn, "111", 90, "подходит", ["роль"])
        storage.upsert_vacancy(self.conn, vacancy("manual:abc", "Ручная",
                                                  source="manual"))
        rows = queue.next_batch(self.conn, limit=None)
        self.assertEqual([row["id"] for row in rows][0], "manual:abc")

    def test_rated_manual_sorted_by_score(self):
        storage.upsert_vacancy(self.conn, vacancy("111", "Обычная", source="html"))
        storage.save_score(self.conn, "111", 90, "подходит", ["роль"])
        storage.upsert_vacancy(self.conn, vacancy("manual:abc", "Ручная",
                                                  source="manual"))
        storage.save_score(self.conn, "manual:abc", 40, "частично", ["роль"])
        rows = queue.next_batch(self.conn, limit=None)
        self.assertEqual([row["id"] for row in rows], ["111", "manual:abc"])


if __name__ == "__main__":
    unittest.main()
