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

BOOT_DIRECTORY="$HOME/.termux/boot"
BOOT_SCRIPT="${BOOT_DIRECTORY}/10-start-camera-agent"

mkdir -p "$BOOT_DIRECTORY"

cat > "$BOOT_SCRIPT" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
set -eu

termux-wake-lock

mkdir -p "\$HOME/timelapse/logs"

exec "${CAMERA_AGENT_DIRECTORY}/scripts/start-agent.sh" \
    >> "\$HOME/timelapse/logs/camera-agent.log" \
    2>&1
EOF

chmod 700 "$BOOT_SCRIPT"

printf 'Installed Termux:Boot script:\n'
printf '  %s\n' "$BOOT_SCRIPT"
printf '\nOpen the Termux:Boot Android app once.\n'
printf 'Exclude Termux and its add-ons from battery optimization.\n'
