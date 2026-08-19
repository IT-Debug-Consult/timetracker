"""Shared pause-state handling for timetracker screenshot capture.

A pause expires either when its duration elapses or at the next boot,
whichever comes first -- so leaving the machine paused and rebooting
doesn't leave capture off indefinitely.
"""

import json
import time
from pathlib import Path

STATE_FILE = Path.home() / ".local" / "share" / "timetracker" / "pause.json"
BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")


def _current_boot_id() -> str:
    return BOOT_ID_FILE.read_text().strip()


def get_pause_until() -> float | None:
    """Return the pause-until epoch timestamp if a pause is active, else None."""
    try:
        data = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    if data.get("boot_id") != _current_boot_id() or time.time() >= data.get("until", 0):
        clear_pause()
        return None

    return data["until"]


def is_paused() -> bool:
    return get_pause_until() is not None


def set_pause(minutes: float) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "until": time.time() + minutes * 60,
        "boot_id": _current_boot_id(),
    }))


def clear_pause() -> None:
    STATE_FILE.unlink(missing_ok=True)
