#!/usr/bin/env bash
# Установка hh-assistant на сервер (Ubuntu 22.04/24.04, Debian 12).
#
#   sudo bash deploy/install.sh                 # код из текущего каталога -> /opt/hh-assistant
#   sudo bash deploy/install.sh --port 9443     # другой порт
#   sudo bash deploy/install.sh --repo URL --dir /opt/hh-assistant
#
# Скрипт идемпотентен: повторный запуск обновляет код и перезапускает сервис,
# но не перезаписывает .env и сертификаты. Пароль входа генерируется один раз
# и хранится только в .env (в вывод не печатается).
set -euo pipefail

REPO="https://github.com/Ollegro/hh-assistant.git"
DIR="/opt/hh-assistant"
PORT="8443"
USER_NAME="hhassist"

while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --dir) DIR="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --user) USER_NAME="$2"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "неизвестный параметр: $1 (см. --help)"; exit 1 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "запустите через sudo"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"

echo "== 1/7 пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 git openssl curl ufw ca-certificates >/dev/null

echo "== 2/7 пользователь $USER_NAME"
id -u "$USER_NAME" >/dev/null 2>&1 || \
    useradd --system --home "$DIR" --shell /usr/sbin/nologin "$USER_NAME"

echo "== 3/7 код в $DIR"
if [ "$SRC" != "$DIR" ] && [ -f "$SRC/cli.py" ]; then
    mkdir -p "$DIR"
    tar -C "$SRC" \
        --exclude=.git --exclude=data --exclude=out --exclude=.env \
        --exclude=__pycache__ --exclude='*.sqlite3' \
        -cf - . | tar -C "$DIR" -xf -
elif [ ! -f "$DIR/cli.py" ]; then
    git clone --depth 1 "$REPO" "$DIR"
fi
mkdir -p "$DIR/data" "$DIR/out" "$DIR/config"
chmod 700 "$DIR/data" "$DIR/out"
chown -R "$USER_NAME:$USER_NAME" "$DIR"

echo "== 4/7 .env (пароль входа и HTTPS)"
if [ ! -f "$DIR/.env" ]; then
    cp "$DIR/.env.example" "$DIR/.env"
    echo "   .env создан из .env.example"
else
    echo "   .env уже есть — не перезаписываю"
fi

if grep -q '^WEB_AUTH_PASSWORD=..*' "$DIR/.env"; then
    echo "   пароль входа уже задан — оставляю как есть"
else
    PASS="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-20)"
    sed -i '/^WEB_AUTH_PASSWORD=/d' "$DIR/.env"
    {
        echo ""
        echo "# --- вход в веб-интерфейс (создано install.sh $(date +%F)) ---"
        echo "WEB_AUTH_PASSWORD=$PASS"
    } >> "$DIR/.env"
    echo "   пароль входа сгенерирован"
fi
grep -q '^WEB_AUTH_USER=' "$DIR/.env" || echo "WEB_AUTH_USER=hh" >> "$DIR/.env"

PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org || true)"
if [ -z "$PUBLIC_IP" ]; then
    PUBLIC_IP="$(hostname -I | awk '{print $1}')"
fi

CERT="$DIR/config/tls.crt"
KEY="$DIR/config/tls.key"
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
        -keyout "$KEY" -out "$CERT" -subj "/CN=$PUBLIC_IP" \
        -addext "subjectAltName=IP:$PUBLIC_IP" >/dev/null 2>&1
    echo "   самоподписанный сертификат для IP $PUBLIC_IP создан"
fi
chmod 600 "$KEY"
chmod 644 "$CERT"

grep -q '^WEB_TLS_CERT=' "$DIR/.env" || echo "WEB_TLS_CERT=$CERT" >> "$DIR/.env"
grep -q '^WEB_TLS_KEY=' "$DIR/.env" || echo "WEB_TLS_KEY=$KEY" >> "$DIR/.env"
chown "$USER_NAME:$USER_NAME" "$DIR/.env" "$CERT" "$KEY"
chmod 600 "$DIR/.env"

echo "== 5/7 systemd-сервис"
sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$USER_NAME|g" -e "s|__PORT__|$PORT|g" \
    "$DIR/deploy/hh-assistant.service" > /etc/systemd/system/hh-assistant.service
systemctl daemon-reload
systemctl enable hh-assistant >/dev/null 2>&1 || true
systemctl restart hh-assistant

echo "== 6/7 файрвол (ufw)"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow "$PORT"/tcp >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true

echo "== 7/7 проверка"
sleep 2
systemctl is-active --quiet hh-assistant || {
    echo "Сервис не запустился. Логи:"
    journalctl -u hh-assistant -n 30 --no-pager
    exit 1
}
CODE="$(curl -ks -o /dev/null -w '%{http_code}' "https://127.0.0.1:$PORT/" || true)"
echo "   https://127.0.0.1:$PORT/ -> HTTP $CODE (401 — значит вход работает)"

cat <<EOF

Готово.
  адрес:   https://$PUBLIC_IP:$PORT
  логин:   $(grep -m1 '^WEB_AUTH_USER=' "$DIR/.env" | cut -d= -f2)
  пароль:  значение WEB_AUTH_PASSWORD в $DIR/.env
  логи:    journalctl -u hh-assistant -f
  рестарт: systemctl restart hh-assistant

Браузер покажет предупреждение о самоподписанном сертификате — это ожидаемо
(«Дополнительно» → «Перейти на сайт»). Подробности и обновление — deploy/README.md.
EOF
