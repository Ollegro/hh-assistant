# -*- coding: utf-8 -*-
"""Хабр Карьера (career.habr.com): публичные страницы вакансий, без авторизации.

Официального API поиска у площадки нет, поэтому читаем те же страницы, что видит
пользователь: список `/vacancies?q=…&page=N` и карточку `/vacancies/<id>`.
Парсеры разделены (`parse_list`, `parse_card`), чтобы их можно было проверять
на сохранённых фикстурах без сети.
"""
import html as html_lib
import re
import time
from urllib.parse import urlencode

from .base import make

NAME = "habr"
LIST_URL = "https://career.habr.com/vacancies"
CARD_URL = "https://career.habr.com/vacancies/%s"
MAX_PER_PAGE = 25

# «Можно удалённо», «Офис», «Гибрид» — формат в чипах карточки
FORMAT_WORDS = (("можно удалённо", "удалённо"), ("удалённо", "удалённо"),
                ("гибрид", "гибрид"), ("офис", "офис"))
GRADES = ("Intern", "Junior", "Middle", "Senior", "Lead", "Principal")


def _text(fragment):
    """HTML -> текст: теги вон, сущности раскрыть, пробелы схлопнуть."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _salary(text):
    """«От 180 000 до 220 000 ₽» -> (180000, 220000, 'RUR')."""
    numbers = [int(re.sub(r"[^\d]", "", chunk))
               for chunk in re.findall(r"\d[\d\s\u00a0]{3,}", text or "")]
    numbers = [n for n in numbers if n]
    if not numbers:
        return None, None, ""
    low = min(numbers)
    high = max(numbers) if len(numbers) > 1 else None
    currency = "RUR" if "₽" in (text or "") else ""
    return low, high, currency



def parse_list(html_text):
    """Страница поиска -> список вакансий (без описания)."""
    items = []
    # карточки списка: <div class="vacancy-card …"> … <a … href="/vacancies/123" …>
    for block in re.split(r'(?=<div class="vacancy-card(?:\s|">))', html_text or ""):
        if 'class="vacancy-card' not in block:
            continue
        link = re.search(r'href="/vacancies/(\d+)"', block)
        if not link:
            continue
        vid = link.group(1)
        title = re.search(r'aria-label="([^"]+)"', block)
        if not title:
            title = re.search(
                r'class="[^"]*vacancy-card__title[^"]*"[^>]*>\s*(?:<a[^>]*>)?([^<]+)', block)
        company = re.search(r'class="link-comp[^"]*"[^>]*>([^<]+)</a>', block)
        date = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        salary_text = re.search(r'class="[^"]*vacancy-card__salary[^"]*"[^>]*>([^<]+)<', block)
        low, high, currency = _salary(salary_text.group(1) if salary_text else "")
        vacancy = make(
            NAME, vid,
            _text(title.group(1)) if title else "",
            _text(company.group(1)) if company else "",
            CARD_URL % vid,
            salary_from=low,
            salary_to=high,
            currency=currency,
            published_at=(date.group(1) if date else "")[:10],
        )
        if vacancy["title"]:
            items.append(vacancy)
    return items


def parse_card(html_text):
    """Карточка вакансии -> {title, description, зарплата, формат, грейд}."""
    result = {}
    title = re.search(r'class="page-title__title"[^>]*>(.*?)</h1>', html_text or "", re.S)
    if title:
        result["title"] = _text(title.group(1))

    marker = "vacancy-description__text"
    pos = html_text.find(marker)
    description = ""
    if pos != -1:
        start = html_text.find("<div", pos)
        end = html_text.find("</section>", start)
        description = _text(html_text[start:end if end > start else start + 20000])
    result["description"] = description[:4000]

    # зарплата, формат и грейд — в шапке карточки, до блока «Описание вакансии»
    head_text = _text(html_text[:pos] if pos > 0 else html_text[:20000])
    salary_match = re.search(r"(?:от\s*\d[\d\s\u00a0]*)?\s*(?:до\s*\d[\d\s\u00a0]*)?\s*₽",
                             head_text, re.I)
    if salary_match:
        low, high, currency = _salary(salary_match.group(0))
        result.update({"salary_from": low, "salary_to": high, "currency": currency})
    lowered = head_text.lower()
    for marker_word, value in FORMAT_WORDS:
        if marker_word in lowered:
            result["work_format"] = value
            break
    for grade in GRADES:
        if re.search(r"\b%s\b" % grade, head_text):
            result["experience"] = grade
            break
    return result


def search(query, area="", pages=1, per_page=50, pause=1.0, remote=False):
    """Поиск по Хабр Карьере: страницы списка + карточки первых вакансий."""
    from .. import collector

    limit = max(1, min(int(per_page or MAX_PER_PAGE), MAX_PER_PAGE))
    found = []
    for page in range(1, max(1, int(pages or 1)) + 1):
        url = LIST_URL + "?" + urlencode({"q": query or "", "type": "all", "page": page})
        items = parse_list(collector.http_get(url))
        if not items:
            break
        found.extend(items)
        if len(items) < MAX_PER_PAGE:
            break
        if pause:
            time.sleep(pause)

    result = []
    for vacancy in found[:limit]:
        if not vacancy["url"]:
            continue
        try:
            card = parse_card(collector.http_get(vacancy["url"]))
        except Exception:  # одна карточка не должна ронять прогон
            card = {}
        if card.get("title") and not vacancy["title"]:
            vacancy["title"] = card["title"]
        for key in ("description", "salary_from", "salary_to", "currency",
                    "work_format", "experience"):
            if card.get(key) not in (None, "", []):
                vacancy[key] = card[key]
        if remote and vacancy["work_format"] and vacancy["work_format"] != "удалённо":
            continue
        result.append(vacancy)
        if pause:
            time.sleep(pause)
    return result
