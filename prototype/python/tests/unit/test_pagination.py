"""Tests for pagination loops per §4.4."""

from __future__ import annotations

import random
from typing import Any

import httpx
import pytest
import respx

from uacp_prototype.auth.base import AuthApplyResult
from uacp_prototype.dispatch.client import (
    DispatchClient,
    DispatchError,
    DispatchSuccess,
)
from uacp_prototype.dispatch.pagination import (
    DEFAULT_MAX_PAGES,
    _parse_link_header,
    _resolve_jsonpath,
    dispatch_paginated,
)
from uacp_prototype.spec.loader import load_dict
from uacp_prototype.spec.models import UACPArtifact


class StaticAuth:
    method = "x-test-static"

    def apply(self, request: httpx.Request, *, credentials: dict[str, Any]) -> AuthApplyResult:
        return AuthApplyResult(headers={"Authorization": "Bearer t"})


def _silent_sleep(_secs: float) -> None:
    pass


def _artifact(operations: list[dict[str, Any]]) -> UACPArtifact:
    raw = {
        "$schema": "https://uacp.spec/v1/schema.json",
        "authentication": {"method": "x-test-static"},
        "dispatch": {"base_url": "https://api.example.com"},
        "operations": operations,
    }
    return load_dict(raw)


def _client(art: UACPArtifact) -> DispatchClient:
    return DispatchClient(
        art,
        auth_method=StaticAuth(),
        credential_resolver=lambda: {"access_token": "t"},
        sleep=_silent_sleep,
        rng=random.Random(0),
    )


# ---------------------------------------------------------------------------
# JSONPath subset
# ---------------------------------------------------------------------------


def test_jsonpath_simple_field() -> None:
    assert _resolve_jsonpath({"foo": "bar"}, "$.foo") == "bar"


def test_jsonpath_nested_field() -> None:
    assert _resolve_jsonpath({"a": {"b": {"c": 42}}}, "$.a.b.c") == 42


def test_jsonpath_missing_returns_none() -> None:
    assert _resolve_jsonpath({"a": {"b": 1}}, "$.a.x") is None
    assert _resolve_jsonpath({"a": "string"}, "$.a.b") is None


def test_jsonpath_invalid_prefix_raises() -> None:
    with pytest.raises(Exception):
        _resolve_jsonpath({}, "foo")


# ---------------------------------------------------------------------------
# Link header parsing
# ---------------------------------------------------------------------------


def test_link_header_single_next() -> None:
    rels = _parse_link_header('<https://api.example.com/p2>; rel="next"')
    assert rels == {"next": "https://api.example.com/p2"}


def test_link_header_multiple_rels() -> None:
    header = (
        '<https://api.example.com/p2>; rel="next", '
        '<https://api.example.com/p1>; rel="prev"'
    )
    rels = _parse_link_header(header)
    assert rels["next"] == "https://api.example.com/p2"
    assert rels["prev"] == "https://api.example.com/p1"


# ---------------------------------------------------------------------------
# Cursor pagination
# ---------------------------------------------------------------------------


def _cursor_op() -> dict[str, Any]:
    return {
        "id": "list_messages",
        "summary": "List messages.",
        "idempotency": "idempotent",
        "request": {
            "method": "GET",
            "path": "/v1/messages",
            "query_parameters": {
                "type": "object",
                "properties": {
                    "page_token": {"type": "string"},
                    "max_results": {"type": "integer", "default": 100},
                },
            },
        },
        "response": {
            "200": {"description": "ok"},
            "default": {"description": "fallback"},
        },
        "pagination": {
            "pattern": "cursor",
            "request_cursor_parameter": "page_token",
            "response_cursor_path": "$.nextPageToken",
        },
    }


@respx.mock
def test_cursor_pagination_iterates_until_token_empty() -> None:
    art = _artifact([_cursor_op()])
    page1 = httpx.Response(200, json={"messages": [{"id": "1"}, {"id": "2"}], "nextPageToken": "tok-2"})
    page2 = httpx.Response(200, json={"messages": [{"id": "3"}], "nextPageToken": ""})
    respx.get("https://api.example.com/v1/messages").mock(
        side_effect=[page1, page2]
    )
    with _client(art) as c:
        pages = list(dispatch_paginated(c, "list_messages"))
    assert len(pages) == 2
    assert all(isinstance(p, DispatchSuccess) for p in pages)
    assert pages[0].body["nextPageToken"] == "tok-2"
    assert pages[1].body["nextPageToken"] == ""


@respx.mock
def test_cursor_pagination_passes_token_on_subsequent_requests() -> None:
    art = _artifact([_cursor_op()])
    pages = [
        httpx.Response(200, json={"messages": [], "nextPageToken": "p2"}),
        httpx.Response(200, json={"messages": [], "nextPageToken": "p3"}),
        httpx.Response(200, json={"messages": []}),  # missing key -> end
    ]
    route = respx.get("https://api.example.com/v1/messages").mock(side_effect=pages)
    with _client(art) as c:
        results = list(dispatch_paginated(c, "list_messages"))
    assert len(results) == 3
    assert route.call_count == 3
    # First call has no page_token; second has p2; third has p3
    from urllib.parse import parse_qs, urlparse

    qs1 = parse_qs(urlparse(str(route.calls[0].request.url)).query)
    qs2 = parse_qs(urlparse(str(route.calls[1].request.url)).query)
    qs3 = parse_qs(urlparse(str(route.calls[2].request.url)).query)
    assert "page_token" not in qs1
    assert qs2["page_token"] == ["p2"]
    assert qs3["page_token"] == ["p3"]


@respx.mock
def test_cursor_pagination_repeated_token_terminates() -> None:
    art = _artifact([_cursor_op()])
    respx.get("https://api.example.com/v1/messages").mock(
        side_effect=[
            httpx.Response(200, json={"nextPageToken": "tok-1"}),
            httpx.Response(200, json={"nextPageToken": "tok-1"}),  # same as prior — end
        ]
    )
    with _client(art) as c:
        pages = list(dispatch_paginated(c, "list_messages"))
    assert len(pages) == 2  # stopped without third call


# ---------------------------------------------------------------------------
# Offset pagination
# ---------------------------------------------------------------------------


def _offset_op_with_total() -> dict[str, Any]:
    return {
        "id": "list_offset",
        "summary": "Offset list.",
        "idempotency": "idempotent",
        "request": {
            "method": "GET",
            "path": "/v1/offset",
            "query_parameters": {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer", "default": 0},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        "response": {"200": {"description": "ok"}, "default": {"description": "fallback"}},
        "pagination": {
            "pattern": "offset",
            "request_offset_parameter": "offset",
            "request_limit_parameter": "limit",
            "response_total_path": "$.total",
        },
    }


@respx.mock
def test_offset_pagination_iterates_to_total() -> None:
    art = _artifact([_offset_op_with_total()])
    respx.get("https://api.example.com/v1/offset").mock(
        side_effect=[
            httpx.Response(200, json={"items": list(range(10)), "total": 25}),
            httpx.Response(200, json={"items": list(range(10, 20)), "total": 25}),
            httpx.Response(200, json={"items": list(range(20, 25)), "total": 25}),
        ]
    )
    with _client(art) as c:
        pages = list(dispatch_paginated(c, "list_offset"))
    assert len(pages) == 3
    assert sum(len(p.body["items"]) for p in pages) == 25  # type: ignore[union-attr]


@respx.mock
def test_offset_pagination_partial_page_terminates() -> None:
    art = _artifact([_offset_op_with_total()])
    respx.get("https://api.example.com/v1/offset").mock(
        side_effect=[
            httpx.Response(200, json={"items": list(range(10)), "total": 13}),
            httpx.Response(200, json={"items": list(range(10, 13)), "total": 13}),
        ]
    )
    with _client(art) as c:
        pages = list(dispatch_paginated(c, "list_offset"))
    assert len(pages) == 2


# ---------------------------------------------------------------------------
# Link header pagination
# ---------------------------------------------------------------------------


def _link_op() -> dict[str, Any]:
    return {
        "id": "list_link",
        "summary": "Link header.",
        "idempotency": "idempotent",
        "request": {"method": "GET", "path": "/v1/items"},
        "response": {"200": {"description": "ok"}, "default": {"description": "fallback"}},
        "pagination": {"pattern": "link_header"},
    }


@respx.mock
def test_link_header_pagination_follows_next() -> None:
    art = _artifact([_link_op()])
    # respx routes match by URL+optional query; route_p2 must come before
    # the query-less route or respx matches the broader route first.
    respx.get("https://api.example.com/v1/items", params={"page": "2"}).mock(
        return_value=httpx.Response(
            200,
            json={"items": [3]},
            # No Link header → end
        )
    )
    respx.get("https://api.example.com/v1/items").mock(
        return_value=httpx.Response(
            200,
            json={"items": [1, 2]},
            headers={"Link": '<https://api.example.com/v1/items?page=2>; rel="next"'},
        )
    )
    with _client(art) as c:
        pages = list(dispatch_paginated(c, "list_link"))
    assert len(pages) == 2
    assert all(isinstance(p, DispatchSuccess) for p in pages)


@respx.mock
def test_link_header_cross_origin_terminates_with_warning() -> None:
    art = _artifact([_link_op()])
    respx.get("https://api.example.com/v1/items").mock(
        return_value=httpx.Response(
            200,
            json={},
            headers={"Link": '<https://other-host.example/p2>; rel="next"'},
        )
    )
    with _client(art) as c:
        pages = list(dispatch_paginated(c, "list_link"))
    # Yields the first page (success) then a DispatchError describing the
    # cross-origin termination.
    assert len(pages) == 2
    assert isinstance(pages[0], DispatchSuccess)
    assert isinstance(pages[1], DispatchError)
    assert "cross-origin" in pages[1].message


# ---------------------------------------------------------------------------
# None / no pagination
# ---------------------------------------------------------------------------


@respx.mock
def test_no_pagination_yields_single_page() -> None:
    art = _artifact(
        [
            {
                "id": "fetch_one",
                "summary": "Fetch one.",
                "idempotency": "idempotent",
                "request": {"method": "GET", "path": "/v1/one"},
                "response": {"200": {"description": "ok"}, "default": {"description": "fallback"}},
            }
        ]
    )
    respx.get("https://api.example.com/v1/one").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    with _client(art) as c:
        pages = list(dispatch_paginated(c, "fetch_one"))
    assert len(pages) == 1


# ---------------------------------------------------------------------------
# Safety limit
# ---------------------------------------------------------------------------


@respx.mock
def test_max_pages_safety_limit() -> None:
    art = _artifact([_cursor_op()])
    # Provider returns distinct cursor on every call so the loop would
    # otherwise iterate indefinitely; the safety limit caps it at 5.
    counter = [0]

    def _next_page(request: httpx.Request) -> httpx.Response:
        counter[0] += 1
        return httpx.Response(200, json={"nextPageToken": f"tok-{counter[0]}"})

    respx.get("https://api.example.com/v1/messages").mock(side_effect=_next_page)
    with _client(art) as c:
        pages = list(dispatch_paginated(c, "list_messages", max_pages=5))
    # Five successful pages then a DispatchError indicating the limit hit
    assert len(pages) == 6
    assert isinstance(pages[-1], DispatchError)
    assert "max-pages" in pages[-1].message


# ---------------------------------------------------------------------------
# Failure mid-loop
# ---------------------------------------------------------------------------


@respx.mock
def test_failure_mid_loop_yielded_as_last() -> None:
    art = _artifact([_cursor_op()])
    respx.get("https://api.example.com/v1/messages").mock(
        side_effect=[
            httpx.Response(200, json={"nextPageToken": "p2"}),
            httpx.Response(404, json={"error": "gone"}),
        ]
    )
    with _client(art) as c:
        pages = list(dispatch_paginated(c, "list_messages"))
    assert len(pages) == 2
    assert isinstance(pages[0], DispatchSuccess)
    assert isinstance(pages[1], DispatchError)
    assert pages[1].code == "not_found"
