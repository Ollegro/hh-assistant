# -*- coding: utf-8 -*-
"""Аккаунты пользователей, подписки и серверные сессии (control.sqlite3).

Фундамент мультипользовательского режима: пользователи, пароли и сессии хранятся
отдельно от рабочей базы вакансий. Вход в веб пока прежний (пара из `.env`).
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta

from .config import DATA_DIR
from .web_sessions import COOKIE, token_from_cookie

DB_PATH = os.path.join(DATA_DIR, "control.sqlite3")
SESSION_TTL = 12 * 60 * 60
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
STATUSES = ("trial", "active", "blocked")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'trial',
    is_admin INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    plan_code TEXT NOT NULL DEFAULT 'trial',
    paid_until TEXT,
    created_at TEXT NOT NULL,
    last_login TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip TEXT DEFAULT '',
    user_agent TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS plans (
    code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    price_rub INTEGER NOT NULL DEFAULT 0,
    period_days INTEGER NOT NULL DEFAULT 30,
    limits_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_code TEXT NOT NULL,
    started_at TEXT NOT NULL,
    paid_until TEXT NOT NULL,
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount_rub INTEGER NOT NULL,
    paid_at TEXT NOT NULL,
    period_days INTEGER NOT NULL,
    method TEXT DEFAULT '',
    document TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_by INTEGER
);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    counters_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor_id INTEGER,
    user_id INTEGER,
    kind TEXT NOT NULL,
    payload TEXT DEFAULT ''
);
"""

DEFAULT_PLANS = (
    ("trial", "Пробный", 0, 1,
     {"vacancies": 500, "collect_per_day": 20, "letters_per_day": 20, "llm_per_month": 50}),
    ("basic", "Базовый", 990, 30,
     {"vacancies": 500, "collect_per_day": 50, "letters_per_day": 50, "llm_per_month": 500}),
    ("pro", "Про", 1990, 30,
     {"vacancies": 2000, "collect_per_day": 100, "letters_per_day": 100, "llm_per_month": 2000}),
)


def now():
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def today():
    return date.today().isoformat()


def connect(timeout=30.0):
    """Соединение с control.sqlite3: WAL, busy_timeout, схема при первом запуске."""
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=%d" % int(timeout * 1000))
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    seed_plans(conn)
    conn.commit()
    return conn


def seed_plans(conn):
    """Тарифы по умолчанию: добавляются, если их ещё нет (ручные правки не трогаем)."""
    for code, title, price, period, limits in DEFAULT_PLANS:
        conn.execute(
            "INSERT INTO plans (code, title, price_rub, period_days, limits_json) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(code) DO NOTHING",
            (code, title, price, period, json.dumps(limits, ensure_ascii=False)))


def normalise_email(email):
    return (email or "").strip().lower()


def _hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), **SCRYPT)
    return salt, digest.hex()


def check_password(user, password):
    """Сравнение пароля с хешем без утечки времени."""
    if not user:
        return False
    try:
        _salt, digest = _hash(password or "", user["salt"])
    except ValueError:
        return False
    return hmac.compare_digest(digest, user["password_hash"])


def create_user(email, password=None, plan_code="trial", days=None, is_admin=False,
                conn=None):
    """Создаёт пользователя. Возвращает (id, пароль); пароль показывается один раз."""
    email = normalise_email(email)
    if not email or "@" not in email:
        raise ValueError("нужен e-mail вида user@example.com")
    days = 1 if days is None else int(days)
    generated = not password
    password = password or secrets.token_urlsafe(9)
    salt, digest = _hash(password)
    own = conn is None
    conn = conn or connect()
    try:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, salt, status, is_admin, "
            "must_change_password, plan_code, paid_until, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (email, digest, salt, "trial" if plan_code == "trial" else "active",
             int(bool(is_admin)), plan_code,
             (date.today() + timedelta(days=days)).isoformat(), now()))
        user_id = int(cur.lastrowid)
        audit(conn, None, user_id, "user_created",
              {"plan": plan_code, "days": days, "admin": bool(is_admin),
               "generated": generated})
        conn.commit()
        return user_id, password
    finally:
        if own:
            conn.close()


def get_user(user_id=None, email=None, conn=None):
    """Пользователь по id или e-mail."""
    own = conn is None
    conn = conn or connect()
    try:
        if user_id is not None:
            return conn.execute("SELECT * FROM users WHERE id = ?",
                                (int(user_id),)).fetchone()
        return conn.execute("SELECT * FROM users WHERE email = ?",
                            (normalise_email(email),)).fetchone()
    finally:
        if own:
            conn.close()


def list_users(conn=None):
    own = conn is None
    conn = conn or connect()
    try:
        return conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    finally:
        if own:
            conn.close()


def set_password(user_id, password=None, must_change=True, conn=None):
    """Меняет пароль. Без аргумента генерирует новый и возвращает его."""
    password = password or secrets.token_urlsafe(9)
    salt, digest = _hash(password)
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute("UPDATE users SET password_hash = ?, salt = ?, "
                     "must_change_password = ? WHERE id = ?",
                     (digest, salt, int(bool(must_change)), int(user_id)))
        audit(conn, None, user_id, "password_changed", {})
        conn.commit()
        return password
    finally:
        if own:
            conn.close()


def set_status(user_id, status, conn=None):
    """trial / active / blocked."""
    if status not in STATUSES:
        raise ValueError("статус должен быть одним из: %s" % ", ".join(STATUSES))
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, int(user_id)))
        audit(conn, None, user_id, "status_set", {"status": status})
        conn.commit()
    finally:
        if own:
            conn.close()


def extend(user_id, days, plan_code=None, conn=None):
    """Продлевает подписку: от текущей даты оплаты или от сегодня, если истекла."""
    days = int(days)
    if days <= 0:
        raise ValueError("срок продления должен быть больше нуля")
    own = conn is None
    conn = conn or connect()
    try:
        user = get_user(user_id, conn=conn)
        if not user:
            raise ValueError("пользователь не найден: %s" % user_id)
        current = user["paid_until"] or today()
        base = max(date.fromisoformat(current), date.today())
        paid_until = (base + timedelta(days=days)).isoformat()
        plan = plan_code or user["plan_code"]
        conn.execute("UPDATE users SET paid_until = ?, plan_code = ?, "
                     "status = CASE WHEN status = 'blocked' THEN status ELSE 'active' END "
                     "WHERE id = ?", (paid_until, plan, int(user_id)))
        conn.execute("INSERT INTO subscriptions (user_id, plan_code, started_at, paid_until) "
                     "VALUES (?, ?, ?, ?)", (int(user_id), plan, today(), paid_until))
        audit(conn, None, user_id, "extended", {"days": days, "paid_until": paid_until})
        conn.commit()
        return paid_until
    finally:
        if own:
            conn.close()


def is_active(user):
    """Доступ к действиям: не заблокирован и оплачен (пробный день тоже)."""
    if not user or user["status"] == "blocked":
        return False
    return (user["paid_until"] or "") >= today()


def plan_limits(plan_code="trial", conn=None):
    """Лимиты тарифа словарём (пустой, если тариф неизвестен)."""
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT limits_json FROM plans WHERE code = ?",
                           (plan_code,)).fetchone()
        return json.loads(row["limits_json"]) if row else {}
    finally:
        if own:
            conn.close()


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id, ttl=SESSION_TTL, ip="", user_agent="", conn=None):
    """Создаёт серверную сессию и возвращает токен для cookie."""
    token = secrets.token_urlsafe(32)
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, ip, "
            "user_agent) VALUES (?, ?, ?, ?, ?, ?)",
            (_token_hash(token), int(user_id), now(),
             (datetime.now() + timedelta(seconds=ttl)).replace(microsecond=0).isoformat(
                 sep=" "),
             (ip or "")[:64], (user_agent or "")[:200]))
        conn.commit()
        return token
    finally:
        if own:
            conn.close()


def session_user(header, conn=None):
    """Пользователь по cookie, если сессия жива; просроченная удаляется."""
    token = token_from_cookie(header)
    if not token:
        return None
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (_token_hash(token), now())).fetchone()
        if not row:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?",
                         (_token_hash(token),))
            conn.commit()
        return row
    finally:
        if own:
            conn.close()


def revoke_session(header, conn=None):
    """Удаляет сессию из cookie."""
    token = token_from_cookie(header)
    if not token:
        return
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        conn.commit()
    finally:
        if own:
            conn.close()


def cleanup_sessions(conn=None):
    """Удаляет просроченные сессии; возвращает число удалённых."""
    own = conn is None
    conn = conn or connect()
    try:
        cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now(),))
        conn.commit()
        return cur.rowcount
    finally:
        if own:
            conn.close()


def audit(conn, actor_id, user_id, kind, payload=None):
    """Запись в журнал действий (без секретов)."""
    conn.execute("INSERT INTO audit (ts, actor_id, user_id, kind, payload) "
                 "VALUES (?, ?, ?, ?, ?)",
                 (now(), actor_id, user_id, kind,
                  json.dumps(payload or {}, ensure_ascii=False)))
