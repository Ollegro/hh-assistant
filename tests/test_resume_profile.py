# -*- coding: utf-8 -*-
"""Резюме → профиль: PDF, «загрузил и профиль обновился», откат, «перечитать».

Сеть и настоящие файлы не используются: путь `profile.json` и папка копий резюме
подменяются временными, настройки — офлайн-профиль без ключа LLM.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP  # noqa: E402,F401

from hhassist import resume, web  # noqa: E402
from hhassist.config import DEFAULT_PROFILE, Settings  # noqa: E402

RESUME_TEXT = """Гуцев Олег Вячеславович
Промпт-инженер / AI-инженер (LLM-разработчик)
Ожидаемая зарплата: 180 000 – 250 000 руб
Формат работы: удалённо, гибрид

О себе
Разработка RAG-ассистентов на Python, LLM API, FastAPI, PostgreSQL.

Ключевые навыки
Python, LLM, RAG, FastAPI, PostgreSQL, Docker

Образование
РЭУ им. Плеханова, 2007
"""


def offline_settings(**over):
    """Настройки без ключа LLM: разбор идёт правилами, в сеть не ходим."""
    data = {"profile": dict(DEFAULT_PROFILE), "daily_limit": 200}
    data.update(over)
    return Settings(**data)


PAGE_SETTINGS = offline_settings()


class ProfileBase(unittest.TestCase):
    """Временные profile.json и data/resume: настоящие файлы не трогаем."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile_path = os.path.join(self.tmp.name, "profile.json")
        for patcher in (
            mock.patch.object(resume, "PROFILE_PATH", self.profile_path),
            mock.patch.object(resume, "RESUME_DIR", os.path.join(self.tmp.name, "resume")),
            mock.patch.object(web, "get_settings", lambda: PAGE_SETTINGS),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def read_profile(self):
        with open(self.profile_path, encoding="utf-8") as fh:
            return json.load(fh)


class PdfReadingTest(unittest.TestCase):
    """PDF: читаем библиотекой, без библиотеки — понятное сообщение вместо мусора."""

    def test_without_library_explains_what_to_do(self):
        with mock.patch.dict(sys.modules, {"pypdf": None, "pdfminer": None,
                                           "pdfminer.high_level": None}):
            with self.assertRaises(ValueError) as ctx:
                resume.pdf_to_text(b"%PDF-1.4\n%test\n")
        text = str(ctx.exception)
        self.assertIn("pypdf", text)
        self.assertIn("DOCX", text)

    def test_broken_pdf_is_reported_not_silent(self):
        if not resume.pdf_supported():
            self.skipTest("библиотек для чтения PDF нет")
        with self.assertRaises(ValueError):
            resume.pdf_to_text(b"%PDF-1.4\n-\xff\xfe broken\n")

    def test_upload_routes_pdf_to_pdf_reader(self):
        with mock.patch.object(resume, "pdf_to_text", lambda data: "Текст резюме. " * 10):
            text = resume.text_from_upload("resume.pdf", b"%PDF-1.4 data")
        self.assertIn("Текст резюме", text)

    def test_txt_upload_still_works(self):
        text = resume.text_from_upload("resume.txt", RESUME_TEXT.encode("utf-8"))
        self.assertIn("Промпт-инженер", text)


class DiffTest(unittest.TestCase):
    """«Было → стало»: только изменившиеся поля и в читаемом виде."""

    def test_identical_profiles_give_no_rows(self):
        before = {"headline": "AI-инженер", "salary_from": 180000, "salary_to": 250000}
        self.assertEqual(resume.diff_profiles(before, dict(before)), [])

    def test_reports_only_changed_fields(self):
        before = {"headline": "AI-инженер / промпт-инженер"}
        after = {"headline": "Промпт-инженер / AI-инженер"}
        rows = resume.diff_profiles(before, after)
        self.assertEqual([label for label, _was, _now in rows], ["Профессия"])

    def test_salary_and_stack_are_human_readable(self):
        before = {"salary_from": 180000, "salary_to": 250000}
        after = {"salary_from": 250000, "salary_to": 250000,
                 "stack": {"must": ["python"], "nice": ["docker"]}}
        rows = {label: (was, now) for label, was, now in resume.diff_profiles(before, after)}
        self.assertEqual(rows["Вилка"], ("180 000–250 000 ₽", "250 000 ₽"))
        self.assertEqual(rows["Стек"][1], "python · + docker")

    def test_work_history_kept_readable(self):
        before = {}
        after = {"work_history": [{"company": "фриланс", "role": "AI-разработчик",
                                   "period": "2024 — н.в."}]}
        rows = resume.diff_profiles(before, after)
        self.assertEqual(rows[0], ("Опыт работы", "—",
                                   "фриланс — AI-разработчик (2024 — н.в.)"))



class ResumeCopyTest(ProfileBase):
    """Копия загруженного резюме — источник для кнопки «Перечитать»."""

    def test_saves_last_copy_and_reports_it(self):
        path = resume.save_resume_copy("Резюме.pdf", b"%PDF-1.4 data")
        self.assertTrue(os.path.isfile(path))
        _path, name, stamp = resume.saved_resume()
        self.assertEqual(name, "last.pdf")
        self.assertRegex(stamp, r"\d\d\.\d\d\.\d{4} \d\d:\d\d")

    def test_new_extension_replaces_old_copy(self):
        resume.save_resume_copy("Резюме.pdf", b"%PDF-1.4 data")
        resume.save_resume_copy("Резюме.txt", RESUME_TEXT.encode("utf-8"))
        self.assertEqual(resume.saved_resume()[1], "last.txt")
        self.assertEqual(len(os.listdir(resume.RESUME_DIR)), 1)

    def test_no_copy_reported_as_none(self):
        self.assertEqual(resume.saved_resume(), (None, "", ""))


class BackupTest(ProfileBase):
    """Откат профиля к прежней версии."""

    def test_restore_returns_previous_version(self):
        resume.save_profile({"headline": "версия 1"})
        resume.save_profile({"headline": "версия 2"})
        _path, when = resume.restore_backup()
        self.assertEqual(self.read_profile()["headline"], "версия 1")
        self.assertRegex(when, r"\d\d\.\d\d\.\d{4} \d\d:\d\d")

    def test_restore_without_backups_explains(self):
        with self.assertRaises(ValueError) as ctx:
            resume.restore_backup()
        self.assertIn("бэкап", str(ctx.exception).lower())


class ProfilePageTest(ProfileBase):
    """Страница профиля: одна кнопка обновления, «Перечитать», «Отменить», diff."""

    def test_upload_card_updates_in_one_click(self):
        page = web.render_profile()
        self.assertIn("value='parse_apply'", page)
        self.assertIn("Загрузить и обновить профиль", page)
        self.assertIn("value='parse'", page)

    def test_pdf_accept_matches_library_availability(self):
        page = web.render_profile()
        if resume.pdf_supported():
            self.assertIn(".pdf,.docx,.txt", page)
        else:
            self.assertIn(".docx,.txt", page)
            self.assertIn("pypdf", page)

    def test_tools_appear_with_saved_resume_and_backup(self):
        resume.save_resume_copy("Резюме.pdf", b"%PDF-1.4 data")
        resume.save_profile({"headline": "версия 1"})
        resume.save_profile({"headline": "версия 2"})
        page = web.render_profile()
        self.assertIn("value='reread'", page)
        self.assertIn("value='revert'", page)
        self.assertIn("last.pdf", page)

    def test_diff_table_is_rendered(self):
        page = web.render_profile(msg="профиль обновлён",
                                  diff=[("Профессия", "было так", "стало так")])
        self.assertIn("Что изменилось в профиле", page)
        self.assertIn("было так", page)
        self.assertIn("стало так", page)


class ProfileHttpTest(ProfileBase):
    """Загрузил резюме — профиль обновился: проверка настоящим POST-запросом."""

    def setUp(self):
        super().setUp()
        quiet = mock.patch.object(web.Handler, "log_message", lambda *a, **k: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def post(self, fields, filename="", filedata=b""):
        """POST /profile как из формы: multipart с файлом и полями."""
        boundary = "----hhassistanttest"
        parts = []
        for name, value in fields.items():
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                          % (boundary, name, value)).encode("utf-8"))
        if filename:
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                          "filename=\"%s\"\r\nContent-Type: text/plain\r\n\r\n"
                          % (boundary, filename)).encode("utf-8"))
            parts.append(filedata + b"\r\n")
        parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
        request = urllib.request.Request(
            self.base + "/profile", data=b"".join(parts), method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8")

    def test_upload_updates_profile_immediately(self):
        page = self.post({"action": "parse_apply"}, "resume.txt", RESUME_TEXT.encode("utf-8"))
        self.assertIn("профиль обновлён", page)
        self.assertIn("Что изменилось в профиле", page)
        saved = self.read_profile()
        self.assertIn("инженер", (saved.get("headline") or "").lower())
        self.assertEqual(resume.saved_resume()[1], "last.txt")

    def test_preview_does_not_touch_profile(self):
        page = self.post({"action": "parse"}, "resume.txt", RESUME_TEXT.encode("utf-8"))
        self.assertIn("Распознано", page)
        self.assertFalse(os.path.isfile(self.profile_path))

    def test_reread_uses_saved_copy(self):
        resume.save_resume_copy("resume.txt", RESUME_TEXT.encode("utf-8"))
        page = self.post({"action": "reread"})
        self.assertIn("профиль обновлён", page)
        self.assertIn("last.txt", page)

    def test_revert_returns_previous_profile(self):
        resume.save_profile({"headline": "прежняя версия"})
        self.post({"action": "parse_apply"}, "resume.txt", RESUME_TEXT.encode("utf-8"))
        page = self.post({"action": "revert"})
        self.assertIn("профиль возвращён", page)
        self.assertEqual(self.read_profile()["headline"], "прежняя версия")

    def test_empty_upload_explains(self):
        page = self.post({"action": "parse_apply"})
        self.assertIn("ничего не передано", page)


if __name__ == "__main__":
    unittest.main()
