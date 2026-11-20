# -*- coding: utf-8 -*-
"""Фильтры сбора: не тащить в базу архивные и уже отработанные вакансии.

Зачем отдельный модуль: правило «что не берём» используется в двух местах
(`jobs.collect_queries` для пакетного сбора и `manual.add` для ручного добавления),
и оно должно быть одинаковым. Раньше фильтров не было вовсе — в базу попадали
вакансии из архива hh и те, на которые владелец уже откликался, и они всплывали
в списке снова после каждого сбора.

Что считается отработанным:
  * есть запись «отправлено вручную» в журнале `applications` (реальный отклик);
  * вакансия отмечена «пропущено» — решение уже принято, второй раз не предлагаем.

Архивные:
  * hh отдаёт поле `archived` в выдаче API (проверено 20.11.2026: поле есть);
  * у getmatch есть признак «Вакансия в архиве» в тексте карточки;
  * у Хабра и «Работы России» признак ищем по тексту описания.
"""
import json

# Подписи, по которым вакансия считается закрытой (архив/снята), если площадка
# не отдаёт структурного признака.
ARCHIVE_MARKERS = (
    "вакансия в архиве",
    "вакансия снята с публикации",
    "вакансия закрыта",
    "архивная вакансия",
    "эта вакансия больше не доступна",
)


class FilterStats:
    """Счётчики отброшенного — их показываем в отчёте сбора."""

    def __init__(self):
        self.archived = 0
        self.applied = 0
        self.skipped = 0

    @property
    def total(self):
        return self.archived + self.applied + self.skipped

    def note(self):
        """Строка для отчёта или '' — если отбрасывать было нечего."""
        parts = []
        if self.archived:
            parts.append("архивных: %d" % self.archived)
        if self.applied:
            parts.append("уже откликался: %d" % self.applied)
        if self.skipped:
            parts.append("ранее пропущено: %d" % self.skipped)
        return ("не взяли " + ", ".join(parts)) if parts else ""


def is_archived(vacancy):
    """True, если вакансия в архиве: структурный флаг или подпись в тексте."""
    if vacancy.get("archived"):
        return True
    text = ((vacancy.get("title") or "") + " " + (vacancy.get("description") or "")).lower()
    return any(marker in text for marker in ARCHIVE_MARKERS)


def closed_ids(conn):
    """{vacancy_id: 'applied'|'skipped'} — вакансии, по которым решение уже принято.

    Статусы берём из журнала `applications`: текущий статус — последняя запись
    по вакансии, поэтому одна и та же вакансия не считается дважды.
    """
    from . import storage

    rows = conn.execute(
        "SELECT vacancy_id, status FROM applications WHERE id IN "
        "(SELECT MAX(id) FROM applications GROUP BY vacancy_id)"
    ).fetchall()
    result = {}
    for row in rows:
        kind = storage.status_kind(row["status"])
        if kind == "sent":
            result[row["vacancy_id"]] = "applied"
        elif kind == "skip":
            result.setdefault(row["vacancy_id"], "skipped")
    return result


def reject(vacancy, closed=None, stats=None, skip_skipped=True):
    """Стоит ли отбросить вакансию. True — не сохраняем.

    `closed` — результат `closed_ids()`, `stats` — FilterStats для счётчиков.
    `skip_skipped=False` оставляет вакансии, которые раньше пропустили:
    владелец может вернуться к ним, если поменял профиль.
    """
    if is_archived(vacancy):
        if stats is not None:
            stats.archived += 1
        return True
    if closed:
        mark = closed.get(str(vacancy.get("id") or ""))
        if mark == "applied":
            if stats is not None:
                stats.applied += 1
            return True
        if mark == "skipped" and skip_skipped:
            if stats is not None:
                stats.skipped += 1
            return True
    return False


def payload(**kwargs):
    """Словарь для записи в журнал событий (без None и пустых значений)."""
    data = {key: value for key, value in kwargs.items() if value not in (None, "")}
    return json.loads(json.dumps(data, ensure_ascii=False))
