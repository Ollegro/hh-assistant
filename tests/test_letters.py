# -*- coding: utf-8 -*-
"""Тесты генерации и проверки письма (hhassist.letters)."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

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


class RequirementsTest(unittest.TestCase):
    """Требования вакансии — то, на что письмо должно отвечать."""

    def test_bullets_become_list(self):
        vacancy = sample_vacancy(description="Чем предстоит заниматься\n"
                                             "• Проектировать RAG-пайплайны\n"
                                             "• Развивать агентов на LangGraph")
        text = letters.requirements(vacancy)
        self.assertIn("- Проектировать RAG-пайплайны", text)
        self.assertIn("- Развивать агентов на LangGraph", text)

    def test_fallback_to_sentences_without_bullets(self):
        vacancy = sample_vacancy(
            description="Нужен инженер в команду. Разработка RAG-ассистентов на Python и интеграций с LLM.")
        self.assertIn("- Разработка RAG-ассистентов", letters.requirements(vacancy))

    def test_empty_description_gives_empty_string(self):
        self.assertEqual(letters.requirements(sample_vacancy(description="")), "")


class RoboticMarkersTest(unittest.TestCase):
    """Штампы, из-за которых письмо читается как автоматическое."""

    ROBOTIC = ("Здравствуйте! Откликаюсь на вакансию Python-разработчика в QSOFT/TEAMLY. "
               "Мой профиль — RAG-системы. Честно о пробелах: не использовал Kubernetes. "
               "Вилка: 180 000–250 000 ₽.")

    def test_detects_template_phrases(self):
        found = letters.robotic_markers(self.ROBOTIC)
        for marker in ("откликаюсь на вакансию", "мой профиль —",
                       "честно о пробелах", "вилка:"):
            self.assertIn(marker, found)

    def test_live_letter_has_no_markers(self):
        text = letters.offline_letter(sample_vacancy(), profile())
        self.assertEqual(letters.robotic_markers(text), [])

    def test_offline_letter_drops_labels(self):
        text = letters.offline_letter(sample_vacancy(), profile())
        for label in ("Что подтверждаю результатами", "Вилка:", "Чего пока не делал",
                      "С уважением"):
            self.assertNotIn(label, text)


class RelevantGapsTest(unittest.TestCase):
    """В письме — только те пробелы, которых касается вакансия."""

    def test_only_gaps_mentioned_in_vacancy(self):
        data = dict(profile())
        data["gaps"] = ["fine-tuning моделей (обучение весов)", "Kubernetes в продакшене"]
        vacancy = sample_vacancy(description="Python, FastAPI, Docker, Kubernetes, GitLab CI")
        self.assertEqual(letters.relevant_gaps(vacancy, data), ["Kubernetes в продакшене"])

    def test_no_relevant_gaps_gives_empty_list(self):
        data = dict(profile())
        data["gaps"] = ["B2B-продажи в IT"]
        self.assertEqual(letters.relevant_gaps(sample_vacancy(), data), [])


class RetryTest(unittest.TestCase):
    """Шаблонный первый ответ модели переписывается, а не уходит клиенту."""

    ROBOTIC = ("Здравствуйте! Откликаюсь на вакансию, мой профиль — RAG. "
               "Честно о пробелах: нет Kubernetes. Вилка: 180 000–250 000 ₽. "
               + "Текст для объёма. " * 30)
    HUMAN = ("Здравствуйте!\n\nПосмотрел вакансию и зацепился за RAG-пайплайны: последние два "
             "года делаю ровно это — retrieval по 167 источникам, 828 автотестов, честные "
             "таймауты и понятные контракты ответов.\n\nЕсли интересно, покажу код и расскажу, "
             "как считал качество.\n\n" + "Текст для объёма. " * 20)

    def test_robotic_first_attempt_is_retried(self):
        calls = []

        def fake_letter(v, prof, settings=None, strict=False):
            calls.append(strict)
            return self.ROBOTIC if len(calls) == 1 else self.HUMAN

        settings = SimpleNamespace(llm_model="deepseek", llm_api_key="x")
        with mock.patch.object(letters, "llm_letter", fake_letter):
            text, model = letters.make_letter(sample_vacancy(), profile(), settings)
        self.assertEqual(calls, [False, True])
        self.assertEqual(model, "deepseek")
        self.assertEqual(text, self.HUMAN)

    def test_offline_when_both_attempts_robotic(self):
        settings = SimpleNamespace(llm_model="deepseek", llm_api_key="x")
        with mock.patch.object(letters, "llm_letter", lambda *a, **k: self.ROBOTIC):
            text, model = letters.make_letter(sample_vacancy(), profile(), settings)
        self.assertEqual(model, "offline")
        self.assertEqual(letters.robotic_markers(text), [])

    def test_single_stamp_keeps_model_letter(self):
        """Один мягкий штамп не повод отдавать шаблон: живой текст модели лучше."""
        soft = ("Здравствуйте!\n\nКак видно из резюме, я работаю с RAG и LLM. "
                + "Опыт и цифры. " * 40)
        settings = SimpleNamespace(llm_model="deepseek", llm_api_key="x")
        with mock.patch.object(letters, "llm_letter", lambda *a, **k: soft):
            text, model = letters.make_letter(sample_vacancy(), profile(), settings)
        self.assertEqual(model, "deepseek")
        self.assertEqual(text, soft)


class PromptTest(unittest.TestCase):
    """В промпт уходят требования и навыки вакансии, а повтор — с усилением."""

    VACANCY = dict(skills=["Python", "Qdrant"],
                   description="Чем предстоит заниматься\n"
                               "• Проектировать RAG-пайплайны\n• Работать с Qdrant")

    def prompt_for(self, strict=False):
        captured = {}

        def fake_chat(settings, messages, temperature=0):
            captured["prompt"] = messages[0]["content"]
            return "Здравствуйте!\n\n" + "Опыт с RAG и LLM. " * 40

        fake_llm = SimpleNamespace(is_enabled=lambda s: True, LLMError=RuntimeError,
                                   chat=fake_chat)
        with mock.patch.object(letters, "llm", fake_llm):
            letters.llm_letter(sample_vacancy(**self.VACANCY), profile(),
                               SimpleNamespace(llm_api_key="x", llm_model="m"), strict=strict)
        return captured["prompt"]

    def test_prompt_carries_requirements_and_skills(self):
        prompt = self.prompt_for()
        self.assertIn("Требования и задачи из описания", prompt)
        self.assertIn("- Проектировать RAG-пайплайны", prompt)
        self.assertIn("Python, Qdrant", prompt)

    def test_strict_prompt_adds_warning(self):
        prompt = self.prompt_for(strict=True)
        self.assertIn("предыдущая попытка звучала как шаблонное письмо", prompt.lower())


if __name__ == "__main__":
    unittest.main()
