# -*- coding: utf-8 -*-
"""Генерация сопроводительного письма: LLM (few-shot по образцам) или офлайн-сборка."""
import glob
import os
import re

from . import llm
from .config import get_settings

def _find_samples_glob():
    """Ищет папку «Отклики на вакансии» вверх по дереву проекта.

    Структура проекта менялась (папку переименовывали и переносили), поэтому
    вместо жёсткого числа уровней поднимаемся от модуля вверх и берём первый
    найденный каталог с образцами писем.
    """
    cur = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        candidate = os.path.join(cur, "Отклики на вакансии")
        if os.path.isdir(candidate):
            return os.path.join(candidate, "0*", "Письмо.md")
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


SAMPLES_GLOB = _find_samples_glob()


def load_samples(limit=3):
    """Берёт несколько готовых писем как примеры стиля."""
    out = []
    for path in sorted(glob.glob(SAMPLES_GLOB))[:limit]:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            out.append(re.sub(r"^#.*\n", "", text).strip()[:1200])
        except OSError:
            continue
    return out


PROMPT = """Ты пишешь сопроводительное письмо для отклика на hh.ru.

Кандидат: {headline}. Вилка: {salary_from}-{salary_to} ₽. Формат: {formats}.
Подтверждённые факты (использовать только их, ничего не выдумывать):
{facts}
Чего у кандидата нет (упомянуть честно, если требование вакансии критично):
{gaps}
Ссылки: {links}

Вакансия: {title} — {company}
Формат: {work_format}; опыт: {experience}; зарплата: {salary}
Описание: {description}

Примеры стиля (не копировать дословно):
{samples}

Требования к письму:
- 900-1600 знаков, без Markdown и без заголовков;
- приветствие, 3-5 фактов с цифрами под требования вакансии, честный блок пробелов, формат и вилка, ссылки отдельными строками;
- не выдумывать опыт и цифры; писать по-русски, деловым тоном.
Верни только текст письма."""


def llm_letter(v, profile, settings=None):
    """Письмо через LLM. None, если LLM не настроен или произошла ошибка."""
    s = settings or get_settings()
    if not llm.is_enabled(s):
        return None
    salary = ""
    if v.get("salary_from") or v.get("salary_to"):
        salary = "%s-%s %s" % (v.get("salary_from") or "?", v.get("salary_to") or "?", v.get("currency") or "")
    links = ", ".join("%s: %s" % (k, val) for k, val in (profile.get("links") or {}).items())
    prompt = PROMPT.format(
        headline=profile.get("headline", ""),
        salary_from=profile.get("salary_from"), salary_to=profile.get("salary_to"),
        formats=", ".join(profile.get("formats", [])),
        facts="\n".join("- " + f for f in profile.get("facts", [])),
        gaps="\n".join("- " + g for g in profile.get("gaps", [])),
        links=links,
        title=v.get("title", ""), company=v.get("company", ""),
        work_format=v.get("work_format", ""), experience=v.get("experience", ""),
        salary=salary or "не указана",
        description=(v.get("description") or "")[:2500],
        samples="\n---\n".join(load_samples()),
    )
    try:
        text = llm.chat(s, [{"role": "user", "content": prompt}], temperature=0.4)
    except llm.LLMError:
        return None
    return text.strip() or None


def pick_facts(v, profile, limit=4):
    """Выбирает факты кандидата, релевантные тексту вакансии."""
    text = " ".join([v.get("title", ""), v.get("description", ""),
                     " ".join(v.get("skills", []) or [])]).lower()
    scored = []
    for fact in profile.get("facts", []):
        words = re.findall(r"[а-яa-z]{5,}", fact.lower())
        hits = sum(1 for w in set(words) if w in text)
        scored.append((hits, fact))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:limit]]


def offline_letter(v, profile):
    """Сборка письма без LLM: проверенные блоки + факты под вакансию."""
    links = profile.get("links") or {}
    lines = ["Здравствуйте!", ""]
    lines.append(
        "Откликаюсь на вакансию «%s» в компании %s. Я %s: с января 2024 года делаю продукты с LLM — "
        "RAG-ассистентов, агентов и автоматизацию процессов, а до этого много лет руководил продажами "
        "в FMCG и получил MBA, поэтому говорю с бизнесом на одном языке."
        % (v.get("title", ""), v.get("company", ""), profile.get("headline", "AI-инженер"))
    )
    lines += ["", "Что подтверждаю результатами:", ""]
    for i, fact in enumerate(pick_facts(v, profile), 1):
        lines.append("%d. %s." % (i, fact.rstrip(".")))
    gaps = profile.get("gaps", [])[:2]
    if gaps:
        lines += ["", "Чего пока не делал: %s — если это критично для роли, скажу честно и сразу."
                  % "; ".join(gaps)]
    lines += ["", "Формат: %s; оформление — ТК, ГПХ или самозанятость. Ориентир по доходу — %s–%s тыс. ₽ на руки."
              % (", ".join(profile.get("formats", [])),
                 profile.get("salary_from", 0) // 1000, profile.get("salary_to", 0) // 1000), ""]
    for key, label in (("github", "GitHub"), ("site", "Сайт проекта"), ("demo", "Демо"),
                       ("telegram", "Telegram"), ("email", "Почта")):
        if links.get(key):
            lines += ["%s: %s" % (label, links[key]), ""]
    lines += ["Готов на коротком созвоне обсудить задачи и ожидания.", "",
              "С уважением,", profile.get("name", "")]
    return "\n".join(lines)


def validate_letter(text):
    """Проверка письма: длина, отсутствие Markdown, наличие приветствия."""
    body = (text or "").strip()
    problems = []
    if "**" in body or re.search(r"^#", body, re.M):
        problems.append("markdown")
    if len(body) < 500:
        problems.append("слишком короткое")
    if len(body) > 2200:
        problems.append("слишком длинное")
    if not body.startswith("Здравствуйте"):
        problems.append("нет приветствия")
    return problems


def make_letter(v, profile, settings=None):
    """Письмо: LLM, если настроен и текст прошёл проверку, иначе офлайн-сборка."""
    s = settings or get_settings()
    text = llm_letter(v, profile, s)
    model = s.llm_model or "offline"
    if not text or validate_letter(text):
        text = offline_letter(v, profile)
        model = "offline"
    return text, model

