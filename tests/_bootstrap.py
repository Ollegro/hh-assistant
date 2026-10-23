# -*- coding: utf-8 -*-
"""Общая настройка тестов: путь к приложению и синтетические данные."""
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(TESTS_DIR)
sys.path.insert(0, APP)

from hhassist.config import DEFAULT_PROFILE  # noqa: E402


def profile():
    """Копия профиля-резюме (без чтения config/profile.json)."""
    return dict(DEFAULT_PROFILE)


def sample_vacancy(**over):
    """Синтетическая вакансия под профиль AI-инженера."""
    v = {
        "id": "1",
        "title": "AI-инженер (LLM)",
        "company": "Тестовая компания",
        "description": "Разработка RAG-ассистентов на Python, LLM API, FastAPI, PostgreSQL.",
        "skills": ["Python", "LLM", "RAG", "FastAPI"],
        "salary_from": 200000,
        "salary_to": 250000,
        "currency": "RUR",
        "work_format": "удалённо",
        "experience": "1–3 года",
    }
    v.update(over)
    return v
