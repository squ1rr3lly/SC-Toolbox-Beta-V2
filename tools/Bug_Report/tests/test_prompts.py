"""The More info prompt and the new-issue form. No network and no key injection."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from shared.config_models import WindowGeometry

from core.bugmirror import NEW_ISSUE_URL, IssueDetail, IssueHit, SearchResult
from ui.window import BugReportWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(qapp) -> BugReportWindow:
    return BugReportWindow(
        WindowGeometry(w=980, h=720), hotkey_text="Ctrl+8", cmd_file=None, fetch_labels=False,
    )


def _quiet(monkeypatch, opened: list[str]) -> None:
    monkeypatch.setattr(
        QDesktopServices, "openUrl",
        lambda url: opened.append(url.toString() if isinstance(url, QUrl) else str(url)) or True,
    )
    monkeypatch.setattr(os, "startfile", lambda _path: None, raising=False)


def test_blank_description_searches_only_selected_labels(qapp, monkeypatch) -> None:
    seen: list[tuple[str, list[str]]] = []

    class _Search(QObject):
        succeeded = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, text: str, labels: list[str] | None = None, parent=None) -> None:
            super().__init__(parent)
            seen.append((text, list(labels or [])))

        def start(self) -> None:
            self.succeeded.emit(SearchResult(hits=[], total=0))
            self.finished.emit()

    monkeypatch.setattr("ui.window.SearchTask", _Search)
    window = _window(qapp)
    window._list.addItem("old result")
    window._description.clear()
    window._labels._chosen.clear()
    window._labels._sheet = None
    window._search()
    assert seen == []
    assert window._list.count() == 0
    assert window._status.text() == "No matching issues on Bugmirror."

    window._labels._chosen = ["PyroII-CheckmateStation", "PyroII-LastDitch"]
    window._search()
    assert seen == [("", ["PyroII-CheckmateStation", "PyroII-LastDitch"])]
    window.close()


def test_choosing_an_issue_copies_screenshots_taken_after_the_clip(qapp, tmp_path) -> None:
    issue = tmp_path / "issue"
    shots = tmp_path / "shots"
    issue.mkdir()
    shots.mkdir()
    now = time.time()
    kept = shots / "bug.jpg"
    kept.write_bytes(b"jpg")
    os.utime(kept, (now, now - 5))
    early = shots / "earlier.png"
    early.write_bytes(b"png")
    os.utime(early, (now, now - 120))
    window = _window(qapp)
    window._folder = str(issue)
    window._media_dir.setText(str(shots))
    window._clip_started = now - 30
    window._report_new()
    assert (issue / "bug.jpg").is_file()
    assert not (issue / "earlier.png").exists()
    assert "Copied 1 screenshot." in window._status.text()
    window.close()


def test_existing_issue_asks_for_more_info_and_saves_it(qapp, tmp_path, monkeypatch) -> None:
    opened: list[str] = []
    _quiet(monkeypatch, opened)
    window = _window(qapp)
    window._folder = str(tmp_path)
    window._description.setText("constellation bed")
    window._detail = IssueDetail(
        key="STARC-222067",
        description="Cannot bed log on the Constellation.",
        council_url="https://issue-council.example/STARC-222067",
    )
    window.show()
    window._confirm_issue()
    assert window._pages.currentWidget() is window._more
    assert "STARC-222067" in window._more._heading.text()

    window._submit_more()
    assert opened == []
    assert "More info note" in window._status.text()

    window._more._edit.setPlainText("Happens every time.\nWorkaround: store the ship.")
    window._submit_more()
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert "More info:\nHappens every time.\nWorkaround: store the ship." in text
    assert "Contributing to: STARC-222067" in text
    assert qapp.clipboard().text() == "Happens every time.\nWorkaround: store the ship."
    assert opened == ["https://issue-council.example/STARC-222067"]
    window.close()


def test_new_issue_enter_adds_steps_and_saves_the_fields(qapp, tmp_path, monkeypatch) -> None:
    opened: list[str] = []
    _quiet(monkeypatch, opened)
    window = _window(qapp)
    window._folder = str(tmp_path)
    window._description.setText("bed log fails")
    window.show()
    window._report_new()
    assert window._new.title() == "bed log fails"
    assert window._new.category() == ""
    assert window._new.severity() == "Playable"

    first = window._new._steps._rows[0][1]
    first.setFocus()
    first.setText("Lie down in the bed")
    QTest.keyClick(first, Qt.Key.Key_Return)
    assert len(window._new._steps._rows) == 2
    second = window._new._steps._rows[1][1]
    second.setText("Log out")
    QTest.keyClick(second, Qt.Key.Key_Return)
    assert len(window._new._steps._rows) == 3
    jumped = []
    window._new._steps.jump_requested.connect(lambda: jumped.append(True))
    QTest.keyClick(window._new._steps._rows[2][1], Qt.Key.Key_Return)
    assert jumped
    assert len(window._new._steps._rows) == 3
    QTest.keyClick(window._new._steps._rows[2][1], Qt.Key.Key_Backspace)
    assert len(window._new._steps._rows) == 2

    window._submit_new()
    assert opened == []
    window._new._happened.setPlainText("The ship was gone on login.")
    window._submit_new()
    assert opened == []
    window._new._expected.setPlainText("The character wakes in the same bed.")
    window._new._category.setEditText("Ships")
    window._submit_new()

    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert "Issue title:\nbed log fails" in text
    assert "Issue category:\nShips" in text
    assert "How bad is it?\nPlayable" in text
    assert "1. Lie down in the bed\n2. Log out\n" in text
    assert "\n3. " not in text
    assert "What happened?\nThe ship was gone on login." in text
    assert "What should have happened?\nThe character wakes in the same bed." in text
    assert "Did you find a workaround?\n(none)" in text
    assert qapp.clipboard().text() == text
    assert opened == [NEW_ISSUE_URL]
    window.close()


def test_results_show_the_total_and_highlight_matched_text(qapp) -> None:
    window = _window(qapp)
    window.show()
    hit = IssueHit(
        key="STARC-1",
        description="Bed log fails",
        status="Open",
        labels=["BedLogging"],
        matched=["bed", "BedLogging"],
    )
    window._on_search(SearchResult(hits=[hit], total=4))
    assert window._matches_header.text() == "4 issues"
    assert "Showing 1 of 4 issues." in window._status.text()
    html = window._list.item(0).data(Qt.UserRole)
    assert '<span class="hit">Bed</span>' in html
    assert '<span class="hit">BedLogging</span>' in html
    assert "match" not in html.lower()
    window.close()
