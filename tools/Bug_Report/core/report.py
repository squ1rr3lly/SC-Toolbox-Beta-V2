"""The paste-ready text handed to Issue Council with the screenshot and clip."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.capture import MEDIA_EXTS
from core.session import SessionContext

# The session block, in order. A later log read replaces this and leaves the
# player's More info / new-issue answers alone.
_SESSION_RE = re.compile(
    r"Build: [^\n]*\nPlayer: [^\n]*\nShard: [^\n]*\n"
    r"Location: [^\n]*\nShip: [^\n]*\nObjective: [^\n]*\n"
)


def ship_line(ctx: SessionContext) -> str:
    if ctx.aboard and ctx.ship:
        return ctx.ship
    if ctx.ship:
        return f"on foot (last ship: {ctx.ship})"
    return "on foot"


def build_line(ctx: SessionContext) -> str:
    version = ctx.version or ctx.build or "unknown"
    if ctx.channel:
        return f"{version} ({ctx.channel})"
    return version


def place_line(ctx: SessionContext) -> str:
    if not ctx.location_name and not ctx.location_raw:
        return "unknown"
    name = ctx.location_name or ctx.location_raw
    extra = []
    if ctx.location_raw and ctx.location_raw != name:
        extra.append(ctx.location_raw)
    if ctx.star_system and ctx.star_system != "Unknown":
        extra.append(ctx.star_system)
    if ctx.jurisdiction:
        extra.append(f"jurisdiction {ctx.jurisdiction}")
    if not extra:
        return name
    return f"{name} ({', '.join(extra)})"


@dataclass
class ReportDraft:
    """What the player typed on top of the session facts.

    ``mode`` is ``""`` until they confirm an existing issue (``contribution``)
    or start a new one (``new``). The two prompts do not share a layout.
    """

    mode: str = ""
    description: str = ""
    issue_key: str = ""
    more_info: str = ""
    title: str = ""
    category: str = ""
    severity: str = ""
    steps: list[str] = field(default_factory=list)
    happened: str = ""
    expected: str = ""
    workaround: str = ""


def attachment_names(folder: str | os.PathLike) -> list[str]:
    """The game screenshot, the Game Bar clip, then other shots and videos."""
    root = Path(folder)
    if not root.is_dir():
        return []
    names = [
        path.name for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in MEDIA_EXTS
    ]

    def rank(name: str) -> tuple[int, str]:
        low = name.lower()
        if low == "screenshot.png":
            return (0, low)
        if low.startswith("clip."):
            return (1, low)
        return (2, low)

    return sorted(names, key=rank)


def session_block(ctx: SessionContext) -> str:
    """Build, player, shard, place, ship, and objective. One fact per line."""
    player = ctx.player_name or "unknown"
    if ctx.player_geid:
        player = f"{player} (geid {ctx.player_geid})"
    return (
        f"Build: {build_line(ctx)}\n"
        f"Player: {player}\n"
        f"Shard: {ctx.shard or 'unknown'}\n"
        f"Location: {place_line(ctx)}\n"
        f"Ship: {ship_line(ctx)}\n"
        f"Objective: {ctx.objective or 'none logged'}\n"
    )


def render_report(
    ctx: SessionContext,
    description: str,
    folder: str | os.PathLike,
    draft: ReportDraft | None = None,
) -> str:
    """Plain text a player can paste into an Issue Council report or contribution."""
    if draft is not None and draft.mode == "new":
        return _render_new(ctx, description, folder, draft)
    if draft is not None and draft.mode == "contribution":
        return _render_contribution(ctx, description, folder, draft)
    text = _one_line(description)
    if not text:
        text = "(not written yet)"
    return (
        "Star Citizen bug report\n"
        "\n"
        "Description:\n"
        f"{text}\n"
        "\n"
        f"{session_block(ctx)}"
        "\n"
        "Steps to reproduce:\n"
        "1.\n"
        "\n"
        "What happened:\n"
        "\n"
        "\n"
        "What should have happened:\n"
        "\n"
        "\n"
        "Attachments (in this folder):\n"
        f"{_attached(folder)}\n"
    )


def write_report(
    folder: str | os.PathLike,
    ctx: SessionContext,
    description: str,
    draft: ReportDraft | None = None,
) -> str:
    """Write ``report.txt``. Returns the path."""
    dest = os.path.join(os.fspath(folder), "report.txt")
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(render_report(ctx, description, folder, draft))
    os.replace(tmp, dest)
    return dest


def refresh_session(folder: str | os.PathLike, ctx: SessionContext) -> bool:
    """Replace the session lines in an existing report. False if there is no report."""
    path = Path(folder) / "report.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    matches = list(_SESSION_RE.finditer(text))
    if not matches:
        return False
    found = matches[-1]
    updated = text[:found.start()] + session_block(ctx) + text[found.end():]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(updated, encoding="utf-8")
    os.replace(tmp, path)
    return True


def existing_description(folder: str | os.PathLike) -> str:
    """The description already saved in ``report.txt``, or '' if there is none."""
    path = Path(folder) / "report.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    start = text.find("Description:\n")
    end = text.find("\n\nBuild:")
    if start < 0 or end < start:
        return ""
    body = text[start + len("Description:\n"):end].strip()
    if body == "(not written yet)":
        return ""
    return body


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def _block(text: str) -> str:
    """Keep the line breaks the player typed; collapse whitespace on each line."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned = [" ".join(line.split()) for line in lines]
    while cleaned and not cleaned[0]:
        cleaned.pop(0)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return "\n".join(cleaned)


def _attached(folder: str | os.PathLike) -> str:
    files = attachment_names(folder)
    return "\n".join(f"- {name}" for name in files) if files else "- (none saved)"


def _render_contribution(
    ctx: SessionContext, description: str, folder: str | os.PathLike, draft: ReportDraft,
) -> str:
    """A contribution. More info is the text pasted into that box on the issue."""
    more = _block(draft.more_info) or "(not written yet)"
    desc = _one_line(description or draft.description) or "(not written yet)"
    head = "Star Citizen bug report\n"
    if draft.issue_key:
        head += f"Contributing to: {draft.issue_key}\n"
    return (
        f"{head}"
        "\n"
        "More info:\n"
        f"{more}\n"
        "\n"
        "Description:\n"
        f"{desc}\n"
        "\n"
        f"{session_block(ctx)}"
        "\n"
        "Attachments (in this folder):\n"
        f"{_attached(folder)}\n"
    )


def _render_new(
    ctx: SessionContext, description: str, folder: str | os.PathLike, draft: ReportDraft,
) -> str:
    """A new issue, with headings that match the Issue Council form."""
    title = _one_line(draft.title) or "(not written yet)"
    category = _one_line(draft.category) or "(not chosen)"
    severity = _one_line(draft.severity) or "(not chosen)"
    steps = [step.strip() for step in draft.steps if step and step.strip()]
    if steps:
        how = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
    else:
        how = "(none yet)"
    happened = _block(draft.happened) or "(not written yet)"
    expected = _block(draft.expected) or "(not written yet)"
    workaround = _block(draft.workaround) or "(none)"
    desc = _one_line(description or draft.description) or "(not written yet)"
    return (
        "Star Citizen bug report\n"
        "\n"
        "Issue title:\n"
        f"{title}\n"
        "\n"
        "Issue category:\n"
        f"{category}\n"
        "\n"
        "How bad is it?\n"
        f"{severity}\n"
        "\n"
        "How did you get there?\n"
        f"{how}\n"
        "\n"
        "What happened?\n"
        f"{happened}\n"
        "\n"
        "What should have happened?\n"
        f"{expected}\n"
        "\n"
        "Did you find a workaround?\n"
        f"{workaround}\n"
        "\n"
        "Description:\n"
        f"{desc}\n"
        "\n"
        f"{session_block(ctx)}"
        "\n"
        "Attachments (in this folder):\n"
        f"{_attached(folder)}\n"
    )


def note_attachments(folder: str | os.PathLike) -> None:
    """Add a clip that arrived after ``report.txt`` was written, without rewriting the draft."""
    dest = Path(folder) / "report.txt"
    try:
        text = dest.read_text(encoding="utf-8")
    except OSError:
        return
    missing = [name for name in attachment_names(folder) if f"- {name}" not in text]
    if not missing:
        return
    extra = "\n".join(f"- {name}" for name in missing)
    marker = "- (none saved)"
    if marker in text:
        text = text.replace(marker, extra, 1)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += extra + "\n"
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)
