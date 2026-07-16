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
    "${RUNTIME_DIRECTORY}/app" \
    "${RUNTIME_DIRECTORY}/bin" \
    "${RUNTIME_DIRECTORY}/pending" \
    "${RUNTIME_DIRECTORY}/tmp" \
    "${RUNTIME_DIRECTORY}/logs" \
    "${RUNTIME_DIRECTORY}/run" \
    "${RUNTIME_DIRECTORY}/validation-captures"

rm -rf "${RUNTIME_DIRECTORY}/app/camera_agent"
cp -R \
    "${CAMERA_AGENT_DIRECTORY}/src/camera_agent" \
    "${RUNTIME_DIRECTORY}/app/camera_agent"

cp \
    "${CAMERA_AGENT_DIRECTORY}/scripts/camera-self-test.sh" \
    "${CAMERA_AGENT_DIRECTORY}/scripts/start-agent.sh" \
    "${RUNTIME_DIRECTORY}/bin/"

chmod 700 \
    "$RUNTIME_DIRECTORY" \
    "${RUNTIME_DIRECTORY}/app" \
    "${RUNTIME_DIRECTORY}/bin" \
    "${RUNTIME_DIRECTORY}/pending" \
    "${RUNTIME_DIRECTORY}/tmp" \
    "${RUNTIME_DIRECTORY}/logs" \
    "${RUNTIME_DIRECTORY}/run" \
    "${RUNTIME_DIRECTORY}/validation-captures" \
    "${RUNTIME_DIRECTORY}/bin/camera-self-test.sh" \
    "${RUNTIME_DIRECTORY}/bin/start-agent.sh"

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
printf 'Self-test: %s/bin/camera-self-test.sh info\n' "$RUNTIME_DIRECTORY"
printf 'Start agent: %s/bin/start-agent.sh\n' "$RUNTIME_DIRECTORY"
