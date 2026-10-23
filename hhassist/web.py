# -*- coding: utf-8 -*-
"""Локальный веб-интерфейс ассистента (стандартная библиотека, без зависимостей)."""
import html
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import analytics, areas, jobs, llm, queue, storage
from .config import BASE, get_settings

CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#f6f7f9;color:#1c1f24}
header{background:#fff;border-bottom:1px solid #e3e6ea;padding:14px 22px;display:flex;gap:18px;align-items:center}
header b{font-size:16px}
header span{color:#5a6472;font-size:13px}
main{padding:18px 22px;max-width:1200px}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e3e6ea;border-radius:8px;overflow:hidden}
th,td{padding:9px 12px;border-bottom:1px solid #eef0f3;font-size:13.5px;text-align:left;vertical-align:top}
th{background:#fafbfc;color:#5a6472;font-weight:600}
tr:last-child td{border-bottom:none}
.score{font-weight:700}
.s-high{color:#1a7f37}.s-mid{color:#9a6700}.s-low{color:#a40e26}
a{color:#1a4d8f;text-decoration:none}a:hover{text-decoration:underline}
.btn{display:inline-block;padding:7px 12px;border-radius:6px;border:1px solid #d0d7de;background:#fff;cursor:pointer;font-size:13px}
.btn.primary{background:#1a4d8f;border-color:#1a4d8f;color:#fff}
textarea{width:100%;min-height:420px;padding:12px;border:1px solid #d0d7de;border-radius:8px;font-family:ui-monospace,Consolas,monospace;font-size:13px;line-height:1.5}
.meta{color:#5a6472;font-size:13px;margin-bottom:10px}
.meta b{color:#1c1f24}
.actions{display:flex;gap:10px;margin:12px 0}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:16px;margin-bottom:14px}
.reasons{font-size:12.5px;color:#5a6472}
"""


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
        "<html><head><meta charset='utf-8'><title>hh-assistant</title><style>%s</style></head><body>" % CSS,
        "<header><b>hh-assistant</b><span>вакансий: %d · оценено: %d · писем: %d · откликов: %d · "
        "ответов: %d · сегодня: %d · остаток лимита: %d</span><span>%s</span></header><main>" % (
            st["vacancies"], st["scored"], st["letters"], st["applications"], st["replied"],
            st["sent_today"], left, esc(llm.describe(get_settings()))),
        "<div class='actions'>%s</div>" % actions,
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


def _queries():
    """Поисковые запросы: config/queries.txt (ручное переопределение) или из профиля-резюме."""
    path = os.path.join(BASE, "config", "queries.txt")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            items = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        if items:
            return items
    from . import matching
    return matching.build_queries(get_settings().profile)


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
        "<html><head><meta charset='utf-8'><title>%s</title><style>%s</style></head><body>" % (esc(row["title"]), CSS),
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


class Handler(BaseHTTPRequestHandler):
    server_version = "hh-assistant/0.1"

    def _send(self, body, status=200, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
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
                self._send("<html><meta charset='utf-8'><body><p>Экспортировано писем: %d в %s</p>"
                           "<p><a href='/'>← назад</a></p></body></html>" % (made, esc(out_dir)))
            else:
                self._send("not found", status=404)
        finally:
            conn.close()

    def do_POST(self):
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
    httpd = ThreadingHTTPServer((host, port), Handler)
    print("hh-assistant: http://%s:%d  (Ctrl+C для остановки)" % (host, port))
    httpd.serve_forever()

