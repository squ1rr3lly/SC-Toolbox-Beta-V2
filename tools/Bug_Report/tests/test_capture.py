"""Issue folders and clip selection. Nothing here presses a key."""
from __future__ import annotations

import ctypes
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from core.capture import (
    _INPUT, console_vk, copy_recent_media, copy_screenshots_since, make_issue_dir, pick_new_clip,
)
from core.settings import clamp_media_minutes


def test_issue_folder_is_timestamped(tmp_path: Path) -> None:
    when = datetime(2026, 10, 17, 16, 4, 5)
    first = make_issue_dir(tmp_path, when)
    second = make_issue_dir(tmp_path, when)
    assert first.name == "20261017-160405"
    assert second.name == "20261017-160405-2"
    assert first.is_dir() and second.is_dir()


def test_pick_new_clip_ignores_what_was_already_there(tmp_path: Path) -> None:
    old = tmp_path / "Star Citizen old.mp4"
    old.write_bytes(b"old")
    started = old.stat().st_mtime - 10
    before = {str(old.resolve())}
    assert pick_new_clip([tmp_path], before, started) is None
    fresh = tmp_path / "Star Citizen new.mp4"
    fresh.write_bytes(b"new")
    chosen = pick_new_clip([tmp_path], before, started)
    assert chosen == fresh


def test_recent_screenshots_and_videos_are_copied(tmp_path: Path) -> None:
    source = tmp_path / "shots"
    issue = tmp_path / "issue"
    source.mkdir()
    issue.mkdir()
    (issue / "screenshot.png").write_bytes(b"game")
    now = time.time()
    recent_shot = source / "shot.jpg"
    recent_shot.write_bytes(b"jpg")
    recent_clip = source / "fight.mp4"
    recent_clip.write_bytes(b"mp4")
    own_shot = source / "screenshot.png"
    own_shot.write_bytes(b"user")
    old = source / "old.png"
    old.write_bytes(b"old")
    note = source / "notes.txt"
    note.write_bytes(b"no")
    os.utime(recent_shot, (now, now - 60))
    os.utime(recent_clip, (now, now - 5 * 60))
    os.utime(own_shot, (now, now - 30))
    os.utime(old, (now, now - 21 * 60))
    copied = copy_recent_media(issue, source, 20, now=now)
    assert sorted(copied) == ["fight.mp4", "screenshot-2.png", "shot.jpg"]
    assert (issue / "shot.jpg").read_bytes() == b"jpg"
    assert (issue / "fight.mp4").read_bytes() == b"mp4"
    assert (issue / "screenshot.png").read_bytes() == b"game"
    assert (issue / "screenshot-2.png").read_bytes() == b"user"
    assert not (issue / "old.png").exists()
    assert not (issue / "notes.txt").exists()
    assert copy_recent_media(issue, "", 20) == []
    assert copy_recent_media(issue, tmp_path / "missing", 20) == []
    assert clamp_media_minutes("20") == 20
    assert clamp_media_minutes(None) == 20
    assert clamp_media_minutes(0) == 1
    assert clamp_media_minutes(9999) == 24 * 60


def test_screenshots_after_the_clip_are_copied(tmp_path: Path) -> None:
    source = tmp_path / "shots"
    issue = tmp_path / "issue"
    source.mkdir()
    issue.mkdir()
    now = time.time()
    started = now - 30
    kept = source / "after.jpg"
    kept.write_bytes(b"new")
    video = source / "after.mp4"
    video.write_bytes(b"mp4")
    early = source / "before.png"
    early.write_bytes(b"old")
    os.utime(kept, (now, now - 10))
    os.utime(video, (now, now - 10))
    os.utime(early, (now, started - 60))
    assert copy_screenshots_since(issue, source, started, now=now) == ["after.jpg"]
    assert (issue / "after.jpg").read_bytes() == b"new"
    assert not (issue / "after.mp4").exists()
    assert not (issue / "before.png").exists()
    assert copy_screenshots_since(issue, source, 0, now=now) == []


def test_a_chosen_clips_folder_replaces_the_game_bar_defaults(tmp_path: Path) -> None:
    from core.capture import captures_directories

    chosen = tmp_path / "clips"
    chosen.mkdir()
    assert captures_directories(str(chosen)) == [chosen]
    assert captures_directories(str(tmp_path / "missing")) == []


def test_displayinfo_is_enabled_before_the_clip(monkeypatch) -> None:
    from core.capture import COMMANDS_ON, request_clip

    order: list[str] = []
    monkeypatch.setattr("core.capture.game_is_foreground", lambda: True)
    monkeypatch.setattr("core.capture.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("core.capture.list_clips", lambda _dirs: set())
    monkeypatch.setattr(
        "core.capture._console_commands",
        lambda commands, _vk: order.append(commands[0]),
    )
    monkeypatch.setattr("core.capture._press_game_bar", lambda: order.append("clip"))
    result = request_clip(enable_displayinfo=True, console_key="`")
    assert order == [COMMANDS_ON[0], "clip"]
    assert result.watch_clip
    order.clear()
    request_clip(enable_displayinfo=False)
    assert order == ["clip"]


def test_grave_is_the_console_key_without_sending_it() -> None:
    assert console_vk("`") == 0xC0
    assert console_vk("") == 0xC0


def test_sendinput_struct_is_the_win32_size() -> None:
    # A truncated struct delivers keys to the wrong place. 40 bytes is INPUT
    # on 64-bit Windows (DWORD, padding, then the largest union member).
    if sys.maxsize > 2**32:
        assert ctypes.sizeof(_INPUT) == 40
