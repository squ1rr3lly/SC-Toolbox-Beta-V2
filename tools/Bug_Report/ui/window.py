"""Bug Report overlay: session facts, a short description, Bugmirror, then Issue Council."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QPushButton, QSplitter, QStackedWidget, QStyle, QStyleOptionViewItem,
    QStyledItemDelegate, QTextEdit, QVBoxLayout, QWidget,
)

from shared.qt.base_window import SCWindow
from shared.qt.theme import P
from shared.qt.title_bar import SCTitleBar

from core.bugmirror import (
    NEW_ISSUE_URL, IssueDetail, IssueHit, SearchResult, highlight_html, matches_session_build,
)
from core.capture import REPORTS_ROOT, CaptureResult, adopt_clip, copy_screenshots_since, make_issue_dir
from core.issues import (
    copy_issue_files, discard_issue, discard_untitled, set_issue_id, state_for_open, write_issue_state,
)
from core.progress import clear_progress, draft_from_dict, load_progress, save_progress
from core.report import (
    ReportDraft, existing_description, note_attachments, refresh_session, write_report,
)
from core.session import SessionContext, read_context, write_context
from core import settings as st
from ui.forms import MoreInfoForm, NewIssueForm
from ui.label_picker import LabelPicker
from ui.my_issues import MyIssuesPage
from ui.settings_dialog import SettingsDialog
from ui.workers import ClipTask, DetailTask, SearchTask, SessionTask

log = logging.getLogger(__name__)

ACCENT = "#ff6644"


class BugReportWindow(SCWindow):
    """One issue folder stays in progress until Finish and Save."""

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
        self._in_progress = False
        self._saved = False
        self._form_origin = "search"
        self._folder = ""
        self._loose_clip = ""
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

        self._status = QLabel("Capture the clip, then contribute to an issue or report a new one.")
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
        self._more.council_requested.connect(self._open_council)
        self._more.save_requested.connect(self._save_entry)
        self._more.discard_requested.connect(self._discard_entry)
        self._more.save_as_new_requested.connect(self._save_as_new)
        self._more.back_requested.connect(self._back_from_form)
        self._more.changed.connect(self._rewrite_report)
        self._pages.addWidget(self._more)
        self._new = NewIssueForm()
        self._new.submit_requested.connect(self._submit_new)
        self._new.council_requested.connect(self._open_council)
        self._new.save_requested.connect(self._save_entry)
        self._new.discard_requested.connect(self._discard_entry)
        self._new.save_as_new_requested.connect(self._save_as_new)
        self._new.back_requested.connect(self._back_from_form)
        self._new.changed.connect(self._rewrite_report)
        self._pages.addWidget(self._new)
        self._mine = MyIssuesPage()
        self._mine.set_root(REPORTS_ROOT)
        self._mine.open_requested.connect(self._open_saved)
        self._mine.id_saved.connect(self._save_listed_id)
        self._mine.delete_requested.connect(self._delete_listed)
        self._mine.back_requested.connect(self._show_search)
        self._pages.addWidget(self._mine)

        self._clip_shortcut = QShortcut(QKeySequence("Ctrl+Shift+G"), self)
        self._clip_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._clip_shortcut.activated.connect(self._capture_clip)

        actions = QHBoxLayout()
        self._settings_btn = QPushButton("Settings")
        self._settings_btn.clicked.connect(self._open_settings)
        actions.addWidget(self._settings_btn)
        actions.addStretch(1)
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
        matches_pane, self._matches_header = _pane("", self._list)
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
        self._confirm = QPushButton("Contribute to this issue")
        self._confirm.setEnabled(False)
        self._confirm.clicked.connect(self._confirm_issue)
        actions.addWidget(self._confirm)
        self._new_issue = QPushButton("Report a new issue")
        self._new_issue.clicked.connect(self._report_new)
        actions.addWidget(self._new_issue)
        self._my_issues = QPushButton("My Issues")
        self._my_issues.clicked.connect(self._show_mine)
        actions.addWidget(self._my_issues)
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

    def show_or_resume(self) -> None:
        """Show the window. An unfinished issue comes back on the page it was on."""
        self._want_visible = True
        if self._busy:
            return
        if self._in_progress and self._folder and os.path.isdir(self._folder):
            self._reveal()
            return
        if self._apply_progress(load_progress()):
            self._status.setText("Back to the issue in progress.")
            self._reveal(focus=True)
            return
        self.begin_activation()

    def begin_activation(self) -> None:
        """Open the tool on the search page. An issue folder waits for Contribute or Report."""
        self._want_visible = True
        if self._busy:
            return
        self._in_progress = False
        self._saved = False
        self._form_origin = "search"
        self._folder = ""
        self._loose_clip = ""
        self._more.set_saved(False)
        self._new.set_saved(False)
        discard_untitled()
        self._hits = []
        self._detail = None
        self._list.clear()
        self._matches_header.setText("")
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
        self._status.setText("Capture the clip, then contribute to an issue or report a new one.")
        task = SessionTask("", self)
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
        if self._busy:
            return
        self._save_in_progress()
        self._busy = True
        self._want_visible = True
        self.hide()
        self._status.setText("Saving the last 30 seconds…")
        task = ClipTask(
            self._folder,
            str(self._settings.get("clips_dir") or ""),
            self._console_key(),
            bool(self._settings.get("enable_displayinfo")),
            self,
        )
        task.ready.connect(self._on_capture_ready)
        task.clip_ready.connect(self._on_clip)
        task.failed.connect(self._on_capture_failed)
        task.finished.connect(lambda task=task: self._forget(task))
        self._tasks.append(task)
        task.start()

    def handle_ipc_command(self, cmd: dict) -> None:
        kind = cmd.get("type", "")
        if kind == "show":
            self.show_or_resume()
        elif kind == "hide":
            self._leave()
            self._want_visible = False
            self.hide()
        elif kind == "quit":
            self._leave()
            self._quit()

    def closeEvent(self, event) -> None:
        self._leave()
        super().closeEvent(event)

    def _on_close(self) -> None:
        self._leave()
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
        if folder and folder != self._folder:
            try:
                if not refresh_session(folder, ctx):
                    write_report(folder, ctx, existing_description(folder))
                note_attachments(folder)
            except OSError as exc:
                log.warning("bug_report: could not write %s: %s", folder, exc)
            return
        self._ctx = ctx
        self._show_facts()
        if self._folder and not folder:
            try:
                write_context(self._folder, ctx)
            except OSError as exc:
                log.warning("bug_report: could not write context: %s", exc)
        self._rewrite_report()
        if not ctx.log_path:
            self._add_note("No Game.log was found, so location and build were left blank.")

    def _on_clip(self, payload: tuple) -> None:
        folder, path = payload
        if folder and folder != self._folder:
            note_attachments(folder)
            return
        if not path:
            self._add_note(
                "No Game Bar clip appeared. Win+Alt+G only keeps the last 30 seconds "
                "when background recording is already on."
            )
            self._rewrite_report()
            return
        if self._folder and os.path.isdir(self._folder):
            self._store_clip(path)
        else:
            self._loose_clip = path
        self._rewrite_report()

    def _store_clip(self, path: str) -> None:
        if not self._folder:
            self._loose_clip = path
            return
        try:
            inside = Path(path).resolve().parent == Path(self._folder).resolve()
        except OSError:
            inside = False
        if not inside:
            adopted = adopt_clip(Path(self._folder), path)
            path = adopted or ""
        self._loose_clip = ""
        if path:
            self._add_note("Clip saved.")

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
            self._matches_header.setText("")
            self._detail_view.clear()
            self._confirm.setEnabled(False)
            self._status.setText("No matching issues on Bugmirror.")
            return
        self._search_btn.setEnabled(False)
        self._list.clear()
        self._matches_header.setText("")
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
        if total < 0:
            total = 0
        self._matches_header.setText(f"{total:,} Matches")
        if total <= 0:
            count = "No matching issues on Bugmirror."
        else:
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
        self._status.setText(count + "  Select one to contribute, or report a new issue.")

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
        if not self._ensure_issue_dir():
            return
        self._form_origin = "search"
        key = self._detail.key or self._detail.bugmirror_key
        if key != self._draft.issue_key:
            self._more.clear()
            self._draft.issue_id = key
        elif not self._draft.issue_id.strip():
            self._draft.issue_id = key
        self._draft.mode = "contribution"
        self._draft.issue_key = key
        row = self._list.currentRow()
        hit_title = self._hits[row].description.strip() if 0 <= row < len(self._hits) else ""
        self._draft.title = hit_title or self._description.text().strip()
        blurb = (self._detail.description or "").strip()
        if len(blurb) > 280:
            blurb = blurb[:277].rstrip() + "…"
        self._more.set_issue(key, blurb)
        self._more.set_detail(_format_detail(self._detail, []))
        copied = self._collect_screenshots()
        self._pages.setCurrentWidget(self._more)
        self._more.set_saved(self._saved)
        self._more.focus_edit()
        self._rewrite_report()
        self._status.setText(self._with_screenshots(
            "Add a More info note, then save and open the issue.", copied,
        ))

    def _report_new(self) -> None:
        if not self._ensure_issue_dir():
            return
        self._form_origin = "search"
        self._draft.mode = "new"
        if not self._new.title().strip():
            self._new.set_title(self._description.text().strip())
        copied = self._collect_screenshots()
        self._pages.setCurrentWidget(self._new)
        self._new.set_saved(self._saved)
        self._new.focus_first()
        self._rewrite_report()
        self._status.setText(self._with_screenshots(
            "Fill in the new-issue fields. Enter adds the next step.", copied,
        ))

    def _show_search(self) -> None:
        self._leave()
        self._pages.setCurrentIndex(0)
        self._description.setFocus()

    def _back_from_form(self) -> None:
        """Search when the issue was started there. My Issues when it was opened from that list."""
        if self._form_origin == "mine":
            self._show_mine()
            return
        self._show_search()

    def _open_council(self) -> None:
        """Open Issue Council for an issue that has already been saved."""
        if not self._saved:
            return
        self._capture_draft()
        if self._draft.mode == "contribution":
            url = self._detail.council_url if self._detail else ""
            if not url:
                self._status.setText("This issue has no Issue Council link.")
                return
            self._hand_off(
                url,
                self._more.more_info(),
                "More info is on the clipboard. Paste it into the More info box, "
                "and attach the screenshot and clip.",
            )
            return
        self._hand_off(
            NEW_ISSUE_URL,
            None,
            "The report is on the clipboard. Each heading matches a box on the form. "
            "Attach the screenshot and clip from the folder that opened.",
        )

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

    def _reveal(self, *, focus: bool = False) -> None:
        self._want_visible = True
        self.show()
        self.raise_()
        self.activateWindow()
        if not focus:
            return
        current = self._pages.currentWidget()
        if current is self._new:
            self._new.focus_first()
        elif current is self._more:
            self._more.focus_edit()
        else:
            self._description.setFocus()

    def _page_id(self) -> str:
        current = self._pages.currentWidget()
        if current is self._new:
            return "new"
        if current is self._more:
            return "more"
        if current is self._mine:
            return "mine"
        return "search"

    def _progress_state(self) -> dict:
        self._capture_draft()
        detail = self._detail
        return {
            "folder": self._folder,
            "page": self._page_id(),
            "description": self._description.text(),
            "labels": self._labels.labels(),
            "clip_started": self._clip_started,
            "blurb": self._more.blurb(),
            "saved": self._saved,
            "origin": self._form_origin,
            "draft": self._draft,
            "detail": None if detail is None else {
                "href": detail.href,
                "bugmirror_key": detail.bugmirror_key,
                "key": detail.key,
                "description": detail.description,
                "reproduction": detail.reproduction,
                "build": detail.build,
                "status": detail.status,
                "priority": detail.priority,
                "labels": list(detail.labels),
                "staff_comments": detail.staff_comments,
                "council_url": detail.council_url,
            },
        }

    def _save_in_progress(self) -> None:
        """Write the report and the resume file. A finished issue is left as it is."""
        if not self._in_progress or not self._folder or not os.path.isdir(self._folder):
            return
        if self._untitled():
            return
        self._rewrite_report()
        try:
            state = self._progress_state()
            save_progress(state)
            write_issue_state(self._folder, state)
        except OSError as exc:
            log.warning("bug_report: could not save the in-progress issue: %s", exc)

    def _leave(self) -> None:
        """Save a titled issue. An untitled one is deleted."""
        if not self._folder or not os.path.isdir(self._folder):
            return
        if self._untitled():
            self._discard_current()
            return
        self._save_in_progress()

    def _untitled(self) -> bool:
        self._capture_draft()
        if self._draft.mode == "contribution":
            return not self._draft.issue_key.strip()
        if self._draft.mode == "new":
            return not self._new.title().strip()
        return True

    def _discard_current(self) -> None:
        folder = self._folder
        self._in_progress = False
        self._folder = ""
        self._loose_clip = ""
        clear_progress()
        self._suspend_rewrite = True
        self._draft = ReportDraft()
        self._more.clear()
        self._new.clear()
        self._suspend_rewrite = False
        if folder:
            discard_issue(folder)

    def _ensure_issue_dir(self) -> bool:
        """Create the issue folder the first time Contribute or Report is chosen."""
        if self._folder and os.path.isdir(self._folder) and not self._saved:
            return True
        self._saved = False
        try:
            self._folder = str(make_issue_dir())
        except OSError as exc:
            self._folder = ""
            self._status.setText(f"Could not create an issue folder ({exc}).")
            return False
        self._in_progress = True
        try:
            write_context(self._folder, self._ctx)
        except OSError as exc:
            log.warning("bug_report: could not write context: %s", exc)
        if self._loose_clip:
            self._store_clip(self._loose_clip)
        return True

    def _show_issue_form(self) -> None:
        if self._draft.mode == "contribution":
            self._more.set_saved(self._saved)
            self._pages.setCurrentWidget(self._more)
            self._more.focus_edit()
            return
        self._draft.mode = "new"
        self._new.restore(self._draft)
        self._new.set_saved(self._saved)
        self._pages.setCurrentWidget(self._new)
        self._new.focus_first()

    def _apply_progress(self, saved: dict | None, *, clear_missing: bool = True) -> bool:
        if not saved:
            return False
        folder = str(saved.get("folder") or "")
        if not folder or not os.path.isdir(folder):
            if clear_missing:
                clear_progress()
            return False
        draft = draft_from_dict(saved.get("draft"))
        draft.description = str(saved.get("description") or draft.description)
        self._folder = folder
        self._clip_started = _as_float(saved.get("clip_started"))
        self._ctx = read_context(folder)
        self._show_facts()
        self._draft = draft
        self._detail = _detail_from(saved.get("detail"))
        self._suspend_rewrite = True
        self._description.blockSignals(True)
        self._description.setText(draft.description)
        self._description.blockSignals(False)
        labels = saved.get("labels")
        self._labels.set_labels(labels if isinstance(labels, list) else [])
        self._more.clear()
        self._new.clear()
        if draft.mode == "contribution":
            self._more.set_issue(draft.issue_key, str(saved.get("blurb") or ""))
            self._more.set_detail(_format_detail(self._detail, []) if self._detail else "")
            self._more.set_note(draft.more_info)
        elif draft.mode == "new":
            self._new.restore(draft)
        page = str(saved.get("page") or "search")
        if page == "new":
            self._pages.setCurrentWidget(self._new)
        elif page == "more":
            self._pages.setCurrentWidget(self._more)
        elif page == "mine":
            self._pages.setCurrentWidget(self._mine)
            self._mine.reload()
        else:
            self._pages.setCurrentIndex(0)
        self._suspend_rewrite = False
        self._in_progress = True
        self._saved = bool(saved.get("saved"))
        origin = str(saved.get("origin") or "search")
        self._form_origin = origin if origin in ("search", "mine") else "search"
        self._more.set_saved(self._saved)
        self._new.set_saved(self._saved)
        return True

    def _save_entry(self) -> None:
        """Keep this issue and return to search. A new issue needs a title first."""
        if self._draft.mode == "new" and not self._new.title().strip():
            self._new.focus_first()
            self._status.setText("Add an issue title. Other players search for this.")
            return
        self._saved = True
        self._finish()

    def _discard_entry(self) -> None:
        """Throw away an issue that has not been saved."""
        if self._saved:
            return
        self._discard_current()
        self.begin_activation()
        self._status.setText("Discarded.")

    def _save_as_new(self) -> None:
        """Write the current form into a new issue folder and leave the saved one as it was."""
        if self._draft.mode == "new" and not self._new.title().strip():
            self._new.focus_first()
            self._status.setText("Add an issue title. Other players search for this.")
            return
        source = self._folder
        try:
            dest = str(make_issue_dir())
        except OSError as exc:
            self._status.setText(f"Could not create an issue folder ({exc}).")
            return
        if source and os.path.isdir(source):
            copy_issue_files(source, dest)
        self._folder = dest
        self._saved = True
        self._in_progress = True
        self._rewrite_report()
        try:
            write_issue_state(dest, self._progress_state())
        except OSError as exc:
            log.warning("bug_report: could not save the new entry: %s", exc)
        self._in_progress = False
        self._folder = ""
        clear_progress()
        self.begin_activation()
        self._status.setText("Saved as a new entry.")

    def _finish(self) -> None:
        """Save a titled issue and return to search. An untitled one is deleted."""
        untitled = bool(self._folder) and self._untitled()
        if untitled:
            self._discard_current()
        elif self._folder and os.path.isdir(self._folder):
            self._rewrite_report()
            try:
                write_issue_state(self._folder, self._progress_state())
            except OSError as exc:
                log.warning("bug_report: could not finish the issue: %s", exc)
            self._in_progress = False
            self._folder = ""
            clear_progress()
        self.begin_activation()
        if untitled:
            self._status.setText("Untitled issue discarded.")
        else:
            self._status.setText(
                "Saved. Capture the clip, then contribute to an issue or report a new one."
            )

    def _show_mine(self) -> None:
        self._leave()
        from core import capture
        self._mine.set_root(capture.REPORTS_ROOT)
        self._mine.reload()
        self._pages.setCurrentWidget(self._mine)
        self._status.setText("Newest issues are at the top. The checkmark saves an issue ID.")

    def _open_saved(self, folder: str) -> None:
        if not self._same_folder(folder):
            self._leave()
        state = state_for_open(folder)
        if state is not None:
            state["origin"] = "mine"
        if state is not None and "saved" not in state:
            progress = load_progress() or {}
            state["saved"] = not self._same_path(str(progress.get("folder") or ""), folder)
        if not state or not self._apply_progress(state, clear_missing=False):
            self._status.setText("That issue folder is missing.")
            return
        self._show_issue_form()
        self._save_in_progress()
        self._status.setText("Opened the saved issue.")

    def _save_listed_id(self, folder: str, issue_id: str) -> None:
        try:
            saved = set_issue_id(folder, issue_id)
        except OSError as exc:
            log.warning("bug_report: could not save the issue id: %s", exc)
            self._status.setText("Could not save the issue ID.")
            return
        if self._same_folder(folder):
            self._draft.issue_id = saved
            self._suspend_rewrite = True
            self._new.set_issue_id(saved)
            self._suspend_rewrite = False
            if self._in_progress:
                self._save_in_progress()
        self._status.setText("Saved the issue ID." if saved else "Cleared the issue ID.")

    def _delete_listed(self, folder: str) -> None:
        if self._same_folder(folder):
            self._in_progress = False
            self._folder = ""
            self._loose_clip = ""
            clear_progress()
            self._suspend_rewrite = True
            self._draft = ReportDraft()
            self._more.clear()
            self._new.clear()
            self._suspend_rewrite = False
        saved = load_progress()
        if saved and self._same_path(str(saved.get("folder") or ""), folder):
            clear_progress()
        discard_issue(folder)
        self._mine.reload()
        self._status.setText("Deleted the issue.")

    def _same_path(self, left: str, right: str) -> bool:
        if not left or not right:
            return False
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))

    def _same_folder(self, folder: str) -> bool:
        return self._same_path(self._folder, folder)

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

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self)
        dialog.exec()

    def _console_key(self) -> str:
        text = str(self._settings.get("console_key") or "`").strip()
        return text[:1] if text else "`"

    def _collect_screenshots(self) -> int:
        """Copy shots taken after the clip, up to this choice of issue."""
        if not self._folder or self._clip_started <= 0:
            return 0
        source = str(self._settings.get("media_dir") or "").strip()
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


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _detail_from(data) -> IssueDetail | None:
    if not isinstance(data, dict):
        return None
    url = str(data.get("council_url") or "")
    key = str(data.get("key") or "")
    if not url and not key:
        return None
    labels = data.get("labels")
    return IssueDetail(
        href=str(data.get("href") or ""),
        bugmirror_key=str(data.get("bugmirror_key") or ""),
        key=key,
        description=str(data.get("description") or ""),
        reproduction=str(data.get("reproduction") or ""),
        build=str(data.get("build") or ""),
        status=str(data.get("status") or ""),
        priority=str(data.get("priority") or ""),
        labels=[str(item) for item in labels] if isinstance(labels, list) else [],
        staff_comments=str(data.get("staff_comments") or ""),
        council_url=url,
    )


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
