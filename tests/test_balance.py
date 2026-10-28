# -*- coding: utf-8 -*-
"""Тесты баланса LLM (DeepSeek GET /user/balance) и плашки в углу страницы.

Сеть не используется: ответ API подменяется заглушкой.
"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, profile  # noqa: E402,F401

from hhassist import llm, queue, storage, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402

SECRET = "sk-тестовый-ключ-1234567890"
PAYLOAD = {"is_available": True,
           "balance_infos": [{"currency": "CNY", "total_balance": "12.34",
                              "granted_balance": "2.34", "topped_up_balance": "10.00"}]}


def llm_settings(**over):
    """Настройки LLM: DeepSeek, ключ фиктивный, реальных запросов нет."""
    base = {"llm_provider": "deepseek", "llm_api_key": SECRET,
            "llm_model": "deepseek-chat", "llm_base_url": "",
            "llm_timeout": 5, "llm_retries": 0}
    base.update(over)
    return SimpleNamespace(**base)


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class BalanceUrlTest(unittest.TestCase):
    def test_v1_is_stripped(self):
        self.assertEqual(
            llm.balance_url(llm_settings(llm_base_url="https://api.deepseek.com/v1")),
            "https://api.deepseek.com/user/balance")

    def test_root_is_kept(self):
        self.assertEqual(
            llm.balance_url(llm_settings(llm_base_url="https://api.deepseek.com/")),
            "https://api.deepseek.com/user/balance")

    def test_provider_detection(self):
        self.assertTrue(llm.balance_supported(llm_settings()))
        self.assertFalse(llm.balance_supported(
            llm_settings(llm_provider="openai", llm_base_url="https://api.openai.com/v1")))


class BalanceFetchTest(unittest.TestCase):
    def setUp(self):
        llm._BALANCE_CACHE.clear()
        self.addCleanup(llm._BALANCE_CACHE.clear)

    def test_parses_amounts(self):
        with mock.patch.object(llm.urllib.request, "urlopen", return_value=FakeResponse(PAYLOAD)):
            data = llm.balance(llm_settings())
        self.assertTrue(data["ok"])
        self.assertEqual(data["infos"][0]["total"], "12.34")
        self.assertEqual(data["infos"][0]["currency"], "CNY")
        self.assertEqual(llm.format_balance(data), "12.34 CNY")

    def test_result_is_cached(self):
        with mock.patch.object(llm.urllib.request, "urlopen",
                               return_value=FakeResponse(PAYLOAD)) as opened:
            llm.balance(llm_settings())
            llm.balance(llm_settings())
        self.assertEqual(opened.call_count, 1)

    def test_force_refreshes(self):
        with mock.patch.object(llm.urllib.request, "urlopen",
                               return_value=FakeResponse(PAYLOAD)) as opened:
            llm.balance(llm_settings())
            llm.balance(llm_settings(), force=True)
        self.assertEqual(opened.call_count, 2)

    def test_key_is_not_in_result(self):
        with mock.patch.object(llm.urllib.request, "urlopen", return_value=FakeResponse(PAYLOAD)):
            data = llm.balance(llm_settings())
        self.assertNotIn(SECRET, json.dumps(data, ensure_ascii=False))

    def test_http_error_is_reported_without_key(self):
        error = llm.urllib.error.HTTPError("https://api.deepseek.com/user/balance", 401,
                                           "unauthorized", {}, None)
        with mock.patch.object(llm.urllib.request, "urlopen", side_effect=error):
            data = llm.balance(llm_settings())
        self.assertFalse(data["ok"])
        self.assertIn("401", data["error"])
        self.assertNotIn(SECRET, json.dumps(data, ensure_ascii=False))

    def test_disabled_without_key(self):
        self.assertFalse(llm.balance(llm_settings(llm_api_key=""))["ok"])


class BalanceInfoTest(unittest.TestCase):
    def info(self, data, **over):
        settings = llm_settings(**over)
        with mock.patch.object(web, "get_settings", lambda: settings), \
                mock.patch.object(llm, "balance", lambda s, **kw: data):
            return web.balance_info()

    def test_text_and_label(self):
        info = self.info({"ok": True, "available": True, "checked": "2026-10-28T03:11:43",
                          "infos": [{"currency": "CNY", "total": "12.34"}]})
        self.assertEqual(info["label"], "DeepSeek")
        self.assertEqual(info["text"], "12.34 CNY")
        self.assertTrue(info["checked"])

    def test_low_balance_is_flagged(self):
        info = self.info({"ok": True, "available": False, "checked": "",
                          "infos": [{"currency": "CNY", "total": "0.00"}]})
        self.assertTrue(info["text"].startswith("⚠"))

    def test_error_is_shown_as_text(self):
        info = self.info({"ok": False, "error": "нет связи (URLError)", "infos": []})
        self.assertEqual(info["text"], "нет связи (URLError)")

    def test_no_info_without_llm(self):
        self.assertEqual(self.info({"ok": True, "checked": "", "infos": []},
                                   llm_api_key="", llm_model=""), {})


class BalanceBoxTest(unittest.TestCase):
    """Плашка баланса появляется внизу страницы, только когда есть LLM-ключ."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH",
                                    os.path.join(self.tmp.name, "test.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = storage.connect()
        self.addCleanup(self.conn.close)

    def page(self, settings):
        for module in (web, queue):
            p = mock.patch.object(module, "get_settings", lambda: settings)
            p.start()
            self.addCleanup(p.stop)
        return web.render_index(self.conn, 10)

    def test_box_present_for_deepseek(self):
        settings = Settings(profile=dict(DEFAULT_PROFILE), llm_provider="deepseek",
                            llm_api_key=SECRET, llm_model="deepseek-chat")
        page = self.page(settings)
        self.assertIn("id='llm-balance'", page)
        self.assertIn("DeepSeek", page)
        self.assertNotIn(SECRET, page)

    def test_box_absent_without_key(self):
        self.assertNotIn("llm-balance", self.page(Settings(profile=dict(DEFAULT_PROFILE))))


if __name__ == "__main__":
    unittest.main()

