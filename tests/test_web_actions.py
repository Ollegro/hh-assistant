# -*- coding: utf-8 -*-
"""Тесты действий веб-интерфейса: сбор по всем запросам и оценка только новых.

Сеть и настоящая база не используются: SQLite подменяется временным файлом,
`collector.collect` — заглушкой, настройки — офлайн-профилем без ключа LLM.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401

from hhassist import collector, queue, storage, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402


def offline_settings(**over):
    """Настройки без ключа LLM: скоринг считает только правила."""
    data = {
        "profile": dict(DEFAULT_PROFILE),
        "daily_limit": 200,
        "score_threshold": 55,
        "collect_limit": 20,
        "collect_pages": 1,
        "collect_queries": 0,
    }
    data.update(over)
    return Settings(**data)


class WebBase(unittest.TestCase):
    """Временная база и подмена настроек: тесты не трогают data/ и не ходят в сеть."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH",
                                    os.path.join(self.tmp.name, "test.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = storage.connect()
        self.addCleanup(self.conn.close)
        self.use_settings(offline_settings())

    def use_settings(self, settings):
        """Подменяет настройки в веб-интерфейсе и в очереди (там тоже читается профиль)."""
        for module in (web, queue):
            patcher = mock.patch.object(module, "get_settings", lambda: settings)
            patcher.start()
            self.addCleanup(patcher.stop)

    def stored(self, table):
        return self.conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


class CollectActionTest(WebBase):
    """Кнопка «Собрать вакансии»: все запросы из queries.txt, лимит из настроек."""

    def run_collect(self, queries, per_query=1, settings=None, broken=()):
        calls = []

        def fake_collect(query, **kwargs):
            calls.append(dict(kwargs, query=query))
            if query in broken:
                raise RuntimeError("HTTP 403")
            items = [sample_vacancy(id="%s-%d" % (query, i)) for i in range(per_query)]
            info = {"source": "html", "fallback": False, "reason": "",
                    "found": 50, "taken": len(items), "errors": []}
            return items, info

        if settings is not None:
            self.use_settings(settings)
        with mock.patch.object(web, "_queries", lambda: list(queries)), \
                mock.patch.object(collector, "collect", fake_collect):
            report = web.run_action("collect", self.conn)
        return report, calls

    def test_uses_every_query_from_queries_file(self):
        queries = ["AI инженер", "промпт-инженер", "LLM инженер", "RAG разработчик",
                   "AI агенты разработчик"]
        report, calls = self.run_collect(queries)
        self.assertEqual([c["query"] for c in calls], queries)
        self.assertEqual(report, "собрано вакансий: 5 из 250 найденных "
                                 "(источник: html, запросов: 5, лимит: 20)")

    def test_limit_and_pages_come_from_settings(self):
        settings = offline_settings(collect_limit=25, collect_pages=2)
        _, calls = self.run_collect(["AI инженер"], settings=settings)
        self.assertEqual(calls[0]["limit"], 25)
        self.assertEqual(calls[0]["pages"], 2)

    def test_queries_cap_from_settings(self):
        settings = offline_settings(collect_queries=2)
        _, calls = self.run_collect(["q1", "q2", "q3", "q4", "q5"], settings=settings)
        self.assertEqual([c["query"] for c in calls], ["q1", "q2"])

    def test_failed_query_does_not_stop_the_rest(self):
        report, calls = self.run_collect(["q1", "q2", "q3"], broken=("q2",))
        self.assertEqual(len(calls), 3)
        self.assertIn("поиск не удался", report)
        self.assertIn("собрано вакансий: 2 из 100", report)
        self.assertEqual(self.stored("vacancies"), 2)

    def test_reports_empty_queries_file(self):
        report, calls = self.run_collect([])
        self.assertEqual(calls, [])
        self.assertIn("config/queries.txt", report)

    def test_saves_found_vacancies(self):
        self.run_collect(["AI инженер"], per_query=3)
        self.assertEqual(self.stored("vacancies"), 3)
        kinds = [row["kind"] for row in self.conn.execute("SELECT kind FROM events")]
        self.assertIn("collect", kinds)


class ScoreActionTest(WebBase):
    """Кнопка «Оценить»: только вакансии без оценки (полная переоценка — cli score --all)."""

    def add_vacancy(self, vid):
        storage.upsert_vacancy(self.conn, sample_vacancy(id=vid))

    def test_scores_only_new_vacancies(self):
        for vid in ("1", "2", "3"):
            self.add_vacancy(vid)
        storage.save_score(self.conn, "1", 99, "ручная оценка", ["не переоценивать"])
        report = web.run_action("score", self.conn)
        self.assertEqual(report, "оценено новых: 2 (всего оценено: 3)")
        row = self.conn.execute(
            "SELECT score, reasons FROM scores WHERE vacancy_id = '1'").fetchone()
        self.assertEqual(row["score"], 99)
        self.assertEqual(json.loads(row["reasons"]), ["не переоценивать"])

    def test_score_without_new_vacancies_gives_hint(self):
        self.add_vacancy("1")
        web.run_action("score", self.conn)
        report = web.run_action("score", self.conn)
        self.assertIn("оценено новых: 0", report)
        self.assertIn("score --all", report)

    def test_unknown_action_reported(self):
        self.assertIn("неизвестное действие", web.run_action("telegram", self.conn))


class CollectSettingsTest(unittest.TestCase):
    """Параметры сбора читаются из .env (COLLECT_*)."""

    def settings_from(self, env):
        from hhassist import config
        with mock.patch.object(config, "load_env", lambda path=None: dict(env)), \
                mock.patch.object(config, "load_profile",
                                  lambda path=None: dict(config.DEFAULT_PROFILE)):
            return config.get_settings()

    def test_collect_options_from_env(self):
        s = self.settings_from({"COLLECT_LIMIT": "25", "COLLECT_PAGES": "2",
                                "COLLECT_QUERIES": "3"})
        self.assertEqual((s.collect_limit, s.collect_pages, s.collect_queries), (25, 2, 3))

    def test_collect_defaults(self):
        s = self.settings_from({})
        self.assertEqual((s.collect_limit, s.collect_pages, s.collect_queries), (20, 1, 0))


class StatusActionTest(WebBase):
    """Карточка вакансии: статусы отклика и страница «Воронка»."""

    def add(self, vid="1", letter=True):
        storage.upsert_vacancy(self.conn, sample_vacancy(id=vid))
        if letter:
            storage.save_letter(self.conn, vid, "письмо для %s" % vid, "manual")

    def test_apply_status_writes_journal(self):
        self.add()
        self.assertEqual(web.apply_status(self.conn, "1", "приглашение"), "статус: приглашение")
        row = storage.latest_application(self.conn, "1")
        self.assertEqual(storage.status_kind(row["status"]), "invite")
        self.assertEqual(self.stored("applications"), 1)

    def test_unknown_status_reported_and_nothing_written(self):
        self.add()
        self.assertIn("неизвестный статус", web.apply_status(self.conn, "1", "телепорт"))
        self.assertIsNone(storage.latest_application(self.conn, "1"))

    def test_missing_vacancy_reported(self):
        self.assertIn("не найдена", web.apply_status(self.conn, "404", "ответ"))

    def test_card_has_status_buttons(self):
        self.add()
        page = web.render_card(self.conn, "1")
        self.assertIn("/status/1", page)
        self.assertIn("Статус отклика", page)
        self.assertIn("Приглашение", page)

    def test_card_shows_current_status_and_message(self):
        self.add()
        web.apply_status(self.conn, "1", "reject")
        self.assertIn("отказ", web.render_card(self.conn, "1"))
        self.assertIn("статус: отказ", web.render_card(self.conn, "1", msg="статус: отказ"))

    def test_index_links_to_funnel(self):
        self.assertIn("/funnel", web.render_index(self.conn, 55, 50))

    def test_funnel_page_renders(self):
        self.add()
        storage.save_score(self.conn, "1", 90, "откликаться", [])
        web.apply_status(self.conn, "1", "sent")
        page = web.render_funnel(self.conn)
        self.assertIn("воронка откликов", page)
        self.assertIn("Ответы работодателей", page)
        self.assertIn("Ждут ответа", page)
        self.assertIn("Разрез по баллам", page)

    def test_funnel_page_on_empty_base(self):
        page = web.render_funnel(self.conn)
        self.assertIn("воронка откликов", page)
        self.assertIn("Таких откликов нет", page)


if __name__ == "__main__":
    unittest.main()
