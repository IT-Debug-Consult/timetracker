#!/usr/bin/env python3
"""Capture a single desktop screenshot via the XDG Desktop Portal.

GNOME's direct Shell D-Bus screenshot interface is locked down as of
GNOME 50, so this goes through org.freedesktop.portal.Screenshot instead.
The portal may show a one-time permission dialog per login session.
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


def request_screenshot_uri(bus: Gio.DBusConnection) -> str:
    request_path = bus.call_sync(
        "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
        "org.freedesktop.portal.Screenshot", "Screenshot",
        GLib.Variant("(sa{sv})", ("", {"interactive": GLib.Variant("b", False)})),
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

    if timed_out["flag"]:
        sys.exit("screenshot request timed out waiting for portal response")
    if response.get("code") != 0:
        sys.exit(f"screenshot request denied or failed (code={response.get('code')})")

    uri = response["results"].get("uri")
    if not uri:
        sys.exit("portal response had no screenshot uri")
    return uri


def main() -> None:
    if pause.is_paused():
        return

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    if is_screen_locked(bus):
        return

    uri = request_screenshot_uri(bus)
    source = Path(unquote(urlparse(uri).path))

    now = datetime.now()
    destination = OUTPUT_ROOT / now.strftime("%Y-%m-%d") / f"{now.strftime('%H-%M-%S')}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    print(destination)


if __name__ == "__main__":
    main()
