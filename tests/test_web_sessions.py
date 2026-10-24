"""HTTP-проверки формы входа и отзыва серверных сессий."""
import http.client
import os
import secrets
import sys
import threading
import unittest
import urllib.parse
from unittest import mock
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP
from hhassist import web, web_sessions
from hhassist.config import Settings


class LoginTest(unittest.TestCase):
    def setUp(self):
        self.password = secrets.token_urlsafe(24)
        self.settings = Settings(web_auth_password=self.password)
        patch = mock.patch.object(web, "get_settings", return_value=self.settings)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(web.Handler, "log_message")
        patch.start()
        self.addCleanup(patch.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def request(self, method, path, fields=None, cookie="", headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            hdr = {"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"}
            hdr.update(headers or {})
            conn.request(method, path, urllib.parse.urlencode(fields or {}), hdr)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            conn.close()

    def test_login_logout_and_protected_actions(self):
        status, headers, _ = self.request("GET", "/")
        self.assertEqual((status, headers["Location"]), (303, "/login"))
        self.assertNotIn("WWW-Authenticate", headers)
        status, headers, body = self.request("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn("autocomplete='current-password'", body)
        status, headers, _ = self.request("POST", "/login", {"username": "hh", "password": "wrong"})
        self.assertEqual(status, 401)
        self.assertNotIn("WWW-Authenticate", headers)
        self.assertNotIn("Set-Cookie", headers)
        status, headers, _ = self.request("POST", "/login", {"username": "hh", "password": self.password})
        self.assertEqual(status, 303)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertTrue(web_sessions.valid(self.settings, cookie))
        status, _, _ = self.request("GET", "/static/missing.jpg", cookie=cookie)
        self.assertEqual(status, 404)
        with mock.patch.object(web.storage, "connect") as connect:
            status, _, _ = self.request("POST", "/reset")
            self.assertEqual(status, 303)
            connect.assert_not_called()
        status, headers, _ = self.request("POST", "/logout", cookie=cookie)
        self.assertEqual(status, 303)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertFalse(web_sessions.valid(self.settings, cookie))
        self.assertEqual(self.request("GET", "/", cookie=cookie)[0], 303)

    def test_expired_forged_and_changed_credentials(self):
        token = web_sessions.create(self.settings)
        cookie = web_sessions.cookie_header(token, secure=True)
        self.assertIn("; Secure", cookie)
        self.assertFalse(web_sessions.valid(self.settings, "hh_session=forged"))
        with mock.patch.object(web_sessions.time, "time", return_value=10**12):
            self.assertFalse(web_sessions.valid(self.settings, cookie))
        token = web_sessions.create(self.settings)
        cookie = web_sessions.cookie_header(token)
        self.assertFalse(web_sessions.valid(Settings(web_auth_password=secrets.token_urlsafe()), cookie))

    def test_cross_site_login_rejected(self):
        self.assertEqual(self.request("POST", "/login", headers={"Origin": "https://other.example"})[0], 403)


if __name__ == "__main__":
    unittest.main()
