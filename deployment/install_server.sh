#!/usr/bin/env bash
set -euo pipefail

PROJECT_PATH="/opt/quant-bot"
APP_USER="quantbot"
APP_GROUP="quantbot"
ENV_PATH="/etc/quant-bot/quant-bot.env"
SERVICE_PATH="/etc/systemd/system/quant-bot.service"
TIMER_PATH="/etc/systemd/system/quant-bot.timer"
BACKUP_SCRIPT_PATH="/usr/local/bin/quant-bot-backup"

install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0755 "${PROJECT_PATH}"
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 /var/lib/quant-bot
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 /var/backups/quant-bot
install -d -o root -g "${APP_GROUP}" -m 0750 /etc/quant-bot

if [ ! -f "${ENV_PATH}" ]; then
  install -m 0600 /dev/null "${ENV_PATH}"
fi
chown root:"${APP_GROUP}" "${ENV_PATH}"
chmod 0600 "${ENV_PATH}"

cp "${PROJECT_PATH}/deployment/quant-bot.service" "${SERVICE_PATH}"
cp "${PROJECT_PATH}/deployment/quant-bot.timer" "${TIMER_PATH}"
install -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${PROJECT_PATH}/deployment/backup_daily_database.sh" "${BACKUP_SCRIPT_PATH}"

systemctl daemon-reload
systemctl enable quant-bot.timer
systemctl start quant-bot.timer
