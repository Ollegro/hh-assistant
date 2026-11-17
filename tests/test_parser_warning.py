# -*- coding: utf-8 -*-
"""Тесты плашки «парсер мог сломаться»: предупреждение гасится успешным сбором.

06.11.2026 на проде висело предупреждение от 14:06 с «заголовков пусто 28», хотя ручная
проверка HTML-парсера в тот же вечер дала 0 пустых заголовков на 6 вакансиях: плашка
смотрела только на событие `parser_suspect` и не замечала последующий здоровый сбор.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401
from test_web_actions import WebBase  # noqa: E402

from hhassist import storage, web  # noqa: E402

SUSPECT = {"query": "AI-инженер", "source": "html",
           "quality": {"state": "suspect", "taken": 40, "empty_titles": 28,
                       "short_descriptions": 0}}


def stamp(minutes_ago=0):
    """Время в формате базы: «сколько минут назад» — чтобы тесты не зависели от даты."""
    return (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


class ParserWarningTest(WebBase):
    def log(self, kind, payload, ts):
        """Пишет событие с заданным временем: порядок в журнале — по id."""
        self.conn.execute("INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
                          (ts, kind, storage.json.dumps(payload, ensure_ascii=False)))
        self.conn.commit()

    def test_no_events_no_warning(self):
        self.assertEqual(web.parser_warning(self.conn), "")

    def test_fresh_suspect_shows_warning(self):
        self.log("parser_suspect", SUSPECT, stamp(minutes_ago=5))
        text = web.parser_warning(self.conn)
        self.assertIn("парсер мог сломаться", text)
        self.assertIn("AI-инженер", text)
        self.assertIn("28", text)

    def test_successful_html_collect_clears_warning(self):
        self.log("parser_suspect", SUSPECT, stamp(minutes_ago=130))
        self.log("collect", {"source": "html", "saved": 163, "found": 529},
                 stamp(minutes_ago=10))
        self.assertEqual(web.parser_warning(self.conn), "")

    def test_api_collect_does_not_clear_html_warning(self):
        """Сбор через API не доказывает, что HTML-парсер жив."""
        self.log("parser_suspect", SUSPECT, stamp(minutes_ago=130))
        self.log("collect", {"source": "api", "saved": 5, "found": 8}, stamp(minutes_ago=10))
        self.assertIn("парсер мог сломаться", web.parser_warning(self.conn))

    def test_old_suspect_is_hidden(self):
        self.log("parser_suspect", SUSPECT, stamp(minutes_ago=60 * 48))
        self.assertEqual(web.parser_warning(self.conn, hours=24), "")

    def test_suspect_after_good_collect_shows_warning(self):
        """Обратный порядок: сначала здоровый сбор, потом поломка — плашка нужна."""
        self.log("collect", {"source": "html", "saved": 10}, stamp(minutes_ago=130))
        self.log("parser_suspect", SUSPECT, stamp(minutes_ago=5))
        self.assertIn("парсер мог сломаться", web.parser_warning(self.conn))


if __name__ == "__main__":
    unittest.main()
