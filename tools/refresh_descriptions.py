# -*- coding: utf-8 -*-
"""Перекачка описаний вакансий: убирает JS-мусор и восстанавливает абзацы.

Зачем: до 24.10.2026 парсер брал описание «до конца документа», и в него
попадал код страницы (jQuery, swiper), а `clean()` схлопывал все переводы
строк. После правки парсера текст собирается правильно, но старые записи
в базе остались «грязными» — этот скрипт скачивает карточку заново и
обновляет описание (title, зарплата и прочее не трогаются).

Примеры:
    python tools/refresh_descriptions.py --dry-run        # только показать, что будет
    python tools/refresh_descriptions.py                  # только «грязные» записи
    python tools/refresh_descriptions.py --all --limit 20 # первые 20 вакансий
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hhassist import collector, storage  # noqa: E402

# Признаки «грязного» описания: код страницы или полное отсутствие абзацев.
# `function ()` — как в JS; «function/tool calling» из текста вакансии не считается.
BAD_MARKERS = re.compile(r"function\s*\(\s*\)|jQuery|window\.|querySelector\(|<script",
                         re.I)
COMMIT_EVERY = 5


def needs_refresh(description, force=False):
    """Нужно ли перекачивать вакансию: код в тексте или нет ни одного абзаца."""
    if force:
        return True
    text = description or ""
    if not text.strip():
        return True
    if BAD_MARKERS.search(text):
        return True
    return "\n" not in text


def pick(conn, force=False, limit=None):
    """Вакансии для перекачки: [(id, description)] по убыванию балла."""
    sql = """SELECT v.id, v.description FROM vacancies v
             LEFT JOIN scores s ON s.vacancy_id = v.id
             ORDER BY COALESCE(s.score, 0) DESC, v.created_at DESC"""
    rows = [(row["id"], row["description"] or "") for row in conn.execute(sql)]
    rows = [r for r in rows if needs_refresh(r[1], force=force)]
    return rows[:limit] if limit else rows


def refresh_one(conn, vacancy_id, pause=1.2):
    """Скачивает карточку и обновляет описание. Возвращает (было, стало) знаков."""
    before = conn.execute("SELECT COALESCE(description, '') AS d FROM vacancies WHERE id = ?",
                          (vacancy_id,)).fetchone()
    before_len = len(before["d"]) if before else 0
    data = collector.fetch_vacancy(vacancy_id, pause=pause)
    text = data.get("description") or ""
    if not text:
        return before_len, before_len
    conn.execute(
        """UPDATE vacancies SET description = ?, skills = COALESCE(NULLIF(?, ''), skills),
                  area = COALESCE(NULLIF(?, ''), area),
                  region = COALESCE(NULLIF(?, ''), region),
                  area_id = COALESCE(NULLIF(?, ''), area_id)
           WHERE id = ?""",
        (text, ",".join(data.get("skills") or []), data.get("area") or "",
         data.get("region") or "", data.get("area_id") or "", vacancy_id))
    return before_len, len(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Перекачать описания вакансий (чистый текст)")
    ap.add_argument("--all", action="store_true", help="все вакансии, а не только грязные")
    ap.add_argument("--limit", type=int, default=None, help="сколько вакансий обработать")
    ap.add_argument("--pause", type=float, default=1.2, help="пауза между запросами, сек")
    ap.add_argument("--dry-run", action="store_true", help="только показать список")
    args = ap.parse_args(argv)

    conn = storage.connect()
    try:
        rows = pick(conn, force=args.all, limit=args.limit)
        print("к перекачке: %d" % len(rows))
        if args.dry_run:
            for vid, text in rows[:30]:
                print("%-10s %5d знаков" % (vid, len(text)))
            conn.close()
            return 0
        for index, (vid, _text) in enumerate(rows, start=1):
            try:
                before, after = refresh_one(conn, vid, pause=args.pause)
                print("[%d/%d] %s: %d -> %d знаков" % (index, len(rows), vid, before, after))
            except Exception as exc:  # одна вакансия не должна останавливать прогон
                print("[%d/%d] %s: ошибка (%s)" % (index, len(rows), vid, exc))
            if index % COMMIT_EVERY == 0:
                conn.commit()
        conn.commit()
        print("готово")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
