# -*- coding: utf-8 -*-
"""Скоринг вакансий: правила + опциональная LLM-оценка (0-100)."""
from . import llm, matching
from .config import get_settings

STOP_PENALTY = 35


def _text(v):
    return " ".join([
        v.get("title", ""),
        v.get("description", ""),
        " ".join(v.get("skills", []) or []),
    ]).lower()


def rule_score(v, profile):
    """Оценка по формальным правилам. Возвращает (score, reasons, hard_stop)."""
    score = 50
    reasons = []
    hard_stop = False
    text = _text(v)

    # стоп-слова: продажи, стажировки и т.п.
    for w in profile.get("stop_words", []):
        if w.lower() in text:
            score -= STOP_PENALTY
            reasons.append("стоп-слово: %s" % w)

    # роль в заголовке, стек технологий и уровень — по профилю-резюме
    for delta, why in (matching.title_match(v, profile),
                       matching.stack_match(v, profile),
                       matching.seniority_penalty(v, profile)):
        score += delta
        reasons += why

    # зарплата
    sf, st = v.get("salary_from"), v.get("salary_to")
    want_from = profile.get("salary_from", 0)
    if v.get("currency") == "RUR" and (sf or st):
        top = st or sf
        if top and top < want_from:
            score -= 30
            reasons.append("зарплата ниже ожиданий (%s)" % top)
        elif sf and sf >= want_from:
            score += 10
            reasons.append("зарплата в вилке")
    else:
        reasons.append("зарплата не указана")

    # формат работы
    fmt = (v.get("work_format") or "").lower()
    wanted = [f.lower() for f in profile.get("formats", [])]
    if fmt:
        if any(w in fmt for w in wanted):
            score += 8
            reasons.append("формат: %s" % fmt)
        else:
            score -= 10
            reasons.append("формат вне предпочтений: %s" % fmt)

    # опыт
    exp = (v.get("experience") or "").lower()
    if "более 6" in exp:
        score -= 12
        reasons.append("требуется опыт 6+ лет")
    elif "1–3" in exp or "1-3" in exp:
        score += 6
        reasons.append("опыт 1–3 года")

    # стоп-лист компаний из профиля: вакансия не рассматривается вовсе
    if matching.is_excluded_company(v, profile):
        score = 0
        hard_stop = True
        reasons.append("компания в стоп-листе профиля")

    score = max(0, min(100, score))
    if score < 30:
        hard_stop = True
    return score, reasons, hard_stop


PROMPT = """Ты помогаешь оценить вакансию под профиль кандидата.

Профиль: {profile}
Ключевые факты кандидата: {facts}
Пробелы кандидата: {gaps}

Вакансия: {title}
Компания: {company}
Формат: {work_format}
Опыт: {experience}
Зарплата: {salary}
Описание: {description}

Верни строго JSON без пояснений:
{{"score": <0-100>, "verdict": "откликаться|условно|не откликаться", "reasons": ["..."], "focus": "что подчеркнуть в письме"}}
"""


RULE_WEIGHT = 0.45
LLM_WEIGHT = 0.55


def combine_scores(rule, model_score):
    """Итоговый балл: смесь оценки правил и оценки LLM с весами RULE_WEIGHT/LLM_WEIGHT."""
    return int(round(RULE_WEIGHT * rule + LLM_WEIGHT * model_score))


def llm_score(v, profile, settings=None):
    """Оценка через LLM. Возвращает dict или None, если LLM не настроен."""
    s = settings or get_settings()
    if not llm.is_enabled(s):
        return None
    salary = ""
    if v.get("salary_from") or v.get("salary_to"):
        salary = "%s–%s %s" % (v.get("salary_from") or "?", v.get("salary_to") or "?", v.get("currency") or "")
    prompt = PROMPT.format(
        profile=matching.profile_brief(profile),
        facts="; ".join(profile.get("facts", [])),
        gaps="; ".join(profile.get("gaps", [])),
        title=v.get("title", ""),
        company=v.get("company", ""),
        work_format=v.get("work_format", ""),
        experience=v.get("experience", ""),
        salary=salary or "не указана",
        description=(v.get("description") or "")[:3000],
    )
    try:
        parsed = llm.chat_json(s, [{"role": "user", "content": prompt}], temperature=0.2)
    except llm.LLMError as exc:
        return {"score": None, "verdict": "", "reasons": ["LLM недоступен: %s" % exc],
                "model": s.llm_model}
    if "score" not in parsed:
        return {"score": None, "verdict": "", "reasons": ["LLM вернул ответ без оценки"],
                "model": s.llm_model}
    reasons = list(parsed.get("reasons") or [])
    if parsed.get("focus"):
        reasons.append("фокус: " + str(parsed["focus"]))
    return {"score": int(parsed.get("score", 0)), "verdict": parsed.get("verdict", ""),
            "reasons": reasons, "model": s.llm_model}


def score_vacancy(v, profile, settings=None):
    """Итоговая оценка: правила + LLM (если доступен)."""
    r_score, r_reasons, hard_stop = rule_score(v, profile)
    llm = llm_score(v, profile, settings)
    if llm and llm.get("score") is not None:
        final = combine_scores(r_score, llm["score"])
        verdict = llm.get("verdict") or ("откликаться" if final >= 60 else "условно")
        reasons = r_reasons + llm.get("reasons", [])
        model = llm.get("model", "")
    else:
        final = r_score
        verdict = "не откликаться" if hard_stop else ("откликаться" if final >= 65 else "условно")
        reasons = r_reasons + (llm.get("reasons", []) if llm else [])
        model = "rules"
    return {"score": final, "verdict": verdict, "reasons": reasons, "model": model}
