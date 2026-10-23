# -*- coding: utf-8 -*-
"""Очередь откликов: выборка подходящих вакансий, лимиты, экспорт писем."""
import os

from . import matching, storage
from .config import get_settings


def can_send_today(conn, daily_limit):
    """Сколько откликов ещё можно подготовить сегодня."""
    sent = storage.sent_today(conn)
    return max(0, daily_limit - sent)


def next_batch(conn, threshold=55, limit=20, only_new=True):
    """Вакансии для работы: оценка не ниже порога и ещё не отмеченные отправленными."""
    sql = """SELECT v.id, v.title, v.company, v.url, v.work_format, v.experience,
                    v.salary_from, v.salary_to, v.currency, s.score, s.verdict, s.reasons,
                    l.text AS letter,
                    (SELECT COUNT(*) FROM applications a WHERE a.vacancy_id = v.id) AS applied
             FROM vacancies v
             LEFT JOIN scores s ON s.vacancy_id = v.id
             LEFT JOIN letters l ON l.vacancy_id = v.id
             WHERE COALESCE(s.score, 0) >= ?"""
    if only_new:
        sql += " AND (SELECT COUNT(*) FROM applications a WHERE a.vacancy_id = v.id) = 0"
    sql += " ORDER BY COALESCE(s.score, 0) DESC, v.created_at DESC LIMIT ?"
    rows = conn.execute(sql, (threshold, limit)).fetchall()
    return _drop_excluded(rows)


def _drop_excluded(rows):
    """Убирает вакансии компаний из стоп-листа профиля (exclude_companies)."""
    excluded = [matching.normalize(name)
                for name in (get_settings().profile.get("exclude_companies") or []) if name]
    if not excluded:
        return rows
    return [r for r in rows
            if not any(word in matching.normalize(r["company"]) for word in excluded)]


def export_letter(conn, vacancy_id, out_dir):
    """Пишет письмо вакансии в .txt и возвращает путь."""
    row = storage.vacancy_with_letter(conn, vacancy_id)
    if not row or not row["letter"]:
        return None
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s.txt" % vacancy_id)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(row["letter"].replace("\r\n", "\n").replace("\n", "\r\n"))
    return path


def mark_sent(conn, vacancy_id, status="отправлено вручную", note=""):
    storage.add_application(conn, vacancy_id, status=status, note=note)
    storage.log_event(conn, "sent", {"vacancy_id": vacancy_id, "status": status})
    return True


def skip(conn, vacancy_id, reason="пропущено"):
    storage.add_application(conn, vacancy_id, status=reason)
    storage.log_event(conn, "skip", {"vacancy_id": vacancy_id, "reason": reason})
    return True
