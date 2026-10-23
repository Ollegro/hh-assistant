# -*- coding: utf-8 -*-
"""Справочник регионов hh.ru: субъекты РФ для выбора региона сбора.

Раньше выбор региона строился из городов, которые уже есть в базе: в списке
оказывались «деревня Вырубово» и «посёлок Усть-Луга», а субъектов РФ не было.
Теперь список берётся из api.hh.ru/areas/113 (дети = 89 субъектов) и кэшируется
в data/areas.json. Сеть нужна только при пустом или просроченном кэше, поэтому
веб-интерфейс и тесты работают без обращений к hh (см. cached()).
"""
import json
import os
import re
import urllib.request
from datetime import datetime, timedelta

from .config import DATA_DIR, get_settings

AREAS_URL = "https://api.hh.ru/areas/113"
CACHE_PATH = os.path.join(DATA_DIR, "areas.json")
AREAS_TTL_DAYS = 30
ALL_RUSSIA = (113, "Россия")

# Минимальный набор, если сети нет и кэша тоже: только проверенные id.
FALLBACK_REGIONS = [
    (1, "Москва"),
    (2, "Санкт-Петербург"),
    (2019, "Московская область"),
    (145, "Ленинградская область"),
    (1624, "Республика Татарстан"),
    (1679, "Нижегородская область"),
    (1202, "Новосибирская область"),
    (1261, "Свердловская область"),
    (1438, "Краснодарский край"),
    (1530, "Ростовская область"),
]


def normalize(name):
    """Название региона в нижнем регистре без лишних пробелов — для сравнений."""
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def _read_cache():
    """[(id, name)] из кэша или None, если кэша нет/он просрочен/битый."""
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        when = datetime.fromisoformat(str(data.get("fetched_at") or "")[:19])
    except ValueError:
        return None
    if datetime.now() - when > timedelta(days=AREAS_TTL_DAYS):
        return None
    regions = []
    for item in data.get("regions") or []:
        try:
            regions.append((int(item["id"]), str(item["name"])))
        except (KeyError, TypeError, ValueError):
            continue
    return regions or None


def _write_cache(regions):
    """Сохраняет справочник в data/areas.json (ошибки записи не критичны)."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        payload = {"fetched_at": datetime.now().isoformat(timespec="seconds"),
                   "regions": [{"id": i, "name": n} for i, n in regions]}
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _download():
    """Справочник субъектов РФ с api.hh.ru. Бросает исключение при сбое."""
    req = urllib.request.Request(AREAS_URL, headers={
        "User-Agent": get_settings().user_agent,
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    regions = []
    for item in data.get("areas") or []:
        try:
            regions.append((int(item["id"]), str(item["name"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not regions:
        raise RuntimeError("справочник регионов пуст")
    return regions


def cached():
    """[(id, name)] без обращений к сети: кэш, иначе встроенный набор."""
    regions = _read_cache()
    return [ALL_RUSSIA] + (regions or list(FALLBACK_REGIONS))


def ensure(force=False):
    """[(id, name)] с загрузкой справочника, если кэш пуст или просрочен."""
    if not force:
        regions = _read_cache()
        if regions:
            return [ALL_RUSSIA] + regions
    try:
        regions = _download()
    except Exception:  # сеть, таймаут, чужой формат ответа — работаем на фолбэке
        return cached()
    _write_cache(regions)
    return [ALL_RUSSIA] + regions


def region_id(name):
    """id региона по названию ('москва' -> 1) или None, если такого нет."""
    wanted = normalize(name)
    if not wanted:
        return None
    for area_id, title in cached():
        if normalize(title) == wanted:
            return area_id
    return None


def name_of(area_id):
    """Название региона по id ('1' -> 'Москва') или '' для неизвестного id."""
    try:
        wanted = int(area_id)
    except (TypeError, ValueError):
        return ""
    for item_id, title in cached():
        if item_id == wanted:
            return title
    return ""
