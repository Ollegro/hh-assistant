# -*- coding: utf-8 -*-
"""Пакетные действия ассистента: сбор, оценка, письма, экспорт.

Логика одна для CLI (`cli.py`) и веб-интерфейса (`hhassist/web.py`): раньше
одинаковые циклы были продублированы в двух местах, из-за чего правки
приходилось делать дважды. Запись в базу идёт одной транзакцией на прогон:
раньше каждый `upsert`/`save_score` делал собственный `commit`.
"""
import json
import os

from . import collector, letters as letters_mod, matching, scoring, storage

# Коммит пачками: одна транзакция на весь прогон держала блокировку записи всё
# время работы LLM (минуты), и параллельный запрос падал с «database is locked».
COMMIT_EVERY = 5


def _commit_progress(conn, done):
    """Промежуточный коммит: короткие транзакции не мешают соседним запросам."""
    if done and done % COMMIT_EVERY == 0:
        conn.commit()


def vacancy_dict(row):
    """Строка SQLite -> словарь вакансии (skills хранится как JSON-строка)."""
    v = dict(row)
    if isinstance(v.get("skills"), str):
        try:
            v["skills"] = json.loads(v["skills"])
        except ValueError:
            v["skills"] = []
    return v


def collect_queries(conn, queries, settings, limit=None, pages=None, on_item=None,
                    area_ids=None):
    """Собирает вакансии по всем запросам. Возвращает (saved, found, source, notes)."""
    limit = settings.collect_limit if limit is None else limit
    pages = settings.collect_pages if pages is None else pages
    areas = list(area_ids or getattr(settings, "collect_areas", None) or [113])
    refresh_days = int(getattr(settings, "collect_refresh_days", 0) or 0)
    remote = bool(getattr(settings, "collect_remote", False))
    # инкрементальный сбор: недавно виденные вакансии повторно не скачиваем
    known = storage.seen_index(conn) if refresh_days else {}
    saved = found = skipped = 0
    source = "html"
    notes = []
    for query in queries:
        try:
            vacancies, info = collector.collect(query, area=areas, pages=pages,
                                                per_page=50, limit=limit, known=known,
                                                refresh_days=refresh_days, remote=remote)
        except Exception as exc:  # сеть, блокировка, парсер — один запрос не рушит прогон
            notes.append("«%s»: поиск не удался (%s)" % (query, exc))
            storage.log_event(conn, "collect_error", {"query": query, "error": str(exc)})
            continue
        source = info["source"]
        found += info.get("found", 0)
        skipped += info.get("skipped", 0)
        if info["fallback"]:
            notes.append("откат на HTML: %s" % info["reason"])
            storage.log_event(conn, "source_fallback",
                              {"query": query, "reason": info["reason"]})
        health = info.get("quality") or {}
        if health.get("state") in ("suspect", "empty"):
            storage.log_event(conn, "parser_suspect",
                              {"query": query, "source": info["source"], "quality": health})
            notes.append("«%s»: похоже, парсер сломался (%s)" % (query, health))
        for v in vacancies:
            storage.upsert_vacancy(conn, v, commit=False)
            saved += 1
            _commit_progress(conn, saved)
            if on_item:
                on_item(v)
    conn.commit()
    if skipped:
        notes.append("уже в базе, повторно не скачивали: %d" % skipped)
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
                           commit=False, rule_score=res.get("rule_score"),
                           llm_score=res.get("llm_score"))
        done += 1
        _commit_progress(conn, done)
        if on_item:
            on_item(v, res)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    return done, total


def letter_vacancies(conn, limit=50, force=False):
    """Вакансии, для которых ещё нет письма (force=True — все), по убыванию балла.

    Порога оценки нет: письма делаются для всех вакансий из списка по порядку
    балла. Письма, правленные человеком (letters.edited=1), не перегенерируются
    никогда, а компании, куда откликались меньше COMPANY_COOLDOWN_DAYS дней назад,
    пропускаются.
    """
    from . import queue

    sql = """SELECT v.* FROM vacancies v
             LEFT JOIN scores sc ON sc.vacancy_id = v.id
             LEFT JOIN letters l ON l.vacancy_id = v.id
             WHERE COALESCE(l.edited, 0) = 0"""
    if not force:
        sql += " AND l.text IS NULL"
    sql += " ORDER BY COALESCE(sc.score, 0) DESC, v.created_at DESC LIMIT ?"
    cooled = queue.cooldown_companies(conn)
    # с запасом по строке: часть вакансий отсеет кулдаун компаний
    rows = conn.execute(sql, (limit * 2 if cooled else limit,)).fetchall()
    if cooled:
        rows = [r for r in rows
                if matching.normalize(r["company"]) not in cooled]
    return rows[:limit]


def score_one(conn, settings, vacancy_id):
    """Оценивает одну вакансию из карточки. Возвращает (балл, вердикт) или None."""
    row = conn.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,)).fetchone()
    if not row:
        return None
    v = vacancy_dict(row)
    res = scoring.score_vacancy(v, settings.profile, settings)
    storage.save_score(conn, v["id"], res["score"], res["verdict"], res["reasons"],
                       rule_score=res.get("rule_score"), llm_score=res.get("llm_score"))
    return res["score"], res["verdict"]


def make_letter_for(conn, settings, vacancy_id):
    """Генерирует письмо для одной вакансии (кнопка в карточке).

    Правленное человеком письмо не затирается: возвращается его текст с пометкой
    «правка сохранена», новый текст пишется только когда письма ещё нет.
    Возвращает (текст, модель, признак «уже было письмо») или None, если вакансии нет.
    """
    row = conn.execute(
        """SELECT v.*, COALESCE(l.edited, 0) AS letter_edited, l.text AS letter
           FROM vacancies v LEFT JOIN letters l ON l.vacancy_id = v.id
           WHERE v.id = ?""", (vacancy_id,)).fetchone()
    if not row:
        return None
    if row["letter_edited"] and row["letter"]:
        return row["letter"], "manual", True
    v = vacancy_dict(row)
    text, model = letters_mod.make_letter(v, settings.profile, settings)
    storage.save_letter(conn, v["id"], text, model)
    return text, model, False


def make_letters(conn, settings, limit=50, force=False, on_item=None):
    """Генерирует письма пачкой. Возвращает количество созданных."""
    made = 0
    for row in letter_vacancies(conn, limit=limit, force=force):
        v = vacancy_dict(row)
        text, model = letters_mod.make_letter(v, settings.profile, settings)
        storage.save_letter(conn, v["id"], text, model, commit=False)
        made += 1
        _commit_progress(conn, made)
        if on_item:
            on_item(v, text, model)
    conn.commit()
    return made


def export_letters(conn, out_dir, limit=1000):
    """Пишет все готовые письма в .txt. Возвращает количество файлов."""
    from . import queue

    made = 0
    for row in queue.next_batch(conn, limit=limit):
        if row["letter"] and queue.export_letter(conn, row["id"], out_dir):
            made += 1
    return made


def default_out_dir(base):
    """Папка out/ рядом с приложением."""
    return os.path.join(base, "out")
