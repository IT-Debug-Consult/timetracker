#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRAY_PATH="$REPO_DIR/tray.py"
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
APP_ID="at.it_debug.timetracker"

chmod +x "$TRAY_PATH"
mkdir -p "$UNIT_DIR" "$AUTOSTART_DIR"

# --- Flatpak sandbox for the screenshot capturer ---
# Runs screenshot.py with its own portal identity, instead of the shared
# unsandboxed '' identity every unconfined process on the system shares.
# See flatpak/at.it_debug.timetracker.json and README.md for why.
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub org.gnome.Platform//50 org.gnome.Sdk//50
flatpak-builder --user --install --force-clean \
    "$REPO_DIR/flatpak/build-dir" "$REPO_DIR/flatpak/$APP_ID.json"

cp "$REPO_DIR/systemd/timetracker-screenshot.service" "$UNIT_DIR/timetracker-screenshot.service"
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

echo "Installed. Screenshots every 15 minutes -> ~/.local/share/timetracker/screenshots/"
echo "Tray icon installed for autostart and launched now."
echo "Check status:  systemctl --user status timetracker-screenshot.timer"
echo "View logs:     journalctl --user -u timetracker-screenshot.service"
