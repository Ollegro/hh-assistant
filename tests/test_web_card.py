# -*- coding: utf-8 -*-
"""Тесты карточки вакансии: блок оценки, окно правил и его данные.

Что проверяется (по замечаниям заказчика):
- причины оценки выводятся списком, а не сырым JSON;
- «фокус» LLM — отдельной строкой «Что подчеркнуть в письме»;
- окно «Как считается оценка» содержит правила с весами и цифры по вакансии
  (балл правил / балл LLM / итог / вердикт);
- окно открывается само после нажатия «Переоценить вакансию» (`data-open='1'`).
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import sample_vacancy  # noqa: E402

from hhassist import jobs, queue, scoring, storage, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402


def offline_settings(**over):
    """Настройки без ключа LLM: скоринг считает только правила."""
    data = {"profile": dict(DEFAULT_PROFILE), "daily_limit": 200,
            "collect_limit": 20, "collect_pages": 1, "collect_queries": 0}
    data.update(over)
    return Settings(**data)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH",
                                    os.path.join(self.tmp.name, "test.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = storage.connect()
        self.addCleanup(self.conn.close)
        settings = offline_settings()
        for module in (web, queue):
            p = mock.patch.object(module, "get_settings", lambda: settings)
            p.start()
            self.addCleanup(p.stop)

    def add(self, vid="1", reasons=None, score=90, verdict="откликаться",
            rule_score=None, llm_score=None, **over):
        storage.upsert_vacancy(self.conn, sample_vacancy(id=vid, **over))
        storage.save_score(self.conn, vid, score, verdict, reasons or [],
                           rule_score=rule_score, llm_score=llm_score)
        return web.render_card(self.conn, vid)


class ScorePartsTest(unittest.TestCase):
    """JSON-причины из базы -> список для страницы."""

    def test_focus_goes_apart(self):
        raw = json.dumps(["роль в заголовке: промпт-инженер", "зарплата не указана",
                          "фокус: подчеркнуть RAG и автотесты"], ensure_ascii=False)
        reasons, focus = web.score_parts(raw)
        self.assertEqual(reasons, ["роль в заголовке: промпт-инженер", "зарплата не указана"])
        self.assertEqual(focus, "подчеркнуть RAG и автотесты")

    def test_broken_json_is_shown_as_text(self):
        self.assertEqual(web.score_parts("не json"), (["не json"], ""))
        self.assertEqual(web.score_parts(""), ([], ""))
        self.assertEqual(web.score_parts(None), ([], ""))

    def test_string_payload(self):
        self.assertEqual(web.score_parts(json.dumps("одна причина")), (["одна причина"], ""))



class CardScoreBlockTest(Base):
    """Карточка: список причин, фокус письма, кнопка правил."""

    def test_reasons_are_a_list_not_raw_json(self):
        page = self.add(reasons=["роль в заголовке: промпт-инженер", "формат: удалённо"])
        self.assertIn("<li>роль в заголовке: промпт-инженер</li>", page)
        self.assertIn("<li>формат: удалённо</li>", page)
        self.assertNotIn('["роль', page)          # сырого JSON в странице нет

    def test_focus_is_shown_separately(self):
        page = self.add(reasons=["опыт 1–3 года", "фокус: показать RAG-бота"])
        self.assertIn("Что подчеркнуть в письме:", page)
        self.assertIn("показать RAG-бота", page)
        self.assertNotIn("<li>фокус:", page)

    def test_card_has_rules_button_and_rerate(self):
        page = self.add()
        self.assertIn("Правила оценки", page)
        self.assertIn("Переоценить вакансию", page)
        self.assertNotIn("Оценить вакансию</button>", page)

    def test_empty_reasons_hint(self):
        page = self.add(reasons=[])
        self.assertIn("причин нет", page)

    def test_description_block_keeps_line_breaks(self):
        page = self.add(description="Первый абзац.\nВторой абзац.")
        self.assertIn("<div class='desc'>", page)
        self.assertIn("Первый абзац.", page)
        self.assertIn("white-space:pre-line", web.CSS)
        self.assertIn("полный текст на hh", page)


class RulesDialogTest(Base):
    """Окно «Как считается оценка»: правила, веса и результат вакансии."""

    def test_rules_and_shares(self):
        page = self.add(score=90, verdict="откликаться", rule_score=80, llm_score=95)
        self.assertIn("<dialog id='rules'", page)
        self.assertIn("Оценка как в школе, от 1 до 5", page)
        self.assertIn("правила: <b>5</b> из 5", page)
        self.assertIn("LLM: <b>5</b> из 5", page)
        self.assertIn("оценка <b>5</b> из 5", page)
        self.assertIn("вердикт <b>откликаться</b>", page)
        # 100-балльная шкала в интерфейсе больше не показывается
        self.assertNotIn("из 100", page)
        self.assertNotIn("итог <b>90</b>", page)

    def test_all_rule_weights_listed(self):
        page = self.add()
        for title, points, _note in scoring.RULE_WEIGHTS:
            self.assertIn(web.esc(title), page)
            self.assertIn(web.esc(points), page)

    def test_closed_by_default_and_open_after_rating(self):
        closed = self.add()
        self.assertIn("data-open='0'", closed)
        opened = web.render_card(self.conn, "1", show_rules=True)
        self.assertIn("data-open='1'", opened)
        self.assertIn("box.showModal()", opened)

    def test_old_score_without_components(self):
        page = self.add(rule_score=None, llm_score=None)
        self.assertIn("составляющие не сохранены", page)


class ScoreOneComponentsTest(Base):
    """Оценка одной вакансии сохраняет балл правил и балл LLM отдельно."""

    def test_components_saved(self):
        storage.upsert_vacancy(self.conn, sample_vacancy(id="1"))
        score, _verdict = jobs.score_one(self.conn, offline_settings(), "1")
        row = self.conn.execute("SELECT * FROM scores WHERE vacancy_id = '1'").fetchone()
        self.assertEqual(row["score"], score)
        self.assertEqual(row["rule_score"], score, "без LLM итог = правила")
        self.assertIsNone(row["llm_score"])

    def test_llm_score_saved(self):
        storage.upsert_vacancy(self.conn, sample_vacancy(id="1"))
        with mock.patch.object(scoring, "llm_score",
                               return_value={"score": 96, "verdict": "откликаться",
                                             "reasons": ["x"], "model": "test"}):
            jobs.score_one(self.conn, offline_settings(), "1")
        row = self.conn.execute("SELECT * FROM scores WHERE vacancy_id = '1'").fetchone()
        self.assertEqual(row["llm_score"], 96)
        self.assertIsNotNone(row["rule_score"])


if __name__ == "__main__":
    unittest.main()
