# -*- coding: utf-8 -*-
"""Тесты входа в веб-интерфейс: HTTP Basic, локальные адреса, отказ без пароля.

Сеть не используется: проверяются чистые функции разбора заголовка и правила доступа,
плюс `Handler._authorized` на объекте без сокета (заголовки и адрес подставляются).
"""
import base64
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP  # noqa: E402,F401

from hhassist import web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402


def settings(**over):
    data = {"profile": dict(DEFAULT_PROFILE)}
    data.update(over)
    return Settings(**data)


def basic(user, password):
    raw = ("%s:%s" % (user, password)).encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


class ParseBasicAuthTest(unittest.TestCase):
    def test_ascii(self):
        self.assertEqual(web.parse_basic_auth(basic("hh", "secret")), ("hh", "secret"))

    def test_cyrillic_password(self):
        self.assertEqual(web.parse_basic_auth(basic("олег", "пароль42")),
                         ("олег", "пароль42"))

    def test_scheme_case_and_spaces(self):
        header = "  bAsIc   " + basic("hh", "x").split()[1]
        self.assertEqual(web.parse_basic_auth(header), ("hh", "x"))

    def test_colon_inside_password(self):
        self.assertEqual(web.parse_basic_auth(basic("hh", "pa:ss")), ("hh", "pa:ss"))

    def test_not_basic(self):
        self.assertIsNone(web.parse_basic_auth("Bearer abc"))
        self.assertIsNone(web.parse_basic_auth(""))

    def test_broken_base64(self):
        self.assertIsNone(web.parse_basic_auth("Basic 1234"))
        self.assertIsNone(web.parse_basic_auth("Basic !!!"))

    def test_no_colon(self):
        raw = base64.b64encode(b"hhonly").decode("ascii")
        self.assertIsNone(web.parse_basic_auth("Basic " + raw))


class LocalAddressTest(unittest.TestCase):
    def test_loopback(self):
        for addr in ("127.0.0.1", "127.0.0.5", "::1", "localhost", "::ffff:127.0.0.1"):
            self.assertTrue(web.is_local_address(addr), addr)

    def test_remote(self):
        for addr in ("8.8.8.8", "10.1.2.3", "192.168.0.10", "", "0.0.0.0", "не-адрес"):
            self.assertFalse(web.is_local_address(addr), addr)


class AuthRulesTest(unittest.TestCase):
    def test_password_requires_login_even_locally(self):
        s = settings(web_auth_user="hh", web_auth_password="secret")
        self.assertTrue(web.auth_required(s, "127.0.0.1"))
        self.assertTrue(web.auth_required(s, "8.8.8.8"))

    def test_without_password_only_local(self):
        s = settings()
        self.assertFalse(web.auth_required(s, "127.0.0.1"))
        self.assertTrue(web.auth_required(s, "8.8.8.8"))

    def test_blank_password_counts_as_missing(self):
        s = settings(web_auth_user="hh", web_auth_password="   ")
        self.assertFalse(web.password_configured(s))

    def test_auth_ok(self):
        s = settings(web_auth_user="hh", web_auth_password="секрет")
        self.assertTrue(web.auth_ok(s, basic("hh", "секрет")))
        self.assertFalse(web.auth_ok(s, basic("hh", "другой")))
        self.assertFalse(web.auth_ok(s, basic("admin", "секрет")))
        self.assertFalse(web.auth_ok(s, ""))
        self.assertFalse(web.auth_ok(s, None))

    def test_default_user_is_hh(self):
        s = settings(web_auth_password="secret")
        self.assertTrue(web.auth_ok(s, basic("hh", "secret")))
        self.assertFalse(web.auth_ok(s, basic("hh2", "secret")))


class HandlerAuthTest(unittest.TestCase):
    """`_authorized` без сокета: заголовки и адрес клиента подставляются вручную."""

    def make_handler(self, client, header=None):
        handler = web.Handler.__new__(web.Handler)
        handler.headers = {"Authorization": header} if header else {}
        handler.client_address = (client, 40000)
        handler._deny = mock.Mock()
        handler._redirect = mock.Mock()
        return handler

    def test_remote_without_password_is_denied_403(self):
        with mock.patch.object(web, "get_settings", return_value=settings()):
            handler = self.make_handler("8.8.8.8")
            self.assertFalse(handler._authorized())
        args = handler._deny.call_args[0]
        self.assertNotIn(401, args)
        self.assertIn("WEB_AUTH_PASSWORD", args[0])

    def test_remote_with_password_redirects_to_login(self):
        s = settings(web_auth_user="hh", web_auth_password="secret")
        with mock.patch.object(web, "get_settings", return_value=s):
            handler = self.make_handler("8.8.8.8")
            self.assertFalse(handler._authorized())
        handler._redirect.assert_called_once_with("/login")
        handler._deny.assert_not_called()

    def test_remote_with_valid_session_passes(self):
        s = settings(web_auth_user="hh", web_auth_password="secret")
        with mock.patch.object(web, "get_settings", return_value=s):
            handler = self.make_handler("8.8.8.8")
            token = web.web_sessions.create(s)
            handler.headers["Cookie"] = web.web_sessions.cookie_header(token)
            self.assertTrue(handler._authorized())
        handler._deny.assert_not_called()
        handler._redirect.assert_not_called()

    def test_local_without_password_passes(self):
        with mock.patch.object(web, "get_settings", return_value=settings()):
            self.assertTrue(self.make_handler("127.0.0.1")._authorized())

    def test_deny_never_sends_www_authenticate(self):
        handler = web.Handler.__new__(web.Handler)
        handler.wfile = io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler._deny("нужен пароль", 401)
        headers = [c[0][0] for c in handler.send_header.call_args_list]
        self.assertNotIn("WWW-Authenticate", headers)
        handler.send_response.assert_called_once_with(401)

        handler.send_header.reset_mock()
        handler._deny("нет пароля в .env")
        headers = [c[0][0] for c in handler.send_header.call_args_list]
        self.assertNotIn("WWW-Authenticate", headers)
        self.assertEqual(handler.send_response.call_args[0][0], 403)


if __name__ == "__main__":
    unittest.main()
