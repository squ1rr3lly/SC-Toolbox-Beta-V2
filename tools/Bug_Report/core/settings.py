"""Settings for Bug Report. ``~/.sctoolbox/bug_report/settings.json``."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_DIR = os.path.join(os.path.expanduser("~"), ".sctoolbox", "bug_report")
_PATH = os.path.join(_DIR, "settings.json")

_DEFAULTS: dict[str, Any] = {
    # The key that opens the Star Citizen console. Grave, the key left of 1
    # on a US keyboard, is the game's default.
    "console_key": "`",
    # Kept so an older settings file still loads. The workbook is downloaded.
    "labels_workbook": "",
    # Screenshots and videos copied into a new issue folder.
    "media_dir": "",
    "media_minutes": 20,
}


def clamp_media_minutes(value: Any) -> int:
    """How far back to look for shots and clips. One minute through one day."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 20
    return min(24 * 60, max(1, minutes))


def settings_path() -> str:
    return _PATH


def load_settings() -> dict[str, Any]:
    data = dict(_DEFAULTS)
    try:
        with open(_PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            data.update(saved)
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("bug_report: settings unreadable (%s); using defaults", exc)
    data["console_key"] = str(data.get("console_key") or "`")[:1] or "`"
    data["media_dir"] = str(data.get("media_dir") or "").strip()
    data["media_minutes"] = clamp_media_minutes(data.get("media_minutes", 20))
    return data


def save_settings(data: dict[str, Any]) -> None:
    os.makedirs(_DIR, exist_ok=True)
    out = dict(_DEFAULTS)
    out.update(data)
    out["console_key"] = str(out.get("console_key") or "`")[:1] or "`"
    out["media_dir"] = str(out.get("media_dir") or "").strip()
    out["media_minutes"] = clamp_media_minutes(out.get("media_minutes", 20))
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    os.replace(tmp, _PATH)
