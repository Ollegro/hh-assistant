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
    """Берёт несколько готовых писем как примеры стиля.

    В промпт они больше не подмешиваются: письма-образцы сами написаны шаблонно,
    и модель копировала их структуру («В <проект> сделал …»). Функция оставлена
    для ручного просмотра образцов и будущих экспериментов со стилем.
    """
    out = []
    for path in sorted(glob.glob(SAMPLES_GLOB))[:limit]:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            out.append(re.sub(r"^#.*\n", "", text).strip()[:1200])
        except OSError:
            continue
    return out


# Штампы, по которым письмо читается как автоматическое. Проверяются в нижнем регистре.
ROBOTIC_MARKERS = (
    "откликаюсь на вакансию",
    "меня заинтересовала вакансия",
    "хочу предложить свою кандидатуру",
    "мой профиль —",
    "мой профиль -",
    "как видно из резюме",
    "резюме прилагаю",
    "честно о пробелах",
    "вилка:",
    "идеально подхожу",
)


def robotic_markers(text):
    """Какие штампы нашлись в письме: список фраз (пусто — письмо живое)."""
    low = (text or "").lower()
    return [marker for marker in ROBOTIC_MARKERS if marker in low]


def requirements(v, limit=10):
    """Требования и задачи из описания вакансии: буллеты/нумерация, иначе первые фразы.

    Нужно, чтобы модель отвечала на конкретные пункты вакансии, а не пересказывала
    профиль: в описаниях вида «Чем предстоит заниматься • … • …» именно эти строки
    и есть смысл отклика.
    """
    description = (v.get("description") or "").replace("\r", "")
    items = []
    bullet = re.compile(r"^([•·▪–—]|\d+[.)])\s*")
    for line in description.split("\n"):
        line = line.strip()
        if not line or not bullet.match(line):
            continue
        item = bullet.sub("", line).strip()
        if 15 <= len(item) <= 300:
            items.append(item)
        if len(items) >= limit:
            break
    if not items:
        for chunk in re.split(r"(?<=[.!?])\s+", description):
            chunk = chunk.strip()
            if 40 <= len(chunk) <= 300:
                items.append(chunk)
            if len(items) >= 4:
                break
    return "\n".join("- " + item for item in items)


def relevant_gaps(v, profile, limit=2):
    """Пробелы, которых вакансия действительно касается (остальные не упоминаем)."""
    text = " ".join([v.get("title", ""), v.get("description", ""),
                     " ".join(v.get("skills") or [])]).lower()
    out = []
    for gap in profile.get("gaps") or []:
        words = re.findall(r"[а-яa-z]{5,}", str(gap).lower())
        if any(word in text for word in words):
            out.append(str(gap))
        if len(out) >= limit:
            break
    return out


PROMPT = """Ты — кандидат и пишешь сопроводительное письмо рекрутеру на hh.ru. Пиши от первого лица,
живым человеческим языком — как пишут коллеге, а не как отвечает автомат.

Задача: показать, что ты прочитал именно эту вакансию, понимаешь, что в ней предстоит делать,
и что у тебя есть подтверждённый опыт ровно под эти задачи.

Кандидат: {headline}
Подтверждённые факты (использовать ТОЛЬКО их, ничего не додумывать):
{facts}
Чего у кандидата пока нет (упоминать только если это прямо требуется в вакансии, одной фразой):
{gaps}
Ссылки: {links}
Ориентир по доходу: {salary_from}-{salary_to} ₽; формат: {formats}.

Вакансия: {title} — {company}
Формат: {work_format}; опыт: {experience}; зарплата: {salary}
Требования и задачи из описания:
{requirements}
Навыки из карточки: {skills}
Полное описание: {description}

Структура (без заголовков, нумерации и Markdown):
1. Приветствие: «Здравствуйте!» или «Здравствуйте, <имя>!» — если имя видно в вакансии.
2. Первый абзац, 2-3 предложения: зачем пишешь, что зацепило в продукте и задачах
   (конкретная деталь из описания: домен, тип задач, стек) и кем работаешь сейчас.
3. Второй и третий абзацы — опыт под требования: по 1-2 факта с цифрами из списка.
   Начинай с задачи, а не с названия проекта: «Собирал RAG по 167 источникам…»,
   «Держал 828 автотестов на ассистенте…». Своими словами повторяй формулировки вакансии.
4. Одна фраза про формат и доход — спокойно, без слова «вилка» и без торговли.
5. Закрытие: предложение обсудить задачи или показать код/демо + подпись именем;
   ссылки отдельными строками в конце.

Тон:
- по-человечески, тепло и по делу: короткие предложения, живой глагол, без канцелярита;
- «я», а не «кандидат»; никаких «идеально подхожу»;
- конкретика вместо общих слов: инструменты, цифры, что именно делал;
- уважение к читателю: он читает 30 писем в день, береги его время;
- лёгкая уместная ирония допустима, шутки про стек — нет.

Запрещено (это сразу выдаёт шаблон):
- начинать с «Откликаюсь на вакансию…», «Меня заинтересовала вакансия…»,
  «Хочу предложить свою кандидатуру»;
- «Мой профиль — …», «Как видно из резюме…», «Резюме прилагаю»;
- блок «Честно о пробелах:», слова-ярлыки «Вилка:», «Формат:», «С уважением» без подписи;
- три абзаца подряд по шаблону «В <проект> сделал …»;
- перечислять всё подряд и упоминать пробелы, которых вакансия не касается
  (например, B2B-продажи или Go, если в вакансии их нет);
- выдуманные цифры, компании и опыт; эмодзи; Markdown.

Объём: 900-1600 знаков. Язык: русский.
Верни только текст письма, без пояснений."""


STRICT_SUFFIX = """

ВАЖНО: предыдущая попытка звучала как шаблонное письмо — так писать нельзя.
Перепиши заново: теплее и человечнее, конкретнее по задачам этой вакансии, короче.
Не использовать фразы: «Откликаюсь на вакансию», «Мой профиль», «Честно о пробелах»,
«Вилка:», «Резюме прилагаю», «С уважением» без подписи. Верни только текст письма."""



def llm_letter(v, profile, settings=None, strict=False):
    """Письмо через LLM. None, если LLM не настроен или произошла ошибка.

    strict=True — повторный запрос после шаблонного текста: тот же промпт плюс
    STRICT_SUFFIX (на первом заходе модель часто выдаёт канцелярит).
    """
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
        description=(v.get("description") or "")[:3200],
        requirements=requirements(v) or "в описании нет списка задач — опирайся на текст",
        skills=", ".join(v.get("skills") or []) or "не указаны",
    )
    if strict:
        prompt += STRICT_SUFFIX
    try:
        text = llm.chat(s, [{"role": "user", "content": prompt}], temperature=0.4)
    except llm.LLMError:
        return None
    return text.strip() or None


def pick_facts(v, profile, limit=4):
    """Выбирает факты кандидата, релевантные тексту вакансии.

    Счёт нормируется на длину факта (иначе длинные абзацы выигрывают у коротких),
    а совпадения с названием вакансии и навыками из карточки получают двойной вес:
    в письмо должен попасть самый близкий к вакансии опыт, а не самый длинный.
    """
    text = " ".join([v.get("title", ""), v.get("description", ""),
                     " ".join(v.get("skills", []) or [])]).lower()
    head = " ".join([v.get("title", ""),
                     " ".join(v.get("skills", []) or [])]).lower()
    scored = []
    for fact in profile.get("facts", []):
        words = set(re.findall(r"[а-яa-z]{5,}", fact.lower()))
        if not words:
            continue
        hits = sum(1 for w in words if w in text)
        bonus = sum(1 for w in words if w in head)
        scored.append(((hits + 2 * bonus) / (len(words) ** 0.5), fact))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:limit]]


def _formats_text(profile):
    """Форматы работы по-человечески: «удалённо или гибрид»."""
    formats = [str(item) for item in profile.get("formats") or [] if str(item).strip()]
    if not formats:
        return "любой формат"
    if len(formats) == 1:
        return formats[0]
    return " или ".join([", ".join(formats[:-1]), formats[-1]])


def offline_letter(v, profile):
    """Сборка письма без LLM: живой тон, факты под вакансию, честно про пробелы.

    Никаких ярлыков «Что подтверждаю результатами», «Вилка:», «Чего пока не делал» —
    письмо должно читаться как написанное человеком (см. ROBOTIC_MARKERS).
    """
    links = profile.get("links") or {}
    title = v.get("title", "")
    company = v.get("company", "")
    facts = pick_facts(v, profile)

    intro = "Посмотрел вакансию «%s»" % title
    if company:
        intro += " в %s" % company
    intro += (". По задачам это близко к тому, чем занимаюсь последние два года: RAG, "
              "интеграции с LLM и автоматизация процессов. Дальше — только то, что могу "
              "подтвердить кодом или цифрами.")
    lines = ["Здравствуйте!", "", intro]

    if facts:
        lines += ["", "Похожее уже делал руками:"]
        for fact in facts[:2]:
            lines += ["", fact.rstrip(".") + "."]

    gaps = relevant_gaps(v, profile)
    if gaps:
        lines += ["", "Сразу про честное: %s — если это для роли принципиально, лучше скажу "
                  "до собеседования." % "; ".join(gaps)]

    lines += ["", "Комфортнее всего %s; по доходу ориентируюсь на %s–%s тыс. ₽ на руки, "
              "детали готов обсуждать от задач."
              % (_formats_text(profile), profile.get("salary_from", 0) // 1000,
                 profile.get("salary_to", 0) // 1000)]

    for key, label in (("github", "GitHub"), ("site", "Сайт проекта"), ("demo", "Демо"),
                       ("telegram", "Telegram"), ("email", "Почта")):
        if links.get(key):
            lines += ["%s: %s" % (label, links[key])]
    lines += ["", "Если удобно — расскажу подробнее на коротком созвоне или покажу код.",
              "", profile.get("name", "")]
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


def _letter_score(text):
    """(сколько замечаний по длине/Markdown, сколько штампов) — меньше лучше."""
    return (len(validate_letter(text)), len(robotic_markers(text)))


def make_letter(v, profile, settings=None):
    """Письмо: LLM, а при шаблонном тексте — один повтор с усиленным промптом.

    Модель на первом заходе любит канцелярит («Откликаюсь на вакансию…», «Честно
    о пробелах…»). Порядок: первый ответ → при замечаниях повтор со STRICT_SUFFIX →
    из двух берём лучший (меньше замечаний, затем меньше штампов) → если и он
    не проходит проверку, отдаём офлайн-сборку.

    Один мягкий штамп письмо не убивает: живой текст модели всё равно лучше
    шаблона, поэтому офлайн включается при замечаниях по длине или двух и более
    штампах.
    """
    s = settings or get_settings()
    best = llm_letter(v, profile, s)
    model = s.llm_model or "offline"
    if best and (validate_letter(best) or robotic_markers(best)):
        retry = llm_letter(v, profile, s, strict=True)
        if retry and _letter_score(retry) <= _letter_score(best):
            best = retry
    if not best or validate_letter(best) or len(robotic_markers(best)) >= 2:
        return offline_letter(v, profile), "offline"
    return best, model

