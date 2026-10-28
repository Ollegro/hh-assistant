# -*- coding: utf-8 -*-
"""Очередь откликов: выборка подходящих вакансий, лимиты, статусы, экспорт писем."""
import os
from datetime import date, timedelta

from . import matching, storage
from .config import get_settings


def can_send_today(conn, daily_limit):
    """Сколько откликов ещё можно подготовить сегодня."""
    sent = storage.sent_today(conn)
    return max(0, daily_limit - sent)


def cooldown_companies(conn, days=None):
    """Компании, куда откликались меньше N дней назад: {нормализованное имя: дата}.

    Правило «одна компания — один отклик за период»: остальные вакансии этой
    компании прячутся из очереди, пока срок не истечёт. 0 — правило выключено.
    """
    days = get_settings().company_cooldown_days if days is None else days
    if not days:
        return {}
    cutoff = (date.today() - timedelta(days=int(days))).isoformat()
    cooled = {}
    for row in storage.sent_companies_since(conn, cutoff):
        name = matching.normalize(row["company"])
        if name:
            cooled.setdefault(name, row["sent_at"])
    return cooled


def next_batch(conn, limit=50, only_new=True, cooldown_days=None, region=""):
    """Вакансии для работы: всё найденное, по убыванию балла, без порога.

    Порога оценки больше нет: список показывает вакансии в порядке балла,
    а ориентир для решения — сам балл и вердикт в колонке «Оценка».
    region — субъект РФ; фильтр применяется в SQL, а не после выборки.
    """
    cooled = cooldown_companies(conn, cooldown_days)
    # Счётчик откликов берётся одним проходом по applications (LEFT JOIN по
    # сгруппированной выборке), а не подзапросом на каждую строку: при тысячах
    # вакансий коррелированный COUNT(*) стоил сотни миллисекунд на страницу.
    # Текст писем в список не тянем — карточка читает его отдельно.
    sql = """SELECT v.id, v.title, v.company, v.url, v.work_format, v.experience,
                    v.area, v.region, v.salary_from, v.salary_to, v.currency,
                    s.score, s.verdict, s.reasons,
                    CASE WHEN l.text IS NOT NULL AND TRIM(l.text) <> '' THEN 1 ELSE 0 END
                        AS has_letter,
                    COALESCE(l.edited, 0) AS letter_edited,
                    COALESCE(a.applied, 0) AS applied
             FROM vacancies v
             LEFT JOIN scores s ON s.vacancy_id = v.id
             LEFT JOIN letters l ON l.vacancy_id = v.id
             LEFT JOIN (SELECT vacancy_id, COUNT(*) AS applied FROM applications
                        GROUP BY vacancy_id) a ON a.vacancy_id = v.id
             WHERE 1 = 1"""
    params = []
    if only_new:
        sql += " AND COALESCE(a.applied, 0) = 0"
    if region:
        # старые строки собраны до появления region: сравниваем ещё и с городом,
        # иначе вакансии с area='Москва' выпадают из фильтра «Москва»
        sql += (" AND (COALESCE(v.region, '') = ?"
                " OR (COALESCE(v.region, '') = '' AND COALESCE(v.area, '') = ?))")
        params.extend([region, region])
    sql += " ORDER BY COALESCE(s.score, 0) DESC, v.created_at DESC"
    # Ограничение применяется после стоп-листа и кулдауна; None — вся выборка
    # для серверных фильтров таблицы и подсчёта страниц.
    rows = conn.execute(sql, params).fetchall()
    return _drop_cooled(_drop_excluded(rows), cooled)[:limit]


def _drop_cooled(rows, cooled):
    """Убирает компании из кулдауна и дубли одной компании внутри пачки."""
    if not cooled:
        return rows
    seen = set()
    out = []
    for row in rows:
        name = matching.normalize(row["company"])
        if name in cooled or name in seen:
            continue
        seen.add(name)
        out.append(row)
    return out


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


def kind_of(value):
    """Код статуса по коду или подписи: 'ответ' -> 'reply', 'invite' -> 'invite'."""
    value = (value or "").strip().lower()
    if value in storage.KIND_STATUSES:
        return value
    return storage.status_kind(value)


def set_status(conn, vacancy_id, kind, note=""):
    """Переводит вакансию в новый статус: запись в журнал + событие.

    Принимает и код ('invite'), и подпись ('приглашение'). Возвращает подпись,
    записанную в журнал.
    """
    code = kind_of(kind)
    if not code:
        raise ValueError("неизвестный статус: %s" % kind)
    status = storage.set_status(conn, vacancy_id, code, note=note)
    storage.log_event(conn, "status", {"vacancy_id": vacancy_id, "kind": code,
                                       "status": status, "note": note})
    return status


def mark_sent(conn, vacancy_id, status=None, note=""):
    """Отклик отправлен вручную."""
    status = status or storage.STATUS_SENT
    storage.add_application(conn, vacancy_id, status=status, note=note)
    storage.log_event(conn, "sent", {"vacancy_id": vacancy_id, "status": status})
    return True


def skip(conn, vacancy_id, reason=""):
    """Вакансия пропущена сознательно (в журнал — статус «пропущено»)."""
    status = storage.set_status(conn, vacancy_id, "skip", note=reason)
    storage.log_event(conn, "skip", {"vacancy_id": vacancy_id, "status": status,
                                     "reason": reason})
    return True

