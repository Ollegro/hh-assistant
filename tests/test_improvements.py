# -*- coding: utf-8 -*-
"""Тесты доработок: миграции, правка письма, кулдаун компаний, инкрементальный сбор,
health-check парсера и регионы.

Все тесты офлайновые: временная база, сеть и LLM не используются.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import sample_vacancy  # noqa: E402

from hhassist import api_adapter, collector, jobs, queue, storage, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings, parse_areas  # noqa: E402

OLD_SCHEMA = """
CREATE TABLE vacancies (
    id TEXT PRIMARY KEY, title TEXT, company TEXT, url TEXT, salary_from INTEGER,
    salary_to INTEGER, currency TEXT, work_format TEXT, experience TEXT,
    published_at TEXT, description TEXT, skills TEXT, source TEXT, created_at TEXT
);
CREATE TABLE letters (
    vacancy_id TEXT PRIMARY KEY, text TEXT, model TEXT, updated_at TEXT
);
"""


def settings(**over):
    data = {"profile": dict(DEFAULT_PROFILE),
            "collect_limit": 20, "collect_pages": 1, "collect_queries": 0}
    data.update(over)
    return Settings(**data)


class Base(unittest.TestCase):
    """Временная база в tmp: data/ не трогаем."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        patcher = mock.patch.object(storage, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = storage.connect()
        self.addCleanup(self.conn.close)

    def vacancy(self, vid, **over):
        v = sample_vacancy(id=vid, **over)
        storage.upsert_vacancy(self.conn, v, commit=False)
        return v

    def scored(self, vid, score=80, **over):
        self.vacancy(vid, **over)
        storage.save_score(self.conn, vid, score, "подходит", [], commit=False)
        self.conn.commit()


class MigrationTest(unittest.TestCase):
    """Старые базы догоняются ALTER-ами, данные не теряются."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "old.sqlite3")
        raw = sqlite3.connect(self.db_path)
        raw.executescript(OLD_SCHEMA)
        raw.execute("INSERT INTO vacancies (id, title, company, created_at) "
                    "VALUES ('1', 'AI-инженер', 'Ромашка', '2024-05-01T10:00:00')")
        raw.execute("INSERT INTO letters (vacancy_id, text, model, updated_at) "
                    "VALUES ('1', 'старое письмо', 'offline', '2024-05-01T10:00:00')")
        raw.commit()
        raw.close()
        patcher = mock.patch.object(storage, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def columns(self, table):
        conn = sqlite3.connect(self.db_path)
        try:
            return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
        finally:
            conn.close()

    def test_new_columns_added(self):
        conn = storage.connect()
        self.addCleanup(conn.close)
        self.assertLessEqual({"area", "area_id", "region", "seen_at"}, self.columns("vacancies"))
        self.assertLessEqual({"edited", "edited_at"}, self.columns("letters"))

    def test_seen_at_backfilled_from_created_at(self):
        conn = storage.connect()
        self.addCleanup(conn.close)
        row = conn.execute("SELECT seen_at, created_at FROM vacancies WHERE id = '1'").fetchone()
        self.assertEqual(row["seen_at"], row["created_at"])

    def test_old_letter_survives_and_is_not_edited(self):
        conn = storage.connect()
        self.addCleanup(conn.close)
        row = storage.vacancy_with_letter(conn, "1")
        self.assertEqual(row["letter"], "старое письмо")
        self.assertEqual(row["letter_edited"], 0)


class LetterEditTest(Base):
    """Правка письма человеком сохраняется и не затирается генерацией."""

    def test_manual_letter_marked_edited(self):
        self.scored("1")
        storage.save_letter(self.conn, "1", "Мой текст", model="manual", edited=True)
        row = storage.vacancy_with_letter(self.conn, "1")
        self.assertEqual(row["letter"], "Мой текст")
        self.assertEqual(row["letter_edited"], 1)
        self.assertTrue(row["letter_edited_at"])

    def test_generation_does_not_touch_manual_letter(self):
        self.scored("1")
        storage.save_letter(self.conn, "1", "Мой текст", model="manual", edited=True)
        made = jobs.make_letters(self.conn, settings(), force=True)
        self.assertEqual(made, 0)
        self.assertEqual(storage.vacancy_with_letter(self.conn, "1")["letter"], "Мой текст")

    def test_card_has_edit_form(self):
        self.scored("1")
        storage.save_letter(self.conn, "1", "Текст письма", model="offline")
        page = web.render_card(self.conn, "1")
        self.assertIn("action='/letter/1'", page)
        self.assertIn("name='text'", page)
        self.assertIn("Сохранить письмо", page)


class CooldownTest(Base):
    """Одна компания — один отклик за COMPANY_COOLDOWN_DAYS дней."""

    def test_second_vacancy_of_same_company_hidden(self):
        self.scored("1", company="Ромашка")
        self.scored("2", company="Ромашка")
        queue.mark_sent(self.conn, "1")
        ids = [r["id"] for r in queue.next_batch(self.conn, limit=10)]
        self.assertEqual(ids, [])

    def test_other_companies_stay(self):
        self.scored("1", company="Ромашка")
        self.scored("2", company="Лютик")
        queue.mark_sent(self.conn, "1")
        ids = [r["id"] for r in queue.next_batch(self.conn, limit=10)]
        self.assertEqual(ids, ["2"])

    def test_zero_days_disables_rule(self):
        self.scored("1", company="Ромашка")
        self.scored("2", company="Ромашка")
        queue.mark_sent(self.conn, "1")
        ids = [r["id"] for r in queue.next_batch(self.conn, limit=10, cooldown_days=0)]
        self.assertEqual(ids, ["2"])

    def test_cooldown_lists_company(self):
        self.scored("1", company="Ромашка")
        queue.mark_sent(self.conn, "1")
        self.assertEqual(len(queue.cooldown_companies(self.conn)), 1)

    def test_letters_skip_cooled_company(self):
        self.scored("1", company="Ромашка")
        self.scored("2", company="Лютик")
        queue.mark_sent(self.conn, "1")
        rows = jobs.letter_vacancies(self.conn)
        self.assertEqual([r["id"] for r in rows], ["2"])


class IncrementalCollectTest(Base):
    """Инкрементальный сбор: недавно виденные вакансии не скачиваем повторно."""

    def run_collect(self, known, refresh_days=3, search=("1", "2")):
        patched = [
            mock.patch.object(collector, "get_settings", lambda: settings(hh_source="html")),
            mock.patch.object(collector, "search_ids", lambda *a, **k: list(search)),
            mock.patch.object(collector, "fetch_vacancy",
                              lambda vid, pause=0: sample_vacancy(id=vid)),
        ]
        for p in patched:
            p.start()
            self.addCleanup(p.stop)
        return collector.collect("AI инженер", known=known, refresh_days=refresh_days)

    def test_recent_vacancy_not_refetched(self):
        vacancies, info = self.run_collect({"1": storage.now()})
        self.assertEqual([v["id"] for v in vacancies], ["2"])
        self.assertEqual(info["skipped"], 1)
        self.assertEqual(info["new"], 1)

    def test_refresh_days_zero_fetches_everything(self):
        vacancies, info = self.run_collect({"1": storage.now()}, refresh_days=0)
        self.assertEqual(len(vacancies), 2)
        self.assertEqual(info["skipped"], 0)

    def test_old_vacancy_is_refreshed(self):
        vacancies, info = self.run_collect({"1": "2020-01-01T00:00:00"})
        self.assertEqual([v["id"] for v in vacancies], ["1", "2"])
        self.assertEqual(info["refreshed"], 1)

    def test_seen_index_reads_storage(self):
        self.vacancy("1")
        self.assertIn("1", storage.seen_index(self.conn))

class ParserHealthTest(unittest.TestCase):
    """Оценка здоровья парсера по результатам прогона."""

    def test_ok(self):
        self.assertEqual(collector.quality([sample_vacancy(id="1")])["state"], "ok")

    def test_suspect_on_empty_titles(self):
        rows = [sample_vacancy(id=str(i), title="") for i in range(4)]
        health = collector.quality(rows)
        self.assertEqual(health["state"], "suspect")
        self.assertEqual(health["empty_titles"], 4)

    def test_suspect_on_short_descriptions(self):
        rows = [sample_vacancy(id=str(i), description="коротко") for i in range(4)]
        self.assertEqual(collector.quality(rows)["state"], "suspect")

    def test_no_results(self):
        self.assertEqual(collector.quality([], taken=0)["state"], "no_results")

    def test_empty_when_nothing_parsed(self):
        self.assertEqual(collector.quality([], taken=5)["state"], "empty")


class SuspectEventTest(Base):
    """Сломанный парсер пишет событие и показывает баннер на главной."""

    def test_suspect_logged_and_banner_shown(self):
        bad = [sample_vacancy(id=str(i), title="") for i in range(3)]
        info = {"source": "html", "fallback": False, "reason": "", "found": 3, "taken": 3,
                "new": 3, "refreshed": 0, "skipped": 0, "errors": [],
                "quality": collector.quality(bad)}
        with mock.patch.object(collector, "collect", lambda *a, **k: (bad, info)):
            saved, found, source, notes = jobs.collect_queries(
                self.conn, ["q"], settings(collect_refresh_days=0))
        self.assertEqual(saved, 3)
        self.assertTrue(any("парсер" in n for n in notes))
        self.assertIsNotNone(storage.last_event(self.conn, "parser_suspect"))
        self.assertIn("парсер мог сломаться", web.render_index(self.conn, 10))


class RegionTest(Base):
    """Регион вакансии: разбор, хранение, фильтр на главной."""

    def test_parse_areas(self):
        self.assertEqual(parse_areas("113"), [113])
        self.assertEqual(parse_areas("1, spb"), [1, 2])
        self.assertEqual(parse_areas(""), [113])
        self.assertEqual(parse_areas("мусор"), [113])

    def test_api_adapter_reads_area(self):
        v = api_adapter.vacancy_from_api({"id": "1", "name": "AI-инженер",
                                          "area": {"id": "2", "name": "Санкт-Петербург"}})
        self.assertEqual((v["area"], v["area_id"]), ("Санкт-Петербург", "2"))

    def test_html_parser_reads_area(self):
        html = ('<h1 data-qa="vacancy-title">AI-инженер</h1>'
                '<script>{"area":{"id":"1","name":"Москва"}}</script>')
        v = collector.parse_vacancy(html, "1")
        self.assertEqual((v["area"], v["area_id"]), ("Москва", "1"))

    def test_city_is_shown_but_not_filtered(self):
        """Город виден в таблице, но фильтр теперь только по региону."""
        self.scored("1", area="Химки", region="Московская область")
        self.scored("2", area="Мытищи", region="Московская область")
        page = web.render_index(self.conn, 10, region="Московская область")
        self.assertIn("Химки", page)
        self.assertIn("Мытищи", page)
        self.assertNotIn("name='city'", page)

    def test_html_parser_reads_region_from_json_ld(self):
        """Город и субъект РФ берутся из JSON-LD JobPosting (разметка hh 2026)."""
        html = ('<h1 data-qa="vacancy-title">AI-инженер</h1>'
                '<script type="application/ld+json">{"@type": "JobPosting",'
                ' "jobLocation": {"address": {"addressLocality": "Химки",'
                ' "addressRegion": "Московская область"}}}</script>')
        v = collector.parse_vacancy(html, "1")
        self.assertEqual((v["area"], v["region"]), ("Химки", "Московская область"))

    def test_region_saved_and_filtered(self):
        self.scored("1", area="Химки", region="Московская область")
        self.scored("2", area="Москва", region="Москва")
        page = web.render_index(self.conn, 10, region="Московская область")
        self.assertIn("Химки", page)
        self.assertNotIn("href='/v/2'", page)

    def test_old_rows_without_region_match_by_city(self):
        """Вакансии, собранные до появления region, не выпадают из фильтра."""
        self.scored("1", area="Москва")
        page = web.render_index(self.conn, 10, region="Москва")
        self.assertIn("href='/v/1'", page)

    def test_low_score_still_in_list(self):
        """Порога нет: вакансия с низким баллом остаётся в списке."""
        self.scored("1", score=10, region="Москва", area="Москва")
        page = web.render_index(self.conn, 10)
        self.assertIn("href='/v/1'", page)

    def test_index_summary_without_threshold(self):
        """Сводка отвечает на «сколько в списке»: собрано, оценено, без оценки."""
        self.scored("1", score=80)
        self.scored("2", score=10)
        page = web.render_index(self.conn, 10)
        self.assertIn("собрано в базе: <b>2</b>", page)
        self.assertIn("без оценки: <b>0</b>", page)
        self.assertNotIn("ниже порога", page)

    def test_area_catalog_without_network(self):
        """Без кэша справочник отдаёт встроенный набор и не ходит в сеть."""
        from hhassist import areas
        with mock.patch.object(areas, "_read_cache", lambda: None), \
                mock.patch.object(areas, "_download", side_effect=AssertionError("сеть")):
            items = areas.cached()
            self.assertEqual(areas.name_of(113), "Россия")
        self.assertEqual(items[0], areas.ALL_RUSSIA)
        self.assertIn((2, "Санкт-Петербург"), items)

    def test_area_catalog_reads_cache(self):
        from hhassist import areas
        with mock.patch.object(areas, "_read_cache", lambda: [(1, "Москва")]):
            self.assertEqual(areas.cached(), [(113, "Россия"), (1, "Москва")])
            self.assertEqual(areas.region_id("москва"), 1)
            self.assertIsNone(areas.region_id("Атлантида"))

    def test_search_ids_sends_multiple_areas(self):
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            return ""

        with mock.patch.object(collector, "http_get", fake_get):
            collector.search_ids("q", area=[113, 1], pages=1, pause=0)
        self.assertIn("area=113", captured["url"])
        self.assertIn("area=1", captured["url"])


if __name__ == "__main__":
    unittest.main()
