# -*- coding: utf-8 -*-
"""Диагностика LLM-контура: настройки, связь с моделью, скоринг и письмо.

Запуск: python tools/check_llm.py
Ключ API не выводится — показывается только признак «задан / не задан».
"""
import os
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from hhassist import letters, llm, scoring  # noqa: E402
from hhassist.config import get_settings  # noqa: E402

DEMO = {
    "id": "demo",
    "title": "AI-инженер (LLM, RAG)",
    "company": "Тестовая компания",
    "description": "Разработка RAG-ассистентов и агентов на Python, LLM API, FastAPI, PostgreSQL. "
                   "Удалённая работа, опыт 1–3 года.",
    "skills": ["Python", "LLM", "RAG", "FastAPI"],
    "salary_from": 200000,
    "salary_to": 250000,
    "currency": "RUR",
    "work_format": "удалённо",
    "experience": "1–3 года",
}

HELP = """Что вписать в .env (файл рядом с cli.py, шаблон — .env.example):
  LLM_PROVIDER=openai            # openai | openrouter | deepseek | ollama | yandex
  LLM_MODEL=gpt-4o-mini          # модель выбранного провайдера
  LLM_API_KEY=...                # ключ из личного кабинета провайдера
  # LLM_BASE_URL=https://...     # нужен только для своего/корпоративного шлюза
  # LLM_TIMEOUT=90  LLM_RETRIES=2  LLM_JSON_MODE=1
Затем повторите: python tools/check_llm.py"""


def main():
    s = get_settings()
    print("=== настройки ===")
    print("провайдер: %s" % (s.llm_provider or "не указан"))
    print("модель:    %s" % (s.llm_model or "не указана"))
    print("адрес API: %s" % (llm.resolve_base_url(s) or "не определён"))
    print("ключ:      %s" % ("задан" if (s.llm_api_key or "").strip() else "не задан"))
    print("таймаут:   %s с, повторов: %s, JSON-режим: %s"
          % (s.llm_timeout, s.llm_retries, s.llm_json_mode))
    print("статус:    %s" % llm.describe(s))

    if not llm.is_enabled(s):
        print()
        print(HELP)
        return 1

    print()
    print("=== связь с моделью ===")
    started = time.time()
    try:
        answer = llm.chat(s, [{"role": "user", "content": "Ответь одним словом: работает"}],
                          temperature=0.0, retries=0)
    except llm.LLMError as exc:
        print("ошибка: %s" % exc)
        print(HELP)
        return 1
    print("ответ за %.1f с: %s" % (time.time() - started, answer.strip()[:60]))

    print()
    print("=== скоринг тестовой вакансии ===")
    res = scoring.score_vacancy(DEMO, s.profile, s)
    print("балл %s | %s | модель %s" % (res["score"], res["verdict"], res["model"]))
    for reason in res["reasons"][:8]:
        print("  - %s" % reason)

    print()
    print("=== письмо ===")
    text, model = letters.make_letter(DEMO, s.profile, s)
    problems = letters.validate_letter(text)
    print("модель: %s, знаков: %d, проверка: %s"
          % (model, len(text), "ок" if not problems else ", ".join(problems)))
    print("первая строка: %s" % text.splitlines()[0][:80])
    return 0


if __name__ == "__main__":
    sys.exit(main())
