# -*- coding: utf-8 -*-
"""Тесты сопоставления вакансии с профилем-резюме (hhassist.matching)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import profile, sample_vacancy  # noqa: E402

from hhassist import matching  # noqa: E402


class BuildQueriesTest(unittest.TestCase):
    def test_queries_from_profile(self):
        queries = matching.build_queries(profile())
        self.assertTrue(queries)
        self.assertLessEqual(len(queries), 5)
        self.assertIn("AI-инженер", queries)

    def test_fallback_to_must_any(self):
        self.assertEqual(matching.build_queries({"must_any": ["ai", "python"]}), ["ai", "python"])


class TitleMatchTest(unittest.TestCase):
    def test_target_role_bonus(self):
        score, reasons = matching.title_match(sample_vacancy(title="Промпт-инженер"), profile())
        self.assertEqual(score, matching.ROLE_BONUS)
        self.assertTrue(reasons)

    def test_avoid_in_title_penalty(self):
        score, _ = matching.title_match(sample_vacancy(title="1С-разработчик"), profile())
        self.assertEqual(score, -matching.ROLE_PENALTY)


class StackMatchTest(unittest.TestCase):
    def test_hits_are_positive_and_capped(self):
        score, reasons = matching.stack_match(sample_vacancy(), profile())
        self.assertGreater(score, 0)
        self.assertLessEqual(score, matching.STACK_BONUS_MAX)
        self.assertTrue(any("стек" in r for r in reasons))

    def test_no_hits_penalty(self):
        v = sample_vacancy(title="Инженер-схемотехник", description="Печатные платы, Altium Designer.",
                           skills=["Altium Designer"])
        score, _ = matching.stack_match(v, profile())
        self.assertEqual(score, -matching.STACK_PENALTY)


class SeniorityTest(unittest.TestCase):
    def test_junior_penalty(self):
        score, _ = matching.seniority_penalty(sample_vacancy(title="Junior AI-инженер"), profile())
        self.assertLess(score, 0)

    def test_overlevel_penalty(self):
        score, _ = matching.seniority_penalty(sample_vacancy(title="Руководитель AI-направления"), profile())
        self.assertLess(score, 0)

    def test_middle_is_neutral(self):
        score, _ = matching.seniority_penalty(sample_vacancy(), profile())
        self.assertEqual(score, 0)


class ExcludedCompanyTest(unittest.TestCase):
    def test_excluded(self):
        p = profile()
        p["exclude_companies"] = ["Ромашка"]
        self.assertTrue(matching.is_excluded_company(sample_vacancy(company="ООО Ромашка"), p))
        self.assertFalse(matching.is_excluded_company(sample_vacancy(company="Другая"), p))


class ExplainTest(unittest.TestCase):
    def test_explain_mentions_matches(self):
        self.assertIn("совпало", matching.explain(sample_vacancy(), profile()))

    def test_profile_brief_has_sections(self):
        brief = matching.profile_brief(profile())
        self.assertIn("Целевые роли", brief)
        self.assertIn("Опыт работы", brief)
        self.assertIn("Ключевой стек", brief)


if __name__ == "__main__":
    unittest.main()
