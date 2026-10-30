# -*- coding: utf-8 -*-
"""Локальный веб-интерфейс ассистента (стандартная библиотека, без зависимостей)."""
import base64
import hmac
import html
import ipaddress
import json
import os
import re
import ssl
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import areas, guide, jobs, llm, queue, resume, scoring, storage, web_sessions, vacancy_filters
from .config import BASE, get_settings, load_profile

CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;min-height:100vh;color:#2b2438;
background:#c9d6e2 url('/static/bg.jpg?v=3') center/cover no-repeat fixed}
header{position:sticky;top:0;z-index:20;display:flex;gap:18px;align-items:center;flex-wrap:wrap;
padding:14px 22px;background:rgba(255,255,255,.78);backdrop-filter:blur(14px) saturate(1.15);
border-bottom:1px solid rgba(255,255,255,.65);box-shadow:0 6px 24px rgba(84,52,140,.10)}
header b{font-size:16px;letter-spacing:.2px}
header span{color:#5d5470;font-size:13px}
header .notice{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:10px;
background:rgba(29,78,216,.13);color:#1d4ed8;font-weight:600;font-size:13px;
box-shadow:inset 0 0 0 1px rgba(29,78,216,.18)}
main{padding:20px 22px 40px;max-width:1200px;margin:0 auto}
table{display:block;overflow-x:auto;border-collapse:separate;border-spacing:0;width:100%;
background:rgba(255,255,255,.88);
border:1px solid rgba(255,255,255,.70);border-radius:14px;box-shadow:0 10px 30px rgba(84,52,140,.12)}
th,td{padding:9px 12px;border-bottom:1px solid rgba(120,90,170,.14);font-size:13.5px;text-align:left;vertical-align:top}
/* длинные строки без пробелов (мусор из разметки, ссылки) не должны растягивать таблицу */
tbody td{overflow-wrap:anywhere}
th{background:rgba(250,247,255,.92);color:#5d5470;font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:none}
tbody tr:hover td{background:rgba(246,240,255,.92)}
.score{font-weight:700}
.s-high{color:#0f7a3d}.s-mid{color:#96660a}.s-low{color:#a01a2c}
.rate{white-space:nowrap}
.verdict{display:inline-block;margin-top:5px;padding:2px 8px;border-radius:8px;font-size:11px;
font-weight:600;background:rgba(120,90,170,.12);color:#5d5470}
.v-yes{background:rgba(15,122,61,.14);color:#0f7a3d}
.v-mid{background:rgba(150,102,10,.16);color:#96660a}
.v-no{background:rgba(160,26,44,.14);color:#a01a2c}
a{color:#6b3fc4;text-decoration:none}a:hover{text-decoration:underline}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:36px;
padding:0 14px;border-radius:10px;border:1px solid rgba(120,90,170,.25);
background:linear-gradient(135deg,#2563eb,#1d4ed8);color:#fff;cursor:pointer;font-size:13px;font-family:inherit;
line-height:1;white-space:nowrap;text-decoration:none;
box-shadow:0 2px 8px rgba(84,52,140,.08);transition:transform .12s ease,box-shadow .12s ease}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 16px rgba(84,52,140,.16);text-decoration:none}
.btn.primary{background:linear-gradient(135deg,#3b82f6,#1d4ed8);border-color:transparent;color:#fff}
.btn.danger{background:linear-gradient(135deg,#ef5350,#b71c1c);border-color:transparent;color:#fff}
.btn.danger:hover{box-shadow:0 6px 16px rgba(183,28,28,.28)}
.btn.success{background:linear-gradient(135deg,#34d399,#059669);border-color:transparent;color:#fff}
.btn.success:hover{box-shadow:0 6px 16px rgba(5,150,105,.28)}
.letter-dot{display:inline-block;width:13px;height:13px;border-radius:50%;background:#16a34a;
 box-shadow:0 0 0 3px rgba(22,163,74,.16);vertical-align:middle}
.card-company{color:#5d5470}
.stop-form{display:flex;align-items:center;margin:6px 0 12px}
td.vid{color:#5d5470;font-size:12.5px;white-space:nowrap}
select{height:36px;padding:0 10px;border-radius:10px;border:1px solid rgba(120,90,170,.25);
background:rgba(255,255,255,.92);color:#3a2d52;font-size:13px;font-family:inherit;line-height:1;
max-width:100%}
textarea{width:100%;min-height:420px;padding:12px;border:1px solid rgba(120,90,170,.22);border-radius:12px;
background:rgba(255,255,255,.94);color:#2b2438;font-family:ui-monospace,Consolas,monospace;
font-size:13px;line-height:1.5}
.meta{margin:4px 0 12px;padding:10px 14px;border-radius:12px;background:rgba(255,255,255,.84);
color:#5d5470;font-size:13px;box-shadow:0 4px 16px rgba(84,52,140,.08)}
.meta b{color:#2b2438}
.balance{position:fixed;right:16px;bottom:14px;z-index:15;padding:9px 13px;border-radius:12px;
background:rgba(255,255,255,.88);color:#5d5470;font-size:12.5px;
box-shadow:0 6px 20px rgba(84,52,140,.18);backdrop-filter:blur(10px)}
.balance b{color:#2b2438;font-size:13px}
@media (max-width:760px){.balance{right:8px;bottom:8px;font-size:11.5px}}
/* Индикатор прогона: общая плашка, внутри — своя картинка под каждое действие.
   Сбор: книга с перелистываемыми страницами и лупа на орбите.
   Оценка: стопка листов, на которых по очереди проступают галочки.
   Письма: лист, который заполняется строками, и карандаш, идущий по строкам.
   Всё на CSS — без картинок и скриптов; при prefers-reduced-motion движение
   отключается, текст остаётся. */
.run-visual{display:flex;align-items:center;gap:16px;margin:4px 0 12px;padding:12px 18px;
border-radius:14px;background:rgba(255,255,255,.86);box-shadow:0 6px 20px rgba(84,52,140,.10)}
.run-visual b{font-size:14px;color:#2b2438}
.run-visual .hint{color:#5d5470;font-size:12.5px;margin-top:3px}
.run-visual .scene{position:relative;width:124px;height:96px;flex:0 0 auto}
/* --- сбор: книга и лупа --- */
.book{position:absolute;left:12px;bottom:6px;width:100px;height:58px;perspective:520px}
.book .cover{position:absolute;inset:0;border-radius:5px 8px 8px 5px;
background:linear-gradient(135deg,#8b5cf6,#2563eb);box-shadow:0 6px 14px rgba(37,99,235,.26)}
.book .page{position:absolute;top:4px;bottom:4px;width:48%;background:#fffdf6;
border:1px solid rgba(120,90,170,.18)}
.book .page.left{left:2%;border-radius:3px 0 0 3px}
.book .page.right{right:2%;border-radius:0 3px 3px 0}
.book .page::after,.book .flip::after{content:'';position:absolute;left:10%;right:10%;top:16%;bottom:16%;
background:repeating-linear-gradient(180deg,rgba(37,99,235,.20) 0 1.5px,transparent 1.5px 7px)}
.book .flip{position:absolute;top:4px;bottom:4px;right:2%;width:48%;border-radius:0 3px 3px 0;
background:linear-gradient(90deg,#fffdf6,#efe9ff);border:1px solid rgba(120,90,170,.20);
transform-origin:left center;backface-visibility:hidden;
animation:page-flip 2.8s ease-in-out infinite}
.book .flip.two{animation-delay:1.4s}
@keyframes page-flip{0%,10%{transform:rotateY(0deg)}46%,64%{transform:rotateY(-158deg)}
92%,100%{transform:rotateY(0deg)}}
.lens-orbit{position:absolute;left:62px;top:44px;width:0;height:0;
animation:lens-spin 4s linear infinite}
.lens{position:absolute;left:-13px;top:-44px;width:26px;height:26px;border-radius:50%;
border:3px solid #1d4ed8;
background:radial-gradient(circle at 35% 30%,rgba(255,255,255,.95),rgba(147,197,253,.50));
box-shadow:0 3px 8px rgba(29,78,216,.35);animation:lens-spin-rev 4s linear infinite}
.lens::after{content:'';position:absolute;left:17px;top:17px;width:3px;height:14px;border-radius:2px;
background:#1d4ed8;transform:rotate(-45deg);transform-origin:top left}
@keyframes lens-spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes lens-spin-rev{from{transform:rotate(0deg)}to{transform:rotate(-360deg)}}
/* --- оценка: стопка листов, на каждом пункте по очереди проступает галочка --- */
.sheets{position:absolute;inset:8px 14px 8px 12px}
.sheets .sheet{position:absolute;border-radius:4px;background:#fffdf6;
border:1px solid rgba(120,90,170,.22);box-shadow:0 3px 9px rgba(84,52,140,.12)}
.sheets .sheet.back{left:16px;top:18px;width:76px;height:56px;transform:rotate(-7deg)}
.sheets .sheet.mid{left:10px;top:11px;width:80px;height:58px;transform:rotate(-4deg)}
.sheets .checklist{position:absolute;left:4px;top:3px;width:86px;height:62px;border-radius:4px;
background:#fffdf6;border:1px solid rgba(120,90,170,.22);
box-shadow:0 4px 12px rgba(84,52,140,.16)}
.sheets .row{position:absolute;left:11px;right:24px;height:3px;border-radius:2px;
background:rgba(120,90,170,.28)}
.sheets .row.r1{top:14px}
.sheets .row.r2{top:29px}
.sheets .row.r3{top:44px}
.sheets .row::after{content:'';position:absolute;right:-19px;top:-5px;width:15px;height:8px;
border-left:3px solid #16a34a;border-bottom:3px solid #16a34a;
transform:rotate(-45deg) scale(0);transform-origin:left center;
animation:tick-in 3s ease-in-out infinite}
.sheets .row.r2::after{animation-delay:.5s}
.sheets .row.r3::after{animation-delay:1s}
@keyframes tick-in{0%,4%{transform:rotate(-45deg) scale(0);opacity:0}
14%,86%{transform:rotate(-45deg) scale(1);opacity:1}
97%,100%{transform:rotate(-45deg) scale(0);opacity:0}}
/* --- письма: лист, который заполняется строками, и карандаш --- */
.writing{position:absolute;inset:6px 30px 6px 10px}
.writing .paper{position:absolute;inset:0;border-radius:4px;background:#fffdf6;
border:1px solid rgba(120,90,170,.22);box-shadow:0 3px 10px rgba(84,52,140,.13)}
.writing .ink{position:absolute;left:10px;height:4px;border-radius:2px;
background:rgba(37,99,235,.42);transform-origin:left center;transform:scaleX(0);
animation:ink-write 4.2s linear infinite}
.writing .ink.i1{top:13px;width:62px}
.writing .ink.i2{top:25px;width:48px;animation-delay:.35s}
.writing .ink.i3{top:37px;width:66px;animation-delay:.7s}
.writing .ink.i4{top:49px;width:40px;animation-delay:1.05s}
.writing .ink.i5{top:61px;width:56px;animation-delay:1.4s}
@keyframes ink-write{0%{transform:scaleX(0)}8%,96%{transform:scaleX(1)}
100%{transform:scaleX(0)}}
.writing .pencil{position:absolute;right:-4px;width:10px;height:26px;border-radius:2px;
background:linear-gradient(180deg,#fbbf24,#b45309);transform:rotate(16deg);
box-shadow:0 2px 6px rgba(120,60,0,.28);animation:pencil-move 4.2s ease-in-out infinite}
.writing .pencil::after{content:'';position:absolute;left:2px;bottom:-6px;width:0;height:0;
border-left:3px solid transparent;border-right:3px solid transparent;border-top:7px solid #1f2937}
@keyframes pencil-move{0%,4%{top:2px}18%{top:14px}34%{top:26px}50%{top:38px}66%{top:50px}
82%{top:60px}100%{top:2px}}
@media (prefers-reduced-motion: reduce){.book .flip,.lens-orbit,.lens,.sheets .row::after,
.writing .ink,.writing .pencil{animation:none}}
@media (max-width:760px){.run-visual .scene{width:100px;height:80px;transform:scale(.82);
transform-origin:left bottom}}
.actions{display:flex;gap:10px;margin:12px 0;flex-wrap:wrap;align-items:center}
.actions form{display:flex;gap:10px;align-items:center;margin:0}
.toolbar{display:flex;gap:10px;margin:14px 0;padding:10px 12px;flex-wrap:wrap;align-items:center;
border-radius:14px;background:rgba(255,255,255,.86);border:1px solid rgba(255,255,255,.72);
backdrop-filter:blur(12px) saturate(1.1);box-shadow:0 8px 24px rgba(84,52,140,.10)}
.toolbar .buttons{display:flex;gap:10px;flex-wrap:nowrap;overflow-x:auto;align-items:center;
 flex:1 1 100%;padding-bottom:2px}
.toolbar .buttons .btn{flex:0 0 auto}
.toolbar .region-form{display:flex;gap:10px;margin:0;align-items:center;flex:0 0 auto}
.toolbar form.action-form{display:inline-flex;margin:0;padding:0}
header .btn{height:32px;padding:0 12px;font-size:12.5px}
.card{background:rgba(255,255,255,.90);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.72);
border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 10px 28px rgba(84,52,140,.12)}
.card table{display:table;width:100%}
.reasons{font-size:12.5px;color:#5d5470}
.rules-list{margin:6px 0 4px 18px;padding:0;font-size:12.5px;color:#5d5470}
.rules-list li{margin:3px 0}
.desc{white-space:pre-line;font-size:13px;line-height:1.55;color:#332b45}
dialog{max-width:720px;width:calc(100% - 36px);border:none;border-radius:16px;padding:20px 22px;
color:#2b2438;background:#fdfbff;box-shadow:0 30px 70px rgba(40,20,70,.35)}
dialog::backdrop{background:rgba(40,20,70,.45)}
dialog h3{margin:0 0 10px;font-size:17px}
dialog table{display:table;width:100%;background:transparent;border:none;box-shadow:none}
dialog th,dialog td{padding:5px 8px;font-size:12.5px;border-bottom:1px solid rgba(120,90,170,.14)}
dialog .points{white-space:nowrap;font-weight:700;color:#5d5470}
textarea.queries-text{min-height:140px}
textarea.resume-text{min-height:200px}
input[type=file]{font-size:13px;color:#3a2d52;background:rgba(255,255,255,.92);
 padding:8px 10px;border:1px solid rgba(120,90,170,.22);border-radius:10px;max-width:100%}
.profile-table th:first-child{width:190px}
.profile-table td{white-space:pre-line}
.guide h1{font-size:22px;margin:0 0 10px}
.guide h2{font-size:17px;margin:0 0 8px}
.guide p,.guide li{font-size:13.5px;line-height:1.65;color:#332b45}
.guide ul,.guide ol{margin:8px 0 4px 20px;padding:0}
.guide li{margin:4px 0}
.guide .toc{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.guide .toc a{font-size:12.5px;padding:5px 10px;border-radius:8px;background:rgba(120,90,170,.12)}
.guide .note,.guide .warn{padding:9px 12px;border-radius:10px;margin:10px 0;font-size:13px}
.guide .note{border-left:3px solid #3b82f6;background:rgba(59,130,246,.09)}
.guide .warn{border-left:3px solid #b71c1c;background:rgba(183,28,28,.09)}
.guide table{display:table;width:100%;background:transparent;border:none;box-shadow:none}
.guide th,.guide td{font-size:13px;padding:7px 9px}
.guide code{background:rgba(120,90,170,.12);padding:1px 5px;border-radius:5px;font-size:12.5px}
.guide .step{font-weight:700;color:#4b3a6b}
@media print{header,.toolbar,.actions{display:none}body{background:#fff}
 .card{box-shadow:none;border:1px solid #ddd}.guide .toc{display:none}}
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
    """Цвет оценки: школьные 5 и 4 — зелёный, 3 — жёлтый, 2 и 1 — красный."""
    grade = scoring.school_grade(score)
    if grade is None:
        return "s-low"
    if grade >= 4:
        return "s-high"
    if grade == 3:
        return "s-mid"
    return "s-low"


def grade_cell(row):
    """Оценка как в школе: крупная цифра 1–5 (внутренние баллы наружу не выводим)."""
    grade = scoring.school_grade(row["score"])
    if grade is None:
        return "<span class='score s-low' title='вакансия ещё не оценена'>—</span>"
    return ("<span class='score %s' title='оценка %d из 5'>%d</span>" % (
        score_class(row["score"]), grade, grade))


def letter_cell(row):
    """Письмо готово? Зелёный кружок, если текст есть (сгенерирован или написан вручную).

    Если письма нет, ячейка остаётся пустой — как просил заказчик; при наведении
    на кружок видно, сгенерировано письмо или отредактировано руками.
    """
    try:
        has_letter = row["has_letter"]
        edited = row["letter_edited"]
    except (IndexError, KeyError):
        has_letter, edited = 0, 0
    if not has_letter:
        return ""
    kind = "написано вручную" if edited else "сгенерировано"
    return "<span class='letter-dot' title='письмо %s'></span>" % kind


def verdict_badge(verdict):
    """Цветной бейдж вердикта: единственный ориентир при выборе вакансии."""
    text = (verdict or "").strip()
    if not text:
        return ""
    low = text.lower()
    if low.startswith("не ") or "не отклик" in low:
        cls = "v-no"
    elif "условно" in low:
        cls = "v-mid"
    else:
        cls = "v-yes"
    return "<span class='verdict %s'>%s</span>" % (cls, esc(text))


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
    """Регионы для единственного селекта: id субъекта hh + счётчики из базы.

    Значение option — id региона hh (он же уходит в сбор), подпись — название
    с числом вакансий. Регионы, которых нет в справочнике, отдаются названием:
    такой вариант годится только для фильтра.
    """
    counts = {row["region"]: row["cnt"] for row in storage.region_counts(conn) if row["region"]}
    total = conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0]
    pairs = [(areas.ALL_RUSSIA[0], "вся Россия (%d)" % total)]
    known = set()
    for area_id, name in areas.cached():
        if area_id == areas.ALL_RUSSIA[0]:
            continue
        known.add(name)
        pairs.append((area_id, "%s (%d)" % (name, counts[name]) if counts.get(name) else name))
    # регионы из базы, которых нет в справочнике (например, «Москва» вместо субъекта)
    for name in sorted(set(counts) - known):
        pairs.append((name, "%s (%d)" % (name, counts[name])))
    return pairs


def _resolve_region(value):
    """Значение селекта региона -> (id для формы, название субъекта для фильтра).

    Принимает и id ('2019'), и название ('Московская область', старые ссылки).
    «Россия» (113) означает «без фильтра по региону».
    """
    text = str(value or "").strip()
    if not text:
        return "", ""
    if text.isdigit():
        area_id = int(text)
        if area_id == areas.ALL_RUSSIA[0]:
            return str(area_id), ""
        name = areas.name_of(area_id)
        return (str(area_id), name) if name else (str(area_id), "")
    found = areas.region_id(text)
    return (str(found) if found else "", text)


def _action_form(action, label, css, region_value, hidden_filters, confirm=False,
                 title="", onclick=""):
    """Кнопка действия отдельной POST-формой с явным регионом.

    Раньше кнопки жили в GET-форме селекта и полагались на `formmethod='post'`:
    если браузер это не учитывал (или регион меняли и кликали до перезагрузки),
    POST приходил без региона, сервер молча брал «вся Россия» и собирал страну
    целиком. Теперь регион и фильтры лежат в теле самой формы: что на странице
    выбрано — то и уйдёт на сервер.
    """
    extra = "<input type='hidden' name='confirm' value='1'>" if confirm else ""
    return ("<form method='post' action='%s' class='action-form'%s>"
            "<input type='hidden' name='region' value='%s'>%s%s"
            "<button class='btn%s' type='submit'%s>%s</button></form>" % (
                action, (" title='%s'" % esc(title)) if title else "",
                esc(region_value), hidden_filters, extra, css,
                (" onclick=\"%s\"" % onclick) if onclick else "", label))


REGION_SYNC_JS = (
    "<script>(function(){var s=document.getElementById('region-select');if(!s)return;"
    "var forms=document.querySelectorAll('form.action-form');"
    "for(var i=0;i<forms.length;i++){(function(f){f.addEventListener('submit',function(){"
    "var h=f.querySelector(\"input[name='region']\");if(h){h.value=s.value;}});})(forms[i]);}"
    "})();</script>")


def render_index(conn, limit, msg="", region="", filters=None, page=1):
    region_value, region_name = _resolve_region(region)
    filters = vacancy_filters.selected(filters or {})
    all_rows = queue.next_batch(conn, limit=None, region=region_name)
    matching_rows = vacancy_filters.apply(all_rows, filters)
    total = len(matching_rows)
    pages = max(1, (total + limit - 1) // limit)
    try:
        page = max(1, min(int(page), pages))
    except (TypeError, ValueError):
        page = 1
    rows = matching_rows[(page - 1) * limit:page * limit]
    hidden_filters = "".join("<input type='hidden' name='f_%s' value='%s'>" % (col, esc(v))
                             for col, values in filters.items() for v in values)
    st = storage.stats(conn)
    left = queue.can_send_today(conn, get_settings().daily_limit)
    # Селект региона — в GET-форме (он же фильтр списка), кнопки действий —
    # отдельные POST-формы с hidden-полем region. Раньше кнопки лежали в GET-форме
    # и зависели от formmethod: если регион не доезжал, сервер молча собирал всю
    # Россию. Теперь регион всегда в теле запроса вместе с текущими фильтрами.
    confirm_text = "Очистить всю базу вакансий, оценок, писем и откликов? Это нельзя отменить."
    region_options = _options(_region_options(conn), region_value)
    toolbar = "".join([
        "<div class='toolbar'>",
        "<form method='get' action='/' class='region-form'>", hidden_filters,
        "<select name='region' id='region-select' title='регион: сбор и фильтр списка'",
        " onchange='this.form.submit()'>", region_options, "</select></form>",
        "<span class='buttons'>",
        _action_form("/run/collect", "Собрать вакансии", " primary",
                     region_value, hidden_filters),
        _action_form("/run/score", "Оценить", "", region_value, hidden_filters),
        _action_form("/run/letters", "Сгенерировать письма", "",
                     region_value, hidden_filters),
        "<a class='btn' href='/export'>Экспорт писем в файлы</a>",
        "<a class='btn success' href='/help'>Руководство</a>",
        "<a class='btn danger' href='/profile'>Профиль соискателя</a>",
        _action_form("/reset", "Очистить базу", " danger", region_value,
                     hidden_filters, confirm=True, title=confirm_text,
                     onclick="return confirm('%s');" % confirm_text),
        "</span></div>",
    ])
    # Сообщение о действии («запущено: сбор вакансий…») — в шапке, на самом верху:
    # раньше это была карточка под панелью, и её приходилось искать глазами.
    notice = ("<span class='notice'>%s</span>" % esc(msg)) if msg else ""
    parts = [
        "<html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        + ("<meta http-equiv='refresh' content='5'>" if RUN["running"] else "") +
        "<title>hh-assistant</title><style>%s</style></head><body>" % CSS,
        "<header>%s<b>hh-assistant</b><span>вакансий: %d · оценено: %d · писем: %d · "
        "откликов: %d · ответов: %d · сегодня: %d · остаток лимита: %d</span>"
        "<span>%s</span></header><main>" % (
            notice,
            st["vacancies"], st["scored"], st["letters"], st["applications"], st["replied"],
            st["sent_today"], left, esc(llm.describe(get_settings()))),
        toolbar,
        _queries_block(),
    ]
    progress = run_progress()
    if progress:
        parts.append("<div class='meta'>%s</div>" % esc(progress))
    indicator = run_indicator()
    if indicator:
        parts.append(indicator)
    if RUN["running"]:
        # Кнопка остановки видна только во время прогона: сбор идёт минутами,
        # и ошибочный запуск нужно уметь прервать, не перезапуская сервис.
        parts.append(
            "<form method='post' action='/run/stop' class='stop-form'>"
            "<button class='btn danger' type='submit'%s>Остановить %s</button>"
            "<span class='meta' style='margin:0 0 0 10px;padding:0;background:none;"
            "box-shadow:none'>уже сохранённое останется в базе</span></form>" % (
                " disabled" if RUN.get("stop") else "", esc(RUN["title"])))
    collect = storage.last_collect(conn)
    if collect:
        # Время берём из журнала событий: плашка RUN живёт в памяти и после
        # перезапуска сервера пуста, а сбор в базе остаётся.
        line = "последний сбор вакансий: <b>%s</b>" % esc(storage.fmt_moscow(collect["ts"]))
        summary = storage.collect_summary(collect.get("payload"))
        if summary:
            line += " · " + esc(summary)
        parts.append("<div class='meta'>%s</div>" % line)
    warning = parser_warning(conn)
    if warning:
        parts.append("<div class='card'>%s</div>" % esc(warning))
    parts.append(
        "<div class='meta'>собрано в базе: <b>%d</b> · оценено: <b>%d</b> · "
        "без оценки: <b>%d</b> · в списке: <b>%d</b></div>" % (
            st["vacancies"], st["scored"], max(0, st["vacancies"] - st["scored"]),
            len(rows)))
    parts.append("<div class='meta'>Найдено по фильтрам: <b>%d</b> · страница %d из %d</div>" %
                 (total, page, pages))
    parts.append("<form id='column-filters' method='get' action='/'></form>"
                 "<input form='column-filters' type='hidden' name='region' value='%s'>" % esc(region_value))
    parts.append("<style>.column-filter{display:inline-block;vertical-align:top}"
                 ".column-filter summary{cursor:pointer;color:#1d4ed8}"
                 ".filter-menu{white-space:normal;min-width:190px;max-width:280px}"
                 ".filter-values{max-height:220px;overflow:auto;margin:8px 0}"
                 ".filter-values label{display:block;font-weight:400;padding:4px}"
                 ".filter-menu input[type=search]{width:100%;padding:7px}</style>")
    parts.append("<table><thead><tr><th>ID</th>")
    # Значения всех колонок считаем за один проход по строкам (options_all),
    # а не пересчитываем меню по разу на колонку.
    menu = vacancy_filters.options_all(all_rows, filters)
    for col, title in vacancy_filters.COLUMNS:
        active = " (%d)" % len(filters[col]) if filters[col] else ""
        parts.append("<th>%s <details class='column-filter'><summary aria-label='Фильтр: %s'>"
                     "▼%s</summary><div class='filter-menu'>" % (title, title, active))
        parts.append("<input type='search' placeholder='Поиск значений' aria-label='Поиск значений: %s' "
                     "oninput=\"var q=this.value.toLocaleLowerCase();"
                     "this.nextElementSibling.querySelectorAll('label').forEach(function(l){"
                     "l.hidden=!l.textContent.toLocaleLowerCase().includes(q)});\">" % title)
        parts.append("<div class='filter-values'>")
        for value in menu.get(col, []):
            parts.append("<label><input form='column-filters' type='checkbox' name='f_%s' "
                         "value='%s'%s> %s</label>" % (col, esc(value),
                         " checked" if value in filters[col] else "", esc(value)))
        parts.append("</div><button class='btn' form='column-filters' type='submit'>Применить</button>"
                     " <button class='btn' type='button' onclick=\""
                     "this.parentElement.querySelectorAll('input[type=checkbox]').forEach(function(i){"
                     "i.checked=false});document.getElementById('column-filters').requestSubmit();\">"
                     "Все значения</button></div></details></th>")
    parts.append("<th></th></tr></thead><tbody>")
    for r in rows:
        parts.append(
            "<tr><td class='vid' title='id вакансии на hh.ru'>%s</td>"
            "<td class='rate'>%s%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td class='letter'>%s</td>"
            "<td><a class='btn' target='_blank' rel='noopener'"
            " href='/v/%s'>Открыть</a></td></tr>" % (
                esc(r["id"]), grade_cell(r), verdict_badge(r["verdict"]),
                esc(r["title"]), esc(r["company"]), esc(r["area"] or r["region"]),
                esc(r["work_format"]), esc(r["experience"]),
                fmt_salary(r), letter_cell(r), esc(r["id"])))
    if not rows:
        parts.append("<tr><td colspan='10'>Ни одной вакансии по фильтру. Соберите вакансии "
                     "кнопкой выше, затем нажмите «Оценить».</td></tr>")
    parts.append("</tbody></table><div class='actions'>")
    query = {"region": region_value}
    query.update({"f_" + col: values for col, values in filters.items() if values})
    for number, label in ((page - 1, "← Назад"), (page + 1, "Далее →")):
        if 1 <= number <= pages:
            href = "/?" + urllib.parse.urlencode(dict(query, page=number), doseq=True)
            parts.append("<a class='btn' href='%s'>%s</a>" % (esc(href), label))
    parts.append("<a class='btn' href='/?%s'>Сбросить фильтры</a>" %
                 esc(urllib.parse.urlencode({"region": region_value})))
    parts.append("<form method='post' action='/logout'><button class='btn' type='submit'>"
                 "Выйти</button></form></div></main>")
    parts.append(balance_box())
    parts.append(REGION_SYNC_JS)
    parts.append("</body></html>")
    return "".join(parts)


def balance_info():
    """Баланс аккаунта LLM для плашки в углу: подпись, сумма, время проверки.

    Ключ наружу не попадает: llm.balance возвращает только суммы и время.
    """
    s = get_settings()
    if not llm.balance_supported(s) or not llm.is_enabled(s):
        return {}
    data = llm.balance(s)
    label = (s.llm_provider or "LLM").strip()
    if label.lower() == "deepseek":
        label = "DeepSeek"
    text = llm.format_balance(data)
    if data.get("ok") and data.get("available") is False:
        text = "⚠ " + text
    checked = storage.fmt_moscow(data.get("checked"), with_label=False) if data.get("checked") else ""
    return {"label": label, "text": text, "checked": checked}


def balance_box():
    """Плашка «сколько денег на счёте LLM» в правом нижнем углу главной.

    Значение подгружается отдельным запросом `/balance`: сеть не задерживает
    страницу, а кэш на минуту не даёт дёргать API при автообновлении.
    """
    s = get_settings()
    if not llm.balance_supported(s) or not llm.is_enabled(s):
        return ""
    label = (s.llm_provider or "LLM").strip()
    if label.lower() == "deepseek":
        label = "DeepSeek"
    return ("<div class='balance' id='llm-balance' title='остаток на счёте провайдера LLM'>"
            "<span>%s: </span><b>…</b></div><script>(function(){"
            "var box=document.getElementById('llm-balance');if(!box)return;"
            "fetch('/balance',{headers:{'Accept':'application/json'}})"
            ".then(function(r){return r.json();}).then(function(d){"
            "if(!d||!d.text)return;box.textContent='';"
            "var name=document.createElement('span');name.textContent=(d.label||'LLM')+': ';"
            "var sum=document.createElement('b');sum.textContent=d.text;"
            "var when=document.createElement('span');"
            "when.textContent=d.checked?(' · проверено '+d.checked+' МСК'):'';"
            "box.appendChild(name);box.appendChild(sum);box.appendChild(when);"
            "}).catch(function(){});})();</script>" % esc(label))


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
    """Поисковые запросы: config/queries.txt (ручное переопределение) или из профиля-резюме.

    Файл существует — значит источник истины он, даже если внутри только заголовок
    (это состояние «список очищен», сбор попросит заполнить запросы). Подстановка
    целевых ролей из профиля работает только пока файла нет вовсе.
    """
    path = _queries_path()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return split_queries(f.read())
    from . import matching
    return matching.build_queries(get_settings().profile)


def clear_queries():
    """Очищает список запросов: в config/queries.txt остаётся только заголовок.

    Осознанное действие пользователя (кнопка с подтверждением): после него сбор
    честно ответит «нет поисковых запросов», а не подставит роли из профиля.
    """
    path = _queries_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(QUERIES_HEADER + "\n")
    return "запросы очищены: сбор попросит заполнить список (config/queries.txt)"


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
        "по одному в строке, строки с # игнорируются. Правки в списке начинают "
        "действовать только после кнопки «Сохранить запросы»: она записывает их в "
        "config/queries.txt, откуда их берёт сбор.</div>"
        "<form method='post' action='/queries/clear' id='queries-clear'"
        " onsubmit=\"return confirm('Очистить список запросов? Пока список пуст, "
        "сбор работать не будет — впишите новые запросы и сохраните.');\"></form>"
        "<form method='post' action='/queries'>"
        "<textarea class='queries-text' name='queries'>%s</textarea>"
        "<div class='actions'><button class='btn primary' type='submit'"
        " title='записать список в config/queries.txt — из него берутся запросы для сбора'>"
        "Сохранить запросы</button>"
        "<button class='btn danger' type='submit' form='queries-clear'"
        " title='убрать все запросы: в config/queries.txt останется только заголовок'>"
        "Очистить запросы</button>"
        "<span class='meta'>%s</span></div></form></div>"
        % (esc(text), note))


def parse_multipart(body, content_type):
    """multipart/form-data -> ({поле: [значения]}, {поле: (имя файла, байты)}).

    Стандартной библиотекой: `cgi` удалён в Python 3.13, а формат простой —
    части разделены `--boundary`, шапка части кончается пустой строкой.
    """
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not match:
        return {}, {}
    marker = b"--" + match.group(1).encode("utf-8")
    fields, files = {}, {}
    for chunk in (body or b"").split(marker):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        head, separator, data = chunk.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = head.decode("utf-8", "replace")
        name_match = re.search(r'name="([^"]*)"', headers)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', headers)
        if filename_match:
            files[name] = (filename_match.group(1), data)
        else:
            fields.setdefault(name, []).append(data.decode("utf-8", "replace"))
    return fields, files


def _salary_text(source):
    """Вилка профиля одной строкой."""
    low, high = source.get("salary_from"), source.get("salary_to")
    if low and high:
        return "%s–%s ₽" % (resume.money(low), resume.money(high))
    if low:
        return "от %s ₽" % resume.money(low)
    if high:
        return "до %s ₽" % resume.money(high)
    return "—"


def _profile_rows(profile):
    """Что сейчас в профиле: пары (подпись, текст). Контакты не показываем."""
    stack = profile.get("stack") or {}
    work = ["%s — %s (%s)" % (job.get("company") or "—", job.get("role") or "—",
                              job.get("period") or "—")
            for job in profile.get("work_history") or []]
    return [
        ("Профессия", profile.get("headline") or "—"),
        ("Целевые роли", ", ".join(profile.get("target_roles") or []) or "—"),
        ("Уровень", profile.get("seniority") or "—"),
        ("Ключевой стек", ", ".join(stack.get("must") or []) or "—"),
        ("Дополнительно", ", ".join(stack.get("nice") or []) or "—"),
        ("Вилка", _salary_text(profile)),
        ("Формат работы", ", ".join(profile.get("formats") or []) or "—"),
        ("Опыт работы", "\n".join(work) or "—"),
        ("Образование", "; ".join(profile.get("education") or []) or "—"),
        ("Языки", ", ".join("%s — %s" % (key, value)
                           for key, value in (profile.get("languages") or {}).items()) or "—"),
        ("Чего нет (gaps)", "; ".join(profile.get("gaps") or []) or "—"),
        ("Факты для письма", "\n".join("• " + fact for fact in profile.get("facts") or [])
         or "—"),
        ("Резюме загружено", profile.get("resume_updated_at") or "ещё не загружалось"),
    ]


def render_profile(msg="", fields=None, method="", note="", diff=None):
    """Страница профиля: что сохранено, загрузка резюме, предпросмотр и «было → стало»."""
    profile = get_settings().profile
    pdf_ok = resume.pdf_supported()
    saved_path, saved_name, saved_stamp = resume.saved_resume()
    backup_path = resume.latest_backup()
    parts = [
        "<html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Профиль — hh-assistant</title><style>%s</style></head><body>" % CSS,
        "<header><b>hh-assistant</b><span>профиль соискателя</span>"
        "<span style='flex:1'></span><a class='btn' href='/'>← К вакансиям</a></header><main>",
    ]
    if msg:
        parts.append("<div class='card'>%s</div>" % esc(msg))
    parts.append("<div class='card'><div class='meta'><b>Что сейчас в профиле</b> — "
                 "эти данные участвуют в оценке вакансий и в письмах</div>"
                 "<table class='profile-table'>")
    for label, value in _profile_rows(profile):
        parts.append("<tr><th>%s</th><td>%s</td></tr>" % (esc(label), esc(value)))
    parts.append("</table></div>")

    parts.append(
        "<div class='card'><div class='meta'><b>Обновить профиль из резюме</b> — %s "
        "или вставленный текст. Профиль обновится сразу, прежняя версия уйдёт в бэкап "
        "(рядом будет кнопка «Отменить»).</div>"
        "<form method='post' action='/profile' enctype='multipart/form-data'>"
        "<div class='actions'><input type='file' name='file' accept='%s'>"
        "<span class='meta'>до %d МБ</span></div>"
        "<textarea class='resume-text' name='text' placeholder='…или вставьте текст резюме "
        "целиком: должность, опыт, навыки, образование'></textarea>"
        "<div class='actions'>"
        "<button class='btn primary' type='submit' name='action' value='parse_apply'>"
        "Загрузить и обновить профиль</button>"
        "<button class='btn' type='submit' name='action' value='parse'>"
        "Сначала показать, что распознано</button>"
        "<span class='meta'>с LLM разбор занимает до минуты; без ключа работают правила"
        "</span></div></form></div>" % (
            "файл PDF, DOCX или TXT" if pdf_ok else
            "файл DOCX или TXT (PDF — если установлена библиотека pypdf)",
            ".pdf,.docx,.txt" if pdf_ok else ".docx,.txt",
            resume.MAX_BYTES // (1024 * 1024)))

    tools = []
    if saved_path:
        tools.append(
            "<form method='post' action='/profile' style='display:inline'>"
            "<input type='hidden' name='action' value='reread'>"
            "<button class='btn' type='submit'>Перечитать сохранённое резюме</button></form>"
            "<span class='meta'>%s · %s</span>" % (esc(saved_name), esc(saved_stamp)))
    if backup_path:
        tools.append(
            "<form method='post' action='/profile' style='display:inline'>"
            "<input type='hidden' name='action' value='revert'>"
            "<button class='btn' type='submit'>Отменить последнее обновление</button></form>"
            "<span class='meta'>бэкап %s</span>" % esc(os.path.basename(backup_path)))
    if tools:
        parts.append("<div class='card'><div class='meta'><b>Резюме и откат</b> — "
                     "профиль перечитывается из сохранённой копии или откатывается "
                     "к прежней версии</div><div class='actions'>%s</div></div>"
                     % "".join(tools))

    if diff:
        parts.append("<div class='card'><div class='meta'><b>Что изменилось в профиле</b>"
                     " · полей: %d</div><table class='profile-table'>"
                     "<tr><th>Поле</th><th>Было</th><th>Стало</th></tr>" % len(diff))
        for label, was, now in diff:
            parts.append("<tr><th>%s</th><td>%s</td><td>%s</td></tr>"
                         % (esc(label), esc(was), esc(now)))
        parts.append("</table></div>")

    if fields:
        rows = resume.describe(fields)
        payload = json.dumps(fields, ensure_ascii=False)
        parts.append(
            "<div class='card'><div class='meta'><b>Распознано</b> · метод: %s%s</div>"
            "<table class='profile-table'>" % (
                esc(method or "правила"),
                (" · " + esc(note)) if note else ""))
        for label, value in rows:
            parts.append("<tr><th>%s</th><td>%s</td></tr>" % (esc(label), esc(value)))
        parts.append("</table>")
        parts.append(
            "<form method='post' action='/profile'>"
            "<input type='hidden' name='action' value='apply'>"
            "<input type='hidden' name='fields' value=\"%s\">"
            "<div class='actions'>"
            "<button class='btn primary' type='submit'>Применить к профилю</button>"
            "<span class='meta'>контакты, стоп-слова, стоп-лист компаний и «чего нет» "
            "останутся прежними; прежняя версия профиля уйдёт в бэкап</span>"
            "</div></form></div>" % esc(payload))
    parts.append("</main></body></html>")
    return "".join(parts)


def render_help():
    """Страница руководства пользователя (GET /help)."""
    return "".join([
        "<html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>%s — hh-assistant</title><style>%s</style></head><body>" % (guide.TITLE, CSS),
        "<header><b>hh-assistant</b><span>руководство пользователя</span>",
        "<span style='flex:1'></span><a class='btn' href='/'>← К вакансиям</a></header><main>",
        guide.render(),
        "</main></body></html>",
    ])


# Один прогон за раз: сбор/оценка/письма пишут в SQLite и ходят в LLM минутами.
RUN_LOCK = threading.Lock()
RUN_BUSY = ("уже выполняется другое действие (сбор, оценка или письма) — "
            "дождитесь его окончания и повторите")
RUN_TITLES = {"collect": "сбор вакансий", "score": "оценка", "letters": "генерация писем"}

# Состояние фонового прогона: страница отвечает сразу и показывает ход работы.
# stop — просьба остановиться из интерфейса: фоновый поток проверяет флаг между
# вакансиями/запросами и выходит, уже сохранённое остаётся в базе.
RUN = {"kind": "", "title": "", "done": 0, "started": "", "report": "", "error": "",
       "running": False, "stop": False}


def _run_tick(*_args):
    RUN["done"] += 1


def wait_run(timeout=60.0):
    """Ждёт завершения фонового прогона. True — дождались. Нужен тестам и проверкам."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not RUN["running"]:
            return True
        time.sleep(0.05)
    return False


def start_action(kind, area=None, vacancy_id=""):
    """Запускает пакетное действие в фоне и сразу возвращает отчёт для страницы.

    Синхронный прогон держал HTTP-соединение минутами: при обрыве сети браузер
    показывал пустую страницу. Теперь ответ уходит сразу, а ход работы виден
    в плашке на главной (страница обновляется сама).
    """
    if not RUN_LOCK.acquire(blocking=False):
        return RUN_BUSY
    RUN.update({"kind": kind, "title": RUN_TITLES.get(kind, kind), "done": 0,
                "started": storage.now()[:16].replace("T", " "), "report": "",
                "error": "", "running": True, "stop": False})

    def worker():
        conn = storage.connect()  # соединение SQLite живёт в своём потоке
        try:
            RUN["report"] = _run_action(kind, conn, area=area, vacancy_id=vacancy_id,
                                        on_item=_run_tick,
                                        should_stop=lambda: bool(RUN.get("stop")))
        except Exception as exc:  # ошибка прогона не должна ломать страницу
            RUN["error"] = "%s: %s" % (type(exc).__name__, exc)
            traceback.print_exc()
        finally:
            conn.close()
            RUN["running"] = False
            RUN_LOCK.release()

    threading.Thread(target=worker, daemon=True).start()
    return "запущено: %s — страница обновится сама" % RUN["title"]


RUN_VISUALS = {
    "collect": ("collect-visual", "идёт сбор вакансий…", "обработано карточек"),
    "score": ("score-visual", "идёт оценка вакансий…", "обработано вакансий"),
    "letters": ("letters-visual", "идёт генерация писем…", "готово писем"),
}


def _scene_collect():
    """Книга с перелистываемыми страницами и лупа, облетающая её по кругу."""
    return ("<div class='book'><span class='cover'></span>"
            "<span class='page left'></span><span class='page right'></span>"
            "<span class='flip'></span><span class='flip two'></span></div>"
            "<div class='lens-orbit'><span class='lens'></span></div>")


def _scene_score():
    """Стопка листов и чек-лист: на каждом пункте по очереди проступает галочка."""
    return ("<div class='sheets'><span class='sheet back'></span>"
            "<span class='sheet mid'></span><span class='checklist'>"
            "<span class='row r1'></span><span class='row r2'></span>"
            "<span class='row r3'></span></span></div>")


def _scene_letters():
    """Лист письма, который заполняется строками, и карандаш, идущий по строкам."""
    return ("<div class='writing'><span class='paper'></span>"
            "<span class='ink i1'></span><span class='ink i2'></span>"
            "<span class='ink i3'></span><span class='ink i4'></span>"
            "<span class='ink i5'></span><span class='pencil'></span></div>")


RUN_SCENES = {"collect": _scene_collect, "score": _scene_score,
              "letters": _scene_letters}


def run_indicator():
    """Картинка текущего прогона: у сбора, оценки и писем она своя.

    Сбор — книга с перелистываемыми страницами и лупа; оценка — стопка листов с
    проступающими галочками; письма — лист, заполняемый строками, и карандаш.
    Показывается только пока идёт соответствующий прогон, рядом счётчик. Только
    CSS и разметка — ни картинок, ни скриптов, поэтому анимация видна и в
    офлайне, и при отключённом JavaScript. Для `prefers-reduced-motion` движение
    отключается в CSS, подпись остаётся.
    """
    kind = RUN.get("kind") or ""
    if not RUN["running"] or kind not in RUN_VISUALS:
        return ""
    css, title, counter = RUN_VISUALS[kind]
    return ("<div class='run-visual %s' role='status' aria-live='polite' "
            "aria-label='%s'><div class='scene' aria-hidden='true'>%s</div>"
            "<div><b>%s</b><div class='hint'>%s: %d · страница обновится сама</div>"
            "</div></div>" % (css, esc(title), RUN_SCENES[kind](), esc(title),
                              esc(counter), RUN["done"]))


def stop_run():
    """Просит фоновый прогон остановиться. Возвращает текст для страницы.

    Флаг видит поток прогона: сбор прекращает скачивать карточки и запросы,
    оценка и письма — обрабатывать следующие вакансии. Уже сделанное остаётся.
    """
    if not RUN.get("running"):
        return "нечего останавливать: прогон уже завершён"
    if RUN.get("stop"):
        return "остановка уже запрошена — завершаю текущую карточку"
    RUN["stop"] = True
    return "останавливаю: %s завершит текущую карточку и остановится" % RUN["title"]


def run_progress():
    """Плашка о прогоне для страницы: идёт / остановка / ошибка / результат последнего."""
    if RUN["running"]:
        text = "идёт %s · обработано: %d · старт: %s" % (RUN["title"], RUN["done"],
                                                        RUN["started"])
        if RUN.get("stop"):
            text += " · останавливаю…"
        return text
    if RUN["error"]:
        return "ошибка (%s): %s" % (RUN["title"], RUN["error"])
    if RUN["report"]:
        return "последний прогон — %s" % RUN["report"]
    return ""


def run_action(kind, conn, limit=None, area=None, vacancy_id="", on_item=None):
    """Выполняет действие из веб-интерфейса: collect / score / letters. Возвращает отчёт.

    area — id региона hh для сбора (пусто — регионы из настроек, по умолчанию вся Россия).
    vacancy_id — действие только для одной вакансии (кнопки в карточке).

    Одновременно идёт только один прогон: повторные клики по «Оценить» раньше
    запускали второй поток, тот падал на блокировке SQLite, и браузер получал
    пустую страницу вместо ответа.
    """
    if not RUN_LOCK.acquire(blocking=False):
        return RUN_BUSY
    try:
        return _run_action(kind, conn, limit=limit, area=area, vacancy_id=vacancy_id,
                           on_item=on_item)
    finally:
        RUN_LOCK.release()


def _run_action(kind, conn, limit=None, area=None, vacancy_id="", on_item=None,
                should_stop=None):
    s = get_settings()
    tick = on_item or (lambda *args: None)

    if kind == "collect":
        lim = limit or s.collect_limit
        total_lim = int(getattr(s, "collect_total", 0) or 0)
        queries = _queries()
        if s.collect_queries > 0:
            queries = queries[:s.collect_queries]
        if not queries:
            return "нет поисковых запросов: заполните config/queries.txt"
        area_ids = [int(area)] if str(area or "").strip().isdigit() else None
        saved, found, source, notes = jobs.collect_queries(conn, queries, s, limit=lim,
                                                           area_ids=area_ids, on_item=tick,
                                                           total_limit=total_lim or None,
                                                           should_stop=should_stop)
        where = areas.name_of(area_ids[0]) if area_ids else "вся Россия"
        storage.log_event(conn, "collect", {"queries": queries, "saved": saved,
                                            "found": found, "source": source,
                                            "area": where, "limit": lim,
                                            "total": total_lim, "from": "web"})
        report = "собрано вакансий: %d из %d найденных (источник: %s, запросов: %d, " \
                 "лимит: %d на запрос, всего до %d, регион: %s)" % (
                     saved, found, source, len(queries), lim, total_lim, where)
        if notes:
            report += "; " + "; ".join(notes[:3])
        return report

    if kind == "score":
        if vacancy_id:
            res = jobs.score_one(conn, s, vacancy_id)
            if not res:
                return "вакансия не найдена: %s" % vacancy_id
            score, verdict = res
            storage.log_event(conn, "score_one", {"vacancy_id": vacancy_id, "score": score,
                                                  "from": "web"})
            return "вакансия %s: оценка %s из 5 (%s)" % (
                vacancy_id, scoring.school_grade(score), verdict)
        done, total = jobs.score_pending(conn, s, on_item=tick, should_stop=should_stop)
        storage.log_event(conn, "score", {"count": done, "model": s.llm_model or "rules",
                                          "from": "web"})
        report = "оценено новых: %d (всего оценено: %d)" % (done, total)
        if should_stop and should_stop():
            report += " · остановлено пользователем"
        elif not done:
            report += "; полная переоценка (после правки профиля) — python cli.py score --all"
        return report

    if kind == "letters":
        if vacancy_id:
            res = jobs.make_letter_for(conn, s, vacancy_id)
            if not res:
                return "вакансия не найдена: %s" % vacancy_id
            text, model, kept = res
            storage.log_event(conn, "letter_one", {"vacancy_id": vacancy_id, "model": model,
                                                   "chars": len(text), "kept_edit": kept,
                                                   "from": "web"})
            if kept:
                return "письмо не изменено: сохранена ваша правка"
            return "письмо готово: %d символов (модель: %s)" % (len(text), model or "offline")
        made = jobs.make_letters(conn, s, limit=50, on_item=tick, should_stop=should_stop)
        storage.log_event(conn, "letters", {"count": made, "model": s.llm_model or "offline",
                                            "from": "web"})
        report = "создано писем: %d" % made
        if should_stop and should_stop():
            report += " · остановлено пользователем"
        return report

    return "неизвестное действие: %s" % kind


def score_parts(raw):
    """JSON-причины оценки -> (список причин, «фокус» — что подчеркнуть в письме).

    Раньше карточка печатала сырой JSON-массив; теперь это список. Битые или
    старые данные не ломают страницу — показываются одной строкой.
    """
    if not raw:
        return [], ""
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return [str(raw)], ""
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        return [str(items)], ""
    reasons, focus = [], ""
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if text.lower().startswith("фокус"):
            focus = text.partition(":")[2].strip() or text
        else:
            reasons.append(text)
    return reasons, focus


def rules_dialog(row, show_rules=False):
    """Окно «Как считается оценка»: правила с весами и результат этой вакансии.

    Открывается кнопкой «Правила оценки» и автоматически после нажатия
    «Переоценить вакансию» (флаг show_rules → data-open='1').
    """
    from . import scoring

    rules = "".join(
        "<tr><td>%s</td><td class='points'>%s</td><td>%s</td></tr>" % (
            esc(title), esc(points), esc(note))
        for title, points, note in scoring.RULE_WEIGHTS)
    parts = []
    if row["rule_score"] is not None:
        parts.append("правила: <b>%s</b> из 5" % scoring.school_grade(row["rule_score"]))
    if row["llm_score"] is not None:
        parts.append("LLM: <b>%s</b> из 5" % scoring.school_grade(row["llm_score"]))
    shares = " · ".join(parts) or "составляющие не сохранены (оценка сделана раньше)"
    reasons, focus = score_parts(row["reasons"])
    items = "".join("<li>%s</li>" % esc(item) for item in reasons)
    items = ("<ul class='rules-list'>%s</ul>" % items) if items else "<div class='meta'>причин нет</div>"
    focus_block = ("<div class='meta'><b>Что подчеркнуть в письме:</b> %s</div>" % esc(focus)) \
        if focus else ""
    grade = scoring.school_grade(row["score"])
    return (
        "<dialog id='rules' data-open='%d'>"
        "<h3>Как считается оценка вакансии</h3>"
        "<div class='meta'>Оценка как в школе, от 1 до 5: 5 — полностью подходит, "
        "4 — подходит, 3 — условно, 2 — слабо, 1 — не подходит. Считается по правилам "
        "профиля и, если настроен ключ LLM, по оценке модели.</div>"
        "<table><tr><th>Правило</th><th>Вклад</th><th>Когда срабатывает</th></tr>%s</table>"
        "<div class='meta'>Результат этой вакансии: %s · оценка <b>%s</b> из 5 · "
        "вердикт <b>%s</b></div>"
        "%s%s"
        "<div class='actions'><form method='dialog'>"
        "<button class='btn primary' type='submit'>Понятно</button></form></div>"
        "</dialog>" % (
            1 if show_rules else 0, rules, shares,
            grade if grade else "—",
            esc(row["verdict"] or "нет"), items, focus_block))


def render_card(conn, vacancy_id, msg="", show_rules=False):
    row = storage.vacancy_with_letter(conn, vacancy_id)
    if not row:
        return None
    letter = row["letter"] or ""
    if row["letter_edited"]:
        note = "правка сохранена: %s" % (row["letter_edited_at"] or "")[:16].replace("T", " ")
    elif letter:
        note = "текст сгенерирован"
    else:
        note = "письма пока нет — нажмите «Сгенерировать письмо»"
    reasons, focus_text = score_parts(row["reasons"])
    if reasons:
        reasons_html = "<ul class='rules-list'>%s</ul>" % "".join(
            "<li>%s</li>" % esc(item) for item in reasons)
    else:
        reasons_html = "<div class='reasons'>причин нет — нажмите «Переоценить вакансию»</div>"
    from . import matching
    grade = scoring.school_grade(row["score"])
    grade_line = ("%d из 5" % grade) if grade else "нет оценки"
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
        + ("<meta http-equiv='refresh' content='5'>" if RUN["running"] else "") +
        "<title>%s — %s</title><style>%s</style></head><body>" % (
            esc(row["title"]), esc(row["company"] or "hh.ru"), CSS),
        "<header><b><a href='/'>← К списку</a></b><span>карточка вакансии</span></header><main>",
        ("<div class='card'>%s</div>" % esc(msg)) if msg else "",
        ("<div class='meta'>%s</div>" % esc(run_progress())) if run_progress() else "",
        run_indicator(),
        "<div class='card'><div class='meta'><b>%s</b>%s · %s · опыт: %s · зарплата: %s · "
        "<span class='score %s'>оценка %s</span> %s</div>"
        "<div class='meta'>Почему такая оценка:</div>%s%s"
        "<div class='actions'>"
        "<button class='btn' type='button' onclick=\"document.getElementById('rules').showModal()\">"
        "Правила оценки</button></div></div>" % (
            esc(row["title"]),
            (" <span class='card-company'>· %s</span>" % esc(row["company"]))
            if row["company"] else "",
            esc(row["work_format"] or "формат не указан"),
            esc(row["experience"] or "—"),
            fmt_salary(row), score_class(row["score"]),
            grade_line,
            verdict_badge(row["verdict"]), reasons_html,
            ("<div class='meta'><b>Что подчеркнуть в письме:</b> %s</div>" % esc(focus_text))
            if focus_text else ""),
        "<div class='card'><div class='reasons'><b>Сопоставление с профилем:</b> %s</div></div>" % esc(match_line),
        "<div class='card'><div class='meta'><b>Оценка</b> — пересчитать оценку этой вакансии "
        "(правила + LLM) и показать правила</div>"
        "<div class='actions'>"
        "<form method='post' action='/run/score/%s' style='display:inline'>"
        "<button class='btn' type='submit' title='пересчитать оценку этой вакансии "
        "(правила + LLM) и показать правила оценки'>Переоценить вакансию</button></form>"
        "</div></div>" % esc(row["id"]),
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
        "<button class='btn' onclick=\"navigator.clipboard.writeText(document.getElementById('letter').value);"
        "this.textContent='Скопировано'\">Скопировать письмо</button>",
        "<a class='btn primary' target='_blank' rel='noopener' href='%s'>Открыть вакансию на hh</a>" % esc(row["url"]),
        "<form method='post' action='/mark/%s' style='display:inline'>"
        "<button class='btn' type='submit'>Отметить отправленным</button></form>" % esc(row["id"]),
        "<form method='post' action='/skip/%s' style='display:inline'>"
        "<button class='btn' type='submit'>Пропустить</button></form>" % esc(row["id"]),
        "<a class='btn' href='/letter/%s'>Скачать .txt</a>" % esc(row["id"]),
        "</div>",
        "<form method='post' action='/letter/%s' id='letter-save'>"
        "<textarea id='letter' name='text'>%s</textarea></form>"
        "<div class='actions'>"
        "<form method='post' action='/run/letters/%s' style='display:inline'>"
        "<button class='btn primary' type='submit'>Сгенерировать письмо</button></form>"
        "<button class='btn primary' type='submit' form='letter-save'>Сохранить письмо</button>"
        "<span class='meta'>%s</span></div>" % (
            esc(row["id"]), esc(letter), esc(row["id"]), esc(note)),
        "<div class='card' style='margin-top:14px'><div class='meta'><b>Описание вакансии</b>"
        " · <a href='%s' target='_blank' rel='noopener'>полный текст на hh</a></div>"
        "<div class='desc'>%s</div></div>" % (esc(row["url"]), esc(row["description"])),
        rules_dialog(row, show_rules),
        "<script>var box=document.getElementById('rules');"
        "if(box&&box.getAttribute('data-open')==='1'){try{box.showModal();}catch(e){}}</script>",
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
        self._responded = True
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if "Cache-Control" not in (headers or {}):
            self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # браузер закрыл вкладку раньше, чем ушёл ответ

    def _fail(self, exc):
        """Ошибка обработчика: текст вместо пустой страницы + трейсбек в журнал."""
        traceback.print_exc()
        if getattr(self, "_responded", False):
            return  # ответ уже отправлен, заголовки повторно не пишем
        text = "ошибка: %s: %s" % (type(exc).__name__, exc)
        path = urllib.parse.urlparse(self.path).path
        parts = [part for part in path.split("/") if part]
        back = "/"
        if parts[:1] == ["v"] and len(parts) > 1:
            back = "/v/%s" % urllib.parse.quote(parts[1])
        elif parts[:1] == ["run"] and len(parts) > 2:
            back = "/v/%s" % urllib.parse.quote(parts[2])
        try:
            if self.command == "POST":
                self._redirect(back + "?msg=" + urllib.parse.quote(text))
            else:
                self._send("<html><head><meta charset='utf-8'>"
                           "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                           "<title>ошибка — hh-assistant</title><style>" + CSS + "</style>"
                           "</head><body><main><div class='card' style='margin-top:10vh'>"
                           "<b>Не получилось выполнить запрос</b>"
                           "<p style='color:#5d5470'>" + esc(text) + "</p>"
                           "<div class='actions'><a class='btn' href='/'>← К списку</a></div>"
                           "</div></main></body></html>", status=500)
        except OSError:
            pass

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
        self._responded = True
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def _form(self):
        """Поля POST-формы: urlencoded (карточка) или multipart (загрузка резюме).

        Тело читается ОДИН раз и запоминается: повторный `read()` на живом
        соединении ждал бы новых байт и вешал запрос (так зависала загрузка резюме).
        Файлы складываются в `self._files` как {поле: (имя, байты)}.
        """
        if getattr(self, "_form_read", False):
            return self._form_fields
        self._form_read = True
        self._form_fields = {}
        self._files = {}
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if not body:
            return self._form_fields
        ctype = self.headers.get("Content-Type") or ""
        if ctype.startswith("multipart/form-data"):
            fields, files = parse_multipart(body, ctype)
            self._files = files
            self._form_fields = fields
        else:
            self._form_fields = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
        return self._form_fields

    def _profile_post(self, conn):
        """POST /profile: parse (предпросмотр), parse_apply (обновить сразу),
        apply (записать предпросмотр), reread (из сохранённой копии), revert (откат).

        Возвращает ("html", тело) — страница отправляется сразу, или ("", отчёт) —
        отчёт уходит в redirect-сообщение."""
        action = (self._form().get("action") or [""])[0]
        if action in ("parse", "parse_apply"):
            text = (self._form().get("text") or [""])[0]
            uploaded = self._files.get("file")
            filename = uploaded[0] if uploaded else ""
            data = uploaded[1] if uploaded else b""
            if not text and not data:
                return ("", "ничего не передано: приложите файл или вставьте текст резюме")
            if data:
                resume.save_resume_copy(filename, data)  # копия для «Перечитать»
            if not text and data:
                text = resume.text_from_upload(filename, data)
            parsed, method, note = resume.parse_resume(text, get_settings())
            if action == "parse":
                return ("html", render_profile(fields=parsed, method=method, note=note))
            report, changes = self._profile_apply(parsed, note or method)
            return ("html", render_profile(msg=report, diff=changes))
        if action == "apply":
            payload = (self._form().get("fields") or [""])[0]
            if not payload:
                return ("", "нечего сохранять: раздел «Распознано» пуст")
            try:
                parsed = json.loads(payload)
            except ValueError:
                return ("", "ошибка разбора разобранных данных: попробуйте загрузить резюме ещё раз")
            report, _changes = self._profile_apply(parsed, "")
            return ("", report)
        if action == "reread":
            path, name, stamp = resume.saved_resume()
            if not path:
                return ("", "сохранённой копии резюме нет — сначала загрузите файл")
            with open(path, "rb") as fh:
                data = fh.read()
            text = resume.text_from_upload(name, data)
            parsed, method, note = resume.parse_resume(text, get_settings())
            report, changes = self._profile_apply(parsed, "%s от %s" % (name, stamp))
            return ("html", render_profile(msg=report, diff=changes))
        if action == "revert":
            try:
                _backup, when = resume.restore_backup()
            except ValueError as exc:
                return ("", str(exc))
            return ("", "профиль возвращён к версии от %s" % when)
        return ("", "неизвестное действие профиля")

    def _profile_apply(self, parsed, source=""):
        """Слияние с профилем и запись. Возвращает (отчёт, [(поле, было, стало)])."""
        current = dict(get_settings().profile)
        merged = resume.merge_profile(current, parsed)
        changes = resume.diff_profiles(current, merged)
        resume.save_profile(merged)
        tail = (" · " + source) if source else ""
        if not changes:
            return ("профиль без изменений: в резюме нет новых данных%s" % tail, [])
        return ("профиль обновлён%s · изменилось полей: %d (%s)"
                % (tail, len(changes), ", ".join(label for label, _was, _now in changes)),
                changes)

    def _deny(self, text, status=403):
        """Страница отказа без вызова браузерного окна Basic Auth."""
        page = ("<html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>hh-assistant: вход</title><style>" + CSS + "</style></head><body>"
                "<main><div class='card' style='margin-top:10vh;max-width:560px'>"
                "<h3 style='margin:0 0 8px'>Доступ закрыт</h3>"
                "<p style='margin:0;color:#5d5470'>" + esc(text) + "</p>"
                "</div></main></body></html>")
        data = page.encode("utf-8")
        self._responded = True
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _login_page(self, error="", status=200):
        page = ("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>Вход — hh-assistant</title><style>" + CSS + "</style></head><body>"
                "<main style='max-width:460px;margin-top:10vh'><div class='card'>"
                "<h1>Вход в hh-assistant</h1><p role='alert'>" + esc(error) + "</p>"
                "<form method='post' action='/login'>"
                "<p><label>Логин<br><input name='username' autocomplete='username' required "
                "style='width:100%;padding:10px'></label></p>"
                "<p><label>Пароль<br><input type='password' name='password' "
                "autocomplete='current-password' required style='width:100%;padding:10px'></label></p>"
                "<button class='btn primary' type='submit'>Войти</button></form>"
                "</div></main></body></html>")
        self._send(page, status=status, headers={"Cache-Control": "no-store"})

    def _session_cookie(self, token):
        return web_sessions.cookie_header(token, isinstance(self.connection, ssl.SSLSocket))

    def _login_post(self):
        settings = get_settings()
        length = int(self.headers.get("Content-Length") or 0)
        if length > 8192:
            self.close_connection = True
            self._login_page("Слишком большой запрос.", 413)
            return
        form = self._form()
        if not password_configured(settings) or not web_sessions.credentials_ok(
                settings, (form.get("username") or [""])[0],
                (form.get("password") or [""])[0]):
            self._login_page("Неверный логин или пароль.", 401)
            return
        web_sessions.revoke(self.headers.get("Cookie"))
        token = web_sessions.create(settings)
        self._send("", status=303, headers={"Location": "/", "Cache-Control": "no-store",
                                            "Set-Cookie": self._session_cookie(token)})

    def _authorized(self):
        """Все страницы и действия требуют действующей серверной сессии."""
        settings = get_settings()
        client = self.client_address[0] if self.client_address else ""
        if not auth_required(settings, client):
            return True
        if password_configured(settings) and web_sessions.valid(settings, self.headers.get("Cookie")):
            return True
        if password_configured(settings):
            self.close_connection = True
            self._redirect("/login")
        else:
            self._deny("Задайте WEB_AUTH_USER и WEB_AUTH_PASSWORD и перезапустите сервер.")
        return False

    def do_GET(self):
        try:
            self._get()
        except Exception as exc:  # без этого браузер получал пустую страницу
            self._fail(exc)

    def do_POST(self):
        try:
            self._post()
        except Exception as exc:
            self._fail(exc)

    def _get(self):
        if urllib.parse.urlparse(self.path).path == "/login":
            self._login_page()
            return
        if not self._authorized():
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/balance":
            # Баланс LLM для плашки в углу: только суммы и время, ключ не отдаём.
            self._send(json.dumps(balance_info(), ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
            return
        if path.startswith("/static/"):
            self._static(path[len("/static/"):])
            return
        conn = storage.connect()
        try:
            if path == "/":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                msg = qs.get("msg", [""])[0]
                region = qs.get("region", [""])[0]
                self._send(render_index(conn, limit=50, msg=msg, region=region,
                                        filters=qs, page=qs.get("page", ["1"])[0]))
            elif path.startswith("/v/"):
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                msg = qs.get("msg", [""])[0]
                show_rules = qs.get("rated", [""])[0] not in ("", "0")
                page = render_card(conn, path.split("/")[2], msg=msg, show_rules=show_rules)
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
                           "<div class='actions'><a class='btn' href='/'>← Назад</a></div>"
                           "</main></body></html>" % (made, esc(out_dir)))
            elif path == "/profile":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                self._send(render_profile(msg=qs.get("msg", [""])[0]))
            elif path == "/help":
                self._send(render_help())
            else:
                self._send("not found", status=404)
        finally:
            conn.close()

    def _post(self):
        path = urllib.parse.urlparse(self.path).path
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            self._deny("Запрос с другого сайта отклонён.")
            return
        if path == "/login":
            self._login_post()
            return
        if not self._authorized():
            return
        if path == "/logout":
            web_sessions.revoke(self.headers.get("Cookie"))
            self._send("", status=303, headers={"Location": "/login", "Cache-Control": "no-store",
                                                "Set-Cookie": self._session_cookie("")})
            return
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
            elif path == "/queries/clear":
                report = clear_queries()
                storage.log_event(conn, "queries_edit",
                                  {"queries": [], "cleared": True, "from": "web"})
                self._redirect("/?msg=" + urllib.parse.quote(report))
            elif path == "/run/stop":
                # Остановка прогона: только флаг, поток сам завершит текущую карточку.
                self._redirect("/?msg=" + urllib.parse.quote(stop_run()))
            elif path.startswith("/run/"):
                parts = [part for part in path.split("/") if part]  # /run/letters/<id>
                kind = parts[1] if len(parts) > 1 else ""
                target = parts[2] if len(parts) > 2 else ""
                form = self._form()
                # единый селект региона (id субъекта); поле area — совместимость со старыми формами
                value = (form.get("region") or form.get("area") or [""])[0]
                area_id, _region = _resolve_region(value)
                if kind == "collect" and not area_id:
                    # раньше здесь молча подставлялась «вся Россия»: при потере региона
                    # сбор уходил по всей стране. Лучше честно попросить выбрать регион.
                    self._redirect("/?msg=" + urllib.parse.quote(
                        "регион сбора не выбран или не распознан (%r) — выберите регион "
                        "в списке и нажмите «Собрать вакансии» ещё раз" % (value or "")))
                    return
                if target:
                    # одна вакансия: быстрый путь, ответ сразу с результатом
                    report = run_action(kind, conn, area=area_id, vacancy_id=target)
                else:
                    # пакетная работа (сбор, оценка всех, письма) — в фоне
                    report = start_action(kind, area=area_id)
                    if kind == "collect":
                        # регион виден сразу в сообщении — ошибку выбора заметно до прогона
                        name = ("вся Россия" if str(area_id) == "113"
                                else areas.name_of(int(area_id)) or area_id)
                        report += " · регион: %s" % name
                back = "/v/%s" % urllib.parse.quote(target) if target else "/"
                extra = "&rated=1" if target and kind == "score" else ""
                preserved = {key: values for key, values in form.items()
                             if key == "region" or key.startswith("f_")}
                suffix = ("&" + urllib.parse.urlencode(preserved, doseq=True)) if not target else ""
                self._redirect(back + "?msg=" + urllib.parse.quote(report) + extra + suffix)
            elif path == "/profile":
                kind, payload = self._profile_post(conn)
                if kind == "html":
                    self._send(payload)
                else:
                    self._redirect("/profile?msg=" + urllib.parse.quote(payload))
            elif path == "/reset":
                if (self._form().get("confirm") or [""])[0] != "1":
                    self._redirect("/?msg=" + urllib.parse.quote(
                        "очистка отменена: не подтверждено"))
                else:
                    deleted = storage.clear_db(conn)
                    storage.log_event(conn, "reset", {"deleted": deleted})
                    self._redirect("/?msg=" + urllib.parse.quote(
                        "база очищена: удалено вакансий %d (оценки, письма, отклики, события)" % deleted))
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

