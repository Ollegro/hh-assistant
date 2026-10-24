# -*- coding: utf-8 -*-
"""Тесты главной страницы: один выбор региона и одна строка кнопок.

Проверяется то, о чём просил заказчик: регион выбирается в одном месте (селект
задаёт и регион сбора, и фильтр списка), а кнопки действий стоят в общей панели
и имеют одинаковую высоту. Сеть не используется: временная база, заглушка
сбора, локальный сервер на петлевом адресе.
"""
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401

from hhassist import collector, queue, storage, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402


def offline_settings(**over):
    """Настройки без ключа LLM: скоринг считает только правила."""
    data = {"profile": dict(DEFAULT_PROFILE), "daily_limit": 200,
            "collect_limit": 20, "collect_pages": 1, "collect_queries": 0}
    data.update(over)
    return Settings(**data)


class Base(unittest.TestCase):
    """Временная база и офлайн-настройки: data/ не трогаем."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH",
                                    os.path.join(self.tmp.name, "test.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = storage.connect()
        self.addCleanup(self.conn.close)
        self.use_settings(offline_settings())

    def use_settings(self, settings):
        for module in (web, queue):
            p = mock.patch.object(module, "get_settings", lambda: settings)
            p.start()
            self.addCleanup(p.stop)

    def scored(self, vid, score=80, **over):
        storage.upsert_vacancy(self.conn, sample_vacancy(id=vid, **over), commit=False)
        storage.save_score(self.conn, vid, score, "подходит", [], commit=False)
        self.conn.commit()


class SingleRegionSelectTest(Base):
    """Один селект региона на страницу: он же задаёт и сбор, и фильтр."""

    def page(self, **kwargs):
        return web.render_index(self.conn, kwargs.pop("limit", 10), **kwargs)

    def test_only_one_region_select(self):
        page = self.page()
        self.assertEqual(page.count("name='region'"), 1)
        self.assertNotIn("name='area'", page)          # второго выбора региона нет
        self.assertNotIn("name='city'", page)          # фильтра по городам нет
        self.assertNotIn("name='threshold'", page)     # порога оценки нет

    def test_region_options_carry_hh_ids(self):
        page = self.page()
        self.assertIn("<option value='113'", page)     # вся Россия
        self.assertIn("<option value='2019'", page)    # Московская область: id hh
        self.assertIn("Московская область", page)

    def test_selected_region_is_marked(self):
        page = self.page(region="2019")
        self.assertIn("<option value='2019' selected>", page)

    def test_region_id_filters_the_list(self):
        self.scored("1", area="Химки", region="Московская область")
        self.scored("2", area="Москва", region="Москва")
        page = self.page(region="2019")
        self.assertIn("Химки", page)
        self.assertNotIn("href='/v/2'", page)

    def test_all_russia_means_no_region_filter(self):
        self.scored("1", area="Химки", region="Московская область")
        self.scored("2", area="Москва", region="Москва")
        page = self.page(region="113")
        self.assertIn("href='/v/1'", page)
        self.assertIn("href='/v/2'", page)

    def test_no_threshold_filter_any_more(self):
        """Порога нет: в списке обе вакансии, даже с низким баллом."""
        self.scored("1", score=90, area="Химки", region="Московская область")
        self.scored("2", score=10, area="Мытищи", region="Московская область")
        page = self.page(region="2019")
        self.assertIn("href='/v/1'", page)
        self.assertIn("href='/v/2'", page)


class ResolveRegionTest(unittest.TestCase):
    """Значение селекта региона: id hh, название (старые ссылки) и пусто."""

    def test_id(self):
        self.assertEqual(web._resolve_region("2019"), ("2019", "Московская область"))

    def test_all_russia_has_no_filter(self):
        self.assertEqual(web._resolve_region("113"), ("113", ""))

    def test_old_name_links(self):
        self.assertEqual(web._resolve_region("Московская область"),
                         ("2019", "Московская область"))

    def test_empty_and_unknown(self):
        self.assertEqual(web._resolve_region(""), ("", ""))
        self.assertEqual(web._resolve_region(None), ("", ""))
        self.assertEqual(web._resolve_region("Нигде"), ("", "Нигде"))


class ToolbarTest(Base):
    """Кнопки действий — в одной панели с селектами, одинаковой высоты."""

    def test_actions_live_in_single_toolbar(self):
        page = web.render_index(self.conn, 10)
        self.assertIn("class='toolbar'", page)
        self.assertLess(page.index("class='toolbar'"), page.index("Собрать вакансии"))
        for label in ("Собрать вакансии", "Оценить", "Сгенерировать письма",
                      "Экспорт писем в файлы"):
            self.assertIn(label, page)
        self.assertNotIn("Воронка", page)  # лишняя функция убрана из панели
        self.assertEqual(page.count("formaction='/run/"), 3)

    def test_css_keeps_same_height_for_buttons_and_selects(self):
        css = web.CSS.replace("\n", "")
        self.assertIn(".btn{display:inline-flex", web.CSS)
        self.assertIn("height:36px", css)
        self.assertIn("select{height:36px", css)
        self.assertIn(".toolbar{display:flex", web.CSS)
        self.assertIn("align-items:center", web.CSS)


class CollectFromToolbarTest(Base):
    """POST /run/collect берёт регион из того же селекта, что и фильтр."""

    def setUp(self):
        super().setUp()
        self.use_settings(offline_settings())
        quiet = mock.patch.object(web.Handler, "log_message", lambda *a, **k: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def post(self, path, fields):
        data = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read().decode("utf-8", "replace")

    def test_region_from_toolbar_goes_to_collector(self):
        calls = []

        def fake_collect(query, **kwargs):
            calls.append(dict(kwargs, query=query))
            info = {"source": "html", "fallback": False, "reason": "", "found": 0,
                    "taken": 0, "new": 0, "refreshed": 0, "skipped": 0, "errors": [],
                    "quality": collector.quality([])}
            return [], info

        with mock.patch.object(web, "_queries", lambda: ["AI инженер"]), \
                mock.patch.object(collector, "collect", fake_collect):
            status, page = self.post("/run/collect", {"region": "1"})
        self.assertEqual(status, 200)
        self.assertIn("запущено: сбор вакансий", page)
        self.assertTrue(web.wait_run(30), "фоновый сбор не завершился")
        self.assertIn("собрано вакансий", web.RUN["report"])
        self.assertEqual(calls[0]["area"], [1])

    def test_score_button_works_from_same_toolbar(self):
        """Пакетная оценка уходит в фон: страница отвечает сразу, прогон идёт отдельно."""
        status, page = self.post("/run/score", {"region": "113"})
        self.assertEqual(status, 200)
        self.assertIn("запущено: оценка", page)
        self.assertTrue(web.wait_run(30), "фоновый прогон не завершился")
        self.assertEqual(web.RUN["error"], "")
        self.assertIn("оценено новых", web.RUN["report"])


if __name__ == "__main__":
    unittest.main()
