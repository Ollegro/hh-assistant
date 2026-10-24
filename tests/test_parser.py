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


class DescriptionFormatTest(unittest.TestCase):
    """Описание вакансии: абзацы сохраняются, скрипты и хвост страницы не попадают."""

    def parse(self, description_html, tail=""):
        html = ('<h1 data-qa="vacancy-title">AI-инженер</h1>'
                '<div data-qa="vacancy-description">%s</div>%s' % (description_html, tail))
        return collector.parse_vacancy(html, "1")["description"]

    def test_paragraphs_are_kept(self):
        text = self.parse("<p>Первый абзац.</p><p>Второй абзац.</p>")
        self.assertEqual(text, "Первый абзац.\nВторой абзац.")

    def test_list_items_become_bullets(self):
        text = self.parse("<ul><li>Python</li><li>LLM</li></ul>")
        self.assertIn("• Python", text)
        self.assertIn("• LLM", text)

    def test_script_inside_block_is_dropped(self):
        text = self.parse("<p>Ищем инженера.</p><script>var secret = 1;</script>")
        self.assertIn("Ищем инженера", text)
        self.assertNotIn("secret", text)

    def test_page_tail_after_block_is_not_included(self):
        tail = '<script>window.jqueryMigrateLTROnLoad = function(){try{}}</script>'
        text = self.parse("<p>Только текст вакансии.</p>", tail=tail)
        self.assertEqual(text, "Только текст вакансии.")

    def test_unclosed_script_is_cut_by_marker(self):
        """HTML обрезан внутри <script> — текст кода всё равно не просачивается."""
        html = ('<div data-qa="vacancy-description"><p>Текст.</p>'
                'window.jqueryMigrateLTROnLoad = function() {try { jQuery.UNSAFE_restore')
        text = collector.parse_vacancy(html, "1")["description"]
        self.assertEqual(text, "Текст.")

    def test_html_to_text_unescapes_and_tightens_spaces(self):
        text = collector.html_to_text("<p>Ищем&nbsp;&amp;  ждём</p><p></p><p>Второй</p>")
        self.assertEqual(text, "Ищем & ждём\nВторой")

    def test_long_description_cut_by_paragraph(self):
        text = self.parse("<p>" + ("строка текста " * 400) + "</p>")
        self.assertLessEqual(len(text), 4000)


class DescriptionRefreshTest(unittest.TestCase):
    """Отбор вакансий для перекачки описаний (tools/refresh_descriptions.py)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = os.path.join(APP, "tools", "refresh_descriptions.py")
        spec = importlib.util.spec_from_file_location("refresh_descriptions", path)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_dirty_texts_need_refresh(self):
        self.assertTrue(self.mod.needs_refresh("Текст jQuery(function(){})"))
        self.assertTrue(self.mod.needs_refresh("Один абзац без переносов"))
        self.assertTrue(self.mod.needs_refresh(""))

    def test_code_terms_in_vacancy_text_are_not_dirty(self):
        """«function/tool calling» — это текст вакансии, а не мусор страницы."""
        text = "Первый абзац.\nОпыт с function/tool calling и structured output."
        self.assertFalse(self.mod.needs_refresh(text))

    def test_clean_text_is_skipped(self):
        self.assertFalse(self.mod.needs_refresh("Первый абзац.\nВторой абзац."))
        self.assertTrue(self.mod.needs_refresh("Первый абзац.\nВторой.", force=True))


if __name__ == "__main__":
    unittest.main()
