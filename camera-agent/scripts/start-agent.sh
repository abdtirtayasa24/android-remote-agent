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

CONFIG_FILE="${
    CAMERA_AGENT_CONFIG:-
    $HOME/timelapse/config.json
}"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export PYTHONPATH="${CAMERA_AGENT_DIRECTORY}/src"

exec python \
    -m camera_agent.main \
    --config "$CONFIG_FILE"
