"""Mock-based end-to-end test for the Slack pipeline.

Parallel to test_end_to_end_mock.py (Google) — loads each Slack .uacp
artifact, mocks the Slack Web API, dispatches through the full stack
(spec loader → workspace OAuth auth → dispatch client → envelope
handler → response normalization), and asserts request shape +
canonical error shape.

This validates that the §3.3 + §4.6 body-predicate machinery works
end-to-end against a Slack-shaped artifact without requiring real
OAuth credentials.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from uacp_prototype.auth.oauth2_workspace import OAuth2WorkspaceMethod
from uacp_prototype.dispatch.client import DispatchClient, DispatchError, DispatchSuccess
from uacp_prototype.dispatch.pagination import dispatch_paginated
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "slack"
CHAT_FILE = EXAMPLES_DIR / "chat-postMessage.uacp"
LIST_FILE = EXAMPLES_DIR / "conversations-list.uacp"


def _client(artifact: Any, *, sleep: Any = lambda _s: None) -> DispatchClient:
    return DispatchClient(
        artifact,
        auth_method=OAuth2WorkspaceMethod(),
        credential_resolver=lambda: {"bot_access_token": "xoxb-MOCK-BOT-TOKEN"},
        sleep=sleep,
    )


# ---------------------------------------------------------------------------
# chat.postMessage
# ---------------------------------------------------------------------------


@respx.mock
def test_chat_postmessage_success_end_to_end_mock() -> None:
    artifact = load(CHAT_FILE)
    assert artifact.authentication.method == "x-oauth2-workspace"

    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "ts": "1715000000.000123",
                "channel": "C0123456789",
                "message": {"text": "hello", "type": "message"},
            },
        )
    )
    client = _client(artifact)
    try:
        result = client.dispatch(
            "chat_postmessage",
            body={"channel": "C0123456789", "text": "hello"},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess)
    assert result.body["ok"] is True
    assert result.body["ts"] == "1715000000.000123"

    request = route.calls[0].request
    assert request.method == "POST"
    assert str(request.url) == "https://slack.com/api/chat.postMessage"
    assert request.headers["Authorization"] == "Bearer xoxb-MOCK-BOT-TOKEN"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == "uacp-prototype/0.1"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.read()) == {"channel": "C0123456789", "text": "hello"}


@respx.mock
def test_chat_postmessage_envelope_failure_end_to_end_mock() -> None:
    """Slack 200 + ok=false converts to DispatchError(code=not_found) via
    the §3.3/§4.6 body-predicate machinery."""
    artifact = load(CHAT_FILE)
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    client = _client(artifact)
    try:
        result = client.dispatch(
            "chat_postmessage",
            body={"channel": "Cnope", "text": "anybody home?"},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchError)
    assert result.status == 200
    assert result.code == "not_found"
    assert result.details["error"] == "channel_not_found"
    assert result.raw == {"ok": False, "error": "channel_not_found"}


# ---------------------------------------------------------------------------
# conversations.list — cursor pagination via nested $.response_metadata.next_cursor
# ---------------------------------------------------------------------------


@respx.mock
def test_conversations_list_cursor_pagination_end_to_end_mock() -> None:
    """Mock two pages with the cursor at the nested
    $.response_metadata.next_cursor location. Validates §3.4's JSONPath
    subset against deep nesting and confirms the dispatch_paginated
    iterator advances correctly."""
    artifact = load(LIST_FILE)
    op = artifact.operations[0]
    assert op.pagination.pattern == "cursor"
    assert op.pagination.response_cursor_path == "$.response_metadata.next_cursor"

    page1 = {
        "ok": True,
        "channels": [
            {"id": "C001", "name": "general", "is_channel": True},
            {"id": "C002", "name": "random", "is_channel": True},
        ],
        "response_metadata": {"next_cursor": "dXNlcjpVMDYxTkZUVDI="},
    }
    page2 = {
        "ok": True,
        "channels": [{"id": "C003", "name": "team", "is_channel": True}],
        # response_metadata.next_cursor empty/absent → end of pagination
        "response_metadata": {"next_cursor": ""},
    }

    # Order matters: query-string-bearing route registered first so respx
    # matches it before the unrestricted route.
    respx.get(
        "https://slack.com/api/conversations.list",
        params={"cursor": "dXNlcjpVMDYxTkZUVDI="},
    ).mock(return_value=httpx.Response(200, json=page2))
    respx.get("https://slack.com/api/conversations.list").mock(
        return_value=httpx.Response(200, json=page1)
    )

    client = _client(artifact)
    try:
        pages = list(
            dispatch_paginated(
                client,
                "conversations_list",
                query={"limit": 2, "types": "public_channel"},
            )
        )
    finally:
        client.close()

    assert len(pages) == 2
    assert all(isinstance(p, DispatchSuccess) for p in pages)
    assert [c["id"] for c in pages[0].body["channels"]] == ["C001", "C002"]
    assert [c["id"] for c in pages[1].body["channels"]] == ["C003"]


@respx.mock
def test_conversations_list_envelope_failure_terminates_pagination() -> None:
    """If the first page returns ok=false (e.g. invalid_auth), the iterator
    yields a DispatchError as the final item and stops. No subsequent
    pages are fetched."""
    artifact = load(LIST_FILE)
    respx.get("https://slack.com/api/conversations.list").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    )
    client = _client(artifact)
    try:
        pages = list(
            dispatch_paginated(
                client,
                "conversations_list",
                query={"limit": 2, "types": "public_channel"},
            )
        )
    finally:
        client.close()

    assert len(pages) == 1
    assert isinstance(pages[0], DispatchError)
    assert pages[0].code == "auth_expired"  # invalid_auth → auth_expired per §4.6 mapping


# ---------------------------------------------------------------------------
# Both .uacp files load cleanly
# ---------------------------------------------------------------------------


def test_slack_examples_load_clean() -> None:
    for name in ("chat-postMessage.uacp", "conversations-list.uacp"):
        path = EXAMPLES_DIR / name
        artifact = load(path)
        assert artifact.authentication.method == "x-oauth2-workspace"
        assert len(artifact.operations) == 1
