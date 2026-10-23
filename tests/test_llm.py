# -*- coding: utf-8 -*-
"""Тесты LLM-контура (hhassist.llm): настройки, ошибки, разбор JSON, откат к правилам.

Сеть не используется: обращения к API подменяются заглушками.
"""
import os
import sys
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import profile, sample_vacancy  # noqa: E402

from hhassist import letters, llm, scoring  # noqa: E402


def settings(**over):
    """Настройки LLM для тестов: ключ фиктивный, реальных запросов нет."""
    base = {
        "llm_provider": "",
        "llm_api_key": "",
        "llm_model": "",
        "llm_base_url": "",
        "llm_timeout": 5,
        "llm_retries": 0,
        "llm_json_mode": False,
    }
    base.update(over)
    return SimpleNamespace(**base)


class FakeResponse:
    """Минимальная замена ответа urlopen для тестов."""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ResolveBaseUrlTest(unittest.TestCase):
    def test_explicit_url_wins(self):
        s = settings(llm_base_url="https://example.test/v1/", llm_provider="openai")
        self.assertEqual(llm.resolve_base_url(s), "https://example.test/v1")

    def test_provider_default(self):
        self.assertEqual(llm.resolve_base_url(settings(llm_provider="openai")),
                         "https://api.openai.com/v1")

    def test_unknown_provider_gives_empty(self):
        self.assertEqual(llm.resolve_base_url(settings(llm_provider="unknown")), "")


class IsEnabledTest(unittest.TestCase):
    def test_disabled_without_key(self):
        self.assertFalse(llm.is_enabled(settings(llm_model="gpt", llm_provider="openai")))

    def test_enabled_via_provider(self):
        self.assertTrue(llm.is_enabled(settings(llm_api_key="k", llm_model="gpt",
                                                llm_provider="openai")))

    def test_describe_does_not_leak_key(self):
        text = llm.describe(settings(llm_api_key="secret-key-123", llm_model="gpt",
                                     llm_provider="openai"))
        self.assertIn("gpt", text)
        self.assertNotIn("secret-key-123", text)

    def test_describe_lists_missing_items(self):
        text = llm.describe(settings())
        self.assertIn("LLM_API_KEY", text)
        self.assertIn("офлайн", text)


class ExtractJsonTest(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(llm.extract_json('{"score": 10}'), {"score": 10})

    def test_fenced_block(self):
        self.assertEqual(llm.extract_json('```json\n{"score": 20}\n```'), {"score": 20})

    def test_text_around_object(self):
        self.assertEqual(llm.extract_json('Оценка:\n{"score": 30, "verdict": "ок"}\nГотово'),
                         {"score": 30, "verdict": "ок"})

    def test_garbage(self):
        self.assertIsNone(llm.extract_json("нет json"))
        self.assertIsNone(llm.extract_json(""))


class ChatTest(unittest.TestCase):
    def test_not_configured_raises(self):
        with self.assertRaises(llm.LLMError):
            llm.chat(settings(), [{"role": "user", "content": "привет"}])

    def test_bad_key_message_hides_secret(self):
        error = urllib.error.HTTPError("https://api.test/v1/chat/completions", 401,
                                       "Unauthorized", {}, None)
        s = settings(llm_api_key="secret-key-123", llm_model="gpt",
                     llm_base_url="https://api.test/v1")
        with mock.patch.object(llm.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.chat(s, [{"role": "user", "content": "привет"}])
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn("secret-key-123", str(ctx.exception))

    def test_retry_after_temporary_error(self):
        error = urllib.error.HTTPError("https://api.test/v1/chat/completions", 429,
                                       "Too Many Requests", {}, None)
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(timeout)
            if len(calls) == 1:
                raise error
            return FakeResponse(b'{"choices":[{"message":{"content":"ok"}}]}')

        s = settings(llm_api_key="k", llm_model="gpt", llm_base_url="https://api.test/v1",
                     llm_retries=1, llm_timeout=1)
        with mock.patch.object(llm.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(llm.time, "sleep", lambda *_: None):
            self.assertEqual(llm.chat(s, [{"role": "user", "content": "привет"}]), "ok")
        self.assertEqual(len(calls), 2)

    def test_json_mode_flag_goes_to_payload(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = request.data.decode("utf-8")
            return FakeResponse(b'{"choices":[{"message":{"content":"{}"}}]}')

        s = settings(llm_api_key="k", llm_model="gpt", llm_base_url="https://api.test/v1")
        with mock.patch.object(llm.urllib.request, "urlopen", side_effect=fake_urlopen):
            llm.chat_json(s, [{"role": "user", "content": "привет"}])
        self.assertIn("json_object", captured["body"])


class ScoringWithLLMTest(unittest.TestCase):
    def test_llm_error_falls_back_to_rules(self):
        s = settings(llm_api_key="k", llm_model="gpt", llm_base_url="https://api.test/v1")
        with mock.patch.object(llm, "chat_json", side_effect=llm.LLMError("HTTP 500")):
            res = scoring.score_vacancy(sample_vacancy(), profile(), s)
        self.assertEqual(res["model"], "rules")
        self.assertEqual(res["score"], scoring.rule_score(sample_vacancy(), profile())[0])
        self.assertTrue(any("LLM недоступен" in r for r in res["reasons"]))

    def test_weighted_average_with_llm(self):
        s = settings(llm_api_key="k", llm_model="gpt-4o-mini", llm_base_url="https://api.test/v1")
        payload = {"score": 90, "verdict": "откликаться", "reasons": ["совпадает роль"],
                   "focus": "RAG и агенты"}
        with mock.patch.object(llm, "chat_json", return_value=payload):
            res = scoring.score_vacancy(sample_vacancy(), profile(), s)
        rules = scoring.rule_score(sample_vacancy(), profile())[0]
        self.assertEqual(res["score"], int(round(0.45 * rules + 0.55 * 90)))
        self.assertEqual(res["model"], "gpt-4o-mini")
        self.assertTrue(any("фокус" in r for r in res["reasons"]))

    def test_answer_without_score_is_ignored(self):
        s = settings(llm_api_key="k", llm_model="gpt", llm_base_url="https://api.test/v1")
        with mock.patch.object(llm, "chat_json", return_value={"verdict": "условно"}):
            res = scoring.score_vacancy(sample_vacancy(), profile(), s)
        self.assertEqual(res["model"], "rules")
        self.assertTrue(any("без оценки" in r for r in res["reasons"]))


class LettersWithLLMTest(unittest.TestCase):
    def test_llm_text_is_used(self):
        s = settings(llm_api_key="k", llm_model="gpt-4o-mini", llm_base_url="https://api.test/v1")
        text = "Здравствуйте!\n\n" + "Опыт с LLM и RAG. " * 40
        with mock.patch.object(llm, "chat", return_value=text):
            made, model = letters.make_letter(sample_vacancy(), profile(), s)
        self.assertEqual(model, "gpt-4o-mini")
        self.assertEqual(made, text.strip())

    def test_llm_failure_uses_offline(self):
        s = settings(llm_api_key="k", llm_model="gpt", llm_base_url="https://api.test/v1")
        with mock.patch.object(llm, "chat", side_effect=llm.LLMError("timeout")):
            text, model = letters.make_letter(sample_vacancy(), profile(), s)
        self.assertEqual(model, "offline")
        self.assertEqual(letters.validate_letter(text), [])

    def test_invalid_llm_text_uses_offline(self):
        s = settings(llm_api_key="k", llm_model="gpt", llm_base_url="https://api.test/v1")
        with mock.patch.object(llm, "chat", return_value="**markdown**"):
            text, model = letters.make_letter(sample_vacancy(), profile(), s)
        self.assertEqual(model, "offline")
        self.assertEqual(letters.validate_letter(text), [])


class SettingsEnvTest(unittest.TestCase):
    def test_llm_options_from_env(self):
        from hhassist import config
        env = {"LLM_API_KEY": "k", "LLM_MODEL": "gpt", "LLM_PROVIDER": "openai",
               "LLM_TIMEOUT": "30", "LLM_RETRIES": "5", "LLM_JSON_MODE": "true"}
        with mock.patch.object(config, "load_env", lambda path=None: env), \
                mock.patch.object(config, "load_profile",
                                  lambda path=None: dict(config.DEFAULT_PROFILE)):
            s = config.get_settings()
        self.assertEqual(s.llm_timeout, 30)
        self.assertEqual(s.llm_retries, 5)
        self.assertTrue(s.llm_json_mode)
        self.assertTrue(llm.is_enabled(s))

    def test_defaults_without_env(self):
        from hhassist import config
        with mock.patch.object(config, "load_env", lambda path=None: {}), \
                mock.patch.object(config, "load_profile",
                                  lambda path=None: dict(config.DEFAULT_PROFILE)):
            s = config.get_settings()
        self.assertEqual(s.llm_timeout, 90)
        self.assertEqual(s.llm_retries, 2)
        self.assertFalse(s.llm_json_mode)
        self.assertFalse(llm.is_enabled(s))


class CombineScoresTest(unittest.TestCase):
    """Формула итогового балла: веса вынесены в scoring.combine_scores."""

    def test_weights_are_45_55(self):
        self.assertEqual(scoring.combine_scores(100, 0), 45)
        self.assertEqual(scoring.combine_scores(0, 100), 55)
        self.assertEqual(scoring.combine_scores(60, 60), 60)

    def test_rounding_matches_formula(self):
        self.assertEqual(scoring.combine_scores(13, 87), int(round(0.45 * 13 + 0.55 * 87)))


class CompareScoresToolTest(unittest.TestCase):
    """tools/compare_scores.py: расчёт сравнения без обращений к сети."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "compare_scores.py")
        spec = importlib.util.spec_from_file_location("compare_scores", path)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def test_without_llm_uses_rules(self):
        with mock.patch.object(scoring, "llm_score", return_value=None):
            item = self.tool.compare(sample_vacancy(), profile(), settings())
        self.assertIsNone(item["llm"])
        self.assertEqual(item["final"], item["rule"])
        self.assertEqual(item["diff"], 0)

    def test_with_llm_applies_weights(self):
        s = settings(llm_api_key="k", llm_model="gpt", llm_base_url="https://api.test/v1")
        with mock.patch.object(scoring, "llm_score", return_value={"score": 90, "reasons": []}):
            item = self.tool.compare(sample_vacancy(), profile(), s)
        self.assertEqual(item["llm"], 90)
        self.assertEqual(item["final"], scoring.combine_scores(item["rule"], 90))
        self.assertEqual(item["diff"], 90 - item["rule"])


if __name__ == "__main__":
    unittest.main()
