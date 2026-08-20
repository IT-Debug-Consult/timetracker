# timetracker

Captures a desktop screenshot every 15 minutes for manual time-tracking review.

- `screenshot.py` — captures one screenshot via the XDG Desktop Portal (GNOME's
  direct Shell D-Bus screenshot API is locked down since GNOME 50) and saves it
  to `~/.local/share/timetracker/screenshots/YYYY-MM-DD/HH-MM-SS.png`. Skips
  capture while the screen is locked or while paused (see below).
- `pause.py` — shared pause-state module (`~/.local/share/timetracker/pause.json`).
  A pause expires either when its duration elapses or at the next boot,
  whichever comes first.
- `tray.py` — a system tray icon (top panel) with a menu to pause capture for
  15/30/60/120 minutes or resume immediately. Requires the "AppIndicator and
  KStatusNotifierItem Support" GNOME Shell extension to be visible. Runs
  unsandboxed (it only needs local D-Bus/pause-state access, no portal calls).
- `flatpak/` — a Flatpak manifest (`at.it_debug.timetracker`) that packages
  `screenshot.py` + `pause.py` so the screenshot portal grant is scoped to this
  app specifically (see "Sandboxing" below).
- `systemd/` — a user-level systemd timer that runs the Flatpak every 15
  minutes, plus the tray icon's autostart `.desktop` entry.
- `install.sh` — installs the GNOME Flatpak runtime/SDK, builds and installs
  the Flatpak, enables the timer, and launches the tray icon.

## Setup

```
./install.sh
```

Requires `flatpak` and `flatpak-builder` (both installed on this system
already). The first run downloads the `org.gnome.Platform`/`org.gnome.Sdk`
runtime (//50) from Flathub if not already present — a few hundred MB.

The very first capture needs a one-time consent dialog. GNOME refuses to show
that dialog for a window-less/headless caller ("only the focused app is
allowed to show a system access dialog"), so `screenshot.py` briefly opens a
focused 1x1 window just for that first request, to give the dialog something
to attach to. Click Allow when it appears. From then on the grant is stored
on disk (`~/.local/share/flatpak/db/screenshot`) and persists across reboots
— every later capture runs fully headless, no window, in well under a second.

## Sandboxing

`screenshot.py` runs as a proper Flatpak app (`at.it_debug.timetracker`)
instead of a plain unsandboxed script. This matters because of how
`xdg-desktop-portal` identifies callers: a plain unsandboxed process has no
identity the portal can distinguish, so *any* unsandboxed program on the
machine ends up sharing one generic `''` grant in the permission store — not
scoped to this script at all. Packaging it as a real Flatpak gives it its own
distinct identity, confined filesystem access (only `xdg-data/timetracker`
and `xdg-pictures`, read-only `/usr`+`/etc`, nothing else), and no network
access (`--unshare-net` is implicit — network isn't in `finish-args`).

A lighter-weight alternative — faking a `/.flatpak-info` file inside a manual
`bubblewrap` sandbox, without a real Flatpak build — was tried first and
rejected: this version of `xdg-desktop-portal` cross-checks a `bwrapinfo.json`
that only the real `flatpak run` launcher produces, specifically to prevent
that kind of identity spoofing.

To inspect or reset grants directly (there's no GNOME Settings page for
this — App Permissions there only covers different, malcontent-tracked data):

```bash
# view current grants
gdbus call --session --dest org.freedesktop.impl.portal.PermissionStore \
  --object-path /org/freedesktop/impl/portal/PermissionStore \
  --method org.freedesktop.impl.portal.PermissionStore.Lookup "screenshot" "screenshot"

# revoke this app's grant (brings back the one-time window dialog on next capture)
gdbus call --session --dest org.freedesktop.impl.portal.PermissionStore \
  --object-path /org/freedesktop/impl/portal/PermissionStore \
  --method org.freedesktop.impl.portal.PermissionStore.DeletePermission "screenshot" "screenshot" "at.it_debug.timetracker"
```

To rebuild after editing `screenshot.py` or `pause.py`, just re-run
`./install.sh` — it rebuilds and reinstalls the Flatpak each time.

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
