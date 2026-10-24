# -*- coding: utf-8 -*-
"""Хранилище на SQLite: вакансии, оценки, письма, отклики, события."""
import json
import os
import sqlite3
from datetime import datetime, date

from .config import DATA_DIR, ensure_dirs

DB_PATH = os.path.join(DATA_DIR, "hhassist.sqlite3")

# Журнал откликов. Каждая смена статуса — новая строка в applications
# (история сохраняется), текущий статус — последняя по id запись вакансии.
STATUS_READY = "готово к отправке"
STATUS_SENT = "отправлено вручную"
STATUS_SKIPPED = "пропущено"
STATUS_REPLY = "ответ"
STATUS_INVITE = "приглашение"
STATUS_REJECT = "отказ"

# Коды статусов: в коде и в аналитике работаем с ними, в журнал пишем подписи.
KIND_STATUSES = {
    "ready": (STATUS_READY,),
    "sent": (STATUS_SENT,),
    "skip": (STATUS_SKIPPED,),
    "reply": (STATUS_REPLY,),
    "invite": (STATUS_INVITE,),
    "reject": (STATUS_REJECT,),
}

KIND_TITLES = {
    "ready": "готово к отправке",
    "sent": "отправлено",
    "skip": "пропущено",
    "reply": "ответ",
    "invite": "приглашение",
    "reject": "отказ",
}

# Прочитать старые записи журнала и свободный ввод из карточки/CLI.
STATUS_ALIASES = {label.lower(): kind for kind, labels in KIND_STATUSES.items()
                  for label in labels}
STATUS_ALIASES.update({
    "готово": "ready",
    "пропуск": "skip",
    "ответ получен": "reply",
    "ответ работодателя": "reply",
    "приглашение на собеседование": "invite",
    "отказ работодателя": "reject",
})

# Отправленные отклики: в этих статусах вакансия уже ушла работодателю.
SENT_KINDS = ("sent", "reply", "invite", "reject")
# Ответы работодателя (приглашение и отказ — тоже ответ).
REPLY_KINDS = ("reply", "invite", "reject")


def status_kind(status):
    """Код статуса ('sent', 'reply', …) по записи журнала; '' — если не распознан."""
    if not status:
        return ""
    return STATUS_ALIASES.get(str(status).strip().lower(), "")


def statuses_for(kinds):
    """Подписи журнала для набора кодов: ('reply', 'invite') -> ('ответ', 'приглашение')."""
    if isinstance(kinds, str):
        kinds = (kinds,)
    return tuple(label for kind in kinds for label in KIND_STATUSES.get(kind, ()))

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
    created_at TEXT,
    area TEXT DEFAULT '',
    area_id TEXT DEFAULT '',
    region TEXT DEFAULT '',
    seen_at TEXT
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
    updated_at TEXT,
    edited INTEGER DEFAULT 0,
    edited_at TEXT
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


# Сколько секунд ждать освобождения базы, прежде чем сообщить «database is locked».
LOCK_TIMEOUT = 30.0


def connect(timeout=LOCK_TIMEOUT):
    """Соединение с базой в режиме WAL и с ожиданием блокировки.

    Раньше стоял режим по умолчанию (rollback journal) и таймаут 5 с: пока один
    прогон оценки шёл с открытой транзакцией (запросы к LLM занимают минуты),
    любой второй клик падал с `database is locked`, и веб-сервер вообще не
    отправлял ответ — в браузере была пустая страница. WAL позволяет читать
    во время записи, а busy_timeout даёт писателю дождаться своей очереди.
    """
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=%d" % int(timeout * 1000))
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Колонки, добавленные после первого релиза: старые базы догоняем через ALTER.
# (CREATE TABLE IF NOT EXISTS не меняет уже созданную таблицу.)
MIGRATIONS = {
    "vacancies": (("area", "TEXT DEFAULT ''"), ("area_id", "TEXT DEFAULT ''"),
                  ("region", "TEXT DEFAULT ''"), ("seen_at", "TEXT")),
    "letters": (("edited", "INTEGER DEFAULT 0"), ("edited_at", "TEXT")),
}


def _migrate(conn):
    """Добавляет недостающие колонки в существующую базу. Идемпотентно.

    Пишет в базу только когда колонки действительно не хватает: иначе каждый
    `connect()` пытался бы открыть транзакцию и мешал соседним соединениям.
    """
    changed = False
    for table, columns in MIGRATIONS.items():
        have = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
        if not have:  # таблицы ещё нет — её создал SCHEMA
            continue
        added = False
        for name, ddl in columns:
            if name not in have:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, ddl))
                added = True
        if added and table == "vacancies":
            # старые строки не имеют seen_at — считаем, что видели их при создании
            conn.execute("UPDATE vacancies SET seen_at = created_at WHERE seen_at IS NULL")
        changed = changed or added
    if changed:
        conn.commit()


def now():
    return datetime.now().isoformat(timespec="seconds")


def upsert_vacancy(conn, v, commit=True):
    conn.execute(
        """INSERT INTO vacancies (id, title, company, url, salary_from, salary_to, currency,
               work_format, experience, published_at, description, skills, source, created_at,
               area, area_id, region, seen_at)
           VALUES (:id, :title, :company, :url, :salary_from, :salary_to, :currency,
               :work_format, :experience, :published_at, :description, :skills, :source,
               :created_at, :area, :area_id, :region, :seen_at)
           ON CONFLICT(id) DO UPDATE SET
               title=excluded.title, company=excluded.company, url=excluded.url,
               salary_from=excluded.salary_from, salary_to=excluded.salary_to,
               currency=excluded.currency, work_format=excluded.work_format,
               experience=excluded.experience, published_at=excluded.published_at,
               description=excluded.description, skills=excluded.skills,
               area=excluded.area, area_id=excluded.area_id, region=excluded.region,
               seen_at=excluded.seen_at""",
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
            "area": v.get("area", "") or "",
            "area_id": str(v.get("area_id") or ""),
            "region": v.get("region", "") or "",
            "seen_at": now(),
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


def save_letter(conn, vacancy_id, text, model="", commit=True, edited=False):
    """Пишет письмо. edited=True — текст правил человек (правка не затирается генерацией)."""
    conn.execute(
        """INSERT INTO letters (vacancy_id, text, model, updated_at, edited, edited_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(vacancy_id) DO UPDATE SET
               text=excluded.text, model=excluded.model, updated_at=excluded.updated_at,
               edited=excluded.edited, edited_at=excluded.edited_at""",
        (vacancy_id, text, model, now(), 1 if edited else 0, now() if edited else None),
    )
    if commit:
        conn.commit()


def add_application(conn, vacancy_id, status=STATUS_READY, note="", commit=True, at=None):
    """Запись в журнал отклика. `at` — время события (по умолчанию сейчас)."""
    conn.execute(
        "INSERT INTO applications (vacancy_id, status, sent_at, note) VALUES (?, ?, ?, ?)",
        (vacancy_id, status, at or now(), note),
    )
    if commit:
        conn.commit()


def set_status(conn, vacancy_id, kind, note="", commit=True, at=None):
    """Пишет в журнал подпись статуса по коду. Возвращает подпись."""
    labels = KIND_STATUSES.get(kind)
    if not labels:
        raise ValueError("неизвестный статус: %s" % kind)
    add_application(conn, vacancy_id, status=labels[0], note=note, commit=commit, at=at)
    return labels[0]


def latest_applications(conn):
    """По одной строке на вакансию — последняя запись журнала (текущий статус)."""
    return conn.execute(
        """SELECT a.id, a.vacancy_id, a.status, a.sent_at, a.note
           FROM applications a
           JOIN (SELECT vacancy_id, MAX(id) AS last_id FROM applications GROUP BY vacancy_id) m
             ON m.last_id = a.id
           ORDER BY a.id DESC"""
    ).fetchall()


def latest_application(conn, vacancy_id):
    """Последняя запись журнала по вакансии (None, если откликов не было)."""
    return conn.execute(
        """SELECT id, vacancy_id, status, sent_at, note FROM applications
           WHERE vacancy_id = ? ORDER BY id DESC LIMIT 1""",
        (vacancy_id,),
    ).fetchone()


def latest_kinds(conn):
    """{vacancy_id: код текущего статуса} — для таблиц и фильтров."""
    return {row["vacancy_id"]: status_kind(row["status"]) for row in latest_applications(conn)}


def vacancy_ids_with(conn, kinds):
    """id вакансий, у которых в журнале есть хотя бы одна запись из указанных статусов."""
    labels = statuses_for(kinds)
    if not labels:
        return set()
    sql = "SELECT DISTINCT vacancy_id FROM applications WHERE status IN (%s)" % ",".join(
        "?" * len(labels))
    return {row["vacancy_id"] for row in conn.execute(sql, labels)}


def count_vacancies_with(conn, kinds):
    """Сколько вакансий хотя бы раз попадали в указанные статусы."""
    return len(vacancy_ids_with(conn, kinds))


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
        """SELECT v.*, s.score, s.verdict, s.reasons, l.text AS letter,
                  COALESCE(l.edited, 0) AS letter_edited, l.edited_at AS letter_edited_at
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


def seen_index(conn):
    """{vacancy_id: время последнего сбора} — для инкрементального сбора."""
    return {row["id"]: row["seen_at"] or row["created_at"] or ""
            for row in conn.execute("SELECT id, seen_at, created_at FROM vacancies")}


def last_event(conn, kind):
    """Последнее событие журнала указанного вида или None."""
    row = conn.execute("SELECT ts, payload FROM events WHERE kind = ? ORDER BY id DESC LIMIT 1",
                       (kind,)).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload"] or "{}")
    except ValueError:
        payload = {}
    return {"ts": row["ts"], "payload": payload}


def score_counts(conn, threshold):
    """(оценено не ниже порога, оценено всего) — для сводки на главной странице."""
    row = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN score >= ? THEN 1 ELSE 0 END), 0) AS above,
                  COUNT(*) AS scored FROM scores""", (threshold,)).fetchone()
    return row["above"], row["scored"]


def region_counts(conn):
    """[(субъект РФ, сколько вакансий)] — верхний уровень фильтра на главной."""
    return conn.execute(
        """SELECT COALESCE(region, '') AS region, COUNT(*) AS cnt FROM vacancies
           GROUP BY COALESCE(region, '') ORDER BY cnt DESC, region"""
    ).fetchall()


def area_counts(conn, region=None):
    """[(город, сколько вакансий)] — нижний уровень фильтра.

    region=None — по всем вакансиям, иначе только по выбранному субъекту РФ.
    """
    sql = "SELECT COALESCE(area, '') AS area, COUNT(*) AS cnt FROM vacancies"
    params = ()
    if region is not None:
        sql += " WHERE COALESCE(region, '') = ?"
        params = (region,)
    sql += " GROUP BY COALESCE(area, '') ORDER BY cnt DESC, area"
    return conn.execute(sql, params).fetchall()


def sent_companies_since(conn, cutoff):
    """[(компания, дата отклика)] — отклики не раньше cutoff (ISO-дата)."""
    labels = statuses_for(SENT_KINDS)
    sql = """SELECT v.company, a.sent_at FROM applications a
             JOIN vacancies v ON v.id = a.vacancy_id
             WHERE a.status IN (%s) AND substr(a.sent_at, 1, 10) >= ?""" % ",".join("?" * len(labels))
    return conn.execute(sql, labels + (cutoff,)).fetchall()


def stats(conn):
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "vacancies": q("SELECT COUNT(*) FROM vacancies"),
        "scored": q("SELECT COUNT(*) FROM scores"),
        "letters": q("SELECT COUNT(*) FROM letters"),
        "applications": q("SELECT COUNT(*) FROM applications"),
        "sent_today": sent_today(conn),
        "high_score": q("SELECT COUNT(*) FROM scores WHERE score >= 70"),
        "sent": count_vacancies_with(conn, SENT_KINDS),
        "replied": count_vacancies_with(conn, REPLY_KINDS),
        "invited": count_vacancies_with(conn, ("invite",)),
    }
