# -*- coding: utf-8 -*-
"""«Работа России» (trudvsem.ru): открытый API вакансий без ключа.

Документация: https://opendata.trudvsem.ru/api/v1/vacancies
Ответ: {"meta": {"total": N, "limit": 100}, "results": {"vacancies": [{"vacancy": {...}}]}}.
Особенности: зарплата в salary_min/salary_max, опыт — числом лет в requirement.experience,
описание складывается из duty (обязанности) и requirements (требования).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import make

NAME = "trudvsem"
API = "https://opendata.trudvsem.ru/api/v1/vacancies"
MAX_PER_PAGE = 100
UA = "hh-assistant/0.1 (o.gutsev@mail.ru)"


def _http_json(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "ignore") or "{}")


def _experience(value):
    """Число лет из API -> подпись как на hh."""
    try:
        years = int(value)
    except (TypeError, ValueError):
        return ""
    if years <= 0:
        return "нет опыта"
    if years <= 3:
        return "1–3 года"
    if years <= 6:
        return "3–6 лет"
    return "более 6 лет"


def _salary(item):
    low = item.get("salary_min") or 0
    high = item.get("salary_max") or 0
    low = int(low) if low else None
    high = int(high) if high else None
    if low is None and high is None:
        return None, None, ""
    if low and high and high == low:
        high = None
    return low, high, "RUR"


def vacancy_from_item(item):
    """Одна запись API -> нормализованная вакансия приложения."""
    item = item if isinstance(item, dict) else {}
    company = item.get("company") or {}
    region = item.get("region") or {}
    requirement = item.get("requirement") or {}
    duty = (item.get("duty") or "").strip()
    reqs = (item.get("requirements") or "").strip()
    description = "\n".join(part for part in (duty, reqs) if part)[:4000]
    skills = [str(s).strip() for s in (item.get("skills") or []) if str(s).strip()][:25]
    low, high, currency = _salary(item)
    return make(
        NAME,
        item.get("id") or "",
        item.get("job-name") or "",
        (company.get("name") if isinstance(company, dict) else "") or "",
        item.get("vac_url") or "",
        salary_from=low,
        salary_to=high,
        currency=currency,
        experience=_experience(requirement.get("experience")),
        published_at=(item.get("creation-date") or "").strip(),
        description=description,
        skills=skills,
        area=(region.get("name") if isinstance(region, dict) else "") or "",
        region=(region.get("name") if isinstance(region, dict) else "") or "",
    )


def search(query, area="", pages=1, per_page=50, pause=1.0, remote=False):
    """Поиск вакансий. area — id региона hh (для фильтра по названию субъекта)."""
    from .. import areas as areas_mod

    region_names = []
    for area_id in (area if isinstance(area, (list, tuple)) else [area]):
        try:
            name = areas_mod.name_of(int(area_id))
        except (TypeError, ValueError):
            name = ""
        if name:
            region_names.append(name.lower())

    limit = max(1, min(int(per_page or 50), MAX_PER_PAGE))
    wanted = limit
    found = []
    page = 0
    # Регион у площадки в параметре `region` работает не для всех запросов,
    # поэтому фильтруем на нашей стороне. Тогда одной страницы мало: нужный
    # субъект может оказаться глубже — догружаем до 20 страниц.
    max_pages = max(1, int(pages or 1)) * (20 if region_names else 1)
    while page < max_pages and len(found) < wanted:
        params = {"limit": limit, "offset": page * limit}
        if query:
            params["text"] = query
        url = API + "?" + urllib.parse.urlencode(params)
        payload = _http_json(url)
        items = ((payload.get("results") or {}).get("vacancies") or [])
        if not items:
            break
        for wrapper in items:
            vacancy = vacancy_from_item(wrapper.get("vacancy") if isinstance(wrapper, dict) else {})
            if not vacancy["id"].endswith(":") and vacancy["title"]:
                if region_names and not any(name in (vacancy["area"] or "").lower()
                                            for name in region_names):
                    continue
                found.append(vacancy)
        if len(items) < limit:
            break
        page += 1
        if pause:
            time.sleep(pause)
    return found[:wanted]
