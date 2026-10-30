# -*- coding: utf-8 -*-
"""Тесты внешних площадок: нормализация, парсеры, диспетчер сбора, дедупликация.

Сеть не используется: парсеры проверяются на синтетической разметке, сбор — на
подменённой функции источника. Настоящие страницы площадок в тестах не читаем.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401

from hhassist import collector, jobs, storage  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402
from hhassist.sources import getmatch, habr, make, superjob, trudvsem  # noqa: E402


def offline_settings(**over):
    data = {"profile": dict(DEFAULT_PROFILE), "collect_limit": 20, "collect_pages": 1,
            "collect_queries": 0}
    data.update(over)
    return Settings(**data)


class MakeTest(unittest.TestCase):
    """Нормализованная вакансия площадки: id с префиксом, source = имя площадки."""

    def test_id_and_source(self):
        vacancy = make("habr", 12345, "  AI-инженер ", " Ромашка ",
                       "https://career.habr.com/vacancies/12345")
        self.assertEqual(vacancy["id"], "habr:12345")
        self.assertEqual(vacancy["source"], "habr")
        self.assertEqual(vacancy["title"], "AI-инженер")
        self.assertEqual(vacancy["company"], "Ромашка")
        self.assertEqual(vacancy["skills"], [])

    def test_extra_fields_override_defaults(self):
        vacancy = make("trudvsem", "abc", "Инженер", "Фирма", "", salary_from=100000,
                       work_format="удалённо", description="текст")
        self.assertEqual(vacancy["salary_from"], 100000)
        self.assertEqual(vacancy["work_format"], "удалённо")
        self.assertEqual(vacancy["description"], "текст")


class TrudvesmTest(unittest.TestCase):
    """«Работа России»: маппинг полей открытого API."""

    ITEM = {
        "id": "05d39665",
        "job-name": "Инженер по данным",
        "company": {"name": "Ромашка"},
        "region": {"name": "Московская область"},
        "salary_min": 100000,
        "salary_max": 150000,
        "duty": "Собирать данные",
        "requirements": "Python, SQL",
        "skills": ["Python", "SQL"],
        "vac_url": "https://trudvsem.ru/vacancy/card/1/05d39665",
        "creation-date": "2026-10-28",
        "requirement": {"experience": 2},
    }

    def test_maps_fields(self):
        vacancy = trudvsem.vacancy_from_item(self.ITEM)
        self.assertEqual(vacancy["id"], "trudvsem:05d39665")
        self.assertEqual(vacancy["title"], "Инженер по данным")
        self.assertEqual(vacancy["company"], "Ромашка")
        self.assertEqual((vacancy["salary_from"], vacancy["salary_to"]), (100000, 150000))
        self.assertEqual(vacancy["currency"], "RUR")
        self.assertEqual(vacancy["experience"], "1–3 года")
        self.assertIn("Собирать данные", vacancy["description"])
        self.assertEqual(vacancy["skills"], ["Python", "SQL"])

    def test_equal_salary_and_missing_fields(self):
        vacancy = trudvsem.vacancy_from_item({"salary_min": 90000, "salary_max": 90000})
        self.assertEqual((vacancy["salary_from"], vacancy["salary_to"]), (90000, None))
        empty = trudvsem.vacancy_from_item({})
        self.assertIsNone(empty["salary_from"])
        self.assertEqual(empty["title"], "")



class HabrTest(unittest.TestCase):
    LIST = (
        '<div class="vacancy-card vacancy-card--bp">'
        '<a aria-label="AI-инженер (LLM)" class="vacancy-card__backdrop-link" '
        'href="/vacancies/1000168585"></a>'
        '<div class="vacancy-card__inner"><div class="vacancy-card__company">'
        '<a class="link-comp link-comp--appearance-dark" href="/companies/ibs">IBS</a></div>'
        '<time class="basic-date" datetime="2026-10-28T10:42:40+03:00">28 октября</time>'
        '</div></div>'
        '<div class="vacancy-card">'
        '<a aria-label="Data Scientist" class="vacancy-card__backdrop-link" '
        'href="/vacancies/1000169544"></a>'
        '<div class="vacancy-card__company"><a class="link-comp" href="/companies/x">Лига</a></div>'
        '</div>'
    )
    CARD = (
        '<div class="vacancy-header"><h1 class="page-title__title">AI-инженер (LLM)</h1>'
        '<div class="vacancy-meta"><div class="chip-with-icon__text">Senior</div>'
        '<div class="chip-with-icon__text">Можно удалённо</div>'
        '<div class="vacancy-card__salary">От 200 000 до 300 000 ₽</div></div></div>'
        '<div class="vacancy-description__text"><div class="style-ugc">'
        '<p>Собирать RAG-пайплайны</p><ul><li>Python</li></ul></div></div></section>'
    )

    def test_parse_list(self):
        items = habr.parse_list(self.LIST)
        self.assertEqual([v["id"] for v in items], ["habr:1000168585", "habr:1000169544"])
        self.assertEqual(items[0]["title"], "AI-инженер (LLM)")
        self.assertEqual(items[0]["company"], "IBS")
        self.assertEqual(items[0]["published_at"], "2026-10-28")
        self.assertEqual(items[0]["source"], "habr")

    def test_parse_card(self):
        card = habr.parse_card(self.CARD)
        self.assertEqual(card["title"], "AI-инженер (LLM)")
        self.assertEqual((card["salary_from"], card["salary_to"]), (200000, 300000))
        self.assertEqual(card["currency"], "RUR")
        self.assertEqual(card["work_format"], "удалённо")
        self.assertEqual(card["experience"], "Senior")
        self.assertIn("Собирать RAG-пайплайны", card["description"])


class SuperJobTest(unittest.TestCase):
    ITEM = {
        "id": 555,
        "profession": "AI-инженер",
        "firm_name": "Ромашка",
        "town": {"title": "Москва"},
        "payment_from": 150000,
        "payment_to": 200000,
        "currency": "rub",
        "experience": {"title": "От 1 года до 3 лет"},
        "date_published": 1793000000,
        "link": "https://www.superjob.ru/vakansii/ai-inzhener-555.html",
        "work": "Разработка ассистентов",
        "candidat": "Python, LLM",
    }

    def test_maps_fields(self):
        vacancy = superjob.vacancy_from_item(self.ITEM)
        self.assertEqual(vacancy["id"], "superjob:555")
        self.assertEqual(vacancy["title"], "AI-инженер")
        self.assertEqual(vacancy["company"], "Ромашка")
        self.assertEqual((vacancy["salary_from"], vacancy["salary_to"]), (150000, 200000))
        self.assertEqual(vacancy["currency"], "RUR")
        self.assertEqual(vacancy["area"], "Москва")
        self.assertIn("Разработка ассистентов", vacancy["description"])
        self.assertTrue(vacancy["published_at"].startswith("20"))

    def test_search_without_key_explains(self):
        settings = offline_settings(superjob_app_id="")
        with mock.patch("hhassist.config.get_settings", lambda: settings):
            with self.assertRaises(RuntimeError) as ctx:
                superjob.search("python")
        self.assertIn("SUPERJOB_APP_ID", str(ctx.exception))


class GetmatchTest(unittest.TestCase):
    CARD = (
        "<html><head><title>AI-инженер — getmatch</title></head><body>"
        "<h1>AI-инженер (LLM)</h1>"
        "<div>Вакансия AI-инженер (LLM), работа в Ромашка, удалённо — getmatch</div>"
        "<div>Удалённо</div><div>Требуемый опыт 6 + лет</div><div>от 250 000 ₽</div>"
        "<div>Поделиться</div><div>Разработка RAG-ассистентов на Python</div>"
        "<div>Похожие вакансии</div></body></html>"
    )
    ARCHIVED = CARD.replace("Поделиться", "Вакансия в архиве Поделиться")

    def test_parse_card(self):
        vacancy = getmatch.parse_card(self.CARD, "https://getmatch.ru/vacancies/35653-ai")
        self.assertEqual(vacancy["id"], "getmatch:35653")
        self.assertEqual(vacancy["title"], "AI-инженер (LLM)")
        self.assertEqual(vacancy["company"], "Ромашка")
        self.assertEqual(vacancy["work_format"], "удалённо")
        self.assertEqual(vacancy["experience"], "более 6 лет")
        self.assertEqual(vacancy["salary_from"], 250000)
        self.assertIn("RAG-ассистентов", vacancy["description"])
        self.assertFalse(vacancy["archived"])


    def test_archived_is_flagged(self):
        vacancy = getmatch.parse_card(self.ARCHIVED, "https://getmatch.ru/vacancies/1-x")
        self.assertTrue(vacancy["archived"])


class DispatchTest(unittest.TestCase):
    """collector.collect: внешние площадки и понятная ошибка на неизвестный источник."""

    def test_external_source_dispatch(self):
        settings = offline_settings()
        found = [make("trudvsem", "1", "Инженер", "Ромашка", "u1"),
                 make("trudvsem", "2", "Аналитик", "Лютик", "u2")]
        with mock.patch("hhassist.collector.get_settings", lambda: settings), \
                mock.patch("hhassist.sources.trudvsem.search", lambda *a, **k: list(found)):
            vacancies, info = collector.collect("python", area=[1], source="trudvsem")
        self.assertEqual(info["source"], "trudvsem")
        self.assertEqual(info["found"], 2)
        self.assertEqual([v["id"] for v in vacancies], ["trudvsem:1", "trudvsem:2"])

    def test_limit_applied(self):
        settings = offline_settings()
        found = [make("trudvsem", str(index), "Инженер %d" % index, "Фирма", "u")
                 for index in range(5)]
        with mock.patch("hhassist.collector.get_settings", lambda: settings), \
                mock.patch("hhassist.sources.trudvsem.search", lambda *a, **k: list(found)):
            vacancies, _info = collector.collect("python", source="trudvsem", limit=2)
        self.assertEqual(len(vacancies), 2)

    def test_unknown_source_raises(self):
        settings = offline_settings()
        with mock.patch("hhassist.collector.get_settings", lambda: settings):
            with self.assertRaises(ValueError) as ctx:
                collector.collect("python", source="avito")
        self.assertIn("avito", str(ctx.exception))


class DedupTest(unittest.TestCase):
    """Одна вакансия с двух площадок не должна попасть в базу дважды."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH",
                                    os.path.join(self.tmp.name, "test.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = storage.connect()
        self.addCleanup(self.conn.close)

    def test_find_duplicate_by_title_and_company(self):
        storage.upsert_vacancy(self.conn, sample_vacancy(id="hh:1", title="AI-инженер",
                                                         company="Ромашка"))
        found = storage.find_duplicate(self.conn, {"id": "habr:9", "title": "ai-инженер",
                                                   "company": " Ромашка ", "source": "habr"})
        self.assertEqual(found, "hh:1")

    def test_same_platform_is_not_a_duplicate(self):
        """Две вакансии одной площадки с одинаковым названием — это норма."""
        storage.upsert_vacancy(self.conn, sample_vacancy(id="hh:1", title="AI-инженер",
                                                         company="Ромашка"))
        self.assertEqual(storage.find_duplicate(
            self.conn, {"id": "hh:2", "title": "AI-инженер", "company": "Ромашка",
                        "source": "html"}), "")

    def test_no_duplicate_without_company(self):
        storage.upsert_vacancy(self.conn, sample_vacancy(id="hh:1", title="AI-инженер",
                                                         company="Ромашка"))
        self.assertEqual(storage.find_duplicate(self.conn,
                                                {"id": "x", "title": "AI-инженер",
                                                 "company": ""}), "")

    def test_collect_skips_duplicate_and_reports(self):
        storage.upsert_vacancy(self.conn, sample_vacancy(id="hh:1", title="AI-инженер",
                                                         company="Ромашка"))
        settings = offline_settings(collect_sources="trudvsem")
        vacancies = [make("trudvsem", "7", "AI-инженер", "Ромашка", "u")]
        info = {"source": "trudvsem", "fallback": False, "reason": "", "found": 1,
                "taken": 1, "skipped": 0, "errors": [], "quality": {}}
        with mock.patch.object(collector, "collect", lambda *a, **k: (vacancies, info)):
            saved, found, source, notes = jobs.collect_queries(
                self.conn, ["python"], settings, sources=("trudvsem",))
        self.assertEqual(saved, 0)
        self.assertEqual(found, 1)
        self.assertEqual(source, "trudvsem")
        self.assertTrue(any("похожие вакансии с других площадок" in note for note in notes))
