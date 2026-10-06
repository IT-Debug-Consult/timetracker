# timetracker

Captures a desktop screenshot every 7.5 minutes for manual time-tracking review.

- `screenshot.py` — captures one screenshot via the XDG Desktop Portal (GNOME's
  direct Shell D-Bus screenshot API is locked down since GNOME 50) and saves it
  to `~/.local/share/timetracker/screenshots/YYYY-MM-DD/HH-MM-SS.png`. Skips
  capture while the screen is locked or while paused (see below). Two capture
  methods, selected with `--method`:
  - `screenshot` (default) — the Screenshot portal, which on GNOME always
    flashes the screen and plays a shutter sound.
  - `screencast` — silent. Opens a ScreenCast portal session, pulls one frame
    per monitor off the PipeWire stream, stitches them together and closes the
    session again. The only visible trace is GNOME's screen-sharing indicator
    in the top bar for a moment.
- `pause.py` — shared pause-state module (`~/.local/share/timetracker/pause.json`).
  A pause expires either when its duration elapses or at the next boot,
  whichever comes first.
- `markers.py` — a small user daemon that reacts to session events the timer
  can't capture. It writes synthetic 640x480 frames into the same screenshot
  folder for a `suspend` (from logind's `PrepareForSleep` on the system bus,
  guarded by a `delay` sleep inhibitor so it lands on disk before the machine
  goes down) and a `screen locked` (from `org.gnome.ScreenSaver`'s
  `ActiveChanged` on the session bus) — without these a suspend or lock is just
  an unexplained gap. On **unlock** it instead triggers one real capture right
  away (`systemctl --user start timetracker-screenshot.service`) so returning
  to the desk is sampled immediately rather than up to 7.5 min later. Markers
  are skipped while paused; a suspend usually locks first, so expect a
  lock+suspend pair a moment apart. Runs unsandboxed (local file write +
  `systemctl` + `login1` / `ScreenSaver` D-Bus only).
- `tray.py` — a system tray icon (top panel) with a menu to pause capture for
  15/30/60/120 minutes or resume immediately. Requires the "AppIndicator and
  KStatusNotifierItem Support" GNOME Shell extension to be visible. Runs
  unsandboxed (it only needs local D-Bus/pause-state access, no portal calls).
- `flatpak/` — a Flatpak manifest (`at.it_debug.timetracker`) that packages
  `screenshot.py` + `pause.py` so the portal grants are scoped to this
  app specifically (see "Sandboxing" below).
- `systemd/` — a user-level systemd timer that runs the Flatpak every 7.5
  minutes, a `timetracker-markers.service` unit that keeps `markers.py`
  running, plus the tray icon's autostart `.desktop` entry.
- `install.sh` — installs the GNOME Flatpak runtime/SDK, builds and installs
  the Flatpak, enables the timer and the marker service, and launches the
  tray icon.

## Setup

```
./install.sh
```

Requires `flatpak` and `flatpak-builder` (both installed on this system
already). The first run downloads the `org.gnome.Platform`/`org.gnome.Sdk`
runtime (//50) from Flathub if not already present — a few hundred MB.

The very first capture needs a one-time consent, separately for each method.

With `screencast` it is a screen-sharing dialog: select all monitors and click
Share. The portal answers with a restore token, which `screenshot.py` keeps in
`~/.local/share/timetracker/screencast-token` and renews on every capture
(tokens are single-use), so the grant persists across reboots — every later
capture runs fully headless, no dialog, in about a second.

Note that the `screencast` grant covers exactly the monitors selected in the
dialog, identified by vendor/model/serial (not by connector) — it is not a
grant for "all screens". When the monitor setup changes
(xdg-desktop-portal-gnome 50):

- All granted monitors present plus a new one: no prompt, and the new monitor
  is silently missing from the captures.
- Any granted monitor missing (undocked, monitor swapped): the grant is not
  used and the dialog comes back on every capture until it is answered. If it
  is ignored, that capture fails after about two minutes.
- Same monitors on different ports: no prompt, nothing changes.

Answering the dialog replaces the grant. So after approving only the laptop
panel while undocked, captures stay laptop-only once docked again, without
any warning — reset the grant (see "Sandboxing" below) and approve all
monitors to fix that. The `screenshot` method has none of this; it always
captures whatever is connected.

With `screenshot` GNOME refuses to show the consent dialog for a
window-less/headless caller ("only the focused app is allowed to show a system
access dialog"), so `screenshot.py` briefly opens a focused 1x1 window just
for that first request, to give the dialog something to attach to. Click Allow
when it appears. From then on the grant is stored on disk
(`~/.local/share/flatpak/db/screenshot`) and persists across reboots.

To switch the timer to the other method, append the switch to `ExecStart` in
`systemd/timetracker-screenshot.service` and re-run `./install.sh`:

```
ExecStart=/usr/bin/flatpak run at.it_debug.timetracker --method screencast
```

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

To reset the `screencast` grant (brings back the one-time dialog on the next
capture, e.g. after changing the monitor setup), delete the restore token:

```bash
rm ~/.local/share/timetracker/screencast-token
```

To inspect or reset the `screenshot` grant directly (there's no GNOME Settings
page for this — App Permissions there only covers different,
malcontent-tracked data):

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
journalctl --user -u timetracker-markers.service         # suspend / lock marker logs
```

To pause temporarily, use the tray icon menu instead of stopping the timer —
it resumes itself automatically after the chosen duration or at next boot.

Screenshots accumulate under `~/.local/share/timetracker/screenshots/` with no
automatic cleanup — delete old dated folders manually as needed.
