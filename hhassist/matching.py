# -*- coding: utf-8 -*-
"""Сопоставление вакансии с профилем-резюме: запросы, стек, роль, уровень.

Профиль (`config/profile.json`) описывает резюме кандидата: целевые роли,
историю работы, стек (must/nice), уровень, образование, языки. Этот модуль
превращает его в поисковые запросы и в понятные правила скоринга.
"""

ROLE_BONUS = 20
ROLE_PENALTY = 25
STACK_BONUS_MAX = 25
MUST_WEIGHT = 4.0
NICE_WEIGHT = 1.5
STACK_PENALTY = 15
JUNIOR_PENALTY = 20
OVERLEVEL_PENALTY = 10

JUNIOR_WORDS = ("junior", "джун", "стажер", "стажёр", "интерн", "trainee", "без опыта", "нет опыта")
LEAD_WORDS = ("head of", "руководитель", "директор", "team lead", "техлид", "cto", "10+ лет")


def normalize(text):
    """Нижний регистр + ё→е: сравнения не должны зависеть от написания."""
    return (text or "").lower().replace("ё", "е")


def vacancy_text(v):
    """Текст вакансии для поиска совпадений: заголовок, описание, навыки."""
    skills = v.get("skills") or []
    if isinstance(skills, str):
        skills = [part for part in skills.split(",")]
    return normalize(" ".join([v.get("title", ""), v.get("description", ""), " ".join(skills)]))


def stack_of(profile):
    stack = profile.get("stack") or {}
    must = [normalize(x) for x in stack.get("must", []) if x]
    nice = [normalize(x) for x in stack.get("nice", []) if x]
    if not must and not nice:
        must = [normalize(x) for x in (profile.get("must_any") or []) if x]
    return must, nice


def target_roles(profile):
    """Целевые роли из резюме; при отсутствии — ключевые слова профиля."""
    roles = [normalize(r) for r in (profile.get("target_roles") or []) if r]
    if roles:
        return roles
    return [normalize(m) for m in (profile.get("must_any") or []) if m]


def build_queries(profile, limit=5):
    """Поисковые запросы на hh.ru из профиля (целевые роли + дополнительные)."""
    queries = [q for q in (profile.get("target_roles") or []) if q]
    if not queries:
        queries = [q for q in (profile.get("must_any") or []) if q]
    for q in profile.get("queries") or []:
        if q and q not in queries:
            queries.append(q)
    return queries[:limit]


def title_match(v, profile):
    """(баллы, причины) за совпадение профессии в заголовке вакансии."""
    title = normalize(v.get("title"))
    score = 0
    reasons = []

    hits = [r for r in target_roles(profile) if r and r in title]
    if hits:
        score += ROLE_BONUS
        reasons.append("роль в заголовке: " + ", ".join(hits[:3]))

    avoid = [normalize(w) for w in (profile.get("stack") or {}).get("avoid_in_title", []) if w]
    bad = [w for w in avoid if w in title]
    if bad:
        score -= ROLE_PENALTY
        reasons.append("нецелевой стек в заголовке: " + ", ".join(bad[:3]))
    return score, reasons


def stack_match(v, profile):
    """(баллы, причины) за совпадение стека технологий из резюме."""
    text = vacancy_text(v)
    must, nice = stack_of(profile)
    hit_must = [w for w in must if w in text]
    hit_nice = [w for w in nice if w in text]

    reasons = []
    if hit_must:
        reasons.append("стек (ключевое): " + ", ".join(hit_must[:8]))
    if hit_nice:
        reasons.append("стек (плюсы): " + ", ".join(hit_nice[:8]))

    if not hit_must and not hit_nice:
        reasons.append("нет совпадений по стеку профиля")
        return -STACK_PENALTY, reasons

    raw = MUST_WEIGHT * len(hit_must) + NICE_WEIGHT * len(hit_nice)
    return int(round(min(STACK_BONUS_MAX, raw))), reasons


def seniority_penalty(v, profile):
    """(баллы, причины) за несоответствие уровню из резюме."""
    title = normalize(v.get("title"))
    experience = normalize(v.get("experience"))
    seniority = normalize(profile.get("seniority") or "middle+")
    score = 0
    reasons = []

    junior = [w for w in JUNIOR_WORDS if w in title or w in experience]
    if junior:
        score -= JUNIOR_PENALTY
        reasons.append("уровень ниже целевого: " + ", ".join(junior[:2]))

    if "middle" in seniority or "senior" in seniority or "+" in seniority:
        over = [w for w in LEAD_WORDS if w in title]
        if over:
            score -= OVERLEVEL_PENALTY
            reasons.append("уровень выше целевого: " + ", ".join(over[:2]))
    return score, reasons


def is_excluded_company(v, profile):
    """True, если компания в стоп-листе профиля."""
    company = normalize(v.get("company"))
    if not company:
        return False
    return any(normalize(name) and normalize(name) in company
               for name in (profile.get("exclude_companies") or []))


def explain(v, profile):
    """Сводка совпадений для карточки вакансии (человекочитаемая строка)."""
    must, _ = stack_of(profile)
    text = vacancy_text(v)
    hit = [w for w in must if w in text]
    missing = [w for w in must if w not in text]
    parts = []
    if hit:
        parts.append("совпало: " + ", ".join(hit[:8]))
    if missing:
        parts.append("нет в тексте: " + ", ".join(missing[:8]))
    return "; ".join(parts)


def profile_brief(profile):
    """Выжимка резюме для LLM-промптов (роль, опыт, стек, образование, языки)."""
    lines = []
    if profile.get("headline"):
        lines.append("Профессия: %s" % profile["headline"])
    if profile.get("target_roles"):
        lines.append("Целевые роли: " + ", ".join(profile["target_roles"]))
    if profile.get("seniority"):
        lines.append("Уровень: %s" % profile["seniority"])

    history = profile.get("work_history") or []
    if history:
        items = ["%s — %s (%s)" % (job.get("role", ""), job.get("company", ""), job.get("period", ""))
                 for job in history]
        lines.append("Опыт работы: " + "; ".join(items))

    must, nice = stack_of(profile)
    if must:
        lines.append("Ключевой стек: " + ", ".join(must))
    if nice:
        lines.append("Дополнительный стек: " + ", ".join(nice))

    if profile.get("education"):
        lines.append("Образование: " + "; ".join(profile["education"]))
    if profile.get("languages"):
        lines.append("Языки: " + ", ".join("%s — %s" % (k, v) for k, v in profile["languages"].items()))
    if profile.get("formats"):
        lines.append("Формат работы: " + ", ".join(profile["formats"]))
    if profile.get("salary_from") or profile.get("salary_to"):
        lines.append("Вилка: %s–%s ₽" % (profile.get("salary_from") or "?", profile.get("salary_to") or "?"))
    return "\n".join(lines)

