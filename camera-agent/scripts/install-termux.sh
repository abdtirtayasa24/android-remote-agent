#!/data/data/com.termux/files/usr/bin/sh
set -eu

SCRIPT_DIRECTORY="$(
    CDPATH= cd -- "$(dirname -- "$0")"
    pwd
)"

CAMERA_AGENT_DIRECTORY="$(
    CDPATH= cd -- "${SCRIPT_DIRECTORY}/.."
    pwd
)"

RUNTIME_DIRECTORY="$HOME/timelapse"
CONFIG_FILE="${RUNTIME_DIRECTORY}/config.json"

pkg update -y

pkg install -y \
    python \
    python-pip \
    python-pillow \
    termux-api

python -m pip install \
    --disable-pip-version-check \
    -r "${CAMERA_AGENT_DIRECTORY}/requirements.txt"

mkdir -p \
    "${RUNTIME_DIRECTORY}/pending" \
    "${RUNTIME_DIRECTORY}/tmp" \
    "${RUNTIME_DIRECTORY}/logs"

chmod 700 \
    "$RUNTIME_DIRECTORY" \
    "${RUNTIME_DIRECTORY}/pending" \
    "${RUNTIME_DIRECTORY}/tmp" \
    "${RUNTIME_DIRECTORY}/logs"

if [ ! -f "$CONFIG_FILE" ]; then
    cp \
        "${CAMERA_AGENT_DIRECTORY}/config.example.json" \
        "$CONFIG_FILE"
fi

chmod 600 "$CONFIG_FILE"

python - <<'PY'
import httpx
from PIL import Image

print("httpx:", httpx.__version__)
print("Pillow:", Image.__version__)
PY

printf '\nInstallation completed.\n'
printf 'Edit: %s\n' "$CONFIG_FILE"
printf 'Then run: %s/scripts/start-agent.sh\n' \
    "$CAMERA_AGENT_DIRECTORY"
