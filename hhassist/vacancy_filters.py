"""Значения и фильтры колонок списка вакансий, до ограничения страницы."""
COLUMNS = (("score", "Оценка"), ("title", "Вакансия"), ("company", "Компания"),
           ("city", "Город"), ("format", "Формат"), ("experience", "Опыт"),
           ("salary", "Зарплата"))


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
        return str(row["score"]) if row["score"] is not None else "—"
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


def options(rows, column, filters):
    # Значения других колонок учитываются; собственный выбор не сужает своё меню.
    other = {key: values for key, values in filters.items() if key != column}
    values = {value(row, column) for row in apply(rows, other)}
    values.update(filters.get(column, []))
    if column == "score":
        return sorted(values, key=lambda v: (v == "—", -int(v) if v.isdigit() else 0))
    return sorted(values, key=lambda v: v.casefold().replace("ё", "е"))
