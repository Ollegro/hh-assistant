# -*- coding: utf-8 -*-
"""Тесты журнала статусов отклика и выгрузки в CSV.

Раньше здесь была ещё аналитика воронки (`hhassist/analytics.py`); страницу
`/funnel` и сам модуль убрали 24.10.2026 как лишние, поэтому проверяются
только статусы (`queue.set_status`, `storage.latest_application`) и
CSV-выгрузка (`hhassist/csv_export.py`, `cli.py export --csv`).
Сеть и настоящая база не используются — SQLite подменяется временным файлом.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import sample_vacancy  # noqa: E402

from hhassist import csv_export, queue, storage  # noqa: E402


class StatusKindTest(unittest.TestCase):
    """Журнал хранит подписи («пропущено»), код работает с кодами («skip»).

    Статусов ответа работодателя («ответ / приглашение / отказ») в приложении нет:
    ответы не отслеживаются, поэтому и статистики по ним не ведётся.
    """

    def test_labels_map_to_kinds(self):
        self.assertEqual(storage.status_kind("отправлено вручную"), "sent")
        self.assertEqual(storage.status_kind("пропущено"), "skip")

    def test_reply_statuses_are_gone(self):
        for label in ("ответ", "приглашение", "отказ",
                      "приглашение на собеседование", "ответ работодателя"):
            self.assertEqual(storage.status_kind(label), "", label)
        for kind in ("reply", "invite", "reject"):
            self.assertNotIn(kind, storage.KIND_STATUSES)
        self.assertFalse(hasattr(storage, "REPLY_KINDS"))

    def test_unknown_status_is_empty(self):
        self.assertEqual(storage.status_kind("телепорт"), "")
        self.assertEqual(storage.status_kind(""), "")
        self.assertEqual(storage.status_kind(None), "")

    def test_statuses_for(self):
        self.assertEqual(storage.statuses_for(("sent",)), ("отправлено вручную",))
        self.assertEqual(storage.statuses_for("skip"), ("пропущено",))
        self.assertEqual(storage.statuses_for(("нет такого",)), ())

    def test_kind_of_accepts_code_and_label(self):
        self.assertEqual(queue.kind_of("skip"), "skip")
        self.assertEqual(queue.kind_of("пропущено"), "skip")
        self.assertEqual(queue.kind_of(""), "")
        self.assertEqual(queue.kind_of("приглашение"), "")


class Base(unittest.TestCase):
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

    def vacancy(self, vid, score=None, letter=False, **over):
        storage.upsert_vacancy(self.conn, sample_vacancy(id=vid, **over))
        if score is not None:
            storage.save_score(self.conn, vid, score, "тест", [])
        if letter:
            storage.save_letter(self.conn, vid, "письмо %s" % vid, "manual")

    def count(self, table):
        return self.conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]



class JournalTest(Base):
    """Журнал откликов: история сохраняется, текущий статус — последняя запись."""

    def test_set_status_writes_journal_and_event(self):
        self.vacancy("1")
        status = queue.set_status(self.conn, "1", "sent")
        self.assertEqual(status, storage.STATUS_SENT)
        self.assertEqual(self.count("applications"), 1)
        kinds = [row["kind"] for row in self.conn.execute("SELECT kind FROM events")]
        self.assertEqual(kinds, ["status"])

    def test_history_is_kept_and_latest_wins(self):
        self.vacancy("1")
        queue.set_status(self.conn, "1", "sent")
        queue.set_status(self.conn, "1", "skip", note="не мой стек")
        self.assertEqual(self.count("applications"), 2)
        latest = storage.latest_application(self.conn, "1")
        self.assertEqual(storage.status_kind(latest["status"]), "skip")
        self.assertEqual(latest["note"], "не мой стек")
        self.assertEqual(storage.latest_kinds(self.conn), {"1": "skip"})

    def test_russian_label_is_accepted(self):
        self.vacancy("1")
        self.assertEqual(queue.set_status(self.conn, "1", "отправлено вручную"),
                         storage.STATUS_SENT)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            queue.set_status(self.conn, "1", "телепорт")

    def test_skip_is_a_status(self):
        self.vacancy("1")
        queue.skip(self.conn, "1", "не мой стек")
        latest = storage.latest_application(self.conn, "1")
        self.assertEqual(storage.status_kind(latest["status"]), "skip")
        self.assertEqual(latest["note"], "не мой стек")

    def test_mark_sent_counts_for_daily_limit(self):
        self.vacancy("1")
        queue.mark_sent(self.conn, "1")
        self.assertEqual(storage.sent_today(self.conn), 1)

    def test_stats_have_no_response_counters(self):
        """Статистики по ответам работодателя нет — полей replied/invited тоже."""
        self.vacancy("1", score=90, letter=True)
        queue.mark_sent(self.conn, "1")
        st = storage.stats(self.conn)
        self.assertEqual(st["sent"], 1)
        self.assertNotIn("replied", st)
        self.assertNotIn("invited", st)


class CsvTest(Base):
    """Выгрузка `export --csv`: Excel-совместимый файл со статусом отклика."""

    def test_csv_has_bom_header_and_status(self):
        self.vacancy("1", score=90, letter=True, company="Альфа")
        queue.set_status(self.conn, "1", "sent")
        queue.set_status(self.conn, "1", "skip", note="не мой стек")
        path = os.path.join(self.tmp.name, "out", "vacancies.csv")
        self.assertEqual(csv_export.write_csv(self.conn, path), 1)
        with open(path, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "BOM нужен, чтобы Excel понял UTF-8")
        text = raw.decode("utf-8-sig")
        self.assertIn("балл;вердикт", text)
        self.assertIn("пропущено", text)
        self.assertIn("не мой стек", text)
        self.assertEqual(len(text.strip().split("\r\n")), 2)

    def test_csv_rows_include_every_vacancy(self):
        self.vacancy("1", score=90, letter=True)
        self.vacancy("2")
        rows = csv_export.csv_rows(self.conn)
        self.assertEqual(sorted(r[0] for r in rows), ["1", "2"])
        empty = [r for r in rows if r[0] == "2"][0]
        self.assertEqual(empty[12], 0, "без письма — 0 знаков")
        self.assertEqual(empty[13], "", "без отклика — пустой статус")

    def test_csv_on_empty_base_has_only_header(self):
        path = os.path.join(self.tmp.name, "empty.csv")
        self.assertEqual(csv_export.write_csv(self.conn, path), 0)
        with open(path, encoding="utf-8-sig") as f:
            lines = [line for line in f.read().splitlines() if line]
        self.assertEqual(lines, [";".join(csv_export.CSV_HEADER)])


if __name__ == "__main__":
    unittest.main()
