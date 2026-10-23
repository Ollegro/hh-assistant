# -*- coding: utf-8 -*-
"""Пакетные действия ассистента: сбор, оценка, письма, экспорт.

Логика одна для CLI (`cli.py`) и веб-интерфейса (`hhassist/web.py`): раньше
одинаковые циклы были продублированы в двух местах, из-за чего правки
приходилось делать дважды. Запись в базу идёт одной транзакцией на прогон:
раньше каждый `upsert`/`save_score` делал собственный `commit`.
"""
import json
import os

from . import collector, letters as letters_mod, scoring, storage


def vacancy_dict(row):
    """Строка SQLite -> словарь вакансии (skills хранится как JSON-строка)."""
    v = dict(row)
    if isinstance(v.get("skills"), str):
        try:
            v["skills"] = json.loads(v["skills"])
        except ValueError:
            v["skills"] = []
    return v


def collect_queries(conn, queries, settings, limit=None, pages=None, on_item=None):
    """Собирает вакансии по всем запросам. Возвращает (saved, found, source, notes)."""
    limit = settings.collect_limit if limit is None else limit
    pages = settings.collect_pages if pages is None else pages
    saved = found = 0
    source = "html"
    notes = []
    for query in queries:
        try:
            vacancies, info = collector.collect(query, area=1, pages=pages,
                                                per_page=50, limit=limit)
        except Exception as exc:  # сеть, блокировка, парсер — один запрос не рушит прогон
            notes.append("«%s»: поиск не удался (%s)" % (query, exc))
            storage.log_event(conn, "collect_error", {"query": query, "error": str(exc)})
            continue
        source = info["source"]
        found += info.get("found", 0)
        if info["fallback"]:
            notes.append("откат на HTML: %s" % info["reason"])
            storage.log_event(conn, "source_fallback",
                              {"query": query, "reason": info["reason"]})
        for v in vacancies:
            storage.upsert_vacancy(conn, v, commit=False)
            saved += 1
            if on_item:
                on_item(v)
    conn.commit()
    return saved, found, source, notes


def score_pending(conn, settings, force=False, on_item=None):
    """Оценивает вакансии: по умолчанию только без оценки. Возвращает (new, total)."""
    sql = "SELECT * FROM vacancies"
    if not force:
        sql += " WHERE id NOT IN (SELECT vacancy_id FROM scores)"
    rows = conn.execute(sql).fetchall()
    done = 0
    for row in rows:
        v = vacancy_dict(row)
        res = scoring.score_vacancy(v, settings.profile, settings)
        storage.save_score(conn, v["id"], res["score"], res["verdict"], res["reasons"],
                           commit=False)
        done += 1
        if on_item:
            on_item(v, res)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    return done, total


def letter_vacancies(conn, threshold, limit=50, force=False):
    """Вакансии выше порога, для которых ещё нет письма (force=True — все)."""
    sql = """SELECT v.* FROM vacancies v
             LEFT JOIN scores sc ON sc.vacancy_id = v.id
             LEFT JOIN letters l ON l.vacancy_id = v.id
             WHERE COALESCE(sc.score, 0) >= ?"""
    if not force:
        sql += " AND l.text IS NULL"
    sql += " ORDER BY COALESCE(sc.score, 0) DESC, v.created_at DESC LIMIT ?"
    return conn.execute(sql, (threshold, limit)).fetchall()


def make_letters(conn, settings, threshold=None, limit=50, force=False, on_item=None):
    """Генерирует письма пачкой. Возвращает количество созданных."""
    threshold = settings.score_threshold if threshold is None else threshold
    made = 0
    for row in letter_vacancies(conn, threshold, limit=limit, force=force):
        v = vacancy_dict(row)
        text, model = letters_mod.make_letter(v, settings.profile, settings)
        storage.save_letter(conn, v["id"], text, model, commit=False)
        made += 1
        if on_item:
            on_item(v, text, model)
    conn.commit()
    return made


def export_letters(conn, out_dir, threshold=0, limit=1000):
    """Пишет все готовые письма в .txt. Возвращает количество файлов."""
    from . import queue

    made = 0
    for row in queue.next_batch(conn, threshold=threshold, limit=limit):
        if row["letter"] and queue.export_letter(conn, row["id"], out_dir):
            made += 1
    return made


def default_out_dir(base):
    """Папка out/ рядом с приложением."""
    return os.path.join(base, "out")
