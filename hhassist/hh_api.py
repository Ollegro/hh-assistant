# -*- coding: utf-8 -*-
"""Клиент API hh.ru: токен приложения (client_credentials) и запросы к api.hh.ru.

Схема авторизации hh — OAuth2 без scope (docs: api.hh.ru/openapi, docs/hhru/api):
  * POST https://api.hh.ru/token, тело application/x-www-form-urlencoded;
  * grant_type=client_credentials (client_id + client_secret) -> токен ПРИЛОЖЕНИЯ,
    срок не ограничен, refresh_token не выдаётся; повторный запрос отзывает старый;
  * токен можно не запрашивать, а взять в кабинете dev.hh.ru/admin (HH_APP_TOKEN);
  * дальше — заголовок Authorization: Bearer <токен> и обязательный User-Agent.

Токен пользователя (authorization_code/refresh_token) здесь не используется:
поддержка API для соискателей прекращена 15.12.2025, отклики через API недоступны.

Секреты (client_secret, токен) никогда не попадают в тексты исключений и в события БД.
"""
import gzip
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import get_settings

API_BASE = "https://api.hh.ru"
TOKEN_URL = "https://api.hh.ru/token"
MAX_RESULTS = 2000      # глубина выдачи hh: page * per_page <= 2000 (спецификация)
MAX_PER_PAGE = 100      # максимум per_page у /vacancies

_TOKEN = None           # кэш токена приложения в памяти процесса
_ETAGS = {}             # url -> ETag (для 304 Not Modified)
_CACHE = {}             # url -> последний разобранный ответ


class HHApiError(RuntimeError):
    """Ошибка API hh.ru: HTTP-код, error/value, request_id (для api@hh.ru)."""

    def __init__(self, status, message="", value="", request_id="", body=""):
        self.status = status
        self.value = value or ""
        self.request_id = request_id or ""
        self.body = body or ""
        parts = []
        if status:
            parts.append("HTTP %s" % status)
        parts.append(message or "ошибка API hh.ru")
        if self.value and self.value not in (message or ""):
            parts.append("[%s]" % self.value)
        if self.request_id:
            parts.append("request_id=%s" % self.request_id)
        super().__init__(" ".join(parts))


def _open(request, timeout):
    """Обёртка над urlopen: в тестах подменяется заглушкой."""
    return urllib.request.urlopen(request, timeout=timeout)


def _decode(resp, raw):
    """Распаковывает gzip-ответ (hh отдаёт его при Accept-Encoding: gzip)."""
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
        return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return raw


def _json(data):
    """bytes/str -> dict; нераспознанный ответ превращается в {}."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "ignore")
    if not data:
        return {}
    try:
        parsed = json.loads(data)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error(status, payload, body=""):
    errors = payload.get("errors") or []
    first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    value = str(first.get("value") or first.get("type") or "")
    message = str(
        payload.get("error_description")
        or payload.get("description")
        or payload.get("error")
        or value
        or ""
    )
    return HHApiError(
        status,
        message or "ошибка API hh.ru",
        value=value,
        request_id=str(payload.get("request_id") or ""),
        body=body,
    )


def _error_payload(exc):
    """(текст, разобранный JSON) из тела HTTPError; сам секрет не читаем."""
    try:
        raw = exc.read()
    except Exception:
        return "", {}
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
    return text, _json(text)


def _retry_after(exc, default):
    """Пауза из заголовка Retry-After (429/503), иначе default."""
    headers = getattr(exc, "headers", None)
    value = headers.get("Retry-After") if hasattr(headers, "get") else None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return default


def clear_cache():
    """Сбрасывает ETag/ответы (для тестов и ручной диагностики)."""
    _ETAGS.clear()
    _CACHE.clear()


def reset_token():
    """Забывает токен в памяти: следующий вызов app_token() возьмёт его из .env или /token."""
    global _TOKEN
    _TOKEN = None


def configured(settings=None):
    """True, если в .env есть токен приложения или пара client_id/secret."""
    s = settings or get_settings()
    return bool(s.hh_app_token or (s.hh_client_id and s.hh_client_secret))


def has_token():
    """Можно ли идти в API (токен в памяти или в настройках)."""
    return bool(_TOKEN) or configured()


def app_token(force=False):
    """Токен приложения: HH_APP_TOKEN из .env либо POST /token (client_credentials)."""
    global _TOKEN
    s = get_settings()
    if _TOKEN and not force:
        return _TOKEN
    if s.hh_app_token and not force:
        return s.hh_app_token
    if not s.hh_client_id or not s.hh_client_secret:
        if force and s.hh_app_token:
            raise HHApiError(0, "перевыпуск возможен только через HH_CLIENT_ID + HH_CLIENT_SECRET "
                                "(токен из кабинета dev.hh.ru генерируется вручную)")
        raise HHApiError(0, "токен hh не настроен: заполните HH_APP_TOKEN "
                            "или HH_CLIENT_ID + HH_CLIENT_SECRET в .env")
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": s.hh_client_id,
        "client_secret": s.hh_client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "User-Agent": s.user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with _open(req, s.hh_api_timeout) as resp:
            payload = _json(_decode(resp, resp.read()))
    except urllib.error.HTTPError as exc:
        text, data = _error_payload(exc)
        raise _error(exc.code, data, text)
    token = payload.get("access_token")
    if not token:
        raise HHApiError(0, "в ответе /token нет access_token")
    _TOKEN = token
    return token


def get(path, params=None, use_cache=True, retries=None, timeout=None):
    """GET к API с Bearer-токеном, gzip, ETag (304) и повторами на 429/5xx."""
    s = get_settings()
    url = path if str(path).startswith("http") else API_BASE + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            # doseq=True: список area превращается в area=1&area=2 (несколько регионов)
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)
    token = app_token()
    attempts = max(1, retries if retries is not None else s.hh_api_retries)
    pause = 1
    last = HHApiError(0, "запрос не выполнен: %s" % url)
    for attempt in range(attempts):
        headers = {
            "User-Agent": s.user_agent,
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        if use_cache and url in _ETAGS:
            headers["If-None-Match"] = _ETAGS[url]
        req = urllib.request.Request(url, headers=headers)
        try:
            with _open(req, timeout or s.hh_api_timeout) as resp:
                data = _json(_decode(resp, resp.read()))
                etag = resp.headers.get("ETag")
                if etag:
                    _ETAGS[url] = etag
                    _CACHE[url] = data
                return data
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and url in _CACHE:
                return _CACHE[url]
            text, payload = _error_payload(exc)
            err = _error(exc.code, payload, text)
            if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                time.sleep(min(_retry_after(exc, pause), 60))
                pause *= 2
                last = err
                continue
            raise err
        except HHApiError:
            raise
        except Exception as exc:  # таймауты, обрыв сети, DNS
            last = HHApiError(0, "%s: %s" % (type(exc).__name__, url))
            if attempt + 1 < attempts:
                time.sleep(pause)
                pause *= 2
                continue
            raise last
    raise last


def me():
    """GET /me — проверка токена (для токена приложения вернёт объект приложения)."""
    return get("/me", use_cache=False)


def search_vacancies(text=None, area=None, pages=1, per_page=50, pause=None, remote=False,
                     **filters):
    """Поиск вакансий: GET /vacancies. Возвращает items (без полного описания).

    area — id региона или список id (несколько регионов). remote=True — только удалёнка.
    """
    s = get_settings()
    per_page = int(per_page)
    pages = int(pages)
    if not 1 <= per_page <= MAX_PER_PAGE:
        raise ValueError("per_page должен быть в диапазоне 1..%d (ограничение hh)" % MAX_PER_PAGE)
    if pages < 1:
        raise ValueError("pages должен быть >= 1")
    if pages * per_page > MAX_RESULTS:
        raise ValueError("глубина выдачи hh.ru — не больше %d результатов (pages * per_page)"
                         % MAX_RESULTS)
    delay = s.delay_seconds if pause is None else pause
    items = []
    for page in range(pages):
        params = {"text": text, "area": area, "page": page, "per_page": per_page}
        if remote:
            params["schedule"] = "remote"
        params.update(filters)
        batch = get("/vacancies", params, use_cache=False).get("items") or []
        items.extend(batch)
        if len(batch) < per_page:
            break
        if delay and page + 1 < pages:
            time.sleep(delay)
    return items


def get_vacancy(vacancy_id):
    """GET /vacancies/{id} — полная карточка (description, key_skills)."""
    return get("/vacancies/%s" % urllib.parse.quote(str(vacancy_id)), use_cache=True)


def probe():
    """Диагностика: наличие токена, GET /me, один поисковый запрос. Секреты не раскрываются."""
    s = get_settings()
    if s.hh_app_token:
        source = "HH_APP_TOKEN"
    elif s.hh_client_id and s.hh_client_secret:
        source = "client_credentials"
    else:
        source = "нет"
    report = {"token_source": source, "token_length": 0, "me": None, "found": None, "error": None}
    try:
        report["token_length"] = len(app_token())
        info = me()
        report["me"] = {
            "id": info.get("id"),
            "name": info.get("name") or info.get("first_name") or "",
        }
        report["found"] = get("/vacancies", {"text": "python", "per_page": 1},
                              use_cache=False).get("found")
    except HHApiError as exc:
        report["error"] = {
            "status": exc.status,
            "value": exc.value,
            "request_id": exc.request_id,
            "message": str(exc),
        }
    return report
