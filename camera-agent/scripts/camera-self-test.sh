#!/data/data/com.termux/files/usr/bin/sh
set -eu

TIMELAPSE_HOME="${TIMELAPSE_HOME:-$HOME/timelapse}"
CONFIG_PATH="$TIMELAPSE_HOME/config.json"
EVIDENCE_DIRECTORY="$TIMELAPSE_HOME/logs/milestone-1"
PID_PATH="$TIMELAPSE_HOME/run/camera-agent.pid"

export PYTHONPATH="$TIMELAPSE_HOME/app${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$EVIDENCE_DIRECTORY"

usage() {
    cat <<'EOF'
Usage:
  camera-self-test.sh info
  camera-self-test.sh once <camera-id>
  camera-self-test.sh ten <camera-id>
  camera-self-test.sh validate [since-utc] [minimum-count]
  camera-self-test.sh thermal [hours]
  camera-self-test.sh status
EOF
}

require_camera_id() {
    case "${1:-}" in
        ''|*[!0-9]*)
            echo "camera-id must be a non-negative integer" >&2
            exit 2
            ;;
    esac
}

command_name="${1:-}"
case "$command_name" in
    info)
        termux-camera-info | tee "$EVIDENCE_DIRECTORY/camera-info.json"
        ;;

    once)
        camera_id="${2:-}"
        require_camera_id "$camera_id"
        python -m camera_agent.main \
            --config "$CONFIG_PATH" \
            --camera-id "$camera_id" \
            --once
        ;;

    ten)
        camera_id="${2:-}"
        require_camera_id "$camera_id"
        started_at_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '%s\n' "$started_at_utc" \
            > "$EVIDENCE_DIRECTORY/ten-capture-started-at-utc.txt"

        python -m camera_agent.main \
            --config "$CONFIG_PATH" \
            --camera-id "$camera_id" \
            --count 10 \
            --fail-fast

        python -m camera_agent.validation \
            --config "$CONFIG_PATH" \
            --since-utc "$started_at_utc" \
            --minimum-count 10 \
            --maximum-gap-seconds 90 \
            | tee "$EVIDENCE_DIRECTORY/ten-capture-report.json"
        ;;

    validate)
        since_utc="${2:-}"
        minimum_count="${3:-1}"
        if [ -n "$since_utc" ]; then
            python -m camera_agent.validation \
                --config "$CONFIG_PATH" \
                --since-utc "$since_utc" \
                --minimum-count "$minimum_count"
        else
            python -m camera_agent.validation \
                --config "$CONFIG_PATH" \
                --minimum-count "$minimum_count"
        fi
        ;;

    thermal)
        hours="${2:-24}"
        case "$hours" in
            ''|*[!0-9]*)
                echo "hours must be a positive integer" >&2
                exit 2
                ;;
        esac
        if [ "$hours" -le 0 ]; then
            echo "hours must be greater than zero" >&2
            exit 2
        fi

        output="$EVIDENCE_DIRECTORY/thermal-$(date -u +%Y%m%dT%H%M%SZ).log"
        samples=$((hours * 12))
        sample=1

        while [ "$sample" -le "$samples" ]; do
            {
                printf 'observed_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
                termux-battery-status
                printf '\n'
            } >> "$output"
            sample=$((sample + 1))
            if [ "$sample" -le "$samples" ]; then
                sleep 300
            fi
        done

        echo "Thermal evidence written to: $output"
        ;;

    status)
        if [ ! -f "$PID_PATH" ]; then
            echo "Agent PID file is missing."
            exit 1
        fi

        agent_pid="$(cat "$PID_PATH")"
        if kill -0 "$agent_pid" 2>/dev/null; then
            echo "Agent wrapper is running with PID $agent_pid."
            tail -n 20 "$TIMELAPSE_HOME/logs/camera-agent.log"
        else
            echo "Agent PID file is stale: $agent_pid" >&2
            exit 1
        fi
        ;;

    *)
        usage
        exit 2
        ;;
esac
