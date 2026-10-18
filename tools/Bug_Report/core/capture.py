"""Clip, console commands, and a screenshot of the Star Citizen window.

Keys are sent only while ``StarCitizen.exe`` is the foreground process, and
only to that window. Nothing here runs at import time.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

REPORTS_ROOT = Path.home() / ".sctoolbox" / "bug_reports"

_CLIP_EXTS = {".mp4", ".mkv", ".avi"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
# Files copied from the player's screenshots-and-videos folder.
MEDIA_EXTS = _CLIP_EXTS | {".webm", ".mov", ".wmv"} | _IMAGE_EXTS
_CLIP_TIMEOUT_S = 15.0
_AFTER_CHORD_S = 0.25
_AFTER_FOCUS_S = 0.15
_AFTER_CONSOLE_OPEN_S = 0.35
_BETWEEN_COMMANDS_S = 0.12
_AFTER_CONSOLE_CLOSE_S = 0.5

VK_RETURN = 0x0D
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_G = 0x47
VK_OEM_3 = 0xC0  # grave, the default Star Citizen console key
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001

COMMANDS_ON = ("r_displayinfo 3", "r_displaysessioninfo 1")
COMMANDS_OFF = ("r_displayinfo 0", "r_displaysessioninfo 0")

SC_PROCESS = "StarCitizen.exe"


@dataclass
class CaptureResult:
    folder: str
    screenshot: bool = False
    game_was_foreground: bool = False
    notes: list[str] = field(default_factory=list)
    # Set when Win+Alt+G was sent, so the caller can wait for the video file.
    watch_clip: bool = False
    clip_before: set[str] = field(default_factory=set)
    clip_started: float = 0.0
    copied_media: list[str] = field(default_factory=list)


def make_issue_dir(root: Path | None = None, when: datetime | None = None) -> Path:
    """``~/.sctoolbox/bug_reports/<YYYYMMDD-HHMMSS>/``, with a suffix on a clash."""
    root = root or REPORTS_ROOT
    when = when or datetime.now()
    stamp = when.strftime("%Y%m%d-%H%M%S")
    path = root / stamp
    n = 2
    while path.exists():
        path = root / f"{stamp}-{n}"
        n += 1
    path.mkdir(parents=True)
    return path


def captures_directories() -> list[Path]:
    """Folders Xbox Game Bar may write a clip into. Missing ones are skipped."""
    candidates: list[Path] = []
    videos = _videos_folder()
    if videos is not None:
        candidates.append(videos / "Captures")
    home = Path.home()
    candidates.append(home / "Videos" / "Captures")
    candidates.append(home / "OneDrive" / "Videos" / "Captures")
    candidates.extend(_game_bar_folders())
    found: list[Path] = []
    seen: set[str] = set()
    for cand in candidates:
        try:
            if not cand.is_dir():
                continue
            key = str(cand.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        found.append(cand)
    return found


def list_clips(dirs: list[Path]) -> set[str]:
    """Resolved paths of video files already in *dirs*."""
    found: set[str] = set()
    for folder in dirs:
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix.lower() in _CLIP_EXTS:
                try:
                    found.add(str(entry.resolve()))
                except OSError:
                    continue
    return found


def pick_new_clip(dirs: list[Path], before: set[str], started: float) -> Path | None:
    """The newest video that was not in *before* and was written at or after *started*."""
    newest: Path | None = None
    newest_mtime = -1.0
    for folder in dirs:
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix.lower() not in _CLIP_EXTS:
                continue
            try:
                resolved = str(entry.resolve())
                if resolved in before:
                    continue
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime + 2 < started:
                continue
            if mtime >= newest_mtime:
                newest = entry
                newest_mtime = mtime
    return newest


def console_vk(key: str) -> int:
    """Virtual key for the console bind. Grave stays OEM_3, the key left of 1."""
    ch = (key or "`")[:1]
    if ch in ("`", "~"):
        return VK_OEM_3
    try:
        import ctypes
        vk = ctypes.windll.user32.VkKeyScanW(ord(ch))
    except (AttributeError, OSError, ValueError):
        return VK_OEM_3
    if vk == -1:
        return VK_OEM_3
    return int(vk) & 0xFF


def game_is_foreground() -> bool:
    """True when the foreground window belongs to StarCitizen.exe.

    Same check Suit Mk2's eyes use: anything else, including a failure to
    read the window, is not the game, so keys are not sent.
    """
    name = _foreground_process_name()
    return name is not None and name.lower() == SC_PROCESS.lower()


def request_clip(wait_s: float = 2.0) -> CaptureResult:
    """Send Win+Alt+G once Star Citizen is in front.

    ``clip_started`` is the moment the clip was requested. Screenshots taken
    after that are the ones to keep.
    """
    result = CaptureResult(folder="")
    deadline = time.time() + wait_s
    while not game_is_foreground() and time.time() < deadline:
        time.sleep(0.05)
    if not game_is_foreground():
        result.notes.append(
            "Star Citizen was not the window in front, so the clip was not saved."
        )
        return result
    result.game_was_foreground = True
    result.clip_before = list_clips(captures_directories())
    result.clip_started = time.time()
    try:
        _press_game_bar()
    except OSError as exc:
        result.notes.append(f"The Game Bar hotkey could not be sent ({exc}).")
        log.warning("bug_report: game bar chord failed: %s", exc)
        result.clip_started = 0.0
        return result
    result.watch_clip = True
    return result


def take_evidence(folder: Path, console_key: str) -> CaptureResult:
    """Request the clip, enable the debug cvars, and screenshot the game window.

    The clip file itself is copied later by :func:`copy_new_clip`; Game Bar
    needs a few seconds to finish writing it.
    """
    result = CaptureResult(folder=str(folder))
    if not game_is_foreground():
        result.notes.append(
            "Star Citizen was not the window in front, so no keys were sent "
            "and no clip or screenshot was taken."
        )
        return result
    result.game_was_foreground = True
    hwnd = _find_game_window()
    if not hwnd:
        result.notes.append("Star Citizen is in front, but its window could not be found.")
        return result
    result.clip_before = list_clips(captures_directories())
    result.clip_started = time.time()
    try:
        _press_game_bar()
    except OSError as exc:
        result.notes.append(f"The Game Bar hotkey could not be sent ({exc}).")
        log.warning("bug_report: game bar chord failed: %s", exc)
    else:
        result.watch_clip = True
    time.sleep(_AFTER_CHORD_S)
    if not _focus(hwnd) or not game_is_foreground():
        result.notes.append(
            "The game was no longer in front, so the console was not opened."
        )
        return result
    try:
        _console_commands(COMMANDS_ON, console_vk(console_key))
    except OSError as exc:
        result.notes.append(f"The console commands could not be sent ({exc}).")
        log.warning("bug_report: console commands failed: %s", exc)
        return result
    time.sleep(_AFTER_CONSOLE_CLOSE_S)
    dest = folder / "screenshot.png"
    if _grab_window(hwnd, dest):
        result.screenshot = True
    else:
        result.notes.append("The screenshot of the game window could not be saved.")
    return result


def copy_recent_media(folder: Path, source: str | Path, minutes: float,
                      now: float | None = None) -> list[str]:
    """Copy screenshots and videos from *source* that are newer than *minutes* ago.

    Names already in the issue folder are kept. The new file gets a ``-2`` suffix.
    """
    moment = time.time() if now is None else now
    cutoff = moment - max(0.0, float(minutes)) * 60.0

    def include(entry: Path) -> bool:
        return entry.suffix.lower() in MEDIA_EXTS and entry.stat().st_mtime >= cutoff

    return _copy_matching(folder, source, include)


def copy_screenshots_since(folder: Path, source: str | Path, started: float,
                           now: float | None = None) -> list[str]:
    """Copy images from *source* saved between *started* and *now*."""
    if started <= 0:
        return []
    moment = time.time() if now is None else now

    def include(entry: Path) -> bool:
        if entry.suffix.lower() not in _IMAGE_EXTS:
            return False
        mtime = entry.stat().st_mtime
        return started - 1 <= mtime <= moment + 1

    return _copy_matching(folder, source, include)


def _copy_matching(folder: Path, source: str | Path, include) -> list[str]:
    text = str(source or "").strip()
    if not text:
        return []
    root = Path(text)
    try:
        if not root.is_dir():
            return []
        if root.resolve() == folder.resolve():
            return []
        entries = list(root.iterdir())
    except OSError as exc:
        log.warning("bug_report: could not read %s: %s", root, exc)
        return []
    copied: list[str] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if entry.resolve().parent == folder.resolve():
                continue
            if not include(entry):
                continue
        except OSError:
            continue
        dest = _unused_name(folder, entry.name)
        try:
            shutil.copy2(entry, dest)
        except OSError as exc:
            log.warning("bug_report: could not copy %s: %s", entry, exc)
            continue
        copied.append(dest.name)
    return copied


def _unused_name(folder: Path, name: str) -> Path:
    dest = folder / name
    if not dest.exists():
        return dest
    stem = Path(name).stem
    suffix = Path(name).suffix
    number = 2
    while True:
        dest = folder / f"{stem}-{number}{suffix}"
        if not dest.exists():
            return dest
        number += 1


def find_new_clip(before: set[str], started: float,
                  timeout: float = _CLIP_TIMEOUT_S) -> str | None:
    """Wait until a new Game Bar video finishes writing. None if none appears."""
    dirs = captures_directories()
    if not dirs:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        clip = pick_new_clip(dirs, before, started)
        if clip is not None and _size_settled(clip):
            return str(clip)
        time.sleep(0.4)
    return None


def adopt_clip(folder: Path, source: str) -> str | None:
    """Copy a Game Bar video into *folder*. A file already there is kept."""
    src = Path(source)
    try:
        if not src.is_file():
            return None
        if src.resolve().parent == folder.resolve():
            return str(src)
    except OSError:
        return None
    dest = folder / f"clip{src.suffix.lower()}"
    if dest.exists():
        dest = _unused_name(folder, dest.name)
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        log.warning("bug_report: could not copy %s: %s", src, exc)
        return None
    return str(dest)


def copy_new_clip(folder: Path, before: set[str], started: float,
                  timeout: float = _CLIP_TIMEOUT_S) -> str | None:
    """Wait for a new Game Bar video and copy it into *folder*. None if none appears."""
    found = find_new_clip(before, started, timeout)
    if not found:
        return None
    return adopt_clip(folder, found)


def disable_debug(console_key: str) -> str:
    """Send the cvars back to 0, only while the game is in front."""
    hwnd = _find_game_window()
    if not hwnd:
        return "Star Citizen's window was not found, so the debug overlay was left as it is."
    if not _focus(hwnd) or not game_is_foreground():
        return "Star Citizen was not in front, so the debug overlay was left as it is."
    try:
        _console_commands(COMMANDS_OFF, console_vk(console_key))
    except OSError as exc:
        log.warning("bug_report: could not clear debug cvars: %s", exc)
        return "The console commands to hide the debug overlay could not be sent."
    return "Debug overlay turned off."


# ── windows input ────────────────────────────────────────────────────────────

def _press_game_bar() -> None:
    """Win+Alt+G. Key-ups are sent even when the hold is interrupted."""
    _send((_down(VK_LWIN, extended=True), _down(VK_MENU), _down(VK_G)))
    try:
        time.sleep(0.08)
    finally:
        _send((_up(VK_G), _up(VK_MENU), _up(VK_LWIN, extended=True)))


def _console_commands(commands: tuple[str, ...], vk: int) -> None:
    _tap(vk)
    time.sleep(_AFTER_CONSOLE_OPEN_S)
    for command in commands:
        _type_text(command)
        _tap(VK_RETURN)
        time.sleep(_BETWEEN_COMMANDS_S)
    _tap(vk)


def _focus(hwnd: int) -> bool:
    """Bring *hwnd* to the foreground. False when that cannot be asked."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    except (AttributeError, OSError) as exc:
        log.warning("bug_report: could not focus the game: %s", exc)
        return False
    time.sleep(_AFTER_FOCUS_S)
    return True


def _grab_window(hwnd: int, dest: Path) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
        import mss
        from PIL import Image
    except ImportError as exc:
        log.warning("bug_report: screenshot libraries missing: %s", exc)
        return False
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return False
    box = {"left": int(rect.left), "top": int(rect.top), "width": int(width), "height": int(height)}
    try:
        with mss.mss() as sct:
            shot = sct.grab(box)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
            image.save(dest, format="PNG")
    except (OSError, ValueError, mss.exception.ScreenShotError) as exc:
        log.warning("bug_report: screenshot failed: %s", exc)
        return False
    return dest.is_file()


def _find_game_window() -> int:
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return 0
    user32 = ctypes.windll.user32
    found: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _visit(hwnd, _lparam):  # pragma: no cover - needs a live window
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _process_image_name(int(pid.value))
        if not name or os.path.basename(name).lower() != SC_PROCESS.lower():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        if area > 0:
            found.append((area, int(hwnd)))
        return True

    user32.EnumWindows(_visit, 0)
    if not found:
        return 0
    found.sort()
    return found[-1][1]


def _foreground_process_name() -> str | None:
    """Executable name of the foreground window, or None. Mirrors Suit Mk2's eyes."""
    try:
        import ctypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return _basename(_process_image_name(int(pid.value)))
    except (AttributeError, OSError, ValueError) as exc:
        log.debug("bug_report: foreground window unreadable: %s", exc)
        return None


def _process_image_name(pid: int) -> str | None:
    if not pid:
        return None
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(len(buf))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            return buf.value
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return None


def _basename(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.basename(path)


def _send(inputs) -> None:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    arr = (_INPUT * len(inputs))(*inputs)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    sent = user32.SendInput(len(arr), arr, ctypes.sizeof(_INPUT))
    if sent != len(arr):
        raise OSError(f"SendInput delivered {sent} of {len(arr)}")


def _down(vk: int, extended: bool = False):
    return _key(vk, up=False, extended=extended)


def _up(vk: int, extended: bool = False):
    return _key(vk, up=True, extended=extended)


def _tap(vk: int) -> None:
    _send((_down(vk), _up(vk)))


def _type_text(text: str) -> None:
    inputs = []
    for ch in text:
        inputs.append(_unicode(ch, up=False))
        inputs.append(_unicode(ch, up=True))
    _send(tuple(inputs))


def _key(vk: int, up: bool, extended: bool = False):
    flags = 0
    if up:
        flags |= KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = _INPUT()
    inp.type = 1
    inp.ki = _KEYBDINPUT(vk, 0, flags, 0, 0)
    return inp


def _unicode(ch: str, up: bool):
    flags = KEYEVENTF_UNICODE
    if up:
        flags |= KEYEVENTF_KEYUP
    inp = _INPUT()
    inp.type = 1
    inp.ki = _KEYBDINPUT(0, ord(ch), flags, 0, 0)
    return inp


def _input_types():
    import ctypes
    from ctypes import wintypes
    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        )

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        )

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = (
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        )

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT))

        _anonymous_ = ("u",)
        _fields_ = (("type", wintypes.DWORD), ("u", _U))

    return KEYBDINPUT, INPUT


_KEYBDINPUT, _INPUT = _input_types()


def _size_settled(path: Path) -> bool:
    try:
        first = path.stat().st_size
    except OSError:
        return False
    if first <= 0:
        return False
    time.sleep(0.4)
    try:
        second = path.stat().st_size
    except OSError:
        return False
    return second == first and second > 0


def _videos_folder() -> Path | None:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            raw, _ = winreg.QueryValueEx(key, "My Video")
    except OSError:
        return None
    if not isinstance(raw, str) or not raw:
        return None
    return Path(os.path.expandvars(raw))


def _game_bar_folders() -> list[Path]:
    """A captures path recorded by Xbox Game Bar, when its settings name one."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    root = Path(local) / "Packages" / "Microsoft.XboxGamingOverlay_8wekyb3d8bbwe" / "LocalState"
    if not root.is_dir():
        return []
    found: list[Path] = []
    try:
        files = [p for p in root.iterdir() if p.suffix.lower() in (".json", ".txt") and p.stat().st_size < 1_000_000]
    except OSError:
        return []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token in text.replace("\\\\", "\\").split('"'):
            if "Captures" not in token:
                continue
            cand = Path(token)
            if cand.is_dir():
                found.append(cand)
    return found
