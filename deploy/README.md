# Размещение hh-assistant на сервере (Selectel, вход по IP)

Приложение работает на чистой стандартной библиотеке Python: ни `pip install`, ни
виртуальное окружение не нужны. Достаточно самого дешёвого VPS с 1 ГБ памяти.

Что получится: `https://<IP-сервера>:8443` — страница со списком вакансий, вход по логину
и паролю (`WEB_AUTH_USER` / `WEB_AUTH_PASSWORD` из `.env`), данные и база — на сервере.

---

## 1. Создать сервер в Selectel

Панель → **Облачные серверы** → **Создать сервер**:

| Параметр | Значение |
|---|---|
| Зона | ближайшая к вам (например, Москва) |
| Образ | Ubuntu 24.04 LTS (подойдёт 22.04) |
| Конфигурация | 1 vCPU, 1 ГБ RAM, 10 ГБ SSD — минимум, этого хватает |
| Сеть | публичный IPv4; приватная сеть не нужна |
| Доступ | SSH-ключ (свой публичный ключ), вход по паролю root лучше отключить |

После создания в разделе **Группы безопасности** (файрвол облака) разрешите входящие:

- `22/tcp` — SSH;
- `8443/tcp` — веб-интерфейс.

Если группа не назначена, облачный файрвол обычно пропускает всё — тогда защиту даёт
только `ufw` на самом сервере (его настраивает скрипт).

## 2. Установка одной командой

```bash
ssh root@<IP-сервера>
apt-get update && apt-get install -y git
git clone --depth 1 https://github.com/Ollegro/hh-assistant.git /root/hh-src
bash /root/hh-src/deploy/install.sh
```

Скрипт делает 7 шагов и печатает итог: пакеты (`python3`, `openssl`, `ufw`), системный
пользователь `hhassist`, код в `/opt/hh-assistant`, `.env` со случайным паролем входа,
самоподписанный сертификат на IP (доверенный Let's Encrypt — `deploy/letsencrypt-ip.sh`,
п. 4), systemd-сервис `hh-assistant`, правила `ufw`.

Параметры: `--port 8443`, `--dir /opt/hh-assistant`, `--user hhassist`, `--repo <git-url>`.
Повторный запуск безопасен: код обновляется, `.env` и сертификаты не перезаписываются.

## 3. Первый вход

1. Откройте `https://<IP-сервера>:8443`.
2. Сертификат из `install.sh` самоподписанный: Chrome и Firefox дают ссылку
   «Дополнительно» → «Перейти на сайт», а **Яндекс.Браузер такой ссылки не показывает**
   (`ERR_CERT_AUTHORITY_INVALID`) — приложение в нём просто не открывается.
   Доверенный сертификат на тот же IP выпускается одной командой (см. п. 4):

   ```bash
   bash /root/hh-src/deploy/letsencrypt-ip.sh
   ```

3. Введите логин `hh` и пароль:

```bash
grep '^WEB_AUTH_PASSWORD=' /opt/hh-assistant/.env
```

Сменить пароль: `nano /opt/hh-assistant/.env` → `systemctl restart hh-assistant`.

## 4. Доверенный сертификат Let's Encrypt на IP

С 2026 года Let's Encrypt выдаёт сертификаты на IP-адреса (только профиль
`shortlived`, срок жизни 6 дней). Тогда «зелёный замок» есть во всех браузерах,
включая Яндекс, и предупреждения нет.

```bash
bash /root/hh-src/deploy/letsencrypt-ip.sh                  # IP определяется сам
bash /root/hh-src/deploy/letsencrypt-ip.sh --dry-run        # тренировка на staging
bash /root/hh-src/deploy/letsencrypt-ip.sh --ip 203.0.113.10 --email me@example.com
```

Что нужно, чтобы проверка прошла:

| Условие | Как проверить |
|---|---|
| порт 80 открыт снаружи и отвечает на `http://<IP>/` | `curl -s -o /dev/null -w '%{http_code}\n' http://<IP>/` |
| каталог `--webroot` отдаётся по этому IP на 80-м порту | положить файл в `<webroot>/.well-known/acme-challenge/` и запросить `http://<IP>/.well-known/acme-challenge/<файл>` |
| certbot 5.4+ | скрипт сам ставит 5.x в `/opt/certbot-ip` (системный 2.9.0 флагов IP не знает) |

Скрипт создаёт hook `/usr/local/bin/hh-tls-deploy.sh` (кладёт `fullchain.pem` и
`privkey.pem` в `config/tls.crt` / `config/tls.key` и перезапускает сервис) и таймер
`hh-certbot-renew.timer` — проверка продления дважды в сутки, потому что сертификат
живёт 6 дней.

Проверка результата:

```bash
openssl s_client -connect <IP>:8443 </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates
systemctl list-timers hh-certbot-renew.timer --no-pager
/opt/certbot-ip/bin/certbot renew --dry-run
```

Если порт 80 занят веб-сервером (часто `nginx`), нужен именно **webroot его
каталога**, а не `--standalone`: `--standalone` требует освободить 80-й порт.

## 5. Ключи и личный профиль (не в git)

`.env` и `config/profile.json` намеренно исключены из репозитория — перенесите их сами:

```bash
# с домашней машины (PowerShell), файл профиля
scp config\profile.json root@<IP-сервера>:/opt/hh-assistant/config/
ssh root@<IP-сервера> "chown hhassist: /opt/hh-assistant/config/profile.json"

# ключ LLM и адрес отправителя правим только на сервере
ssh root@<IP-сервера>
nano /opt/hh-assistant/.env      # LLM_PROVIDER, LLM_MODEL, LLM_API_KEY, HH_USER_AGENT
systemctl restart hh-assistant
```

Без ключа LLM приложение работает в офлайн-режиме (правила + шаблонные письма) —
проверить контур: `python3 /opt/hh-assistant/cli.py stats` и `... tools/check_llm.py`.

## 6. Обновление версии

```bash
cd /root/hh-src && git pull
bash deploy/install.sh
```

или, если код правился на сервере: `systemctl restart hh-assistant`.
База (`data/hhassist.sqlite3`), письма (`out/`), `.env` и `config/` при обновлении не трогаются.

## 7. Резервная копия базы

SQLite умеет безопасный онлайн-бэкап (сервис можно не останавливать):

```bash
python3 - <<'PY'
import sqlite3, datetime
src = sqlite3.connect("/opt/hh-assistant/data/hhassist.sqlite3")
dst = sqlite3.connect("/root/hhassist-%s.sqlite3" % datetime.date.today())
with dst:
    src.backup(dst)
print("готово")
PY
```

Дальше файл можно забрать `scp` или сложить в Object Storage Selectel. Дополнительно помогает
снапшот диска в панели (Облачные серверы → сервер → Снапшоты).

## 8. Диагностика

```bash
systemctl status hh-assistant            # активен ли сервис
journalctl -u hh-assistant -n 50 --no-pager
ss -ltnp | grep 8443                     # слушает ли порт
curl -k -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8443/        # 401 без пароля — норма
curl -k -u hh:ПАРОЛЬ -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8443/   # 200 с паролем
```

Расшифровка кодов: `401` — вход работает и пароль запрошен; `200` — пароль верный;
`403` — запрос пришёл не с `127.0.0.1`, а пароль в `.env` не задан; `000` — TLS не поднялся,
смотрите журнал (частая причина — неверные пути `WEB_TLS_CERT` / `WEB_TLS_KEY`).

Типовые ошибки установки:

| Симптом | Причина | Что делать |
|---|---|---|
| `bash: $'\r': command not found`, `bad interpreter` | `install.sh` попал на сервер с CRLF (пересохранён в Windows) | `sed -i 's/\r$//' deploy/install.sh deploy/hh-assistant.service` |
| `address already in use` в журнале | порт занят другим процессом | `ss -ltnp \| grep 8443`, затем сменить `--port` и перезапустить скрипт |
| `401` локально, `000` снаружи | порт закрыт в группе безопасности Selectel или в `ufw` | разрешить `8443/tcp` в панели и `ufw allow 8443/tcp` |
| сертификат «не для этого адреса» | сервер получил новый IP | удалить `config/tls.crt`, `config/tls.key`, перезапустить `install.sh`, затем `letsencrypt-ip.sh` |


## 9. Что защищено и чего нет

Защищено:

- вход по паролю на **всех** страницах и действиях (включая `POST /run/*`, `/queries`,
  `/mark/*`), `401` → браузер сам показывает окно входа;
- HTTPS: пароль не передаётся открытым текстом;
- если пароль не задан, сервер пускает только `127.0.0.1` — случайный запуск с
  `--host 0.0.0.0` не откроет данные наружу (ответ `403` с подсказкой);
- `ufw`: снаружи доступны только SSH и порт веб-интерфейса;
- systemd: процесс от непривилегированного пользователя, `ProtectSystem=strict`,
  запись только в `data/`, `out/`, `config/`;
- `.env` — права `600`, `data/` и `out/` — `700`.

Честные ограничения:

- сертификат самоподписанный, поэтому браузер предупреждает при входе; доверенный
  сертификат на IP выпускается `deploy/letsencrypt-ip.sh` (Let's Encrypt, профиль
  `shortlived`: 6 дней, продление автоматическое);
- Basic Auth не даёт «выхода» из сессии, кроме закрытия браузера; один пользователь,
  без 2FA и разграничения ролей;
- весь сетевой трафик — один порт; отдельного API-токена нет (не нужен для одного человека);
- если позже появится домен, лучше поставить `nginx` с Let's Encrypt и проксировать на
  `127.0.0.1:8765` (тогда в `.env` достаточно `WEB_AUTH_*`, а `WEB_TLS_*` убрать).

## 10. Если IP сменился

Сертификат выписан на конкретный IP. После смены адреса:

```bash
rm -f /opt/hh-assistant/config/tls.crt /opt/hh-assistant/config/tls.key
bash /root/hh-src/deploy/install.sh               # самоподписанный, чтобы сервис поднялся
bash /root/hh-src/deploy/letsencrypt-ip.sh        # доверенный на новый IP (новый адрес определится сам)
```

Скрипт берёт адрес из `https://api.ipify.org`, при недоступности — из `hostname -I`.
