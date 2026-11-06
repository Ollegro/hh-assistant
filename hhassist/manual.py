# -*- coding: utf-8 -*-
"""Ручное добавление вакансии: ссылка на hh.ru + текст описания.

Зачем: сбор по поисковым запросам видит не всё. Вакансию присылают в мессенджере,
она висит в чужом чате или найдена на другой площадке — её нужно внести руками,
а дальше оценивать и писать письмо как обычную.

Что делает модуль:
  * достаёт id из ссылки (`hh.ru/vacancy/136901954` → `136901954`);
  * пробует скачать карточку (API при наличии токена, иначе HTML) и дополнить поля;
  * если сети нет или ссылка не на hh.ru — работает по тому, что ввёл человек;
  * даёт стабильный синтетический id, чтобы повторное добавление не создавало дубль.
"""
import hashlib
import re
import urllib.parse

from . import collector

HH_ID_RE = re.compile(r"(?:^|/)(?:vacancy|vacancies)/(\d{6,})")
DIGITS_RE = re.compile(r"^\s*(\d{6,})\s*$")
URL_RE = re.compile(r"^(?:https?://|www\.)", re.IGNORECASE)
MANUAL_PREFIX = "manual:"
HH_HOSTS = ("hh.ru", "www.hh.ru", "hh.kz", "hh.uz", "rabota.by")


def _urlobj(text):
    """urlsplit с добавлением схемы: 'hh.ru/vacancy/1' тоже должно разбираться."""
    value = text if "//" in text else "https://" + text
    try:
        return urllib.parse.urlsplit(value)
    except ValueError:
        return None


def parse_hh_id(value):
    """id вакансии hh из ссылки; '' — если это не ссылка на hh.ru и не номер."""
    text = (value or "").strip()
    if not text:
        return ""
    digits = DIGITS_RE.match(text)
    if digits:
        return digits.group(1)
    if not URL_RE.match(text):
        return ""
    parts = _urlobj(text)
    if parts is None:
        return ""
    if (parts.netloc or "").lower().split(":")[0] not in HH_HOSTS:
        return ""
    match = HH_ID_RE.search(parts.path or "")
    return match.group(1) if match else ""


def synthetic_id(url):
    """Стабильный внутренний id для вакансии без hh-номера."""
    raw = (url or "").strip().lower()
    if not raw:
        return ""
    return MANUAL_PREFIX + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _clean(text):
    """Однострочные поля: без переводов строк и лишних пробелов."""
    return " ".join((text or "").replace("\r", " ").replace("\n", " ").split())


def _salary(value):
    """'120000', '120 000', '120000-150000' → (from, to); иначе (None, None)."""
    numbers = []
    for part in re.findall(r"\d[\d\s]{2,}", value or ""):
        digits = part.replace(" ", "")
        if digits.isdigit() and int(digits) >= 1000:
            numbers.append(int(digits))
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], None
    return min(numbers[0], numbers[1]), max(numbers[0], numbers[1])


def build(url="", title="", company="", description="", salary="", currency="RUB",
          work_format="", experience="", area="", region=""):
    """Словарь вакансии из того, что ввёл человек (без сети)."""
    url = (url or "").strip()
    salary_from, salary_to = _salary(salary)
    vacancy_id = (parse_hh_id(url) or synthetic_id(url)
                  or synthetic_id(_clean(title) + "\n" + _clean(company)))
    return {
        "id": vacancy_id,
        "title": _clean(title),
        "company": _clean(company),
        "url": url,
        "salary_from": salary_from,
        "salary_to": salary_to,
        "currency": (currency or "RUB").strip().upper(),
        "work_format": _clean(work_format),
        "experience": _clean(experience),
        "published_at": "",
        "description": (description or "").strip(),
        "skills": [],
        "source": "manual",
        "area": _clean(area),
        "area_id": "",
        "region": _clean(region or area),
    }


def _from_hh(hh_id, fetch):
    """Карточка hh: (поля, '') или ({}, причина). Сеть здесь единственная точка отказа."""
    try:
        data = fetch(hh_id)
    except Exception as exc:                      # сеть, капча, смена разметки
        return {}, "%s: %s" % (type(exc).__name__, exc)
    if not isinstance(data, dict) or not (data.get("title") or data.get("company")):
        return {}, "карточка не распознана"
    return data, ""


# Поля, которые дополняем с hh, если человек их не заполнил
FILL_FIELDS = ("title", "company", "salary_from", "salary_to", "currency", "work_format",
               "experience", "published_at", "description", "skills", "area", "area_id",
               "region")


def add(url="", title="", company="", description="", salary="", currency="RUB",
        work_format="", experience="", area="", region="", fetch=None):
    """Вакансия для сохранения: приоритет у введённого вручную.

    Если человек заполнил название или компанию, они не затираются данными со страницы;
    пустые поля дополняются с hh. Возвращает (vacancy, note) — note для интерфейса.
    """
    if fetch is None:
        fetch = _fetch_hh
    vacancy = build(url=url, title=title, company=company, description=description,
                    salary=salary, currency=currency, work_format=work_format,
                    experience=experience, area=area, region=region)
    note = ""
    hh_id = parse_hh_id(url)
    if hh_id:
        fetched, reason = _from_hh(hh_id, fetch)
        if fetched:
            for key in FILL_FIELDS:
                current = vacancy.get(key)
                value = fetched.get(key)
                if current in (None, "", []) and value not in (None, "", []):
                    vacancy[key] = value
            vacancy["id"] = hh_id
            note = "данные дополнены с hh.ru"
        else:
            note = "карточку hh получить не удалось (%s) — сохранено как введено" % reason
    elif vacancy["id"].startswith(MANUAL_PREFIX):
        note = "ссылка не на hh.ru — сохранено как введено, id внутренний"
    return vacancy, note


def _fetch_hh(hh_id):
    """Карточка с hh: API при наличии токена, иначе HTML-парсер."""
    from . import hh_api
    if hh_api.has_token():
        try:
            from . import api_adapter
            return api_adapter.vacancy_from_api(hh_api.get_vacancy(hh_id))
        except hh_api.HHApiError:
            pass
    return collector.fetch_vacancy(hh_id)


def validate(vacancy):
    """Что мешает сохранить; пустой список — можно сохранять."""
    problems = []
    if not (vacancy.get("title") or "").strip():
        problems.append("нужно название вакансии")
    if not (vacancy.get("description") or "").strip():
        problems.append("нужно описание: по нему считаются оценка и письмо")
    return problems

