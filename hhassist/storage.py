# -*- coding: utf-8 -*-
"""Хранилище на SQLite: вакансии, оценки, письма, отклики, события."""
import json
import os
import sqlite3
from datetime import datetime, date

from .config import DATA_DIR, ensure_dirs

DB_PATH = os.path.join(DATA_DIR, "hhassist.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vacancies (
    id TEXT PRIMARY KEY,
    title TEXT,
    company TEXT,
    url TEXT,
    salary_from INTEGER,
    salary_to INTEGER,
    currency TEXT,
    work_format TEXT,
    experience TEXT,
    published_at TEXT,
    description TEXT,
    skills TEXT,
    source TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS scores (
    vacancy_id TEXT PRIMARY KEY,
    score INTEGER,
    verdict TEXT,
    reasons TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS letters (
    vacancy_id TEXT PRIMARY KEY,
    text TEXT,
    model TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id TEXT,
    status TEXT,
    sent_at TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    kind TEXT,
    payload TEXT
);
"""


def connect():
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now():
    return datetime.now().isoformat(timespec="seconds")


def upsert_vacancy(conn, v, commit=True):
    conn.execute(
        """INSERT INTO vacancies (id, title, company, url, salary_from, salary_to, currency,
               work_format, experience, published_at, description, skills, source, created_at)
           VALUES (:id, :title, :company, :url, :salary_from, :salary_to, :currency,
               :work_format, :experience, :published_at, :description, :skills, :source, :created_at)
           ON CONFLICT(id) DO UPDATE SET
               title=excluded.title, company=excluded.company, url=excluded.url,
               salary_from=excluded.salary_from, salary_to=excluded.salary_to,
               currency=excluded.currency, work_format=excluded.work_format,
               experience=excluded.experience, published_at=excluded.published_at,
               description=excluded.description, skills=excluded.skills""",
        {
            "id": v.get("id"),
            "title": v.get("title", ""),
            "company": v.get("company", ""),
            "url": v.get("url", ""),
            "salary_from": v.get("salary_from"),
            "salary_to": v.get("salary_to"),
            "currency": v.get("currency", ""),
            "work_format": v.get("work_format", ""),
            "experience": v.get("experience", ""),
            "published_at": v.get("published_at", ""),
            "description": v.get("description", ""),
            "skills": json.dumps(v.get("skills", []), ensure_ascii=False),
            "source": v.get("source", "html"),
            "created_at": now(),
        },
    )
    if commit:
        conn.commit()


def save_score(conn, vacancy_id, score, verdict, reasons, commit=True):
    conn.execute(
        """INSERT INTO scores (vacancy_id, score, verdict, reasons, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(vacancy_id) DO UPDATE SET
               score=excluded.score, verdict=excluded.verdict,
               reasons=excluded.reasons, updated_at=excluded.updated_at""",
        (vacancy_id, int(score), verdict, json.dumps(reasons, ensure_ascii=False), now()),
    )
    if commit:
        conn.commit()


def save_letter(conn, vacancy_id, text, model="", commit=True):
    conn.execute(
        """INSERT INTO letters (vacancy_id, text, model, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(vacancy_id) DO UPDATE SET
               text=excluded.text, model=excluded.model, updated_at=excluded.updated_at""",
        (vacancy_id, text, model, now()),
    )
    if commit:
        conn.commit()


def add_application(conn, vacancy_id, status="готово к отправке", note="", commit=True):
    conn.execute(
        "INSERT INTO applications (vacancy_id, status, sent_at, note) VALUES (?, ?, ?, ?)",
        (vacancy_id, status, now(), note),
    )
    if commit:
        conn.commit()


def log_event(conn, kind, payload=None, commit=True):
    conn.execute(
        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
        (now(), kind, json.dumps(payload or {}, ensure_ascii=False)),
    )
    if commit:
        conn.commit()


def pending_vacancies(conn, min_score=None, limit=50):
    sql = """SELECT v.*, s.score, s.verdict, s.reasons,
                    (SELECT COUNT(*) FROM applications a WHERE a.vacancy_id = v.id) AS applied
             FROM vacancies v
             LEFT JOIN scores s ON s.vacancy_id = v.id
             WHERE (s.score IS NULL OR s.score >= ?)
             ORDER BY COALESCE(s.score, 0) DESC, v.created_at DESC
             LIMIT ?"""
    return conn.execute(sql, (min_score if min_score is not None else -1, limit)).fetchall()


def vacancy_with_letter(conn, vacancy_id):
    return conn.execute(
        """SELECT v.*, s.score, s.verdict, s.reasons, l.text AS letter
           FROM vacancies v
           LEFT JOIN scores s ON s.vacancy_id = v.id
           LEFT JOIN letters l ON l.vacancy_id = v.id
           WHERE v.id = ?""",
        (vacancy_id,),
    ).fetchone()


def sent_today(conn):
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM applications WHERE substr(sent_at, 1, 10) = ?", (today,)
    ).fetchone()
    return row["c"] if row else 0


def stats(conn):
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "vacancies": q("SELECT COUNT(*) FROM vacancies"),
        "scored": q("SELECT COUNT(*) FROM scores"),
        "letters": q("SELECT COUNT(*) FROM letters"),
        "applications": q("SELECT COUNT(*) FROM applications"),
        "sent_today": sent_today(conn),
        "high_score": q("SELECT COUNT(*) FROM scores WHERE score >= 70"),
    }
