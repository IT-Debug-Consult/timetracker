# timetracker

Captures a desktop screenshot every 5 minutes for manual time-tracking review.

- `screenshot.py` — captures one screenshot via the XDG Desktop Portal (GNOME's
  direct Shell D-Bus screenshot API is locked down since GNOME 50) and saves it
  to `~/.local/share/timetracker/screenshots/YYYY-MM-DD/HH-MM-SS.png`. Skips
  capture while the screen is locked or while paused (see below).
- `pause.py` — shared pause-state module (`~/.local/share/timetracker/pause.json`).
  A pause expires either when its duration elapses or at the next boot,
  whichever comes first.
- `tray.py` — a system tray icon (top panel) with a menu to pause capture for
  15/30/60/120 minutes or resume immediately. Requires the "AppIndicator and
  KStatusNotifierItem Support" GNOME Shell extension to be visible.
- `systemd/` — a user-level systemd timer that runs `screenshot.py` every 5
  minutes, plus the tray icon's autostart `.desktop` entry.
- `install.sh` — installs/enables the timer, installs the tray autostart entry,
  and launches the tray icon immediately.

## Setup

```
./install.sh
```

The portal shows a one-time permission dialog on first capture each login
session — click Allow. Subsequent captures that session run silently.

## Usage

```
systemctl --user status timetracker-screenshot.timer   # check schedule
journalctl --user -u timetracker-screenshot.service     # view logs
systemctl --user stop timetracker-screenshot.timer      # stop entirely
```

To pause temporarily, use the tray icon menu instead of stopping the timer —
it resumes itself automatically after the chosen duration or at next boot.

Screenshots accumulate under `~/.local/share/timetracker/screenshots/` with no
automatic cleanup — delete old dated folders manually as needed.


