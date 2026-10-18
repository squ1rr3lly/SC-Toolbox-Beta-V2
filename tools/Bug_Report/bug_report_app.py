"""SC Toolbox — Bug Report.

On its hotkey: open the tool. Capture clip saves the last 30 seconds.
Screenshots taken after that are copied in when an issue is chosen.

Args: <x> <y> <w> <h> <opacity> <cmd_file>
"""
from __future__ import annotations

import os
import sys

sys.path.insert(
    0,
    os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    ),
)
from shared.app_bootstrap import bootstrap_skill  # noqa: E402

bootstrap_skill(__file__)

import logging  # noqa: E402

log = logging.getLogger(__name__)


def _hotkey() -> str:
    try:
        from shared.hotkey_label import hotkey_label
        return hotkey_label("hotkey_bug_report", "<ctrl>+8")
    except ImportError:
        return "Ctrl+8"


def main() -> None:
    from PySide6.QtCore import QThread, QTimer
    from PySide6.QtWidgets import QApplication

    from shared.config_models import WindowGeometry
    from shared.crash_logger import init_crash_logging
    from shared.data_utils import parse_cli_args
    from shared.qt.ipc_thread import IPCWatcher
    from shared.qt.theme import apply_theme

    from ui.window import BugReportWindow

    init_crash_logging("bug_report")
    args = parse_cli_args(sys.argv[1:], defaults={"w": 980, "h": 720})
    preload = bool(os.environ.pop("SC_TOOLBOX_PRELOAD", ""))

    app = QApplication(sys.argv)
    app.setApplicationName("SC Toolbox - Bug Report")
    apply_theme(app)

    geometry = WindowGeometry(
        x=args["x"], y=args["y"], w=args["w"], h=args["h"], opacity=args["opacity"])
    window = BugReportWindow(
        geometry=geometry, hotkey_text=_hotkey(), cmd_file=args.get("cmd_file"))

    if args.get("cmd_file"):
        watcher = IPCWatcher(args["cmd_file"], poll_ms=150)
        watcher.command_received.connect(window.handle_ipc_command)
        watcher.start(QThread.NormalPriority)

    if not preload:
        QTimer.singleShot(0, window.begin_activation)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
