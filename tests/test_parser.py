# -*- coding: utf-8 -*-
"""Тесты HTML-парсера вакансий (hhassist.collector) на сохранённой фикстуре."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP  # noqa: E402,F401

from hhassist import collector  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "vacancy.html")


class ParseVacancyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding="utf-8") as f:
            cls.v = collector.parse_vacancy(f.read(), "12345")

    def test_title(self):
        self.assertEqual(self.v["title"], "AI-инженер (LLM)")

    def test_company(self):
        self.assertIn("Ромашка", self.v["company"])

    def test_salary_and_currency(self):
        self.assertEqual(self.v["salary_from"], 200000)
        self.assertEqual(self.v["salary_to"], 250000)
        self.assertEqual(self.v["currency"], "RUR")

    def test_format_and_experience(self):
        self.assertEqual(self.v["work_format"], "удалённо")
        self.assertEqual(self.v["experience"], "1–3 года")

    def test_skills_and_description(self):
        self.assertIn("Python", self.v["skills"])
        self.assertIn("RAG", self.v["description"])
        self.assertIn("pgvector", self.v["description"])

    def test_published_and_url(self):
        self.assertEqual(self.v["url"], "https://hh.ru/vacancy/12345")
        self.assertTrue(self.v["published_at"].startswith("2026-10-23"))

    def test_source(self):
        self.assertEqual(self.v["source"], "html")


class CleanTest(unittest.TestCase):
    def test_strips_tags_and_scripts(self):
        raw = "<div><script>var a = 1;</script>Текст   <b>жирный</b></div>"
        self.assertEqual(collector.clean(raw), "Текст жирный")


if __name__ == "__main__":
    unittest.main()
