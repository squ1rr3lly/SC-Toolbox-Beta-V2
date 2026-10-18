"""Search the public Bugmirror filter and read one issue's page.

Bugmirror is a server-rendered External Share for Jira filter. There is no
separate API: the same ``search`` field the page uses is a query parameter,
and each row links to the issue page that holds the full description.
"""
from __future__ import annotations

import html
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

log = logging.getLogger(__name__)

FILTER_URL = "https://jira.external-share.com/issue/407265/bugmirror_-_all_of_it"
SITE = "https://jira.external-share.com"
NEW_ISSUE_URL = "https://issue-council.robertsspaceindustries.com/projects/STAR-CITIZEN/issues/create"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_COUNCIL_RE = re.compile(r"https://issue-council\.robertsspaceindustries\.com[^'\"\s]*")
_BGMR_RE = re.compile(r"BGMR-\d+")
_TOTAL_RE = re.compile(r'id="total-count">\s*([\d,]+)')
_EMPTY = {"", "none"}
# Closed-out rows. They stay on Bugmirror, but they are not results here.
_EXCLUDED_STATUS = frozenset({"expired", "duplicate"})
# Empty elements. Counting them as opened tags never sees a close, and one
# <br> in a reproduction step would swallow the rest of the page.
_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


@dataclass
class IssueHit:
    href: str = ""
    key: str = ""
    build: str = ""
    description: str = ""
    status: str = ""
    priority: str = ""
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    council_url: str = ""
    # Short issue title is ``description``. ``happened`` is the What happened
    # text when the row has it apart from the title. ``matches`` is the rank score.
    # ``matched`` is the label text and words to highlight.
    happened: str = ""
    matches: int = 0
    matched: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    hits: list[IssueHit]
    total: int | None = None


@dataclass
class IssueDetail:
    href: str = ""
    bugmirror_key: str = ""
    key: str = ""
    description: str = ""
    reproduction: str = ""
    build: str = ""
    status: str = ""
    priority: str = ""
    labels: list[str] = field(default_factory=list)
    staff_comments: str = ""
    council_url: str = ""


def matches_session_build(row_build: str, build: str, version: str = "") -> bool:
    """True when a Bugmirror build string is this session's build.

    A short build id is ignored so a stray digit cannot badge every row.
    """
    row = (row_build or "").strip().lower()
    if not row:
        return False
    if version and version.strip().lower() in row:
        return True
    token = (build or "").strip()
    return len(token) >= 5 and token in row


# A label match outranks a title word, which outranks a body word. The list
# is ordered by this score, and the score is the number shown on each row.
_LABEL_WEIGHT = 4
_TITLE_WEIGHT = 2
_BODY_WEIGHT = 1
_STOP = frozenset("a an the and or of to in on for with at by from".split())
_WHAT_HAPPENED = re.compile(r"what happened\s*:?", re.IGNORECASE)
_MAX_TERMS = 6
_MAX_HITS = 50


def search_terms(text: str) -> list[str]:
    """The whole query, then its distinctive words.

    Bugmirror's own search misses a label or a title that does not contain
    every word, so each word is also fetched and the rows are ranked here.
    """
    full = " ".join((text or "").split())[:240]
    if not full:
        return []
    terms = [full]
    seen = {full.lower()}
    spaced = " ".join(full.replace("-", " ").replace("_", " ").split())
    if spaced.lower() not in seen:
        seen.add(spaced.lower())
        terms.append(spaced)
    words = []
    for token in re.findall(r"[A-Za-z0-9]+", full):
        low = token.lower()
        if len(token) < 3 or low in _STOP or low in seen:
            continue
        seen.add(low)
        words.append(token)
    words.sort(key=len, reverse=True)
    terms.extend(words)
    return terms[:_MAX_TERMS]


def rank_hits(query: str, hits: list[IssueHit]) -> list[IssueHit]:
    """Keep rows that match, best first.

    A label matches when its text appears in the search. Title and What
    happened match whole words. Comparison is case-insensitive.
    """
    query_tokens = _tokens(query)
    words = _content_words(query_tokens)
    scored: list[tuple[int, int, int, int, str, IssueHit]] = []
    for hit in hits:
        labels, title_n, body_n = _match_counts(hit, query_tokens, words)
        score = labels * _LABEL_WEIGHT + title_n * _TITLE_WEIGHT + body_n * _BODY_WEIGHT
        if score <= 0:
            continue
        hit.matches = score
        hit.matched = _matched_terms(hit, query_tokens, words)
        scored.append((score, labels, title_n, body_n, hit.key, hit))
    scored.sort(key=lambda item: (-item[0], -item[1], -item[2], -item[3], item[4]))
    return [item[-1] for item in scored]


def search(text: str, labels: list[str] | None = None, timeout: float = 25.0) -> SearchResult:
    """Bugmirror rows for *text*, ranked by label, then title, then what happened.

    Included *labels* are a filter: a row is kept only when it carries at least
    one of them. An empty description returns every such row. The description
    still ranks the rows that remain. Expired and Duplicate issues are left out.
    """
    query = " ".join((text or "").split())[:240]
    chosen = _unique_labels(labels or [])
    if not query and not chosen:
        return SearchResult(hits=[])
    terms = search_terms(query) if query else []
    have = {term.lower() for term in terms}
    for label in chosen:
        if label.lower() not in have:
            terms.append(label)
            have.add(label.lower())
    found: dict[str, IssueHit] = {}
    for index, term in enumerate(terms):
        url = FILTER_URL + "?" + urllib.parse.urlencode({"search": term})
        try:
            page = parse_search(fetch(url, timeout=timeout))
        except OSError:
            if index == 0:
                raise
            log.warning("bug_report: Bugmirror search %r failed", term)
            continue
        for hit in page.hits:
            key = hit.href or hit.key
            if not key:
                continue
            existing = found.get(key)
            if existing is None:
                found[key] = hit
            else:
                _merge_labels(existing, hit)
    hits = [hit for hit in found.values() if not _excluded(hit)]
    if chosen:
        hits = [hit for hit in hits if _matching_labels(hit, chosen)]
    if not query:
        for hit in hits:
            hit.matched = _matching_labels(hit, chosen)
            hit.matches = len(hit.matched)
        hits.sort(key=lambda hit: (-hit.matches, hit.key))
        log.info("bug_report: Bugmirror labels %s -> %d issues", chosen, len(hits))
        return SearchResult(hits=hits, total=len(hits))
    ranked = rank_hits(query, hits)
    for hit in ranked:
        _remember_terms(hit, _matching_labels(hit, chosen))
    log.info("bug_report: Bugmirror search %r labels %s -> %d matches", query, chosen, len(ranked))
    return SearchResult(hits=ranked[:_MAX_HITS], total=len(ranked))


def load_detail(href: str, timeout: float = 25.0) -> IssueDetail:
    url = absolute_url(href)
    detail = parse_detail(fetch(url, timeout=timeout))
    detail.href = href
    if not detail.bugmirror_key:
        found = _BGMR_RE.search(url)
        if found:
            detail.bugmirror_key = found.group(0)
    return detail


def fetch(url: str, timeout: float = 25.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise OSError(f"Bugmirror returned {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise OSError(f"Bugmirror could not be reached ({exc.reason})") from exc


def absolute_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urllib.parse.urljoin(SITE, href)


def _excluded(hit: IssueHit) -> bool:
    return hit.status.strip().lower() in _EXCLUDED_STATUS


def label_total(label: str, timeout: float = 25.0) -> int | None:
    """How many issues Bugmirror lists for this label.

    The page counter is the full result, not the rows on the first page.
    """
    token = " ".join((label or "").split())
    if not token:
        return None
    url = FILTER_URL + "?" + urllib.parse.urlencode({"search": token})
    page = parse_search(fetch(url, timeout=timeout))
    if page.total is not None:
        return page.total
    # An empty page often has no counter. That is zero issues, not an unknown total.
    if not page.hits:
        return 0
    return None


def parse_search(html: str) -> SearchResult:
    parser = _RowParser()
    parser.feed(html)
    parser.close()
    total = None
    found = _TOTAL_RE.search(html)
    if found:
        try:
            total = int(found.group(1).replace(",", ""))
        except ValueError:
            total = None
    return SearchResult(hits=parser.rows, total=total)


def parse_detail(html: str) -> IssueDetail:
    parser = _DetailParser()
    parser.feed(html)
    parser.close()
    return parser.detail


def _classes(attrs: dict[str, str]) -> set[str]:
    return set((attrs.get("class") or "").split())


def _norm(text: str) -> str:
    """Collapse whitespace, and the blank line a ``<br>`` plus the source newline makes."""
    lines = [" ".join(part.split()) for part in text.replace("\r", "").split("\n")]
    cleaned: list[str] = []
    for line in lines:
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        cleaned.append(line)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _content_words(tokens: list[str]) -> list[str]:
    """Query words worth matching in a title or body. One copy of each, in order."""
    words: list[str] = []
    for token in tokens:
        if len(token) < 3 or token in _STOP or token in words:
            continue
        words.append(token)
    if words:
        return words
    for token in tokens:
        if token not in words:
            words.append(token)
    return words


def _unique_labels(labels: list[str]) -> list[str]:
    chosen: list[str] = []
    seen: set[str] = set()
    for label in labels:
        cleaned = " ".join((label or "").split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            chosen.append(cleaned)
    return chosen


def _norm_label(label: str) -> str:
    return " ".join((label or "").casefold().split())


def _matching_labels(hit: IssueHit, chosen: list[str]) -> list[str]:
    """Included labels that this issue actually carries. Comparison ignores case."""
    have = {_norm_label(label) for label in hit.labels}
    have.discard("")
    return [label for label in chosen if _norm_label(label) in have]


def _merge_labels(existing: IssueHit, hit: IssueHit) -> None:
    have = {_norm_label(label) for label in existing.labels}
    for label in hit.labels:
        key = _norm_label(label)
        if key and key not in have:
            existing.labels.append(label)
            have.add(key)


def highlight_html(text: str, terms: list[str]) -> str:
    """Escape *text* and wrap whole-token matches in ``<span class="hit">``."""
    pieces = []
    seen: set[str] = set()
    for term in sorted(terms, key=len, reverse=True):
        cleaned = " ".join((term or "").split())
        key = cleaned.casefold()
        if len(cleaned) < 2 or key in seen:
            continue
        seen.add(key)
        pieces.append(re.escape(cleaned))
    if not text:
        return ""
    if not pieces:
        return html.escape(text).replace("\n", "<br>")
    pattern = re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(pieces) + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    parts: list[str] = []
    last = 0
    for found in pattern.finditer(text):
        parts.append(html.escape(text[last:found.start()]))
        parts.append(f'<span class="hit">{html.escape(found.group(0))}</span>')
        last = found.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts).replace("\n", "<br>")


def _remember_terms(hit: IssueHit, terms: list[str]) -> None:
    have = {term.casefold() for term in hit.matched}
    for term in terms:
        key = term.casefold()
        if term and key not in have:
            hit.matched.append(term)
            have.add(key)


def _matched_terms(hit: IssueHit, query_tokens: list[str], words: list[str]) -> list[str]:
    """Labels and words on this issue that the search actually matched."""
    terms: list[str] = []
    for label in hit.labels:
        parts = _tokens(label)
        if parts and _phrase_in(parts, query_tokens):
            terms.append(label)
    title, body = _title_and_body(hit)
    haystack = set(_tokens(title)) | set(_tokens(body))
    for word in words:
        if word in haystack:
            terms.append(word)
    return terms


def _phrase_in(parts: list[str], query_tokens: list[str]) -> bool:
    width = len(parts)
    return any(query_tokens[start:start + width] == parts for start in range(len(query_tokens) - width + 1))


def _label_hits(labels: list[str], query_tokens: list[str]) -> int:
    """How many labels appear, in order, in the search."""
    if not query_tokens:
        return 0
    count = 0
    for label in labels:
        parts = _tokens(label)
        if parts and _phrase_in(parts, query_tokens):
            count += 1
    return count


def _word_hits(words: list[str], text: str) -> int:
    haystack = set(_tokens(text))
    return sum(1 for word in words if word in haystack)


def _title_and_body(hit: IssueHit) -> tuple[str, str]:
    """The row's short text is the title. A What happened section is the body."""
    text = hit.description or ""
    body = hit.happened or ""
    found = _WHAT_HAPPENED.search(text)
    if found and not body:
        title = text[:found.start()].strip(" \n:-")
        body = text[found.end():].strip()
        return title or text, body
    return text, body


def _match_counts(hit: IssueHit, query_tokens: list[str], words: list[str]) -> tuple[int, int, int]:
    title, body = _title_and_body(hit)
    labels = _label_hits(hit.labels, query_tokens)
    title_n = _word_hits(words, title)
    body_n = _word_hits(words, body)
    if body and _tokens(body) == _tokens(title):
        body_n = 0
    return labels, title_n, body_n


def _blank(text: str) -> str:
    cleaned = _norm(text)
    if cleaned.lower() in _EMPTY:
        return ""
    return cleaned


class _BaseParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._ignore = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parsed = {key: value or "" for key, value in attrs}
        if tag in _VOID:
            if not self._ignore:
                self.on_start(tag, parsed)
            return
        self._depth += 1
        if tag in ("script", "style"):
            self._ignore += 1
            return
        if self._ignore:
            return
        self.on_start(tag, parsed)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <img .../> and <br/> are empty. The default would open and then close
        # them, and a void open that did not change depth would then decrement.
        parsed = {key: value or "" for key, value in attrs}
        if not self._ignore and tag not in ("script", "style"):
            self.on_start(tag, parsed)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._ignore:
            self._ignore -= 1
            self._depth = max(0, self._depth - 1)
            return
        if not self._ignore:
            self.on_end(tag)
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if not self._ignore:
            self.on_data(data)

    def on_start(self, tag: str, attrs: dict[str, str]) -> None:
        return None

    def on_end(self, tag: str) -> None:
        return None

    def on_data(self, data: str) -> None:
        return None


class _RowParser(_BaseParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[IssueHit] = []
        self._row: IssueHit | None = None
        self._row_depth = 0
        self._cap: tuple[str, int] | None = None
        self._buf: list[str] = []

    def on_start(self, tag: str, attrs: dict[str, str]) -> None:
        classes = _classes(attrs)
        if self._row is None:
            if tag == "a" and "issue-row" in classes:
                self._row = IssueHit(href=attrs.get("href", ""))
                self._row_depth = self._depth
            return
        onclick = attrs.get("onclick", "")
        if "issue-council.robertsspaceindustries.com" in onclick and not self._row.council_url:
            found = _COUNCIL_RE.search(onclick)
            if found:
                self._row.council_url = found.group(0)
        alt = attrs.get("alt", "")
        if alt.lower().startswith("priority:") and not self._row.priority:
            self._row.priority = alt.split(":", 1)[1].strip()
        if tag == "br" and self._cap:
            self._buf.append("\n")
        if "cf-array-string" in classes and self._cap and self._cap[0] in ("labels", "components"):
            self._buf.append("\n")
        if self._cap is not None:
            return
        kind = ""
        if "summary" in classes and "column" in classes:
            kind = "key"
        elif "customfield_10289" in classes:
            kind = "build"
        elif "user_text" in classes:
            kind = "description"
        elif "field-string-item" in classes and "status" in classes:
            kind = "status"
        elif "labels" in classes and "column" in classes:
            kind = "labels"
        elif "components" in classes and "column" in classes:
            kind = "components"
        elif "what happened" in (attrs.get("data-label") or "").lower():
            kind = "happened"
        if kind:
            self._cap = (kind, self._depth)
            self._buf = []

    def on_end(self, tag: str) -> None:
        if self._cap is not None and self._depth == self._cap[1]:
            self._flush()
        if self._row is not None and tag == "a" and self._depth == self._row_depth:
            self._flush()
            if self._row.key or self._row.href:
                self.rows.append(self._row)
            self._row = None

    def on_data(self, data: str) -> None:
        if self._cap is not None:
            self._buf.append(data)

    def _flush(self) -> None:
        if self._cap is None or self._row is None:
            self._cap = None
            self._buf = []
            return
        kind = self._cap[0]
        if kind in ("labels", "components"):
            items = [part.strip() for part in _norm("".join(self._buf)).split("\n") if part.strip()]
            setattr(self._row, kind, items)
        else:
            text = " ".join("".join(self._buf).split())
            if text:
                setattr(self._row, kind, text)
        self._cap = None
        self._buf = []


class _DetailParser(_BaseParser):
    """One Bugmirror issue page: summary, description, and the labelled fields."""

    _LABELS = {
        "env+build#": "build",
        "reproduction steps": "reproduction",
        "staff comments": "staff_comments",
        "priority": "priority",
        "labels": "labels",
    }

    def __init__(self) -> None:
        super().__init__()
        self.detail = IssueDetail()
        self._cap: tuple[str, int] | None = None
        self._buf: list[str] = []
        self._label = ""
        self._saw_status = False

    def on_start(self, tag: str, attrs: dict[str, str]) -> None:
        classes = _classes(attrs)
        if tag == "h1" and "data-empty-hint" in attrs and self._cap is None:
            self._cap = ("key", self._depth)
            self._buf = []
        elif attrs.get("id") == "issue-description" and self._cap is None:
            self._cap = ("description", self._depth)
            self._buf = []
        elif tag == "h2" and "field-label" in classes and self._cap is None:
            self._cap = ("label", self._depth)
            self._buf = []
        elif "field-value" in classes and self._label and self._cap is None:
            self._cap = ("value", self._depth)
            self._buf = []
        elif (
            not self._saw_status
            and "field-string-item" in classes
            and "status" in classes
            and self._cap is None
        ):
            self._cap = ("status", self._depth)
            self._buf = []
        if tag == "br" and self._cap:
            self._buf.append("\n")
        if "cf-array-string" in classes and self._cap and self._cap[0] == "value" and self._label == "labels":
            self._buf.append("\n")
        href = attrs.get("href", "")
        if "issue-council.robertsspaceindustries.com/projects/" in href and not self.detail.council_url:
            self.detail.council_url = href
        if tag == "title" and self._cap is None:
            self._cap = ("title", self._depth)
            self._buf = []

    def on_end(self, tag: str) -> None:
        if self._cap is not None and self._depth == self._cap[1]:
            self._flush()

    def on_data(self, data: str) -> None:
        if self._cap is not None:
            self._buf.append(data)

    def _flush(self) -> None:
        if self._cap is None:
            return
        kind = self._cap[0]
        text = _norm("".join(self._buf))
        self._cap = None
        self._buf = []
        if kind == "label":
            self._label = text.strip().lower()
            return
        if kind == "key" and text:
            self.detail.key = " ".join(text.split())
        elif kind == "description":
            self.detail.description = text
        elif kind == "status":
            self.detail.status = " ".join(text.split())
            self._saw_status = True
        elif kind == "title":
            found = _BGMR_RE.search(text)
            if found and not self.detail.bugmirror_key:
                self.detail.bugmirror_key = found.group(0)
        elif kind == "value":
            self._store_value(text)
            self._label = ""

    def _store_value(self, text: str) -> None:
        field_name = self._LABELS.get(self._label)
        if field_name == "labels":
            self.detail.labels = [
                part.strip() for part in text.split("\n") if part.strip() and part.strip().lower() not in _EMPTY
            ]
            return
        if field_name == "build":
            self.detail.build = " ".join(text.split())
            return
        if field_name == "priority":
            self.detail.priority = " ".join(text.split())
            return
        if field_name == "reproduction":
            self.detail.reproduction = _blank(text)
            return
        if field_name == "staff_comments":
            self.detail.staff_comments = _blank(text)
