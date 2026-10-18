"""Bugmirror HTML, with no network."""
from __future__ import annotations

from pathlib import Path

from core.bugmirror import (
    IssueHit, highlight_html, label_total, matches_session_build, parse_detail, parse_search,
    rank_hits, search, search_terms,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_search_rows() -> None:
    result = parse_search((_FIXTURES / "search.html").read_text(encoding="utf-8"))
    assert result.total == 14
    assert len(result.hits) == 2
    first, second = result.hits
    assert first.key == "STARC-222067"
    assert first.href.endswith("/BGMR-19449")
    assert first.build == "4.10.2-live.12913470"
    assert first.description == "defended ship exploed for no reason (and really quick)"
    assert first.status == "Open"
    assert first.priority == "Low"
    assert first.labels == ["AI-Flight", "Contract-Mercenary"]
    assert first.components == ["Star Citizen"]
    assert first.council_url.endswith("/STARC-222067")
    assert second.key == "STARC-191326"
    assert second.status == "Confirmed"
    assert "Description:" not in first.description


def test_label_total_reads_the_page_counter(monkeypatch) -> None:
    page = (_FIXTURES / "search.html").read_text(encoding="utf-8")
    seen: list[str] = []

    def fake_fetch(url: str, timeout: float = 25) -> str:
        seen.append(url)
        assert timeout == 5
        return page

    monkeypatch.setattr("core.bugmirror.fetch", fake_fetch)
    assert label_total("PyroII-CheckmateStation", timeout=5) == 14
    assert seen == [
        "https://jira.external-share.com/issue/407265/bugmirror_-_all_of_it?search=PyroII-CheckmateStation"
    ]
    assert label_total("  ") is None

    def fake_empty(url: str, timeout: float = 25) -> str:
        return "<html><body></body></html>"

    monkeypatch.setattr("core.bugmirror.fetch", fake_empty)
    assert label_total("NoSuchLabel") == 0


def test_detail_page_skips_dialog_headings() -> None:
    detail = parse_detail((_FIXTURES / "detail.html").read_text(encoding="utf-8"))
    assert detail.bugmirror_key == "BGMR-19449"
    assert detail.key == "STARC-222067"
    assert detail.key != "STARC-00000"
    assert "Delete this comment" not in detail.key
    assert detail.description == "defended ship exploed for no reason (and really quick)"
    assert detail.build == "4.10.2-live.12913470"
    assert detail.priority == "Low"
    assert detail.status == "Open"
    assert detail.staff_comments == ""
    assert detail.labels == ["AI-Flight", "Contract-Mercenary"]
    assert "defend ship mission" in detail.reproduction
    assert "allied ship exploded" in detail.reproduction
    assert detail.council_url.endswith("/STARC-222067")


def test_rank_puts_labels_ahead_of_title_ahead_of_what_happened() -> None:
    label = IssueHit(key="L", labels=["AI-Flight"], description="unrelated report")
    title = IssueHit(key="T", description="Bed log fails on the Constellation")
    body = IssueHit(key="B", description="something else", happened="the allied ship exploded")
    none = IssueHit(key="N", description="quantum travel marker")
    ranked = rank_hits("AI-Flight bed exploded", [body, none, title, label])
    assert [hit.key for hit in ranked] == ["L", "T", "B"]
    assert label.matches > title.matches > body.matches


def test_rank_is_case_insensitive_and_counts_every_word() -> None:
    one = IssueHit(key="1", description="Bed log fails")
    two = IssueHit(key="2", description="Bed log fails on the Constellation")
    ranked = rank_hits("BED constellation", [one, two])
    assert [hit.key for hit in ranked] == ["2", "1"]
    assert two.matches > one.matches


def test_label_appears_in_the_search_with_hyphens_or_spaces() -> None:
    hit = IssueHit(key="L", labels=["Contract-Mercenary", "Ships"], description="other")
    ranked = rank_hits("contract mercenary ships", [hit])
    assert ranked[0] is hit
    assert hit.matches == 8  # two labels, four points each
    assert rank_hits("mercenary contract", [hit]) == []


def test_what_happened_section_is_the_body_not_a_second_title() -> None:
    hit = IssueHit(
        key="W",
        description="Bed log\nWhat happened: the ship was gone on login",
    )
    ranked = rank_hits("gone", [hit])
    assert len(ranked) == 1
    assert hit.matches == 1
    titled = IssueHit(key="T", description="the ship was gone")
    both = rank_hits("gone", [titled, hit])
    assert [item.key for item in both] == ["T", "W"]


def test_search_terms_add_distinctive_words() -> None:
    terms = search_terms("the bed-log fails")
    assert terms[0] == "the bed-log fails"
    assert "the bed log fails" in terms
    assert "fails" in terms
    assert "the" not in terms[1:]


def _issue_row(key: str, status: str, label: str = "AI-Flight") -> str:
    return (
        f'<a href="/issue/{key}" class="row issue-row">'
        f'<div class="field column summary"><span class="cf-string">{key}</span></div>'
        '<div class="adf cell-description"><span class="user_text"><p>ship broke</p></span></div>'
        f'<div class="field-string-item status"><span>{status}</span></div>'
        '<div class="field column labels"><span class="cf-array cf-array-string">'
        f'{label}</span></div></a>'
    )


def test_expired_and_duplicate_issues_are_left_out(monkeypatch) -> None:
    page = (
        "<html><body>"
        + _issue_row("STARC-1", "Open")
        + _issue_row("STARC-2", "Expired")
        + _issue_row("STARC-3", "Duplicate")
        + _issue_row("STARC-4", "Confirmed")
        + "</body></html>"
    )
    monkeypatch.setattr("core.bugmirror.fetch", lambda url, timeout=25.0: page)
    assert [hit.key for hit in search("ship").hits] == ["STARC-1", "STARC-4"]
    labeled = search("", ["AI-Flight"])
    assert [hit.key for hit in labeled.hits] == ["STARC-1", "STARC-4"]
    assert labeled.total == 2


def test_included_label_hides_issues_that_do_not_have_it(monkeypatch) -> None:
    page = (_FIXTURES / "search.html").read_text(encoding="utf-8")
    monkeypatch.setattr("core.bugmirror.fetch", lambda url, timeout=25.0: page)
    ranked = search("ship", ["AI-Flight"])
    assert [hit.key for hit in ranked.hits] == ["STARC-222067"]
    assert search("ship", ["NotALabel"]).hits == []


def test_blank_search_without_labels_returns_nothing() -> None:
    assert search("").hits == []
    assert search("", []).hits == []


def test_empty_search_returns_every_issue_with_the_included_label(monkeypatch) -> None:
    page = (_FIXTURES / "search.html").read_text(encoding="utf-8")
    monkeypatch.setattr("core.bugmirror.fetch", lambda url, timeout=25.0: page)
    result = search("", ["ai-flight", "Contract-Mercenary"])
    assert [hit.key for hit in result.hits] == ["STARC-222067"]
    assert result.hits[0].matches == 2
    assert search("", ["BedLogging"]).hits == []


def test_highlight_wraps_matched_words_and_labels() -> None:
    marked = highlight_html("Bed log fails on the ship", ["bed", "ship"])
    assert '<span class="hit">Bed</span>' in marked
    assert '<span class="hit">ship</span>' in marked
    assert highlight_html("Ships stay dark", ["ship"]) == "Ships stay dark"
    label = highlight_html("Labels: AI-Flight, Ships", ["AI-Flight"])
    assert '<span class="hit">AI-Flight</span>' in label
    assert "<script>" not in highlight_html("<script>", ["script"])


def test_rank_remembers_the_matched_text() -> None:
    hit = IssueHit(key="L", labels=["AI-Flight"], description="Bed log fails")
    ranked = rank_hits("AI-Flight bed", [hit])
    assert ranked[0] is hit
    assert "AI-Flight" in hit.matched
    assert "bed" in hit.matched


def test_build_badge_uses_the_session_build_number() -> None:
    assert matches_session_build("4.10.2-live.12913470", "12913470", "4.10.2-live.12913470")
    assert not matches_session_build("3.17.1-live.8073467", "12913470", "")
    assert not matches_session_build("4.10.2-live.12913470", "1", "")
