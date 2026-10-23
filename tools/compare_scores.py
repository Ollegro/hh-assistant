# -*- coding: utf-8 -*-
"""Сравнение скоринга: правила против LLM на вакансиях из базы.

Запуск: python tools/compare_scores.py [--limit N] [--diff N] [--txt FILE]

Зачем: после подключения ключа видно, где оценка модели расходится с правилами,
и можно осознанно калибровать веса `scoring.RULE_WEIGHT` / `scoring.LLM_WEIGHT`.
Без ключа скрипт печатает инструкцию для `.env` и завершается кодом 1.
"""
import argparse
import json
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from hhassist import llm, scoring, storage  # noqa: E402
from hhassist.config import get_settings  # noqa: E402

HELP = """LLM не настроен: %s.
Впишите в .env (файл рядом с cli.py): LLM_PROVIDER, LLM_MODEL, LLM_API_KEY
и повторите запуск: python tools/compare_scores.py"""


def load_vacancies(conn, limit=0):
    """Вакансии из базы, свежие первыми. Поле skills из JSON приводим к списку."""
    rows = conn.execute("SELECT * FROM vacancies ORDER BY created_at DESC").fetchall()
    items = []
    for row in rows:
        v = dict(row)
        try:
            v["skills"] = json.loads(v.get("skills") or "[]")
        except ValueError:
            v["skills"] = []
        items.append(v)
    return items[:limit] if limit else items


def compare(v, profile, settings=None):
    """Баллы одной вакансии: правила, LLM, итог и расхождение (LLM минус правила)."""
    s = settings or get_settings()
    rule = scoring.rule_score(v, profile)[0]
    answer = scoring.llm_score(v, profile, s) or {}
    model_score = answer.get("score")
    return {
        "title": v.get("title", ""),
        "company": v.get("company", ""),
        "rule": rule,
        "llm": model_score,
        "final": scoring.combine_scores(rule, model_score) if model_score is not None else rule,
        "diff": (model_score - rule) if model_score is not None else 0,
    }


def write_txt(path, lines):
    """Отчёт в файл: удобно, когда вывод терминала теряется."""
    if not path:
        return
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("written: %s" % path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Сравнить скоринг правил и LLM")
    parser.add_argument("--limit", type=int, default=15, help="сколько вакансий взять (0 — все)")
    parser.add_argument("--diff", type=int, default=15,
                        help="порог расхождения, с которого вакансия попадает в список внимания")
    parser.add_argument("--txt", help="дополнительно записать отчёт в файл")
    args = parser.parse_args(argv)

    s = get_settings()
    lines = []

    def out(text=""):
        print(text)
        lines.append(text)

    out("=== настройки ===")
    out("статус: %s" % llm.describe(s))
    if not llm.is_enabled(s):
        out()
        out(HELP % llm.describe(s))
        write_txt(args.txt, lines)
        return 1

    conn = storage.connect()
    try:
        vacancies = load_vacancies(conn, args.limit)
    finally:
        conn.close()
    if not vacancies:
        out()
        out("В базе нет вакансий — сначала: python tools/run_hh_assistant.py collect")
        write_txt(args.txt, lines)
        return 1

    out()
    out("=== сравнение (вакансий: %d, запросов к LLM: %d) ===" % (len(vacancies), len(vacancies)))
    rows = [compare(v, s.profile, s) for v in vacancies]
    rows.sort(key=lambda r: -abs(r["diff"]))
    out("%5s %5s %6s %7s  %s" % ("прав", "LLM", "итог", "разн.", "вакансия"))
    for item in rows:
        out("%5d %5s %6d %7s  %s — %s" % (
            item["rule"],
            item["llm"] if item["llm"] is not None else "—",
            item["final"],
            "%+d" % item["diff"],
            item["title"][:48],
            item["company"][:30],
        ))

    measured = [r for r in rows if r["llm"] is not None]
    out()
    out("=== сводка ===")
    if not measured:
        out("модель не дала ни одной оценки — причины видны в карточке вакансии")
        write_txt(args.txt, lines)
        return 1
    spread = [abs(r["diff"]) for r in measured]
    out("оценок от модели: %d из %d" % (len(measured), len(rows)))
    out("средний балл: правила %.1f, LLM %.1f, итог %.1f"
        % (sum(r["rule"] for r in measured) / len(measured),
           sum(r["llm"] for r in measured) / len(measured),
           sum(r["final"] for r in measured) / len(measured)))
    out("среднее расхождение: %.1f, максимум: %d"
        % (sum(spread) / len(spread), max(spread)))
    edge = [r for r in measured if abs(r["diff"]) >= args.diff]
    if edge:
        out("внимание (расхождение >= %d):" % args.diff)
        for r in edge:
            out("  %+d  %s" % (r["diff"], r["title"][:60]))
    else:
        out("расхождений >= %d нет — веса 0.45/0.55 можно оставить" % args.diff)
    write_txt(args.txt, lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
