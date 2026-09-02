#!/usr/bin/env python3
"""React to session events the timer-driven capture (screenshot.py) can't see.

The timer goes blind in two situations, each of which then shows up during
review as an unexplained gap that looks the same as idle time:

  * suspend  -- the timer simply doesn't fire while the machine is asleep.
  * screen lock -- screenshot.py deliberately skips capture while locked.

This daemon fills both gaps with an explicit 640x480 marker frame ("suspend"
or "screen locked"), saved alongside the real screenshots so the gap reads
as what it is. On unlock it also kicks off one real capture straight away,
instead of leaving up to 7.5 min of un-sampled desk time before the next tick.

  * Suspend is detected via logind's PrepareForSleep signal (system bus). We
    hold a "delay" sleep inhibitor so the frame lands on disk before the
    machine goes down, then re-take it on resume.
  * Lock/unlock is detected via org.gnome.ScreenSaver's ActiveChanged signal
    (session bus). No inhibitor needed -- locking doesn't freeze processes.

A suspend usually locks the screen first, so a lock+suspend pair produces two
frames a moment apart; that's accurate, not a bug.

Runs unsandboxed (like tray.py): it only writes a local file, starts a systemd
unit, and talks to org.freedesktop.login1 / org.gnome.ScreenSaver -- no portal
calls.
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import cairo
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

import pause

OUTPUT_ROOT = Path.home() / ".local" / "share" / "timetracker" / "screenshots"
WIDTH, HEIGHT = 640, 480
INHIBIT_ARGS = ("sleep", "timetracker", "record a suspend marker screenshot", "delay")
CAPTURE_UNIT = "timetracker-screenshot.service"
# Give the desktop a moment to repaint after unlock before we capture it.
UNLOCK_CAPTURE_DELAY_SECONDS = 2


def write_marker(label: str) -> None:
    now = datetime.now()

    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, WIDTH, HEIGHT)
    ctx = cairo.Context(surface)

    ctx.set_source_rgb(0.11, 0.12, 0.15)
    ctx.paint()

    ctx.set_source_rgb(0.90, 0.90, 0.92)
    ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(84)
    ext = ctx.text_extents(label)
    ctx.move_to((WIDTH - ext.width) / 2 - ext.x_bearing,
                (HEIGHT - ext.height) / 2 - ext.y_bearing)
    ctx.show_text(label)

    ctx.set_source_rgb(0.55, 0.55, 0.60)
    ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(22)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    ext = ctx.text_extents(stamp)
    ctx.move_to((WIDTH - ext.width) / 2 - ext.x_bearing, HEIGHT - 44)
    ctx.show_text(stamp)

    destination = OUTPUT_ROOT / now.strftime("%Y-%m-%d") / f"{now.strftime('%H-%M-%S')}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(destination))
    print(f"{label!r} -> {destination}", flush=True)


def try_write_marker(label: str) -> None:
    try:
        if not pause.is_paused():
            write_marker(label)
    except Exception as error:  # noqa: BLE001  (a marker failure must never block suspend/lock)
        print(f"could not write {label!r} marker: {error}", file=sys.stderr, flush=True)


def trigger_capture() -> bool:
    """Run one normal screenshot capture now. screenshot.py still does its own
    pause/lock checks, and starting the unit also resets the 7.5-min timer so
    this doesn't cause a redundant capture right after. Returns False so it can
    double as a one-shot GLib timeout callback."""
    try:
        subprocess.run(
            ["systemctl", "--user", "--no-block", "start", CAPTURE_UNIT], check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print(f"could not trigger unlock capture: {error}", file=sys.stderr, flush=True)
    return False


class MarkerDaemon:
    def __init__(self):
        self.system_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.session_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.inhibitor_fd: int | None = None

        self.take_inhibitor()
        self.system_bus.signal_subscribe(
            "org.freedesktop.login1", "org.freedesktop.login1.Manager",
            "PrepareForSleep", "/org/freedesktop/login1", None,
            Gio.DBusSignalFlags.NONE, self.on_prepare_for_sleep,
        )
        self.session_bus.signal_subscribe(
            "org.gnome.ScreenSaver", "org.gnome.ScreenSaver",
            "ActiveChanged", "/org/gnome/ScreenSaver", None,
            Gio.DBusSignalFlags.NONE, self.on_screensaver_active_changed,
        )

    def take_inhibitor(self) -> None:
        if self.inhibitor_fd is not None:
            return
        try:
            result, fd_list = self.system_bus.call_with_unix_fd_list_sync(
                "org.freedesktop.login1", "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager", "Inhibit",
                GLib.Variant("(ssss)", INHIBIT_ARGS),
                GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None,
            )
            self.inhibitor_fd = fd_list.get(result.unpack()[0])
        except GLib.Error as error:
            print(f"could not take sleep inhibitor: {error}", file=sys.stderr, flush=True)

    def release_inhibitor(self) -> None:
        if self.inhibitor_fd is not None:
            os.close(self.inhibitor_fd)
            self.inhibitor_fd = None

    def on_prepare_for_sleep(self, _connection, _sender, _path, _iface, _signal, params) -> None:
        going_to_sleep = params.unpack()[0]
        if not going_to_sleep:
            self.take_inhibitor()
            return
        try:
            try_write_marker("suspend")
        finally:
            self.release_inhibitor()

    def on_screensaver_active_changed(self, _connection, _sender, _path, _iface, _signal, params) -> None:
        if params.unpack()[0]:  # True -> screen just locked
            try_write_marker("screen locked")
        else:  # False -> screen just unlocked
            GLib.timeout_add_seconds(UNLOCK_CAPTURE_DELAY_SECONDS, trigger_capture)


def main() -> None:
    daemon = MarkerDaemon()  # noqa: F841  (kept alive for the lifetime of the loop)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
