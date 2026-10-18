"""The list of issue folders, newest first, with an Issue ID on each row."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QStyle, QVBoxLayout,
    QWidget,
)

from shared.qt.theme import P

from core.issues import IssueRecord, list_issues


class MyIssuesPage(QWidget):
    """Every saved issue. The checkmark stores the Issue ID for that row."""

    open_requested = Signal(str)
    id_saved = Signal(str, str)
    delete_requested = Signal(str)
    back_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._root: Path | None = None
        self._edits: dict[str, QLineEdit] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        back = QPushButton("Back")
        back.clicked.connect(self.back_requested.emit)
        root.addWidget(back)
        self._empty = QLabel("No issues yet.")
        self._empty.setStyleSheet(f"color: {P.fg_dim}; padding: 8px;")
        root.addWidget(self._empty)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        holder = QWidget()
        self._rows = QVBoxLayout(holder)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(6)
        self._rows.addStretch(1)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

    def set_root(self, root: Path | None) -> None:
        self._root = root

    def reload(self) -> None:
        self.show_issues(list_issues(self._root))

    def show_issues(self, records: list[IssueRecord]) -> None:
        while self._rows.count() > 1:
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._edits = {}
        self._empty.setVisible(not records)
        for record in records:
            self._rows.insertWidget(self._rows.count() - 1, self._row(record))

    def issue_id_text(self, folder: str) -> str:
        edit = self._edits.get(folder)
        return edit.text() if edit is not None else ""

    def _row(self, record: IssueRecord) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        when = QLabel(record.when)
        when.setStyleSheet(f"color: {P.fg_dim};")
        when.setFixedWidth(148)
        lay.addWidget(when)
        open_btn = QPushButton(record.title)
        open_btn.setToolTip("Open issue")
        open_btn.setStyleSheet(
            f"QPushButton {{ text-align: left; padding: 8px 10px; color: {P.fg}; }}"
        )
        open_btn.clicked.connect(lambda _checked=False, folder=record.folder: self.open_requested.emit(folder))
        lay.addWidget(open_btn, 1)
        edit = QLineEdit(record.issue_id)
        edit.setPlaceholderText("Issue ID")
        edit.setFixedWidth(160)
        self._edits[record.folder] = edit
        lay.addWidget(edit)
        save = QPushButton()
        save.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        save.setToolTip("Save issue ID")
        save.setFixedWidth(36)
        save.clicked.connect(lambda _checked=False, folder=record.folder: self._save(folder))
        lay.addWidget(save)
        delete = QPushButton("✕")
        delete.setToolTip("Delete issue")
        delete.setFixedWidth(36)
        delete.clicked.connect(
            lambda _checked=False, folder=record.folder, title=record.title: self._confirm_delete(folder, title),
        )
        lay.addWidget(delete)
        return row

    def _save(self, folder: str) -> None:
        self.id_saved.emit(folder, self.issue_id_text(folder))

    def _confirm_delete(self, folder: str, title: str) -> None:
        answer = QMessageBox.question(
            self,
            "Delete issue",
            f'Delete "{title}"? This cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.delete_requested.emit(folder)
