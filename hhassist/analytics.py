# -*- coding: utf-8 -*-
"""Аналитика воронки откликов: найдено → оценено → письмо → отправлено → ответ.

Считается по локальной базе SQLite, без сети и без LLM. Один код для CLI
(`python cli.py funnel`) и веб-интерфейса (страница `/funnel`), поэтому цифры
в консоли и в браузере всегда совпадают.

Смысл статусов журнала:
- `sent` — вакансия ушла работодателю (вручную);
- `reply` / `invite` / `reject` — работодатель ответил (переписка / приглашение / отказ);
- `skip` — вакансия пропущена сознательно;
- ответ фиксируется отдельной записью, поэтому «отправлено» и «ответ» не теряются.
"""
import csv
import os
from datetime import date, timedelta

from . import storage

# Шаги воронки: (ключ, подпись, от какого шага считаем конверсию)
STEPS = (
    ("found", "найдено", None),
    ("scored", "оценено", "found"),
    ("letters", "письма готовы", "scored"),
    ("sent", "отправлено", "letters"),
    ("replied", "ответы", "sent"),
)

# Диапазоны баллов для разреза «балл → отклики → ответы»
SCORE_BANDS = (
    ("0–39", 0, 40),
    ("40–54", 40, 55),
    ("55–69", 55, 70),
    ("70–84", 70, 85),
    ("85–100", 85, 101),
)

CSV_HEADER = ("id", "балл", "вердикт", "вакансия", "компания", "формат", "опыт",
              "зарплата от", "зарплата до", "валюта", "опубликовано", "ссылка",
              "письмо, знаков", "статус", "обновлено", "примечание")


def percent(part, whole):
    """Доля в процентах с одним знаком; 0.0 при пустом знаменателе."""
    if not whole:
        return 0.0
    return round(100.0 * part / whole, 1)


def _scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else 0


def _cutoff(wait_days):
    return (date.today() - timedelta(days=int(wait_days))).isoformat()


def no_answer(conn, wait_days=14):
    """Сколько откликов отправлено, ответа нет и прошло не меньше wait_days дней."""
    sent = storage.statuses_for(storage.SENT_KINDS)
    replied = storage.statuses_for(storage.REPLY_KINDS)
    sql = """SELECT COUNT(*) FROM (
                 SELECT vacancy_id, MIN(substr(sent_at, 1, 10)) AS first_sent
                 FROM applications
                 WHERE status IN (%s)
                 GROUP BY vacancy_id)
             WHERE first_sent <= ?
               AND vacancy_id NOT IN (SELECT vacancy_id FROM applications WHERE status IN (%s))""" % (
        ",".join("?" * len(sent)), ",".join("?" * len(replied)))
    return _scalar(conn, sql, tuple(sent) + (_cutoff(wait_days),) + tuple(replied))


def awaiting_reply(conn, wait_days=14, limit=50):
    """Отклики без ответа дольше wait_days — список для страницы воронки."""
    sent = storage.statuses_for(storage.SENT_KINDS)
    replied = storage.statuses_for(storage.REPLY_KINDS)
    sql = """SELECT v.id, v.title, v.company, v.url, s.score,
                    MIN(substr(a.sent_at, 1, 10)) AS sent_date
             FROM applications a
             JOIN vacancies v ON v.id = a.vacancy_id
             LEFT JOIN scores s ON s.vacancy_id = v.id
             WHERE a.status IN (%s)
               AND v.id NOT IN (SELECT vacancy_id FROM applications WHERE status IN (%s))
             GROUP BY v.id
             HAVING MIN(substr(a.sent_at, 1, 10)) <= ?
             ORDER BY sent_date ASC, COALESCE(s.score, 0) DESC
             LIMIT ?""" % (",".join("?" * len(sent)), ",".join("?" * len(replied)))
    params = tuple(sent) + tuple(replied) + (_cutoff(wait_days), limit)
    return conn.execute(sql, params).fetchall()


def funnel(conn, wait_days=14):
    """Числа воронки по базе. 'replied' — вакансии с любым ответом работодателя."""
    ready = _scalar(
        conn,
        """SELECT COUNT(*) FROM letters l
           WHERE l.vacancy_id NOT IN (SELECT vacancy_id FROM applications)""",
    )
    return {
        "found": _scalar(conn, "SELECT COUNT(*) FROM vacancies"),
        "scored": _scalar(conn, "SELECT COUNT(*) FROM scores"),
        "letters": _scalar(conn, "SELECT COUNT(*) FROM letters"),
        "ready": ready,
        "sent": storage.count_vacancies_with(conn, storage.SENT_KINDS),
        "replied": storage.count_vacancies_with(conn, storage.REPLY_KINDS),
        "invited": storage.count_vacancies_with(conn, ("invite",)),
        "rejected": storage.count_vacancies_with(conn, ("reject",)),
        "skipped": storage.count_vacancies_with(conn, ("skip",)),
        "no_answer": no_answer(conn, wait_days),
        "wait_days": int(wait_days),
    }


def conversions(f):
    """Конверсии между шагами воронки в процентах (0.0 вместо деления на ноль)."""
    result = {}
    for key, _title, base in STEPS:
        if base:
            result[key] = percent(f.get(key, 0), f.get(base, 0))
    result["invited"] = percent(f.get("invited", 0), f.get("replied", 0))
    return result


def outcomes(conn):
    """По строке на вакансию: балл, формат, компания, был ли отклик и ответ."""
    sent = storage.statuses_for(storage.SENT_KINDS)
    replied = storage.statuses_for(storage.REPLY_KINDS)
    sql = """SELECT v.id, v.company, v.work_format, s.score,
                    EXISTS(SELECT 1 FROM applications a
                           WHERE a.vacancy_id = v.id AND a.status IN (%s)) AS sent,
                    EXISTS(SELECT 1 FROM applications a
                           WHERE a.vacancy_id = v.id AND a.status IN (%s)) AS replied
             FROM vacancies v
             LEFT JOIN scores s ON s.vacancy_id = v.id""" % (
        ",".join("?" * len(sent)), ",".join("?" * len(replied)))
    rows = conn.execute(sql, tuple(sent) + tuple(replied)).fetchall()
    return [{"id": r["id"], "company": r["company"] or "", "work_format": r["work_format"] or "",
             "score": r["score"], "sent": bool(r["sent"]), "replied": bool(r["replied"])}
            for r in rows]


def _summary(title, rows):
    sent = sum(1 for r in rows if r["sent"])
    replied = sum(1 for r in rows if r["replied"])
    return {"title": title, "scored": len(rows), "sent": sent, "replied": replied,
            "reply_rate": percent(replied, sent)}


def by_score_band(conn):
    """Разрез по диапазонам баллов: сколько оценено, отправлено, ответов."""
    rows = [r for r in outcomes(conn) if r["score"] is not None]
    return [_summary(title, [r for r in rows if low <= r["score"] < high])
            for title, low, high in SCORE_BANDS]


def _grouped(conn, key, limit=None):
    groups = {}
    for row in outcomes(conn):
        groups.setdefault(key(row), []).append(row)
    result = [_summary(title, rows) for title, rows in groups.items()]
    result.sort(key=lambda g: (-g["sent"], -g["scored"], g["title"]))
    return result[:limit] if limit else result


def by_format(conn):
    """Разрез по формату работы: где отклики чаще получают ответ."""
    return _grouped(conn, lambda r: r["work_format"] or "не указан")


def by_company(conn, limit=10):
    """Разрез по компаниям (топ по числу откликов)."""
    return _grouped(conn, lambda r: r["company"] or "не указана", limit=limit)


def format_report(conn, wait_days=14):
    """Текстовый отчёт воронки для `python cli.py funnel`.

    Только ASCII-разделители (`->`, `~`): при перенаправлении вывода в файл
    консоль Windows использует cp866/cp1251, и стрелки `→` ломают печать.
    """
    f = funnel(conn, wait_days=wait_days)
    c = conversions(f)
    lines = ["воронка откликов", "%-22s %6s %8s" % ("шаг", "шт.", "%")]
    for key, title, _base in STEPS:
        value = "-" if key == "found" else "%.1f" % c.get(key, 0.0)
        lines.append("%-22s %6d %8s" % (title, f[key], value))
    lines += [
        "",
        "%-22s %6d" % ("готово к отправке", f["ready"]),
        "%-22s %6d" % ("пропущено", f["skipped"]),
        "%-22s %6d" % ("без ответа > %d дн." % f["wait_days"], f["no_answer"]),
        "%-22s %6d" % ("приглашения", f["invited"]),
        "%-22s %6d" % ("отказы", f["rejected"]),
        "",
        "конверсии: отправка -> ответ %.1f%%, ответ -> приглашение %.1f%%" % (
            c.get("replied", 0.0), c.get("invited", 0.0)),
        "ориентир отрасли: ~1 ответ на 18 откликов (5,6%)",
    ]
    return "\n".join(lines)


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
