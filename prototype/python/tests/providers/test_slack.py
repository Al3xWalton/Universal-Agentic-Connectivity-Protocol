"""Integration tests against Slack's real Web API.

Marked @pytest.mark.integration; skipped by default. Requires operator
setup per the README:

  1. Create a Slack app at https://api.slack.com/apps → "Create New App"
     → "From scratch". Choose a development workspace you control.
  2. OAuth & Permissions → Bot Token Scopes → add ``chat:write`` and
     ``channels:read``. (For optional message-history tests, also add
     ``channels:history``.) Add a Redirect URL of
     ``http://localhost:8765/oauth/callback``.
  3. Install to Workspace. Copy the Client ID and Client Secret from
     "Basic Information".
  4. In your test workspace, create or pick a channel (the bot will be
     auto-invited if you ``/invite @<botname>`` from the channel after
     install). Capture its channel id (Cxxxxxxxxxx).
  5. Populate the .env (or export the variables) with:
       UACP_SLACK_CLIENT_ID=<client id>
       UACP_SLACK_CLIENT_SECRET=<client secret>
       UACP_SLACK_TEST_CHANNEL_ID=Cxxxxxxxxxx
       UACP_SLACK_REDIRECT_URI=http://localhost:8765/oauth/callback
  6. Run with:
       uv run pytest tests/providers/test_slack.py -m integration

The first run launches the OAuth flow in a browser; the user grants
consent; a tiny local HTTP server captures the redirect and the
returned code. The test exchanges the code for tokens, persists them
to the local-keyring, and dispatches the operation. Subsequent runs
reuse the persisted tokens.
"""

from __future__ import annotations

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

import pytest

from uacp_prototype.auth.oauth2_workspace import (
    OAuth2WorkspaceConfig,
    OAuth2WorkspaceMethod,
    build_auth_url,
    exchange_code,
    generate_pkce_pair,
)
from uacp_prototype.dispatch.client import DispatchClient, DispatchError, DispatchSuccess
from uacp_prototype.dispatch.pagination import dispatch_paginated
from uacp_prototype.security.secrets import LocalKeyringStore, SecretResolver
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "slack"
CHAT_FILE = EXAMPLES_DIR / "chat-postMessage.uacp"
LIST_FILE = EXAMPLES_DIR / "conversations-list.uacp"


def _required_env(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        pytest.skip(
            f"integration test requires {var} (see tests/providers/test_slack.py docstring)"
        )
    return value


@pytest.fixture(scope="module")
def slack_config() -> dict[str, str]:
    return {
        "client_id": _required_env("UACP_SLACK_CLIENT_ID"),
        "client_secret": _required_env("UACP_SLACK_CLIENT_SECRET"),
        "test_channel_id": _required_env("UACP_SLACK_TEST_CHANNEL_ID"),
        "redirect_uri": os.environ.get(
            "UACP_SLACK_REDIRECT_URI", "http://localhost:8765/oauth/callback"
        ),
    }


# ---------------------------------------------------------------------------
# Local OAuth callback server
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
            b"<html><body><h1>UACP Slack OAuth callback received.</h1>"
            b"<p>You can close this tab.</p></body></html>"
        )

    def log_message(self, *_args: Any) -> None:
        return


def _run_callback_server(
    port: int, captured: dict[str, str]
) -> tuple[socketserver.TCPServer, threading.Thread]:
    _OAuthCallbackHandler.captured = captured
    server = socketserver.TCPServer(("localhost", port), _OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


# ---------------------------------------------------------------------------
# Token acquisition
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def slack_tokens(
    tmp_path_factory: pytest.TempPathFactory, slack_config: dict[str, str]
) -> dict[str, Any]:
    """Acquire workspace-scoped tokens via OAuth and cache for the module."""
    tmp_root = tmp_path_factory.mktemp("uacp-slack-integration")
    store = LocalKeyringStore(base_dir=tmp_root / ".uacp" / "secrets")
    secret_resolver = SecretResolver(local_keyring=store)
    secret_resolver.store(
        "secret://local-keyring/slack-int#client_secret",
        slack_config["client_secret"].encode("utf-8"),
    )

    artifact = load(CHAT_FILE)
    auth = artifact.authentication
    cfg = OAuth2WorkspaceConfig(
        authorization_endpoint=auth.model_extra["authorization_endpoint"],  # type: ignore[index]
        token_endpoint=auth.model_extra["token_endpoint"],  # type: ignore[index]
        client_id=slack_config["client_id"],
        redirect_uri=slack_config["redirect_uri"],
        # Combine bot scopes from both .uacp files for the consent screen.
        scopes=("chat:write", "channels:read"),
        user_scopes=(),
        client_secret_resolved=slack_config["client_secret"],
    )

    state = pysecrets.token_urlsafe(16)
    pkce = generate_pkce_pair()
    auth_url = build_auth_url(cfg, state=state, pkce=pkce)

    captured: dict[str, str] = {}
    parsed_redirect = urlparse(cfg.redirect_uri)
    server, _thread = _run_callback_server(parsed_redirect.port or 8765, captured)
    try:
        webbrowser.open(auth_url)
        deadline = time.time() + 300
        while time.time() < deadline and "code" not in captured and "error" not in captured:
            time.sleep(0.5)
    finally:
        server.shutdown()

    if "error" in captured:
        pytest.fail(f"Slack OAuth authorization rejected: {captured.get('error')}")
    if "code" not in captured:
        pytest.fail("Slack OAuth authorization timed out before code was received")
    if captured.get("state") != state:
        pytest.fail("Slack OAuth state mismatch — possible CSRF; aborting")

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
def test_oauth_flow_end_to_end(slack_tokens: dict[str, Any]) -> None:
    """The OAuth fixture itself proves the end-to-end flow: build_auth_url
    → consent → exchange_code → WorkspaceTokens. This test only asserts
    that the tokens parsed correctly."""
    tokens = slack_tokens["tokens"]
    assert tokens.bot_access_token.startswith("xoxb-"), (
        f"expected xoxb-prefixed bot token; got {tokens.bot_access_token[:8]}..."
    )
    assert tokens.team_id, "team_id missing from Slack token response"
    assert tokens.bot_scope and "chat:write" in tokens.bot_scope


@pytest.mark.integration
def test_chat_postmessage_success(
    slack_tokens: dict[str, Any], slack_config: dict[str, str]
) -> None:
    """Send a real message to the configured test channel."""
    artifact = load(CHAT_FILE)
    tokens = slack_tokens["tokens"]
    client = DispatchClient(
        artifact,
        auth_method=OAuth2WorkspaceMethod(),
        credential_resolver=lambda: {"bot_access_token": tokens.bot_access_token},
        sleep=time.sleep,
    )
    try:
        result = client.dispatch(
            "chat_postmessage",
            body={
                "channel": slack_config["test_channel_id"],
                "text": f"UACP integration test — {int(time.time())}",
            },
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess), f"send failed: {result}"
    assert result.body["ok"] is True
    assert result.body.get("ts"), "Slack response missing ts"


@pytest.mark.integration
def test_chat_postmessage_invalid_channel_envelope_failure(
    slack_tokens: dict[str, Any],
) -> None:
    """Sending to a nonexistent channel returns HTTP 200 with ok=false +
    error="channel_not_found". The §3.3/§4.6 body-predicate
    machinery converts this into a canonical DispatchError with
    code=not_found, status=200 (faithful to wire), provider error
    string preserved in details."""
    artifact = load(CHAT_FILE)
    tokens = slack_tokens["tokens"]
    client = DispatchClient(
        artifact,
        auth_method=OAuth2WorkspaceMethod(),
        credential_resolver=lambda: {"bot_access_token": tokens.bot_access_token},
        sleep=time.sleep,
    )
    try:
        result = client.dispatch(
            "chat_postmessage",
            body={
                "channel": "C_NONEXISTENT_999999",
                "text": "this should never arrive",
            },
        )
    finally:
        client.close()

    assert isinstance(result, DispatchError), (
        f"expected DispatchError for invalid channel; got {result!r}"
    )
    assert result.status == 200, "Slack returns 200 with ok=false; status preserved"
    assert result.code == "not_found", (
        f"expected not_found from channel_not_found mapping; got {result.code}"
    )
    assert result.details.get("error") == "channel_not_found"


@pytest.mark.integration
def test_conversations_list_single_page(slack_tokens: dict[str, Any]) -> None:
    """List conversations with a single dispatch (not paginated); asserts
    the success path works against the cursor-pagination .uacp shape."""
    artifact = load(LIST_FILE)
    tokens = slack_tokens["tokens"]
    client = DispatchClient(
        artifact,
        auth_method=OAuth2WorkspaceMethod(),
        credential_resolver=lambda: {"bot_access_token": tokens.bot_access_token},
        sleep=time.sleep,
    )
    try:
        result = client.dispatch(
            "conversations_list",
            query={"limit": 5, "types": "public_channel"},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess), f"list failed: {result}"
    assert result.body["ok"] is True
    assert isinstance(result.body.get("channels"), list)


@pytest.mark.integration
def test_conversations_list_paginated(slack_tokens: dict[str, Any]) -> None:
    """Exercise the cursor pagination loop end-to-end. Caps at 3 pages so a
    workspace with many channels doesn't make the test run forever. The
    interesting property under test: the cursor lives at the nested path
    response_metadata.next_cursor, not at a top-level field — validates
    §3.4's JSONPath subset against deep nesting."""
    artifact = load(LIST_FILE)
    tokens = slack_tokens["tokens"]
    client = DispatchClient(
        artifact,
        auth_method=OAuth2WorkspaceMethod(),
        credential_resolver=lambda: {"bot_access_token": tokens.bot_access_token},
        sleep=time.sleep,
    )
    try:
        pages = list(
            dispatch_paginated(
                client,
                "conversations_list",
                query={"limit": 2, "types": "public_channel"},
                max_pages=3,
            )
        )
    finally:
        client.close()

    assert len(pages) >= 1
    # All but possibly the last item are successful pages; the last item is
    # either the last successful page or a max-pages DispatchError.
    success_pages = [p for p in pages if isinstance(p, DispatchSuccess)]
    assert len(success_pages) >= 1
    for p in success_pages:
        assert p.body["ok"] is True
