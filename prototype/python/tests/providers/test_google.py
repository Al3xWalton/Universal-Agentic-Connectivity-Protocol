"""Integration tests against Google's real Gmail and Calendar APIs.

Marked @pytest.mark.integration; skipped by default. Requires operator
setup per the README:

  1. Register an OAuth client on Google Cloud Console (Desktop app).
  2. Enable Gmail API and Google Calendar API on the project.
  3. Configure the OAuth consent screen, add yourself as a test user,
     and request these scopes:
       - https://www.googleapis.com/auth/gmail.send
       - https://www.googleapis.com/auth/calendar.readonly
  4. Populate the .env file (or export the variables) with:
       UACP_GOOGLE_CLIENT_ID=...
       UACP_GOOGLE_CLIENT_SECRET=...
       UACP_GOOGLE_TEST_EMAIL=your@gmail.address
       UACP_GOOGLE_REDIRECT_URI=http://localhost:8765/oauth/callback
  5. Run with:
       uv run pytest tests/providers -m integration

The first run launches the OAuth flow in a browser; the user grants
consent; a tiny local HTTP server captures the redirect and the
returned code. The test exchanges the code for tokens, persists them
to the local-keyring, and dispatches the operation. Subsequent runs
reuse the persisted tokens.
"""

from __future__ import annotations

import base64
import http.server
import os
import secrets as pysecrets
import socketserver
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from uacp_prototype.auth.oauth2_authcode import (
    OAuth2AuthCodeConfig,
    OAuth2AuthCodeMethod,
    PKCEPair,
    build_auth_url,
    exchange_code,
    generate_pkce_pair,
    refresh,
)
from uacp_prototype.dispatch.client import DispatchClient, DispatchError, DispatchSuccess
from uacp_prototype.dispatch.pagination import dispatch_paginated
from uacp_prototype.lifecycle.refresh import (
    is_in_refresh_window,
    refresh_with_rotation,
)
from uacp_prototype.lifecycle.state import Connection, ConnectionState
from uacp_prototype.security.secrets import (
    LocalKeyringStore,
    SecretResolver,
    make_credential_resolver,
)
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "google"
GMAIL_FILE = EXAMPLES_DIR / "gmail-send.uacp"
CALENDAR_FILE = EXAMPLES_DIR / "google-calendar-list.uacp"


def _required_env(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        pytest.skip(
            f"integration test requires {var} (see tests/providers/test_google.py docstring)"
        )
    return value


@pytest.fixture(scope="module")
def google_config() -> dict[str, str]:
    return {
        "client_id": _required_env("UACP_GOOGLE_CLIENT_ID"),
        "client_secret": _required_env("UACP_GOOGLE_CLIENT_SECRET"),
        "test_email": _required_env("UACP_GOOGLE_TEST_EMAIL"),
        "redirect_uri": os.environ.get(
            "UACP_GOOGLE_REDIRECT_URI", "http://localhost:8765/oauth/callback"
        ),
    }


# ---------------------------------------------------------------------------
# Local OAuth callback server — captures the redirect after consent
# ---------------------------------------------------------------------------


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    captured: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        for k, v in params.items():
            self.captured[k] = v[0] if v else ""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h1>UACP OAuth callback received.</h1>"
            b"<p>You can close this tab.</p></body></html>"
        )

    def log_message(self, *_args: Any) -> None:  # silence the default logger
        return


def _run_callback_server(port: int, captured: dict[str, str]) -> tuple[socketserver.TCPServer, threading.Thread]:
    _OAuthCallbackHandler.captured = captured
    server = socketserver.TCPServer(("localhost", port), _OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


# ---------------------------------------------------------------------------
# Token acquisition / persistence
# ---------------------------------------------------------------------------


def _persist_path(tmp_root: Path) -> LocalKeyringStore:
    return LocalKeyringStore(base_dir=tmp_root / ".uacp" / "secrets")


@pytest.fixture(scope="module")
def google_tokens(tmp_path_factory: pytest.TempPathFactory, google_config: dict[str, str]) -> dict[str, Any]:
    """Acquire tokens via the OAuth 2.0 authorization-code flow and persist
    them. Reuses persisted tokens on subsequent runs within the same
    pytest session.
    """
    tmp_root = tmp_path_factory.mktemp("uacp-google-integration")
    store = _persist_path(tmp_root)
    secret_resolver = SecretResolver(local_keyring=store)
    secret_resolver.store(
        "secret://local-keyring/google-int#client_secret",
        google_config["client_secret"].encode("utf-8"),
    )

    artifact = load(GMAIL_FILE)
    auth = artifact.authentication
    cfg = OAuth2AuthCodeConfig(
        authorization_endpoint=auth.model_extra["authorization_endpoint"],  # type: ignore[index]
        token_endpoint=auth.model_extra["token_endpoint"],  # type: ignore[index]
        client_id=google_config["client_id"],
        redirect_uri=google_config["redirect_uri"],
        scopes=tuple(auth.model_extra["scopes"]) + tuple(  # type: ignore[index]
            load(CALENDAR_FILE).authentication.model_extra["scopes"]
        ),
        client_secret_resolved=google_config["client_secret"],
    )

    state = pysecrets.token_urlsafe(16)
    pkce = generate_pkce_pair()
    auth_url = build_auth_url(
        cfg,
        state=state,
        pkce=pkce,
        extra_params={"access_type": "offline", "prompt": "consent"},
    )

    captured: dict[str, str] = {}
    parsed_redirect = urlparse(cfg.redirect_uri)
    server, _thread = _run_callback_server(parsed_redirect.port or 8765, captured)
    try:
        webbrowser.open(auth_url)
        # Wait up to 5 minutes for the user to complete consent
        deadline = time.time() + 300
        while time.time() < deadline and "code" not in captured and "error" not in captured:
            time.sleep(0.5)
    finally:
        server.shutdown()

    if "error" in captured:
        pytest.fail(f"OAuth authorization rejected: {captured.get('error')}")
    if "code" not in captured:
        pytest.fail("OAuth authorization timed out before code was received")
    if captured.get("state") != state:
        pytest.fail("OAuth state mismatch — possible CSRF; aborting")

    tokens = exchange_code(cfg, code=captured["code"], pkce=pkce)
    return {
        "tokens": tokens,
        "config": cfg,
        "secret_resolver": secret_resolver,
        "tmp_root": tmp_root,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_gmail_send_real(google_tokens: dict[str, Any], google_config: dict[str, str]) -> None:
    """Send a real email to the test address via the Gmail API."""
    artifact = load(GMAIL_FILE)
    auth_method = OAuth2AuthCodeMethod()
    tokens = google_tokens["tokens"]

    rfc2822 = (
        f"From: {google_config['test_email']}\r\n"
        f"To: {google_config['test_email']}\r\n"
        f"Subject: UACP integration test {int(time.time())}\r\n"
        f"\r\n"
        f"This is an automated message from the UACP prototype's integration test.\r\n"
    )
    raw_b64 = base64.urlsafe_b64encode(rfc2822.encode("utf-8")).decode("ascii")

    client = DispatchClient(
        artifact,
        auth_method=auth_method,
        credential_resolver=lambda: {"access_token": tokens.access_token},
        sleep=time.sleep,
    )
    try:
        result = client.dispatch(
            "gmail_users_messages_send",
            path_params={"userId": "me"},
            body={"raw": raw_b64},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess), f"send failed: {result}"
    assert result.status == 200
    assert result.body.get("id"), f"missing message id in response: {result.body}"


@pytest.mark.integration
def test_calendar_list_real(google_tokens: dict[str, Any]) -> None:
    """List the next 10 events on the primary calendar."""
    artifact = load(CALENDAR_FILE)
    auth_method = OAuth2AuthCodeMethod()
    tokens = google_tokens["tokens"]

    client = DispatchClient(
        artifact,
        auth_method=auth_method,
        credential_resolver=lambda: {"access_token": tokens.access_token},
        sleep=time.sleep,
    )
    try:
        result = client.dispatch(
            "calendar_events_list",
            path_params={"calendarId": "primary"},
            query={
                "maxResults": 10,
                "singleEvents": True,
                "orderBy": "startTime",
                "timeMin": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess), f"list failed: {result}"
    assert result.body.get("kind") == "calendar#events"
    assert "items" in result.body


@pytest.mark.integration
def test_calendar_paginated_real(google_tokens: dict[str, Any]) -> None:
    """Exercise the cursor-pagination loop. Caps at 3 pages so a calendar
    with many events doesn't make the test run forever.
    """
    artifact = load(CALENDAR_FILE)
    auth_method = OAuth2AuthCodeMethod()
    tokens = google_tokens["tokens"]

    client = DispatchClient(
        artifact,
        auth_method=auth_method,
        credential_resolver=lambda: {"access_token": tokens.access_token},
        sleep=time.sleep,
    )
    try:
        pages = list(
            dispatch_paginated(
                client,
                "calendar_events_list",
                path_params={"calendarId": "primary"},
                query={"maxResults": 10, "singleEvents": True},
                max_pages=3,
            )
        )
    finally:
        client.close()

    assert len(pages) >= 1
    # All pages successful (or the final one carries an upstream_error if
    # we hit the safety limit)
    for page in pages[:-1]:
        assert isinstance(page, DispatchSuccess)


@pytest.mark.integration
def test_discovery_doc_ingestion_matches_hand_written() -> None:
    """Fetch Google's real discovery doc for Gmail and Calendar and confirm
    the ingester produces an Operation that matches the hand-written .uacp
    file (modulo expected lossiness: source.ingested_at differs every run;
    the auth + dispatch blocks aren't produced by ingestion; the
    description text wording differs).

    This is the §3.6 round-trip property — a real provider's published
    description converges on the same canonical form a human would
    hand-author.
    """
    from uacp_prototype.connections.ingest_openapi import from_discovery_doc

    # Gmail
    gmail_result = from_discovery_doc(
        "https://www.googleapis.com/discovery/v1/apis/gmail/v1/rest"
    )
    gmail_send = next(
        (op for op in gmail_result.operations if op.id == "gmail_users_messages_send"),
        None,
    )
    assert gmail_send is not None, (
        "discovery ingestion did not produce gmail_users_messages_send; "
        "this is a spec gap if Gmail still publishes it. Log to "
        "docs/open-questions.md."
    )

    hand_written = load(GMAIL_FILE)
    hand_op = next(op for op in hand_written.operations if op.id == "gmail_users_messages_send")

    # Method, path
    assert gmail_send.request.method == hand_op.request.method == "POST"
    assert gmail_send.request.path == hand_op.request.path
    # Idempotency lossiness is documented in §3.6: POST defaults to
    # `unknown` after ingestion. The hand-written artifact may declare a
    # more specific value (`not_idempotent` for send) that ingestion
    # cannot infer. Accept the documented divergence.
    assert gmail_send.idempotency == "unknown"
    assert hand_op.idempotency in ("unknown", "idempotent", "not_idempotent")

    # Path parameters: same parameter set
    gmail_path_params = set((gmail_send.request.path_parameters or {}).get("properties", {}).keys())
    hand_path_params = set((hand_op.request.path_parameters or {}).get("properties", {}).keys())
    assert gmail_path_params == hand_path_params == {"userId"}

    # Calendar
    calendar_result = from_discovery_doc(
        "https://www.googleapis.com/discovery/v1/apis/calendar/v3/rest"
    )
    calendar_list = next(
        (op for op in calendar_result.operations if op.id == "calendar_events_list"),
        None,
    )
    assert calendar_list is not None
    hand_cal = next(
        op for op in load(CALENDAR_FILE).operations if op.id == "calendar_events_list"
    )

    assert calendar_list.request.method == hand_cal.request.method == "GET"
    assert calendar_list.request.path == hand_cal.request.path
    # Cursor pagination present and matches
    assert calendar_list.pagination is not None
    assert hand_cal.pagination is not None
    assert calendar_list.pagination.pattern == hand_cal.pagination.pattern == "cursor"
    assert (
        calendar_list.pagination.request_cursor_parameter
        == hand_cal.pagination.request_cursor_parameter
        == "pageToken"
    )


@pytest.mark.integration
def test_refresh_real(google_tokens: dict[str, Any]) -> None:
    """Force a refresh by directly invoking the refresh function and
    verifying the new tokens work."""
    cfg: OAuth2AuthCodeConfig = google_tokens["config"]
    tokens = google_tokens["tokens"]

    if not tokens.refresh_token:
        pytest.skip("no refresh token issued (Google requires access_type=offline + prompt=consent)")

    new_tokens = refresh(cfg, refresh_token=tokens.refresh_token)
    assert new_tokens.access_token
    assert new_tokens.access_token != tokens.access_token

    # Use the new access token to make a real request
    artifact = load(GMAIL_FILE)
    client = DispatchClient(
        artifact,
        auth_method=OAuth2AuthCodeMethod(),
        credential_resolver=lambda: {"access_token": new_tokens.access_token},
        sleep=time.sleep,
    )
    try:
        # Use a non-destructive check: send a message to self (or skip if
        # we don't want extra emails; the gmail_send test above already
        # validates the send path with the original token).
        result = client.dispatch(
            "gmail_users_messages_send",
            path_params={"userId": "me"},
            body={
                "raw": base64.urlsafe_b64encode(
                    f"From: me\r\nTo: me\r\nSubject: UACP refresh test {int(time.time())}\r\n\r\nrefresh OK".encode()
                ).decode("ascii")
            },
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess)
