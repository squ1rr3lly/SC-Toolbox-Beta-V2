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
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from shared.config_models import WindowGeometry

from core.bugmirror import NEW_ISSUE_URL, IssueDetail, IssueHit, SearchResult
from core.issues import read_issue_state, write_issue_state
from core.progress import draft_from_dict, load_progress
from core.report import ReportDraft
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
    window._settings["media_dir"] = str(shots)
    window._clip_started = now - 30
    window._report_new()
    assert (issue / "bug.jpg").is_file()
    assert not (issue / "earlier.png").exists()
    assert "Copied 1 screenshot." in window._status.text()
    window.close()


def test_settings_hold_the_folders_console_key_and_issues_folder(qapp, tmp_path, monkeypatch) -> None:
    saved: list[dict] = []
    monkeypatch.setattr("ui.settings_dialog.st.save_settings", lambda data: saved.append(dict(data)))
    opened: list[str] = []
    monkeypatch.setattr(
        "ui.settings_dialog.os.startfile", lambda path: opened.append(str(path)), raising=False,
    )
    window = _window(qapp)
    labels = [widget.text() for widget in window.findChildren(QLabel)]
    buttons = [widget.text() for widget in window.findChildren(QPushButton)]
    assert "Screenshots" not in labels
    assert "Console key" not in labels
    assert "Open folder" not in buttons
    assert "Turn debug info off" not in buttons
    assert "Settings" in buttons

    from ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(window._settings, window)
    dialog._shots.setText(str(tmp_path / "shots"))
    dialog._clips.setText(str(tmp_path / "clips"))
    dialog._console.setText("~")
    dialog._displayinfo.setChecked(True)
    dialog.apply()
    assert window._settings["media_dir"] == str(tmp_path / "shots")
    assert window._settings["clips_dir"] == str(tmp_path / "clips")
    assert window._settings["console_key"] == "~"
    assert window._settings["enable_displayinfo"] is True
    assert saved
    names = [widget.text() for widget in dialog.findChildren(QPushButton)]
    assert "Open saved issues directory" in names
    dialog._open_issues()
    assert opened
    dialog.close()
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
    assert "Cannot bed log on the Constellation." in window._more.detail_text()

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
    assert window._matches_header.text() == ""
    window._on_search(SearchResult(hits=[hit], total=4))
    assert window._matches_header.text() == "4 Matches"
    window._on_search(SearchResult(hits=[], total=0))
    assert window._matches_header.text() == "0 Matches"
    window._on_search(SearchResult(hits=[hit], total=4))
    assert "Showing 1 of 4 issues." in window._status.text()
    html = window._list.item(0).data(Qt.UserRole)
    assert '<span class="hit">Bed</span>' in html
    assert '<span class="hit">BedLogging</span>' in html
    assert "match" not in html.lower()
    window.close()


def _progress(monkeypatch, tmp_path):
    monkeypatch.setattr("core.progress._PATH", tmp_path / "in_progress.json")


def test_hiding_saves_the_form_and_reopening_returns_to_it(qapp, tmp_path, monkeypatch) -> None:
    _progress(monkeypatch, tmp_path)
    issue = tmp_path / "issue"
    issue.mkdir()
    window = _window(qapp)
    window._folder = str(issue)
    window._in_progress = True
    window._clip_started = 1234.5
    window._description.setText("bed log fails")
    window._labels.set_labels(["PyroII-CheckmateStation"])
    window.show()
    window._report_new()
    window._new._happened.setPlainText("The ship was gone.")
    window._new._steps.set_steps(["Lie down"])
    window._new._category.setEditText("Ships")
    window.handle_ipc_command({"type": "hide"})
    assert not window.isVisible()
    report = (issue / "report.txt").read_text(encoding="utf-8")
    assert "The ship was gone." in report
    assert "1. Lie down" in report

    again = _window(qapp)
    again.show_or_resume()
    assert again._folder == str(issue)
    assert again._pages.currentWidget() is again._new
    assert again._new.happened() == "The ship was gone."
    assert again._new.steps() == ["Lie down"]
    assert again._new.category() == "Ships"
    assert again._description.text() == "bed log fails"
    assert again._labels.labels() == ["PyroII-CheckmateStation"]
    assert again._clip_started == 1234.5
    assert again.isVisible()

    class _Session(QObject):
        context_ready = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, folder, parent=None) -> None:
            super().__init__(parent)

        def start(self) -> None:
            self.finished.emit()

    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr("core.issues.REPORTS_ROOT", reports)
    monkeypatch.setattr("ui.window.SessionTask", _Session)
    again._finish()
    assert again._pages.currentIndex() == 0
    assert again._folder == ""
    assert again._description.text() == ""
    assert load_progress() is None
    kept = draft_from_dict(read_issue_state(issue)["draft"])
    assert kept.happened == "The ship was gone."
    again.close()
    window.close()


def test_closing_from_the_title_bar_saves_the_more_info_note(qapp, tmp_path, monkeypatch) -> None:
    _progress(monkeypatch, tmp_path)
    issue = tmp_path / "issue"
    issue.mkdir()
    window = _window(qapp)
    window._folder = str(issue)
    window._in_progress = True
    window._detail = IssueDetail(
        key="STARC-222067",
        description="Cannot bed log.",
        council_url="https://issue-council.example/STARC-222067",
    )
    window.show()
    window._confirm_issue()
    window._more.set_note("Happens every time.")
    window._standalone = True
    monkeypatch.setattr(window, "_quit", lambda: None)
    window._on_close()

    text = (issue / "report.txt").read_text(encoding="utf-8")
    assert "Happens every time." in text
    again = _window(qapp)
    again.show_or_resume()
    assert again._pages.currentWidget() is again._more
    assert again._more.more_info() == "Happens every time."
    assert "Cannot bed log." in again._more.detail_text()
    assert again._detail is not None
    assert again._detail.council_url == "https://issue-council.example/STARC-222067"
    again.close()
    window.close()


def test_my_issues_opens_a_saved_issue_and_stores_its_id(qapp, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.capture.REPORTS_ROOT", tmp_path)
    older = tmp_path / "20261018-090000"
    newer = tmp_path / "20261018-110000"
    older.mkdir()
    newer.mkdir()
    write_issue_state(older, {
        "folder": str(older),
        "page": "search",
        "description": "older bug",
        "draft": ReportDraft(mode="new", title="Older title", happened="It failed."),
    })
    write_issue_state(newer, {
        "folder": str(newer),
        "page": "new",
        "description": "newer bug",
        "draft": ReportDraft(mode="new", title="Newer title", issue_id="STARC-9"),
    })
    window = _window(qapp)
    window.show()
    search_buttons = [button.text() for button in window._pages.widget(0).findChildren(QPushButton)]
    assert "Contribute to this issue" in search_buttons
    assert "My Issues" in search_buttons
    assert "Finish and Save" not in search_buttons

    window._show_mine()
    titles = [button.text() for button in window._mine.findChildren(QPushButton) if button.toolTip() == "Open issue"]
    stamps = [label.text() for label in window._mine.findChildren(QLabel)]
    assert titles == ["Newer title", "Older title"]
    assert "2026-10-18 11:00:00" in stamps
    assert "2026-10-18 09:00:00" in stamps
    edit = window._mine._edits[str(newer)]
    assert edit.text() == "STARC-9"
    edit.setText("STARC-15")
    check = next(button for button in edit.parentWidget().findChildren(QPushButton) if button.toolTip() == "Save issue ID")
    assert check.icon().isNull() is False
    check.click()
    assert draft_from_dict(read_issue_state(newer)["draft"]).issue_id == "STARC-15"
    delete = next(button for button in edit.parentWidget().findChildren(QPushButton) if button.toolTip() == "Delete issue")
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    delete.click()
    assert newer.is_dir()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    delete.click()
    assert not newer.exists()

    opener = next(button for button in window._mine.findChildren(QPushButton) if "Older title" in button.text())
    opener.click()
    assert window._folder == str(older)
    assert window._pages.currentWidget() is window._new
    assert window._new.title() == "Older title"
    assert window._new.happened() == "It failed."
    window._new.set_issue_id("STARC-3")

    class _Session(QObject):
        context_ready = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, folder, parent=None) -> None:
            super().__init__(parent)

        def start(self) -> None:
            self.finished.emit()

    monkeypatch.setattr("core.issues.REPORTS_ROOT", tmp_path)
    monkeypatch.setattr("ui.window.SessionTask", _Session)
    window._finish()
    assert window._pages.currentIndex() == 0
    assert window._folder == ""
    assert "Issue ID:\nSTARC-3" in (older / "report.txt").read_text(encoding="utf-8")
    window.close()


def test_an_issue_folder_is_created_only_from_report_or_contribute(qapp, tmp_path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr("core.issues.REPORTS_ROOT", reports)

    class _Session(QObject):
        context_ready = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, folder, parent=None) -> None:
            super().__init__(parent)

        def start(self) -> None:
            self.finished.emit()

    created: list[str] = []

    def _make():
        path = reports / "20261018-120000"
        path.mkdir()
        created.append(str(path))
        return path

    monkeypatch.setattr("ui.window.SessionTask", _Session)
    monkeypatch.setattr("ui.window.make_issue_dir", _make)
    window = _window(qapp)
    window.begin_activation()
    assert created == []
    assert window._folder == ""
    window._description.setText("bed log")
    window._report_new()
    assert created == [str(reports / "20261018-120000")]
    assert window._pages.currentWidget() is window._new
    assert window._new.title() == "bed log"
    window._new.set_title("")
    window._finish()
    assert not (reports / "20261018-120000").exists()
    assert window._pages.currentIndex() == 0
    assert window._status.text() == "Untitled issue discarded."
    window.close()


def _form_buttons(form) -> list[str]:
    return [button.text() for button in form.findChildren(QPushButton)]


def test_a_new_issue_is_saved_or_discarded_and_a_saved_one_can_fork(qapp, tmp_path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr("core.issues.REPORTS_ROOT", reports)
    monkeypatch.setattr("core.capture.REPORTS_ROOT", reports)

    class _Session(QObject):
        context_ready = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, folder, parent=None) -> None:
            super().__init__(parent)

        def start(self) -> None:
            self.finished.emit()

    numbers = {"n": 0}

    def _make():
        numbers["n"] += 1
        path = reports / f"20261018-12000{numbers['n']}"
        path.mkdir()
        return path

    monkeypatch.setattr("ui.window.SessionTask", _Session)
    monkeypatch.setattr("ui.window.make_issue_dir", _make)
    window = _window(qapp)
    window._description.setText("bed log")
    window._report_new()
    assert "Save" in _form_buttons(window._new)
    assert "Discard" in _form_buttons(window._new)
    assert "Save Changes" not in _form_buttons(window._new)
    assert "Save and open issue" not in _form_buttons(window._new)
    council = next(button for button in window._new.findChildren(QPushButton) if button.text() == "Open Issue Council")
    assert not council.isEnabled()
    first = window._folder
    window._new._happened.setPlainText("The ship was gone.")
    window._save_entry()
    assert window._pages.currentIndex() == 0
    assert read_issue_state(first)["saved"] is True

    window._show_mine()
    opener = next(button for button in window._mine.findChildren(QPushButton) if button.text() == "bed log")
    opener.click()
    assert window._pages.currentWidget() is window._new
    assert "Save Changes" in _form_buttons(window._new)
    assert "Save as New Entry" in _form_buttons(window._new)
    council = next(button for button in window._new.findChildren(QPushButton) if button.text() == "Open Issue Council")
    assert council.isEnabled()
    next(button for button in window._new.findChildren(QPushButton) if button.text() == "Back").click()
    assert window._pages.currentWidget() is window._mine
    opener = next(button for button in window._mine.findChildren(QPushButton) if button.text() == "bed log")
    opener.click()
    window._new.set_title("bed log copy")
    window._save_as_new()
    assert os.path.isdir(first)
    assert read_issue_state(first)["draft"]["title"] == "bed log"
    copies = [
        path for path in reports.iterdir()
        if path.is_dir() and os.path.normcase(str(path)) != os.path.normcase(first)
    ]
    assert len(copies) == 1
    assert read_issue_state(copies[0])["draft"]["title"] == "bed log copy"
    assert read_issue_state(copies[0])["saved"] is True
    window.close()
