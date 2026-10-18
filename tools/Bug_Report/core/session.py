"""What the live Game.log can tell a bug report.

One forward pass keeps the latest match of each fact. World coordinates are
not in the log; ``r_displayinfo 3`` puts those on the screenshot.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_CHANNELS = ("LIVE", "PTU", "EPTU", "HOTFIX", "TECH-PREVIEW", "TECH-PREVIEW-2")
_CHANNEL_SET = {c.upper() for c in _CHANNELS}

_BUILD_RE = re.compile(r"Build\((\d+)\)")
_VERSION_RE = re.compile(
    r"\b(\d+\.\d+\.\d+-(?:live|ptu|eptu|tp|hotfix)(?:\.[A-Za-z0-9]+)*\.\d+)\b",
    re.IGNORECASE,
)
_NAME_RE = re.compile(r"\bname\s+(\S+)")
_GEID_RE = re.compile(r"\bgeid\s+(\S+)")
_SHARD_RE = re.compile(r"\{Join PU\}\s*\[([^\]]+)\]")
_LOC_RE = re.compile(r"Location\[([^\]]+)\]")
_CHANNEL_RE = re.compile(r"channel\s+'([^']+)'", re.IGNORECASE)
_JUR_RE = re.compile(r'"Entered\s+(.+?)\s+Jurisdiction')
_OBJ_RE = re.compile(r'"New Objective:\s*(.+?):\s*"')

_names = None


@dataclass
class SessionContext:
    """Facts from the newest Game.log. Empty strings mean the log did not say."""

    log_path: str = ""
    channel: str = ""
    build: str = ""
    version: str = ""
    player_name: str = ""
    player_geid: str = ""
    shard: str = ""
    location_raw: str = ""
    location_name: str = ""
    star_system: str = ""
    ship: str = ""
    aboard: bool = False
    jurisdiction: str = ""
    objective: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def channel_of(path: str) -> str:
    """Release channel from a Game.log path (``.../LIVE/Game.log``)."""
    for seg in re.split(r"[\\/]+", path or ""):
        if seg.upper() in _CHANNEL_SET:
            return seg.upper()
    return ""


def clean_ship(raw: str) -> str:
    """'RSI Constellation Andromeda : Pilot' -> 'RSI Constellation Andromeda'."""
    name = (raw or "").split(" : ")[0].strip()
    if name.startswith("@vehicle_Name"):
        name = name[len("@vehicle_Name"):]
    return name.replace("_", " ").strip()


def parse_log(path: str) -> SessionContext:
    """Read *path* once and keep the latest value of each fact."""
    ctx = SessionContext(log_path=path, channel=channel_of(path))
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("bug_report: cannot read %s: %s", path, exc)
        return ctx
    with fh:
        for line in fh:
            _apply_line(ctx, line)
    if ctx.location_raw:
        ctx.location_name, ctx.star_system = _name_location(ctx.location_raw)
    return ctx


def load_session(log_path: str | None = None) -> SessionContext:
    """The newest Game.log under the shared install, or *log_path* when given."""
    path = log_path
    if not path:
        try:
            from shared.sc_install import get_or_detect_sc_root, newest_game_log
        except ImportError as exc:
            log.warning("bug_report: sc_install unavailable: %s", exc)
            return SessionContext()
        root = get_or_detect_sc_root()
        path = newest_game_log(root) if root else None
    if not path or not os.path.isfile(path):
        return SessionContext()
    return parse_log(path)


def write_context(folder: str | os.PathLike, ctx: SessionContext) -> str:
    """Write ``context.json`` into the issue folder. Returns the path."""
    dest = os.path.join(os.fspath(folder), "context.json")
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ctx.to_dict(), fh, indent=2)
    os.replace(tmp, dest)
    return dest


def _apply_line(ctx: SessionContext, line: str) -> None:
    if not ctx.build:
        found = _BUILD_RE.search(line)
        if found:
            ctx.build = found.group(1)
    if not ctx.version:
        found = _VERSION_RE.search(line)
        if found:
            ctx.version = found.group(1)
    if "AccountLoginCharacterStatus_Character" in line:
        name = _NAME_RE.search(line)
        geid = _GEID_RE.search(line)
        if name:
            ctx.player_name = name.group(1)
        if geid:
            ctx.player_geid = geid.group(1)
    if "{Join PU}" in line:
        shard = _SHARD_RE.search(line)
        if shard:
            ctx.shard = shard.group(1).strip()
    if "RequestLocationInventory" in line:
        loc = _LOC_RE.search(line)
        if loc:
            ctx.location_raw = loc.group(1)
    lower = line.lower()
    if "you have joined channel" in lower:
        joined = _CHANNEL_RE.search(line)
        if joined:
            ctx.ship = clean_ship(joined.group(1))
            ctx.aboard = True
    elif "you have left channel" in lower or "you have left the channel" in lower:
        ctx.aboard = False
    if "Jurisdiction" in line:
        jur = _JUR_RE.search(line)
        if jur:
            ctx.jurisdiction = jur.group(1).strip()
    if "New Objective:" in line:
        obj = _OBJ_RE.search(line)
        if obj:
            ctx.objective = obj.group(1).strip()
    elif "Objective Complete:" in line or "Objective Withdrawn:" in line:
        ctx.objective = ""


def _name_location(code: str) -> tuple[str, str]:
    mod = _location_names()
    if mod is None:
        return code.replace("_", " "), ""
    try:
        return mod.get_location_name(code), mod.get_location_system(code)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("bug_report: location name failed for %s: %s", code, exc)
        return code.replace("_", " "), ""


def _location_names():
    """The Suit Mk2 place table, loaded by path so this tool does not import that package."""
    global _names
    if _names is not None:
        return _names or None
    path = Path(__file__).resolve().parents[3] / "tools" / "SuitMk2" / "core" / "location_names.py"
    try:
        spec = importlib.util.spec_from_file_location("bug_report_location_names", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"no loader for {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except (OSError, ImportError, SyntaxError) as exc:
        log.warning("bug_report: location names unavailable: %s", exc)
        _names = False
        return None
    _names = mod
    return mod
