#!/usr/bin/env python3
"""Capture a single desktop screenshot via the XDG Desktop Portal.

GNOME's direct Shell D-Bus screenshot interface is locked down as of
GNOME 50, so this goes through org.freedesktop.portal.Screenshot instead.
Runs sandboxed (see ../flatpak/) so the portal grants consent to this app
specifically rather than to every unsandboxed process on the system.

The very first capture (or after a permission reset) needs a one-time
consent grant. GNOME refuses to show that consent dialog for a headless
caller with no window ("only the focused app is allowed to show a system
access dialog"), so on failure we briefly open a focused 1x1 window, export
its handle, and retry with that as the dialog's parent. Once the grant
exists, all later captures succeed headlessly with no window at all.
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

import pause

OUTPUT_ROOT = Path.home() / ".local" / "share" / "timetracker" / "screenshots"
RESPONSE_TIMEOUT_SECONDS = 30
WINDOW_HANDLE_TIMEOUT_SECONDS = 10


def is_screen_locked(bus: Gio.DBusConnection) -> bool:
    try:
        result = bus.call_sync(
            "org.gnome.ScreenSaver", "/org/gnome/ScreenSaver",
            "org.gnome.ScreenSaver", "GetActive", None,
            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 2000, None,
        )
        return result.unpack()[0]
    except GLib.Error:
        return False


def request_screenshot_uri(bus: Gio.DBusConnection, parent_window: str = "") -> str | None:
    request_path = bus.call_sync(
        "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
        "org.freedesktop.portal.Screenshot", "Screenshot",
        GLib.Variant("(sa{sv})", (parent_window, {"interactive": GLib.Variant("b", False)})),
        GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 5000, None,
    ).unpack()[0]

    loop = GLib.MainLoop()
    response = {}
    timed_out = {"flag": False}

    def on_response(connection, sender_name, path, iface, signal, params):
        response["code"], response["results"] = params.unpack()
        loop.quit()

    def on_timeout():
        timed_out["flag"] = True
        loop.quit()
        return False

    sub_id = bus.signal_subscribe(
        "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
        "Response", request_path, None, Gio.DBusSignalFlags.NONE, on_response,
    )
    timeout_id = GLib.timeout_add_seconds(RESPONSE_TIMEOUT_SECONDS, on_timeout)

    loop.run()

    bus.signal_unsubscribe(sub_id)
    if not timed_out["flag"]:
        GLib.source_remove(timeout_id)

    if timed_out["flag"] or response.get("code") != 0:
        return None

    return response["results"].get("uri")


def get_window_handle() -> str | None:
    """Open a brief, focused window and export its handle for the portal."""
    gi.require_version("Gtk", "4.0")
    gi.require_version("GdkWayland", "4.0")
    from gi.repository import Gtk  # noqa: PLC0415
    from gi.repository import GdkWayland  # noqa: F401,PLC0415  (registers the wayland toplevel type)

    Gtk.init()
    window = Gtk.Window(title="Timetracker")
    window.set_default_size(1, 1)
    window.present()

    loop = GLib.MainLoop()
    handle_box = {}

    def on_handle(_toplevel, handle):
        handle_box["handle"] = handle
        loop.quit()

    def try_export():
        surface = window.get_surface()
        if surface is None:
            return True
        if not surface.export_handle(on_handle):
            loop.quit()
        return False

    GLib.idle_add(try_export)
    GLib.timeout_add_seconds(WINDOW_HANDLE_TIMEOUT_SECONDS, loop.quit)

    loop.run()
    window.destroy()

    handle = handle_box.get("handle")
    return f"wayland:{handle}" if handle else None


def main() -> None:
    if pause.is_paused():
        return

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    if is_screen_locked(bus):
        return

    uri = request_screenshot_uri(bus)
    if uri is None:
        parent_window = get_window_handle()
        if parent_window is None:
            sys.exit("could not obtain a window handle for first-time screenshot consent")
        uri = request_screenshot_uri(bus, parent_window)
        if uri is None:
            sys.exit("screenshot request denied even with a focused window")

    source = Path(unquote(urlparse(uri).path))

    now = datetime.now()
    destination = OUTPUT_ROOT / now.strftime("%Y-%m-%d") / f"{now.strftime('%H-%M-%S')}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    print(destination)


if __name__ == "__main__":
    main()
