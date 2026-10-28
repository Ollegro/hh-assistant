"""Значения и фильтры колонок списка вакансий, до ограничения страницы."""
from . import scoring

COLUMNS = (("score", "Оценка"), ("title", "Вакансия"), ("company", "Компания"),
           ("city", "Город"), ("format", "Формат"), ("experience", "Опыт"),
           ("salary", "Зарплата"), ("letter", "Письмо"))


def salary(row):
    low, high, currency = row["salary_from"], row["salary_to"], row["currency"] or ""
    if low is not None and high is not None:
        return "%s–%s %s" % (low, high, currency)
    if low is not None:
        return "от %s %s" % (low, currency)
    if high is not None:
        return "до %s %s" % (high, currency)
    return "—"


def value(row, column):
    if column == "score":
        grade = scoring.school_grade(row["score"])
        return str(grade) if grade else "—"
    if column == "letter":
        try:
            has_letter = row["has_letter"]
        except (IndexError, KeyError):
            has_letter = 0
        return "есть" if has_letter else "нет"
    if column == "city":
        return row["area"] or row["region"] or "—"
    if column == "format":
        return row["work_format"] or "—"
    if column == "salary":
        return salary(row)
    return row[column] or "—"


def selected(query):
    return {column: list(dict.fromkeys(query.get("f_" + column, []))) for column, _ in COLUMNS}


def apply(rows, filters):
    return [row for row in rows if all(not values or value(row, col) in values
                                     for col, values in filters.items())]


def _sorted_values(column, values):
    """Порядок значений в меню фильтра: оценка по убыванию, письмо — «есть» первым."""
    if column == "score":
        return sorted(values, key=lambda v: (v == "—", -int(v) if v.isdigit() else 0))
    if column == "letter":
        order = {"есть": 0, "нет": 1}
        return sorted(values, key=lambda v: order.get(v, 2))
    return sorted(values, key=lambda v: v.casefold().replace("ё", "е"))


def options(rows, column, filters):
    """Значения колонки для меню фильтра: учитывают фильтры других колонок."""
    other = {key: values for key, values in filters.items() if key != column}
    values = {value(row, column) for row in apply(rows, other)}
    values.update(filters.get(column, []))
    return _sorted_values(column, values)


def options_all(rows, filters):
    """Значения всех колонок за один проход: {колонка: [значения]}.

    Меню колонки учитывает фильтры других колонок, но не свой выбор — как в
    options(). Считаем все колонки за один проход по строкам: раньше список
    фильтровался семь раз, по разу на колонку.
    """
    others = {col: set(values) for col, values in filters.items() if values}
    result = {col: set() for col, _ in COLUMNS}
    for row in rows:
        values = {col: value(row, col) for col, _ in COLUMNS}
        missed = {col for col, wanted in others.items() if values.get(col) not in wanted}
        for col in result:
            if col not in missed:
                result[col].add(values[col])
    for col, values in filters.items():
        result.setdefault(col, set()).update(values)
    return {col: _sorted_values(col, values) for col, values in result.items()}
