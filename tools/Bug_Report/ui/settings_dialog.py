"""Screenshots folder, clips folder, console key, and the displayinfo option."""
from __future__ import annotations

import logging
import os
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from shared.qt.theme import P

from core.capture import REPORTS_ROOT
from core import settings as st

log = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """The folders and keys that used to sit on the main window."""

    def __init__(self, settings: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        lay.addWidget(self._label("Screenshots"))
        self._shots = QLineEdit(str(settings.get("media_dir") or ""))
        self._shots.setPlaceholderText("Folder where screenshots are saved")
        self._shots.editingFinished.connect(self.apply)
        lay.addLayout(self._folder_row(self._shots, "Screenshots"))

        lay.addWidget(self._label("Video clips"))
        self._clips = QLineEdit(str(settings.get("clips_dir") or ""))
        self._clips.setPlaceholderText("Folder where video clips are saved")
        self._clips.editingFinished.connect(self.apply)
        lay.addLayout(self._folder_row(self._clips, "Video clips"))

        lay.addWidget(self._label("Console key"))
        self._console = QLineEdit(str(settings.get("console_key") or "`"))
        self._console.setMaxLength(1)
        self._console.setFixedWidth(36)
        self._console.setToolTip("The key that opens the Star Citizen console. Default is `.")
        self._console.editingFinished.connect(self.apply)
        lay.addWidget(self._console)

        self._displayinfo = QCheckBox("Enable displayinfo before taking clip")
        self._displayinfo.setChecked(bool(settings.get("enable_displayinfo")))
        self._displayinfo.toggled.connect(lambda _checked: self.apply())
        lay.addWidget(self._displayinfo)

        open_issues = QPushButton("Open saved issues directory")
        open_issues.clicked.connect(self._open_issues)
        lay.addWidget(open_issues)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close)

    def accept(self) -> None:
        self.apply()
        super().accept()

    def apply(self) -> None:
        """Write the fields into the shared settings and onto disk."""
        key = self._console.text().strip()[:1] or "`"
        self._console.setText(key)
        self._settings["media_dir"] = self._shots.text().strip()
        self._settings["clips_dir"] = self._clips.text().strip()
        self._settings["console_key"] = key
        self._settings["enable_displayinfo"] = self._displayinfo.isChecked()
        try:
            st.save_settings(self._settings)
        except OSError as exc:
            log.warning("bug_report: could not save settings: %s", exc)

    def _folder_row(self, edit: QLineEdit, title: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        browse = QPushButton("Browse")
        browse.clicked.connect(lambda _checked=False, box=edit, name=title: self._browse(box, name))
        row.addWidget(browse)
        return row

    def _browse(self, edit: QLineEdit, title: str) -> None:
        chosen = QFileDialog.getExistingDirectory(self, title, edit.text().strip())
        if not chosen:
            return
        edit.setText(chosen)
        self.apply()

    def _open_issues(self) -> None:
        root = REPORTS_ROOT
        try:
            root.mkdir(parents=True, exist_ok=True)
            os.startfile(root)  # noqa: S606 - the player's own issues folder
        except OSError as exc:
            log.warning("bug_report: could not open %s: %s", root, exc)

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {P.fg_dim};")
        return label
