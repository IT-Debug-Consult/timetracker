#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$REPO_DIR/screenshot.py"
TRAY_PATH="$REPO_DIR/tray.py"
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"

chmod +x "$SCRIPT_PATH" "$TRAY_PATH"
mkdir -p "$UNIT_DIR" "$AUTOSTART_DIR"

sed "s|__SCRIPT_PATH__|$SCRIPT_PATH|" "$REPO_DIR/systemd/timetracker-screenshot.service" \
    > "$UNIT_DIR/timetracker-screenshot.service"
cp "$REPO_DIR/systemd/timetracker-screenshot.timer" "$UNIT_DIR/timetracker-screenshot.timer"

sed "s|__SCRIPT_PATH__|$TRAY_PATH|" "$REPO_DIR/systemd/timetracker-tray.desktop" \
    > "$AUTOSTART_DIR/timetracker-tray.desktop"

systemctl --user daemon-reload
systemctl --user enable --now timetracker-screenshot.timer

# Start the tray icon now too, so it's usable without a re-login.
if ! pgrep -f "python3 $TRAY_PATH" > /dev/null; then
    nohup python3 "$TRAY_PATH" > /dev/null 2>&1 &
    disown
fi

echo "Installed. Screenshots every 5 minutes -> ~/.local/share/timetracker/screenshots/"
echo "Tray icon installed for autostart and launched now."
echo "Check status:  systemctl --user status timetracker-screenshot.timer"
echo "View logs:     journalctl --user -u timetracker-screenshot.service"
