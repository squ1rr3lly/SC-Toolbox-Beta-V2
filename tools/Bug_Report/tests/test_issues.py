"""The saved-issue list, newest first."""
from __future__ import annotations

from core.issues import list_issues, set_issue_id, write_issue_state
from core.progress import draft_from_dict
from core.report import ReportDraft, write_report
from core.session import SessionContext


def test_issues_are_newest_first_and_the_id_is_saved(tmp_path) -> None:
    older = tmp_path / "20261017-080000"
    newer = tmp_path / "20261018-120000"
    older.mkdir()
    newer.mkdir()
    write_issue_state(older, {
        "description": "yesterday",
        "draft": ReportDraft(mode="new", title="Yesterday"),
    })
    write_issue_state(newer, {
        "description": "today",
        "draft": ReportDraft(mode="new", title="Today"),
    })
    write_report(newer, SessionContext(), "today", ReportDraft(mode="new", title="Today"))
    blank = tmp_path / "20261018-010000"
    blank.mkdir()
    (blank / "report.txt").write_text("Star Citizen bug report\n\nDescription:\nhello\n", encoding="utf-8")
    found = list_issues(tmp_path)
    assert [item.title for item in found] == ["Today", "Yesterday"]
    assert found[0].when == "2026-10-18 12:00:00"
    assert not blank.exists()
    contributed = tmp_path / "20261018-130000"
    contributed.mkdir()
    write_issue_state(contributed, {
        "blurb": "Bed log fails",
        "draft": ReportDraft(
            mode="contribution", issue_key="STARC-7", title="Bed log fails",
        ),
    })
    listed = list_issues(tmp_path)
    assert listed[0].title == "Bed log fails"
    assert listed[0].when == "2026-10-18 13:00:00"
    assert listed[0].issue_id == "STARC-7"
    assert set_issue_id(newer, "STARC-42") == "STARC-42"
    saved = {item.folder: item for item in list_issues(tmp_path)}
    assert saved[str(newer)].issue_id == "STARC-42"
    assert draft_from_dict({"issue_id": "STARC-42"}).issue_id == "STARC-42"
    text = (newer / "report.txt").read_text(encoding="utf-8")
    assert "Issue ID:\nSTARC-42" in text
