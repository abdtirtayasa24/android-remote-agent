#!/data/data/com.termux/files/usr/bin/sh
set -eu

SCRIPT_DIRECTORY="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIRECTORY="$(CDPATH= cd -- "$SCRIPT_DIRECTORY/.." && pwd)"
TIMELAPSE_HOME="${TIMELAPSE_HOME:-$HOME/timelapse}"

if [ -z "${PREFIX:-}" ] || [ ! -d "$PREFIX" ]; then
    echo "This installer must be run inside Termux." >&2
    exit 1
fi

pkg update
pkg install -y python termux-api

python -m pip install --upgrade pip
python -m pip install -r "$PROJECT_DIRECTORY/requirements.txt"

mkdir -p \
    "$TIMELAPSE_HOME/app" \
    "$TIMELAPSE_HOME/bin" \
    "$TIMELAPSE_HOME/logs" \
    "$TIMELAPSE_HOME/run" \
    "$TIMELAPSE_HOME/validation-captures" \
    "$HOME/.termux/boot"

rm -rf "$TIMELAPSE_HOME/app/camera_agent"
cp -R "$PROJECT_DIRECTORY/src/camera_agent" "$TIMELAPSE_HOME/app/camera_agent"
cp "$PROJECT_DIRECTORY/scripts/start-agent.sh" "$TIMELAPSE_HOME/bin/start-agent.sh"
cp "$PROJECT_DIRECTORY/scripts/camera-self-test.sh" "$TIMELAPSE_HOME/bin/camera-self-test.sh"

chmod 700 "$TIMELAPSE_HOME/bin/"*.sh
chmod 700 "$TIMELAPSE_HOME/logs" "$TIMELAPSE_HOME/run"

if [ ! -f "$TIMELAPSE_HOME/config.json" ]; then
    cp "$PROJECT_DIRECTORY/config.example.json" "$TIMELAPSE_HOME/config.json"
fi
chmod 600 "$TIMELAPSE_HOME/config.json"

cat > "$HOME/.termux/boot/10-start-camera-agent" <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
exec "$HOME/timelapse/bin/start-agent.sh"
EOF
chmod 700 "$HOME/.termux/boot/10-start-camera-agent"

echo
echo "Installed Milestone 1 files under: $TIMELAPSE_HOME"
echo "Next:"
echo "  1. Edit $TIMELAPSE_HOME/config.json"
echo "  2. Run $TIMELAPSE_HOME/bin/camera-self-test.sh info"
echo "  3. Run $TIMELAPSE_HOME/bin/camera-self-test.sh once <camera-id>"
echo "  4. Run $TIMELAPSE_HOME/bin/camera-self-test.sh ten <camera-id>"
