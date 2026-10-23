# -*- coding: utf-8 -*-
"""Проверка токена приложения hh.ru и доступа к api.hh.ru.

Запуск из папки приложения:
    python tools/hh_api_token.py            # настройки из .env приложения
    python tools/hh_api_token.py --refresh  # перевыпустить токен (client_credentials)

Отчёт дублируется в out/hh-api-check.txt. Значения токена и client_secret
не печатаются: в отчёте только источник токена и его длина.
"""
import argparse
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from hhassist import hh_api  # noqa: E402
from hhassist.config import get_settings  # noqa: E402

OUT = os.path.join(APP, "out", "hh-api-check.txt")


def main(argv=None):
    parser = argparse.ArgumentParser(description="проверка токена приложения hh.ru")
    parser.add_argument("--refresh", action="store_true", help="перевыпустить токен")
    args = parser.parse_args(argv)

    s = get_settings()
    if args.refresh:
        hh_api.reset_token()

    lines = [
        "приложение: %s" % APP,
        "User-Agent: %s" % s.user_agent,
        "HH_SOURCE: %s" % s.hh_source,
        "HH_APP_TOKEN: %s" % ("задан" if s.hh_app_token else "пусто"),
        "HH_CLIENT_ID/SECRET: %s" % ("заданы" if (s.hh_client_id and s.hh_client_secret) else "пусто"),
        "",
    ]

    report = hh_api.probe()
    lines.append("источник токена: %s" % report["token_source"])
    if report["token_length"]:
        lines.append("длина токена: %d символов" % report["token_length"])
    if report["me"]:
        lines.append("GET /me: id=%s name=%s" % (report["me"].get("id"),
                                                  report["me"].get("name") or "—"))
    if report["found"] is not None:
        lines.append("GET /vacancies?text=python&per_page=1: найдено %s" % report["found"])

    if report["error"]:
        err = report["error"]
        if err["status"]:
            lines.append("ОШИБКА: HTTP %s %s" % (err["status"], err["value"]))
        else:
            lines.append("ОШИБКА: настройка hh")
        lines.append("сообщение: %s" % err["message"])
        if err["request_id"]:
            lines.append("request_id: %s (указать в письме на api@hh.ru)" % err["request_id"])
        lines.append("")
        lines.append("что делать: без одобренной заявки на dev.hh.ru оставьте HH_SOURCE=html —")
        lines.append("приложение работает через HTML-парсер и токен не требует.")
        code = 1
    else:
        lines.append("итог: API доступен, можно ставить HH_SOURCE=api")
        code = 0

    text = "\n".join(lines) + "\n"
    print(text)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print("отчёт: %s" % OUT)
    return code


if __name__ == "__main__":
    sys.exit(main())
