#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   sudo bash deployment/secure_dashboard_setup.sh [domain]
# Notes:
#   - If no domain is provided, HTTP + basic auth is configured only.
#   - TLS is configured only when certbot issuance succeeds.

DOMAIN="${1:-}"
NGINX_SITE="/etc/nginx/sites-available/quant-bot-dashboard"
NGINX_LINK="/etc/nginx/sites-enabled/quant-bot-dashboard"
HTPASSWD_FILE="/etc/nginx/.htpasswd-quant-bot-dashboard"
SOURCE_CONF="/home/quantbot/quant-trading-bot/deployment/nginx-quant-bot-dashboard.conf"

if [[ $EUID -ne 0 ]]; then
    echo "This script must run as root." >&2
    exit 1
fi

if ! command -v nginx >/dev/null 2>&1; then
    apt-get update
    apt-get install -y nginx apache2-utils
else
    apt-get install -y apache2-utils
fi

install -m 0644 "${SOURCE_CONF}" "${NGINX_SITE}"
ln -sf "${NGINX_SITE}" "${NGINX_LINK}"
rm -f /etc/nginx/sites-enabled/default

if [[ ! -f "${HTPASSWD_FILE}" ]]; then
    echo "Create dashboard HTTP basic-auth credentials:"
    htpasswd -c "${HTPASSWD_FILE}" quantbot
else
    echo "Update dashboard HTTP basic-auth credentials for user quantbot:"
    htpasswd "${HTPASSWD_FILE}" quantbot
fi
chmod 0640 "${HTPASSWD_FILE}"
chown root:www-data "${HTPASSWD_FILE}"

nginx -t
systemctl enable nginx
systemctl restart nginx

# Keep streamlit local-only service binding.
systemctl restart quant-bot-dashboard.service
systemctl restart quant-bot-mobile-dashboard.service

if command -v ufw >/dev/null 2>&1; then
    ufw allow OpenSSH || true
    ufw allow 80/tcp || true
    if [[ -n "${DOMAIN}" ]]; then
        ufw allow 443/tcp || true
    else
        ufw --force delete allow 443/tcp || true
    fi
    ufw deny 8501/tcp || true
    ufw deny 8502/tcp || true
    ufw --force enable
fi

if [[ -n "${DOMAIN}" ]]; then
    apt-get install -y certbot python3-certbot-nginx
    set +e
    certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m admin@"${DOMAIN}" --redirect
    CERTBOT_EXIT=$?
    set -e
    if [[ ${CERTBOT_EXIT} -ne 0 ]]; then
        echo "TLS certificate issuance failed for ${DOMAIN}. HTTPS was not confirmed."
        exit 2
    fi
    echo "HTTPS configured for ${DOMAIN}."
else
    echo "No domain provided. HTTPS not configured."
fi

echo "Setup complete."
