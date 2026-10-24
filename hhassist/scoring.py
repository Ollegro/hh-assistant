# -*- coding: utf-8 -*-
"""Скоринг вакансий: правила + опциональная LLM-оценка (0-100)."""
from . import llm, matching
from .config import get_settings

BASE_SCORE = 50
STOP_PENALTY = 35
SALARY_BELOW = 30
SALARY_IN_RANGE = 10
FORMAT_BONUS = 8
FORMAT_PENALTY = 10
EXPERIENCE_BONUS = 6
EXPERIENCE_PENALTY = 12
HARD_STOP_BELOW = 30

# Описание правил для окна «Как считается оценка» в интерфейсе.
# Формируется из тех же констант, что и сам расчёт: подпись не разойдётся с кодом.
RULE_WEIGHTS = (
    ("Стартовый балл", "+%d" % BASE_SCORE, "с этого начинает любая вакансия"),
    ("Стоп-слово профиля", "−%d" % STOP_PENALTY, "«стажёр», «продажи» и т.п. — за каждое"),
    ("Роль в заголовке", "по профилю", "совпадение с целевой ролью (промпт/AI-инженер)"),
    ("Стек технологий", "по профилю", "ключевые и дополнительные технологии из резюме"),
    ("Уровень (junior/lead)", "по профилю", "ниже или выше целевого — минус"),
    ("Зарплата ниже ожиданий", "−%d" % SALARY_BELOW, "верхняя граница вилки ниже желаемой"),
    ("Зарплата в вилке", "+%d" % SALARY_IN_RANGE, "нижняя граница не ниже желаемой"),
    ("Зарплата не указана", "0", "балл не меняется, причина остаётся в списке"),
    ("Формат работы подходит", "+%d" % FORMAT_BONUS, "удалёнка/гибрид из профиля"),
    ("Формат вне предпочтений", "−%d" % FORMAT_PENALTY, "офис и прочее"),
    ("Опыт 1–3 года", "+%d" % EXPERIENCE_BONUS, "целевой уровень"),
    ("Требуется опыт 6+ лет", "−%d" % EXPERIENCE_PENALTY, "слишком высокий грейд"),
    ("Компания в стоп-листе", "0 и стоп", "вакансия не рассматривается вовсе"),
    ("Итог ниже %d" % HARD_STOP_BELOW, "стоп", "вердикт «не откликаться»"),
)


def _text(v):
    return " ".join([
        v.get("title", ""),
        v.get("description", ""),
        " ".join(v.get("skills", []) or []),
    ]).lower()


def rule_score(v, profile):
    """Оценка по формальным правилам. Возвращает (score, reasons, hard_stop)."""
    score = BASE_SCORE
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
            score -= SALARY_BELOW
            reasons.append("зарплата ниже ожиданий (%s)" % top)
        elif sf and sf >= want_from:
            score += SALARY_IN_RANGE
            reasons.append("зарплата в вилке")
    else:
        reasons.append("зарплата не указана")

    # формат работы
    fmt = (v.get("work_format") or "").lower()
    wanted = [f.lower() for f in profile.get("formats", [])]
    if fmt:
        if any(w in fmt for w in wanted):
            score += FORMAT_BONUS
            reasons.append("формат: %s" % fmt)
        else:
            score -= FORMAT_PENALTY
            reasons.append("формат вне предпочтений: %s" % fmt)

    # опыт
    exp = (v.get("experience") or "").lower()
    if "более 6" in exp:
        score -= EXPERIENCE_PENALTY
        reasons.append("требуется опыт 6+ лет")
    elif "1–3" in exp or "1-3" in exp:
        score += EXPERIENCE_BONUS
        reasons.append("опыт 1–3 года")

    # стоп-лист компаний из профиля: вакансия не рассматривается вовсе
    if matching.is_excluded_company(v, profile):
        score = 0
        hard_stop = True
        reasons.append("компания в стоп-листе профиля")

    score = max(0, min(100, score))
    if score < HARD_STOP_BELOW:
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
    """Итоговая оценка: правила + LLM (если доступен).

    Возвращает балл правил и балл LLM отдельно (`rule_score`, `llm_score`) —
    их показывает окно «Как считается оценка» в карточке.
    """
    r_score, r_reasons, hard_stop = rule_score(v, profile)
    llm = llm_score(v, profile, settings)
    model_score = None
    if llm and llm.get("score") is not None:
        model_score = llm["score"]
        final = combine_scores(r_score, model_score)
        verdict = llm.get("verdict") or ("откликаться" if final >= 60 else "условно")
        reasons = r_reasons + llm.get("reasons", [])
        model = llm.get("model", "")
    else:
        final = r_score
        verdict = "не откликаться" if hard_stop else ("откликаться" if final >= 65 else "условно")
        reasons = r_reasons + (llm.get("reasons", []) if llm else [])
        model = "rules"
    return {"score": final, "verdict": verdict, "reasons": reasons, "model": model,
            "rule_score": r_score, "llm_score": model_score}
