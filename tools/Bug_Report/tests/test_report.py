"""The paste-ready Issue Council draft."""
from __future__ import annotations

from pathlib import Path

from core.report import (
    ReportDraft, existing_description, note_attachments, refresh_session, render_report, write_report,
)
from core.session import SessionContext


def _ctx() -> SessionContext:
    return SessionContext(
        channel="LIVE",
        build="12913470",
        version="4.10.2-live.12913470",
        player_name="ProjectGegnome",
        player_geid="204715025323",
        shard="shard-alpha",
        location_raw="RR_HUR_L2",
        location_name="HUR-L2",
        star_system="Stanton",
        ship="RSI Constellation Andromeda",
        aboard=True,
        jurisdiction="Hurston",
        objective="Defend the ship",
    )


def test_draft_carries_the_description_and_session(tmp_path: Path) -> None:
    (tmp_path / "screenshot.png").write_bytes(b"png")
    (tmp_path / "clip.mp4").write_bytes(b"mp4")
    text = render_report(_ctx(), "allied ship exploded", tmp_path)
    assert "allied ship exploded" in text
    assert "4.10.2-live.12913470 (LIVE)" in text
    assert "ProjectGegnome (geid 204715025323)" in text
    assert "shard-alpha" in text
    assert "HUR-L2" in text
    assert "Hurston" in text
    assert "RSI Constellation Andromeda" in text
    assert "Defend the ship" in text
    (tmp_path / "shot.jpg").write_bytes(b"jpg")
    (tmp_path / "notes.txt").write_bytes(b"txt")
    text = render_report(_ctx(), "allied ship exploded", tmp_path)
    assert "screenshot.png" in text
    assert "clip.mp4" in text
    assert "shot.jpg" in text
    assert "notes.txt" not in text
    assert "Steps to reproduce:" in text


def test_on_foot_names_the_last_ship(tmp_path: Path) -> None:
    ctx = _ctx()
    ctx.aboard = False
    text = render_report(ctx, "", tmp_path)
    assert "(not written yet)" in text
    assert "on foot (last ship: RSI Constellation Andromeda)" in text


def test_write_report(tmp_path: Path) -> None:
    path = write_report(tmp_path, _ctx(), "bed log fails")
    assert Path(path).read_text(encoding="utf-8").startswith("Star Citizen bug report")
    assert "bed log fails" in Path(path).read_text(encoding="utf-8")


def test_more_info_is_saved_apart_from_the_description(tmp_path: Path) -> None:
    draft = ReportDraft(
        mode="contribution",
        issue_key="STARC-222067",
        more_info="Happens every time at HUR-L2.\nWorkaround: store the ship.",
    )
    write_report(tmp_path, _ctx(), "constellation bed", draft)
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert "Contributing to: STARC-222067" in text
    assert "More info:\nHappens every time at HUR-L2.\nWorkaround: store the ship." in text
    assert existing_description(tmp_path) == "constellation bed"


def test_new_issue_fields_follow_the_form(tmp_path: Path) -> None:
    draft = ReportDraft(
        mode="new",
        title="Bed log deletes the ship",
        category="Ships",
        severity="Playable",
        steps=["Lie down in the bed", "Log out", ""],
        happened="The ship was gone on login.",
        expected="The character wakes in the same bed.",
        workaround="",
    )
    text = render_report(_ctx(), "constellation bed", tmp_path, draft)
    assert "Issue title:\nBed log deletes the ship" in text
    assert "Issue category:\nShips" in text
    assert "How bad is it?\nPlayable" in text
    assert "How did you get there?\n1. Lie down in the bed\n2. Log out\n" in text
    assert "\n3. " not in text
    assert "What happened?\nThe ship was gone on login." in text
    assert "What should have happened?\nThe character wakes in the same bed." in text
    assert "Did you find a workaround?\n(none)" in text
    assert "4.10.2-live.12913470 (LIVE)" in text


def test_later_log_read_keeps_more_info(tmp_path: Path) -> None:
    draft = ReportDraft(mode="contribution", issue_key="STARC-222067", more_info="Every time.")
    write_report(tmp_path, _ctx(), "constellation bed", draft)
    ctx = _ctx()
    ctx.build = "13000000"
    ctx.version = "4.10.2-live.13000000"
    assert refresh_session(tmp_path, ctx)
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert "More info:\nEvery time." in text
    assert "4.10.2-live.13000000 (LIVE)" in text
    assert "12913470" not in text


def test_late_clip_keeps_the_description(tmp_path: Path) -> None:
    write_report(tmp_path, _ctx(), "bed log fails")
    (tmp_path / "clip.mp4").write_bytes(b"mp4")
    note_attachments(tmp_path)
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert "bed log fails" in text
    assert "- clip.mp4" in text
    assert existing_description(tmp_path) == "bed log fails"
    note_attachments(tmp_path)
    assert text.count("- clip.mp4") == (tmp_path / "report.txt").read_text(encoding="utf-8").count("- clip.mp4")
