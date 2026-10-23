# -*- coding: utf-8 -*-
"""Тесты аналитики воронки (`hhassist/analytics.py`) и журнала статусов отклика.

Проверяем то, что видит пользователь на странице «Воронка» и в `cli.py funnel`:
сколько найдено, оценено, написано писем, отправлено и сколько пришло ответов;
конверсии между шагами; разрезы по баллам, форматам и компаниям; CSV-выгрузку.
Сеть и настоящая база не используются — SQLite подменяется временным файлом,
даты отправки задаются через `storage.set_status(..., at=...)`.
"""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import sample_vacancy  # noqa: E402

from hhassist import analytics, queue, storage  # noqa: E402


def days_ago(days):
    """Метка времени «N дней назад» в формате журнала."""
    return (date.today() - timedelta(days=days)).isoformat() + "T10:00:00"


class StatusKindTest(unittest.TestCase):
    """Журнал хранит подписи («ответ»), аналитика работает с кодами («reply»)."""

    def test_labels_map_to_kinds(self):
        self.assertEqual(storage.status_kind("отправлено вручную"), "sent")
        self.assertEqual(storage.status_kind("пропущено"), "skip")
        self.assertEqual(storage.status_kind("ответ"), "reply")
        self.assertEqual(storage.status_kind("приглашение"), "invite")
        self.assertEqual(storage.status_kind("отказ"), "reject")

    def test_legacy_and_free_text_aliases(self):
        self.assertEqual(storage.status_kind(" Приглашение на собеседование "), "invite")
        self.assertEqual(storage.status_kind("ответ работодателя"), "reply")

    def test_unknown_status_is_empty(self):
        self.assertEqual(storage.status_kind("телепорт"), "")
        self.assertEqual(storage.status_kind(""), "")
        self.assertEqual(storage.status_kind(None), "")

    def test_statuses_for(self):
        self.assertEqual(storage.statuses_for(("sent",)), ("отправлено вручную",))
        self.assertEqual(storage.statuses_for("reply"), ("ответ",))
        self.assertEqual(storage.statuses_for(("reply", "invite")), ("ответ", "приглашение"))
        self.assertEqual(storage.statuses_for(("нет такого",)), ())

    def test_kind_of_accepts_code_and_label(self):
        self.assertEqual(queue.kind_of("invite"), "invite")
        self.assertEqual(queue.kind_of("приглашение"), "invite")
        self.assertEqual(queue.kind_of(""), "")


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
        queue.set_status(self.conn, "1", "invite", note="собеседование 27.10")
        self.assertEqual(self.count("applications"), 2)
        latest = storage.latest_application(self.conn, "1")
        self.assertEqual(storage.status_kind(latest["status"]), "invite")
        self.assertEqual(latest["note"], "собеседование 27.10")
        self.assertEqual(storage.latest_kinds(self.conn), {"1": "invite"})

    def test_russian_label_is_accepted(self):
        self.vacancy("1")
        self.assertEqual(queue.set_status(self.conn, "1", "ответ"), storage.STATUS_REPLY)

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

    def test_stats_include_responses(self):
        self.vacancy("1", score=90, letter=True)
        queue.set_status(self.conn, "1", "sent")
        queue.set_status(self.conn, "1", "reject")
        st = storage.stats(self.conn)
        self.assertEqual((st["sent"], st["replied"], st["invited"]), (1, 1, 0))


class FunnelTest(Base):
    """Числа воронки на управляемом наборе вакансий."""

    def seed(self):
        """1 — отправлено + приглашение; 2 — отправлено 20 дней назад, тишина;
        3 — письмо готово, не отправлено; 4 — оценено без письма; 5 — без оценки;
        6 — письмо + пропущено."""
        self.vacancy("1", score=90, letter=True)
        queue.set_status(self.conn, "1", "sent")
        queue.set_status(self.conn, "1", "invite")
        self.vacancy("2", score=70, letter=True)
        storage.set_status(self.conn, "2", "sent", at=days_ago(20))
        self.vacancy("3", score=60, letter=True)
        self.vacancy("4", score=30)
        self.vacancy("5")
        self.vacancy("6", score=65, letter=True)
        queue.skip(self.conn, "6")

    def test_funnel_numbers(self):
        self.seed()
        f = analytics.funnel(self.conn, wait_days=14)
        self.assertEqual(f["found"], 6)
        self.assertEqual(f["scored"], 5)
        self.assertEqual(f["letters"], 4)
        self.assertEqual(f["ready"], 1)
        self.assertEqual(f["sent"], 2)
        self.assertEqual(f["replied"], 1)
        self.assertEqual(f["invited"], 1)
        self.assertEqual(f["rejected"], 0)
        self.assertEqual(f["skipped"], 1)
        self.assertEqual(f["no_answer"], 1)

    def test_no_answer_depends_on_window(self):
        self.seed()
        self.assertEqual(analytics.no_answer(self.conn, wait_days=365), 0)
        self.assertEqual(analytics.no_answer(self.conn, wait_days=0), 1)

    def test_empty_base_gives_zeros(self):
        f = analytics.funnel(self.conn)
        self.assertEqual((f["found"], f["sent"], f["replied"], f["ready"]), (0, 0, 0, 0))
        self.assertEqual(analytics.no_answer(self.conn), 0)

    def test_awaiting_reply_lists_old_sends(self):
        self.seed()
        rows = analytics.awaiting_reply(self.conn, wait_days=14)
        self.assertEqual([r["id"] for r in rows], ["2"])


class ConversionTest(unittest.TestCase):
    """Конверсии между шагами: проценты и защита от деления на ноль."""

    def test_percentages(self):
        f = {"found": 10, "scored": 8, "letters": 4, "sent": 2, "replied": 1, "invited": 1}
        c = analytics.conversions(f)
        self.assertEqual(c["scored"], 80.0)
        self.assertEqual(c["letters"], 50.0)
        self.assertEqual(c["sent"], 50.0)
        self.assertEqual(c["replied"], 50.0)
        self.assertEqual(c["invited"], 100.0)

    def test_zero_denominators(self):
        self.assertTrue(all(v == 0.0 for v in analytics.conversions({}).values()))

    def test_percent_helper(self):
        self.assertEqual(analytics.percent(1, 4), 25.0)
        self.assertEqual(analytics.percent(1, 0), 0.0)
        self.assertEqual(analytics.percent(2, 3), 66.7)


class BreakdownTest(Base):
    """Разрезы: балл → ответы, формат работы, компании."""

    def test_score_bands_cover_all_and_zero_fill(self):
        self.vacancy("1", score=95, letter=True)
        self.vacancy("2", score=72, letter=True)
        self.vacancy("3", score=58, letter=True)
        self.vacancy("4", score=45)
        self.vacancy("5")  # без оценки в разрезе не участвует
        queue.set_status(self.conn, "1", "sent")
        queue.set_status(self.conn, "1", "invite")
        bands = analytics.by_score_band(self.conn)
        self.assertEqual([b["title"] for b in bands],
                         ["0–39", "40–54", "55–69", "70–84", "85–100"])
        by_title = {b["title"]: b for b in bands}
        self.assertEqual(by_title["85–100"]["scored"], 1)
        self.assertEqual(by_title["85–100"]["sent"], 1)
        self.assertEqual(by_title["85–100"]["replied"], 1)
        self.assertEqual(by_title["85–100"]["reply_rate"], 100.0)
        self.assertEqual(by_title["0–39"]["scored"], 0)
        self.assertEqual(by_title["0–39"]["reply_rate"], 0.0)

    def test_by_format_groups_and_marks_missing(self):
        self.vacancy("1", score=80, letter=True, work_format="удалённо")
        self.vacancy("2", score=70, letter=True, work_format="удалённо")
        self.vacancy("3", score=60, work_format="гибрид")
        self.vacancy("4", score=50, work_format="")
        queue.set_status(self.conn, "1", "sent")
        groups = {g["title"]: g for g in analytics.by_format(self.conn)}
        self.assertEqual(groups["удалённо"]["scored"], 2)
        self.assertEqual(groups["удалённо"]["sent"], 1)
        self.assertIn("не указан", groups)

    def test_by_company_sorted_by_sent_and_limited(self):
        self.vacancy("1", score=80, letter=True, company="Альфа")
        self.vacancy("2", score=70, letter=True, company="Альфа")
        self.vacancy("3", score=60, letter=True, company="Бета")
        queue.set_status(self.conn, "1", "sent")
        queue.set_status(self.conn, "2", "sent")
        groups = analytics.by_company(self.conn)
        self.assertEqual(groups[0]["title"], "Альфа")
        self.assertEqual((groups[0]["sent"], groups[0]["scored"]), (2, 2))
        self.assertEqual(len(analytics.by_company(self.conn, limit=1)), 1)


class CsvTest(Base):
    """Выгрузка `export --csv`: Excel-совместимый файл со статусом отклика."""

    def test_csv_has_bom_header_and_status(self):
        self.vacancy("1", score=90, letter=True, company="Альфа")
        queue.set_status(self.conn, "1", "sent")
        queue.set_status(self.conn, "1", "invite", note="созвон 27.10")
        path = os.path.join(self.tmp.name, "out", "vacancies.csv")
        self.assertEqual(analytics.write_csv(self.conn, path), 1)
        with open(path, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "BOM нужен, чтобы Excel понял UTF-8")
        text = raw.decode("utf-8-sig")
        self.assertIn("балл;вердикт", text)
        self.assertIn("приглашение", text)
        self.assertIn("созвон 27.10", text)
        self.assertEqual(len(text.strip().split("\r\n")), 2)

    def test_csv_rows_include_every_vacancy(self):
        self.vacancy("1", score=90, letter=True)
        self.vacancy("2")
        rows = analytics.csv_rows(self.conn)
        self.assertEqual(sorted(r[0] for r in rows), ["1", "2"])
        empty = [r for r in rows if r[0] == "2"][0]
        self.assertEqual(empty[12], 0, "без письма — 0 знаков")
        self.assertEqual(empty[13], "", "без отклика — пустой статус")

    def test_csv_on_empty_base_has_only_header(self):
        path = os.path.join(self.tmp.name, "empty.csv")
        self.assertEqual(analytics.write_csv(self.conn, path), 0)
        with open(path, encoding="utf-8-sig") as f:
            lines = [line for line in f.read().splitlines() if line]
        self.assertEqual(lines, [";".join(analytics.CSV_HEADER)])


class ReportTest(Base):
    """Текстовый отчёт `cli.py funnel`."""

    def test_report_contains_funnel_numbers(self):
        self.vacancy("1", score=90, letter=True)
        queue.set_status(self.conn, "1", "sent")
        text = analytics.format_report(self.conn, wait_days=7)
        self.assertIn("воронка откликов", text)
        self.assertIn("найдено", text)
        self.assertIn("готово к отправке", text)
        self.assertIn("без ответа > 7 дн.", text)

    def test_report_on_empty_base(self):
        text = analytics.format_report(self.conn)
        self.assertIn("конверсии: отправка -> ответ 0.0%", text)


if __name__ == "__main__":
    unittest.main()

