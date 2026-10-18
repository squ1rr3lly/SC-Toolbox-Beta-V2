"""The label workbook: tabs, then columns left to right."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
import urllib.request

from core.labels import _download_url, download_workbook, label_tokens, load_catalog, narrow


def _xlsx(path: Path, sheets: dict[str, list[list[str]]]) -> None:
    strings: list[str] = []
    index: dict[str, int] = {}

    def sid(text: str) -> int:
        if text not in index:
            index[text] = len(strings)
            strings.append(text)
        return index[text]

    def col_name(number: int) -> str:
        name = ""
        while number:
            number, rem = divmod(number - 1, 26)
            name = chr(65 + rem) + name
        return name

    sheet_xml = []
    for rows in sheets.values():
        body = []
        for r_index, row in enumerate(rows, 1):
            cells = []
            for c_index, value in enumerate(row, 1):
                ref = f"{col_name(c_index)}{r_index}"
                cells.append(f'<c r="{ref}" t="s"><v>{sid(value)}</v></c>')
            body.append(f'<row r="{r_index}">{"".join(cells)}</row>')
        sheet_xml.append(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>'
        )
    shared = "".join(f"<si><t>{escape(text)}</t></si>" for text in strings)
    sheet_tags = []
    rels = []
    for number, name in enumerate(sheets, 1):
        sheet_tags.append(
            f'<sheet name="{escape(name)}" sheetId="{number}" r:id="rId{number}"/>'
        )
        rels.append(
            f'<Relationship Id="rId{number}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{number}.xml"/>'
        )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(sheet_tags)}</sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(rels)}</Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '</Types>'
    )
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("[Content_Types].xml", content_types)
        book.writestr("xl/workbook.xml", workbook)
        book.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        book.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{shared}</sst>',
        )
        for number, xml in enumerate(sheet_xml, 1):
            book.writestr(f"xl/worksheets/sheet{number}.xml", xml)


def test_tabs_then_columns_narrow_to_the_label(tmp_path: Path) -> None:
    path = tmp_path / "labels.xlsx"
    _xlsx(path, {
        "FPS Weapons": [
            ["Label group", "Label", "Filter"],
            ["Ballistic", "FPSWeapon", "FPS weapons"],
            ["", "Rifle", "Rifles"],
            ["Energy", "Laser", "Laser weapons"],
        ],
        "all the rest": [
            ["Label group", "Label", "Filter"],
            ["", "EMP", "EMP (Electro Magnetic Pulse)"],
            ["", "QED", "QED (Quantum Entanglement Dampener)"],
        ],
    })
    catalog = load_catalog(path)
    assert [sheet.name for sheet in catalog.sheets] == ["FPS Weapons", "all the rest"]

    weapons = catalog.sheet("FPS Weapons")
    assert weapons is not None
    assert narrow(weapons, []).steps[0][0] == "Label group"
    assert narrow(weapons, []).steps[0][1] == ["Ballistic", "Energy"]
    ballistic = narrow(weapons, ["Ballistic"])
    assert [step[0] for step in ballistic.steps] == ["Label group", "Label"]
    assert ballistic.steps[1][1] == ["FPSWeapon", "Rifle"]
    assert ballistic.label == ""
    chosen = narrow(weapons, ["Ballistic", "Rifle"])
    assert [step[0] for step in chosen.steps] == ["Label group", "Label"]
    assert chosen.label == "Rifle"
    assert chosen.detail == "Rifles"

    rest = catalog.sheet("all the rest")
    assert rest is not None
    opened = narrow(rest, [])
    assert [step[0] for step in opened.steps] == ["Label"]
    assert opened.steps[0][1] == ["EMP", "QED"]
    picked = narrow(rest, ["EMP"])
    assert picked.label == "EMP"
    assert picked.detail == "EMP (Electro Magnetic Pulse)"


def test_section_titles_and_a_labels_column(tmp_path: Path) -> None:
    path = tmp_path / "sections.xlsx"
    _xlsx(path, {
        "FPS Weapons": [
            ["Primary Weapons", "", "", "", ""],
            ["Manufacturer", "Weapon Type", "Label", "Filter Name", "Description"],
            ["", "Rifle", "FPSWeapon-Rifle", "Rifles", "rifles"],
            ["Secondary Weapons", "", "", "", ""],
            ["Manufacturer", "Weapon Type", "Label", "Filter Name", "Description"],
            ["", "Pistol", "FPSWeapon-Pistol", "Pistols", "pistols"],
        ],
        "Locations": [
            ["", "", ""],
            ["Star System", "Labels", "Filter"],
            ["Nyx", "Nyx", "Nyx (Star System)"],
        ],
        "Welcome": [
            ["This page is only an introduction"],
        ],
    })
    catalog = load_catalog(path)
    assert [sheet.name for sheet in catalog.sheets] == ["FPS Weapons", "Locations"]

    weapons = catalog.sheet("FPS Weapons")
    assert weapons is not None
    assert [section.name for section in weapons.sections] == ["Primary Weapons", "Secondary Weapons"]
    primary = weapons.sections[0]
    opened = narrow(primary, [])
    assert [step[0] for step in opened.steps] == ["Weapon Type", "Label"]
    assert opened.steps[0][1:] == (["Rifle"], "Rifle")
    chosen = narrow(primary, ["Rifle"])
    assert chosen.label == "FPSWeapon-Rifle"
    assert chosen.detail == "Rifles"
    assert chosen.description == "rifles"

    places = catalog.sheet("Locations")
    assert places is not None
    assert places.sections == []
    assert places.headers[:2] == ["Star System", "Labels"]
    assert narrow(places, ["Nyx", "Nyx"]).label == "Nyx"
    assert label_tokens(
        "Tumbril-CycloneSeries OR  Tumbril-CycloneAA OR Tumbril-CycloneMT"
    ) == ["Tumbril-CycloneSeries", "Tumbril-CycloneAA", "Tumbril-CycloneMT"]


def test_description_is_white_and_clears_when_the_label_list_opens(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from ui.label_picker import LabelPicker, _LabelBox

    QApplication.instance() or QApplication([])
    path = tmp_path / "labels.xlsx"
    _xlsx(path, {
        "FPS Weapons": [
            ["Weapon Type", "Label", "Filter Name", "Description"],
            ["Rifle", "FPSWeapon-Rifle", "Rifles", "describes rifles"],
            ["Pistol", "FPSWeapon-Pistol", "Pistols", "describes pistols"],
        ],
    })
    picker = LabelPicker({}, fetch=False)
    picker._show_catalog(load_catalog(path))
    picker._picks = ["Rifle"]
    picker._rebuild_boxes(picker._sheet)
    assert picker._description.text() == "describes rifles"
    assert "#ffffff" in picker._description.styleSheet()
    assert picker._filters.text() == "Rifles"
    layout = picker.layout()
    order = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is picker._description:
            order.append("description")
        elif item.widget() is picker._filters:
            order.append("filters")
        elif item.layout() is picker._chips:
            order.append("chips")
    assert order == ["description", "filters", "chips"]
    label_box = next(box for box in picker._boxes if isinstance(box, _LabelBox))
    assert [box.currentText() for box in picker._boxes] == ["Rifle", "Rifles"]
    assert label_box.currentText() == "Rifles"
    assert label_box.currentData() == "FPSWeapon-Rifle"
    assert all(label_box.itemText(i) != "FPSWeapon-Rifle" for i in range(label_box.count()))
    picker.show()
    label_box.showPopup()
    assert picker._description.text() == ""
    both = tmp_path / "both.xlsx"
    _xlsx(both, {
        "Weapons": [
            ["Label", "Filter Name", "Description"],
            ["FPSWeapon-Rifle", "Rifles", "describes rifles"],
            ["FPSWeapon-Pistol", "Pistols", "describes pistols"],
        ],
    })
    picker._show_catalog(load_catalog(both))
    open_list = next(box for box in picker._boxes if isinstance(box, _LabelBox))
    assert open_list.itemText(0) == "Filter"
    assert [open_list.itemText(i) for i in range(1, open_list.count())] == ["Rifles", "Pistols"]
    assert "FPSWeapon-Rifle" not in [open_list.itemText(i) for i in range(open_list.count())]
    picker.close()


def test_filter_name_shows_how_many_issues_match_the_label(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from ui.label_picker import LabelPicker

    QApplication.instance() or QApplication([])
    path = tmp_path / "labels.xlsx"
    _xlsx(path, {
        "FPS Weapons": [
            ["Weapon Type", "Label", "Filter Name", "Description"],
            ["Rifle", "FPSWeapon-Rifle", "Rifles", "describes rifles"],
            ["Pistol", "FPSWeapon-Pistol", "Pistols", "describes pistols"],
        ],
    })
    picker = LabelPicker({}, fetch=False)
    picker._show_catalog(load_catalog(path))
    picker._picks = ["Rifle"]
    picker._rebuild_boxes(picker._sheet)
    assert picker._filters.text() == "Rifles"
    picker._on_count("FPSWeapon-Rifle", 14)
    assert picker._filters.text() == "Rifles  14 issues"
    picker._on_count("FPSWeapon-Rifle", 1)
    assert picker._filters.text() == "Rifles  1 issue"
    picker._on_count("FPSWeapon-Rifle", 0)
    assert picker._filters.text() == "Rifles  0 issues"
    picker._picks = ["Pistol"]
    picker._rebuild_boxes(picker._sheet)
    picker._on_count("FPSWeapon-Rifle", 99)
    assert picker._filters.text() == "Pistols"
    picker._on_count("FPSWeapon-Pistol", 1234)
    assert picker._filters.text() == "Pistols  1,234 issues"
    picker.close()


def test_filter_list_includes_every_option_when_the_group_is_automatic(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from ui.label_picker import LabelPicker, _LabelBox

    QApplication.instance() or QApplication([])
    path = tmp_path / "mining.xlsx"
    rows = [["Label group", "Label", "Filter", "Description"]]
    for number in range(1, 8):
        rows.append(["Mining", f"Mining-{number}", f"Mining filter {number}", f"description {number}"])
    _xlsx(path, {"Action Specific": rows})
    picker = LabelPicker({}, fetch=False)
    picker._show_catalog(load_catalog(path))
    box = next(box for box in picker._boxes if isinstance(box, _LabelBox))
    assert [box.itemText(i) for i in range(box.count())] == ["Filter", *[f"Mining filter {n}" for n in range(1, 8)]]
    picker.close()


def test_currently_reproducible_copy_is_not_a_second_filter(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from ui.label_picker import LabelPicker, _LabelBox

    QApplication.instance() or QApplication([])
    path = tmp_path / "locations.xlsx"
    rows = [["Manufacturer", "Star System", "Planetary System", "Celestial Body", "Labels", "Filter", "Description"]]
    filters = [
        ("PyroII-CheckmateStation", "Checkmate Station (PyroII, Monox) [currently reproducible]", "currently reproducible dashboard"),
        ("PyroII-LastDitch", "Last Ditch (PyroII, Monox)", "Last Ditch"),
        ("PyroII", "PyroII (Monox)", "the planet"),
        ("PyroII-SunsetMesa", "Sunset Mesa (PyroII, Monox)", "Sunset Mesa"),
        ("PyroII-JacksonsSwap", "Jackson's Swap (PyroII, Monox)", "Jackson's Swap"),
        ("PyroII-CheckmateStation", "Checkmate Station (PyroII, Monox)", "the station"),
        ("PyroII-YangsPlace", "Yang's Place (PyroII, Monox)", "Yang's Place"),
    ]
    for label, filt, description in filters:
        rows.append(["", "Pyro", "Monox", "Monox", label, filt, description])
    _xlsx(path, {"Locations": rows})
    sheet = load_catalog(path).sheet("Locations")
    assert sheet is not None
    shown = [
        "Last Ditch (PyroII, Monox)",
        "PyroII (Monox)",
        "Sunset Mesa (PyroII, Monox)",
        "Jackson's Swap (PyroII, Monox)",
        "Checkmate Station (PyroII, Monox)",
        "Yang's Place (PyroII, Monox)",
    ]
    opened = narrow(sheet, ["Pyro", "Monox", "Monox"])
    assert opened.steps[-1][1] == [
        "PyroII-LastDitch", "PyroII", "PyroII-SunsetMesa", "PyroII-JacksonsSwap",
        "PyroII-CheckmateStation", "PyroII-YangsPlace",
    ]
    station = narrow(sheet, ["Pyro", "Monox", "Monox", "PyroII-CheckmateStation"])
    assert station.label == "PyroII-CheckmateStation"
    assert station.detail == "Checkmate Station (PyroII, Monox)"
    assert station.description == "the station"

    picker = LabelPicker({}, fetch=False)
    picker._show_catalog(load_catalog(path))
    box = next(box for box in picker._boxes if isinstance(box, _LabelBox))
    assert [box.itemText(i) for i in range(box.count())] == ["Filter", *shown]
    picker._picks = ["Pyro", "Monox", "Monox", "PyroII-CheckmateStation"]
    picker._rebuild_boxes(picker._sheet)
    assert picker._description.text() == "the station"
    picker._include_label()
    assert picker.labels() == ["PyroII-CheckmateStation"]
    picker.close()


def test_missing_workbook_is_an_empty_catalog(tmp_path: Path) -> None:
    assert load_catalog(tmp_path / "missing.xlsx").sheets == []


def test_download_url_asks_onedrive_for_the_file() -> None:
    url = _download_url("https://onedrive.live.com/share?rtime=abc&redeem=xyz")
    assert "download=1" in url
    assert "rtime=abc" in url
    assert "redeem=xyz" in url
    assert _download_url(url) == url


def test_download_keeps_the_guest_cookie_and_rejects_a_login_page(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []
    handlers: list[object] = []

    class _Response:
        headers = {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}

        def read(self) -> bytes:
            return b"PK\x03\x04workbook"

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

    class _Opener:
        def open(self, req, timeout=None):
            seen.append(req.full_url)
            assert timeout == 15
            return _Response()

    def _opener(*args):
        handlers.extend(args)
        return _Opener()

    monkeypatch.setattr("core.labels.urllib.request.build_opener", _opener)
    dest = download_workbook(tmp_path / "labels.xlsx", "https://onedrive.live.com/share", timeout=15)
    assert dest.read_bytes().startswith(b"PK")
    assert seen == ["https://onedrive.live.com/share?download=1"]
    assert any(isinstance(handler, urllib.request.HTTPCookieProcessor) for handler in handlers)

    class _Page(_Response):
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self) -> bytes:
            return b"<html>sign in</html>"

    class _Login(_Opener):
        def open(self, req, timeout=None):
            return _Page()

    monkeypatch.setattr("core.labels.urllib.request.build_opener", lambda *_handlers: _Login())
    try:
        download_workbook(tmp_path / "nope.xlsx", "https://onedrive.live.com/share")
    except OSError as exc:
        assert "workbook" in str(exc)
    else:
        raise AssertionError("a login page was accepted")
