#!/data/data/com.termux/files/usr/bin/sh
set -eu

SCRIPT_DIRECTORY="$(
    CDPATH= cd -- "$(dirname -- "$0")"
    pwd
)"

TIMELAPSE_HOME="${TIMELAPSE_HOME:-$HOME/timelapse}"
INSTALLED_APP_DIRECTORY="$TIMELAPSE_HOME/app"
SOURCE_APP_DIRECTORY="$SCRIPT_DIRECTORY/../src"
CONFIG_PATH="$TIMELAPSE_HOME/config.json"
LOG_DIRECTORY="$TIMELAPSE_HOME/logs"
RUN_DIRECTORY="$TIMELAPSE_HOME/run"
LOG_PATH="$LOG_DIRECTORY/camera-agent.log"
PID_PATH="$RUN_DIRECTORY/camera-agent.pid"

if [ -d "$INSTALLED_APP_DIRECTORY/camera_agent" ]; then
    APP_DIRECTORY="$INSTALLED_APP_DIRECTORY"
elif [ -d "$SOURCE_APP_DIRECTORY/camera_agent" ]; then
    APP_DIRECTORY="$SOURCE_APP_DIRECTORY"
else
    echo "Camera agent package does not exist." >&2
    echo "Expected one of:" >&2
    echo "  $INSTALLED_APP_DIRECTORY/camera_agent" >&2
    echo "  $SOURCE_APP_DIRECTORY/camera_agent" >&2
    echo "Run: cd camera-agent && ./scripts/install-termux.sh" >&2
    exit 1
fi

mkdir -p "$LOG_DIRECTORY" "$RUN_DIRECTORY"
chmod 700 "$LOG_DIRECTORY" "$RUN_DIRECTORY"

if [ -n "${PYTHONPATH:-}" ]; then
    PYTHONPATH="$APP_DIRECTORY:$PYTHONPATH"
else
    PYTHONPATH="$APP_DIRECTORY"
fi

export PYTHONPATH

cleanup() {
    rm -f "$PID_PATH"
    termux-wake-unlock >/dev/null 2>&1 || true
}

trap cleanup EXIT HUP INT TERM

if ! command -v python >/dev/null 2>&1; then
    echo "Python was not found. Run: pkg install python" >&2
    exit 1
fi

if ! command -v termux-wake-lock >/dev/null 2>&1; then
    echo "termux-wake-lock was not found. Run: pkg install termux-api" >&2
    exit 1
fi

if [ ! -f "$CONFIG_PATH" ]; then
    echo "Configuration file does not exist: $CONFIG_PATH" >&2
    exit 1
fi

termux-wake-lock

printf '%s\n' "$$" > "$PID_PATH"

echo "Starting camera agent..."
echo "Configuration: $CONFIG_PATH"
echo "Log file: $LOG_PATH"

python -m camera_agent.main \
    --config "$CONFIG_PATH" \
    "$@" >> "$LOG_PATH" 2>&1
