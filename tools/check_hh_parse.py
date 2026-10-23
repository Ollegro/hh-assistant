# -*- coding: utf-8 -*-
"""Диагностика парсера: показывает, какие маркеры есть в HTML вакансии.

Запуск: python tools/check_hh_parse.py 138516835
Результат: %TEMP%\\hh_parse_check.txt (UTF-8)
"""
import os
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from hhassist import collector  # noqa: E402

vid = sys.argv[1] if len(sys.argv) > 1 else "138516835"
html = collector.http_get(collector.VACANCY_URL % vid)

checks = {
    "<title>": "<title>",
    "vacancy-title": 'data-qa="vacancy-title"',
    "workFormats": '"workFormats"',
    "work-format": 'data-qa="work-format"',
    "vacancy-view-employment-mode": 'vacancy-view-employment-mode',
    "vacancy-salary": 'data-qa="vacancy-salary"',
    "vacancy-description": 'data-qa="vacancy-description"',
    "workExperience": '"workExperience"',
}

lines = ["size: %d" % len(html)]
for name, marker in checks.items():
    i = html.find(marker)
    lines.append("%-32s %s" % (name, "найден @%d" % i if i >= 0 else "НЕ найден"))
    if i >= 0:
        lines.append("    " + html[i:i + 300].replace("\n", " "))

# отдельно: первые 400 символов <title>
i = html.find("<title>")
if i >= 0:
    lines.append("title raw: " + html[i:i + 400].replace("\n", " "))

parsed = collector.parse_vacancy(html, vid)
lines.append("parsed: " + repr(parsed)[:1200])

out = os.path.join(tempfile.gettempdir(), "hh_parse_check.txt")
open(out, "w", encoding="utf-8").write("\n".join(lines))
print("written:", out)
