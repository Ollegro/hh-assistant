# -*- coding: utf-8 -*-
"""Тесты скоринга вакансий (hhassist.scoring)."""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import profile, sample_vacancy  # noqa: E402

from hhassist import scoring  # noqa: E402


class RuleScoreTest(unittest.TestCase):
    def test_stop_word_penalty(self):
        base, _, _ = scoring.rule_score(sample_vacancy(), profile())
        stop, reasons, _ = scoring.rule_score(sample_vacancy(title="Стажер AI-инженер"), profile())
        self.assertLess(stop, base)
        self.assertTrue(any("стоп-слово" in r for r in reasons))

    def test_low_salary_penalty(self):
        _, reasons, _ = scoring.rule_score(
            sample_vacancy(salary_from=60000, salary_to=80000), profile())
        self.assertTrue(any("ниже ожиданий" in r for r in reasons))

    def test_format_outside_preferences(self):
        _, reasons, _ = scoring.rule_score(
            sample_vacancy(work_format="на месте работодателя"), profile())
        self.assertTrue(any("формат вне предпочтений" in r for r in reasons))

    def test_hard_stop_on_low_score(self):
        v = sample_vacancy(
            title="Инженер по сопровождению ИС",
            description="Поддержка пользователей 1С и печать документов.",
            skills=["1С"],
            salary_from=None, salary_to=None, currency="",
            work_format="на месте работодателя", experience="нет опыта",
        )
        score, reasons, hard_stop = scoring.rule_score(v, profile())
        self.assertTrue(hard_stop)
        self.assertLess(score, 30)
        self.assertTrue(any("уровень ниже целевого" in r for r in reasons))

    def test_excluded_company_is_hard_stop(self):
        p = profile()
        p["exclude_companies"] = ["Ромашка"]
        score, reasons, hard_stop = scoring.rule_score(sample_vacancy(company="ООО Ромашка"), p)
        self.assertEqual(score, 0)
        self.assertTrue(hard_stop)
        self.assertTrue(any("стоп-лист" in r for r in reasons))


class ScoreVacancyTest(unittest.TestCase):
    def test_offline_rules_verdict(self):
        res = scoring.score_vacancy(sample_vacancy(), profile(),
                                    SimpleNamespace(llm_api_key="", llm_base_url="", llm_model=""))
        self.assertEqual(set(res), {"score", "verdict", "reasons", "model",
                                    "rule_score", "llm_score"})
        self.assertEqual(res["model"], "rules")
        self.assertEqual(res["rule_score"], res["score"], "без LLM итог равен правилам")
        self.assertIsNone(res["llm_score"])
        self.assertGreaterEqual(res["score"], 65)
        self.assertEqual(res["verdict"], "откликаться")

    def test_irrelevant_vacancy_not_approved(self):
        v = sample_vacancy(title="Менеджер по продажам оборудования",
                           description="Холодные звонки, работа с клиентами, план продаж.",
                           skills=["Продажи"], salary_from=50000, salary_to=70000,
                           work_format="на месте работодателя")
        res = scoring.score_vacancy(v, profile(),
                                    SimpleNamespace(llm_api_key="", llm_base_url="", llm_model=""))
        self.assertEqual(res["verdict"], "не откликаться")
        self.assertLess(res["score"], 30)


if __name__ == "__main__":
    unittest.main()
