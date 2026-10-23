# -*- coding: utf-8 -*-
"""Тесты API-источника hh.ru: hh_api, api_adapter и collector.collect (сеть подменена)."""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401

from hhassist import api_adapter, collector, hh_api  # noqa: E402
from hhassist.config import Settings  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
TOKEN = "test-token-value"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class Headers(dict):
    """Заголовки ответа: нужен только .get(), как у http.client.HTTPMessage."""


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = Headers(headers or {})

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return urllib.error.HTTPError("https://api.hh.ru/x", code, "error",
                                  Headers(headers or {}), io.BytesIO(body))


def header(request, name):
    """Значение заголовка без учёта регистра (urllib нормализует имена)."""
    for key, value in request.header_items():
        if key.lower() == name.lower():
            return value
    return None


class ApiBase(unittest.TestCase):
    """Подменяет настройки, сеть и паузы: тесты не ходят в hh.ru."""

    def setUp(self):
        self.settings = Settings(
            user_agent="hh-assistant-test/0.1 (test@example.com)",
            hh_app_token=TOKEN,
            hh_source="auto",
            hh_api_timeout=5,
            hh_api_retries=2,
            delay_seconds=0,
        )
        for module in (hh_api, collector):
            patcher = mock.patch.object(module, "get_settings", lambda: self.settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        sleeper = mock.patch.object(hh_api.time, "sleep", lambda *_: None)
        sleeper.start()
        self.addCleanup(sleeper.stop)
        hh_api.reset_token()
        hh_api.clear_cache()
        self.addCleanup(hh_api.clear_cache)
        self.addCleanup(hh_api.reset_token)
        self.requests = []

    def serve(self, *responses):
        """Ответы по очереди; последний повторяется (удобно для повторов)."""
        queue = list(responses)

        def _open(request, timeout):
            self.requests.append(request)
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return item

        patcher = mock.patch.object(hh_api, "_open", _open)
        patcher.start()
        self.addCleanup(patcher.stop)


class AdapterTest(unittest.TestCase):
    def test_maps_full_vacancy(self):
        v = api_adapter.vacancy_from_api(fixture("vacancy_api.json"))
        self.assertEqual(v["id"], "12345")
        self.assertEqual(v["title"], "AI-инженер (LLM)")
        self.assertIn("Ромашка", v["company"])
        self.assertEqual((v["salary_from"], v["salary_to"], v["currency"]),
                         (200000, 250000, "RUR"))
        self.assertEqual(v["work_format"], "удалённо")
        self.assertEqual(v["experience"], "1–3 года")
        self.assertIn("Python", v["skills"])
        self.assertIn("RAG", v["description"])
        self.assertNotIn("<p>", v["description"])
        self.assertEqual(v["url"], "https://hh.ru/vacancy/12345")
        self.assertTrue(v["published_at"].startswith("2026-10-23"))
        self.assertEqual(v["source"], "api")

    def test_snippet_used_when_no_description(self):
        v = api_adapter.vacancy_from_api(fixture("search_api.json")["items"][0])
        self.assertEqual(v["work_format"], "удалённо")
        self.assertEqual(v["experience"], "1–3 года")
        self.assertIn("Разработка ассистентов", v["description"])
        self.assertIn("Python", v["description"])
        self.assertEqual(v["skills"], [])

    def test_empty_item_does_not_break(self):
        v = api_adapter.vacancy_from_api({})
        self.assertEqual(v["id"], "")
        self.assertEqual(v["title"], "")
        self.assertIsNone(v["salary_from"])
        self.assertEqual(v["source"], "api")

    def test_vacancies_from_search(self):
        out = api_adapter.vacancies_from_search(fixture("search_api.json")["items"])
        self.assertEqual([v["id"] for v in out], ["111", "222"])
        self.assertEqual(out[1]["work_format"], "гибрид")
        self.assertEqual(out[1]["experience"], "3–6 лет")
        self.assertIsNone(out[1]["salary_from"])


class TokenTest(ApiBase):
    def test_env_token_used_without_network(self):
        def boom(request, timeout):
            raise AssertionError("сеть не должна вызываться: %s" % request.full_url)

        patcher = mock.patch.object(hh_api, "_open", boom)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertEqual(hh_api.app_token(), TOKEN)
        self.assertTrue(hh_api.configured())
        self.assertTrue(hh_api.has_token())

    def test_client_credentials_request(self):
        self.settings.hh_app_token = ""
        self.settings.hh_client_id = "cid"
        self.settings.hh_client_secret = "csecret"
        self.serve(FakeResponse({"access_token": "fresh-token", "token_type": "bearer"}))
        self.assertEqual(hh_api.app_token(), "fresh-token")
        req = self.requests[0]
        self.assertEqual(req.get_method(), "POST")
        body = req.data.decode("utf-8")
        self.assertIn("grant_type=client_credentials", body)
        self.assertIn("client_id=cid", body)
        self.assertEqual(req.full_url, hh_api.TOKEN_URL)
        self.assertIsNone(header(req, "Authorization"))
        # повторный вызов берёт токен из кэша, без нового запроса
        self.assertEqual(hh_api.app_token(), "fresh-token")
        self.assertEqual(len(self.requests), 1)

    def test_missing_config_raises_without_network(self):
        self.settings.hh_app_token = ""
        self.serve(FakeResponse({}))
        with self.assertRaises(hh_api.HHApiError) as ctx:
            hh_api.app_token()
        self.assertIn("не настроен", str(ctx.exception))
        self.assertEqual(self.requests, [])
        self.assertFalse(hh_api.has_token())


class RequestTest(ApiBase):
    def test_bearer_header_and_empty_params_skipped(self):
        self.serve(FakeResponse({"found": 1, "items": []}, {"ETag": "W/\"1\""}))
        data = hh_api.get("/vacancies", {"text": "python", "area": 1, "page": None})
        self.assertEqual(data["found"], 1)
        req = self.requests[0]
        self.assertEqual(header(req, "Authorization"), "Bearer " + TOKEN)
        self.assertIn("text=python", req.full_url)
        self.assertIn("area=1", req.full_url)
        self.assertNotIn("page=", req.full_url)

    def test_second_request_sends_if_none_match(self):
        self.serve(FakeResponse({"found": 1}, {"ETag": "W/\"1\""}))
        hh_api.get("/vacancies", {"text": "python"})
        hh_api.get("/vacancies", {"text": "python"})
        self.assertEqual(header(self.requests[1], "If-None-Match"), "W/\"1\"")

    def test_retry_on_429(self):
        self.serve(http_error(429, {"errors": [{"type": "limit_exceeded"}]}, {"Retry-After": "0"}),
                   FakeResponse({"found": 7}))
        self.assertEqual(hh_api.get("/vacancies")["found"], 7)
        self.assertEqual(len(self.requests), 2)

    def test_403_raises_and_hides_token(self):
        self.serve(http_error(403, {"description": "Forbidden",
                                    "errors": [{"value": "bad_authorization", "type": "oauth"}],
                                    "request_id": "req-1"}))
        with self.assertRaises(hh_api.HHApiError) as ctx:
            hh_api.get("/vacancies")
        err = ctx.exception
        self.assertEqual(err.status, 403)
        self.assertEqual(err.value, "bad_authorization")
        self.assertEqual(err.request_id, "req-1")
        self.assertNotIn(TOKEN, str(err))
        self.assertEqual(len(self.requests), 1)


class SearchTest(ApiBase):
    def test_depth_limit(self):
        with self.assertRaises(ValueError) as ctx:
            hh_api.search_vacancies("python", pages=21, per_page=100)
        self.assertIn("2000", str(ctx.exception))
        self.assertEqual(self.requests, [])

    def test_per_page_limit(self):
        with self.assertRaises(ValueError):
            hh_api.search_vacancies("python", per_page=101)

    def test_pagination_stops_on_short_page(self):
        items = fixture("search_api.json")["items"]
        self.serve(FakeResponse({"found": 3, "items": items}),
                   FakeResponse({"found": 3, "items": [items[1]]}))
        result = hh_api.search_vacancies("python", pages=3, per_page=2)
        self.assertEqual([i["id"] for i in result], ["111", "222", "222"])
        self.assertEqual(len(self.requests), 2)


class ProbeTest(ApiBase):
    def test_probe_ok(self):
        self.serve(FakeResponse({"id": "app-1", "name": "hh-assistant"}),
                   FakeResponse({"found": 42, "items": []}))
        report = hh_api.probe()
        self.assertIsNone(report["error"])
        self.assertEqual(report["found"], 42)
        self.assertEqual(report["token_source"], "HH_APP_TOKEN")
        self.assertEqual(report["token_length"], len(TOKEN))

    def test_probe_reports_error(self):
        self.serve(FakeResponse({"id": "app-1"}),
                   http_error(403, {"description": "Forbidden",
                                    "errors": [{"type": "forbidden"}],
                                    "request_id": "req-2"}))
        report = hh_api.probe()
        self.assertEqual(report["error"]["status"], 403)
        self.assertEqual(report["error"]["request_id"], "req-2")


class CollectTest(ApiBase):
    def test_auto_without_token_falls_back_to_html(self):
        self.settings.hh_app_token = ""
        with mock.patch.object(collector, "search_ids", lambda *a, **k: ["1"]), \
                mock.patch.object(collector, "fetch_vacancy",
                                  lambda vid, **k: sample_vacancy(id=vid)):
            vacancies, info = collector.collect("AI инженер")
        self.assertEqual(info["source"], "html")
        self.assertTrue(info["fallback"])
        self.assertIn("токен", info["reason"])
        self.assertEqual([v["id"] for v in vacancies], ["1"])
        self.assertEqual(info["errors"], [])

    def test_api_source_uses_search_and_full_cards(self):
        self.serve(FakeResponse(fixture("search_api.json")),
                   FakeResponse(fixture("vacancy_api.json")))
        vacancies, info = collector.collect("AI инженер", per_page=2, limit=1)
        self.assertEqual(info["source"], "api")
        self.assertFalse(info["fallback"])
        self.assertEqual(info["found"], 2)
        self.assertEqual(len(vacancies), 1)
        self.assertEqual(vacancies[0]["id"], "12345")
        self.assertEqual(vacancies[0]["source"], "api")
        self.assertIn("RAG", vacancies[0]["description"])

    def test_api_source_without_details(self):
        self.serve(FakeResponse(fixture("search_api.json")))
        vacancies, info = collector.collect("AI инженер", per_page=2, details=False)
        self.assertEqual([v["id"] for v in vacancies], ["111", "222"])
        self.assertEqual(info["source"], "api")
        self.assertEqual(len(self.requests), 1)

    def test_auto_api_error_falls_back_to_html(self):
        self.serve(http_error(403, {"description": "Forbidden",
                                    "errors": [{"value": "bad_authorization", "type": "oauth"}],
                                    "request_id": "req-3"}))
        with mock.patch.object(collector, "search_ids", lambda *a, **k: []), \
                mock.patch.object(collector, "fetch_vacancy",
                                  lambda vid, **k: sample_vacancy(id=vid)):
            vacancies, info = collector.collect("AI инженер", per_page=2)
        self.assertEqual(info["source"], "html")
        self.assertTrue(info["fallback"])
        self.assertIn("403", info["reason"])
        self.assertEqual(vacancies, [])

    def test_api_mode_raises_instead_of_fallback(self):
        self.serve(http_error(403, {"errors": [{"type": "forbidden"}]}))
        with self.assertRaises(hh_api.HHApiError):
            collector.collect("AI инженер", per_page=2, source="api")

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            collector.collect("AI инженер", source="telegram")

    def test_html_reports_found_before_limit(self):
        self.settings.hh_app_token = ""
        ids = ["1", "2", "3", "4", "5", "6", "7"]
        with mock.patch.object(collector, "search_ids", lambda *a, **k: list(ids)), \
                mock.patch.object(collector, "fetch_vacancy",
                                  lambda vid, **k: sample_vacancy(id=vid)):
            vacancies, info = collector.collect("AI инженер", limit=3)
        self.assertEqual(info["found"], 7)
        self.assertEqual(info["taken"], 3)
        self.assertEqual([v["id"] for v in vacancies], ["1", "2", "3"])

    def test_api_info_has_taken(self):
        self.serve(FakeResponse(fixture("search_api.json")),
                   FakeResponse(fixture("vacancy_api.json")))
        vacancies, info = collector.collect("AI инженер", per_page=2, limit=1)
        self.assertEqual((info["found"], info["taken"]), (2, 1))
        self.assertEqual(len(vacancies), 1)


if __name__ == "__main__":
    unittest.main()


