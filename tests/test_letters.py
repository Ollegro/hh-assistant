# -*- coding: utf-8 -*-
"""Тесты генерации и проверки письма (hhassist.letters)."""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import profile, sample_vacancy  # noqa: E402

from hhassist import letters  # noqa: E402


class ValidateLetterTest(unittest.TestCase):
    def test_markdown_detected(self):
        body = "Здравствуйте!\n\n## Заголовок\n" + "Опыт с LLM и RAG. " * 40
        self.assertIn("markdown", letters.validate_letter(body))

    def test_short_letter(self):
        self.assertIn("слишком короткое", letters.validate_letter("Здравствуйте! Коротко."))

    def test_no_greeting(self):
        self.assertIn("нет приветствия", letters.validate_letter("Добрый день! " + "Опыт с LLM. " * 40))

    def test_ok_letter(self):
        self.assertEqual(letters.validate_letter("Здравствуйте!\n\n" + "Опыт с LLM и RAG. " * 40), [])


class OfflineLetterTest(unittest.TestCase):
    def test_structure(self):
        text = letters.offline_letter(sample_vacancy(), profile())
        self.assertTrue(text.startswith("Здравствуйте"))
        self.assertIn("GitHub: https://github.com/Ollegro", text)
        self.assertIn("180–250", text)
        self.assertIn("AI-инженер (LLM)", text)

    def test_make_letter_without_llm(self):
        settings = SimpleNamespace(llm_api_key="", llm_base_url="", llm_model="")
        text, model = letters.make_letter(sample_vacancy(), profile(), settings)
        self.assertEqual(model, "offline")
        self.assertEqual(letters.validate_letter(text), [])


class SamplesTest(unittest.TestCase):
    def test_samples_glob_is_absolute_or_empty(self):
        glob = letters.SAMPLES_GLOB
        self.assertTrue(glob == "" or os.path.isabs(glob))


if __name__ == "__main__":
    unittest.main()
