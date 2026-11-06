"""Фильтры по всей базе, пагинация и сохранение выбора."""
import os
import sys
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP
from test_web_layout import Base
from hhassist import web, vacancy_filters, collector, hh_api


class FiltersTest(Base):
    def setUp(self):
        super().setUp()
        for i in range(65):
            self.scored(str(i), score=100 - i, company="Обычная", area="Москва", region="Москва")
        self.scored("target", score=1, title="Технолог", company="Молоко", area="Йошкар-Ола",
                    region="Республика Марий Эл", work_format="офис", experience="нет опыта",
                    salary_from=0, salary_to=90000)

    def test_filter_finds_row_beyond_first_fifty(self):
        page = web.render_index(self.conn, 50, filters={"f_company": ["Молоко"]})
        self.assertIn("href='/v/target'", page)
        self.assertNotIn("href='/v/0'", page)
        self.assertIn("value='Молоко' checked", page)
        self.assertIn("Найдено по фильтрам: <b>1</b>", page)

    def test_all_columns_and_combination(self):
        query = {"f_score": ["1"], "f_title": ["Технолог"], "f_company": ["Молоко"],
                 "f_city": ["Йошкар-Ола"], "f_format": ["офис"],
                 "f_experience": ["нет опыта"], "f_salary": ["0–90000 RUR"]}
        page = web.render_index(self.conn, 50, filters=query)
        self.assertIn("href='/v/target'", page)
        query["f_score"] = ["99"]
        page = web.render_index(self.conn, 50, filters=query)
        self.assertNotIn("href='/v/target'", page)
        self.assertIn("Найдено по фильтрам: <b>0</b>", page)

    def test_pagination_keeps_filters_and_region(self):
        page = web.render_index(self.conn, 10, region="113", filters={"f_company": ["Обычная"]}, page=2)
        self.assertIn("страница 2 из 7", page)
        self.assertIn("f_company=", page)
        self.assertIn("page=3", page)
        self.assertIn("region=113", page)
        self.assertNotIn("href='/v/target'", page)
        self.assertEqual(page.count(">Открыть</a>"), 10)

    def test_case_safe_html_and_multi_selection(self):
        self.scored("special", title="<script>alert(1)</script>", company="A&B")
        page = web.render_index(self.conn, 50, filters={"f_company": ["Молоко", "A&B"]})
        self.assertIn("href='/v/special'", page)
        self.assertIn("href='/v/target'", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)


class FormatAndSalaryTest(unittest.TestCase):
    """Формат работы и зарплата: JSON-LD важнее «не указан» в анонимном HTML.

    Случай с живой вакансии 136901954 (06.11.2026): работодатель написал в тексте
    «Формат работы: гибрид», hh разметил её как ON_SITE, а вилку (100 000 ₽) анонимно
    не показал вовсе — из-за этого правило профиля штрафовало подходящую вакансию.
    """

    def parse(self, body):
        return collector.parse_vacancy(body, "1")

    def test_text_format_wins_over_on_site(self):
        v = self.parse('<html><body>Формат работы: гибрид'
                       '{"workFormats":["ON_SITE"]}</body></html>')
        self.assertIn("гибрид", v["work_format"])
        self.assertIn("офис", v["work_format"])

    def test_remote_from_json_ld(self):
        v = self.parse('<html><body><script type="application/ld+json">'
                       '{"@type": "JobPosting", "jobLocationType": "TELECOMMUTE"}'
                       '</script></body></html>')
        self.assertEqual(v["work_format"], "удалённо")

    def test_salary_from_json_ld_when_html_says_none(self):
        v = self.parse('<html><body>Уровень дохода не указан'
                       '<script type="application/ld+json">{"@type":"JobPosting",'
                       '"baseSalary":{"@type":"MonetaryAmount","currency":"RUR",'
                       '"value":{"@type":"QuantitativeValue","minValue":100000,'
                       '"maxValue":120000}}}</script></body></html>')
        self.assertEqual((v["salary_from"], v["salary_to"], v["currency"]),
                         (100000, 120000, "RUR"))

    def test_published_from_json_ld(self):
        v = self.parse('<html><body><script type="application/ld+json">'
                       '{"@type":"JobPosting","datePosted":"2026-11-02T09:59:22+03:00"}'
                       '</script></body></html>')
        self.assertEqual(v["published_at"], "2026-11-02T09:59:22+03:00")

    def test_broken_json_ld_does_not_break_parsing(self):
        v = self.parse('<html><body><script type="application/ld+json">{битый}</script>'
                       'Уровень дохода не указано</body></html>')
        self.assertEqual(v["salary_from"], None)
        self.assertEqual(collector.json_ld("<html></html>"), {})

    def test_json_ld_string_numbers(self):
        # Ревью 06.11.2026: hh отдаёт суммы строкой — int("100000") на строке падал
        # в min(), и парсинг карточки целиком ломался с ValueError.
        v = self.parse('<html><body><script type="application/ld+json">{"@type":"JobPosting",'
                       '"baseSalary":{"currency":"RUB","value":{"minValue":"100000",'
                       '"maxValue":"120000"}}}</script></body></html>')
        self.assertEqual((v["salary_from"], v["salary_to"], v["currency"]),
                         (100000, 120000, "RUR"))

    def test_json_ld_single_value(self):
        v = self.parse('<html><body><script type="application/ld+json">{"@type":"JobPosting",'
                       '"baseSalary":{"currency":"RUR","value":100000}}</script></body></html>')
        self.assertEqual((v["salary_from"], v["salary_to"], v["currency"]),
                         (100000, None, "RUR"))

    def test_json_ld_without_currency(self):
        v = self.parse('<html><body><script type="application/ld+json">{"@type":"JobPosting",'
                       '"baseSalary":{"value":{"minValue":90000}}}</script></body></html>')
        self.assertEqual((v["salary_from"], v["currency"]), (90000, ""))


class SearchOrderTest(unittest.TestCase):
    """Сортировка выдачи: по умолчанию релевантность, дата — по настройке.

    При order_by=publication_time нужная вакансия стояла 244-й из 876 и не попадала
    в первую страницу (COLLECT_PAGES=1), поэтому порядок стал настраиваемым.
    """

    def request_params(self, **kwargs):
        urls = []
        with mock.patch.object(collector, "http_get",
                               side_effect=lambda url: urls.append(url) or ""):
            collector.search_ids("AI-инженер", area=[113], pause=0, **kwargs)
        # keep_blank_values: по умолчанию parse_qs выбрасывает order_by="" (релевантность)
        return urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query,
                                     keep_blank_values=True)

    def test_default_is_relevance(self):
        # по умолчанию параметра нет — hh сортирует по релевантности, как сайт
        self.assertNotIn("order_by", self.request_params())

    def test_order_by_date_when_asked(self):
        params = self.request_params(order_by="publication_time")
        self.assertEqual(params["order_by"], ["publication_time"])

    def test_pages_are_requested(self):
        urls = []
        with mock.patch.object(collector, "http_get",
                               side_effect=lambda url: urls.append(url) or ""):
            collector.search_ids("AI-инженер", area=[113], pages=3, pause=0)
        self.assertEqual(len(urls), 3)


class HhSourceAliasTest(unittest.TestCase):
    """Площадка "hh" из списка источников не должна падать в collector.collect.

    jobs.collect_queries передаёт source="hh" — до 06.11.2026 это давало ValueError,
    ошибка глоталась как «поиск не удался», и прогон молча сохранял 0 вакансий.
    """

    def test_hh_source_is_auto(self):
        calls = []

        def fake_search_ids(query, **kwargs):
            calls.append(kwargs)
            return []

        with mock.patch.object(collector, "search_ids", fake_search_ids), \
                mock.patch.object(collector, "get_settings",
                                  lambda: mock.Mock(hh_source="html", hh_api_timeout=1,
                                                    hh_api_retries=1, delay_seconds=0,
                                                    user_agent="t", hh_app_token="",
                                                    hh_client_id="", hh_client_secret="")):
            vacancies, info = collector.collect("q", area=[113], source="hh")
        self.assertEqual(vacancies, [])
        self.assertEqual(info["source"], "html")
        self.assertEqual(calls[0]["order_by"], None)

    def test_unknown_source_still_raises(self):
        with self.assertRaises(ValueError):
            collector.collect("q", source="нечто")


class RegionRequestsTest(unittest.TestCase):
    def test_html_request_has_only_selected_area(self):
        urls = []
        with mock.patch.object(collector, "http_get", side_effect=lambda url: urls.append(url) or ""):
            collector.search_ids("технолог", area=[1620], pause=0)
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)
        self.assertEqual(params["area"], ["1620"])
        self.assertNotIn("113", params["area"])

    def test_api_request_has_only_selected_area(self):
        with mock.patch.object(hh_api, "get", return_value={"items": [], "pages": 1}) as get:
            hh_api.search_vacancies("технолог", area=[1620], pages=1)
        self.assertEqual(get.call_args[0][1]["area"], [1620])


if __name__ == "__main__":
    unittest.main()
