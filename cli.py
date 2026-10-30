# -*- coding: utf-8 -*-
"""CLI ассистента: collect / score / letters / serve / export / stats.

Примеры:
    python cli.py collect --query "AI инженер" --pages 2
    python cli.py score
    python cli.py letters
    python cli.py serve
"""
import argparse
import os
import sys
import time

from hhassist import (areas, collector, csv_export, hh_api, jobs, letters, llm, queue, storage,
                      web)
from hhassist.config import BASE, ensure_dirs, get_settings, parse_areas


def cmd_collect(args):
    ensure_dirs()
    areas.ensure()  # справочник регионов: нужен фильтрам и выбору региона в вебе
    conn = storage.connect()
    s = get_settings()
    queries = list(args.query)
    if args.query_file:
        with open(args.query_file, encoding="utf-8") as f:
            queries += [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if not queries:
        print("укажите --query или --query-file")
        conn.close()
        return 1
    area_ids = parse_areas(args.areas) if args.areas else (
        [args.area] if args.area else s.collect_areas)
    refresh_days = 0 if args.force else (
        s.collect_refresh_days if args.refresh_days is None else args.refresh_days)
    remote = args.remote or s.collect_remote
    known = storage.seen_index(conn) if refresh_days else {}
    print("регионы: %s · обновлять старше %d дн. · удалёнка: %s" % (
        ", ".join(str(a) for a in area_ids), refresh_days, "да" if remote else "нет"))
    total = 0
    sources = {}
    total_cap = int(getattr(s, "collect_total", 0) or 0)   # предел за прогон (COLLECT_TOTAL)
    for query in queries:
        if total_cap and total >= total_cap:
            print("достигнут предел сбора за прогон: %d" % total_cap)
            break
        print("поиск: %s" % query)
        per_query = min(args.limit, total_cap - total) if total_cap else args.limit
        try:
            vacancies, info = collector.collect(
                query, area=area_ids, pages=args.pages, per_page=args.per_page,
                limit=per_query, source=args.source, details=not args.no_details,
                known=known, refresh_days=refresh_days, remote=remote,
            )
        except Exception as e:
            print("  ошибка поиска: %s" % e)
            storage.log_event(conn, "collect_error", {"query": query, "error": str(e)})
            continue
        sources[info["source"]] = sources.get(info["source"], 0) + 1
        print("  найдено: %d, новых: %d, обновлено: %d, уже в базе: %d (источник: %s)" % (
            info["found"], info.get("new", 0), info.get("refreshed", 0),
            info.get("skipped", 0), info["source"]))
        if info["fallback"]:
            print("  откат на HTML-парсер: %s" % info["reason"])
            storage.log_event(conn, "source_fallback",
                              {"query": query, "reason": info["reason"]})
        health = info.get("quality") or {}
        if health.get("state") in ("suspect", "empty"):
            print("  внимание: похоже, парсер сломался (%s)" % health)
            storage.log_event(conn, "parser_suspect",
                              {"query": query, "source": info["source"], "quality": health})
        for err in info["errors"]:
            print("  %s: ошибка загрузки (%s)" % (err["id"], err["error"]))
        for v in vacancies:
            if total_cap and total >= total_cap:
                break
            storage.upsert_vacancy(conn, v, commit=False)
            total += 1
            print("  + %s | %s | %s | %s" % (v["id"], v["title"][:60], v["company"][:40],
                                             v.get("area") or "—"))
        conn.commit()
    storage.log_event(conn, "collect", {"queries": args.query, "saved": total,
                                        "areas": area_ids,
                                        "ua": s.user_agent, "sources": sources})
    print("сохранено вакансий: %d" % total)
    conn.close()


def cmd_letter(args):
    """Показать или заменить текст письма (правка вручную, не перезаписывается генерацией)."""
    conn = storage.connect()
    row = storage.vacancy_with_letter(conn, args.id)
    if not row:
        print("вакансия %s не найдена" % args.id)
        conn.close()
        return 1
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read().replace("\r\n", "\n").strip()
        if not text:
            print("файл пустой — письмо не изменено")
            conn.close()
            return 1
        storage.save_letter(conn, args.id, text, model="manual", edited=True)
        storage.log_event(conn, "letter_edit", {"vacancy_id": args.id, "chars": len(text),
                                                "from": "cli"})
        print("письмо сохранено: символов %d" % len(text))
    else:
        print(row["letter"] or "(письма нет)")
    conn.close()


def cmd_api_check(args):
    """Диагностика доступа к API hh.ru: токен -> /me -> поиск. Секреты не печатаются."""
    conn = storage.connect()
    s = get_settings()
    print("HH_SOURCE      %s" % s.hh_source)
    print("токен          %s" % ("настроен" if hh_api.configured() else "нет настройки"))
    if args.refresh:
        hh_api.reset_token()
    report = hh_api.probe()
    print("источник токена %s" % report["token_source"])
    if report["token_length"]:
        print("длина токена   %d символов" % report["token_length"])
    me = report["me"] or {}
    if me:
        print("/me            id=%s, %s" % (me.get("id"), me.get("name") or "-"))
    if report["found"] is not None:
        print("/vacancies     найдено: %s" % report["found"])
    if report["error"]:
        err = report["error"]
        if err["status"]:
            print("ошибка         HTTP %s %s" % (err["status"], err["value"]))
        else:
            print("ошибка         настройка hh")
        print("сообщение      %s" % err["message"])
        if err["request_id"]:
            print("request_id     %s (указать в письме на api@hh.ru)" % err["request_id"])
        print("подсказка      HH_SOURCE=html — работа через HTML-парсер, без токена")
        storage.log_event(conn, "api_check", {"ok": False, "status": err["status"],
                                              "value": err["value"],
                                              "request_id": err["request_id"]})
        conn.close()
        return 1
    storage.log_event(conn, "api_check", {"ok": True, "found": report["found"]})
    print("итог           API доступен")
    conn.close()
    return 0



def cmd_score(args):
    conn = storage.connect()
    s = get_settings()

    def show(v, res):
        print("%s | %3d | %-16s | %s" % (v["id"], res["score"], res["verdict"], v["title"][:55]))

    done, _total = jobs.score_pending(conn, s, force=args.all, on_item=show)
    storage.log_event(conn, "score", {"count": done, "model": s.llm_model or "rules"})
    print("оценено: %d" % done)
    conn.close()


def cmd_letters(args):
    conn = storage.connect()
    s = get_settings()

    def show(v, text, model):
        problems = letters.validate_letter(text)
        flag = ("проблемы: " + ",".join(problems)) if problems else "ок"
        print("%s | %-9s | %4d знаков | %s | %s" % (
            v["id"], model, len(text), flag, v["title"][:45]))

    made = jobs.make_letters(conn, s, limit=args.limit, force=args.force, on_item=show)
    storage.log_event(conn, "letters", {"count": made, "model": s.llm_model or "offline"})
    print("писем создано: %d" % made)
    conn.close()


def cmd_serve(args):
    web.run(host=args.host, port=args.port)


def cmd_export(args):
    conn = storage.connect()
    out_dir = args.out or jobs.default_out_dir(BASE)
    if not args.csv:
        made = jobs.export_letters(conn, out_dir)
        print("экспортировано писем: %d -> %s" % (made, out_dir))
    if args.csv:
        path = args.csv_path or os.path.join(out_dir, "vacancies.csv")
        rows = csv_export.write_csv(conn, path)
        print("экспортировано строк CSV: %d -> %s" % (rows, path))
    conn.close()


def cmd_mark(args):
    """Ручная смена статуса: отправлено / пропущено."""
    conn = storage.connect()
    code = queue.kind_of(args.status)
    if not code:
        print("неизвестный статус: %s" % args.status)
        print("доступны: %s" % ", ".join(
            "%s (%s)" % (storage.KIND_TITLES[k], k) for k in storage.KIND_STATUSES))
        conn.close()
        return 1
    if not storage.vacancy_with_letter(conn, args.id):
        print("вакансия не найдена: %s" % args.id)
        conn.close()
        return 1
    status = queue.set_status(conn, args.id, code, note=args.note or "")
    print("%s -> %s%s" % (args.id, status,
                          (" | " + args.note) if args.note else ""))
    conn.close()
    return 0


def cmd_areas(args):
    """Справочник регионов hh.ru: список субъектов РФ для фильтров и сбора."""
    items = areas.ensure(force=args.refresh)
    print("регионов: %d (кэш: %s)" % (len(items), areas.CACHE_PATH))
    for area_id, name in items:
        print("%6s  %s" % (area_id, name))
    return 0


def cmd_reset(args):
    from hhassist.config import DATA_DIR
    db = os.path.join(DATA_DIR, "hhassist.sqlite3")
    if not args.yes:
        print("это удалит базу данных: %s\nповторите с --yes" % db)
        return 1
    if os.path.isfile(db):
        os.remove(db)
        print("база удалена:", db)
    else:
        print("база не найдена:", db)
    return 0


def cmd_list(args):
    conn = storage.connect()
    rows = conn.execute(
        """SELECT v.*, s.score, s.verdict FROM vacancies v
           LEFT JOIN scores s ON s.vacancy_id = v.id
           ORDER BY COALESCE(s.score, 0) DESC, v.created_at DESC LIMIT ?""",
        (args.limit,),
    ).fetchall()
    print("%-10s %4s %-38s %-22s %-14s %s" % ("id", "балл", "вакансия", "компания", "формат", "описание"))
    for r in rows:
        print("%-10s %4s %-38s %-22s %-14s %d знаков" % (
            r["id"], r["score"] if r["score"] is not None else "-",
            (r["title"] or "")[:38], (r["company"] or "")[:22],
            (r["work_format"] or "-")[:14], len(r["description"] or "")))
    print("всего:", len(rows))
    conn.close()


def cmd_stats(args):
    conn = storage.connect()
    st = storage.stats(conn)
    s = get_settings()
    for k, v in st.items():
        print("%-14s %s" % (k, v))
    print("%-14s %s" % ("лимит/день", s.daily_limit))
    print("%-14s %s" % ("UA", s.user_agent))
    print("%-14s %s" % ("LLM", llm.describe(s)))
    conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="hh-assistant: ассистент откликов на hh.ru")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect", help="собрать вакансии по поисковым запросам")
    p.add_argument("--query", action="append", default=[], help="поисковый запрос (можно несколько)")
    p.add_argument("--query-file", default=None,
                   help="файл со списком запросов (по одному в строке, UTF-8) — удобно для русских запросов")
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--per-page", type=int, default=50)
    p.add_argument("--area", type=int, default=None, help="один регион hh (1 = Москва)")
    p.add_argument("--areas", default=None,
                   help="регионы через запятую: 113,1 или moscow,spb (по умолчанию COLLECT_AREAS=113)")
    p.add_argument("--remote", action="store_true", help="только удалёнка (COLLECT_REMOTE)")
    p.add_argument("--refresh-days", type=int, default=None,
                   help="не скачивать повторно вакансии, виденные меньше N дней назад "
                        "(COLLECT_REFRESH_DAYS, по умолчанию 3)")
    p.add_argument("--force", action="store_true",
                   help="скачать заново все найденные вакансии (без инкремента)")
    p.add_argument("--limit", type=int, default=50, help="максимум вакансий на запрос")
    p.add_argument("--source", choices=("auto", "api", "html"), default=None,
                   help="источник данных: auto (HH_SOURCE), api или html")
    p.add_argument("--no-details", action="store_true",
                   help="не запрашивать полные карточки (быстрее, но описание из сниппета)")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("api-check", help="проверить токен приложения hh.ru и доступ к API")
    p.add_argument("--refresh", action="store_true", help="перевыпустить токен (client_credentials)")
    p.set_defaults(func=cmd_api_check)

    p = sub.add_parser("score", help="оценить вакансии (правила + LLM)")
    p.add_argument("--all", action="store_true", help="переоценить все вакансии")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("letters", help="сгенерировать письма для вакансий без письма")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--force", action="store_true", help="перегенерировать существующие письма")
    p.set_defaults(func=cmd_letters)

    p = sub.add_parser("serve", help="запустить локальный веб-интерфейс")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("export", help="экспортировать письма в out/ (--csv — таблица вакансий)")
    p.add_argument("--out", default=None)
    p.add_argument("--csv", action="store_true", help="вместо писем выгрузить таблицу вакансий в CSV")
    p.add_argument("--csv-path", default=None, help="куда сохранить CSV (по умолчанию out/vacancies.csv)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("mark", help="сменить статус отклика вручную")
    p.add_argument("id", help="id вакансии (см. list)")
    p.add_argument("--status", required=True,
                   help="sent | skip (можно по-русски: «отправлено», «пропущено»)")
    p.add_argument("--note", default="", help="примечание к записи журнала")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("letter", help="показать или заменить письмо вакансии (правка вручную)")
    p.add_argument("id", help="id вакансии")
    p.add_argument("--file", default=None,
                   help="файл с новым текстом письма (UTF-8); правка больше не перегенерируется")
    p.set_defaults(func=cmd_letter)

    p = sub.add_parser("reset", help="удалить базу данных (для пересбора)")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("areas", help="справочник регионов hh.ru (субъекты РФ)")
    p.add_argument("--refresh", action="store_true", help="загрузить заново с api.hh.ru")
    p.set_defaults(func=cmd_areas)

    p = sub.add_parser("list", help="показать собранные вакансии")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("stats", help="показать статистику")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
