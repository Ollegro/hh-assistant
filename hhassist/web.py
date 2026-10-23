# -*- coding: utf-8 -*-
"""Локальный веб-интерфейс ассистента (стандартная библиотека, без зависимостей)."""
import html
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import jobs, llm, queue, storage
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


def render_index(conn, threshold, limit, msg=""):
    rows = queue.next_batch(conn, threshold=threshold, limit=limit)
    st = storage.stats(conn)
    left = queue.can_send_today(conn, get_settings().daily_limit)
    actions = (
        "<form method='post' action='/run/collect' style='display:inline'>"
        "<button class='btn' type='submit'>Собрать вакансии</button></form>"
        "<form method='post' action='/run/score' style='display:inline'>"
        "<button class='btn' type='submit'>Оценить</button></form>"
        "<form method='post' action='/run/letters' style='display:inline'>"
        "<button class='btn' type='submit'>Сгенерировать письма</button></form>"
        "<a class='btn' href='/export'>Экспорт писем в файлы</a>"
    )
    parts = [
        "<html><head><meta charset='utf-8'><title>hh-assistant</title><style>%s</style></head><body>" % CSS,
        "<header><b>hh-assistant</b><span>вакансий: %d · оценено: %d · писем: %d · откликов: %d · "
        "сегодня: %d · остаток лимита: %d</span><span>%s</span></header><main>" % (
            st["vacancies"], st["scored"], st["letters"], st["applications"], st["sent_today"], left,
            esc(llm.describe(get_settings()))),
        "<div class='actions'>%s</div>" % actions,
    ]
    if msg:
        parts.append("<div class='card'>%s</div>" % esc(msg))
    parts.append("<table><tr><th>Балл</th><th>Вакансия</th><th>Компания</th><th>Формат</th><th>Опыт</th>"
                 "<th>Зарплата</th><th>Вердикт</th><th></th></tr>")
    for r in rows:
        parts.append(
            "<tr><td class='score %s'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td><a class='btn' href='/v/%s'>открыть</a></td></tr>" % (
                score_class(r["score"]), r["score"] if r["score"] is not None else "—",
                esc(r["title"]), esc(r["company"]), esc(r["work_format"]), esc(r["experience"]),
                fmt_salary(r), esc(r["verdict"] or "—"), esc(r["id"])))
    if not rows:
        parts.append("<tr><td colspan='8'>Пока нет вакансий выше порога. Запустите "
                     "<code>python cli.py collect</code>, затем <code>score</code> и <code>letters</code>.</td></tr>")
    parts.append("</table></main></body></html>")
    return "".join(parts)


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


def run_action(kind, conn, limit=None):
    """Выполняет действие из веб-интерфейса: collect / score / letters. Возвращает отчёт."""
    s = get_settings()

    if kind == "collect":
        lim = limit or s.collect_limit
        queries = _queries()
        if s.collect_queries > 0:
            queries = queries[:s.collect_queries]
        if not queries:
            return "нет поисковых запросов: заполните config/queries.txt"
        saved, found, source, notes = jobs.collect_queries(conn, queries, s, limit=lim)
        storage.log_event(conn, "collect", {"queries": queries, "saved": saved,
                                            "found": found, "source": source, "from": "web"})
        report = "собрано вакансий: %d из %d найденных (источник: %s, запросов: %d, лимит: %d)" % (
            saved, found, source, len(queries), lim)
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


def render_card(conn, vacancy_id):
    row = storage.vacancy_with_letter(conn, vacancy_id)
    if not row:
        return None
    letter = row["letter"] or ""
    reasons = row["reasons"] or ""
    from . import matching
    match_line = matching.explain(dict(row), get_settings().profile) or "нет данных"
    parts = [
        "<html><head><meta charset='utf-8'><title>%s</title><style>%s</style></head><body>" % (esc(row["title"]), CSS),
        "<header><b><a href='/'>← к списку</a></b><span>%s</span></header><main>" % esc(row["company"]),
        "<div class='card'><div class='meta'><b>%s</b> · %s · опыт: %s · зарплата: %s · "
        "<span class='score %s'>балл %s</span></div>"
        "<div class='reasons'>%s</div></div>" % (
            esc(row["title"]), esc(row["work_format"] or "формат не указан"), esc(row["experience"] or "—"),
            fmt_salary(row), score_class(row["score"]), row["score"] if row["score"] is not None else "—",
            esc(reasons)),
        "<div class='card'><div class='reasons'><b>Сопоставление с профилем:</b> %s</div></div>" % esc(match_line),
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
        "<textarea id='letter'>%s</textarea>" % esc(letter),
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

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        conn = storage.connect()
        try:
            if path == "/":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                threshold = int(qs.get("threshold", [get_settings().score_threshold])[0])
                msg = qs.get("msg", [""])[0]
                self._send(render_index(conn, threshold, limit=50, msg=msg))
            elif path.startswith("/v/"):
                page = render_card(conn, path.split("/")[2])
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
            elif path.startswith("/run/"):
                kind = path.split("/")[2]
                report = run_action(kind, conn)
                self._redirect("/?msg=" + urllib.parse.quote(report))
            else:
                self._send("not found", status=404)
        finally:
            conn.close()

    def log_message(self, fmt, *args):
        pass


def run(host="127.0.0.1", port=8765):
    httpd = ThreadingHTTPServer((host, port), Handler)
    print("hh-assistant: http://%s:%d  (Ctrl+C для остановки)" % (host, port))
    httpd.serve_forever()

