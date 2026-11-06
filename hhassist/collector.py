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
# Сортировка выдачи hh по умолчанию: пустая строка — релевантность (как на сайте).
# "publication_time" — по дате; так свежие вакансии оказываются в конце выдачи:
# 06.11.2026 нужная вакансия была 244-й из 876 и в первую страницу не попадала.
DEFAULT_ORDER = ""

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
    """HTML -> текст: убрать теги, скрипты, стили, схлопнуть пробелы.

    Годится только для однострочных полей (заголовок, компания, зарплата).
    Описание вакансии разбирает `html_to_text`: там важны абзацы и списки.
    """
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# Блоки, которые в описании не текст: их содержимое (JS, стили, фреймы) выбрасываем.
DROP_BLOCKS = re.compile(r"<(script|style|noscript|iframe|svg|template)\b.*?</\1>",
                         re.S | re.I)
# Маркеры скриптов hh: если тег <script> обрезался лимитом, ловим начало кода.
JS_MARKERS = re.compile(r"<script\b|window\.jquery|jQuery[.\w]*\s*=|!function\s*\(", re.I)
# Блочные теги: на их границах в тексте появляется перевод строки.
BLOCK_BREAK = re.compile(r"</?(?:p|div|br|li|ul|ol|tr|td|th|h[1-6]|section|article|"
                         r"blockquote|table)\b[^>]*>", re.I)


def html_to_text(fragment):
    """HTML -> читаемый текст: абзацы, списки и «• » сохраняются.

    Раньше описание шло через `clean()`: он схлопывал все пробелы, и текст
    вакансии превращался в одно полотно («…иностранных языков; Карьерный
    рост: …» подряд). Теперь границы блоков становятся переводами строк
    (каждый абзац и пункт — с новой строки), пустые строки убираются.
    """
    text = DROP_BLOCKS.sub(" ", fragment or "")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<li\b[^>]*>", "\n• ", text, flags=re.I)
    text = BLOCK_BREAK.sub("\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _balanced_div_end(text, start):
    """Конец <div>, начинающегося не раньше start, по балансу вложенных div.

    -1 — закрывающего тега не нашлось (битая или обрезанная разметка).
    """
    depth = 0
    for match in re.finditer(r"<div\b|</div\s*>", text[start:], re.I):
        if match.group(0)[1] == "/":
            depth -= 1
            if depth <= 0:
                return start + match.end()
        else:
            depth += 1
    return -1


def description_of(html_text, limit=4000):
    """Описание вакансии из карточки hh: только текст блока, без скриптов.

    Раньше regex брал всё до конца документа, и хвост страницы (jQuery, swiper)
    попадал в описание: 10 вакансий из 147 содержали JS вместо текста. Теперь
    фрагмент ограничен закрытием блока `data-qa="vacancy-description"`.
    """
    marker = 'data-qa="vacancy-description"'
    pos = html_text.find(marker)
    if pos == -1:
        return ""
    start = html_text.rfind("<div", 0, pos)
    start = 0 if start == -1 else start
    end = _balanced_div_end(html_text, start)
    fragment = html_text[start:end] if end > start else html_text[start:start + 20000]
    # страховка: если блок обрезан и <script> остался незакрытым — режем по нему
    fragment = JS_MARKERS.split(fragment, maxsplit=1)[0]
    text = html_to_text(fragment)
    if len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        text = text[:cut if cut > limit * 0.6 else limit].rstrip()
    return text


def _find(text, pattern, flags=re.S):
    m = re.search(pattern, text, flags)
    return m.group(1) if m else ""


def search_ids(query, area=1, pages=1, per_page=50, pause=1.5, remote=False, order_by=None):
    """Возвращает список id вакансий по поисковому запросу.

    area — id региона или список id; remote=True — только удалёнка.
    order_by — сортировка выдачи hh: None — релевантность (как на сайте по умолчанию),
    "publication_time" — по дате. По дате свежие вакансии уезжают в конец: 06.11.2026
    нужная вакансия стояла 244-й из 876 и в первую страницу не попадала, поэтому по
    умолчанию берём релевантность, а порядок настраивается через COLLECT_ORDER.
    """
    ids = []
    for page in range(pages):
        params = {
            "text": query,
            "area": area,
            "page": page,
            "items_on_page": per_page,
        }
        # Пустой order_by не отправляем: hh по умолчанию сортирует по релевантности,
        # а лишний параметр только засоряет запрос.
        if order_by or DEFAULT_ORDER:
            params["order_by"] = order_by or DEFAULT_ORDER
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


def _flat(text):
    """Страница с раскрытыми экранированными кавычками.

    hh отдаёт один и тот же JSON дважды: обычным текстом и строкой, где кавычки
    записаны как `\\"` или `&#34;`. Для разбора места работы приводим всё к
    одному виду, иначе блок `"area"` не находится.
    """
    if '\\"' not in text and "&#34;" not in text and "&quot;" not in text:
        return text
    return (text.replace("\\&#34;", '"').replace("&#34;", '"')
                .replace("&quot;", '"').replace('\\"', '"'))


def _area(html_text):
    """(город, id региона, субъект РФ) вакансии.

    Главный источник — блок самой вакансии `"area": {"@id", "name", "path"}`:
    именно он определяет регион в поиске hh (у «Главного технолога» area —
    Донецк). `address` в карточке — адрес работодателя, он может быть в другом
    регионе (address.city = «Московская область»), поэтому город берётся из
    area, а JSON-LD `addressLocality`/`addressRegion` и `data-qa` остаются
    запасными вариантами.
    """
    flat = _flat(html_text)
    city = region = ""
    area_id = area_name = ""
    with_path = r'"area"\s*:\s*\{\s*"@?id"\s*:\s*"?(\d+)"?\s*,\s*"name"\s*:\s*"([^"]+)"\s*,\s*"path"\s*:\s*"([^"]*)"'
    title_text = html_lib.unescape(_find(html_text, r"<title[^>]*>(.*?)</title>"))
    # path вида ".113.2134.118." должен заканчиваться тем же id — так отсеиваются
    # чужие блоки area (справочники регионов, похожие вакансии).
    valid = [m for m in re.finditer(with_path, flat)
             if m.group(3).rstrip(".").rsplit(".", 1)[-1] == m.group(1)]
    chosen = None
    for m in valid:
        # В заголовке страницы hh пишет город: «Вакансия … в Донецке». Если имя
        # блока встречается в заголовке, это область самой вакансии, а не
        # справочник регионов выше по странице.
        if not chosen or m.group(2) in title_text:
            chosen = m
        if m.group(2) in title_text:
            break
    if chosen is not None:
        area_id = chosen.group(1)
        area_name = html_lib.unescape(chosen.group(2))[:120]
    elif not valid:
        # старый формат блока без path: объект закрывается сразу после name
        m = re.search(r'"area"\s*:\s*\{\s*"@?id"\s*:\s*"?(\d+)"?\s*,\s*"name"\s*:\s*"([^"]+)"\s*\}',
                      flat)
        if m:
            area_id = m.group(1)
            area_name = html_lib.unescape(m.group(2))[:120]
    m = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', flat)
    if m:
        city = html_lib.unescape(m.group(1))[:120]
    m = re.search(r'"addressRegion"\s*:\s*"([^"]+)"', flat)
    if m:
        region = html_lib.unescape(m.group(1))[:120]
    if area_name:
        city = area_name
    if not city:
        city = clean(_find(html_text, r'data-qa="vacancy-view-location"[^>]*>(.*?)</span>'))
    if not city:
        city = clean(_find(html_text, r'data-qa="vacancy-view-raw-address"[^>]*>(.*?)</span>'))
        city = city.split(",")[0].strip()
    return city[:120], area_id, region[:120]


COMPANY_JUNK = ("vacancyview", "&quot;", '":', "{", "}", "function(", "window.")


def _clean_company(value):
    """Название компании: отсекаем склейки из служебного JSON страницы hh.

    Регулярка «работа в компании …» иногда уезжает в JS-конфиг вакансии и тянет
    оттуда кусок вида `{0}","vacancyview.titleCompensation":…`. Такая «компания»
    растягивала таблицу и ломала вёрстку страницы (видно было на второй странице).
    """
    text = clean(value or "")
    if not text:
        return ""
    low = text.lower()
    if any(marker in low for marker in COMPANY_JUNK):
        return ""
    if len(text) > 40 and " " not in text:
        return ""
    return text[:120]


# Формат работы из карточки кодируется по-разному: JSON `workFormats`, строка
# «Формат работы: …» и подпись `data-qa="work-format"`. hh может поставить
# ON_SITE («на месте работодателя»), хотя в тексте вакансии указан гибрид —
# тогда верим тексту и JSON-LD, иначе правило профиля штрафует подходящую вакансию.
FORMAT_MAP = {"REMOTE": "удалённо", "HYBRID": "гибрид", "ON_SITE": "офис",
              "FIELD_WORK": "разъездная", "ON_SITE_OFFICE": "офис"}
FORMAT_TEXT_FROM_TEXT = {"на месте работодателя": "офис", "полный день": "полный день",
                         "сменный график": "сменный график"}


def json_ld(html_text):
    """JSON-LD карточки (`JobPosting`) как словарь: datePosted, employmentType, baseSalary…

    hh кладёт в этот блок то, чего нет в анонимном HTML-тексте: дату публикации и,
    когда работодатель её показал, вилку зарплаты. Пустой словарь — блок не найден
    или разметка битая.
    """
    for raw in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                          html_text, re.S):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, dict) and data:
            return data
    return {}


def _salary(html_text, head):
    """(from, to, currency): разметка карточки, JSON-LD, строка «Уровень дохода».

    Анонимно hh часто пишет «Уровень дохода не указан», хотя в JSON-LD `baseSalary`
    бывает заполнен — поэтому источник идёт вторым по приоритету.
    """
    candidates = [clean(_find(html_text, r'data-qa="vacancy-salary"[^>]*>(.*?)</')),
                  head and (re.search(r"Уровень дохода\s*(.{0,160})", head).group(1)
                            if re.search(r"Уровень дохода\s*(.{0,160})", head) else "")]
    bank = json_ld(html_text).get("baseSalary") or {}
    if isinstance(bank, dict):
        value = bank.get("value") if isinstance(bank.get("value"), dict) else bank
        low = value.get("minValue", value.get("value") if isinstance(value, dict) else None)
        high = value.get("maxValue")
        currency = str(bank.get("currency") or value.get("currency") or "").upper()
        if low or high:
            nums = [int(n) for n in (low, high) if isinstance(n, (int, float))]
            return min(nums), (max(nums) if len(nums) > 1 else None), \
                {"RUR": "RUR", "RUB": "RUR", "USD": "USD", "EUR": "EUR"}.get(currency, "")
    for raw in candidates:
        if not raw or "не указан" in raw.lower():
            continue
        nums = [int(x.replace("\u00a0", "").replace(" ", ""))
                for x in re.findall(r"(\d[\d\s\u00a0]{3,})", raw)]
        if not nums:
            continue
        currency = ("RUR" if "₽" in raw or "руб" in raw.lower() else
                    "USD" if "$" in raw else "EUR" if "€" in raw else "")
        return min(nums), (max(nums) if len(nums) > 1 else None), currency
    return None, None, ""


def _work_format(html_text, head):
    """Формат работы: «Формат работы: …» → JSON-LD → workFormats → data-qa.

    Возвращает строку вида «гибрид, офис»: если работодатель указал в тексте гибрид,
    а разметка говорит «на месте работодателя», сохраняются оба значения — правило
    профиля найдёт «гибрид» и не оштрафует вакансию.
    """
    found = []
    m = re.search(r"Формат работы:\s*(удалённо|на месте работодателя|гибрид|разъездная|"
                  r"полный день|сменный график)", head)
    if m:
        found.append(FORMAT_TEXT_FROM_TEXT.get(m.group(1), m.group(1)))
    bank = json_ld(html_text)
    employment = str(bank.get("employmentType") or "").upper()
    found += [FORMAT_MAP[e] for e in re.findall(r"[A-Z_]+", employment) if e in FORMAT_MAP]
    if str(bank.get("jobLocationType") or "").upper() == "TELECOMMUTE" or m and \
            m.group(1) == "удалённо":
        found.append("удалённо")
    m = re.search(r'"workFormats"\s*:\s*\[(.*?)\]', html_text)
    if m:
        found += [FORMAT_MAP[v] for v in re.findall(r'"([A-Z_]+)"', m.group(1))
                  if v in FORMAT_MAP]
    if not found:
        label = clean(_find(html_text, r'data-qa="work-format"[^>]*>(.*?)<'))
        if label:
            found.append(FORMAT_TEXT_FROM_TEXT.get(label.lower(), label.lower()))
    return ", ".join(dict.fromkeys(v for v in found if v))


def parse_vacancy(html_text, vacancy_id):
    """Разбирает карточку вакансии в нормализованный словарь."""
    head = clean(html_text[:120000])[:4000]
    area, area_id, region = _area(html_text)

    title = clean(_find(html_text, r'data-qa="vacancy-title"[^>]*>(.*?)</h1>'))
    if not title:
        title = clean(_find(html_text, r"<title>(.*?)</title>"))
        title = re.sub(r"^Вакансия\s+", "", title)
        title = re.sub(r"\s+в\s+[^,]+,?\s*работа в компании.*$", "", title).strip()

    company = ""
    m = re.search(r"работа в компании\s+([^<]{1,120})", html_lib.unescape(html_text))
    if m:
        company = _clean_company(m.group(1))
    if not company:
        company = _clean_company(_find(html_text, r'data-qa="vacancy-company-name"[^>]*>(.*?)<'))

    salary_from, salary_to, currency = _salary(html_text, head)

    work_format = _work_format(html_text, head)

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
                _find(html_text, r'"publicationTime"\s*:\s*"([^"]+)"') or \
                str(json_ld(html_text).get("datePosted") or "")

    description = description_of(html_text)

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
        "region": region,
        "source": "html",
    }


def _collect_external(name, query, area, pages, per_page, limit, known, refresh_days, remote):
    """Сбор с внешней площадки (Хабр Карьера, SuperJob, Работа России, getmatch).

    Формат вакансии тот же, что у hh, поэтому скоринг и письма работают без правок;
    отличается только `id` — с префиксом площадки, чтобы записи не сталкивались.
    Сортировка `order_by` у площадок своя, поэтому параметр hh сюда не передаётся.
    """
    from . import sources as external

    s = get_settings()
    pause = float(getattr(s, "delay_seconds", 0) or 0) or 1.0
    found = external.collect(name, query, area=area, pages=pages, per_page=per_page,
                             pause=pause, remote=remote)
    skip = _recent_ids(known, refresh_days)
    skipped = sum(1 for vacancy in found if str(vacancy.get("id")) in skip)
    vacancies = [vacancy for vacancy in found if str(vacancy.get("id")) not in skip]
    if limit:
        vacancies = vacancies[:limit]
    return vacancies, _info(name, False, "", len(found), len(vacancies), skipped, [],
                            vacancies, known)


def fetch_vacancy(vacancy_id, pause=1.2):
    """Скачивает и разбирает одну вакансию."""
    html_text = http_get(VACANCY_URL % vacancy_id)
    data = parse_vacancy(html_text, vacancy_id)
    if pause:
        time.sleep(pause)
    return data


def collect(query, area=1, pages=1, per_page=50, limit=None, source=None, details=True,
            known=None, refresh_days=0, remote=False, order_by=None):
    """Собирает вакансии выбранным источником и возвращает (vacancies, info).

    source: "auto" (по умолчанию значение HH_SOURCE) — API hh.ru, если настроен токен
    приложения, иначе HTML; при ошибке API в режиме auto — откат на HTML.
    Дополнительно принимаются внешние площадки из `hhassist.sources`
    (habr, superjob, trudvsem, getmatch) — каждая со своим парсером.
    area — id региона или список id. remote=True — только удалёнка.
    order_by — сортировка выдачи hh для HTML-сбора (None — из COLLECT_ORDER).
    known — {vacancy_id: время последнего сбора}: вакансии, собранные меньше
    refresh_days дней назад, заново не скачиваются (инкрементальный сбор).
    info = {source, fallback, reason, found, taken, new, refreshed, skipped, errors,
    quality} — для журнала событий.
    """
    s = get_settings()
    mode = (source or getattr(s, "hh_source", "auto") or "auto").strip().lower()
    if mode not in ("auto", "api", "html"):
        from . import sources as external

        if not external.get(mode):
            raise ValueError("источник должен быть auto|api|html|%s, получено: %r"
                             % ("|".join(external.SOURCES), mode))
        return _collect_external(mode, query, area=area, pages=pages, per_page=per_page,
                                 limit=limit, known=known, refresh_days=refresh_days,
                                 remote=remote)

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

    ids = search_ids(query, area=area, pages=pages, per_page=per_page, remote=remote,
                     order_by=order_by)
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


