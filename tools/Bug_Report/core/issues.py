"""Every issue folder under ``~/.sctoolbox/bug_reports``, newest first."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.capture import REPORTS_ROOT
from core.progress import draft_from_dict
from core.report import ReportDraft, write_report
from core.session import read_context

log = logging.getLogger(__name__)

_STATE = "state.json"
_STAMP = re.compile(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})")


@dataclass
class IssueRecord:
    """One saved issue, as My Issues shows it."""

    folder: str
    title: str
    issue_id: str
    when: str


def list_issues(root: Path | None = None) -> list[IssueRecord]:
    """Issue folders, most recently created first. Untitled folders are removed."""
    root = root or REPORTS_ROOT
    discard_untitled(root)
    if not root.is_dir():
        return []
    folders = [path for path in root.iterdir() if path.is_dir()]
    folders.sort(key=lambda path: path.name, reverse=True)
    return [_record(path) for path in folders]


def discard_untitled(root: Path | None = None) -> None:
    """Delete issue folders that never received a title or an issue to contribute to."""
    root = root or REPORTS_ROOT
    if not root.is_dir():
        return
    for path in list(root.iterdir()):
        if path.is_dir() and not kept_title(path):
            shutil.rmtree(path, ignore_errors=True)


def state_for_open(folder: str | os.PathLike) -> dict[str, Any] | None:
    """Saved fields for the form page. None when the folder is missing or untitled."""
    path = Path(folder)
    if not path.is_dir() or not kept_title(path):
        return None
    state = dict(read_issue_state(path) or {})
    draft = draft_from_dict(state.get("draft"))
    if draft.mode not in ("new", "contribution"):
        key = _contribution_key(_report_text(path))
        if key:
            draft = ReportDraft(mode="contribution", issue_key=key)
        else:
            draft = ReportDraft(mode="new", title=_title_in_report(path))
    state["folder"] = str(path)
    state["draft"] = asdict(draft)
    state["page"] = "more" if draft.mode == "contribution" else "new"
    return state


def copy_issue_files(source: str | os.PathLike, dest: str | os.PathLike) -> None:
    """Copy the saved report and evidence into a new folder. The state file stays behind."""
    src = Path(source)
    dst = Path(dest)
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if not item.is_file() or item.name == _STATE:
            continue
        shutil.copy2(item, dst / item.name)


def discard_issue(folder: str | os.PathLike) -> None:
    path = Path(folder)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def read_issue_state(folder: str | os.PathLike) -> dict[str, Any] | None:
    path = Path(folder) / _STATE
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("bug_report: could not read %s (%s)", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    data["folder"] = str(folder)
    return data


def write_issue_state(folder: str | os.PathLike, state: dict[str, Any]) -> None:
    """Remember this issue so My Issues can open it again after Finish and Save."""
    payload = dict(state)
    payload["folder"] = str(folder)
    draft = payload.get("draft")
    if isinstance(draft, ReportDraft):
        payload["draft"] = asdict(draft)
    dest = Path(folder) / _STATE
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, dest)


def set_issue_id(folder: str | os.PathLike, issue_id: str) -> str:
    """Store the Issue Council id for this folder and refresh ``report.txt`` when it exists."""
    text = " ".join((issue_id or "").split())
    state = read_issue_state(folder) or {
        "folder": str(folder),
        "page": "search",
        "description": "",
        "draft": {},
    }
    draft = draft_from_dict(state.get("draft"))
    draft.issue_id = text
    state["draft"] = draft
    write_issue_state(folder, state)
    report = Path(folder) / "report.txt"
    if report.is_file() or draft.mode:
        description = str(state.get("description") or draft.description or "")
        try:
            write_report(folder, read_context(folder), description, draft)
        except OSError as exc:
            log.warning("bug_report: could not update the report issue id: %s", exc)
    return text


def kept_title(path: Path) -> str:
    """The name My Issues shows. Empty means the folder is untitled and should go."""
    state = read_issue_state(path) or {}
    draft = draft_from_dict(state.get("draft"))
    if draft.mode == "contribution":
        if not draft.issue_key.strip():
            return ""
        title = draft.title.strip() or str(state.get("blurb") or "").strip()
        return title or draft.issue_key.strip()
    if draft.mode == "new":
        return draft.title.strip()
    return _title_in_report(path)


def _record(path: Path) -> IssueRecord:
    state = read_issue_state(path) or {}
    draft = draft_from_dict(state.get("draft"))
    issue_id = draft.issue_id.strip()
    if not issue_id and draft.mode == "contribution":
        issue_id = draft.issue_key.strip()
    return IssueRecord(
        folder=str(path),
        title=kept_title(path),
        issue_id=issue_id,
        when=_when(path.name),
    )


def _contribution_key(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Contributing to:"):
            return line.split(":", 1)[1].strip()
    return ""


def _report_text(path: Path) -> str:
    try:
        return (path / "report.txt").read_text(encoding="utf-8")
    except OSError:
        return ""


def _title_in_report(path: Path) -> str:
    """A title from an older report that has no state file."""
    text = _report_text(path)
    key = _contribution_key(text)
    if key:
        return key
    marker = "Issue title:\n"
    start = text.find(marker)
    if start < 0:
        return ""
    title = text[start + len(marker):].split("\n", 1)[0].strip()
    if title == "(not written yet)":
        return ""
    return title


def _when(name: str) -> str:
    found = _STAMP.match(name)
    if not found:
        return name
    year, month, day, hour, minute, second = found.groups()
    return f"{year}-{month}-{day} {hour}:{minute}:{second}"
