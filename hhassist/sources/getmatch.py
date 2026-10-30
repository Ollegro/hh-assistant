# -*- coding: utf-8 -*-
"""getmatch.ru: карточки вакансий из sitemap, без браузера.

Список вакансий у площадки рендерится в браузере, поэтому берём официальный
sitemap (`robots.txt` разрешает обход и указывает его), фильтруем адреса
`/vacancies/<id>-<слаг>` по ключевым словам запроса и читаем карточки — они
отдаются сервером полностью (заголовок, компания, город, формат, уровень, текст).

Разбор карточки эвристический: по тексту страницы, а не по именам классов
(они меняются при редизайне). Если шаблон переделают, парсер вернёт меньше полей —
это видно по предупреждению «похоже, парсер сломался».
"""
import gzip
import html as html_lib
import io
import os
import re
import time
import urllib.request

from .base import make

NAME = "getmatch"
SITEMAP = "https://getmatch.ru/sitemap.xml"
CACHE_HOURS = 12
UA = "hh-assistant/0.1 (o.gutsev@mail.ru)"


def _http(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return raw.decode("utf-8", "ignore")


def _cache_file():
    from ..config import DATA_DIR
    return os.path.join(DATA_DIR, "cache", "getmatch_sitemap.xml")


def sitemap(force=False):
    """Текст sitemap с кэшем на 12 часов (файл 5–6 МБ, качать каждый раз накладно)."""
    path = _cache_file()
    if not force and os.path.isfile(path):
        if time.time() - os.path.getmtime(path) < CACHE_HOURS * 3600:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                return fh.read()
    body = _http(SITEMAP)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return body


def vacancy_urls(force=False):
    """Адреса карточек вакансий из sitemap (страницы-фильтры отбрасываем)."""


def _experience(text):
    match = re.search(r"Требуемый опыт\s*([0-9]+)\s*(\+)?\s*лет", text)
    if match:
        years = int(match.group(1))
        if match.group(2):          # «6 + лет» — это «более 6»
            return "более 6 лет"
        if years <= 3:
            return "1–3 года"
        if years <= 6:
            return "3–6 лет"
        return "более 6 лет"
    return "нет опыта" if "нет опыта" in text.lower() else ""


def _salary(text):
    match = re.search(r"(?:от\s*)?(\d[\d\s\u00a0]{3,})\s*(?:до\s*(\d[\d\s\u00a0]{3,})\s*)?₽", text)
    if not match:
        return None, None, ""
    low = int(re.sub(r"[^\d]", "", match.group(1)))
    high = int(re.sub(r"[^\d]", "", match.group(2))) if match.group(2) else None
    return low, high, "RUR"


def parse_card(html_text, url=""):
    """Карточка getmatch -> нормализованная вакансия (+ признак «в архиве»)."""
    text = _plain(html_text)
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", html_text or "", re.S)
    title = _plain(title_match.group(1)) if title_match else ""
    if not title:
        raw_title = re.search(r"<title>(.*?)</title>", html_text or "", re.S)
        title = re.sub(r"\s*—\s*getmatch.*$", "", _plain(raw_title.group(1) if raw_title else ""))
    company = ""
    match = re.search(r"работа в ([^,]{2,80}),", text)
    if match:
        company = match.group(1).strip()
    work_format = ""
    for word, value in (("Удалённо", "удалённо"), ("Гибрид", "гибрид"), ("Офис", "офис")):
        if word in text:
            work_format = value
            break
    low, high, currency = _salary(text)

    description = ""
    start = text.find("Поделиться")
    if start != -1:
        tail = text[start + len("Поделиться"):]
        cut = re.search(r"Похожие вакансии|Похожие предложения|Откликнуться", tail)
        description = (tail[:cut.start()] if cut else tail[:4000]).strip()

    vacancy = make(
        NAME,
        (re.search(r"/vacancies/(\d+)", url) or [None, ""])[1],
        title, company, url,
        salary_from=low,
        salary_to=high,
        currency=currency,
        work_format=work_format,
        experience=_experience(text),
        description=description[:4000],
    )
    vacancy["archived"] = "Вакансия в архиве" in text
    return vacancy


def search(query, area="", pages=1, per_page=50, pause=1.0, remote=False):
    """Поиск: ключевые слова — по адресам карточек, формат — по тексту."""
    tokens = [token for token in re.split(r"[^a-zа-яё0-9]+", (query or "").lower())
              if len(token) >= 3]
    urls = vacancy_urls()
    if tokens:
        urls = [url for url in urls if any(token in url.lower() for token in tokens)]

    limit = max(1, min(int(per_page or 50), 50))
    result = []
    for url in urls[:limit]:
        try:
            vacancy = parse_card(_http(url), url)
        except Exception:  # одна карточка не должна ронять прогон
            continue
        if vacancy.pop("archived", False):
            continue
        if remote and vacancy["work_format"] and vacancy["work_format"] != "удалённо":
            continue
        result.append(vacancy)
        if pause:
            time.sleep(pause)
    return result

    locs = re.findall(r"<loc>([^<]+)</loc>", sitemap(force=force))
    return [url for url in locs if re.search(r"/vacancies/\d+-", url)]


def _plain(html_text):
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", html_text or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()
