#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIRECTORY="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"

ENVIRONMENT_FILE="${ENVIRONMENT_FILE:-${SCRIPT_DIRECTORY}/.env}"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this script as root." >&2
    exit 1
fi

if [[ ! -f "$ENVIRONMENT_FILE" ]]; then
    echo "Missing environment file: $ENVIRONMENT_FILE" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENVIRONMENT_FILE"
set +a

failed=0

pass() {
    printf 'PASS: %s\n' "$1"
}

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    failed=1
}

for service_name in \
    postgresql.service \
    nginx.service \
    timelapse-migrate.service \
    timelapse-api.service \
    timelapse-worker.service \
    timelapse-bot.service \
    timelapse-camera.target
do
    if systemctl is-active --quiet "$service_name"; then
        pass "$service_name is active"
    else
        fail "$service_name is active"
    fi
done

if curl \
    --fail \
    --silent \
    --show-error \
    "http://127.0.0.1:${API_PORT}/health/live" \
    | grep -q '"status":"ok"'
then
    pass "local API liveness"
else
    fail "local API liveness"
fi

if curl \
    --fail \
    --silent \
    --show-error \
    "https://${PUBLIC_DOMAIN}/health/live" \
    | grep -q '"status":"ok"'
then
    pass "public HTTPS liveness"
else
    fail "public HTTPS liveness"
fi

api_listeners="$(
    ss -lntH \
        | awk -v suffix=":${API_PORT}" '$4 ~ suffix "$" { print $4 }'
)"

if grep -qx "127.0.0.1:${API_PORT}" <<< "$api_listeners"; then
    pass "API is listening on IPv4 loopback"
else
    fail "API is not listening exactly on 127.0.0.1:${API_PORT}"
fi

if grep -Eq '(^0\.0\.0\.0:|\[::\]:)' <<< "$api_listeners"; then
    fail "API has a wildcard listener"
else
    pass "API has no wildcard listener"
fi

postgres_listeners="$(
    ss -lntH \
        | awk '$4 ~ /:5432$/ { print $4 }'
)"

if [[ -n "$postgres_listeners" ]]; then
    pass "PostgreSQL has a local TCP listener"
else
    fail "PostgreSQL has no TCP listener"
fi

if grep -Eq '(^0\.0\.0\.0:5432$|\[::\]:5432$)' \
    <<< "$postgres_listeners"
then
    fail "PostgreSQL has a public wildcard listener"
else
    pass "PostgreSQL has no wildcard listener"
fi

if PGPASSWORD="$POSTGRES_PASSWORD" \
    psql \
        --host "$POSTGRES_HOST" \
        --port "$POSTGRES_PORT" \
        --username "$POSTGRES_USER" \
        --dbname "$POSTGRES_DB" \
        --no-align \
        --tuples-only \
        --command "SELECT 1;" \
    | grep -qx '1'
then
    pass "application database authentication"
else
    fail "application database authentication"
fi

table_count="$(
    PGPASSWORD="$POSTGRES_PASSWORD" \
        psql \
            --host "$POSTGRES_HOST" \
            --port "$POSTGRES_PORT" \
            --username "$POSTGRES_USER" \
            --dbname "$POSTGRES_DB" \
            --no-align \
            --tuples-only \
            --command "
                SELECT count(*)
                FROM information_schema.tables
                WHERE table_schema = 'public';
            "
)"

if [[ "$table_count" =~ ^[1-9][0-9]*$ ]]; then
    pass "database schema contains ${table_count} tables"
else
    fail "database schema is empty"
fi

if (
    cd /home/ubuntu/android-remote/server

    runuser -u ubuntu -- env \
        POSTGRES_HOST="$POSTGRES_HOST" \
        POSTGRES_PORT="$POSTGRES_PORT" \
        POSTGRES_DB="$POSTGRES_DB" \
        POSTGRES_USER="$POSTGRES_USER" \
        POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
        PYTHONPATH=/home/ubuntu/android-remote/current/server/src \
        /home/ubuntu/android-remote/.venv/bin/alembic \
            -c alembic.ini \
            current
) | grep -q '20260714_0001'
then
    pass "Alembic revision is current"
else
    fail "Alembic revision is not current"
fi

if nginx -t >/dev/null 2>&1; then
    pass "Nginx configuration"
else
    fail "Nginx configuration"
fi

if ufw status | grep -q "Status: active"; then
    pass "UFW is active"
else
    fail "UFW is active"
fi

if [[ -L /opt/android-remote/current ]]; then
    pass "current release symlink exists"
else
    fail "current release symlink exists"
fi

if [[ "$(stat -c '%a' /etc/android-remote/server.env)" == "600" ]]; then
    pass "server environment file mode is 0600"
else
    fail "server environment file mode is not 0600"
fi

echo
echo "Service memory:"
for service_name in \
    timelapse-api.service \
    timelapse-worker.service \
    timelapse-bot.service
do
    systemctl show \
        "$service_name" \
        --property Id \
        --property MemoryCurrent \
        --property MemoryPeak
done

echo
echo "Listeners:"
ss -lntp | grep -E ":(${API_PORT}|5432|80|443)\b" || true

echo
echo "UFW:"
ufw status numbered

if [[ "$failed" -ne 0 ]]; then
    exit 1
fi
