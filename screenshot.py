#!/usr/bin/env python3
"""Capture a single desktop screenshot via the XDG Desktop Portal.

GNOME's direct Shell D-Bus screenshot interface is locked down as of
GNOME 50, so by default (--method screenshot) this goes through
org.freedesktop.portal.Screenshot instead, which always flashes the screen
and plays a shutter sound. --method screencast avoids that: it opens a
short-lived org.freedesktop.portal.ScreenCast session, pulls one frame per
monitor off the PipeWire stream, and closes it again -- silent, apart from
GNOME's screen-sharing indicator showing for a moment.
Runs sandboxed (see ../flatpak/) so the portal grants consent to this app
specifically rather than to every unsandboxed process on the system.

The very first capture (or after a permission reset) needs a one-time
consent grant, separately for each method. For screencast it comes back as
a restore token, kept in TOKEN_FILE and renewed on every capture; for
screenshot the portal stores it itself. GNOME refuses to show the
screenshot consent dialog for a headless caller with no window ("only the
focused app is allowed to show a system access dialog"), so when a request
fails we briefly open a focused 1x1 window, export its handle, and retry
with that as the dialog's parent. Once the grant exists, all later captures
succeed headlessly with no window at all.
"""

import argparse
import itertools
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gst

import pause

OUTPUT_ROOT = Path.home() / ".local" / "share" / "timetracker" / "screenshots"
TOKEN_FILE = OUTPUT_ROOT.parent / "screencast-token"
RESPONSE_TIMEOUT_SECONDS = 30
CONSENT_TIMEOUT_SECONDS = 120
FRAME_TIMEOUT_SECONDS = 10
WINDOW_HANDLE_TIMEOUT_SECONDS = 10

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
SOURCE_TYPE_MONITOR = 1
CURSOR_MODE_HIDDEN = 1
PERSIST_UNTIL_REVOKED = 2

_request_tokens = itertools.count()


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


def portal_request(
    bus: Gio.DBusConnection, iface: str, method: str, signature: str, args: tuple,
    options: dict, timeout_seconds: int = RESPONSE_TIMEOUT_SECONDS,
) -> dict | None:
    """Call a portal method that answers via a Request object; return its results."""
    token = f"timetracker{next(_request_tokens)}"
    sender = bus.get_unique_name()[1:].replace(".", "_")
    request_path = f"{PORTAL_PATH}/request/{sender}/{token}"
    options = {**options, "handle_token": GLib.Variant("s", token)}

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
        PORTAL_BUS, "org.freedesktop.portal.Request",
        "Response", request_path, None, Gio.DBusSignalFlags.NONE, on_response,
    )
    try:
        bus.call_sync(
            PORTAL_BUS, PORTAL_PATH, iface, method,
            GLib.Variant(f"({signature}a{{sv}})", (*args, options)),
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 5000, None,
        )
    except GLib.Error:
        bus.signal_unsubscribe(sub_id)
        return None
    timeout_id = GLib.timeout_add_seconds(timeout_seconds, on_timeout)

    loop.run()

    bus.signal_unsubscribe(sub_id)
    if not timed_out["flag"]:
        GLib.source_remove(timeout_id)

    if timed_out["flag"] or response.get("code") != 0:
        return None

    return response["results"]


def request_screenshot_uri(bus: Gio.DBusConnection, parent_window: str = "") -> str | None:
    results = portal_request(bus, SCREENSHOT_IFACE, "Screenshot", "s", (parent_window,), {
        "interactive": GLib.Variant("b", False),
    })
    return None if results is None else results.get("uri")


def start_screencast(bus: Gio.DBusConnection, parent_window: str = "") -> tuple[str, list] | None:
    """Open a ScreenCast session on all monitors; return (session_handle, streams)."""
    results = portal_request(bus, SCREENCAST_IFACE, "CreateSession", "", (), {
        "session_handle_token": GLib.Variant("s", "timetracker"),
    })
    if results is None:
        return None
    session = results["session_handle"]

    options = {
        "types": GLib.Variant("u", SOURCE_TYPE_MONITOR),
        "multiple": GLib.Variant("b", True),
        "cursor_mode": GLib.Variant("u", CURSOR_MODE_HIDDEN),
        "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
    }
    try:
        options["restore_token"] = GLib.Variant("s", TOKEN_FILE.read_text().strip())
    except FileNotFoundError:
        pass
    if portal_request(bus, SCREENCAST_IFACE, "SelectSources", "o", (session,), options) is None:
        close_session(bus, session)
        return None

    # Without a valid restore token this shows the consent dialog, so give
    # the user time to answer it.
    results = portal_request(
        bus, SCREENCAST_IFACE, "Start", "os", (session, parent_window), {},
        CONSENT_TIMEOUT_SECONDS,
    )
    if results is None or not results.get("streams"):
        close_session(bus, session)
        return None

    # Restore tokens are single-use: each Start hands back the next one.
    if "restore_token" in results:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(results["restore_token"])

    return session, results["streams"]


def close_session(bus: Gio.DBusConnection, session: str) -> None:
    try:
        bus.call_sync(
            PORTAL_BUS, session, "org.freedesktop.portal.Session", "Close", None,
            None, Gio.DBusCallFlags.NONE, 2000, None,
        )
    except GLib.Error:
        pass


def open_pipewire_remote(bus: Gio.DBusConnection, session: str) -> int:
    result, fd_list = bus.call_with_unix_fd_list_sync(
        PORTAL_BUS, PORTAL_PATH, SCREENCAST_IFACE, "OpenPipeWireRemote",
        GLib.Variant("(oa{sv})", (session, {})),
        GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, 5000, None, None,
    )
    return fd_list.get(result.unpack()[0])


def grab_frame(pipewire_fd: int, node_id: int) -> GdkPixbuf.Pixbuf | None:
    """Pull a single frame from one PipeWire screencast stream."""
    pipeline = Gst.parse_launch(
        f"pipewiresrc fd={os.dup(pipewire_fd)} path={node_id} ! videoconvert ! "
        "video/x-raw,format=RGB ! appsink name=sink max-buffers=1 drop=true"
    )
    pipeline.set_state(Gst.State.PLAYING)
    try:
        sample = pipeline.get_by_name("sink").emit(
            "try-pull-sample", FRAME_TIMEOUT_SECONDS * Gst.SECOND,
        )
        if sample is None:
            return None
        structure = sample.get_caps().get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")
        buffer = sample.get_buffer()
        data = buffer.extract_dup(0, buffer.get_size())
        rowstride = (width * 3 + 3) & ~3
        return GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, False, 8, width, height, rowstride,
        )
    finally:
        pipeline.set_state(Gst.State.NULL)


def compose(frames: list) -> GdkPixbuf.Pixbuf:
    """Lay out per-monitor frames as (pixbuf, position, size) into one image."""
    if len(frames) == 1:
        return frames[0][0]

    # Stream position/size are in logical coordinates; frames are in pixels.
    scale = max(pixbuf.get_width() / size[0] for pixbuf, _, size in frames)
    left = min(position[0] for _, position, _ in frames)
    top = min(position[1] for _, position, _ in frames)
    right = max(position[0] + size[0] for _, position, size in frames)
    bottom = max(position[1] + size[1] for _, position, size in frames)

    canvas = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8,
        round((right - left) * scale), round((bottom - top) * scale),
    )
    canvas.fill(0)
    for pixbuf, position, size in frames:
        width, height = round(size[0] * scale), round(size[1] * scale)
        if (width, height) != (pixbuf.get_width(), pixbuf.get_height()):
            pixbuf = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        pixbuf.copy_area(
            0, 0, width, height, canvas,
            round((position[0] - left) * scale), round((position[1] - top) * scale),
        )
    return canvas


def capture(bus: Gio.DBusConnection, session: str, streams: list) -> GdkPixbuf.Pixbuf | None:
    pipewire_fd = open_pipewire_remote(bus, session)
    try:
        frames = []
        for node_id, properties in streams:
            pixbuf = grab_frame(pipewire_fd, node_id)
            if pixbuf is None:
                return None
            position = properties.get("position", (0, 0))
            size = properties.get("size", (pixbuf.get_width(), pixbuf.get_height()))
            frames.append((pixbuf, position, size))
        return compose(frames)
    finally:
        os.close(pipewire_fd)


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


def capture_screencast(bus: Gio.DBusConnection, destination: Path) -> None:
    Gst.init(None)

    screencast = start_screencast(bus)
    if screencast is None:
        parent_window = get_window_handle()
        if parent_window is None:
            sys.exit("could not obtain a window handle for first-time screencast consent")
        screencast = start_screencast(bus, parent_window)
        if screencast is None:
            sys.exit("screencast request denied even with a focused window")

    session, streams = screencast
    try:
        image = capture(bus, session, streams)
    finally:
        close_session(bus, session)
    if image is None:
        sys.exit("no frame received from the screencast stream")

    image.savev(str(destination), "png", [], [])


def capture_screenshot(bus: Gio.DBusConnection, destination: Path) -> None:
    uri = request_screenshot_uri(bus)
    if uri is None:
        parent_window = get_window_handle()
        if parent_window is None:
            sys.exit("could not obtain a window handle for first-time screenshot consent")
        uri = request_screenshot_uri(bus, parent_window)
        if uri is None:
            sys.exit("screenshot request denied even with a focused window")

    shutil.move(unquote(urlparse(uri).path), str(destination))


METHODS = {"screenshot": capture_screenshot, "screencast": capture_screencast}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--method", choices=METHODS, default="screenshot",
        help="screenshot: via the Screenshot portal, with GNOME's flash and shutter "
             "sound (default); screencast: silent, via a short ScreenCast session",
    )
    args = parser.parse_args()

    if pause.is_paused():
        return

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    if is_screen_locked(bus):
        return

    now = datetime.now()
    destination = OUTPUT_ROOT / now.strftime("%Y-%m-%d") / f"{now.strftime('%H-%M-%S')}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    METHODS[args.method](bus, destination)
    print(destination)


if __name__ == "__main__":
    main()
