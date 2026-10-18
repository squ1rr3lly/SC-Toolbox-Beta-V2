"""Pick Bugmirror labels from the workbook, one column at a time."""
from __future__ import annotations

import logging

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from shared.qt.theme import P

from core.labels import (
    BUNDLED, LabelCatalog, LabelSheet, cache_path, label_tokens, load_catalog, narrow,
)

log = logging.getLogger(__name__)


def _filter_line(detail: str, total: int | None) -> str:
    if not detail:
        return ""
    if total is None:
        return detail
    noun = "issue" if total == 1 else "issues"
    return f"{detail}  {total:,} {noun}"


class _LabelBox(QComboBox):
    """The label list. Opening it clears the description until a label is chosen."""

    opened = Signal()

    def showPopup(self) -> None:
        self.opened.emit()
        super().showPopup()


class LabelPicker(QWidget):
    """Tab, then each column left to right. Included labels join the search."""

    changed = Signal()

    def __init__(self, settings: dict, parent=None, fetch: bool = True) -> None:
        super().__init__(parent)
        self._settings = settings
        self._catalog = LabelCatalog()
        self._picks: list[str] = []
        self._chosen: list[str] = []
        self._sheet: LabelSheet | None = None
        self._fetch_task = None
        self._count_issues = fetch
        self._totals: dict[str, int] = {}
        self._counting: set[str] = set()
        self._shown_label = ""
        self._shown_detail = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._cascade = QHBoxLayout()
        self._sheet_box = QComboBox()
        self._sheet_box.setMinimumWidth(140)
        self._sheet_box.currentIndexChanged.connect(self._sheet_changed)
        self._cascade.addWidget(self._sheet_box)
        self._section_box = QComboBox()
        self._section_box.setMinimumWidth(160)
        self._section_box.hide()
        self._section_box.currentIndexChanged.connect(self._section_changed)
        self._cascade.addWidget(self._section_box)
        self._steps = QHBoxLayout()
        self._cascade.addLayout(self._steps)
        self._boxes: list[QComboBox] = []
        self._include = QPushButton("Include")
        self._include.setEnabled(False)
        self._include.clicked.connect(self._include_label)
        self._cascade.addWidget(self._include)
        root.addLayout(self._cascade)

        self._description = QLabel("")
        self._description.setWordWrap(True)
        self._description.setStyleSheet("color: #ffffff;")
        root.addWidget(self._description)

        self._filters = QLabel("")
        self._filters.setWordWrap(True)
        self._filters.setStyleSheet(f"color: {P.fg};")
        root.addWidget(self._filters)

        self._chips = QHBoxLayout()
        root.addLayout(self._chips)
        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {P.fg_dim};")
        root.addWidget(self._hint)

        saved = cache_path()
        if saved.is_file():
            self._show_catalog(load_catalog(saved))
        elif BUNDLED.is_file():
            self._show_catalog(load_catalog(BUNDLED))
        else:
            self._hint.setText("Downloading the label workbook…")
        if fetch:
            self._start_fetch()

    def labels(self) -> list[str]:
        return list(self._chosen)

    def search_labels(self) -> list[str]:
        """Included labels, plus the label chosen in the dropdowns."""
        found = list(self._chosen)
        table = self._active_table()
        if table is None:
            return found
        for token in label_tokens(narrow(table, self._picks).label):
            if token not in found:
                found.append(token)
        return found

    def clear_chosen(self) -> None:
        if not self._chosen:
            return
        self._chosen.clear()
        self._rebuild_chips()
        self.changed.emit()

    def _start_fetch(self) -> None:
        from ui.workers import LabelbookTask

        if self._catalog.sheets:
            self._hint.setText("Updating labels…")
        task = LabelbookTask(str(cache_path()), self)
        task.succeeded.connect(self._on_workbook)
        task.failed.connect(self._on_workbook_failed)
        self._fetch_task = task
        task.start()

    def _on_workbook(self, path: str) -> None:
        self._show_catalog(load_catalog(path))

    def _on_workbook_failed(self, _message: str) -> None:
        if self._catalog.sheets:
            self._hint.setText("")
            return
        self._hint.setText("Could not download the label workbook.")
        self._rebuild_boxes(None)

    def _show_catalog(self, catalog: LabelCatalog) -> None:
        previous = self._sheet.name if self._sheet is not None else ""
        self._catalog = catalog
        self._sheet_box.blockSignals(True)
        self._sheet_box.clear()
        for sheet in catalog.sheets:
            self._sheet_box.addItem(sheet.name)
        if previous:
            found = self._sheet_box.findText(previous)
            if found >= 0:
                self._sheet_box.setCurrentIndex(found)
        self._sheet_box.blockSignals(False)
        if catalog.sheets:
            self._hint.setText("")
            self._sheet_changed()
        else:
            self._sheet = None
            self._section_box.hide()
            self._hint.setText("That workbook has no label sheets.")
            self._rebuild_boxes(None)

    def _sheet_changed(self, _index: int = 0) -> None:
        name = self._sheet_box.currentText()
        self._sheet = self._catalog.sheet(name)
        self._picks = []
        self._section_box.blockSignals(True)
        self._section_box.clear()
        if self._sheet is not None and self._sheet.sections:
            self._section_box.addItem("Section")
            self._section_box.setItemData(0, "")
            for section in self._sheet.sections:
                self._section_box.addItem(section.name, section.name)
            self._section_box.show()
            self._section_box.blockSignals(False)
            self._rebuild_boxes(None)
            return
        self._section_box.hide()
        self._section_box.blockSignals(False)
        self._rebuild_boxes(self._sheet)

    def _section_changed(self, _index: int = 0) -> None:
        self._picks = []
        QTimer.singleShot(0, self._apply_picks)

    def _active_table(self) -> LabelSheet | None:
        if self._sheet is None:
            return None
        if not self._sheet.sections:
            return self._sheet
        name = self._section_box.currentData()
        for section in self._sheet.sections:
            if section.name == name:
                return section
        return None

    def _rebuild_boxes(self, sheet: LabelSheet | None) -> None:
        while self._steps.count():
            item = self._steps.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._boxes = []
        self._include.setEnabled(False)
        if sheet is None:
            self._description.setText("")
            self._show_filter("", "")
            return
        state = narrow(sheet, self._picks)
        self._description.setText(state.description if state.label else "")
        self._show_filter(state.detail if state.label else "", state.label)
        for index, (header, values, chosen) in enumerate(state.steps):
            is_label = header.lower() in {"label", "labels"}
            box = _LabelBox() if is_label else QComboBox()
            box.setMinimumWidth(160)
            box.blockSignals(True)
            choices = self._choices(sheet, index, values, header)
            if len(choices) == 1:
                box.addItem(choices[0][0], choices[0][1])
            else:
                box.addItem("Filter" if is_label else header)
                box.setItemData(0, "")
                for text, value in choices:
                    box.addItem(text, value)
                if chosen:
                    found = box.findData(chosen)
                    if found >= 0:
                        box.setCurrentIndex(found)
            box.blockSignals(False)
            if isinstance(box, _LabelBox):
                box.opened.connect(self._description.clear)
            box.currentIndexChanged.connect(lambda _i, slot=index: self._picked(slot))
            self._steps.addWidget(box)
            self._boxes.append(box)
        pending = [token for token in label_tokens(state.label) if token not in self._chosen]
        self._include.setEnabled(bool(pending))

    def _choices(self, sheet: LabelSheet, index: int, values: list[str], header: str) -> list[tuple[str, str]]:
        """Visible text and stored value. A label row shows its filter, never the label token."""
        if header.lower() not in {"label", "labels"}:
            return [(value, value) for value in values]
        # Earlier columns may already be chosen without being stored in ``_picks``.
        prefix = [chosen for _, _, chosen in narrow(sheet, self._picks).steps[:index] if chosen]
        choices: list[tuple[str, str]] = []
        for value in values:
            detail = narrow(sheet, prefix + [value]).detail
            if detail:
                choices.append((detail, value))
        return choices

    def _show_filter(self, detail: str, label: str) -> None:
        """The filter name, with how many issues carry that label once known."""
        self._shown_detail = detail
        self._shown_label = label
        self._filters.setText(_filter_line(detail, self._totals.get(label) if label else None))
        if label and self._count_issues and label not in self._counting and label not in self._totals:
            self._start_count(label)

    def _start_count(self, label: str) -> None:
        from ui.workers import LabelCountTask

        self._counting.add(label)
        task = LabelCountTask(label, label_tokens(label), self)
        task.succeeded.connect(self._on_count)
        task.finished.connect(lambda name=label: self._counting.discard(name))
        self._count_task = task
        task.start()

    def _on_count(self, label: str, total: int) -> None:
        self._totals[label] = total
        if label == self._shown_label:
            self._filters.setText(_filter_line(self._shown_detail, total))

    def _picked(self, slot: int) -> None:
        if slot >= len(self._boxes):
            return
        values: list[str] = []
        for index, box in enumerate(self._boxes):
            if index > slot:
                break
            value = box.currentData()
            if not value:
                break
            values.append(str(value))
        self._picks = values
        QTimer.singleShot(0, self._apply_picks)

    def _apply_picks(self) -> None:
        self._rebuild_boxes(self._active_table())

    def _include_label(self) -> None:
        table = self._active_table()
        if table is None:
            return
        tokens = [token for token in label_tokens(narrow(table, self._picks).label) if token not in self._chosen]
        if not tokens:
            return
        self._chosen.extend(tokens)
        self._rebuild_chips()
        self._include.setEnabled(False)
        self.changed.emit()

    def _rebuild_chips(self) -> None:
        while self._chips.count():
            item = self._chips.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for label in self._chosen:
            button = QPushButton(f"{label}  ×")
            button.clicked.connect(lambda _checked=False, name=label: self._remove(name))
            self._chips.addWidget(button)
        self._chips.addStretch(1)

    def _remove(self, label: str) -> None:
        if label in self._chosen:
            self._chosen.remove(label)
            self._rebuild_chips()
            table = self._active_table()
            if table is not None:
                pending = [token for token in label_tokens(narrow(table, self._picks).label) if token not in self._chosen]
                self._include.setEnabled(bool(pending))
            self.changed.emit()
