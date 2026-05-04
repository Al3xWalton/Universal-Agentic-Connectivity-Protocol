"""Mock-based end-to-end test.

Loads the Gmail .uacp artifact, mocks the Gmail API endpoint, dispatches
the send operation through the full stack (spec loader → auth subsystem
→ dispatch client → response normalization), and asserts that the
request shape matches what Google's Gmail API expects.

This is the test the brief calls "mock-based dispatch end-to-end":
proof that the spec layer + auth layer + dispatch layer compose
correctly, without requiring real OAuth credentials or real API calls.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from uacp_prototype.auth.oauth2_authcode import OAuth2AuthCodeMethod
from uacp_prototype.dispatch.client import DispatchClient, DispatchSuccess
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "google"


@respx.mock
def test_gmail_send_end_to_end_mock() -> None:
    """Load gmail-send.uacp, mock the Gmail API, dispatch, assert request
    shape matches Google's expectations.
    """
    artifact = load(EXAMPLES_DIR / "gmail-send.uacp")
    assert artifact.authentication.method == "oauth2_authorization_code"

    # Mock the Gmail API endpoint
    route = respx.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "1814f5cd0a6e9d23",
                "threadId": "1814f5cd0a6e9d23",
                "labelIds": ["SENT"],
            },
        )
    )

    auth_method = OAuth2AuthCodeMethod()
    creds = {"access_token": "ya29.MOCK_ACCESS_TOKEN"}
    client = DispatchClient(
        artifact,
        auth_method=auth_method,
        credential_resolver=lambda: creds,
        sleep=lambda _s: None,
    )
    try:
        # Compose an RFC 2822 message and base64url-encode it as the API expects
        rfc2822 = (
            "From: me@example.com\r\n"
            "To: alice@example.com\r\n"
            "Subject: Test from UACP prototype\r\n"
            "\r\n"
            "Hello, this is a test message."
        )
        raw_b64 = base64.urlsafe_b64encode(rfc2822.encode("utf-8")).decode("ascii")

        result = client.dispatch(
            "gmail_users_messages_send",
            path_params={"userId": "me"},
            body={"raw": raw_b64},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess)
    assert result.status == 200
    assert result.body["id"] == "1814f5cd0a6e9d23"

    # Verify the request shape
    assert route.called
    request = route.calls[0].request
    assert request.method == "POST"
    assert (
        str(request.url)
        == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    )
    # Auth header
    assert request.headers["Authorization"] == "Bearer ya29.MOCK_ACCESS_TOKEN"
    # Default headers from the artifact
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == "uacp-prototype/0.1"
    # JSON body matching the Message schema
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.read())
    assert body == {"raw": raw_b64}


@respx.mock
def test_calendar_list_end_to_end_mock_with_pagination() -> None:
    """Load google-calendar-list.uacp, mock two pages of events, dispatch
    via the paginated entry, assert both pages flow.
    """
    from uacp_prototype.dispatch.pagination import dispatch_paginated

    artifact = load(EXAMPLES_DIR / "google-calendar-list.uacp")
    op = artifact.operations[0]
    assert op.pagination is not None
    assert op.pagination.pattern == "cursor"

    # Mock two pages — second page's request includes pageToken=tok-1
    page1 = {
        "kind": "calendar#events",
        "items": [
            {"id": "ev1", "summary": "Event 1"},
            {"id": "ev2", "summary": "Event 2"},
        ],
        "nextPageToken": "tok-1",
    }
    page2 = {
        "kind": "calendar#events",
        "items": [{"id": "ev3", "summary": "Event 3"}],
        # No nextPageToken → end of pagination
    }
    # Order matters: query-string-bearing route registered first so respx
    # matches it before the unrestricted route.
    respx.get(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        params={"pageToken": "tok-1"},
    ).mock(return_value=httpx.Response(200, json=page2))
    respx.get(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    ).mock(return_value=httpx.Response(200, json=page1))

    auth_method = OAuth2AuthCodeMethod()
    client = DispatchClient(
        artifact,
        auth_method=auth_method,
        credential_resolver=lambda: {"access_token": "ya29.MOCK"},
        sleep=lambda _s: None,
    )
    try:
        pages = list(
            dispatch_paginated(
                client,
                "calendar_events_list",
                path_params={"calendarId": "primary"},
                query={"maxResults": 50, "singleEvents": True},
            )
        )
    finally:
        client.close()

    assert len(pages) == 2
    assert all(isinstance(p, DispatchSuccess) for p in pages)
    assert pages[0].body["nextPageToken"] == "tok-1"
    assert "nextPageToken" not in pages[1].body
    items = []
    for p in pages:
        items.extend(p.body["items"])
    assert [i["id"] for i in items] == ["ev1", "ev2", "ev3"]


def test_examples_load_clean() -> None:
    """Both example .uacp files load and validate."""
    for name in ("gmail-send.uacp", "google-calendar-list.uacp"):
        path = EXAMPLES_DIR / name
        artifact = load(path)
        assert artifact.authentication.method == "oauth2_authorization_code"
        assert len(artifact.operations) >= 1


def test_cli_validate_smoke(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The `uacp validate` CLI produces a summary on a valid file."""
    from uacp_prototype.cli import main

    rc = main(["validate", str(EXAMPLES_DIR / "gmail-send.uacp")])
    captured = capsys.readouterr()
    assert rc == 0
    assert "gmail_users_messages_send" in captured.out
    assert "POST /gmail/v1/users/{userId}/messages/send" in captured.out


def test_cli_validate_invalid_file_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from uacp_prototype.cli import main

    bad = tmp_path / "bad.uacp"
    bad.write_text("{ this is not json")
    rc = main(["validate", str(bad)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "validation failed" in captured.err
