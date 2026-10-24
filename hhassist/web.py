# -*- coding: utf-8 -*-
"""Локальный веб-интерфейс ассистента (стандартная библиотека, без зависимостей)."""
import base64
import hmac
import html
import ipaddress
import os
import ssl
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import analytics, areas, jobs, llm, queue, storage
from .config import BASE, get_settings

CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;min-height:100vh;color:#2b2438;
background:#dccbf5 url('/static/bg.jpg') center/cover no-repeat fixed}
header{position:sticky;top:0;z-index:20;display:flex;gap:18px;align-items:center;flex-wrap:wrap;
padding:14px 22px;background:rgba(255,255,255,.78);backdrop-filter:blur(14px) saturate(1.15);
border-bottom:1px solid rgba(255,255,255,.65);box-shadow:0 6px 24px rgba(84,52,140,.10)}
header b{font-size:16px;letter-spacing:.2px}
header span{color:#5d5470;font-size:13px}
main{padding:20px 22px 40px;max-width:1200px;margin:0 auto}
table{display:block;overflow-x:auto;border-collapse:separate;border-spacing:0;width:100%;
background:rgba(255,255,255,.82);
border:1px solid rgba(255,255,255,.70);border-radius:14px;box-shadow:0 10px 30px rgba(84,52,140,.12)}
th,td{padding:9px 12px;border-bottom:1px solid rgba(120,90,170,.14);font-size:13.5px;text-align:left;vertical-align:top}
th{background:rgba(250,247,255,.86);color:#5d5470;font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:none}
tbody tr:hover td{background:rgba(246,240,255,.72)}
.score{font-weight:700}
.s-high{color:#0f7a3d}.s-mid{color:#96660a}.s-low{color:#a01a2c}
a{color:#6b3fc4;text-decoration:none}a:hover{text-decoration:underline}
.btn{display:inline-block;padding:8px 13px;border-radius:10px;border:1px solid rgba(120,90,170,.25);
background:rgba(255,255,255,.92);color:#3a2d52;cursor:pointer;font-size:13px;font-family:inherit;
box-shadow:0 2px 8px rgba(84,52,140,.08);transition:transform .12s ease,box-shadow .12s ease}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 16px rgba(84,52,140,.16)}
.btn.primary{background:linear-gradient(135deg,#8b5cf6,#d06ef0);border-color:transparent;color:#fff}
select{padding:7px 10px;border-radius:10px;border:1px solid rgba(120,90,170,.25);
background:rgba(255,255,255,.92);color:#3a2d52;font-size:13px;font-family:inherit}
textarea{width:100%;min-height:420px;padding:12px;border:1px solid rgba(120,90,170,.22);border-radius:12px;
background:rgba(255,255,255,.94);color:#2b2438;font-family:ui-monospace,Consolas,monospace;
font-size:13px;line-height:1.5}
.meta{margin:4px 0 12px;padding:10px 14px;border-radius:12px;background:rgba(255,255,255,.74);
color:#5d5470;font-size:13px;box-shadow:0 4px 16px rgba(84,52,140,.08)}
.meta b{color:#2b2438}
.actions{display:flex;gap:10px;margin:12px 0;flex-wrap:wrap;align-items:center}
.card{background:rgba(255,255,255,.84);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.72);
border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 10px 28px rgba(84,52,140,.12)}
.reasons{font-size:12.5px;color:#5d5470}
textarea.queries-text{min-height:140px}
@media (max-width:760px){main{padding:12px}th,td{padding:7px 9px;font-size:12.5px}
header{padding:10px 14px;gap:10px}}
"""



STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".svg": "image/svg+xml", ".ico": "image/x-icon",
                ".css": "text/css; charset=utf-8"}


def static_file(name):
    """Путь к файлу из hhassist/static или None.

    Разрешены только известные расширения и простые имена (без каталогов),
    поэтому запрос не может выйти за пределы каталога статики.
    """
    safe = os.path.basename(name or "")
    if not safe or safe != name:
        return None
    if os.path.splitext(safe)[1].lower() not in STATIC_TYPES:
        return None
    path = os.path.join(STATIC_DIR, safe)
    return path if os.path.isfile(path) else None


def esc(x):
    return html.escape(str(x if x is not None else ""))


def fmt_salary(row):
    sf, st, cur = row["salary_from"], row["salary_to"], row["currency"] or ""
    if not sf and not st:
        return "—"
    sign = "₽" if cur == "RUR" else ("$" if cur == "USD" else ("€" if cur == "EUR" else cur))
    if sf and st:
        return "%s–%s %s" % (sf, st, sign)
    if sf:
        return "от %s %s" % (sf, sign)
    return "до %s %s" % (st, sign)


def score_class(score):
    score = score or 0
    if score >= 70:
        return "s-high"
    if score >= 50:
        return "s-mid"
    return "s-low"


def parser_warning(conn, hours=24):
    """Свежее предупреждение «парсер мог сломаться» (не старше hours) или ''."""
    from datetime import datetime

    event = storage.last_event(conn, "parser_suspect")
    if not event:
        return ""
    try:
        when = datetime.fromisoformat((event["ts"] or "")[:19])
    except ValueError:
        return ""
    if (datetime.now() - when).total_seconds() > hours * 3600:
        return ""
    payload = event["payload"]
    quality = payload.get("quality") or {}
    return ("⚠ парсер мог сломаться (запрос «%s», состояние %s: заголовков пусто %s, "
            "описаний коротко %s, %s)" % (
                payload.get("query", "—"), quality.get("state", "?"),
                quality.get("empty_titles", "?"), quality.get("short_descriptions", "?"),
                (event["ts"] or "")[:16].replace("T", " ")))


def _options(pairs, current=""):
    """<option> из [(значение, подпись)] с отметкой выбранного."""
    out = []
    for value, title in pairs:
        selected = " selected" if str(value) == str(current) else ""
        out.append("<option value='%s'%s>%s</option>" % (esc(value), selected, esc(title)))
    return "".join(out)


def _region_options(conn):
    """Регионы: справочник субъектов РФ + счётчики из базы."""
    counts = {row["region"]: row["cnt"] for row in storage.region_counts(conn) if row["region"]}
    total = conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0]
    pairs = [("", "все регионы (%d)" % total)]
    known = set()
    for area_id, name in areas.cached():
        if area_id == areas.ALL_RUSSIA[0]:
            continue
        known.add(name)
        pairs.append((name, "%s (%d)" % (name, counts[name]) if counts.get(name) else name))
    # регионы из базы, которых нет в справочнике (например, «Москва» вместо субъекта)
    for name in sorted(set(counts) - known):
        pairs.append((name, "%s (%d)" % (name, counts[name])))
    return pairs


def _city_options(conn, region):
    """Города: при выбранном регионе — только его, иначе все из базы."""
    pairs = [("", "все города")]
    for row in storage.area_counts(conn, region or None):
        value = row["area"] or ""
        if value:
            pairs.append((value, "%s (%d)" % (value, row["cnt"])))
    return pairs


def render_index(conn, threshold, limit, msg="", area="", region=""):
    rows = queue.next_batch(conn, threshold=threshold, limit=limit,
                            region=region, area=area)
    st = storage.stats(conn)
    left = queue.can_send_today(conn, get_settings().daily_limit)
    collect_regions = _options([(aid, name) for aid, name in areas.cached()], 113)
    actions = (
        "<form method='post' action='/run/collect' style='display:inline'>"
        "<select name='area' title='регион сбора'>%s</select>"
        "<button class='btn' type='submit'>Собрать вакансии</button></form>"
        "<form method='post' action='/run/score' style='display:inline'>"
        "<button class='btn' type='submit'>Оценить</button></form>"
        "<form method='post' action='/run/letters' style='display:inline'>"
        "<button class='btn' type='submit'>Сгенерировать письма</button></form>"
        "<a class='btn' href='/export'>Экспорт писем в файлы</a>"
        "<a class='btn' href='/funnel'>Воронка и ответы</a>" % collect_regions
    )
    parts = [
        "<html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>hh-assistant</title><style>%s</style></head><body>" % CSS,
        "<header><b>hh-assistant</b><span>вакансий: %d · оценено: %d · писем: %d · откликов: %d · "
        "ответов: %d · сегодня: %d · остаток лимита: %d</span><span>%s</span></header><main>" % (
            st["vacancies"], st["scored"], st["letters"], st["applications"], st["replied"],
            st["sent_today"], left, esc(llm.describe(get_settings()))),
        "<div class='actions'>%s</div>" % actions,
        _queries_block(),
    ]
    if msg:
        parts.append("<div class='card'>%s</div>" % esc(msg))
    warning = parser_warning(conn)
    if warning:
        parts.append("<div class='card'>%s</div>" % esc(warning))
    filters = (
        "<div class='actions'><form method='get' action='/' style='display:inline'>"
        "<select name='region' title='регион' onchange='this.form.submit()'>%s</select>"
        " <select name='area' title='город' onchange='this.form.submit()'>%s</select>"
        " <select name='threshold' title='порог оценки' onchange='this.form.submit()'>%s</select>"
        "</form></div>" % (_options(_region_options(conn), region),
                           _options(_city_options(conn, region), area),
                           _options([(0, "порог: показать все"), (40, "порог: 40"),
                                     (55, "порог: 55"), (70, "порог: 70")], threshold))
    )
    parts.append(filters)
    above, scored = storage.score_counts(conn, threshold)
    parts.append(
        "<div class='meta'>собрано в базе: <b>%d</b> · оценено: <b>%d</b> · "
        "выше порога %d: <b>%d</b> · ниже порога: <b>%d</b> · без оценки: <b>%d</b> · "
        "в списке: <b>%d</b></div>" % (
            st["vacancies"], scored, threshold, above, max(0, scored - above),
            max(0, st["vacancies"] - scored), len(rows)))
    parts.append("<table><tr><th>Балл</th><th>Вакансия</th><th>Компания</th><th>Город</th>"
                 "<th>Формат</th><th>Опыт</th>"
                 "<th>Зарплата</th><th>Вердикт</th><th></th></tr>")
    for r in rows:
        parts.append(
            "<tr><td class='score %s'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td><a class='btn' href='/v/%s'>открыть</a></td></tr>" % (
                score_class(r["score"]), r["score"] if r["score"] is not None else "—",
                esc(r["title"]), esc(r["company"]), esc(r["region"] or r["area"]),
                esc(r["work_format"]), esc(r["experience"]),
                fmt_salary(r), esc(r["verdict"] or "—"), esc(r["id"])))
    if not rows:
        parts.append("<tr><td colspan='9'>Ни одной вакансии по фильтру. Соберите вакансии "
                     "кнопкой выше, затем нажмите «Оценить»; порог можно снизить в фильтре.</td></tr>")
    parts.append("</table></main></body></html>")
    return "".join(parts)


def _breakdown_table(title, rows):
    """Таблица разреза аналитики: группа, оценено, отправлено, ответов, доля."""
    parts = ["<div class='card'><div class='meta'><b>%s</b></div><table>"
             "<tr><th>Группа</th><th>Оценено</th><th>Отправлено</th><th>Ответов</th>"
             "<th>Доля ответов</th></tr>" % esc(title)]
    for r in rows:
        parts.append("<tr><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%.1f%%</td></tr>" % (
            esc(r["title"]), r["scored"], r["sent"], r["replied"], r["reply_rate"]))
    if not rows:
        parts.append("<tr><td colspan='5'>нет данных</td></tr>")
    parts.append("</table></div>")
    return "".join(parts)


def render_funnel(conn, wait_days=None):
    """Страница «Воронка»: найдено → оценено → письмо → отправлено → ответ."""
    settings = get_settings()
    wait_days = settings.reply_wait_days if wait_days is None else wait_days
    f = analytics.funnel(conn, wait_days=wait_days)
    c = analytics.conversions(f)
    parts = [
        "<html><head><meta charset='utf-8'><title>Воронка — hh-assistant</title>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<style>%s</style></head><body>" % CSS,
        "<header><b><a href='/'>← к списку</a></b><span>воронка откликов · "
        "окно ожидания ответа: %d дн.</span></header><main>" % wait_days,
        "<div class='card'><table><tr><th>Шаг</th><th>Вакансий</th><th>Конверсия</th></tr>",
    ]
    for key, title, _base in analytics.STEPS:
        value = "—" if key == "found" else "%.1f%%" % c.get(key, 0.0)
        parts.append("<tr><td>%s</td><td class='score'>%d</td><td>%s</td></tr>" % (
            esc(title), f[key], value))
    parts.append("</table></div>")

    parts.append("<div class='card'><div class='meta'><b>Детали</b></div><table>")
    for title, value in (("готово к отправке", f["ready"]),
                         ("пропущено", f["skipped"]),
                         ("без ответа > %d дн." % wait_days, f["no_answer"]),
                         ("приглашения", f["invited"]),
                         ("отказы", f["rejected"])):
        parts.append("<tr><td>%s</td><td class='score'>%d</td></tr>" % (esc(title), value))
    parts.append("</table></div>")

    parts.append(_breakdown_table("Разрез по баллам", analytics.by_score_band(conn)))
    parts.append(_breakdown_table("Форматы работы", analytics.by_format(conn)))
    parts.append(_breakdown_table("Компании (топ-10)", analytics.by_company(conn)))

    rows = queue.responded(conn)
    parts.append("<div class='card'><div class='meta'><b>Ответы работодателей: %d</b></div>"
                 "<table><tr><th>Статус</th><th>Вакансия</th><th>Компания</th><th>Балл</th>"
                 "<th>Когда</th><th></th></tr>" % len(rows))
    for r in rows:
        parts.append("<tr><td>%s</td><td>%s</td><td>%s</td><td class='score %s'>%s</td>"
                     "<td>%s</td><td><a class='btn' href='/v/%s'>открыть</a></td></tr>" % (
                         esc(r["status"]), esc(r["title"]), esc(r["company"]),
                         score_class(r["score"]),
                         r["score"] if r["score"] is not None else "—",
                         esc((r["sent_at"] or "")[:10]), esc(r["id"])))
    if not rows:
        parts.append("<tr><td colspan='6'>Ответов пока нет: отметьте в карточке «Ответили», "
                     "«Приглашение» или «Отказ».</td></tr>")
    parts.append("</table></div>")

    waiting = analytics.awaiting_reply(conn, wait_days=wait_days)
    parts.append("<div class='card'><div class='meta'><b>Ждут ответа > %d дн.: %d</b></div>"
                 "<table><tr><th>Отправлено</th><th>Вакансия</th><th>Компания</th><th>Балл</th>"
                 "<th></th></tr>" % (wait_days, len(waiting)))
    for r in waiting:
        parts.append("<tr><td>%s</td><td>%s</td><td>%s</td><td class='score %s'>%s</td>"
                     "<td><a class='btn' href='/v/%s'>открыть</a></td></tr>" % (
                         esc(r["sent_date"]), esc(r["title"]), esc(r["company"]),
                         score_class(r["score"]),
                         r["score"] if r["score"] is not None else "—", esc(r["id"])))
    if not waiting:
        parts.append("<tr><td colspan='5'>Таких откликов нет.</td></tr>")
    parts.append("</table></div></main></body></html>")
    return "".join(parts)


def apply_status(conn, vacancy_id, kind, note=""):
    """Смена статуса отклика из карточки. Возвращает текст отчёта для интерфейса."""
    code = queue.kind_of(kind)
    if not code:
        return "неизвестный статус: %s (доступны: %s)" % (
            kind, ", ".join(storage.KIND_TITLES[k] for k in storage.KIND_STATUSES))
    if not storage.vacancy_with_letter(conn, vacancy_id):
        return "вакансия не найдена: %s" % vacancy_id
    queue.set_status(conn, vacancy_id, code, note=note)
    return "статус: %s" % storage.KIND_TITLES[code]


QUERIES_HEADER = ("# Поисковые запросы для сбора вакансий "
                  "(по одному в строке, строки с # игнорируются)")


def _queries_path():
    return os.path.join(BASE, "config", "queries.txt")


def split_queries(text):
    """Действующие запросы из текста: без пустых строк, без # (даже с отступом), без повторов."""
    out = []
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        item = line.strip()
        if not item or item.startswith("#") or item in out:
            continue
        out.append(item)
    return out


def queries_text():
    """Текст редактора и источник: содержимое config/queries.txt или список из профиля."""
    path = _queries_path()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read(), "config/queries.txt"
    from . import matching
    items = matching.build_queries(get_settings().profile)
    return QUERIES_HEADER + "\n" + "\n".join(items) + "\n", \
        "профиль (файла config/queries.txt ещё нет)"


def save_queries(text):
    """Сохраняет редактор в config/queries.txt (CRLF, UTF-8). Возвращает отчёт."""
    items = split_queries(text)
    if not items:
        return "не сохранено: ни одного запроса (пустые строки и строки с # не считаются)"
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    path = _queries_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(body + "\n")
    return "запросы сохранены: %d (config/queries.txt)" % len(items)


def _queries():
    """Поисковые запросы: config/queries.txt (ручное переопределение) или из профиля-резюме."""
    path = _queries_path()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            items = split_queries(f.read())
        if items:
            return items
    from . import matching
    return matching.build_queries(get_settings().profile)


def _queries_block():
    """Редактор поисковых запросов на главной: правится руками и сразу влияет на сбор."""
    s = get_settings()
    text, source = queries_text()
    items = split_queries(text)
    note = "действующих запросов: <b>%d</b> · источник: %s" % (len(items), esc(source))
    if s.collect_queries > 0:
        note += (" · <b>внимание:</b> сбор берёт только первые %d (COLLECT_QUERIES=%d)"
                 % (min(s.collect_queries, len(items)), s.collect_queries))
    return (
        "<div class='card'><div class='meta'><b>Поисковые запросы для сбора</b> — "
        "по одному в строке, строки с # игнорируются</div>"
        "<form method='post' action='/queries'>"
        "<textarea class='queries-text' name='queries'>%s</textarea>"
        "<div class='actions'><button class='btn primary' type='submit'>"
        "Сохранить запросы</button><span class='meta'>%s</span></div></form></div>"
        % (esc(text), note))


def run_action(kind, conn, limit=None, area=None):
    """Выполняет действие из веб-интерфейса: collect / score / letters. Возвращает отчёт.

    area — id региона hh для сбора (пусто — регионы из настроек, по умолчанию вся Россия).
    """
    s = get_settings()

    if kind == "collect":
        lim = limit or s.collect_limit
        queries = _queries()
        if s.collect_queries > 0:
            queries = queries[:s.collect_queries]
        if not queries:
            return "нет поисковых запросов: заполните config/queries.txt"
        area_ids = [int(area)] if str(area or "").strip().isdigit() else None
        saved, found, source, notes = jobs.collect_queries(conn, queries, s, limit=lim,
                                                           area_ids=area_ids)
        where = areas.name_of(area_ids[0]) if area_ids else "вся Россия"
        storage.log_event(conn, "collect", {"queries": queries, "saved": saved,
                                            "found": found, "source": source,
                                            "area": where, "from": "web"})
        report = "собрано вакансий: %d из %d найденных (источник: %s, запросов: %d, " \
                 "лимит: %d, регион: %s)" % (saved, found, source, len(queries), lim, where)
        if notes:
            report += "; " + "; ".join(notes[:3])
        return report

    if kind == "score":
        done, total = jobs.score_pending(conn, s)
        storage.log_event(conn, "score", {"count": done, "model": s.llm_model or "rules",
                                          "from": "web"})
        report = "оценено новых: %d (всего оценено: %d)" % (done, total)
        if not done:
            report += "; полная переоценка (после правки профиля) — python cli.py score --all"
        return report

    if kind == "letters":
        made = jobs.make_letters(conn, s, limit=50)
        storage.log_event(conn, "letters", {"count": made, "model": s.llm_model or "offline",
                                            "from": "web"})
        return "создано писем: %d" % made

    return "неизвестное действие: %s" % kind


def render_card(conn, vacancy_id, msg=""):
    row = storage.vacancy_with_letter(conn, vacancy_id)
    if not row:
        return None
    letter = row["letter"] or ""
    note = "текст сгенерирован"
    if row["letter_edited"]:
        note = "правка сохранена: %s" % (row["letter_edited_at"] or "")[:16].replace("T", " ")
    reasons = row["reasons"] or ""
    from . import matching
    match_line = matching.explain(dict(row), get_settings().profile) or "нет данных"
    last = storage.latest_application(conn, vacancy_id)
    kind = storage.status_kind(last["status"]) if last else ""
    if kind:
        status_line = "%s · %s" % (storage.KIND_TITLES.get(kind, last["status"]),
                                   (last["sent_at"] or "")[:16].replace("T", " "))
        if last["note"]:
            status_line += " · " + last["note"]
    else:
        status_line = "ещё не отмечен"
    parts = [
        "<html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>%s</title><style>%s</style></head><body>" % (esc(row["title"]), CSS),
        "<header><b><a href='/'>← к списку</a></b><span>%s</span>"
        "<a class='btn' href='/funnel'>Воронка</a></header><main>" % esc(row["company"]),
        ("<div class='card'>%s</div>" % esc(msg)) if msg else "",
        "<div class='card'><div class='meta'><b>%s</b> · %s · опыт: %s · зарплата: %s · "
        "<span class='score %s'>балл %s</span></div>"
        "<div class='reasons'>%s</div></div>" % (
            esc(row["title"]), esc(row["work_format"] or "формат не указан"),
            esc(row["experience"] or "—"),
            fmt_salary(row), score_class(row["score"]), row["score"] if row["score"] is not None else "—",
            esc(reasons)),
        "<div class='card'><div class='reasons'><b>Сопоставление с профилем:</b> %s</div></div>" % esc(match_line),
        "<div class='card'><div class='reasons'><b>Статус отклика:</b> %s</div>"
        "<div class='actions'>"
        "<form method='post' action='/status/%s' style='display:inline'>"
        "<input type='hidden' name='kind' value='reply'>"
        "<button class='btn' type='submit'>Ответили</button></form>"
        "<form method='post' action='/status/%s' style='display:inline'>"
        "<input type='hidden' name='kind' value='invite'>"
        "<button class='btn' type='submit'>Приглашение</button></form>"
        "<form method='post' action='/status/%s' style='display:inline'>"
        "<input type='hidden' name='kind' value='reject'>"
        "<button class='btn' type='submit'>Отказ</button></form>"
        "</div></div>" % (esc(status_line), esc(row["id"]), esc(row["id"]), esc(row["id"])),
        "<div class='actions'>",
        "<a class='btn primary' target='_blank' rel='noopener' href='%s'>Открыть вакансию на hh</a>" % esc(row["url"]),
        "<button class='btn' onclick=\"navigator.clipboard.writeText(document.getElementById('letter').value);"
        "this.textContent='скопировано'\">Скопировать письмо</button>",
        "<form method='post' action='/mark/%s' style='display:inline'>"
        "<button class='btn' type='submit'>Отметить отправленным</button></form>" % esc(row["id"]),
        "<form method='post' action='/skip/%s' style='display:inline'>"
        "<button class='btn' type='submit'>Пропустить</button></form>" % esc(row["id"]),
        "<a class='btn' href='/letter/%s'>Скачать .txt</a>" % esc(row["id"]),
        "</div>",
        "<form method='post' action='/letter/%s'>"
        "<textarea id='letter' name='text'>%s</textarea>"
        "<div class='actions'><button class='btn primary' type='submit'>Сохранить письмо</button>"
        "<span class='meta'>%s</span></div></form>" % (esc(row["id"]), esc(letter), esc(note)),
        "<div class='card' style='margin-top:14px'><div class='meta'><b>Описание вакансии</b></div>%s</div>" % esc(row["description"]),
        "</main></body></html>",
    ]
    return "".join(parts)


def is_local_address(addr):
    """True для петлевых адресов (127.0.0.0/8, ::1): им вход не нужен."""
    host = (addr or "").split("%")[0].strip()
    if host in ("", "localhost"):
        return host == "localhost"
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_basic_auth(header):
    """'Basic dXNlcjpwYXNz' -> (user, password). None, если заголовок не Basic/битый."""
    if not header:
        return None
    parts = str(header).split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        raw = base64.b64decode(parts[1].strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if ":" not in raw:
        return None
    user, _, password = raw.partition(":")
    return user, password


def password_configured(settings):
    return bool((settings.web_auth_password or "").strip())


def auth_required(settings, client_address):
    """Вход нужен всегда, если задан пароль; без пароля — только для нелокальных адресов."""
    if password_configured(settings):
        return True
    return not is_local_address(client_address)


def auth_ok(settings, header):
    """Проверка заголовка Authorization (Basic) без утечки по времени."""
    user = (settings.web_auth_user or "").strip() or "hh"
    pair = parse_basic_auth(header)
    if not pair:
        return False
    got_user, got_password = pair
    user_ok = hmac.compare_digest(got_user.encode("utf-8"), user.encode("utf-8"))
    pass_ok = hmac.compare_digest(got_password.encode("utf-8"),
                                  (settings.web_auth_password or "").encode("utf-8"))
    return user_ok and pass_ok


class Handler(BaseHTTPRequestHandler):
    server_version = "hh-assistant/0.1"

    def _send(self, body, status=200, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _static(self, name):
        """Отдать файл из hhassist/static (только известные расширения)."""
        path = static_file(name)
        if not path:
            self._send("not found", status=404, ctype="text/plain; charset=utf-8")
            return
        with open(path, "rb") as fh:
            data = fh.read()
        self._send(data, ctype=STATIC_TYPES[os.path.splitext(path)[1].lower()],
                   headers={"Cache-Control": "public, max-age=604800"})


    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def _form(self):
        """Поля POST-формы (карточка присылает kind/note)."""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if not body:
            return {}
        return urllib.parse.parse_qs(body.decode("utf-8", "replace"))

    def _deny(self, text, status=403):
        """Страница отказа: 401 просит у браузера логин/пароль, 403 — просто запрет."""
        page = ("<html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>hh-assistant: вход</title><style>" + CSS + "</style></head><body>"
                "<main><div class='card' style='margin-top:10vh;max-width:560px'>"
                "<h3 style='margin:0 0 8px'>Доступ закрыт</h3>"
                "<p style='margin:0;color:#5d5470'>" + esc(text) + "</p>"
                "</div></main></body></html>")
        data = page.encode("utf-8")
        self.send_response(status)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="hh-assistant", charset="UTF-8"')
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _authorized(self):
        """Вход: пароль из .env или запрос с самого сервера (127.0.0.1)."""
        settings = get_settings()
        client = self.client_address[0] if self.client_address else ""
        if not auth_required(settings, client):
            return True
        if auth_ok(settings, self.headers.get("Authorization")):
            return True
        if password_configured(settings):
            self._deny("Введите логин и пароль из .env (WEB_AUTH_USER, WEB_AUTH_PASSWORD). "
                       "Логин по умолчанию — hh.", 401)
        else:
            self._deny("Сервер слушает сеть без пароля, поэтому запросы не с этого компьютера "
                       "отклонены: задайте в .env WEB_AUTH_USER и WEB_AUTH_PASSWORD "
                       "и перезапустите сервер.")
        return False

    def do_GET(self):
        if not self._authorized():
            return
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/static/"):
            self._static(path[len("/static/"):])
            return
        conn = storage.connect()
        try:
            if path == "/":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                threshold = int(qs.get("threshold", [get_settings().score_threshold])[0])
                msg = qs.get("msg", [""])[0]
                area = qs.get("area", [""])[0]
                region = qs.get("region", [""])[0]
                self._send(render_index(conn, threshold, limit=50, msg=msg, area=area,
                                        region=region))
            elif path == "/funnel":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                days = qs.get("days", [None])[0]
                self._send(render_funnel(conn, wait_days=int(days) if days else None))
            elif path.startswith("/v/"):
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                msg = qs.get("msg", [""])[0]
                page = render_card(conn, path.split("/")[2], msg=msg)
                self._send(page if page else "не найдено", status=200 if page else 404)
            elif path.startswith("/letter/"):
                row = storage.vacancy_with_letter(conn, path.split("/")[2])
                if not row or not row["letter"]:
                    self._send("письмо не найдено", status=404)
                else:
                    body = row["letter"].replace("\r\n", "\n").replace("\n", "\r\n")
                    self._send(body, ctype="text/plain; charset=utf-8")
            elif path == "/export":
                out_dir = os.path.join(BASE, "out")
                made = jobs.export_letters(conn, out_dir)
                self._send("<html><head><meta charset='utf-8'>"
                           "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                           "<title>Экспорт писем</title><style>" + CSS + "</style></head><body>"
                           "<main><div class='card'>Экспортировано писем: <b>%d</b> в %s</div>"
                           "<div class='actions'><a class='btn' href='/'>← назад</a></div>"
                           "</main></body></html>" % (made, esc(out_dir)))
            else:
                self._send("not found", status=404)
        finally:
            conn.close()

    def do_POST(self):
        if not self._authorized():
            return
        path = urllib.parse.urlparse(self.path).path
        conn = storage.connect()
        try:
            if path.startswith("/mark/"):
                queue.mark_sent(conn, path.split("/")[2])
                self._redirect("/")
            elif path.startswith("/skip/"):
                queue.skip(conn, path.split("/")[2])
                self._redirect("/")
            elif path.startswith("/status/"):
                vacancy_id = path.split("/")[2]
                form = self._form()
                report = apply_status(conn, vacancy_id, (form.get("kind") or [""])[0],
                                      (form.get("note") or [""])[0])
                self._redirect("/v/%s?msg=%s" % (urllib.parse.quote(vacancy_id),
                                                 urllib.parse.quote(report)))
            elif path.startswith("/letter/"):
                vacancy_id = path.split("/")[2]
                text = (self._form().get("text") or [""])[0]
                text = text.replace("\r\n", "\n").strip()
                if text:
                    storage.save_letter(conn, vacancy_id, text, model="manual", edited=True)
                    storage.log_event(conn, "letter_edit",
                                      {"vacancy_id": vacancy_id, "chars": len(text)})
                    msg = "письмо сохранено"
                else:
                    msg = "пустой текст не сохранён"
                self._redirect("/v/%s?msg=%s" % (urllib.parse.quote(vacancy_id),
                                                 urllib.parse.quote(msg)))
            elif path == "/queries":
                text = (self._form().get("queries") or [""])[0]
                report = save_queries(text)
                storage.log_event(conn, "queries_edit",
                                  {"queries": split_queries(text), "from": "web"})
                self._redirect("/?msg=" + urllib.parse.quote(report))
            elif path.startswith("/run/"):
                kind = path.split("/")[2]
                form = self._form()
                area = (form.get("area") or [""])[0]
                report = run_action(kind, conn, area=area)
                self._redirect("/?msg=" + urllib.parse.quote(report))
            else:
                self._send("not found", status=404)
        finally:
            conn.close()

    def log_message(self, fmt, *args):
        pass


def run(host="127.0.0.1", port=8765):
    areas.ensure()  # справочник субъектов РФ для фильтра «регион»
    settings = get_settings()
    httpd = ThreadingHTTPServer((host, port), Handler)
    scheme = "http"
    if settings.web_tls_cert or settings.web_tls_key:
        if not (settings.web_tls_cert and settings.web_tls_key):
            httpd.server_close()
            raise SystemExit("для HTTPS нужны оба пути: WEB_TLS_CERT и WEB_TLS_KEY в .env")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(settings.web_tls_cert, settings.web_tls_key)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    print("hh-assistant: %s://%s:%d  (Ctrl+C для остановки)" % (scheme, host, port))
    if not password_configured(settings) and not is_local_address(host):
        print("ВНИМАНИЕ: сервер открыт по сети без пароля. Запросы не с этого компьютера "
              "будут отклонены — задайте WEB_AUTH_USER и WEB_AUTH_PASSWORD в .env.")
    if scheme == "http" and password_configured(settings) and not is_local_address(host):
        print("ВНИМАНИЕ: пароль передаётся по HTTP в открытом виде. Включите HTTPS "
              "(WEB_TLS_CERT, WEB_TLS_KEY) или поставьте перед сервером nginx с TLS.")
    httpd.serve_forever()

