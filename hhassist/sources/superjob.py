# -*- coding: utf-8 -*-
"""SuperJob (api.superjob.ru): официальный API 2.0, нужен ключ приложения.

Ключ (`X-Api-App-Id`) выдаётся после регистрации приложения на api.superjob.ru;
хранится в `.env` как `SUPERJOB_APP_ID` и в чат/логи не попадает.
Лимит площадки — 120 запросов в минуту, в выдаче до 100 вакансий на страницу,
глубина — 500 записей.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import make

NAME = "superjob"
API = "https://api.superjob.ru/2.0/vacancies/"
MAX_PER_PAGE = 100
UA = "hh-assistant/0.1 (o.gutsev@mail.ru)"


def _key():
    from ..config import get_settings
    return (getattr(get_settings(), "superjob_app_id", "") or "").strip()


def _http_json(url, app_id, timeout=40):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "X-Api-App-Id": app_id,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError("SuperJob ответил HTTP %s" % exc.code)
    return json.loads(raw.decode("utf-8", "ignore") or "{}")


def _salary(item):
    low = item.get("payment_from") or None
    high = item.get("payment_to") or None
    currency = "RUR" if (item.get("currency") or "").lower() in ("rub", "rur", "") else \
        (item.get("currency") or "").upper()
    return low, high, currency


def vacancy_from_item(item):
    """Объект вакансии SuperJob -> нормализованная вакансия приложения."""
    item = item if isinstance(item, dict) else {}
    town = item.get("town") or {}
    experience = item.get("experience") or {}
    low, high, currency = _salary(item)
    parts = [str(item.get("work") or "").strip(), str(item.get("candidat") or "").strip()]
    description = "\n".join(part for part in parts if part)[:4000]
    published = ""
    if item.get("date_published"):
        published = time.strftime("%Y-%m-%d", time.gmtime(int(item["date_published"])))
    return make(
        NAME,
        item.get("id") or "",
        str(item.get("profession") or "").strip(),
        str(item.get("firm_name") or "").strip(),
        str(item.get("link") or "").strip(),
        salary_from=low,
        salary_to=high,
        currency=currency,
        area=(town.get("title") if isinstance(town, dict) else "") or "",
        region=(town.get("title") if isinstance(town, dict) else "") or "",
        experience=(experience.get("title") if isinstance(experience, dict) else "") or "",
        published_at=published,
        description=description,
    )


def search(query, area="", pages=1, per_page=50, pause=1.0, remote=False):
    """Поиск вакансий через API. Без ключа — понятная ошибка, а не пустой список."""
    app_id = _key()
    if not app_id:
        raise RuntimeError("SuperJob: не задан SUPERJOB_APP_ID в .env "
                           "(ключ приложения с api.superjob.ru)")
    limit = max(1, min(int(per_page or 50), MAX_PER_PAGE))
    result = []
    for page in range(max(1, int(pages or 1))):
        params = {"keyword": query or "", "count": limit, "page": page}
        if remote:
            params["moveable"] = 1
        url = API + "?" + urllib.parse.urlencode(params)
        payload = _http_json(url, app_id)
        objects = payload.get("objects") or []
        if not objects:
            break
        for item in objects:
            vacancy = vacancy_from_item(item)
            if vacancy["title"]:
                result.append(vacancy)
        if not payload.get("more") or len(objects) < limit:
            break
        if pause:
            time.sleep(pause)
    return result[:limit * max(1, int(pages or 1))]
