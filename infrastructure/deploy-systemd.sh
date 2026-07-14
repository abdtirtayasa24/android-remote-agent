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
    POSTGRES_HOST
    POSTGRES_PORT
    POSTGRES_DB
    POSTGRES_USER
    POSTGRES_PASSWORD
    STORAGE_ROOT
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

if [[ ! "${POSTGRES_PORT}" =~ ^[0-9]+$ ]] \
    || ((POSTGRES_PORT < 1 || POSTGRES_PORT > 65535)); then
    echo "POSTGRES_PORT must be between 1 and 65535." >&2
    exit 2
fi

if [[ "${POSTGRES_HOST}" != "127.0.0.1" \
    && "${POSTGRES_HOST}" != "localhost" ]]; then
    echo "Native deployment requires local PostgreSQL." >&2
    echo "Set POSTGRES_HOST to 127.0.0.1 or localhost." >&2
    exit 2
fi

identifier_pattern='^[a-z_][a-z0-9_]{0,62}$'

if [[ ! "${POSTGRES_USER}" =~ ${identifier_pattern} ]]; then
    echo "POSTGRES_USER is not a supported PostgreSQL identifier." >&2
    exit 2
fi

if [[ ! "${POSTGRES_DB}" =~ ${identifier_pattern} ]]; then
    echo "POSTGRES_DB is not a supported PostgreSQL identifier." >&2
    exit 2
fi

if [[ "${STORAGE_ROOT}" != "/srv/timelapse" ]]; then
    echo "For this deployment, STORAGE_ROOT must be /srv/timelapse." >&2
    exit 2
fi

if [[ "${#POSTGRES_PASSWORD}" -lt 20 ]]; then
    echo "POSTGRES_PASSWORD must contain at least 20 characters." >&2
    exit 2
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
    echo "Missing service user: $APP_USER" >&2
    echo "Run infrastructure/bootstrap-ubuntu.sh first." >&2
    exit 1
fi

if ! systemctl is-active --quiet postgresql; then
    echo "PostgreSQL is not active." >&2
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

echo "Creating or updating the PostgreSQL role and database..."

runuser -u postgres -- env \
    TIMELAPSE_DB_USER="$POSTGRES_USER" \
    TIMELAPSE_DB_PASSWORD="$POSTGRES_PASSWORD" \
    TIMELAPSE_DB_NAME="$POSTGRES_DB" \
    psql \
        --set ON_ERROR_STOP=1 \
        --dbname postgres <<'SQL'
\getenv db_user TIMELAPSE_DB_USER
\getenv db_password TIMELAPSE_DB_PASSWORD
\getenv db_name TIMELAPSE_DB_NAME

SELECT format(
    'CREATE ROLE %I LOGIN',
    :'db_user'
)
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_roles
    WHERE rolname = :'db_user'
)
\gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN PASSWORD %L',
    :'db_user',
    :'db_password'
)
\gexec

SELECT format(
    'CREATE DATABASE %I OWNER %I',
    :'db_name',
    :'db_user'
)
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_database
    WHERE datname = :'db_name'
)
\gexec

SELECT format(
    'ALTER DATABASE %I OWNER TO %I',
    :'db_name',
    :'db_user'
)
\gexec
SQL

repair_database_ownership() {
    echo "Ensuring application role owns existing database objects..."

    runuser -u postgres -- \
        psql \
            --set ON_ERROR_STOP=1 \
            --set app_user="$POSTGRES_USER" \
            --dbname "$POSTGRES_DB" <<'SQL'
SELECT format(
    'ALTER DATABASE %I OWNER TO %I',
    current_database(),
    :'app_user'
)
\gexec

GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";

SELECT format(
    'ALTER TABLE %I.%I OWNER TO %I',
    schemaname,
    tablename,
    :'app_user'
)
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename
\gexec

SELECT format(
    'ALTER SEQUENCE %I.%I OWNER TO %I',
    schemaname,
    sequencename,
    :'app_user'
)
FROM pg_sequences
WHERE schemaname = 'public'
ORDER BY sequencename
\gexec

SELECT format(
    'ALTER TYPE %I.%I OWNER TO %I',
    namespace.nspname,
    type_definition.typname,
    :'app_user'
)
FROM pg_type AS type_definition
JOIN pg_namespace AS namespace
    ON namespace.oid = type_definition.typnamespace
WHERE namespace.nspname = 'public'
  AND type_definition.typtype IN ('e', 'd')
ORDER BY type_definition.typname
\gexec
SQL
}

repair_database_ownership

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

echo "Applying Alembic migrations from the new release..."

set +e

(
    cd "${release_directory}/server"

    runuser -u "$APP_USER" -- env \
        ENVIRONMENT="${ENVIRONMENT:-production}" \
        LOG_LEVEL="${LOG_LEVEL:-INFO}" \
        POSTGRES_HOST="$POSTGRES_HOST" \
        POSTGRES_PORT="$POSTGRES_PORT" \
        POSTGRES_DB="$POSTGRES_DB" \
        POSTGRES_USER="$POSTGRES_USER" \
        POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
        STORAGE_ROOT="$STORAGE_ROOT" \
        PYTHONPATH="${release_directory}/server/src" \
        PYTHONDONTWRITEBYTECODE=1 \
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
