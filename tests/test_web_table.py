# -*- coding: utf-8 -*-
"""Тесты таблицы списка: ID первой колонкой, школьная оценка, колонка «Письмо».

Проверяется то, о чём просил заказчик: id вакансии с hh.ru виден слева, оценка
показывается как в школе (1–5) с баллом в подсказке, справа — зелёный кружок,
если письмо уже есть (сгенерировано или написано вручную).
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import APP, sample_vacancy  # noqa: E402,F401
from test_web_layout import Base  # noqa: E402

from hhassist import scoring, storage, web  # noqa: E402


class SchoolGradeTest(unittest.TestCase):
    def test_scale(self):
        cases = {None: None, 0: 1, 19: 1, 20: 2, 39: 2, 40: 3, 59: 3,
                 60: 4, 79: 4, 80: 5, 100: 5}
        for score, grade in cases.items():
            self.assertEqual(scoring.school_grade(score), grade, "балл %r" % score)

    def test_colors_follow_grades(self):
        self.assertEqual(web.score_class(85), "s-high")   # 5
        self.assertEqual(web.score_class(65), "s-high")   # 4
        self.assertEqual(web.score_class(45), "s-mid")    # 3
        self.assertEqual(web.score_class(25), "s-low")    # 2
        self.assertEqual(web.score_class(5), "s-low")     # 1
        self.assertEqual(web.score_class(None), "s-low")


class ButtonLabelsTest(Base):
    """Единый стиль кнопок: подпись с большой буквы, в карточке первым — «Скопировать письмо».

    Заказчик: «Все кнопки должны начинаться с большой буквы» и «кнопки открыть вакансию
    и скопировать письмо поменять местами».
    """

    BTN = re.compile(r"class='btn[^']*'[^>]*>([^<]{1,60})</(?:a|button)>")

    def setUp(self):
        super().setUp()
        self.scored("136935354", score=92, verdict="откликаться", title="AI-инженер")
        storage.save_letter(self.conn, "136935354", "Здравствуйте! Текст письма.",
                            model="offline")

    def labels(self, page):
        return [match.strip() for match in self.BTN.findall(page)]

    def assert_capitalized(self, page, where):
        for label in self.labels(page):
            letters = [ch for ch in label if ch.isalpha()]
            if letters and letters[0].islower():
                self.fail("кнопка «%s» на странице «%s» начинается с маленькой буквы"
                          % (label, where))

    def test_list_page_buttons(self):
        page = web.render_index(self.conn, 10)
        self.assert_capitalized(page, "список")
        self.assertIn(">Открыть</a>", page)

    def test_card_buttons_and_order(self):
        page = web.render_card(self.conn, "136935354")
        self.assert_capitalized(page, "карточка")
        self.assertLess(page.index("Скопировать письмо"), page.index("Открыть вакансию на hh"))

    def test_help_and_profile_buttons(self):
        self.assert_capitalized(web.render_help(), "руководство")
        self.assert_capitalized(web.render_profile(), "профиль")


class TableColumnsTest(Base):
    def setUp(self):
        super().setUp()
        self.scored("136935354", score=92, verdict="откликаться", title="AI-инженер")
        self.scored("200", score=47, verdict="условно", title="Аналитик")
        storage.save_letter(self.conn, "136935354", "Здравствуйте! Текст письма.",
                            model="offline")

    def page(self, **kwargs):
        return web.render_index(self.conn, 10, **kwargs)

    def test_id_is_the_first_column(self):
        page = self.page()
        self.assertIn("<table><thead><tr><th>ID</th>", page)
        self.assertIn("<td class='vid' title='id вакансии на hh.ru'>136935354</td>", page)
        # ID идёт до колонки «Оценка»
        self.assertLess(page.index("<th>ID</th>"), page.index("<th>Оценка"))

    def test_score_is_shown_as_school_grade(self):
        page = self.page()
        self.assertIn("<span class='score s-high' title='оценка 5 из 5'>5</span>", page)
        self.assertIn("<span class='score s-mid' title='оценка 3 из 5'>3</span>", page)
        self.assertNotIn(">92<", page)          # сырой балл в таблице не печатается
        self.assertNotIn("из 100", page)        # 100-балльная шкала убрана из интерфейса

    def test_letter_column_on_the_right(self):
        page = self.page()
        self.assertIn("<th>Письмо", page)
        self.assertIn("class='letter-dot' title='письмо сгенерировано'", page)
        self.assertIn("<td class='letter'></td>", page)   # письма нет — поле пустое
        # колонка стоит после «Зарплата» и до кнопки «открыть»
        self.assertLess(page.index("<th>Зарплата"), page.index("<th>Письмо"))
        self.assertLess(page.index("<th>Письмо"), page.index("<th></th></tr>"))

    def test_letter_written_by_hand_is_marked(self):
        storage.save_letter(self.conn, "200", "Своё письмо", model="manual", edited=True)
        page = self.page()
        self.assertIn("title='письмо написано вручную'", page)

    def test_letter_filter(self):
        page = self.page(filters={"f_letter": ["есть"]})
        self.assertIn("href='/v/136935354'", page)
        self.assertNotIn("href='/v/200'", page)
        page = self.page(filters={"f_letter": ["нет"]})
        self.assertIn("href='/v/200'", page)
        self.assertNotIn("href='/v/136935354'", page)

    def test_grade_filter_uses_school_scale(self):
        page = self.page(filters={"f_score": ["5"]})
        self.assertIn("href='/v/136935354'", page)
        self.assertNotIn("href='/v/200'", page)

    def test_guide_button_is_green(self):
        page = self.page()
        self.assertIn("<a class='btn success' href='/help'>Руководство</a>", page)
        self.assertIn(".btn.success{background:linear-gradient(", web.CSS)


class CardLetterButtonsTest(Base):
    def card(self):
        self.scored("136935354", score=92)
        return web.render_card(self.conn, "136935354")

    def test_generate_button_is_left_of_save(self):
        page = self.card()
        self.assertLess(page.index("Сгенерировать письмо"), page.index("Сохранить письмо"))
        # кнопка сохранения работает через form="letter-save", форма письма существует
        self.assertIn("id='letter-save'", page)
        self.assertIn("form='letter-save'", page)

    def test_card_shows_grade_only(self):
        page = self.card()
        self.assertIn("оценка 5 из 5", page)
        self.assertNotIn("из 100", page)


class CardHeaderTest(Base):
    """Компания — часть названия вакансии, а не «шапка» страницы."""

    def card(self, company="Спецобъединение Юго-Запад"):
        self.scored("136935354", score=54, verdict="условно",
                    title="Специалист по внедрению ИИ / AI Specialist / AI-тренер",
                    company=company)
        return web.render_card(self.conn, "136935354")

    def test_company_is_next_to_the_title(self):
        page = self.card()
        self.assertIn("<b>Специалист по внедрению ИИ / AI Specialist / AI-тренер</b>"
                      " <span class='card-company'>· Спецобъединение Юго-Запад</span>", page)

    def test_company_is_not_in_the_header(self):
        page = self.card()
        header = page.split("<header>", 1)[1].split("</header>", 1)[0]
        self.assertNotIn("Спецобъединение Юго-Запад", header)
        self.assertIn("К списку", header)

    def test_browser_tab_title_has_title_and_company(self):
        page = self.card()
        self.assertIn("<title>Специалист по внедрению ИИ / AI Specialist / AI-тренер — "
                      "Спецобъединение Юго-Запад</title>", page)

    def test_empty_company_does_not_break_the_line(self):
        page = self.card(company="")
        self.assertIn("<b>Специалист по внедрению ИИ / AI Specialist / AI-тренер</b> ·", page)
        self.assertNotIn("<span class='card-company'>", page)


if __name__ == "__main__":
    unittest.main()
