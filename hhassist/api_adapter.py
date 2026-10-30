# -*- coding: utf-8 -*-
"""Преобразование JSON API hh.ru в нормализованный словарь вакансии.

Словарь — тот же, что отдаёт HTML-парсер (collector.parse_vacancy), плюс source="api":
id, title, company, url, salary_from, salary_to, currency, work_format,
experience, published_at, description, skills, source.
"""
from .collector import html_to_text

EXPERIENCE_MAP = {
    "noExperience": "нет опыта",
    "between1And3": "1–3 года",
    "between3And6": "3–6 лет",
    "moreThan6": "более 6 лет",
}

WORK_FORMAT_MAP = {
    "REMOTE": "удалённо",
    "HYBRID": "гибрид",
    "ON_SITE": "офис",
    "FIELD_WORK": "разъездная",
    "ON_SITE_OFFICE": "офис",
}

SCHEDULE_MAP = {
    "remote": "удалённо",
    "fullDay": "полный день",
    "shift": "сменный график",
    "flexible": "гибкий график",
    "flyInFlyOut": "вахта",
}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _salary_values(salary):
    """(from, to, currency) из одного объекта вилки."""
    try:
        salary_from = int(salary["from"]) if salary.get("from") is not None else None
    except (TypeError, ValueError):
        salary_from = None
    try:
        salary_to = int(salary["to"]) if salary.get("to") is not None else None
    except (TypeError, ValueError):
        salary_to = None
    return salary_from, salary_to, _text(salary.get("currency"))


def _salary(item):
    """(from, to, currency): сначала item["salary_range"], затем item["salary"].

    По документации hh поле `salary` помечено deprecated — актуальное `salary_range`
    (те же from/to/currency плюс gross, mode, frequency). `salary_range` бывает null,
    поэтому старый ключ остаётся резервом, чтобы вилка не пропадала.
    """
    for key in ("salary_range", "salary"):
        data = item.get(key)
        if not isinstance(data, dict) or not data:
            continue
        values = _salary_values(data)
        if values[0] is not None or values[1] is not None or values[2]:
            return values
    return None, None, ""


def _work_format(item):
    """Формат работы строкой: work_format -> schedule (deprecated) -> пусто.

    `work_format` — актуальное поле схемы; `schedule` hh помечает deprecated и держит
    ради совместимости, поэтому читаем его только последним резервом.
    """
    parts = []
    for entry in item.get("work_format") or []:
        if isinstance(entry, dict):
            key = _text(entry.get("id"))
            parts.append(WORK_FORMAT_MAP.get(key, _text(entry.get("name")) or key))
        else:
            parts.append(WORK_FORMAT_MAP.get(_text(entry), _text(entry)))
    if not parts:
        schedule = item.get("schedule") or {}
        if isinstance(schedule, dict):
            sid = _text(schedule.get("id"))
            parts.append(SCHEDULE_MAP.get(sid, _text(schedule.get("name"))))
    return ", ".join(p for p in dict.fromkeys(parts) if p)


def _experience(item):
    exp = item.get("experience") or {}
    if isinstance(exp, dict):
        key = _text(exp.get("id"))
        return EXPERIENCE_MAP.get(key, _text(exp.get("name")) or key)
    return _text(exp)


def _skills(item):
    skills = []
    for entry in item.get("key_skills") or []:
        name = _text(entry.get("name")) if isinstance(entry, dict) else _text(entry)
        if name and name not in skills:
            skills.append(name)
    return skills[:25]


def _description(item, limit=4000):
    """Полное описание из /vacancies/{id} либо сниппет из поисковой выдачи."""
    full = html_to_text(_text(item.get("description")))
    if full:
        return full[:limit]
    snippet = item.get("snippet") or {}
    if isinstance(snippet, dict):
        parts = [html_to_text(_text(snippet.get("responsibility"))),
                 html_to_text(_text(snippet.get("requirement")))]
        text = "\n".join(p for p in parts if p)
        return text[:limit]
    return ""


def _area(item):
    """(название региона, id региона, субъект РФ) из item["area"]/item["address"]."""
    area = item.get("area") or {}
    address = item.get("address") or {}
    region = ""
    if isinstance(address, dict):
        region = _text(address.get("region")) or _text(address.get("city"))
    if isinstance(area, dict):
        vid = area.get("id")
        return (_text(area.get("name"))[:120], "" if vid is None else _text(str(vid)),
                region[:120])
    return _text(area)[:120], "", region[:120]


def vacancy_from_api(item):
    """JSON вакансии hh.ru -> нормализованный словарь приложения."""
    item = item if isinstance(item, dict) else {}
    vid = str(item.get("id") or "")
    employer = item.get("employer") or {}
    company = _text(employer.get("name")) if isinstance(employer, dict) else ""
    salary_from, salary_to, currency = _salary(item)
    area, area_id, region = _area(item)
    return {
        "id": vid,
        "title": _text(item.get("name")),
        "company": company[:120],
        "url": _text(item.get("alternate_url")) or ("https://hh.ru/vacancy/%s" % vid if vid else ""),
        "salary_from": salary_from,
        "salary_to": salary_to,
        "currency": currency,
        "work_format": _work_format(item),
        "experience": _experience(item),
        "published_at": _text(item.get("published_at")),
        "description": _description(item),
        "skills": _skills(item),
        "area": area,
        "area_id": area_id,
        "region": region,
        "source": "api",
    }


def vacancies_from_search(items):
    """Список items поисковой выдачи -> список нормализованных вакансий."""
    return [vacancy_from_api(item) for item in items or []]
