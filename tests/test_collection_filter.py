# -*- coding: utf-8 -*-
"""Тесты фильтров сбора: архивные и уже отработанные вакансии не попадают в базу."""
import json
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401
from test_web_actions import WebBase  # noqa: E402

from hhassist import collection_filter, jobs, storage  # noqa: E402


class ArchivedTest(unittest.TestCase):
    def test_flag_from_api(self):
        self.assertTrue(collection_filter.is_archived({"archived": True}))

    def test_live_vacancy_passes(self):
        self.assertFalse(collection_filter.is_archived({"archived": False,
                                                        "title": "AI-инженер",
                                                        "description": "Задачи"}))

    def test_marker_in_description(self):
        self.assertTrue(collection_filter.is_archived(
            {"title": "Разработчик", "description": "Вакансия в архиве"}))

    def test_marker_case_insensitive(self):
        self.assertTrue(collection_filter.is_archived(
            {"description": "ВАКАНСИЯ СНЯТА С ПУБЛИКАЦИИ"}))

    def test_clean_text_is_not_archived(self):
        self.assertFalse(collection_filter.is_archived(
            {"description": "Ищем инженера. Архив документов ведётся в 1С."}))


class ClosedIdsTest(WebBase):
    def mark(self, vacancy_id, status):
        storage.add_application(self.conn, vacancy_id, status=status, note="")

    def test_sent_marks_applied(self):
        self.mark("111", storage.STATUS_SENT)
        self.assertEqual(collection_filter.closed_ids(self.conn), {"111": "applied"})

    def test_skip_marks_skipped(self):
        self.mark("222", storage.STATUS_SKIPPED)
        self.assertEqual(collection_filter.closed_ids(self.conn), {"222": "skipped"})

    def test_last_status_wins(self):
        self.mark("333", storage.STATUS_READY)
        self.mark("333", storage.STATUS_SENT)
        self.assertEqual(collection_filter.closed_ids(self.conn)["333"], "applied")

    def test_empty_db(self):
        self.assertEqual(collection_filter.closed_ids(self.conn), {})


class RejectTest(unittest.TestCase):
    def test_archived_rejected_and_counted(self):
        stats = collection_filter.FilterStats()
        self.assertTrue(collection_filter.reject({"archived": True}, stats=stats))
        self.assertEqual(stats.archived, 1)

    def test_applied_rejected(self):
        stats = collection_filter.FilterStats()
        closed = {"111": "applied"}
        self.assertTrue(collection_filter.reject({"id": "111"}, closed=closed, stats=stats))
        self.assertEqual(stats.applied, 1)

    def test_skipped_rejected_by_default(self):
        closed = {"222": "skipped"}
        self.assertTrue(collection_filter.reject({"id": "222"}, closed=closed))

    def test_skipped_can_be_kept(self):
        closed = {"222": "skipped"}
        self.assertFalse(collection_filter.reject({"id": "222"}, closed=closed,
                                                  skip_skipped=False))

    def test_new_vacancy_passes(self):
        self.assertFalse(collection_filter.reject({"id": "999", "archived": False},
                                                  closed={"111": "applied"}))

    def test_note_text(self):
        stats = collection_filter.FilterStats()
        stats.archived, stats.applied = 3, 1
        note = stats.note()
        self.assertIn("архивных: 3", note)
        self.assertIn("уже откликался: 1", note)

    def test_empty_note(self):
        self.assertEqual(collection_filter.FilterStats().note(), "")


class CollectSkipsTest(WebBase):
    """collect_queries не пишет в базу архивные и уже отработанные вакансии."""

    def run_collect(self, vacancies):
        calls = []

        def fake_collect(query, **kwargs):
            calls.append(query)
            return vacancies, {"source": "api", "fallback": False, "reason": "",
                               "found": len(vacancies), "skipped": 0, "quality": {}}

        from unittest import mock

        settings = self.settings
        with mock.patch.object(jobs.collector, "collect", fake_collect):
            saved, found, source, notes = jobs.collect_queries(
                self.conn, ["AI-инженер"], settings, limit=10, pages=1,
                sources=("hh",))
        return saved, notes

    def setUp(self):
        super().setUp()
        from test_web_actions import offline_settings

        self.settings = offline_settings()

    def test_archived_not_saved(self):
        live = sample_vacancy(id="111")
        dead = sample_vacancy(id="222")
        dead["archived"] = True
        saved, notes = self.run_collect([live, dead])
        self.assertEqual(saved, 1)
        rows = [row[0] for row in self.conn.execute("SELECT id FROM vacancies")]
        self.assertEqual(rows, ["111"])
        self.assertTrue(any("архивных: 1" in note for note in notes))

    def test_applied_not_saved(self):
        storage.add_application(self.conn, "333", status=storage.STATUS_SENT, note="")
        saved, notes = self.run_collect([sample_vacancy(id="333")])
        self.assertEqual(saved, 0)
        self.assertTrue(any("уже откликался: 1" in note for note in notes))

    def test_skipped_not_saved(self):
        storage.add_application(self.conn, "444", status=storage.STATUS_SKIPPED, note="")
        saved, _notes = self.run_collect([sample_vacancy(id="444")])
        self.assertEqual(saved, 0)

    def test_new_vacancy_saved(self):
        saved, notes = self.run_collect([sample_vacancy(id="555")])
        self.assertEqual(saved, 1)
        self.assertFalse(any("не взяли" in note for note in notes))


if __name__ == "__main__":
    unittest.main()
