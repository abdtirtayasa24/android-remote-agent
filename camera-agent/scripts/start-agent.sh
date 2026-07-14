#!/data/data/com.termux/files/usr/bin/sh
set -eu

TIMELAPSE_HOME="${TIMELAPSE_HOME:-$HOME/timelapse}"
APP_DIRECTORY="$TIMELAPSE_HOME/app"
CONFIG_PATH="$TIMELAPSE_HOME/config.json"
LOG_DIRECTORY="$TIMELAPSE_HOME/logs"
RUN_DIRECTORY="$TIMELAPSE_HOME/run"
LOG_PATH="$TIMELAPSE_HOME/camera-agent.log"
PID_PATH="$TIMELAPSE_HOME/camera-agent.pid"

mkdir -p "$LOG_DIRECTORY" "$RUN_DIRECTORY"
chmod 700 "$LOG_DIRECTORY" "$RUN_DIRECTORY"

export PYTHONPATH="$APP_DIRECTORY${PYTHONPATH:+:$PYTHONPATH}"

cleanup() {
    rm -rf "$PID_PATH"
    termux-wake-unlock >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

termux-wake-lock
printf '%s\n' "$$" > "$PID_PATH"

python -m camera_agent.main --config "$CONFIG_PATH" "$@" >> "$LOG_PATH" 2>&1
