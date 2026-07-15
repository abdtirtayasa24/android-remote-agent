#!/usr/bin/env bash
set -Eeuo pipefail

ENVIRONMENT_FILE="/etc/android-remote/server.env"
CREDENTIAL_COMMAND="/opt/android-remote/.venv/bin/timelapse-credentials"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this command as root." >&2
    exit 1
fi

if [[ ! -f "$ENVIRONMENT_FILE" ]]; then
    echo "Missing environment file: $ENVIRONMENT_FILE" >&2
    exit 1
fi

if [[ ! -x "$CREDENTIAL_COMMAND" ]]; then
    echo "Credential command is not installed:" >&2
    echo "  $CREDENTIAL_COMMAND" >&2
    echo "Run deploy-systemd.sh first." >&2
    exit 1
fi

if grep -q $'\r' "$ENVIRONMENT_FILE"; then
    echo "Environment file contains CRLF line endings." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENVIRONMENT_FILE"
set +a

cd /opt/android-remote/current/server

exec runuser \
    --user ubuntu \
    --preserve-environment \
    -- \
    "$CREDENTIAL_COMMAND" \
    "$@"
