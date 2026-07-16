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
    nginx.service \
    timelapse-migrate.service \
    timelapse-api.service \
    timelapse-worker.service \
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

echo "Checking Neon database connectivity..."

if runuser \
    --user ubuntu \
    --preserve-environment \
    -- \
    /opt/android-remote/.venv/bin/python - <<'PY'
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from timelapse.configuration import get_settings


async def main() -> None:
    settings = get_settings()

    engine = create_async_engine(
        settings.runtime_database_url,
        connect_args=settings.database_connect_args,
        pool_pre_ping=True,
    )

    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT
                        current_user,
                        current_database(),
                        current_setting('server_version')
                    """
                )
            )

            user, database, version = result.one()

            print(f"Database user: {user}")
            print(f"Database: {database}")
            print(f"PostgreSQL version: {version}")
    finally:
        await engine.dispose()


asyncio.run(main())
PY
then
    pass "Neon database connection"
else
    fail "Neon database connection"
fi

if (
    cd /opt/android-remote/current/server

    runuser \
        --user ubuntu \
        --preserve-environment \
        -- \
        /opt/android-remote/.venv/bin/alembic \
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
    timelapse-worker.service
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
