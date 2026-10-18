"""The in-progress file that brings an unfinished issue back."""
from __future__ import annotations

from core.progress import clear_progress, draft_from_dict, load_progress, save_progress
from core.report import ReportDraft
from core.session import SessionContext, read_context, write_context


def test_progress_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.progress._PATH", tmp_path / "in_progress.json")
    assert load_progress() is None
    save_progress({
        "folder": str(tmp_path / "issue"),
        "page": "new",
        "draft": ReportDraft(mode="new", title="Bed log", steps=["Lie down"]),
    })
    saved = load_progress()
    assert saved is not None
    assert saved["page"] == "new"
    draft = draft_from_dict(saved["draft"])
    assert draft.title == "Bed log"
    assert draft.steps == ["Lie down"]
    clear_progress()
    assert load_progress() is None


def test_read_context_returns_the_saved_session(tmp_path) -> None:
    folder = tmp_path / "issue"
    folder.mkdir()
    write_context(folder, SessionContext(build="12345", player_name="Pilot"))
    ctx = read_context(folder)
    assert ctx.build == "12345"
    assert ctx.player_name == "Pilot"
    assert read_context(tmp_path / "missing").build == ""
