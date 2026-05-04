"""RFC 8288 link-header parser tests.

Stage 8d strengthens the §3.4 link-header pagination implementation
to handle the RFC 8288 edge cases the original Stage 8a parser
glossed over: rel case-insensitivity, multiple rels in one entry,
link parameters, comma inside angle-bracketed URI, multi-header
concatenation, relative URI resolution.
"""

from __future__ import annotations

import pytest

from uacp_prototype.dispatch.pagination import (
    _is_absolute_uri,
    _parse_link_entry,
    _parse_link_header,
    _split_link_entries,
)


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


def test_simple_next_link() -> None:
    rels = _parse_link_header('<https://api.example.com/p2>; rel="next"')
    assert rels == {"next": "https://api.example.com/p2"}


def test_multiple_entries_comma_separated() -> None:
    header = (
        '<https://api.example.com/p2>; rel="next", '
        '<https://api.example.com/p9>; rel="last"'
    )
    rels = _parse_link_header(header)
    assert rels["next"] == "https://api.example.com/p2"
    assert rels["last"] == "https://api.example.com/p9"


def test_github_style_full_navigation() -> None:
    """GitHub typical Link header on a paginated endpoint."""
    header = (
        '<https://api.github.com/u/x/repos?page=2>; rel="next", '
        '<https://api.github.com/u/x/repos?page=5>; rel="last", '
        '<https://api.github.com/u/x/repos?page=1>; rel="first", '
        '<https://api.github.com/u/x/repos?page=2>; rel="prev"'
    )
    rels = _parse_link_header(header)
    assert rels["next"] == "https://api.github.com/u/x/repos?page=2"
    assert rels["last"] == "https://api.github.com/u/x/repos?page=5"
    assert rels["first"] == "https://api.github.com/u/x/repos?page=1"
    assert rels["prev"] == "https://api.github.com/u/x/repos?page=2"


# ---------------------------------------------------------------------------
# rel parameter edge cases
# ---------------------------------------------------------------------------


def test_rel_unquoted_value() -> None:
    """RFC 8288 permits unquoted rel values; the parser handles both."""
    rels = _parse_link_header("<https://api.example.com/p2>; rel=next")
    assert rels == {"next": "https://api.example.com/p2"}


def test_rel_case_insensitive() -> None:
    """RFC 8288 §3.3: relation types are case-insensitive. The parser
    lowercases the rel value so consumers can do rels["next"] without
    case games."""
    rels = _parse_link_header('<https://api.example.com/p2>; rel="NEXT"')
    assert rels == {"next": "https://api.example.com/p2"}


def test_rel_mixed_case() -> None:
    rels = _parse_link_header('<https://api.example.com/p2>; rel="Next"')
    assert rels == {"next": "https://api.example.com/p2"}


def test_multiple_rels_space_separated() -> None:
    """RFC 8288 §3.3 permits space-separated relations; the URI is
    registered under each relation."""
    rels = _parse_link_header(
        '<https://api.example.com/p2>; rel="next prev"'
    )
    assert rels["next"] == "https://api.example.com/p2"
    assert rels["prev"] == "https://api.example.com/p2"


# ---------------------------------------------------------------------------
# Link parameters beyond rel
# ---------------------------------------------------------------------------


def test_link_with_title_parameter() -> None:
    """Multiple parameters per entry; rel is what we extract, title is
    ignored."""
    rels = _parse_link_header(
        '<https://api.example.com/p2>; rel="next"; title="Next page"'
    )
    assert rels["next"] == "https://api.example.com/p2"


def test_link_with_type_and_rel() -> None:
    rels = _parse_link_header(
        '<https://api.example.com/p2>; type="application/json"; rel="next"'
    )
    assert rels["next"] == "https://api.example.com/p2"


# ---------------------------------------------------------------------------
# Comma + bracket edge cases
# ---------------------------------------------------------------------------


def test_comma_inside_angle_bracketed_uri_preserved() -> None:
    """A comma inside <...> is part of the URI, not an entry separator.
    RFC 8288 strongly recommends URLs not contain literal commas
    (they SHOULD be percent-encoded), but RFC 3986 doesn't forbid
    them; the parser handles raw commas inside brackets as a
    permissive case."""
    header = (
        '<https://api.example.com/path,with,comma>; rel="next", '
        '<https://api.example.com/p2>; rel="last"'
    )
    rels = _parse_link_header(header)
    assert rels["next"] == "https://api.example.com/path,with,comma"
    assert rels["last"] == "https://api.example.com/p2"


def test_comma_inside_quoted_param_preserved() -> None:
    """A comma inside a quoted parameter value is part of the value,
    not an entry separator."""
    header = (
        '<https://api.example.com/p2>; rel="next"; title="Page 2, of 5", '
        '<https://api.example.com/p3>; rel="last"'
    )
    rels = _parse_link_header(header)
    assert rels["next"] == "https://api.example.com/p2"
    assert rels["last"] == "https://api.example.com/p3"


# ---------------------------------------------------------------------------
# Multiple Link headers (HTTP semantics)
# ---------------------------------------------------------------------------


def test_multiple_link_headers_as_list() -> None:
    """HTTP allows the same field name to appear multiple times. When
    the input is a list, entries are joined with ', ' and parsed as
    a single string."""
    headers = [
        '<https://api.example.com/p2>; rel="next"',
        '<https://api.example.com/p9>; rel="last"',
    ]
    rels = _parse_link_header(headers)
    assert rels["next"] == "https://api.example.com/p2"
    assert rels["last"] == "https://api.example.com/p9"


def test_empty_list_returns_empty_dict() -> None:
    assert _parse_link_header([]) == {}


def test_empty_string_returns_empty_dict() -> None:
    assert _parse_link_header("") == {}


# ---------------------------------------------------------------------------
# Relative URI resolution
# ---------------------------------------------------------------------------


def test_relative_uri_resolved_against_base() -> None:
    """RFC 8288 §3.4 permits relative-reference URIs."""
    rels = _parse_link_header(
        '</items?page=2>; rel="next"',
        base_url="https://api.example.com/v1/list",
    )
    assert rels["next"] == "https://api.example.com/items?page=2"


def test_relative_uri_query_only() -> None:
    rels = _parse_link_header(
        '<?page=2>; rel="next"',
        base_url="https://api.example.com/v1/list?page=1",
    )
    assert rels["next"] == "https://api.example.com/v1/list?page=2"


def test_absolute_uri_unchanged_with_base() -> None:
    """An already-absolute URI passes through untouched even when
    base_url is supplied."""
    rels = _parse_link_header(
        '<https://other.example.com/p2>; rel="next"',
        base_url="https://api.example.com/",
    )
    assert rels["next"] == "https://other.example.com/p2"


# ---------------------------------------------------------------------------
# Malformed input tolerance
# ---------------------------------------------------------------------------


def test_entry_without_brackets_skipped() -> None:
    """An entry that doesn't start with < is not a valid Link entry
    per RFC 8288 §3; the parser skips it."""
    header = (
        'malformed entry, '
        '<https://api.example.com/p2>; rel="next"'
    )
    rels = _parse_link_header(header)
    assert rels == {"next": "https://api.example.com/p2"}


def test_entry_without_close_bracket_skipped() -> None:
    rels = _parse_link_header('<https://api.example.com/p2; rel="next"')
    assert rels == {}


def test_entry_without_rel_skipped() -> None:
    """An entry with a URI but no rel parameter is structurally valid
    per RFC 8288 but not useful for pagination; we skip it."""
    rels = _parse_link_header(
        '<https://api.example.com/p2>; title="orphan"'
    )
    assert rels == {}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def test_split_link_entries_simple() -> None:
    entries = _split_link_entries(
        '<https://a/>; rel="next", <https://b/>; rel="last"'
    )
    assert entries == [
        '<https://a/>; rel="next"',
        '<https://b/>; rel="last"',
    ]


def test_split_link_entries_comma_in_brackets() -> None:
    entries = _split_link_entries(
        '<https://a/path,x>; rel="next", <https://b/>; rel="last"'
    )
    assert entries == [
        '<https://a/path,x>; rel="next"',
        '<https://b/>; rel="last"',
    ]


def test_split_link_entries_comma_in_quotes() -> None:
    entries = _split_link_entries(
        '<https://a/>; rel="next"; title="Page, with comma", <https://b/>'
    )
    assert len(entries) == 2


def test_parse_link_entry_returns_uri_and_params() -> None:
    uri, params = _parse_link_entry('<https://a/>; rel="next"; type=json')
    assert uri == "https://a/"
    assert params == {"rel": "next", "type": "json"}


def test_parse_link_entry_missing_brackets() -> None:
    uri, params = _parse_link_entry("not a link")
    assert uri is None
    assert params == {}


def test_is_absolute_uri() -> None:
    assert _is_absolute_uri("https://example.com/")
    assert _is_absolute_uri("http://example.com/")
    assert not _is_absolute_uri("/relative/path")
    assert not _is_absolute_uri("?query=only")
    assert not _is_absolute_uri("relative")
