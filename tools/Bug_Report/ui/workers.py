"""Background work. The window thread never touches the network, the log, or the keyboard."""
from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)


class _Task(QThread):
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._label = "bug_report"

    def _guard(self, fn) -> None:
        try:
            fn()
        except Exception as exc:  # disk, network, SendInput — reported, never fatal
            log.warning("bug_report: %s failed: %s", self._label, exc)
            self.failed.emit(str(exc))


class SessionTask(_Task):
    """Read the newest Game.log into an issue folder. No keys are sent."""

    context_ready = Signal(object)

    def __init__(self, folder: str, parent=None) -> None:
        super().__init__(parent)
        self._folder = folder
        self._label = "session"

    def run(self) -> None:
        def work() -> None:
            from core.session import load_session, write_context

            ctx = load_session()
            write_context(self._folder, ctx)
            self.context_ready.emit((self._folder, ctx))

        self._guard(work)


class ClipTask(_Task):
    """Win+Alt+G, then copy the Game Bar file into the issue folder."""

    ready = Signal(object)
    clip_ready = Signal(object)

    def __init__(self, folder: str, parent=None) -> None:
        super().__init__(parent)
        self._folder = folder
        self._label = "clip"

    def run(self) -> None:
        from pathlib import Path

        from core.capture import copy_new_clip, request_clip

        shown = False
        try:
            result = request_clip()
            result.folder = self._folder
            self.ready.emit(result)
            shown = True
            path = ""
            if result.watch_clip:
                path = copy_new_clip(
                    Path(self._folder), result.clip_before, result.clip_started,
                ) or ""
            self.clip_ready.emit((self._folder, path))
        except Exception as exc:  # disk, SendInput — reported, never fatal
            log.warning("bug_report: clip failed: %s", exc)
            if not shown:
                self.failed.emit(str(exc))
            else:
                self.clip_ready.emit((self._folder, ""))


class SearchTask(_Task):
    succeeded = Signal(object)

    def __init__(self, text: str, labels: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._labels = list(labels or [])
        self._label = "search"

    def run(self) -> None:
        def work() -> None:
            from core.bugmirror import search
            self.succeeded.emit(search(self._text, self._labels))

        self._guard(work)


class DetailTask(_Task):
    succeeded = Signal(object)

    def __init__(self, href: str, parent=None) -> None:
        super().__init__(parent)
        self._href = href
        self._label = "detail"

    def run(self) -> None:
        def work() -> None:
            from core.bugmirror import load_detail
            self.succeeded.emit(load_detail(self._href))

        self._guard(work)


class LabelbookTask(_Task):
    """Download the shared label workbook. The window thread never does this."""

    succeeded = Signal(str)

    def __init__(self, dest: str, parent=None) -> None:
        super().__init__(parent)
        self._dest = dest
        self._label = "labels"

    def run(self) -> None:
        def work() -> None:
            from core.labels import download_workbook
            download_workbook(self._dest)
            self.succeeded.emit(self._dest)

        self._guard(work)


class LabelCountTask(_Task):
    """How many Bugmirror issues carry one selected label."""

    succeeded = Signal(str, int)

    def __init__(self, label: str, tokens: list[str], parent=None) -> None:
        super().__init__(parent)
        self._name = label
        self._tokens = list(tokens)
        self._label = "label count"

    def run(self) -> None:
        def work() -> None:
            from core.bugmirror import label_total

            total = 0
            found = False
            for token in self._tokens:
                try:
                    count = label_total(token)
                except OSError:
                    continue
                if count is None:
                    continue
                found = True
                total += count
            if found:
                self.succeeded.emit(self._name, total)

        self._guard(work)


class DebugOffTask(_Task):
    finished_ok = Signal(str)

    def __init__(self, console_key: str, parent=None) -> None:
        super().__init__(parent)
        self._console_key = console_key
        self._label = "debug off"

    def run(self) -> None:
        def work() -> None:
            from core.capture import disable_debug
            self.finished_ok.emit(disable_debug(self._console_key))

        self._guard(work)
