#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIRECTORY="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"

SSH_PORT="${SSH_PORT:-22}"
ENABLE_UFW="${ENABLE_UFW:-0}"

APP_USER="ubuntu"
APP_GROUP="ubuntu"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this script as root." >&2
    exit 1
fi

if [[ ! "${SSH_PORT}" =~ ^[0-9]+$ ]] \
    || ((SSH_PORT < 1 || SSH_PORT > 65535)); then
    echo "SSH_PORT must be between 1 and 65535." >&2
    exit 2
fi

source /etc/os-release

if [[ "${ID}" != "ubuntu" || "${VERSION_ID}" != "24.04" ]]; then
    echo "This bootstrap supports Ubuntu Server 24.04 only." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "Installing operating-system packages..."

apt-get update

apt-get install -y \
    build-essential \
    ca-certificates \
    certbot \
    curl \
    ffmpeg \
    gettext-base \
    libgl1 \
    libglib2.0-0 \
    nginx \
    postgresql-client \
    python3.12 \
    python3.12-dev \
    python3.12-venv \
    python3-pip \
    rsync \
    ufw

curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs

node_major_version="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"

if [[ ! "$node_major_version" =~ ^[0-9]+$ ]] || ((node_major_version < 22)); then
    echo "Node.js 22 LTS or newer is required for dashboard builds." >&2
    exit 1
fi

npm --version >/dev/null

if ! getent group "$APP_GROUP" >/dev/null; then
    groupadd --system "$APP_GROUP"
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd \
        --system \
        --gid "$APP_GROUP" \
        --home-dir /nonexistent \
        --shell /usr/sbin/nologin \
        "$APP_USER"
fi

install -d \
    -o root \
    -g "$APP_GROUP" \
    -m 0750 \
    /opt/android-remote \
    /opt/android-remote/releases

install -d \
    -o root \
    -g root \
    -m 0750 \
    /etc/android-remote

install -d \
    -o "$APP_USER" \
    -g "$APP_GROUP" \
    -m 0750 \
    /srv/timelapse \
    /srv/timelapse/images \
    /srv/timelapse/exports \
    /srv/timelapse/quarantine \
    /srv/timelapse/tmp

install -d \
    -o root \
    -g root \
    -m 0755 \
    /var/www/certbot

install -d \
    -o root \
    -g www-data \
    -m 0755 \
    /var/www/android-remote \
    /var/www/android-remote/dashboard

systemctl enable --now nginx

echo "Adding required UFW rules without removing existing rules..."

ufw allow "${SSH_PORT}/tcp" comment "administrative SSH"
ufw allow 80/tcp comment "HTTP and ACME"
ufw allow 443/tcp comment "HTTPS"

if ufw status | grep -q "Status: active"; then
    echo "UFW is already active. Existing rules were preserved."
elif [[ "$ENABLE_UFW" == "1" ]]; then
    ufw default deny incoming
    ufw default allow outgoing
    ufw --force enable
else
    echo
    echo "UFW rules were added, but UFW was not enabled."
    echo "This protects an existing VPS from an accidental SSH lockout."
    echo
    echo "After verifying SSH_PORT=${SSH_PORT}, enable it with:"
    echo "  sudo ufw default deny incoming"
    echo "  sudo ufw default allow outgoing"
    echo "  sudo ufw enable"
    echo
    echo "Or rerun:"
    echo "  sudo ENABLE_UFW=1 SSH_PORT=${SSH_PORT} $0"
fi

echo
echo "Bootstrap completed."

echo
echo "UFW:"
ufw status numbered
