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
