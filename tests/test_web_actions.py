# -*- coding: utf-8 -*-
"""Тесты действий веб-интерфейса: сбор по всем запросам и оценка только новых.

Сеть и настоящая база не используются: SQLite подменяется временным файлом,
`collector.collect` — заглушкой, настройки — офлайн-профилем без ключа LLM.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401

from hhassist import collector, letters, queue, storage, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402


def offline_settings(**over):
    """Настройки без ключа LLM: скоринг считает только правила."""
    data = {
        "profile": dict(DEFAULT_PROFILE),
        "daily_limit": 200,
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

    def run_collect(self, queries, per_query=1, settings=None, broken=(), area=None):
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
            report = web.run_action("collect", self.conn, area=area)
        return report, calls

    def test_uses_every_query_from_queries_file(self):
        queries = ["AI инженер", "промпт-инженер", "LLM инженер", "RAG разработчик",
                   "AI агенты разработчик"]
        report, calls = self.run_collect(queries)
        self.assertEqual([c["query"] for c in calls], queries)
        self.assertEqual(report, "собрано вакансий: 5 из 250 найденных "
                                 "(источник: html, запросов: 5, лимит: 20 на запрос, "
                                 "всего до 200, регион: вся Россия)")

    def test_region_from_form_goes_to_collector(self):
        """Выбор региона в интерфейсе уходит в поиск hh как area=<id>."""
        _, calls = self.run_collect(["AI инженер"], area="1")
        self.assertEqual(calls[0]["area"], [1])

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
        self.assertEqual((s.collect_limit, s.collect_total, s.collect_pages,
                          s.collect_queries), (40, 200, 1, 0))

    def test_collect_total_from_env(self):
        self.assertEqual(self.settings_from({"COLLECT_TOTAL": "150"}).collect_total, 150)


class QueriesEditorTest(WebBase):
    """Редактор поисковых запросов на главной: пишет config/queries.txt и влияет на сбор."""

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.tmp.name, "config"))
        patcher = mock.patch.object(web, "BASE", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = os.path.join(self.tmp.name, "config", "queries.txt")

    def test_split_skips_blanks_comments_and_duplicates(self):
        text = ("# шапка\n\nAI инженер\n  промпт-инженер  \n"
                "   # комментарий с отступом\nAI инженер\n")
        self.assertEqual(web.split_queries(text), ["AI инженер", "промпт-инженер"])

    def test_save_writes_file_as_crlf_and_reports_count(self):
        report = web.save_queries("# мои должности\nAI инженер\nRAG разработчик\n")
        self.assertEqual(report, "запросы сохранены: 2 (config/queries.txt)")
        with open(self.path, "rb") as f:
            raw = f.read()
        self.assertIn("AI инженер".encode("utf-8"), raw)
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\r\r\n", raw)
        with open(self.path, encoding="utf-8") as f:
            self.assertIn("# мои должности", f.read())

    def test_empty_input_does_not_overwrite_file(self):
        web.save_queries("AI инженер\n")
        report = web.save_queries("# только комментарий\n\n   \n")
        self.assertIn("не сохранено", report)
        self.assertEqual(web._queries(), ["AI инженер"])

    def test_saved_queries_are_used_by_collect(self):
        web.save_queries("AI инженер\nAI агенты разработчик\n")
        self.assertEqual(web._queries(), ["AI инженер", "AI агенты разработчик"])
        calls = []

        def fake_collect(query, **kwargs):
            calls.append(query)
            return [sample_vacancy(id="q1")], {"source": "html", "fallback": False,
                                               "reason": "", "found": 1, "taken": 1,
                                               "errors": []}

        with mock.patch.object(collector, "collect", fake_collect):
            web.run_action("collect", self.conn)
        self.assertEqual(calls, ["AI инженер", "AI агенты разработчик"])

    def test_index_shows_editor_with_current_queries(self):
        web.save_queries("AI инженер\nLLM инженер\n")
        page = web.render_index(self.conn, 50)
        self.assertIn("name='queries'", page)
        self.assertIn("AI инженер", page)
        self.assertIn("LLM инженер", page)
        self.assertIn("Сохранить запросы", page)
        self.assertIn("действующих запросов: <b>2</b>", page)

    def test_index_warns_about_collect_queries_limit(self):
        web.save_queries("AI инженер\nLLM инженер\nRAG разработчик\n")
        self.use_settings(offline_settings(collect_queries=2))
        page = web.render_index(self.conn, 50)
        self.assertIn("сбор берёт только первые 2", page)

    def test_index_falls_back_to_profile_without_file(self):
        page = web.render_index(self.conn, 50)
        self.assertIn("профиль (файла config/queries.txt ещё нет)", page)
        self.assertIn("RAG-разработчик", page)  # целевая роль из DEFAULT_PROFILE

    def test_clear_queries_empties_list_and_keeps_header(self):
        web.save_queries("AI инженер\nLLM инженер\n")
        self.assertEqual(len(web._queries()), 2)
        report = web.clear_queries()
        self.assertIn("очищены", report)
        self.assertEqual(web._queries(), [])          # профиль больше не подставляется
        with open(self.path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("#", content)                   # заголовок остался
        self.assertNotIn("AI инженер", content)

    def test_index_shows_clear_button_with_confirm(self):
        page = web.render_index(self.conn, 50)
        self.assertIn("action='/queries/clear'", page)
        self.assertIn("Очистить запросы", page)
        self.assertIn("return confirm(", page)

    def test_index_reports_zero_queries_after_clear(self):
        web.save_queries("AI инженер\n")
        web.clear_queries()
        page = web.render_index(self.conn, 50)
        self.assertIn("действующих запросов: <b>0</b>", page)

    def test_collect_after_clear_asks_to_fill_queries(self):
        web.save_queries("AI инженер\n")
        web.clear_queries()
        report = web.run_action("collect", self.conn)
        self.assertIn("нет поисковых запросов", report)
        self.assertIn("config/queries.txt", report)


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

    def test_index_has_no_funnel_button(self):
        """«Воронка» удалена из интерфейса: ни кнопки, ни ссылки."""
        page = web.render_index(self.conn, 50)
        self.assertNotIn("/funnel", page)
        self.assertNotIn("Воронка", page)

    def test_card_offers_single_vacancy_actions(self):
        """В карточке — кнопки только для этой вакансии, а не для всей базы."""
        self.add()
        page = web.render_card(self.conn, "1")
        self.assertIn("action='/run/letters/1'", page)
        self.assertIn("action='/run/score/1'", page)
        self.assertIn("Сгенерировать письмо", page)
        self.assertNotIn("/funnel", page)

    def test_funnel_is_gone(self):
        """Страница и модуль воронки удалены полностью."""
        self.assertFalse(hasattr(web, "render_funnel"))
        with self.assertRaises(ImportError):
            __import__("hhassist.analytics")


class SingleVacancyActionTest(WebBase):
    """Кнопки карточки: оценка и письмо только для одной вакансии, не для всей базы."""

    def add(self, **over):
        for vid in ("1", "2"):
            storage.upsert_vacancy(self.conn, sample_vacancy(id=vid, **over))

    def test_score_one_scores_only_target(self):
        self.add()
        report = web.run_action("score", self.conn, vacancy_id="1")
        self.assertIn("оценка", report)
        self.assertIn("из 5", report)
        self.assertNotIn("из 100", report)
        rows = {row["vacancy_id"] for row in self.conn.execute("SELECT vacancy_id FROM scores")}
        self.assertEqual(rows, {"1"})

    def test_letter_one_writes_only_target(self):
        self.add()
        with mock.patch.object(letters, "make_letter",
                               lambda v, profile, s: ("письмо %s" % v["id"], "test")):
            report = web.run_action("letters", self.conn, vacancy_id="1")
        self.assertIn("письмо готово", report)
        rows = {row["vacancy_id"] for row in self.conn.execute("SELECT vacancy_id FROM letters")}
        self.assertEqual(rows, {"1"})

    def test_missing_vacancy_reported(self):
        self.assertIn("не найдена", web.run_action("score", self.conn, vacancy_id="404"))
        self.assertIn("не найдена", web.run_action("letters", self.conn, vacancy_id="404"))

    def test_second_run_reports_busy_instead_of_crashing(self):
        """Раньше второй клик падал на блокировке SQLite и отдавал пустую страницу."""
        self.add()
        with web.RUN_LOCK:
            report = web.run_action("score", self.conn, vacancy_id="1")
        self.assertIn("уже выполняется", report)
        self.assertEqual(self.stored("scores"), 0)

    def test_edited_letter_is_kept(self):
        """Правку человека повторная генерация не затирает."""
        self.add()
        storage.save_letter(self.conn, "1", "моя правка", "manual", edited=True)
        with mock.patch.object(letters, "make_letter",
                               side_effect=AssertionError("генерация не нужна")):
            report = web.run_action("letters", self.conn, vacancy_id="1")
        self.assertIn("сохранена ваша правка", report)
        self.assertEqual(storage.vacancy_with_letter(self.conn, "1")["letter"], "моя правка")


class DatabaseLockTest(unittest.TestCase):
    """SQLite работает в WAL: читатели не ждут писателя, писатель ждёт своей очереди."""

    def test_connect_enables_wal_and_busy_timeout(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(storage, "DB_PATH", os.path.join(tmp.name, "wal.sqlite3")):
            conn = storage.connect()
            self.addCleanup(conn.close)
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")
        self.assertGreaterEqual(busy, 5000)


class ErrorPageTest(WebBase):
    """Ошибка обработчика отдаёт страницу с текстом, а не пустой ответ."""

    def setUp(self):
        super().setUp()
        quiet = mock.patch.object(web.Handler, "log_message", lambda *a, **k: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def test_get_error_returns_page_with_text(self):
        with mock.patch.object(web, "render_index", side_effect=RuntimeError("boom")):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(self.base + "/", timeout=20)
        self.assertEqual(ctx.exception.code, 500)
        body = ctx.exception.read().decode("utf-8")
        self.assertIn("Не получилось выполнить запрос", body)
        self.assertIn("boom", body)

    def test_post_error_redirects_with_message(self):
        data = urllib.parse.urlencode({"region": "113"}).encode("utf-8")
        request = urllib.request.Request(self.base + "/run/score", data=data, method="POST")
        with mock.patch.object(web, "start_action", side_effect=RuntimeError("boom")):
            with urllib.request.urlopen(request, timeout=20) as response:
                page = response.read().decode("utf-8")
        self.assertIn("ошибка: RuntimeError: boom", page)



if __name__ == "__main__":
    unittest.main()
