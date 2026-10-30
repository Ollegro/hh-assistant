# -*- coding: utf-8 -*-
"""Общее для всех площадок: нормализация вакансии в формат приложения."""

TITLES = {"hh": "hh.ru", "habr": "Хабр Карьера", "superjob": "SuperJob",
          "trudvsem": "Работа России", "getmatch": "getmatch"}


def make(name, external_id, title, company, url, **fields):
    """Нормализованная вакансия площадки: id с префиксом, source = имя источника."""
    vacancy = {
        "id": "%s:%s" % (name, external_id),
        "title": (title or "").strip(),
        "company": (company or "").strip()[:120],
        "url": url or "",
        "salary_from": None,
        "salary_to": None,
        "currency": "",
        "work_format": "",
        "experience": "",
        "published_at": "",
        "description": "",
        "skills": [],
        "area": "",
        "area_id": "",
        "region": "",
        "source": name,
    }
    for key, value in fields.items():
        if value is not None:
            vacancy[key] = value
    return vacancy
