# -*- coding: utf-8 -*-
"""Тесты ручного добавления вакансии (hhassist/manual.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401

from hhassist import manual  # noqa: E402


class ParseIdTest(unittest.TestCase):
    def test_full_url(self):
        self.assertEqual(manual.parse_hh_id("https://hh.ru/vacancy/136901954"),
                         "136901954")

    def test_url_with_query_and_slash(self):
        self.assertEqual(
            manual.parse_hh_id("https://hh.ru/vacancy/136901954/?query=python#top"),
            "136901954")

    def test_mobile_and_region_hosts(self):
        self.assertEqual(manual.parse_hh_id("https://hh.kz/vacancy/1234567"), "1234567")
        self.assertEqual(manual.parse_hh_id("http://www.hh.ru/vacancy/7654321?from=x"),
                         "7654321")

    def test_bare_number(self):
        self.assertEqual(manual.parse_hh_id("136901954"), "136901954")
        self.assertEqual(manual.parse_hh_id("  136901954  "), "136901954")

    def test_not_a_vacancy(self):
        for value in ("", None, "https://hh.ru/search/vacancy?text=python",
                      "https://habr.com/ru/job/123456/", "AI-инженер", "12345"):
            self.assertEqual(manual.parse_hh_id(value), "", value)


class SyntheticIdTest(unittest.TestCase):
    def test_same_url_same_id(self):
        first = manual.synthetic_id("https://example.com/job/1")
        second = manual.synthetic_id("https://example.com/job/1")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith(manual.MANUAL_PREFIX))

    def test_different_url_different_id(self):
        self.assertNotEqual(manual.synthetic_id("https://example.com/job/1"),
                            manual.synthetic_id("https://example.com/job/2"))

    def test_empty(self):
        self.assertEqual(manual.synthetic_id(""), "")


class SalaryTest(unittest.TestCase):
    def test_single_number(self):
        self.assertEqual(manual._salary("150000"), (150000, None))

    def test_spaces(self):
        self.assertEqual(manual._salary("150 000"), (150000, None))

    def test_range(self):
        self.assertEqual(manual._salary("120000-180000"), (120000, 180000))
        self.assertEqual(manual._salary("180 000 — 120 000"), (120000, 180000))

    def test_no_numbers(self):
        self.assertEqual(manual._salary(""), (None, None))
        self.assertEqual(manual._salary("по договорённости"), (None, None))
        self.assertEqual(manual._salary("500"), (None, None))   # это не зарплата


class BuildTest(unittest.TestCase):
    def test_hh_url_gives_hh_id(self):
        v = manual.build(url="https://hh.ru/vacancy/136901954", title="AI-инженер",
                         company="Клиника", description="Задачи")
        self.assertEqual(v["id"], "136901954")
        self.assertEqual(v["source"], "manual")
        self.assertEqual(v["title"], "AI-инженер")

    def test_foreign_url_gives_manual_id(self):
        v = manual.build(url="https://example.com/job/7", title="Разработчик")
        self.assertTrue(v["id"].startswith(manual.MANUAL_PREFIX))

    def test_multiline_fields_are_flattened(self):
        v = manual.build(title="AI-инженер\n  (офис)  ", company="ООО\nРомашка")
        self.assertEqual(v["title"], "AI-инженер (офис)")
        self.assertEqual(v["company"], "ООО Ромашка")

    def test_description_keeps_line_breaks(self):
        v = manual.build(title="T", description="строка 1\nстрока 2")
        self.assertEqual(v["description"], "строка 1\nстрока 2")

    def test_region_falls_back_to_area(self):
        v = manual.build(title="T", area="Москва")
        self.assertEqual(v["region"], "Москва")


class AddTest(unittest.TestCase):
    def test_fetch_fills_empty_fields(self):
        fetched = sample_vacancy()
        v, note = manual.add(url="https://hh.ru/vacancy/136901954", title="",
                             description="", fetch=lambda _id: fetched)
        self.assertEqual(v["id"], "136901954")
        self.assertEqual(v["title"], fetched["title"])
        self.assertEqual(v["company"], fetched["company"])
        self.assertIn("дополнены", note)

    def test_manual_values_win(self):
        fetched = sample_vacancy()
        v, _note = manual.add(url="https://hh.ru/vacancy/136901954",
                              title="Своё название", company="Своя компания",
                              description="Своё описание",
                              fetch=lambda _id: fetched)
        self.assertEqual(v["title"], "Своё название")
        self.assertEqual(v["company"], "Своя компания")
        self.assertEqual(v["description"], "Своё описание")

    def test_fetch_failure_keeps_manual_data(self):
        def boom(_id):
            raise OSError("сеть недоступна")

        v, note = manual.add(url="https://hh.ru/vacancy/136901954", title="AI-инженер",
                             description="Описание", fetch=boom)
        self.assertEqual(v["title"], "AI-инженер")
        self.assertEqual(v["id"], "136901954")
        self.assertIn("не удалось", note)

    def test_foreign_url_does_not_call_network(self):
        calls = []

        def fetch(hh_id):
            calls.append(hh_id)
            return sample_vacancy()

        v, note = manual.add(url="https://example.com/job/7", title="Разработчик",
                             description="Описание", fetch=fetch)
        self.assertEqual(calls, [])
        self.assertTrue(v["id"].startswith(manual.MANUAL_PREFIX))
        self.assertIn("не на hh.ru", note)


class ValidateTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(manual.validate({"title": "T", "description": "D"}), [])

    def test_requires_title(self):
        self.assertTrue(manual.validate({"title": " ", "description": "D"}))

    def test_requires_description(self):
        problems = manual.validate({"title": "T", "description": ""})
        self.assertEqual(len(problems), 1)
        self.assertIn("описание", problems[0])


if __name__ == "__main__":
    unittest.main()
