#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIRECTORY="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"
REPOSITORY_DIRECTORY="$(
    cd -- "${SCRIPT_DIRECTORY}/.."
    pwd
)"

ENVIRONMENT_FILE="${ENVIRONMENT_FILE:-${SCRIPT_DIRECTORY}/.env}"

APP_USER="ubuntu"
APP_GROUP="ubuntu"

APP_ROOT="/opt/android-remote"
RELEASES_DIRECTORY="${APP_ROOT}/releases"
CURRENT_LINK="${APP_ROOT}/current"
VIRTUAL_ENVIRONMENT="${APP_ROOT}/.venv"

CONFIG_DIRECTORY="/etc/android-remote"
SERVER_ENVIRONMENT_FILE="${CONFIG_DIRECTORY}/server.env"

SYSTEMD_DIRECTORY="/etc/systemd/system"

NGINX_AVAILABLE="/etc/nginx/sites-available/timelapse-camera.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/timelapse-camera.conf"

ACME_WEBROOT="/var/www/certbot"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this deployment script as root." >&2
    exit 1
fi

if [[ ! -f "${ENVIRONMENT_FILE}" ]]; then
    echo "Missing environment file: ${ENVIRONMENT_FILE}" >&2
    echo "Copy environment.example to .env and configure it." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENVIRONMENT_FILE}"
set +a

required_variables=(
    PUBLIC_DOMAIN
    LETSENCRYPT_EMAIL
    API_PORT
    DATABASE_URL
    DATABASE_MIGRATION_URL
    STORAGE_ROOT
    CAMERA_TOKEN_PEPPER
)

for variable_name in "${required_variables[@]}"; do
    if [[ -z "${!variable_name:-}" ]]; then
        echo "Missing required variable: ${variable_name}" >&2
        exit 2
    fi
done

if [[ ! "${PUBLIC_DOMAIN}" =~ ^[a-z0-9.-]+$ ]]; then
    echo "PUBLIC_DOMAIN contains unsupported characters." >&2
    exit 2
fi

if [[ ! "${LETSENCRYPT_EMAIL}" =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]]; then
    echo "LETSENCRYPT_EMAIL is invalid." >&2
    exit 2
fi

if [[ ! "${API_PORT}" =~ ^[0-9]+$ ]] \
    || ((API_PORT < 1024 || API_PORT > 65535)); then
    echo "API_PORT must be between 1024 and 65535." >&2
    exit 2
fi

if [[ "${STORAGE_ROOT}" != "/srv/timelapse" ]]; then
    echo "For this deployment, STORAGE_ROOT must be /srv/timelapse." >&2
    exit 2
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
    echo "Missing service user: $APP_USER" >&2
    echo "Run infrastructure/bootstrap-ubuntu.sh first." >&2
    exit 1
fi

if ss -lntH | awk '{print $4}' \
    | grep -Eq "(^|:)${API_PORT}$"; then
    existing_api_pid="$(
        ss -lntpH \
            | awk -v suffix=":${API_PORT}" '$4 ~ suffix "$" { print }'
    )"

    if ! systemctl is-active --quiet timelapse-api.service; then
        echo "API_PORT ${API_PORT} is already occupied:" >&2
        echo "${existing_api_pid}" >&2
        exit 1
    fi
fi

install -d \
    -o root \
    -g "$APP_GROUP" \
    -m 0750 \
    "$APP_ROOT" \
    "$RELEASES_DIRECTORY"

install -d \
    -o root \
    -g root \
    -m 0750 \
    "$CONFIG_DIRECTORY"

install -d \
    -o "$APP_USER" \
    -g "$APP_GROUP" \
    -m 0750 \
    "$STORAGE_ROOT" \
    "$STORAGE_ROOT/images" \
    "$STORAGE_ROOT/exports" \
    "$STORAGE_ROOT/quarantine" \
    "$STORAGE_ROOT/tmp"

install \
    -o root \
    -g root \
    -m 0600 \
    "$ENVIRONMENT_FILE" \
    "$SERVER_ENVIRONMENT_FILE"

release_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
release_directory="${RELEASES_DIRECTORY}/${release_id}"

echo "Creating release: ${release_id}"

install -d \
    -o root \
    -g "$APP_GROUP" \
    -m 0750 \
    "$release_directory"

rsync \
    --archive \
    --delete \
    "${REPOSITORY_DIRECTORY}/server/" \
    "${release_directory}/server/"

chown -R root:"$APP_GROUP" "$release_directory"
chmod -R u=rwX,g=rX,o= "$release_directory"

if [[ ! -d "$VIRTUAL_ENVIRONMENT" ]]; then
    echo "Creating shared Python virtual environment..."

    python3.12 -m venv "$VIRTUAL_ENVIRONMENT"

    chown -R root:"$APP_GROUP" "$VIRTUAL_ENVIRONMENT"
    chmod -R u=rwX,g=rX,o= "$VIRTUAL_ENVIRONMENT"
fi

echo "Installing server package and dependencies..."

"$VIRTUAL_ENVIRONMENT/bin/python" \
    -m pip install \
    --upgrade \
    pip \
    setuptools \
    wheel

"$VIRTUAL_ENVIRONMENT/bin/python" \
    -m pip install \
    --upgrade \
    --no-cache-dir \
    "${release_directory}/server"

"$VIRTUAL_ENVIRONMENT/bin/python" \
    -m pip freeze \
    > "${release_directory}/pip-freeze.txt"

chown root:"$APP_GROUP" "${release_directory}/pip-freeze.txt"
chmod 0640 "${release_directory}/pip-freeze.txt"

previous_release=""

if [[ -L "$CURRENT_LINK" ]]; then
    previous_release="$(readlink -f "$CURRENT_LINK")"
fi

echo "Stopping existing time-lapse runtime services..."

systemctl stop \
    timelapse-api.service \
    timelapse-worker.service \
    timelapse-bot.service \
    2>/dev/null || true

validate_neon_urls() {
    echo "Validating Neon database URLs..."

    runuser \
        --user "$APP_USER" \
        --preserve-environment \
        -- \
        "$VIRTUAL_ENVIRONMENT/bin/python" - <<'PY'
from timelapse.configuration import get_settings

settings = get_settings()

runtime = settings.runtime_database_url
migration = settings.migration_database_url

if runtime.host is None:
    raise SystemExit("DATABASE_URL has no host")

if migration.host is None:
    raise SystemExit("DATABASE_MIGRATION_URL has no host")

if "-pooler." not in runtime.host:
    raise SystemExit(
        "DATABASE_URL should use the Neon pooled hostname"
    )

if "-pooler." in migration.host:
    raise SystemExit(
        "DATABASE_MIGRATION_URL must use the direct Neon hostname"
    )

print("Neon URL configuration is valid.")
print(f"Runtime host: {runtime.host}")
print(f"Migration host: {migration.host}")
PY
}

echo "Applying Alembic migrations to Neon..."

set +e

(
    cd "${release_directory}/server"

    runuser \
        --user "$APP_USER" \
        --preserve-environment \
        -- \
        "${VIRTUAL_ENVIRONMENT}/bin/alembic" \
            -c alembic.ini \
            upgrade head
)

migration_status=$?
set -e

if [[ "$migration_status" -ne 0 ]]; then
    echo "Migration failed. The current release was not changed." >&2

    if [[ -n "$previous_release" ]]; then
        systemctl start \
            timelapse-api.service \
            timelapse-worker.service \
            timelapse-bot.service \
            2>/dev/null || true
    fi

    exit "$migration_status"
fi

ln -sfnT "$release_directory" "$CURRENT_LINK"

echo "Installing systemd units..."

for unit_name in \
    timelapse-migrate.service \
    timelapse-api.service \
    timelapse-worker.service \
    timelapse-bot.service \
    timelapse-camera.target
do
    install \
        -o root \
        -g root \
        -m 0644 \
        "${SCRIPT_DIRECTORY}/systemd/${unit_name}" \
        "${SYSTEMD_DIRECTORY}/${unit_name}"
done

systemctl daemon-reload
systemctl enable timelapse-camera.target

echo "Configuring Nginx site..."

install -d \
    -o root \
    -g root \
    -m 0755 \
    "$ACME_WEBROOT"

export PUBLIC_DOMAIN
export API_PORT

certificate_directory="/etc/letsencrypt/live/${PUBLIC_DOMAIN}"

if [[ ! -f "${certificate_directory}/fullchain.pem" ]]; then
    envsubst '${PUBLIC_DOMAIN} ${API_PORT}' \
        < "${SCRIPT_DIRECTORY}/nginx/timelapse-camera.http.conf.template" \
        > "$NGINX_AVAILABLE"

    ln -sfn "$NGINX_AVAILABLE" "$NGINX_ENABLED"

    nginx -t
    systemctl reload nginx

    certbot certonly \
        --webroot \
        --webroot-path "$ACME_WEBROOT" \
        --domain "$PUBLIC_DOMAIN" \
        --email "$LETSENCRYPT_EMAIL" \
        --agree-tos \
        --non-interactive
fi

envsubst '${PUBLIC_DOMAIN} ${API_PORT}' \
    < "${SCRIPT_DIRECTORY}/nginx/timelapse-camera.conf.template" \
    > "$NGINX_AVAILABLE"

ln -sfn "$NGINX_AVAILABLE" "$NGINX_ENABLED"

install -d \
    -o root \
    -g root \
    -m 0755 \
    /etc/letsencrypt/renewal-hooks/deploy

cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF

chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx

nginx -t
systemctl reload nginx

systemctl enable --now certbot.timer 2>/dev/null || true

echo "Starting migration and application services..."

systemctl reset-failed \
    timelapse-migrate.service \
    timelapse-api.service \
    timelapse-worker.service \
    timelapse-bot.service \
    2>/dev/null || true

systemctl restart timelapse-migrate.service
systemctl restart timelapse-api.service
systemctl restart timelapse-worker.service
systemctl restart timelapse-bot.service
systemctl start timelapse-camera.target

echo "Waiting for local API liveness..."

for attempt in {1..30}; do
    if curl \
        --fail \
        --silent \
        --show-error \
        "http://127.0.0.1:${API_PORT}/health/live" \
        >/dev/null
    then
        break
    fi

    if [[ "$attempt" -eq 30 ]]; then
        echo "API failed to become ready." >&2
        journalctl \
            --unit timelapse-api.service \
            --lines 100 \
            --no-pager
        exit 1
    fi

    sleep 2
done

echo
echo "Deployment completed."
echo "Release: ${release_id}"
echo

curl \
    --fail \
    --silent \
    --show-error \
    "http://127.0.0.1:${API_PORT}/health/live"

echo

curl \
    --fail \
    --silent \
    --show-error \
    "https://${PUBLIC_DOMAIN}/health/live"

echo
