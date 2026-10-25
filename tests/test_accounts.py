# -*- coding: utf-8 -*-
"""Аккаунты, пароли, подписки и сессии (hhassist.accounts)."""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP  # noqa: E402,F401

from hhassist import accounts  # noqa: E402


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(
            accounts, "DB_PATH", os.path.join(self.tmp.name, "control.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = accounts.connect()
        self.addCleanup(self.conn.close)

    def add_user(self, email="user@example.com", **kwargs):
        return accounts.create_user(email, conn=self.conn, **kwargs)

    def test_create_user_sets_trial_and_must_change(self):
        user_id, password = self.add_user()
        user = accounts.get_user(user_id, conn=self.conn)
        self.assertEqual(user["email"], "user@example.com")
        self.assertEqual(user["status"], "trial")
        self.assertEqual(user["plan_code"], "trial")
        self.assertEqual(user["must_change_password"], 1)
        self.assertEqual(user["paid_until"], (date.today() + timedelta(days=1)).isoformat())
        self.assertTrue(password)

    def test_password_is_hashed_and_checked(self):
        user_id, password = self.add_user("A@Example.com", password="s3cret-pass")
        user = accounts.get_user(user_id, conn=self.conn)
        self.assertNotIn("s3cret-pass", user["password_hash"])
        self.assertTrue(accounts.check_password(user, "s3cret-pass"))
        self.assertFalse(accounts.check_password(user, "wrong"))
        self.assertFalse(accounts.check_password(None, "s3cret-pass"))

    def test_duplicate_email_is_rejected(self):
        self.add_user("dup@example.com")
        with self.assertRaises(Exception):
            self.add_user("DUP@example.com")

    def test_extend_moves_paid_until_and_records_subscription(self):
        user_id, _ = self.add_user()
        before = accounts.get_user(user_id, conn=self.conn)["paid_until"]
        after = accounts.extend(user_id, 30, plan_code="basic", conn=self.conn)
        user = accounts.get_user(user_id, conn=self.conn)
        self.assertEqual(user["status"], "active")
        self.assertEqual(user["plan_code"], "basic")
        self.assertGreater(after, before)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertTrue(accounts.is_active(user))

    def test_block_disables_access(self):
        user_id, _ = self.add_user(days=30)
        accounts.set_status(user_id, "blocked", conn=self.conn)
        user = accounts.get_user(user_id, conn=self.conn)
        self.assertFalse(accounts.is_active(user))
        accounts.set_status(user_id, "active", conn=self.conn)
        self.assertTrue(accounts.is_active(accounts.get_user(user_id, conn=self.conn)))

    def test_set_password_changes_hash(self):
        user_id, _ = self.add_user(password="old-pass")
        new_password = accounts.set_password(user_id, "new-pass", conn=self.conn)
        self.assertEqual(new_password, "new-pass")
        user = accounts.get_user(user_id, conn=self.conn)
        self.assertTrue(accounts.check_password(user, "new-pass"))
        self.assertFalse(accounts.check_password(user, "old-pass"))

    def test_plan_limits_are_seeded(self):
        limits = accounts.plan_limits("trial", conn=self.conn)
        self.assertIn("vacancies", limits)
        self.assertEqual(accounts.plan_limits("nope", conn=self.conn), {})


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(
            accounts, "DB_PATH", os.path.join(self.tmp.name, "control.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = accounts.connect()
        self.addCleanup(self.conn.close)
        self.user_id, _ = accounts.create_user("s@example.com", conn=self.conn)

    def test_session_lifecycle(self):
        token = accounts.create_session(self.user_id, conn=self.conn)
        header = "hh_session=%s; Path=/" % token
        user = accounts.session_user(header, conn=self.conn)
        self.assertEqual(user["id"], self.user_id)
        accounts.revoke_session(header, conn=self.conn)
        self.assertIsNone(accounts.session_user(header, conn=self.conn))

    def test_expired_session_is_removed(self):
        token = accounts.create_session(self.user_id, ttl=-1, conn=self.conn)
        self.assertIsNone(accounts.session_user("hh_session=%s" % token, conn=self.conn))
        left = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        self.assertEqual(left, 0)

    def test_cleanup_removes_only_expired(self):
        good = accounts.create_session(self.user_id, conn=self.conn)
        accounts.create_session(self.user_id, ttl=-1, conn=self.conn)
        self.assertEqual(accounts.cleanup_sessions(conn=self.conn), 1)
        self.assertIsNotNone(accounts.session_user("hh_session=%s" % good, conn=self.conn))


if __name__ == "__main__":
    unittest.main()
