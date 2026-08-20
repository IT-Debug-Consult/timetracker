#!/usr/bin/env python3
"""Tray icon to pause/resume timetracker screenshot capture."""

import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator3
from gi.repository import GLib, Gtk

import pause

DURATIONS_MINUTES = [15, 30, 60, 120]
REFRESH_INTERVAL_SECONDS = 15
ICON_ACTIVE = "camera-photo-symbolic"
ICON_PAUSED = "media-playback-pause-symbolic"


def format_duration(minutes: int) -> str:
    if minutes % 60 == 0:
        return f"{minutes // 60} h"
    return f"{minutes} min"


class TimetrackerTray:
    def __init__(self):
        self.indicator = AppIndicator3.Indicator.new(
            "timetracker-screenshot",
            ICON_ACTIVE,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        self.status_item = Gtk.MenuItem(label="", sensitive=False)
        self.resume_item = Gtk.MenuItem(label="Resume now")
        self.resume_item.connect("activate", self.on_resume)

        self.menu = Gtk.Menu()
        self.menu.append(self.status_item)
        self.menu.append(self.resume_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        for minutes in DURATIONS_MINUTES:
            item = Gtk.MenuItem(label=f"Pause {format_duration(minutes)}")
            item.connect("activate", self.on_pause, minutes)
            self.menu.append(item)
        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit tray icon")
        quit_item.connect("activate", lambda _widget: Gtk.main_quit())
        self.menu.append(quit_item)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

        self.refresh()
        GLib.timeout_add_seconds(REFRESH_INTERVAL_SECONDS, self.refresh)

    def on_pause(self, _widget, minutes):
        pause.set_pause(minutes)
        self.refresh()

    def on_resume(self, _widget):
        pause.clear_pause()
        self.refresh()

    def refresh(self):
        until = pause.get_pause_until()
        if until is None:
            self.indicator.set_icon_full(ICON_ACTIVE, "Timetracker: capturing")
            self.status_item.set_label("Capturing every 7.5 min")
            self.resume_item.set_sensitive(False)
        else:
            remaining = max(0, int(until - time.time()))
            self.indicator.set_icon_full(ICON_PAUSED, "Timetracker: paused")
            self.status_item.set_label(f"Paused ({remaining // 60}m {remaining % 60}s left)")
            self.resume_item.set_sensitive(True)
        return True


def main():
    TimetrackerTray()
    Gtk.main()


if __name__ == "__main__":
    main()
