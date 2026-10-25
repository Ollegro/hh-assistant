# -*- coding: utf-8 -*-
"""Перекачка места работы вакансий: `area`, `area_id`, `region` из блока `area` hh.

Зачем: до 25.10.2026 парсер брал город из адреса работодателя
(`addressLocality`), а блок самой вакансии `"area": {"@id": …, "name": …}` не
читал. Из-за этого вакансия из ДНР с адресом работодателя в Московской области
показывалась как московская, хотя фильтр региона относил её к ДНР. Парсер
исправлен, но старые строки остались с прежними значениями — этот скрипт
скачивает карточку заново и обновляет только `area`, `area_id`, `region`
(заголовок, описание, зарплата и прочее не трогаются).

Примеры:
    python tools/refresh_areas.py --dry-run        # только показать, что будет
    python tools/refresh_areas.py                  # строки без area_id
    python tools/refresh_areas.py --all --limit 20 # первые 20 вакансий
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hhassist import collector, storage  # noqa: E402

COMMIT_EVERY = 5


def needs_refresh(area_id, force=False):
    """Нужно ли перекачивать: у HTML-строк до 25.10.2026 `area_id` пустой."""
    if force:
        return True
    return not str(area_id or "").strip()


def pick(conn, force=False, limit=None):
    """Вакансии для перекачки: [(id, area, region, area_id)] от новых к старым."""
    sql = "SELECT id, area, region, area_id FROM vacancies ORDER BY created_at DESC"
    rows = [(row["id"], row["area"] or "", row["region"] or "", row["area_id"] or "")
            for row in conn.execute(sql)]
    rows = [row for row in rows if needs_refresh(row[3], force=force)]
    return rows[:limit] if limit else rows


def refresh_one(conn, vacancy_id, pause=1.2):
    """Скачивает карточку и обновляет место работы. Возвращает (было, стало)."""
    before = conn.execute("SELECT area, region, area_id FROM vacancies WHERE id = ?",
                          (vacancy_id,)).fetchone()
    before = (before["area"] or "", before["region"] or "", before["area_id"] or "") if before else ("", "", "")
    data = collector.fetch_vacancy(vacancy_id, pause=pause)
    conn.execute(
        """UPDATE vacancies SET area = COALESCE(NULLIF(?, ''), area),
                  region = COALESCE(NULLIF(?, ''), region),
                  area_id = COALESCE(NULLIF(?, ''), area_id)
           WHERE id = ?""",
        (data.get("area") or "", data.get("region") or "",
         str(data.get("area_id") or ""), vacancy_id))
    after = (data.get("area") or before[0], data.get("region") or before[1],
             str(data.get("area_id") or before[2]))
    return before, after


def main(argv=None):
    ap = argparse.ArgumentParser(description="Перекачать место работы вакансий (area/region)")
    ap.add_argument("--all", action="store_true", help="все вакансии, а не только без area_id")
    ap.add_argument("--limit", type=int, default=None, help="сколько вакансий обработать")
    ap.add_argument("--pause", type=float, default=1.2, help="пауза между запросами, сек")
    ap.add_argument("--dry-run", action="store_true", help="только показать список")
    args = ap.parse_args(argv)

    conn = storage.connect()
    try:
        rows = pick(conn, force=args.all, limit=args.limit)
        print("к обновлению: %d" % len(rows))
        if args.dry_run:
            for vid, area, region, area_id in rows[:30]:
                print("%-10s area=%r region=%r area_id=%r" % (vid, area, region, area_id))
            return 0
        for index, (vid, _area, _region, _area_id) in enumerate(rows, start=1):
            try:
                before, after = refresh_one(conn, vid, pause=args.pause)
                print("[%d/%d] %s: %r -> %r" % (index, len(rows), vid, before, after))
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
