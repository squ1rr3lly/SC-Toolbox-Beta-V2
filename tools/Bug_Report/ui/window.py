"""Bug Report overlay: session facts, a short description, Bugmirror, then Issue Council."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QPushButton, QSplitter, QStackedWidget, QStyle, QStyleOptionViewItem,
    QStyledItemDelegate, QTextEdit, QVBoxLayout, QWidget,
)

from shared.qt.base_window import SCWindow
from shared.qt.theme import P
from shared.qt.title_bar import SCTitleBar

from core.bugmirror import (
    NEW_ISSUE_URL, IssueDetail, IssueHit, SearchResult, highlight_html, matches_session_build,
)
from core.capture import CaptureResult, copy_screenshots_since, make_issue_dir
from core.report import (
    ReportDraft, existing_description, note_attachments, refresh_session, write_report,
)
from core.session import SessionContext
from core import settings as st
from ui.forms import MoreInfoForm, NewIssueForm
from ui.label_picker import LabelPicker
from ui.workers import ClipTask, DebugOffTask, DetailTask, SearchTask, SessionTask

log = logging.getLogger(__name__)

ACCENT = "#ff6644"


class BugReportWindow(SCWindow):
    """One activation is one issue folder. The window stays hidden until the shot is taken."""

    def __init__(
        self, geometry, hotkey_text: str = "", cmd_file: Optional[str] = None,
        fetch_labels: bool = True,
    ) -> None:
        super().__init__(
            title="Bug Report",
            width=geometry.w, height=geometry.h,
            min_w=720, min_h=520,
            opacity=geometry.opacity, accent=ACCENT,
        )
        self.restore_geometry_from_args(
            geometry.x, geometry.y, geometry.w, geometry.h, geometry.opacity)
        self._standalone = not cmd_file or cmd_file == os.devnull
        self._want_visible = False
        self._busy = False
        self._folder = ""
        self._clip_started = 0.0
        self._ctx = SessionContext()
        self._hits: list[IssueHit] = []
        self._detail: IssueDetail | None = None
        self._draft = ReportDraft()
        self._suspend_rewrite = False
        self._tasks: list = []
        self._settings = st.load_settings()
        self._fetch_labels = fetch_labels

        title = SCTitleBar(
            window=self, title="BUG REPORT", icon_text="\U0001f41e", accent_color=ACCENT,
            hotkey_text=hotkey_text, show_minimize=True,
        )
        title.minimize_clicked.connect(self.showMinimized)
        title.close_clicked.connect(self._on_close)
        self.content_layout.addWidget(title)

        self._status = QLabel("Capture the clip, then choose an existing issue or report a new one.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {P.fg_dim}; padding: 6px 12px;")
        self.content_layout.addWidget(self._status)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 0, 12, 10)
        lay.setSpacing(8)
        self.content_layout.addWidget(body, 1)

        self._facts = QLabel("")
        self._facts.setWordWrap(True)
        self._facts.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._facts.setStyleSheet(
            f"color: {P.fg}; background: {P.bg_card}; border: 1px solid {P.border}; "
            f"border-radius: 4px; padding: 8px;"
        )
        lay.addWidget(self._facts)
        self._show_facts()

        self._pages = QStackedWidget()
        lay.addWidget(self._pages, 1)
        self._pages.addWidget(self._build_search_page())
        self._more = MoreInfoForm()
        self._more.submit_requested.connect(self._submit_more)
        self._more.back_requested.connect(self._show_search)
        self._more.changed.connect(self._rewrite_report)
        self._pages.addWidget(self._more)
        self._new = NewIssueForm()
        self._new.submit_requested.connect(self._submit_new)
        self._new.back_requested.connect(self._show_search)
        self._new.changed.connect(self._rewrite_report)
        self._pages.addWidget(self._new)

        media = QHBoxLayout()
        media_label = QLabel("Screenshots")
        media_label.setStyleSheet(f"color: {P.fg_dim};")
        media.addWidget(media_label)
        self._media_dir = QLineEdit(self._settings.get("media_dir", ""))
        self._media_dir.setPlaceholderText("Folder where screenshots are saved")
        self._media_dir.setToolTip(
            "Screenshots saved here after Capture clip are copied in when you choose an issue."
        )
        self._media_dir.editingFinished.connect(self._save_media)
        media.addWidget(self._media_dir, 1)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_media)
        media.addWidget(browse)
        lay.addLayout(media)
        self._clip_shortcut = QShortcut(QKeySequence("Ctrl+Shift+G"), self)
        self._clip_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._clip_shortcut.activated.connect(self._capture_clip)

        actions = QHBoxLayout()
        self._debug_off = QPushButton("Turn debug info off")
        self._debug_off.clicked.connect(self._turn_debug_off)
        actions.addWidget(self._debug_off)
        self._open_folder = QPushButton("Open folder")
        self._open_folder.clicked.connect(self._reveal_folder)
        actions.addWidget(self._open_folder)
        actions.addStretch(1)
        key_label = QLabel("Console key")
        key_label.setStyleSheet(f"color: {P.fg_dim};")
        actions.addWidget(key_label)
        self._console = QLineEdit(self._settings.get("console_key", "`"))
        self._console.setMaxLength(1)
        self._console.setFixedWidth(36)
        self._console.setToolTip("The key that opens the Star Citizen console. Default is `.")
        self._console.editingFinished.connect(self._save_console_key)
        actions.addWidget(self._console)
        lay.addLayout(actions)

    def _build_search_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        row = QHBoxLayout()
        self._description = QLineEdit()
        self._description.setPlaceholderText("Short description of what went wrong")
        self._description.returnPressed.connect(self._search)
        self._description.textChanged.connect(lambda _text: self._rewrite_report())
        row.addWidget(self._description, 1)
        self._search_btn = QPushButton("Search Bugmirror")
        self._search_btn.clicked.connect(self._search)
        row.addWidget(self._search_btn)
        lay.addLayout(row)

        self._labels = LabelPicker(self._settings, fetch=self._fetch_labels)
        lay.addWidget(self._labels)

        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: {ACCENT}; }}")
        self._list = QListWidget()
        self._list.setItemDelegate(_MatchDelegate(self._list))
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {P.bg_card}; color: {P.fg}; border: none; outline: none; }}"
            f"QListWidget::item {{ border-bottom: 1px solid {P.border}; }}"
            f"QListWidget::item:selected {{ background: {P.selection}; color: {P.fg_bright}; }}"
        )
        self._list.currentRowChanged.connect(self._row_chosen)
        matches_pane, self._matches_header = _pane("Matches", self._list)
        splitter.addWidget(matches_pane)
        self._detail_view = QTextEdit()
        self._detail_view.setReadOnly(True)
        self._detail_view.setPlaceholderText("Select an issue to read it.")
        self._detail_view.setStyleSheet(
            f"QTextEdit {{ background: {P.bg_primary}; color: {P.fg}; border: none; padding: 8px; }}"
        )
        issue_pane, _issue_header = _pane("Issue", self._detail_view)
        splitter.addWidget(issue_pane)
        splitter.setSizes([240, 280])
        lay.addWidget(splitter, 1)

        actions = QHBoxLayout()
        self._confirm = QPushButton("This is the one")
        self._confirm.setEnabled(False)
        self._confirm.clicked.connect(self._confirm_issue)
        actions.addWidget(self._confirm)
        self._new_issue = QPushButton("Report a new issue")
        self._new_issue.clicked.connect(self._report_new)
        actions.addWidget(self._new_issue)
        actions.addStretch(1)
        lay.addLayout(actions)
        clip_row = QHBoxLayout()
        clip_row.addStretch(1)
        self._capture = QPushButton("Capture clip")
        self._capture.setToolTip("Save the last 30 seconds (Ctrl+Shift+G).")
        self._capture.setCursor(Qt.CursorShape.PointingHandCursor)
        self._capture.setMinimumSize(320, 56)
        self._capture.setStyleSheet(
            "QPushButton {"
            " background: #2fbe4a; color: #071407; font-size: 18px; font-weight: 700;"
            " border: none; border-radius: 8px; padding: 14px 40px;"
            "}"
            "QPushButton:hover { background: #46d15f; }"
            "QPushButton:pressed { background: #24963a; }"
        )
        self._capture.clicked.connect(self._capture_clip)
        clip_row.addWidget(self._capture)
        clip_row.addStretch(1)
        lay.addLayout(clip_row)
        return page

    # ── activation ───────────────────────────────────────────────────────────

    def begin_activation(self) -> None:
        """Open the tool on a new issue folder. The clip is a separate step."""
        self._want_visible = True
        if self._busy:
            return
        self._hits = []
        self._detail = None
        self._list.clear()
        self._matches_header.setText("Matches")
        self._detail_view.clear()
        self._confirm.setEnabled(False)
        self._clip_started = 0.0
        self._suspend_rewrite = True
        self._description.blockSignals(True)
        self._description.clear()
        self._description.blockSignals(False)
        self._more.clear()
        self._new.clear()
        self._labels.clear_chosen()
        self._draft = ReportDraft()
        self._pages.setCurrentIndex(0)
        self._suspend_rewrite = False
        self._ctx = SessionContext()
        self._show_facts()
        try:
            self._folder = str(make_issue_dir())
        except OSError as exc:
            self._folder = ""
            self._status.setText(f"Could not create an issue folder ({exc}).")
        else:
            self._status.setText("Capture the clip, then choose an existing issue or report a new one.")
            task = SessionTask(self._folder, self)
            task.context_ready.connect(self._on_context)
            task.failed.connect(self._on_capture_failed)
            task.finished.connect(lambda task=task: self._forget(task))
            self._tasks.append(task)
            task.start()
        self.show()
        self.raise_()
        self.activateWindow()
        self._description.setFocus()

    def _capture_clip(self) -> None:
        """Hide, send Win+Alt+G, then come back. Screenshots after this are kept."""
        if self._busy or not self._folder:
            return
        self._busy = True
        self._want_visible = True
        self.hide()
        self._status.setText("Saving the last 30 seconds…")
        task = ClipTask(self._folder, self)
        task.ready.connect(self._on_capture_ready)
        task.clip_ready.connect(self._on_clip)
        task.failed.connect(self._on_capture_failed)
        task.finished.connect(lambda task=task: self._forget(task))
        self._tasks.append(task)
        task.start()

    def handle_ipc_command(self, cmd: dict) -> None:
        kind = cmd.get("type", "")
        if kind == "show":
            self.begin_activation()
        elif kind == "hide":
            self._want_visible = False
            self.hide()
        elif kind == "quit":
            self._quit()

    def _on_close(self) -> None:
        if self._standalone:
            self._quit()
        else:
            self._want_visible = False
            self.user_close()

    def _quit(self) -> None:
        for task in list(self._tasks):
            task.wait(1500)
        app = QApplication.instance()
        if app:
            app.quit()

    def _forget(self, task) -> None:
        if task in self._tasks:
            self._tasks.remove(task)

    # ── capture results ──────────────────────────────────────────────────────

    def _on_capture_ready(self, result: CaptureResult) -> None:
        self._busy = False
        if result.watch_clip:
            self._clip_started = result.clip_started
            self._status.setText(
                "Clip requested. Screenshots you take now are included when you choose an issue."
            )
        else:
            self._status.setText(" ".join(result.notes) or "The clip was not saved.")
        if self._want_visible:
            self.show()
            self.raise_()
            self.activateWindow()
            self._description.setFocus()

    def _on_capture_failed(self, message: str) -> None:
        self._busy = False
        self._status.setText(message)
        if self._want_visible:
            self.show()
            self.raise_()
            self.activateWindow()

    def _on_context(self, payload: tuple) -> None:
        folder, ctx = payload
        if folder != self._folder:
            try:
                if not refresh_session(folder, ctx):
                    write_report(folder, ctx, existing_description(folder))
                note_attachments(folder)
            except OSError as exc:
                log.warning("bug_report: could not write %s: %s", folder, exc)
            return
        self._ctx = ctx
        self._show_facts()
        self._rewrite_report()
        if not ctx.log_path:
            self._add_note("No Game.log was found, so location and build were left blank.")

    def _on_clip(self, payload: tuple) -> None:
        folder, path = payload
        if folder != self._folder:
            note_attachments(folder)
            return
        if path:
            self._add_note("Clip saved.")
        else:
            self._add_note(
                "No Game Bar clip appeared. Win+Alt+G only keeps the last 30 seconds "
                "when background recording is already on."
            )
        self._rewrite_report()

    def _add_note(self, text: str) -> None:
        current = self._status.text()
        self._status.setText((current + " " + text).strip())

    def _show_facts(self) -> None:
        ctx = self._ctx
        build = ctx.version or ctx.build or "—"
        if ctx.channel:
            build = f"{build}  ·  {ctx.channel}"
        place = ctx.location_name or ctx.location_raw or "—"
        if ctx.star_system and ctx.star_system != "Unknown":
            place = f"{place}  ·  {ctx.star_system}"
        if ctx.aboard and ctx.ship:
            ship = ctx.ship
        elif ctx.ship:
            ship = f"on foot (last ship: {ctx.ship})"
        else:
            ship = "—"
        player = ctx.player_name or "—"
        self._facts.setText(
            f"Build   {build}\n"
            f"Place   {place}\n"
            f"Ship    {ship}\n"
            f"Shard   {ctx.shard or '—'}\n"
            f"Player  {player}"
            + (f"\nObjective   {ctx.objective}" if ctx.objective else "")
        )

    # ── Bugmirror ────────────────────────────────────────────────────────────

    def _search(self) -> None:
        text = self._description.text().strip()
        labels = self._labels.search_labels() if not text else self._labels.labels()
        if not text and not labels:
            self._hits = []
            self._detail = None
            self._list.clear()
            self._matches_header.setText("Matches")
            self._detail_view.clear()
            self._confirm.setEnabled(False)
            self._status.setText("No matching issues on Bugmirror.")
            return
        self._search_btn.setEnabled(False)
        self._list.clear()
        self._matches_header.setText("Matches")
        self._hits = []
        self._detail = None
        self._confirm.setEnabled(False)
        self._detail_view.clear()
        self._status.setText("Searching Bugmirror…")
        task = SearchTask(text, labels, self)
        task.succeeded.connect(self._on_search)
        task.failed.connect(self._on_search_failed)
        task.finished.connect(lambda: self._search_btn.setEnabled(True))
        task.finished.connect(lambda task=task: self._forget(task))
        self._tasks.append(task)
        task.start()

    def _on_search(self, result: SearchResult) -> None:
        self._hits = list(result.hits)
        self._list.clear()
        for hit in self._hits:
            badge = "this build" if matches_session_build(hit.build, self._ctx.build, self._ctx.version) else ""
            head = "  ·  ".join(
                part for part in (hit.key or hit.href, hit.status, hit.build, badge) if part
            )
            html = highlight_html(head, [])
            if hit.description:
                html += "<br>" + highlight_html(hit.description, hit.matched)
            shown = _labels_to_show(hit)
            if shown:
                html += "<br>" + highlight_html("  ·  ".join(shown), hit.matched)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, html)
            item.setToolTip(hit.council_url or hit.href)
            self._list.addItem(item)
        total = result.total if result.total is not None else len(self._hits)
        if total <= 0:
            self._matches_header.setText("Matches")
            count = "No matching issues on Bugmirror."
        else:
            self._matches_header.setText(f"{total:,} issue" + ("s" if total != 1 else ""))
            if len(self._hits) < total:
                count = f"Showing {len(self._hits):,} of {total:,} issues."
            else:
                count = f"{total:,} issue" + ("s." if total != 1 else ".")
        if self._hits and not self._description.text().strip():
            count += "  Every issue with a selected label."
        elif self._hits:
            if self._labels.labels():
                count += "  Only issues with an included label."
            count += "  Labels count most, then the title, then what happened."
        self._status.setText(count + "  Select one, or report a new issue.")

    def _on_search_failed(self, message: str) -> None:
        self._status.setText(message)

    def _row_chosen(self, row: int) -> None:
        self._detail = None
        self._confirm.setEnabled(False)
        if row < 0 or row >= len(self._hits):
            return
        hit = self._hits[row]
        self._detail_view.setPlainText("Loading…")
        task = DetailTask(hit.href, self)
        task.succeeded.connect(lambda detail, href=hit.href: self._on_detail(href, detail))
        task.failed.connect(lambda message, href=hit.href: self._on_detail_failed(href, message))
        task.finished.connect(lambda task=task: self._forget(task))
        self._tasks.append(task)
        task.start()

    def _on_detail_failed(self, href: str, message: str) -> None:
        self._on_search_failed(message)
        row = self._list.currentRow()
        if 0 <= row < len(self._hits) and self._hits[row].href == href:
            self._detail_view.setPlainText(message)

    def _on_detail(self, href: str, detail: IssueDetail) -> None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._hits) or self._hits[row].href != href:
            return
        self._detail = detail
        terms = self._hits[row].matched
        self._detail_view.setHtml(_format_detail(detail, terms))
        self._confirm.setEnabled(bool(detail.council_url))

    def _confirm_issue(self) -> None:
        if not (self._detail and self._detail.council_url):
            return
        key = self._detail.key or self._detail.bugmirror_key
        if key != self._draft.issue_key:
            self._more.clear()
        self._draft.mode = "contribution"
        self._draft.issue_key = key
        blurb = (self._detail.description or "").strip()
        if len(blurb) > 280:
            blurb = blurb[:277].rstrip() + "…"
        self._more.set_issue(key, blurb)
        copied = self._collect_screenshots()
        self._pages.setCurrentWidget(self._more)
        self._more.focus_edit()
        self._rewrite_report()
        self._status.setText(self._with_screenshots(
            "Add a More info note, then save and open the issue.", copied,
        ))

    def _report_new(self) -> None:
        self._draft.mode = "new"
        if not self._new.title().strip():
            self._new.set_title(self._description.text().strip())
        copied = self._collect_screenshots()
        self._pages.setCurrentWidget(self._new)
        self._new.focus_first()
        self._rewrite_report()
        self._status.setText(self._with_screenshots(
            "Fill in the new-issue fields. Enter adds the next step.", copied,
        ))

    def _show_search(self) -> None:
        self._pages.setCurrentIndex(0)
        self._description.setFocus()

    def _submit_more(self) -> None:
        text = self._more.more_info().strip()
        if not text:
            self._status.setText("Write a More info note first.")
            self._more.focus_edit()
            return
        if not (self._detail and self._detail.council_url):
            self._status.setText("This issue has no Issue Council link.")
            return
        self._draft.mode = "contribution"
        self._draft.issue_key = self._detail.key or self._detail.bugmirror_key
        self._draft.more_info = self._more.more_info()
        self._hand_off(
            self._detail.council_url,
            text,
            "More info is on the clipboard and saved in the folder. "
            "Paste it into the More info box, and attach the screenshot and clip.",
        )

    def _submit_new(self) -> None:
        missing = self._new.first_missing()
        if missing:
            self._status.setText(missing)
            return
        self._draft.mode = "new"
        self._new.apply(self._draft)
        self._hand_off(
            NEW_ISSUE_URL,
            None,
            "The new-issue fields are saved and copied. Each heading matches a box on the form. "
            "Attach the screenshot and clip from the folder that opened.",
        )

    def _hand_off(self, url: str, clipboard_text: str | None, note: str) -> None:
        path = self._rewrite_report()
        copied = False
        app = QApplication.instance()
        if app is not None:
            text = clipboard_text
            if text is None and path:
                try:
                    text = open(path, encoding="utf-8").read()
                except OSError as exc:
                    log.warning("bug_report: could not read the report: %s", exc)
                    text = None
            if text:
                app.clipboard().setText(text)
                copied = True
        QDesktopServices.openUrl(QUrl(url))
        self._reveal_folder()
        if copied:
            self._status.setText(note)
        else:
            self._status.setText("Issue Council opened.")

    # ── folder, report, debug cvars ──────────────────────────────────────────

    def _capture_draft(self) -> None:
        self._draft.description = self._description.text()
        if self._draft.mode == "contribution":
            self._draft.more_info = self._more.more_info()
        elif self._draft.mode == "new":
            self._new.apply(self._draft)

    def _rewrite_report(self) -> str:
        if self._suspend_rewrite or not self._folder:
            return ""
        self._capture_draft()
        try:
            return write_report(self._folder, self._ctx, self._description.text(), self._draft)
        except OSError as exc:
            log.warning("bug_report: could not write the report: %s", exc)
            return ""

    def _reveal_folder(self) -> None:
        if not self._folder or not os.path.isdir(self._folder):
            return
        try:
            os.startfile(self._folder)  # noqa: S606 - the player's own issue folder
        except OSError as exc:
            log.warning("bug_report: could not open %s: %s", self._folder, exc)

    def _turn_debug_off(self) -> None:
        self._debug_off.setEnabled(False)
        self.hide()
        QTimer.singleShot(250, self._send_debug_off)

    def _send_debug_off(self) -> None:
        task = DebugOffTask(self._console_key(), self)
        task.finished_ok.connect(self._debug_off_done)
        task.failed.connect(self._debug_off_done)
        task.finished.connect(lambda task=task: self._forget(task))
        self._tasks.append(task)
        task.start()

    def _debug_off_done(self, message: str) -> None:
        self._debug_off.setEnabled(True)
        self._status.setText(message)
        if self._want_visible:
            self.show()
            self.raise_()
            self.activateWindow()

    def _console_key(self) -> str:
        text = self._console.text().strip()
        return text[:1] if text else "`"

    def _collect_screenshots(self) -> int:
        """Copy shots taken after the clip, up to this choice of issue."""
        if not self._folder or self._clip_started <= 0:
            return 0
        source = self._media_dir.text().strip()
        if not source:
            return 0
        try:
            copied = copy_screenshots_since(Path(self._folder), source, self._clip_started)
        except OSError as exc:
            log.warning("bug_report: could not copy screenshots: %s", exc)
            return 0
        return len(copied)

    def _with_screenshots(self, message: str, copied: int) -> str:
        if not copied:
            return message
        noun = "screenshot" if copied == 1 else "screenshots"
        return f"{message} Copied {copied} {noun}."

    def _browse_media(self) -> None:
        current = self._media_dir.text().strip()
        chosen = QFileDialog.getExistingDirectory(self, "Screenshots", current)
        if not chosen:
            return
        self._media_dir.setText(chosen)
        self._save_media()

    def _save_media(self) -> None:
        self._settings["media_dir"] = self._media_dir.text().strip()
        try:
            st.save_settings(self._settings)
        except OSError as exc:
            log.warning("bug_report: could not save the media folder: %s", exc)

    def _save_console_key(self) -> None:
        self._settings["console_key"] = self._console_key()
        self._console.setText(self._settings["console_key"])
        try:
            st.save_settings(self._settings)
        except OSError as exc:
            log.warning("bug_report: could not save the console key: %s", exc)


def _pane(title: str, widget: QWidget) -> tuple[QWidget, QLabel]:
    """A labelled card, so the match list and the open issue stay distinct."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    header = QLabel(title)
    header.setStyleSheet(
        f"color: {P.fg_bright}; font-weight: bold; background: {P.bg_header}; "
        f"border: 1px solid {P.border}; border-bottom: 2px solid {ACCENT}; "
        f"padding: 4px 8px;"
    )
    lay.addWidget(header)
    frame = QFrame()
    frame.setStyleSheet(f"QFrame {{ background: {P.bg_card}; border: 1px solid {P.border}; border-top: none; }}")
    inner = QVBoxLayout(frame)
    inner.setContentsMargins(0, 0, 0, 0)
    inner.addWidget(widget)
    lay.addWidget(frame, 1)
    return box, header


def _labels_to_show(hit: IssueHit) -> list[str]:
    """Labels that matched, so a label-only hit still shows the highlighted text."""
    wanted = {term.casefold() for term in hit.matched}
    return [label for label in hit.labels if label.casefold() in wanted]


class _MatchDelegate(QStyledItemDelegate):
    """Draws a result row with the matched words in the accent color."""

    def paint(self, painter, option, index) -> None:
        painter.save()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        color = P.fg_bright if selected else P.fg
        width = max(opt.rect.width() - 16, 20)
        doc = _document(index.data(Qt.UserRole) or "", color, width, opt.font)
        painter.translate(opt.rect.left() + 8, opt.rect.top() + 4)
        doc.drawContents(painter, QRectF(0, 0, width, opt.rect.height() - 8))
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        width = option.rect.width()
        widget = option.widget
        if width < 40 and widget is not None:
            width = widget.viewport().width()
        width = max(width - 16, 20)
        doc = _document(index.data(Qt.UserRole) or "", P.fg, width, option.font)
        return QSize(int(width) + 16, int(doc.size().height()) + 10)


def _document(body: str, color: str, width: float, font) -> QTextDocument:
    doc = QTextDocument()
    doc.setDefaultFont(font)
    doc.setDefaultStyleSheet(
        f"body {{ color: {color}; }} span.hit {{ color: {ACCENT}; font-weight: 700; }}"
    )
    doc.setHtml(f"<body>{body}</body>")
    doc.setTextWidth(width)
    return doc


def _format_detail(detail: IssueDetail, terms: list[str]) -> str:
    head = "  ·  ".join(
        part for part in (
            detail.key,
            detail.bugmirror_key,
            detail.status,
            detail.priority,
            detail.build,
        ) if part
    )
    labels = ", ".join(detail.labels) if detail.labels else "—"
    staff = detail.staff_comments or "—"
    steps = detail.reproduction or "—"
    body = detail.description or "—"

    def marked(text: str) -> str:
        return highlight_html(text, terms)

    return (
        f"<body style=\"color: {P.fg}\">"
        f"{marked(head)}<br>"
        f"Labels: {marked(labels)}<br><br>"
        f"{marked(body)}<br><br>"
        f"Reproduction steps<br>"
        f"{marked(steps)}<br><br>"
        f"Staff comments<br>"
        f"{marked(staff)}"
        f"</body>"
    )
