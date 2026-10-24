"""Серверные сессии веб-входа. Пароли и cookie не записываются на диск."""
import hashlib
import hmac
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError

COOKIE = "hh_session"
TTL = 12 * 60 * 60
_LOCK = threading.Lock()
_SESSIONS = {}


def credentials_ok(settings, user, password):
    expected = (settings.web_auth_user or "").strip() or "hh"
    return (hmac.compare_digest(user.encode(), expected.encode()) &
            hmac.compare_digest(password.encode(), (settings.web_auth_password or "").encode()))


def _fingerprint(settings):
    return hashlib.sha256(((settings.web_auth_user or "").strip() or "hh").encode() +
                          b"\0" + (settings.web_auth_password or "").encode()).digest()


def token_from_cookie(header):
    try:
        cookie = SimpleCookie()
        cookie.load(header or "")
        return cookie[COOKIE].value if COOKIE in cookie else ""
    except CookieError:
        return ""


def create(settings):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        for key, (expires, _) in list(_SESSIONS.items()):
            if expires <= now:
                del _SESSIONS[key]
        # Ограничиваем память при большом числе повторных входов.
        if len(_SESSIONS) >= 1000:
            del _SESSIONS[next(iter(_SESSIONS))]
        _SESSIONS[hashlib.sha256(token.encode()).digest()] = (now + TTL, _fingerprint(settings))
    return token


def valid(settings, header):
    token = token_from_cookie(header)
    key = hashlib.sha256(token.encode()).digest()
    with _LOCK:
        session = _SESSIONS.get(key)
        if session and session[0] > time.time() and hmac.compare_digest(session[1], _fingerprint(settings)):
            return True
        _SESSIONS.pop(key, None)
    return False


def revoke(header):
    key = hashlib.sha256(token_from_cookie(header).encode()).digest()
    with _LOCK:
        _SESSIONS.pop(key, None)


def cookie_header(token, secure=False):
    return ("%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d%s" %
            (COOKIE, token, TTL if token else 0, "; Secure" if secure else ""))
