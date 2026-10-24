# -*- coding: utf-8 -*-
"""Тесты общих пакетных действий (`hhassist/jobs.py`).

Один код обслуживает и CLI, и веб-интерфейс, поэтому проверяем его напрямую:
оценка только новых вакансий, письма по порогу, сбор по всем запросам и
транзакции (батч-коммит вместо коммита на каждую запись).
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import sample_vacancy  # noqa: E402,F401

from hhassist import collector, jobs, storage  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402


def settings(**over):
    """Офлайн-настройки: без ключа LLM скоринг считает только правила."""
    data = {
        "profile": dict(DEFAULT_PROFILE),
        "collect_limit": 20,
        "collect_pages": 1,
        "collect_queries": 0,
    }
    data.update(over)
    return Settings(**data)


class JobsBase(unittest.TestCase):
    """Временная база: тесты не трогают data/ и не ходят в сеть."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(storage, "DB_PATH",
                                    os.path.join(self.tmp.name, "test.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = storage.connect()
        self.addCleanup(self.conn.close)

    def count(self, table):
        return self.conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]

    def save(self, *ids):
        for vid in ids:
            storage.upsert_vacancy(self.conn, sample_vacancy(id=vid))


class ScorePendingTest(JobsBase):
    def test_scores_only_new_vacancies(self):
        self.save("1", "2", "3")
        storage.save_score(self.conn, "1", 77, "оценено вручную", [])
        done, total = jobs.score_pending(self.conn, settings())
        self.assertEqual((done, total), (2, 3))
        row = self.conn.execute(
            "SELECT score, verdict FROM scores WHERE vacancy_id = '1'").fetchone()
        self.assertEqual((row["score"], row["verdict"]), (77, "оценено вручную"))

    def test_force_rescores_every_vacancy(self):
        self.save("1", "2")
        storage.save_score(self.conn, "1", 77, "оценено вручную", [])
        done, total = jobs.score_pending(self.conn, settings(), force=True)
        self.assertEqual((done, total), (2, 2))
        row = self.conn.execute("SELECT score FROM scores WHERE vacancy_id = '1'").fetchone()
        self.assertNotEqual(row["score"], 77, "оценка должна быть пересчитана правилами")

    def test_skills_from_json_are_parsed(self):
        self.save("1")
        seen = []
        jobs.score_pending(self.conn, settings(), on_item=lambda v, res: seen.append((v, res)))
        self.assertEqual(seen[0][0]["skills"], ["Python", "LLM", "RAG", "FastAPI"])
        self.assertIn("score", seen[0][1])

    def test_empty_base_gives_zero(self):
        self.assertEqual(jobs.score_pending(self.conn, settings()), (0, 0))


class CollectQueriesTest(JobsBase):
    def fake_collect(self, calls, per_query=2):
        def run(query, **kwargs):
            calls.append(dict(kwargs, query=query))
            items = [sample_vacancy(id="%s-%d" % (query, i)) for i in range(per_query)]
            info = {"source": "api", "fallback": False, "reason": "",
                    "found": 10, "taken": len(items), "errors": []}
            return items, info
        return run

    def test_saves_every_query_result(self):
        calls = []
        with mock.patch.object(collector, "collect", self.fake_collect(calls)):
            saved, found, source, notes = jobs.collect_queries(self.conn, ["a", "b"], settings())
        self.assertEqual([c["query"] for c in calls], ["a", "b"])
        self.assertEqual((saved, found, source, notes), (4, 20, "api", []))
        self.assertEqual(self.count("vacancies"), 4)

    def test_limit_and_pages_from_settings(self):
        calls = []
        with mock.patch.object(collector, "collect", self.fake_collect(calls)):
            jobs.collect_queries(self.conn, ["a"], settings(collect_limit=7, collect_pages=3))
        self.assertEqual((calls[0]["limit"], calls[0]["pages"]), (7, 3))

    def test_failed_query_is_logged_and_skipped(self):
        def run(query, **kwargs):
            if query == "b":
                raise RuntimeError("HTTP 403")
            return ([sample_vacancy(id="ok-1")],
                    {"source": "html", "fallback": False, "reason": "",
                     "found": 1, "taken": 1, "errors": []})

        with mock.patch.object(collector, "collect", run):
            saved, found, _source, notes = jobs.collect_queries(
                self.conn, ["a", "b", "c"], settings())
        self.assertEqual((saved, found), (2, 2))
        self.assertEqual(len(notes), 1)
        self.assertIn("поиск не удался", notes[0])
        kinds = [row["kind"] for row in self.conn.execute("SELECT kind FROM events")]
        self.assertIn("collect_error", kinds)


class LettersTest(JobsBase):
    def test_takes_every_vacancy_without_letter(self):
        """Письма делаются для всех вакансий без письма, балл больше не фильтрует."""
        self.save("1", "2", "3")
        storage.save_score(self.conn, "1", 90, "откликаться", [])
        storage.save_score(self.conn, "2", 10, "не откликаться", [])
        storage.save_score(self.conn, "3", 56, "условно", [])
        self.assertEqual(jobs.make_letters(self.conn, settings()), 3)
        ids = sorted(row["vacancy_id"]
                     for row in self.conn.execute("SELECT vacancy_id FROM letters"))
        self.assertEqual(ids, ["1", "2", "3"])

    def test_letter_order_follows_score(self):
        """Порядок — по убыванию балла: первым идёт лучшая вакансия."""
        self.save("1", "2")
        storage.save_score(self.conn, "1", 20, "не откликаться", [])
        storage.save_score(self.conn, "2", 95, "откликаться", [])
        self.assertEqual([r["id"] for r in jobs.letter_vacancies(self.conn)], ["2", "1"])

    def test_skips_existing_letters_without_force(self):
        self.save("1")
        storage.save_score(self.conn, "1", 90, "откликаться", [])
        storage.save_letter(self.conn, "1", "старое письмо", "manual")
        self.assertEqual(jobs.make_letters(self.conn, settings()), 0)
        self.assertEqual(jobs.make_letters(self.conn, settings(), force=True), 1)
        row = self.conn.execute("SELECT text FROM letters WHERE vacancy_id = '1'").fetchone()
        self.assertNotEqual(row["text"], "старое письмо")

    def test_limit_caps_the_batch(self):
        self.save(*[str(i) for i in range(1, 6)])
        for i in range(1, 6):
            storage.save_score(self.conn, str(i), 90, "откликаться", [])
        self.assertEqual(jobs.make_letters(self.conn, settings(), limit=2), 2)


class TransactionTest(JobsBase):
    def test_upsert_without_commit_leaves_no_trace(self):
        storage.upsert_vacancy(self.conn, sample_vacancy(id="9"), commit=False)
        other = storage.connect()
        self.addCleanup(other.close)
        self.assertEqual(other.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0], 0)
        self.conn.commit()
        self.assertEqual(other.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0], 1)

    def test_score_batch_commits_once(self):
        self.save("1", "2", "3")
        with mock.patch.object(storage, "save_score", wraps=storage.save_score) as spy:
            jobs.score_pending(self.conn, settings())
        self.assertEqual(spy.call_count, 3)
        self.assertTrue(all(call.kwargs.get("commit") is False
                            for call in spy.call_args_list))
        self.assertEqual(self.count("scores"), 3)


if __name__ == "__main__":
    unittest.main()
