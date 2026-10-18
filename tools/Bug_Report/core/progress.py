"""The issue that is open until the player clicks Finish and Save.

``~/.sctoolbox/bug_report/in_progress.json`` is what a later open reads so the
same form comes back after the window was hidden or the process restarted.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from core.report import ReportDraft

log = logging.getLogger(__name__)

_PATH = Path.home() / ".sctoolbox" / "bug_report" / "in_progress.json"


def progress_path() -> Path:
    return Path(_PATH)


def draft_from_dict(data: Any) -> ReportDraft:
    if not isinstance(data, dict):
        return ReportDraft()
    known = {item.name for item in fields(ReportDraft)}
    values = {key: data[key] for key in known if key in data}
    steps = values.get("steps")
    values["steps"] = [str(step) for step in steps] if isinstance(steps, list) else []
    for key in ("mode", "description", "issue_key", "more_info", "title", "category",
                "severity", "happened", "expected", "workaround", "issue_id"):
        if key in values and not isinstance(values[key], str):
            values[key] = str(values[key] or "")
    return ReportDraft(**values)


def load_progress() -> dict[str, Any] | None:
    """The saved in-progress issue, or None when there isn't one."""
    path = progress_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("bug_report: in-progress file unreadable (%s)", exc)
        return None
    if not isinstance(data, dict) or not str(data.get("folder") or "").strip():
        return None
    return data


def save_progress(state: dict[str, Any]) -> None:
    path = progress_path()
    os.makedirs(path.parent, exist_ok=True)
    payload = dict(state)
    draft = payload.get("draft")
    if isinstance(draft, ReportDraft):
        payload["draft"] = asdict(draft)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, path)


def clear_progress() -> None:
    path = progress_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("bug_report: could not clear the in-progress file (%s)", exc)
