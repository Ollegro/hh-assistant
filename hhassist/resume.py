# -*- coding: utf-8 -*-
"""Резюме -> профиль: текст из файла, извлечение полей, слияние с профилем.

Заказчик просил загружать резюме в программу. Порядок работы:

1. `text_from_upload()` — достаёт текст из TXT или DOCX (stdlib: zipfile + XML).
   PDF пока не поддержан осознанно: нужен либо отдельный пакет, либо свой
   разборщик шрифтов; при загрузке PDF отдаём понятное сообщение, а не мусор.
2. `extract_fields()` — правила: должность, роли, стек, вилка, формат, уровень,
   опыт работы, образование, языки, «факты с цифрами» для письма.
3. `llm_parse()` — тот же разбор через LLM (если ключ задан); LLM идёт первым,
   правила добивают пропуски. Без ключа всё работает на правилах.
4. `merge_profile()` — аккуратное слияние: контакты, стоп-слова, пробелы
   (`gaps`) и ручные правки не затираются.
5. `save_profile()` — запись `config/profile.json` с бэкапом прежней версии.
"""
import glob
import html as html_lib
import io
import json
import os
import re
import zipfile
from datetime import datetime

from . import llm
from .config import BASE, PROFILE_PATH, get_settings

MAX_BYTES = 5 * 1024 * 1024          # больше не принимаем: это резюме, а не архив
MAX_TEXT = 60000                      # столько символов разбираем (остальное отбрасываем)
MAX_PDF_MESSAGE = ("PDF пока не поддерживается: сохраните резюме в DOCX или TXT "
                   "либо вставьте текст на страницу профиля")
BINARY_RATIO = 0.10                   # доля «непечатных» символов, после которой это не текст


def _decode(data):
    """Байты -> текст: utf-8 (с BOM), затем cp1251; мусор отбраковываем."""
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        sample = text[:4000]
        if sample:
            bad = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\r\n\t")
            if bad / len(sample) > BINARY_RATIO:
                continue
        return text
    raise ValueError("не удалось прочитать текст: файл не похож на текстовый")


def docx_to_text(data):
    """DOCX -> текст: абзацы `w:p`, строки `w:t` (без сторонних библиотек)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("файл DOCX повреждён")
    name = "word/document.xml"
    if name not in archive.namelist():
        raise ValueError("в файле нет word/document.xml — это не документ Word")
    xml = archive.read(name).decode("utf-8", "replace")
    xml = re.sub(r"<w:tab\b[^>]*/>", "\t", xml)
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:br\b[^>]*/>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return html_lib.unescape(xml)


def text_from_upload(filename, data):
    """Текст резюме из загруженного файла. Ошибки — ValueError с понятным текстом."""
    if not data:
        raise ValueError("файл пустой")
    if len(data) > MAX_BYTES:
        raise ValueError("файл больше %d МБ — загрузите резюме, а не архив"
                         % (MAX_BYTES // (1024 * 1024)))
    name = (filename or "").lower()
    if data.startswith(b"%PDF") or name.endswith(".pdf"):
        raise ValueError(MAX_PDF_MESSAGE)
    if data.startswith(b"PK\x03\x04") or name.endswith(".docx"):
        text = docx_to_text(data)
    else:
        text = _decode(data)
    text = normalize_text(text)[:MAX_TEXT]
    if len(text) < 40:
        raise ValueError("в файле слишком мало текста — нечего разбирать")
    return text


def normalize_text(text):
    """Единый вид: LF, без неразрывных пробелов, без пустых строк подряд."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", "  ", text)
    lines = [line.rstrip() for line in text.split("\n")]
    out = []
    for line in lines:
        if line.strip() or (out and out[-1].strip()):
            out.append(line)
    return "\n".join(out).strip()


# Заголовки блоков резюме -> канонические имена. Сравнение по началу строки.
SECTION_KEYS = {
    "headline": ("желаемая должность", "специализация", "должность", "позиция"),
    "salary": ("зарплата", "ожидания по зарплате", "желаемый доход"),
    "format": ("формат работы", "условия работы", "занятость", "график работы"),
    "experience": ("опыт работы", "опыт"),
    "skills": ("навыки", "ключевые навыки", "технологии", "стек", "инструменты"),
    "education": ("образование",),
    "languages": ("языки", "иностранные языки"),
    "about": ("о себе", "обо мне", "дополнительная информация", "достижения", "проекты"),
}

# Технологии, которые узнаём в свободном тексте (для «плюсов» и must-стека).
SKILL_WORDS = (
    "python", "fastapi", "django", "flask", "asyncio", "celery", "pydantic",
    "postgresql", "postgres", "mysql", "sqlite", "redis", "kafka", "rabbitmq",
    "clickhouse", "spark", "pandas", "numpy", "pytorch", "tensorflow", "sklearn",
    "docker", "kubernetes", "git", "linux", "nginx", "airflow", "n8n",
    "llm", "rag", "prompt", "промпт", "openai", "anthropic", "claude", "gpt",
    "langchain", "langgraph", "langflow", "llama", "ollama", "embeddings",
    "faiss", "pgvector", "chroma", "aiogram", "telegram", "bot",
    "javascript", "typescript", "react", "node", "java", "kotlin", "go",
    "c++", "c#", "1с", "sql", "api", "rest", "graphql",
)

FORMAT_WORDS = {
    "удалённо": ("удалён", "удален", "remote", "на дому", "из дома"),
    "гибрид": ("гибрид", "hybrid", "частично удал"),
    "офис": ("офис", "на месте работодателя", "в офисе"),
}

SENIORITY_WORDS = (
    ("senior", ("senior", "сеньор", "ведущий")),
    ("lead", ("lead", "лид", "руководитель", "head", "cto")),
    ("middle+", ("middle", "мидл")),
    ("junior", ("junior", "джун", "стажёр", "стажер", "интерн", "trainee")),
)

ROLE_HINTS = ("инженер", "разработчик", "программист", "аналитик", "менеджер",
              "специалист", "архитектор", "лид", "lead", "prompt", "ai", "ml",
              "llm", "data", "product")


def _lines(text):
    return [line.strip() for line in (text or "").split("\n") if line.strip()]


def split_sections(text):
    """{канонический раздел: [строки]}; текст до первого заголовка — в 'header'."""
    sections = {"header": []}
    current = "header"
    for line in _lines(text):
        low = line.lower().strip(" :")
        matched = ""
        for key, titles in SECTION_KEYS.items():
            if any(low == title or low.startswith(title + ":") or
                   (low.startswith(title) and len(low) <= len(title) + 18)
                   for title in titles):
                matched = key
                break
        if matched:
            current = matched
            sections.setdefault(current, [])
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if rest:
                sections[current].append(rest)
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _first_role_line(lines):
    """Первая строка, похожая на название должности."""
    for line in lines:
        low = line.lower()
        if 3 <= len(line) <= 80 and any(hint in low for hint in ROLE_HINTS):
            return line
    return ""


def _money(text):
    """Вилка из текста: «от 180 000 ₽» / «180–250 тыс» -> (180000, 250000)."""
    flat = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}\b)", "", text.replace("\u00a0", " "))
    values = []
    for match in re.finditer(r"(\d{2,3})\s*тыс", flat, re.I):
        values.append(int(match.group(1)) * 1000)
    for match in re.finditer(r"(\d{5,7})\s*(?:₽|руб)", flat, re.I):
        values.append(int(match.group(1)))
    for match in re.finditer(r"(\d{5,7})\s*[—–-]\s*(\d{5,7})", flat):
        values.extend([int(match.group(1)), int(match.group(2))])
    values = [value for value in values if 20000 <= value <= 1500000]
    if not values:
        return None, None
    return min(values), (max(values) if len(set(values)) > 1 else None)



PERIOD_RE = re.compile(
    r"((?:0?[1-9]|1[0-2])[./])?(20\d{2})\s*[—–-]\s*"
    r"((?:0?[1-9]|1[0-2])[./])?(20\d{2}|н\.?\s?в\.?|наст)", re.I)


def _skills(sections, text):
    """(must, nice): навыки из блока «Навыки» + технологии из остального текста."""
    block = sections.get("skills") or []
    items = []
    for line in block:
        for part in re.split(r"[,;•·|/]", line):
            part = part.strip(" .-—")
            if 2 <= len(part) <= 40 and part not in items:
                items.append(part)
    must = [item.lower() for item in items][:12]
    low = text.lower()

    def known(word):
        """«postgres» и «промпт» — те же навыки, что «postgresql» и «промпт-инжиниринг»."""
        return any(word in item or item in word for item in must)

    nice = [word for word in SKILL_WORDS if word in low and not known(word)][:10]
    return must, nice


def _formats(sections, text):
    """Формат работы: сначала раздел «Формат/Условия», потом весь текст."""
    block = " ".join(sections.get("format") or []).lower()
    low = (block or text).lower()
    found = [fmt for fmt, words in FORMAT_WORDS.items()
             if any(word in low for word in words)]
    if not block and "офис" in found and len(found) > 1:
        found = [fmt for fmt in found if fmt != "офис"]
    return found[:3]


def _work_history(sections, limit=5):
    """Периоды работы: компания, должность, срок."""
    lines = _lines("\n".join(sections.get("experience") or []))
    out = []
    for index, line in enumerate(lines):
        match = PERIOD_RE.search(line)
        if not match:
            continue
        period = match.group(0).strip()
        company = line[:match.start()].strip(" ,—-·")
        role = line[match.end():].strip(" ,—-·")
        # Три раскладки строк: «Компания — Должность 2024 — 2025»,
        # «2024 — н.в.» отдельной строкой (компания и должность ниже),
        # «Компания 2024 — 2025» (должность ниже).
        after = [lines[index + 1].strip(" ,—-·") if index + 1 < len(lines) else "",
                 lines[index + 2].strip(" ,—-·") if index + 2 < len(lines) else ""]
        if not company and not role:
            company, role = after[0], after[1]
        elif not role:
            role = after[0]
        elif not company and index > 0:
            company = lines[index - 1].strip(" ,—-·")
        if not role or len(role) > 80 or role.endswith("."):
            continue
        item = {"company": company[:80], "role": role[:80], "period": period[:40]}
        if item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _years_of_experience(sections, text):
    """Грубая оценка стажа по годам в периодах работы."""
    years = []
    for line in _lines("\n".join(sections.get("experience") or [])) or _lines(text):
        for match in PERIOD_RE.finditer(line):
            years.append(int(match.group(2)))
            end = match.group(4)
            if end and end.isdigit():
                years.append(int(end))
            else:
                years.append(datetime.now().year)      # «н.в.» / «наст. время»
    if not years:
        match = re.search(r"(\d{1,2})\s*(?:\+\s*)?(?:год|лет)\s+(?:опыт|работ)", text, re.I)
        return int(match.group(1)) if match else 0
    current = datetime.now().year
    if max(years) >= current - 1:
        return max(0, min(30, current - min(years)))
    return max(0, max(years) - min(years))


def _seniority(text, years):
    """Уровень: явное слово в тексте важнее стажа."""
    low = text.lower()
    for grade, words in SENIORITY_WORDS:
        if any(word in low for word in words):
            return grade
    if years >= 5:
        return "senior"
    if years >= 2:
        return "middle+"
    return "junior"


def _education(sections):
    lines = sections.get("education") or []
    return [" ".join(lines[:2])[:160]] if lines else []


def _languages(sections, text):
    """{'английский': 'B1'} из раздела «Языки» или из строки «Английский — B2»."""
    source = "\n".join(sections.get("languages") or []) or text
    out = {}
    for match in re.finditer(
            r"([А-Яа-яA-Za-z]+)\s*[—–:-]\s*(родной|свободный|свободно|базовый|"
            r"продвинутый|A1|A2|B1|B2|C1|C2)", source, re.I):
        out[match.group(1).lower()[:20]] = match.group(2)
    return out


def _facts(sections, text, limit=5):
    """Факты для письма: строки с цифрами (достижения)."""
    pool = []
    for key in ("about", "experience", "header"):
        pool.extend(sections.get(key) or [])
    out = []
    for line in pool or _lines(text):
        digits = sum(1 for char in line if char.isdigit())
        if 60 <= len(line) <= 240 and digits >= 2 and line not in out:
            out.append(line.strip(" ."))
        if len(out) >= limit:
            break
    return out


def extract_fields(text):
    """Разбор резюме правилами. Возвращает только непустые поля профиля."""
    text = normalize_text(text)
    sections = split_sections(text)
    header = sections.get("header") or []

    headline = (sections.get("headline") or [""])[0]
    if not headline:
        headline = _first_role_line(header)
    if not headline and len(header) > 1:
        headline = header[1]

    roles = []
    for part in re.split(r"[/,;]|\s+и\s+", headline):
        part = part.strip(" .-—·")
        if 3 <= len(part) <= 60 and part.lower() not in [r.lower() for r in roles]:
            roles.append(part)

    salary_from, salary_to = _money("\n".join(sections.get("salary") or []) or text)
    must, nice = _skills(sections, text)
    history = _work_history(sections)
    years = _years_of_experience(sections, text)

    fields = {
        "headline": headline[:120],
        "target_roles": roles[:5],
        "salary_from": salary_from,
        "salary_to": salary_to,
        "formats": _formats(sections, text),
        "seniority": _seniority(text, years),
        "work_history": history,
        "education": _education(sections),
        "languages": _languages(sections, text),
        "facts": _facts(sections, text),
        "stack": {"must": must, "nice": nice},
        "experience_years": years,
    }
    return {key: value for key, value in fields.items() if value not in (None, "", [], {})}



PROMPT = """Ты разбираешь резюме кандидата и заполняешь профиль для подбора вакансий.

Верни строго JSON без пояснений:
{{"headline": "профессия одной строкой",
 "target_roles": ["3-5 целевых ролей"],
 "seniority": "junior|middle|middle+|senior|lead",
 "stack": {{"must": ["ключевые технологии"], "nice": ["дополнительные"]}},
 "salary_from": 0, "salary_to": 0,
 "formats": ["удалённо"],
 "work_history": [{{"company": "", "role": "", "period": ""}}],
 "education": ["вуз, год, специальность"],
 "languages": {{"английский": "B1"}},
 "facts": ["3-5 достижений с цифрами, только из резюме"],
 "gaps": ["1-3 честных пробела: чего в опыте нет"]}}

Правила: бери только то, что есть в тексте, ничего не выдумывай;
must — до 12 технологий, nice — до 10; зарплата — число в рублях (0, если не указана);
facts — короткие пункты с цифрами; gaps — формулировки «fine-tuning моделей» и т.п.

Резюме:
{text}"""

EDITABLE = ("headline", "target_roles", "salary_from", "salary_to", "formats",
            "seniority", "work_history", "education", "languages", "facts", "stack")
PROTECTED = ("name", "stop_words", "exclude_companies", "locations", "links",
             "gaps", "queries", "must_any")


def _as_list(value, limit, item_len=60):
    if isinstance(value, str):
        value = [value]
    out = []
    for item in value or []:
        text = str(item).strip()[:item_len]
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _as_int(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(float(str(value).replace(" ", "").replace("\u00a0", "")))
    except (TypeError, ValueError):
        return None
    return number if 0 < number <= 2000000 else None


def clean_llm_fields(data):
    """Приводит ответ модели к тем же типам, что у профиля. Мусор отбрасывается."""
    if not isinstance(data, dict):
        return {}
    out = {}
    headline = str(data.get("headline") or "").strip()
    if 3 <= len(headline) <= 120:
        out["headline"] = headline
    roles = _as_list(data.get("target_roles"), 5)
    if roles:
        out["target_roles"] = roles
    grade = normalize_text(str(data.get("seniority") or "")).lower()
    if grade in ("junior", "middle", "middle+", "senior", "lead"):
        out["seniority"] = grade
    stack = data.get("stack") if isinstance(data.get("stack"), dict) else {}
    must = [item.lower() for item in _as_list(stack.get("must"), 12, 40)]
    nice = [item.lower() for item in _as_list(stack.get("nice"), 10, 40)]
    if must or nice:
        out["stack"] = {key: value for key, value in
                        (("must", must), ("nice", nice)) if value}
    salary_from = _as_int(data.get("salary_from"))
    salary_to = _as_int(data.get("salary_to"))
    if salary_from:
        out["salary_from"] = salary_from
    if salary_to:
        out["salary_to"] = salary_to
    formats = [item for item in _as_list(data.get("formats"), 3)
               if item in FORMAT_WORDS]
    if formats:
        out["formats"] = formats
    history = []
    for item in data.get("work_history") or []:
        if not isinstance(item, dict):
            continue
        row = {key: str(item.get(key) or "").strip()[:80]
               for key in ("company", "role", "period")}
        if row["role"] or row["company"]:
            history.append(row)
    if history:
        out["work_history"] = history[:5]
    education = _as_list(data.get("education"), 3, 160)
    if education:
        out["education"] = education
    languages = data.get("languages")
    if isinstance(languages, dict):
        clean = {str(key).lower()[:20]: str(value)[:20]
                 for key, value in languages.items() if key and value}
        if clean:
            out["languages"] = clean
    facts = _as_list(data.get("facts"), 5, 240)
    if facts:
        out["facts"] = facts
    gaps = _as_list(data.get("gaps"), 3, 120)
    if gaps:
        out["gaps"] = gaps
    return out


def llm_parse(text, settings=None):
    """Разбор резюме моделью. (поля, примечание); без ключа — ({}, причина)."""
    s = settings or get_settings()
    if not llm.is_enabled(s):
        return {}, "LLM не настроен — разобрано правилами"
    try:
        data = llm.chat_json(s, [{"role": "user",
                                  "content": PROMPT.format(text=text[:12000])}],
                             temperature=0.1)
    except llm.LLMError as exc:
        return {}, "LLM недоступен (%s) — разобрано правилами" % exc
    fields = clean_llm_fields(data)
    return (fields, "LLM + правила") if fields else ({}, "LLM вернул пустой разбор")


def combine(rules, model):
    """Поля модели приоритетнее; правила добивают пропуски. Стек объединяется."""
    merged = dict(rules)
    for key, value in (model or {}).items():
        if key == "stack":
            continue
        if value not in (None, "", [], {}):
            merged[key] = value
    stack = {}
    for source in (model or {}).get("stack") or {}, (rules or {}).get("stack") or {}:
        for key in ("must", "nice"):
            items = list(stack.get(key) or [])
            for item in source.get(key) or []:
                if item not in items:
                    items.append(item)
            if items:
                stack[key] = items[:12 if key == "must" else 10]
    if stack:
        merged["stack"] = stack
    return {key: value for key, value in merged.items() if value not in (None, "", [], {})}


def parse_resume(text, settings=None):
    """Текст резюме -> (поля профиля, метод, заметка)."""
    text = normalize_text(text)[:MAX_TEXT]
    if len(text) < 40:
        raise ValueError("в резюме слишком мало текста — нужно хотя бы несколько строк")
    rules = extract_fields(text)
    model, note = llm_parse(text, settings)
    fields = combine(rules, model)
    method = "LLM + правила" if model else "правила"
    return fields, method, note


def merge_profile(current, extracted):
    """Слияние разобранного резюме с текущим профилем.

    Что не затирается никогда: имя, контакты, стоп-слова, стоп-лист компаний,
    `gaps` (человек их формулировал сам) и `queries` (ими управляет редактор
    запросов). Факты меняются только если новых минимум два — иначе письма
    останутся без материала.
    """
    merged = dict(current or {})
    extracted = extracted or {}
    for key in EDITABLE:
        value = extracted.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "target_roles":
            roles = []
            for item in list(value) + list(merged.get("target_roles") or []):
                if item and item.lower() not in [r.lower() for r in roles]:
                    roles.append(item)
            merged["target_roles"] = roles[:5]
        elif key == "stack":
            stack = dict(merged.get("stack") or {})
            for part, limit in (("must", 12), ("nice", 10)):
                items = list((value or {}).get(part) or [])
                for item in (stack.get(part) or []):
                    if item not in items:
                        items.append(item)
                if items:
                    stack[part] = items[:limit]
            merged["stack"] = stack
        elif key == "facts":
            if len(value) >= 2 or not merged.get("facts"):
                merged["facts"] = value
        else:
            merged[key] = value
    merged["resume_updated_at"] = datetime.now().isoformat(timespec="seconds")
    return merged


def save_profile(profile, path=None):
    """Пишет profile.json, сохраняя бэкап прежней версии. Возвращает имя бэкапа."""
    import shutil

    path = path or PROFILE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backup = ""
    if os.path.isfile(path):
        backup = "%s.bak-%s" % (path, datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return backup


def save_resume_text(text, base=None):
    """Кладёт исходный текст резюме в data/resume.txt (папка не попадает в git)."""
    folder = os.path.join(base or BASE, "data")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "resume.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write((text or "").strip() + "\n")
    return path


def money(value):
    """180000 -> «180 000» (для карточек в интерфейсе)."""
    return "{:,}".format(int(value)).replace(",", " ")


def years_text(years):
    """1 год / 2 года / 5 лет."""
    years = int(years)
    if years % 10 == 1 and years % 100 != 11:
        return "%d год" % years
    if 2 <= years % 10 <= 4 and not 12 <= years % 100 <= 14:
        return "%d года" % years
    return "%d лет" % years


def describe(fields):
    """Сводка для предпросмотра: [(подпись, значение)] — то, что увидит человек."""
    fields = fields or {}
    rows = []
    if fields.get("headline"):
        rows.append(("Профессия", fields["headline"]))
    if fields.get("target_roles"):
        rows.append(("Целевые роли", ", ".join(fields["target_roles"])))
    if fields.get("seniority"):
        rows.append(("Уровень", fields["seniority"]))
    stack = fields.get("stack") or {}
    if stack.get("must"):
        rows.append(("Ключевой стек", ", ".join(stack["must"])))
    if stack.get("nice"):
        rows.append(("Дополнительно", ", ".join(stack["nice"])))
    if fields.get("salary_from") or fields.get("salary_to"):
        low, high = fields.get("salary_from"), fields.get("salary_to")
        if low and high:
            text = "%s–%s ₽" % (money(low), money(high))
        elif low:
            text = "от %s ₽" % money(low)
        else:
            text = "до %s ₽" % money(high)
        rows.append(("Вилка", text))
    if fields.get("formats"):
        rows.append(("Формат работы", ", ".join(fields["formats"])))
    if fields.get("experience_years"):
        rows.append(("Стаж (по резюме)", years_text(fields["experience_years"])))
    for index, job in enumerate(fields.get("work_history") or [], 1):
        rows.append(("Место работы %d" % index,
                     "%s — %s (%s)" % (job.get("company") or "—",
                                       job.get("role") or "—",
                                       job.get("period") or "—")))
    if fields.get("education"):
        rows.append(("Образование", "; ".join(fields["education"])))
    if fields.get("languages"):
        rows.append(("Языки", ", ".join("%s — %s" % (key, value)
                                        for key, value in fields["languages"].items())))
    for index, fact in enumerate(fields.get("facts") or [], 1):
        rows.append(("Факт %d" % index, fact))
    for index, gap in enumerate(fields.get("gaps") or [], 1):
        rows.append(("Пробел %d" % index, gap))
    return rows
