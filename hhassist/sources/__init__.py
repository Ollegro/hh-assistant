# -*- coding: utf-8 -*-
"""Внешние площадки вакансий, кроме hh.ru.

Каждый источник — модуль с функцией
`search(query, area, pages, per_page, pause, remote) -> list[vacancy]`,
где vacancy — тот же нормализованный словарь, что отдаёт HTML-парсер hh
(`collector.parse_vacancy`). Отличие одно: `id` получает префикс площадки
(`habr:12345`), а `source` — имя источника, поэтому записи не сталкиваются
и в списке видно, откуда пришла вакансия.

Доступ к данным:
* `trudvsem` — открытый API «Работа России», без ключа;
* `superjob` — официальный API 2.0, нужен ключ приложения (`SUPERJOB_APP_ID`);
* `habr` и `getmatch` — публичные HTML-страницы, без авторизации.

Скоринг, письма, дедупликация и таблица работают с ними без изменений.
"""
from . import getmatch, habr, superjob, trudvsem  # noqa: F401
from .base import TITLES, make  # noqa: F401

SOURCES = ("habr", "superjob", "trudvsem", "getmatch")
MODULES = {"habr": habr, "superjob": superjob, "trudvsem": trudvsem, "getmatch": getmatch}


def get(name):
    """Модуль источника по имени (None, если такого нет)."""
    return MODULES.get((name or "").strip().lower())


def titles():
    """{код: подпись} для интерфейса — включая hh."""
    return dict(TITLES)


def collect(name, query, area="", pages=1, per_page=50, pause=1.0, remote=False):
    """Сбор одним источником. Ошибки источника поднимаются исключением."""
    module = get(name)
    if not module:
        raise ValueError("неизвестный источник: %r (доступны: %s)"
                         % (name, ", ".join(SOURCES)))
    return module.search(query, area=area, pages=pages, per_page=per_page,
                         pause=pause, remote=remote)
