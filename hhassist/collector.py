# -*- coding: utf-8 -*-
"""Сбор вакансий с hh.ru: страницы поиска + карточки вакансий (HTML-парсер)."""
import gzip
import html as html_lib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .config import get_settings

SEARCH_URL = "https://hh.ru/search/vacancy"
VACANCY_URL = "https://hh.ru/vacancy/%s"

_UA = None


def ua():
    global _UA
    if _UA is None:
        _UA = get_settings().user_agent
    return _UA


def http_get(url, retries=3, timeout=40):
    """GET с корректным User-Agent (требование hh), gzip и повторами."""
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": ua(),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Accept-Encoding": "gzip",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw.decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code in (403, 429):
                time.sleep(2 + attempt * 3)
            else:
                break
        except Exception as e:  # таймауты, сеть
            last = type(e).__name__
            time.sleep(1 + attempt)
    raise RuntimeError("не удалось получить %s (%s)" % (url, last))


def clean(text):
    """HTML -> текст: убрать теги, скрипты, стили, схлопнуть пробелы."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _find(text, pattern, flags=re.S):
    m = re.search(pattern, text, flags)
    return m.group(1) if m else ""


def search_ids(query, area=1, pages=1, per_page=50, pause=1.5, remote=False):
    """Возвращает список id вакансий по поисковому запросу.

    area — id региона или список id; remote=True — только удалёнка.
    """
    ids = []
    for page in range(pages):
        params = {
            "text": query,
            "area": area,
            "page": page,
            "items_on_page": per_page,
            "order_by": "publication_time",
        }
        if remote:
            params["schedule"] = "remote"
        # doseq=True: несколько регионов уходят как area=1&area=2
        url = SEARCH_URL + "?" + urllib.parse.urlencode(params, doseq=True)
        html = http_get(url)
        found = re.findall(r"/vacancy/(\d+)", html)
        for vid in found:
            if vid not in ids:
                ids.append(vid)
        time.sleep(pause)
    return ids


def _area(html_text):
    """Регион вакансии: JSON-блок area -> data-qa-локация -> сырой адрес."""
    m = re.search(r'"area"\s*:\s*\{\s*"id"\s*:\s*"?(\d+)"?\s*,\s*"name"\s*:\s*"([^"]+)"',
                  html_text)
    if m:
        return html_lib.unescape(m.group(2))[:120], m.group(1)
    name = clean(_find(html_text, r'data-qa="vacancy-view-location"[^>]*>(.*?)</span>'))
    if not name:
        name = clean(_find(html_text, r'data-qa="vacancy-view-raw-address"[^>]*>(.*?)</span>'))
        name = name.split(",")[0].strip()
    return name[:120], ""


def parse_vacancy(html_text, vacancy_id):
    """Разбирает карточку вакансии в нормализованный словарь."""
    head = clean(html_text[:120000])[:4000]
    area, area_id = _area(html_text)

    title = clean(_find(html_text, r'data-qa="vacancy-title"[^>]*>(.*?)</h1>'))
    if not title:
        title = clean(_find(html_text, r"<title>(.*?)</title>"))
        title = re.sub(r"^Вакансия\s+", "", title)
        title = re.sub(r"\s+в\s+[^,]+,?\s*работа в компании.*$", "", title).strip()

    company = ""
    m = re.search(r"работа в компании\s+([^<]+)", html_lib.unescape(html_text))
    if m:
        company = clean(m.group(1))[:120]
    if not company:
        company = clean(_find(html_text, r'data-qa="vacancy-company-name"[^>]*>(.*?)<'))

    salary_raw = clean(_find(html_text, r'data-qa="vacancy-salary"[^>]*>(.*?)</'))
    if not salary_raw:
        m = re.search(r"Уровень дохода\s*(.{0,160})", head)
        salary_raw = m.group(1) if m else ""
    salary_from = salary_to = None
    currency = ""
    nums = [int(x.replace("\u00a0", "").replace(" ", ""))
            for x in re.findall(r"(\d[\d\s\u00a0]{3,})", salary_raw)]
    if nums:
        salary_from = min(nums)
        salary_to = max(nums) if len(nums) > 1 else None
    if "₽" in salary_raw:
        currency = "RUR"
    elif "$" in salary_raw:
        currency = "USD"
    elif "€" in salary_raw:
        currency = "EUR"

    work_format = ""
    m = re.search(r'"workFormats"\s*:\s*\[(.*?)\]', html_text)
    if m:
        mapping = {"REMOTE": "удалённо", "HYBRID": "гибрид", "ON_SITE": "офис", "FIELD_WORK": "разъездная"}
        vals = re.findall(r'"([A-Z_]+)"', m.group(1))
        work_format = ", ".join(mapping.get(v, v) for v in vals)
    if not work_format:
        m = re.search(r"Формат работы:\s*(удалённо|на месте работодателя|гибрид|разъездная|полный день|сменный график)", head)
        work_format = m.group(1) if m else ""
    if not work_format:
        work_format = clean(_find(html_text, r'data-qa="work-format"[^>]*>(.*?)<'))

    experience = ""
    m = re.search(r'"workExperience"\s*:\s*"([A-Za-z0-9_]+)"', html_text)
    if m:
        mapping = {
            "noExperience": "нет опыта",
            "between1And3": "1–3 года",
            "between3And6": "3–6 лет",
            "moreThan6": "более 6 лет",
        }
        experience = mapping.get(m.group(1), m.group(1))
    if not experience:
        m = re.search(r"Опыт работы\s*:?\s*(нет опыта|1–3 года|3–6 лет|более 6 лет)", head)
        experience = m.group(1) if m else ""
    if not experience:
        experience = clean(_find(html_text, r'data-qa="vacancy-experience"[^>]*>(.*?)<'))

    published = _find(html_text, r'"publicationTimeIso"\s*:\s*"([^"]+)"') or \
                _find(html_text, r'"publicationTime"\s*:\s*"([^"]+)"')

    desc_match = re.search(r'data-qa="vacancy-description"[^>]*>(.*)', html_text, re.S)
    description = clean(desc_match.group(1)[:20000])[:4000] if desc_match else ""

    skills = []
    m = re.search(r'"keySkills"\s*:\s*\[(.*?)\]', html_text)
    if m:
        skills = re.findall(r'"([^"]{2,40})"', m.group(1))[:25]

    return {
        "id": str(vacancy_id),
        "title": title,
        "company": company,
        "url": VACANCY_URL % vacancy_id,
        "salary_from": salary_from,
        "salary_to": salary_to,
        "currency": currency,
        "work_format": work_format,
        "experience": experience,
        "published_at": published,
        "description": description,
        "skills": skills,
        "area": area,
        "area_id": area_id,
        "source": "html",
    }


def fetch_vacancy(vacancy_id, pause=1.2):
    """Скачивает и разбирает одну вакансию."""
    html_text = http_get(VACANCY_URL % vacancy_id)
    data = parse_vacancy(html_text, vacancy_id)
    if pause:
        time.sleep(pause)
    return data


def save_raw(vacancy_id, html_text, out_dir):
    """Сохраняет исходный HTML (для отладки парсера)."""
    path = "%s/%s.html" % (out_dir.rstrip("/"), vacancy_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return path


def to_json(vacancies):
    return json.dumps(vacancies, ensure_ascii=False, indent=1)


def collect(query, area=1, pages=1, per_page=50, limit=None, source=None, details=True,
            known=None, refresh_days=0, remote=False):
    """Собирает вакансии выбранным источником и возвращает (vacancies, info).

    source: "auto" (по умолчанию значение HH_SOURCE) — API hh.ru, если настроен токен
    приложения, иначе HTML; при ошибке API в режиме auto — откат на HTML.
    area — id региона или список id. remote=True — только удалёнка.
    known — {vacancy_id: время последнего сбора}: вакансии, собранные меньше
    refresh_days дней назад, заново не скачиваются (инкрементальный сбор).
    info = {source, fallback, reason, found, taken, new, refreshed, skipped, errors,
    quality} — для журнала событий.
    """
    s = get_settings()
    mode = (source or getattr(s, "hh_source", "auto") or "auto").strip().lower()
    if mode not in ("auto", "api", "html"):
        raise ValueError("источник должен быть auto|api|html, получено: %r" % mode)

    skip = _recent_ids(known, refresh_days)
    reason = ""
    if mode in ("auto", "api"):
        # локальный импорт: api_adapter импортирует clean() из этого модуля
        from . import api_adapter, hh_api

        if hh_api.has_token():
            try:
                items = hh_api.search_vacancies(query, area=area, pages=pages, per_page=per_page,
                                                remote=remote)
                found = len(items)
                skipped = sum(1 for item in items if str(item.get("id") or "") in skip)
                items = [item for item in items if str(item.get("id") or "") not in skip]
                if limit:
                    items = items[:limit]
                vacancies, errors = [], []
                for item in items:
                    vid = str(item.get("id") or "")
                    full = item
                    if details and vid:
                        try:
                            full = hh_api.get_vacancy(vid)
                        except hh_api.HHApiError as e:
                            errors.append({"id": vid, "error": str(e)})
                    vacancies.append(api_adapter.vacancy_from_api(full))
                return vacancies, _info("api", False, "", found, len(items), skipped,
                                        errors, vacancies, known)
            except hh_api.HHApiError as e:
                if mode == "api":
                    raise
                reason = str(e)
        elif mode == "api":
            raise RuntimeError("HH_SOURCE=api, но токен не настроен: "
                               "HH_APP_TOKEN или HH_CLIENT_ID + HH_CLIENT_SECRET")
        else:
            reason = "токен hh не настроен"

    ids = search_ids(query, area=area, pages=pages, per_page=per_page, remote=remote)
    found = len(ids)
    skipped = sum(1 for vid in ids if vid in skip)
    ids = [vid for vid in ids if vid not in skip]
    if limit:
        ids = ids[:limit]
    vacancies, errors = [], []
    for vid in ids:
        try:
            vacancies.append(fetch_vacancy(vid))
        except Exception as e:
            errors.append({"id": vid, "error": "%s: %s" % (type(e).__name__, e)})
    return vacancies, _info("html", bool(reason), reason, found, len(ids), skipped,
                            errors, vacancies, known)


def _recent_ids(known, refresh_days):
    """id вакансий, собранных меньше refresh_days дней назад: заново не скачиваем."""
    if not known or not refresh_days:
        return set()
    cutoff = (datetime.now() - timedelta(days=int(refresh_days))).isoformat()
    return {vid for vid, ts in known.items() if ts and ts >= cutoff}


def quality(vacancies, taken=None):
    """Здоровье парсера: ok | suspect | empty | no_results.

    suspect — данные пришли, но разбор похож на сломанный: у большинства вакансий
    пустой заголовок или подозрительно короткое описание.
    """
    taken = len(vacancies) if taken is None else taken
    if not taken:
        return {"state": "no_results", "taken": 0, "empty_titles": 0, "short_descriptions": 0}
    total = len(vacancies)
    empty_titles = sum(1 for v in vacancies if not (v.get("title") or "").strip())
    short = sum(1 for v in vacancies if v.get("description") and len(v["description"]) < 100)
    state = "ok"
    if not total:
        state = "empty"
    elif empty_titles >= 3 and empty_titles * 2 > total:
        state = "suspect"
    elif short >= 3 and short * 2 > total:
        state = "suspect"
    return {"state": state, "taken": taken, "empty_titles": empty_titles,
            "short_descriptions": short}


def _info(source, fallback, reason, found, taken, skipped, errors, vacancies, known):
    """info-словарь прогона: источник, счётчики нового/обновлённого, качество разбора."""
    known = known or {}
    refreshed = sum(1 for v in vacancies if v.get("id") in known)
    return {"source": source, "fallback": fallback, "reason": reason, "found": found,
            "taken": taken, "new": len(vacancies) - refreshed, "refreshed": refreshed,
            "skipped": skipped, "errors": errors, "quality": quality(vacancies, taken)}


