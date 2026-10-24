# -*- coding: utf-8 -*-
"""Выгрузка вакансий в CSV для Excel (`python cli.py export --csv`).

Отдельный модуль, потому что аналитики воронки в приложении больше нет:
страницу `/funnel` и `analytics.py` убрали 24.10.2026 как лишние, а выгрузка
таблицы осталась нужной.
"""
import csv
import os

from . import storage

CSV_HEADER = ("id", "балл", "вердикт", "вакансия", "компания", "формат", "опыт",
              "зарплата от", "зарплата до", "валюта", "опубликовано", "ссылка",
              "письмо, знаков", "статус", "обновлено", "примечание")


def csv_rows(conn):
    """Строки выгрузки: вакансия + балл + письмо + текущий статус отклика."""
    sql = """SELECT v.id, v.title, v.company, v.work_format, v.experience,
                    v.salary_from, v.salary_to, v.currency, v.published_at, v.url,
                    s.score, s.verdict, LENGTH(l.text) AS letter_len,
                    a.status, a.sent_at, a.note
             FROM vacancies v
             LEFT JOIN scores s ON s.vacancy_id = v.id
             LEFT JOIN letters l ON l.vacancy_id = v.id
             LEFT JOIN (SELECT vacancy_id, MAX(id) AS last_id FROM applications
                        GROUP BY vacancy_id) m ON m.vacancy_id = v.id
             LEFT JOIN applications a ON a.id = m.last_id
             ORDER BY COALESCE(s.score, 0) DESC, v.created_at DESC"""
    rows = []
    for r in conn.execute(sql):
        kind = storage.status_kind(r["status"])
        rows.append((
            r["id"],
            r["score"] if r["score"] is not None else "",
            r["verdict"] or "",
            r["title"] or "",
            r["company"] or "",
            r["work_format"] or "",
            r["experience"] or "",
            r["salary_from"] or "",
            r["salary_to"] or "",
            r["currency"] or "",
            r["published_at"] or "",
            r["url"] or "",
            r["letter_len"] or 0,
            storage.KIND_TITLES.get(kind, r["status"] or ""),
            r["sent_at"] or "",
            r["note"] or "",
        ))
    return rows


def write_csv(conn, path):
    """Пишет CSV (разделитель «;», utf-8-sig — открывается в Excel). Возвращает строк."""
    rows = csv_rows(conn)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";", lineterminator="\r\n")
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    return len(rows)
