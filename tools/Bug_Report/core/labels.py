"""The Bugmirror label workbook, read left to right.

Each sheet tab is the top level. Under it, the columns narrow a row down to
the Label cell. A blank cell to the left of Label repeats the value above it,
which is how a group header covers the rows under it.
"""
from __future__ import annotations

import http.cookiejar
import logging
import os
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"

BUNDLED = Path(__file__).resolve().parents[1] / "data" / "BugmirrorLabelsAndFilters.xlsx"
# The Bugmirror label workbook. OneDrive returns the file only when the guest
# cookie set on the download redirect is sent with the following request.
SHARE_URL = (
    "https://onedrive.live.com/:x:/g/personal/182f0505ebbe8f29/"
    "IQBz8NiTqirPQap9b0XRMhNBARVXWww9JoggkE4BInaY9rw"
    "?rtime=6yYFOaEs30g"
    "&redeem=aHR0cHM6Ly8xZHJ2Lm1zL3gvYy8xODJmMDUwNWViYmU4ZjI5L0VYUHcySk9xS3M5QnFuMXZSZEV5RTBFQkZWZGJERDBtaUNDUVRnRWlkcGoydkE"
)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def cache_path() -> Path:
    """Where a downloaded copy is kept between launches."""
    return Path.home() / ".sctoolbox" / "bug_report" / "BugmirrorLabelsAndFilters.xlsx"


def download_workbook(dest: str | Path, url: str = SHARE_URL, timeout: float = 60) -> Path:
    """Save the shared workbook. Raises if OneDrive does not return an xlsx."""
    target = Path(dest)
    request_url = _download_url(url)
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        request_url, headers={"User-Agent": _UA, "Accept": "*/*"},
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = resp.read()
            kind = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise OSError(f"OneDrive returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OSError(f"Could not reach OneDrive: {exc.reason}") from exc
    if not data.startswith(b"PK") or "html" in kind.lower():
        raise OSError("OneDrive did not return the workbook")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    partial.write_bytes(data)
    os.replace(partial, target)
    return target


def _download_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if not any(key == "download" for key, _value in query):
        query.append(("download", "1"))
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), ""))


@dataclass
class LabelSheet:
    """One tab. ``rows`` line up with ``headers``."""

    name: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    label_column: int = 1
    # A tab like FPS Weapons is several tables, each under its own title.
    sections: list["LabelSheet"] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.label_column < 0:
            self.label_column = _label_column(self.headers)


@dataclass
class LabelCatalog:
    sheets: list[LabelSheet] = field(default_factory=list)

    def sheet(self, name: str) -> LabelSheet | None:
        for item in self.sheets:
            if item.name == name:
                return item
        return None


@dataclass
class Narrowing:
    """The combos still to show, and the label once the Label column is chosen."""

    steps: list[tuple[str, list[str], str]]
    label: str = ""
    detail: str = ""
    description: str = ""


def load_catalog(path: str | Path | None = None) -> LabelCatalog:
    """Read an xlsx workbook. Missing file or a bad workbook is an empty catalog."""
    candidate = Path(path) if path else BUNDLED
    if not candidate.is_file():
        return LabelCatalog()
    try:
        return _read_workbook(candidate)
    except (OSError, ET.ParseError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        log.warning("bug_report: could not read %s: %s", candidate, exc)
        return LabelCatalog()


def narrow(sheet: LabelSheet, picks: list[str]) -> Narrowing:
    """Walk the columns left to right for the choices made so far.

    A column with one remaining value is still a step, already chosen.
    ``picks`` lines up with those steps, including the single-value ones.
    The Label step is one entry per label. A second row that only marks that
    same label currently reproducible is not another filter.
    """
    steps: list[tuple[str, list[str], str]] = []
    prefix: list[tuple[int, str]] = []
    label = ""
    detail = ""
    description = ""
    for col, header in enumerate(sheet.headers):
        if _is_description(header) or _is_filter(header):
            continue
        if col == sheet.label_column:
            values = _label_values(sheet, prefix)
        else:
            values = _distinct(sheet.rows, col, prefix)
        if not values:
            continue
        slot = len(steps)
        picked = picks[slot] if slot < len(picks) and picks[slot] in values else ""
        chosen = picked or (values[0] if len(values) == 1 else "")
        steps.append((header, values, chosen))
        if not chosen:
            break
        prefix.append((col, chosen))
        if col == sheet.label_column:
            label = chosen
    if label:
        detail = _preferred_filter(sheet, prefix)
        described = prefix
        if detail:
            described = prefix + [(_filter_column(sheet), detail)]
        descriptions = _distinct(sheet.rows, _description_column(sheet), described)
        if len(descriptions) == 1:
            description = descriptions[0]
    return Narrowing(steps=steps, label=label, detail=detail, description=description)


def label_tokens(label: str) -> list[str]:
    """A cell can list several Bugmirror labels joined with OR."""
    return [part.strip() for part in re.split(r"\s+OR\s+", label) if part.strip()]


def _is_filter(header: str) -> bool:
    return header.strip().lower().startswith("filter")


def _is_description(header: str) -> bool:
    return header.strip().lower() == "description"


def _filter_column(sheet: LabelSheet) -> int:
    return _column(sheet, _is_filter)


def _description_column(sheet: LabelSheet) -> int:
    return _column(sheet, _is_description)


def _column(sheet: LabelSheet, match) -> int:
    for index, header in enumerate(sheet.headers):
        if match(header):
            return index
    return -1


def _label_values(sheet: LabelSheet, prefix: list[tuple[int, str]]) -> list[str]:
    """One label each. A currently-reproducible copy does not count as its own label."""
    filter_at = _filter_column(sheet)
    found: list[str] = []
    repro_only: list[str] = []
    for row in sheet.rows:
        if any(col >= len(row) or row[col] != value for col, value in prefix):
            continue
        label = row[sheet.label_column] if sheet.label_column < len(row) else ""
        if not label or label in found:
            continue
        filt = row[filter_at] if 0 <= filter_at < len(row) else ""
        if filt and _is_repro(filt):
            if label not in repro_only:
                repro_only.append(label)
            continue
        found.append(label)
    found.extend(label for label in repro_only if label not in found)
    return found


def _preferred_filter(sheet: LabelSheet, prefix: list[tuple[int, str]]) -> str:
    filters = _distinct(sheet.rows, _filter_column(sheet), prefix)
    plain = [item for item in filters if not _is_repro(item)]
    chosen = plain or filters
    return chosen[0] if chosen else ""


def _is_repro(text: str) -> bool:
    return "[currently reproducible]" in text.lower()


def _distinct(rows: list[list[str]], col: int, prefix: list[tuple[int, str]]) -> list[str]:
    if col < 0:
        return []
    found: list[str] = []
    for row in rows:
        if any(col_index >= len(row) or row[col_index] != value for col_index, value in prefix):
            continue
        value = row[col] if col < len(row) else ""
        if value and value not in found:
            found.append(value)
    return found


def _label_column(headers: list[str]) -> int:
    for index, header in enumerate(headers):
        if header.strip().lower() in {"label", "labels"}:
            return index
    return 1 if len(headers) > 1 else 0


def _read_workbook(path: Path) -> LabelCatalog:
    with zipfile.ZipFile(path) as book:
        shared = _shared_strings(book)
        order = _sheet_order(book)
        sheets: list[LabelSheet] = []
        for name, target in order:
            rows, merges = _read_sheet(book, target, shared)
            table = _as_sheet(name, rows, merges)
            if table is not None:
                sheets.append(table)
    return LabelCatalog(sheets=sheets)


def _sheet_order(book: zipfile.ZipFile) -> list[tuple[str, str]]:
    root = ET.fromstring(book.read("xl/workbook.xml"))
    rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    targets = {}
    for rel in rels:
        rid = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if target and not target.startswith("/"):
            target = posixpath.normpath(posixpath.join("xl", target))
        elif target.startswith("/"):
            target = target.lstrip("/")
        targets[rid] = target
    order = []
    sheets = root.find(f"{_NS}sheets")
    for sheet in list(sheets or []):
        name = sheet.attrib.get("name", "")
        rid = sheet.attrib.get(f"{_REL}id", "")
        target = targets.get(rid, "")
        if name and target:
            order.append((name, target))
    return order


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(book.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    strings = []
    for item in root.findall(f"{_NS}si"):
        strings.append("".join(node.text or "" for node in item.iter(f"{_NS}t")))
    return strings


def _read_sheet(book: zipfile.ZipFile, target: str, shared: list[str]) -> tuple[dict[int, dict[int, str]], list[tuple[int, int, int, int]]]:
    root = ET.fromstring(book.read(target))
    rows: dict[int, dict[int, str]] = {}
    for cell in root.iter(f"{_NS}c"):
        ref = cell.attrib.get("r", "")
        col, row = _ref(ref)
        if row < 1:
            continue
        rows.setdefault(row, {})[col] = _cell_text(cell, shared)
    merges = []
    for merge in root.iter(f"{_NS}mergeCell"):
        span = merge.attrib.get("ref", "")
        if ":" not in span:
            continue
        start, end = span.split(":", 1)
        c1, r1 = _ref(start)
        c2, r2 = _ref(end)
        merges.append((min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2)))
    return rows, merges


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{_NS}t")).strip()
    value = cell.find(f"{_NS}v")
    raw = (value.text or "").strip() if value is not None and value.text else ""
    if kind == "s":
        try:
            return shared[int(raw)].strip()
        except (ValueError, IndexError):
            return ""
    return raw


def _ref(ref: str) -> tuple[int, int]:
    letters = []
    digits = []
    for char in ref:
        if char.isalpha():
            letters.append(char.upper())
        elif char.isdigit():
            digits.append(char)
    col = 0
    for char in letters:
        col = col * 26 + (ord(char) - 64)
    row = int("".join(digits) or "0")
    return col - 1, row


def _as_sheet(name: str, cells: dict[int, dict[int, str]], merges: list[tuple[int, int, int, int]]) -> LabelSheet | None:
    if not cells:
        return None
    width = max((max(row) for row in cells.values()), default=-1) + 1
    if width <= 0:
        return None
    grid = []
    for index in range(1, max(cells) + 1):
        grid.append([cells.get(index, {}).get(col, "").strip() for col in range(width)])
    for c1, r1, c2, r2 in merges:
        if r1 < 1 or r1 > len(grid) or c1 < 0 or c1 >= width:
            continue
        value = grid[r1 - 1][c1]
        for row in range(r1 - 1, min(r2, len(grid))):
            for col in range(c1, min(c2 + 1, width)):
                if not grid[row][col]:
                    grid[row][col] = value
    header_rows = [index for index, row in enumerate(grid) if _is_label_header(row)]
    if not header_rows:
        return None
    sections: list[LabelSheet] = []
    for number, header_at in enumerate(header_rows):
        title = _section_title(grid, header_at)
        stop = len(grid)
        if number + 1 < len(header_rows):
            stop = header_rows[number + 1]
            earlier = _section_title_row(grid, header_rows[number + 1])
            if earlier is not None:
                stop = earlier
        headers = _trim_headers(grid[header_at])
        rows = [_fit(row, len(headers)) for row in grid[header_at + 1:stop]]
        table = _table(title, headers, rows)
        if table.rows:
            sections.append(table)
    if not sections:
        return None
    if len(sections) == 1:
        only = sections[0]
        return LabelSheet(
            name=name, headers=only.headers, rows=only.rows, label_column=only.label_column,
        )
    return LabelSheet(name=name, sections=sections)


def _is_label_header(row: list[str]) -> bool:
    return any(cell.strip().lower() in {"label", "labels"} for cell in row)


def _section_title_row(grid: list[list[str]], header_at: int) -> int | None:
    for prev in range(header_at - 1, -1, -1):
        if not any(grid[prev]):
            continue
        if _is_label_header(grid[prev]):
            return None
        values = list(dict.fromkeys(cell for cell in grid[prev] if cell))
        if len(values) == 1:
            return prev
        return None
    return None


def _section_title(grid: list[list[str]], header_at: int) -> str:
    row = _section_title_row(grid, header_at)
    if row is None:
        return ""
    return next(cell for cell in grid[row] if cell)


def _trim_headers(headers: list[str]) -> list[str]:
    last = 0
    for index, header in enumerate(headers):
        if header:
            last = index
    return headers[: last + 1]


def _fit(row: list[str], width: int) -> list[str]:
    fitted = row[:width]
    if len(fitted) < width:
        fitted.extend([""] * (width - len(fitted)))
    return fitted


def _table(name: str, headers: list[str], rows: list[list[str]]) -> LabelSheet:
    label_at = _label_column(headers)
    data: list[list[str]] = []
    for row in rows:
        if _is_label_header(row):
            continue
        if label_at < len(row):
            for col in range(label_at):
                if not row[col] and data and col < len(data[-1]):
                    row[col] = data[-1][col]
        if label_at < len(row) and row[label_at].strip():
            data.append(row)
    return LabelSheet(name=name, headers=headers, rows=data, label_column=label_at)
