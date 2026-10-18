"""The two prompts that sit between Bugmirror and Issue Council.

An existing issue asks for the More info note. A new issue walks the fields
Issue Council asks for, in the same order, and Enter adds the next step.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from shared.qt.theme import P

from core.report import ReportDraft

# Closest categories on the new-issue form. The box is editable: Issue Council
# renames these, and a typed value is what gets saved.
CATEGORIES = (
    "AI",
    "Animation",
    "Audio",
    "Character",
    "Crash",
    "Economy",
    "Environment",
    "Exploit / Account Issue",
    "Gameplay",
    "Graphics",
    "Interaction",
    "Inventory",
    "Missions",
    "Persistence",
    "Player",
    "Server",
    "Ships",
    "UI",
    "Vehicles",
    "Weapons",
)

# Official "How bad is it?" answers, with the short meaning from the guide.
SEVERITIES = (
    ("Cosmetic", "Unpleasant, and it does not change how you play."),
    ("Playable", "Affects play, and you can work around it or avoid it."),
    ("Hardly Playable", "Loses progress in this session, and is hard to avoid."),
    ("Critical", "Stops play, or loses long-term progress."),
)

_MAX_STEPS = 10


def _dim(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"color: {P.fg_dim};")
    label.setWordWrap(True)
    return label


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {P.fg_bright}; font-weight: bold;")
    return label


def _plain(placeholder: str, lines: int | None = 3) -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setPlaceholderText(placeholder)
    edit.setStyleSheet(
        f"QPlainTextEdit {{ background-color: rgba(28, 34, 51, 160); color: {P.fg}; "
        f"border: 1px solid rgba(68, 170, 255, 30); border-bottom: 1px solid rgba(68, 170, 255, 60); "
        f"padding: 6px; font-family: Consolas, monospace; font-size: 9pt; }}"
        f"QPlainTextEdit:focus {{ border-color: {P.accent}; }}"
    )
    if lines is not None:
        edit.setFixedHeight(24 + lines * 18)
    return edit


def _submit_shortcut(widget: QWidget, slot) -> None:
    shortcut = QShortcut(QKeySequence("Ctrl+Return"), widget)
    shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    shortcut.activated.connect(slot)


class StepList(QWidget):
    """One action per row. Enter adds the next step, up to the form's limit of 10."""

    changed = Signal()
    jump_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[QLabel, QLineEdit, QWidget]] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._rows_lay = QVBoxLayout()
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(4)
        lay.addLayout(self._rows_lay)
        self._add = QPushButton("Add step")
        self._add.clicked.connect(self._add_and_focus)
        lay.addWidget(self._add, 0, Qt.AlignmentFlag.AlignLeft)
        self.add_step()

    def steps(self) -> list[str]:
        return [edit.text().strip() for _label, edit, _wrap in self._rows if edit.text().strip()]

    def set_steps(self, steps: list[str]) -> None:
        self.clear()
        texts = [step for step in steps if step and step.strip()]
        if not texts:
            return
        self._rows[0][1].setText(texts[0])
        for step in texts[1:_MAX_STEPS]:
            edit = self.add_step(step)
            if edit is None:
                break

    def clear(self) -> None:
        while len(self._rows) > 1:
            self._discard(len(self._rows) - 1)
        if self._rows:
            self._rows[0][1].clear()
        self._renumber()

    def focus_first(self) -> None:
        if self._rows:
            self._rows[0][1].setFocus()

    def add_step(self, text: str = "") -> QLineEdit | None:
        if len(self._rows) >= _MAX_STEPS:
            return None
        label = QLabel()
        label.setFixedWidth(28)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet(f"color: {P.fg_dim};")
        edit = QLineEdit(text)
        edit.setPlaceholderText("One action")
        edit.returnPressed.connect(lambda edit=edit: self._on_return(edit))
        edit.textChanged.connect(lambda _text: self.changed.emit())
        edit.installEventFilter(self)
        row = QWidget()
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(6)
        row_lay.addWidget(label)
        row_lay.addWidget(edit, 1)
        self._rows.append((label, edit, row))
        self._rows_lay.addWidget(row)
        self._renumber()
        return edit

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Backspace
            and isinstance(obj, QLineEdit)
            and not obj.text()
            and len(self._rows) > 1
        ):
            self._remove(obj)
            return True
        return super().eventFilter(obj, event)

    def _on_return(self, edit: QLineEdit) -> None:
        idx = self._index(edit)
        if idx < 0:
            return
        last = idx == len(self._rows) - 1
        if not edit.text().strip():
            if last and idx > 0:
                self.jump_requested.emit()
            return
        if not last:
            self._rows[idx + 1][1].setFocus()
            return
        nxt = self.add_step()
        if nxt is not None:
            nxt.setFocus()
        else:
            self.jump_requested.emit()

    def _add_and_focus(self) -> None:
        edit = self.add_step()
        if edit is not None:
            edit.setFocus()

    def _index(self, edit: QLineEdit) -> int:
        for idx, (_label, row_edit, _wrap) in enumerate(self._rows):
            if row_edit is edit:
                return idx
        return -1

    def _remove(self, edit: QLineEdit) -> None:
        idx = self._index(edit)
        if idx <= 0 and len(self._rows) <= 1:
            return
        if idx < 0:
            return
        self._discard(idx)
        self._renumber()
        self._rows[max(0, idx - 1)][1].setFocus()
        self.changed.emit()

    def _discard(self, idx: int) -> None:
        _label, edit, wrap = self._rows.pop(idx)
        edit.removeEventFilter(self)
        self._rows_lay.removeWidget(wrap)
        wrap.deleteLater()

    def _renumber(self) -> None:
        for index, (label, _edit, _wrap) in enumerate(self._rows, 1):
            label.setText(f"{index}.")
        self._add.setEnabled(len(self._rows) < _MAX_STEPS)


class MoreInfoForm(QWidget):
    """The More info box on an existing Issue Council report."""

    submit_requested = Signal()
    back_requested = Signal()
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._heading = _heading("More info")
        lay.addWidget(self._heading)
        self._blurb = _dim("")
        lay.addWidget(self._blurb)
        self._edit = _plain(
            "Different steps, how often it happens, or a workaround.",
            lines=None,
        )
        self._edit.setMinimumHeight(160)
        self._edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._edit.textChanged.connect(self.changed.emit)
        lay.addWidget(self._edit, 1)
        lay.addWidget(_dim("This is pasted into the More info box. Ctrl+Enter saves and opens the issue."))
        lay.addLayout(self._buttons("Save and open issue"))
        _submit_shortcut(self, self.submit_requested.emit)

    def set_issue(self, key: str, blurb: str) -> None:
        self._heading.setText(f"More info  ·  {key}" if key else "More info")
        self._blurb.setText(blurb)
        self._blurb.setVisible(bool(blurb))

    def more_info(self) -> str:
        return self._edit.toPlainText()

    def clear(self) -> None:
        self._edit.clear()
        self.set_issue("", "")

    def focus_edit(self) -> None:
        self._edit.setFocus()

    def _buttons(self, save_label: str) -> QHBoxLayout:
        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(self.back_requested.emit)
        save = QPushButton(save_label)
        save.clicked.connect(self.submit_requested.emit)
        row.addWidget(back)
        row.addWidget(save)
        row.addStretch(1)
        return row


class NewIssueForm(QWidget):
    """The new-issue fields, in the order Issue Council asks for them."""

    submit_requested = Signal()
    back_requested = Signal()
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        form = QVBoxLayout(inner)
        form.setContentsMargins(0, 0, 8, 0)
        form.setSpacing(6)

        form.addWidget(_dim("Issue title"))
        self._title = QLineEdit()
        self._title.setPlaceholderText("Short title other players will search for")
        self._title.textChanged.connect(lambda _text: self.changed.emit())
        form.addWidget(self._title)

        form.addWidget(_dim("Issue category"))
        self._category = QComboBox()
        self._category.setEditable(True)
        self._category.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._category.addItems(CATEGORIES)
        self._category.setCurrentIndex(-1)
        self._category.setEditText("")
        line = self._category.lineEdit()
        if line is not None:
            line.setPlaceholderText("Closest category, or type one")
            line.textChanged.connect(lambda _text: self.changed.emit())
        form.addWidget(self._category)

        form.addWidget(_dim("How bad is it?"))
        self._severity = QComboBox()
        for name, tip in SEVERITIES:
            self._severity.addItem(name)
            self._severity.setItemData(self._severity.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self._severity.setCurrentIndex(1)
        self._severity.currentIndexChanged.connect(self._on_severity)
        self._on_severity()
        form.addWidget(self._severity)

        form.addWidget(_dim("How did you get there?"))
        form.addWidget(_dim("Press Enter to add the next step. One action per step, up to 10. Backspace on an empty step removes it."))
        self._steps = StepList()
        self._steps.changed.connect(self.changed.emit)
        self._steps.jump_requested.connect(self._happened_focus)
        form.addWidget(self._steps)

        self._happened = _plain("What actually happened")
        self._expected = _plain("What should have happened")
        self._workaround = QLineEdit()
        self._workaround.setPlaceholderText("Optional. Leave blank if you did not find one.")
        for label, widget in (
            ("What happened?", self._happened),
            ("What should have happened?", self._expected),
            ("Did you find a workaround?", self._workaround),
        ):
            form.addWidget(_dim(label))
            form.addWidget(widget)
        self._happened.textChanged.connect(self.changed.emit)
        self._expected.textChanged.connect(self.changed.emit)
        self._workaround.textChanged.connect(lambda _text: self.changed.emit())
        self._title.returnPressed.connect(self._category.setFocus)
        self._workaround.returnPressed.connect(self.submit_requested.emit)
        form.addStretch(1)

        scroll.setWidget(inner)
        root.addWidget(scroll, 1)
        root.addWidget(_dim("Ctrl+Enter saves the report and opens the new-issue page."))
        root.addLayout(self._button_row())
        _submit_shortcut(self, self.submit_requested.emit)

    def title(self) -> str:
        return self._title.text()

    def set_title(self, text: str) -> None:
        self._title.setText(text)
        self._title.setCursorPosition(len(self._title.text()))

    def category(self) -> str:
        return self._category.currentText()

    def severity(self) -> str:
        return self._severity.currentText()

    def steps(self) -> list[str]:
        return self._steps.steps()

    def happened(self) -> str:
        return self._happened.toPlainText()

    def expected(self) -> str:
        return self._expected.toPlainText()

    def workaround(self) -> str:
        return self._workaround.text()

    def apply(self, draft: ReportDraft) -> None:
        draft.title = self.title()
        draft.category = self.category()
        draft.severity = self.severity()
        draft.steps = self.steps()
        draft.happened = self.happened()
        draft.expected = self.expected()
        draft.workaround = self.workaround()

    def clear(self) -> None:
        self._title.clear()
        self._category.setEditText("")
        self._severity.setCurrentIndex(1)
        self._steps.clear()
        self._happened.clear()
        self._expected.clear()
        self._workaround.clear()

    def focus_first(self) -> None:
        self._title.setFocus()
        self._title.setCursorPosition(len(self._title.text()))

    def first_missing(self) -> str:
        """A status line naming the first empty required field, or ''."""
        if not self.title().strip():
            self._title.setFocus()
            return "Add an issue title. Other players search for this."
        if not self.steps():
            self._steps.focus_first()
            return "Add the first step. One action, then Enter for the next."
        if not self.happened().strip():
            self._happened.setFocus()
            return "Say what happened."
        if not self.expected().strip():
            self._expected.setFocus()
            return "Say what should have happened."
        return ""

    def _happened_focus(self) -> None:
        self._happened.setFocus()

    def _on_severity(self, _index: int = 0) -> None:
        idx = self._severity.currentIndex()
        tip = self._severity.itemData(idx, Qt.ItemDataRole.ToolTipRole) or ""
        self._severity.setToolTip(str(tip))
        self.changed.emit()

    def _button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(self.back_requested.emit)
        save = QPushButton("Save and open issue")
        save.clicked.connect(self.submit_requested.emit)
        row.addWidget(back)
        row.addWidget(save)
        row.addStretch(1)
        return row
