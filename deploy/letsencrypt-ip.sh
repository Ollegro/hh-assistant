#!/usr/bin/env bash
# Доверенный сертификат Let's Encrypt прямо на IP-адрес (без домена).
#
# Let's Encrypt с 2026 года выдаёт сертификаты на IP, но только с профилем
# "shortlived" (6 дней) и требует certbot 5.4+; системный certbot из Ubuntu
# 24.04 (2.9) таких флагов не знает, поэтому свежий ставится в отдельный venv.
#
# Запуск (от root, приложение уже установлено deploy/install.sh):
#   bash deploy/letsencrypt-ip.sh
#   bash deploy/letsencrypt-ip.sh --ip 203.0.113.10 --webroot /var/www/certbot \
#        --dir /opt/hh-assistant --user hhassist --email me@example.com
#   bash deploy/letsencrypt-ip.sh --dry-run     # тренировка на staging
#
# Что делает: certbot в /opt/certbot-ip → выпуск сертификата (http-01 через
# webroot вашего веб-сервера) → hook, который кладёт fullchain/privkey в
# config/tls.* и перезапускает hh-assistant → systemd-таймер продления дважды
# в сутки (сертификат живёт 6 дней).
set -euo pipefail

IP=""
WEBROOT="/var/www/certbot"
DIR="/opt/hh-assistant"
RUN_USER="hhassist"
EMAIL=""
DRY_RUN=0
CERT_NAME="hh-ip"

while [ $# -gt 0 ]; do
    case "$1" in
        --ip) IP="$2"; shift 2 ;;
        --webroot) WEBROOT="$2"; shift 2 ;;
        --dir) DIR="$2"; shift 2 ;;
        --user) RUN_USER="$2"; shift 2 ;;
        --email) EMAIL="$2"; shift 2 ;;
        --cert-name) CERT_NAME="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "неизвестный параметр: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$IP" ]; then
    IP="$(curl -s --max-time 10 https://api.ipify.org || true)"
    [ -n "$IP" ] || IP="$(hostname -I | awk '{print $1}')"
fi
[ -n "$IP" ] || { echo "не удалось определить IP — задайте --ip" >&2; exit 2; }

echo "IP: $IP"
echo "webroot: $WEBROOT (каталог, который ваш веб-сервер отдаёт по этому IP на порту 80)"
echo "приложение: $DIR (пользователь $RUN_USER)"

# 1. Свежий certbot в отдельном venv: системный 2.9.0 не умеет --ip-address.
CERTBOT=/opt/certbot-ip/bin/certbot
if [ ! -x "$CERTBOT" ]; then
    apt-get update -qq
    apt-get install -y -qq python3-venv
    python3 -m venv /opt/certbot-ip
    /opt/certbot-ip/bin/python -m pip install --quiet --upgrade pip certbot
fi
"$CERTBOT" --version

# 2. Hook: свежий сертификат — в приложение, затем перезапуск сервиса.
HOOK=/usr/local/bin/hh-tls-deploy.sh
cat > "$HOOK" <<HOOKEOF
#!/bin/sh
# Кладёт свежий сертификат Let's Encrypt в приложение и перезапускает его.
set -e
LIVE=/etc/letsencrypt/live/$CERT_NAME
install -m 644 -o $RUN_USER -g $RUN_USER "\$LIVE/fullchain.pem" $DIR/config/tls.crt
install -m 600 -o $RUN_USER -g $RUN_USER "\$LIVE/privkey.pem" $DIR/config/tls.key
systemctl restart hh-assistant
HOOKEOF
chmod +x "$HOOK"

# 3. Выпуск (http-01: файл проверки отдаёт уже работающий веб-сервер).
mkdir -p "$WEBROOT/.well-known/acme-challenge"
EXTRA=(--register-unsafely-without-email)
[ -n "$EMAIL" ] && EXTRA=(--email "$EMAIL")
ARGS=(certonly --preferred-profile shortlived --webroot --webroot-path "$WEBROOT"
      --ip-address "$IP" --cert-name "$CERT_NAME" --non-interactive --agree-tos
      "${EXTRA[@]}" --deploy-hook "$HOOK")
[ "$DRY_RUN" = "1" ] && ARGS+=(--staging)
"$CERTBOT" "${ARGS[@]}"

if [ "$DRY_RUN" = "1" ]; then
    echo "тренировка на staging прошла: боевой сертификат не выдан"
    exit 0
fi

# 4. Продление дважды в сутки: срок жизни сертификата — 6 дней.
cat > /etc/systemd/system/hh-certbot-renew.service <<UNITEOF
[Unit]
Description=Продление сертификата Let's Encrypt для hh-assistant (IP)
[Service]
Type=oneshot
ExecStart=$CERTBOT renew --quiet --deploy-hook $HOOK
UNITEOF
cat > /etc/systemd/system/hh-certbot-renew.timer <<'UNITEOF'
[Unit]
Description=Проверка продления сертификата hh-assistant дважды в сутки
[Timer]
OnCalendar=*-*-* 03,15:30:00
RandomizedDelaySec=600
Persistent=true
[Install]
WantedBy=timers.target
UNITEOF
systemctl daemon-reload
systemctl enable --now hh-certbot-renew.timer
systemctl list-timers hh-certbot-renew.timer --no-pager | head -3

echo "готово: https://$IP:8443 — сертификат доверенный, браузер не предупреждает"
