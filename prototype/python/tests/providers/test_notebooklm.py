"""Integration tests against Google NotebookLM via session_cookie auth.

Marked ``@pytest.mark.integration``; skipped by default. Requires
operator setup per the README:

  1. Read §2.10 carefully and confirm you accept responsibility for
     ToS compliance. NotebookLM has no public API; the connection
     replays browser session cookies, which is a grey-zone practice
     that may violate Google's Terms of Service.
  2. Install Playwright in the prototype environment:
       uv run playwright install chromium
  3. Run the prototype's capture helper to perform an interactive
     login and capture storage_state:
       uv run python -m uacp_prototype.cli capture-storage-state \\
           --provider notebooklm \\
           --output ~/.uacp/storage/notebooklm.json
     A browser window opens; sign into Google → navigate to
     notebooklm.google.com → confirm a notebook is visible. The
     helper writes the cookies + origins to the output path.
  4. Populate the .env (or export the variables) with:
       UACP_NOTEBOOKLM_STORAGE_STATE=$HOME/.uacp/storage/notebooklm.json
       UACP_NOTEBOOKLM_TEST_NOTEBOOK_ID=<a notebook id you have access to>
       # Optional — skip if you only want list-notebooks tests:
       UACP_NOTEBOOKLM_TEST_MESSAGE="hello from uacp integration"
  5. Run with:
       uv run pytest tests/providers/test_notebooklm.py -m integration

Captured storage state is sensitive — treat it like a password. The
helper writes it with mode 0600.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from uacp_prototype.auth.session_cookie import (
    CSRFConfig,
    SessionCookieConfig,
    SessionCookieMethod,
)
from uacp_prototype.dispatch.client import DispatchClient, DispatchError, DispatchSuccess
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "examples" / "notebooklm"
)
LIST_FILE = EXAMPLES_DIR / "list-notebooks.uacp"
CHAT_FILE = EXAMPLES_DIR / "send-chat-message.uacp"


def _required_env(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        pytest.skip(
            f"integration test requires {var} "
            "(see tests/providers/test_notebooklm.py docstring)"
        )
    return value


def _client_for(artifact: Any, storage_path: str) -> DispatchClient:
    """Wire a DispatchClient with session_cookie auth and a storage
    state read from disk on demand (so a refresh recapture during
    development is picked up on the next dispatch)."""
    extra = artifact.authentication.model_extra or {}
    csrf_block = extra.get("csrf_token")
    csrf = None
    if isinstance(csrf_block, dict):
        csrf = CSRFConfig(
            header_name=csrf_block["header_name"],
            cookie_name=csrf_block.get("cookie_name"),
            refresh_url=csrf_block.get("refresh_url"),
            extraction_path=csrf_block.get("extraction_path"),
            extraction_format=csrf_block.get("extraction_format", "json"),
        )

    method = SessionCookieMethod(
        config=SessionCookieConfig(
            cookie_names=tuple(extra.get("cookie_names") or ()),
            csrf=csrf,
        )
    )

    def resolver() -> dict[str, Any]:
        text = Path(storage_path).read_text()
        return {"storage_state": text}

    return DispatchClient(
        artifact,
        auth_method=method,
        credential_resolver=resolver,
    )


# ---------------------------------------------------------------------------
# 1. list_notebooks_success
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_list_notebooks_success() -> None:
    """Authenticated list-notebooks call returns 200 and the body
    decodes as the expected text-format batchexecute response."""
    storage = _required_env("UACP_NOTEBOOKLM_STORAGE_STATE")
    artifact = load(LIST_FILE)
    client = _client_for(artifact, storage)
    try:
        result = client.dispatch(
            "notebooklm_list_notebooks",
            query={"rpcids": "wXbhsf"},
            body=b"f.req=" + b"%5B%5B%5B%22wXbhsf%22%2C%22%5Bnull%2C1%5D%22%2Cnull%2C%22generic%22%5D%5D%5D&at=",
        )
    finally:
        client.close()
    assert isinstance(result, DispatchSuccess), f"dispatch failed: {result}"
    # batchexecute responses are text — should start with the )]}',
    # sentinel.
    assert isinstance(result.body, str)
    assert result.body.startswith(")]}'") or result.body.startswith(")]}\\'")


# ---------------------------------------------------------------------------
# 2. list_notebooks_after_csrf_refresh
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_list_notebooks_after_csrf_refresh() -> None:
    """When the captured storage state contains a stale CSRF token but
    valid session cookies, the dispatcher's §2.10 refresh path acquires
    a fresh token and the retry succeeds.

    To exercise this path against the real provider, we mutate the
    in-memory storage state to inject a known-stale token before
    dispatch. The session cookies remain valid; the CSRF refresh URL
    yields a fresh token; the retry succeeds.
    """
    storage = _required_env("UACP_NOTEBOOKLM_STORAGE_STATE")
    storage_text = Path(storage).read_text()
    parsed = json.loads(storage_text)

    # Replace _csrf_token cookie value with a known-stale value so the
    # initial request's X-Same-Domain header is bad. The refresh URL
    # is fetched with the *cookies* (still valid), so it returns a
    # fresh token.
    found = False
    for cookie in parsed.get("cookies", []):
        if cookie.get("name") == "_csrf_token":
            cookie["value"] = "uacp-test-stale-csrf"
            found = True
            break
    if not found:
        pytest.skip(
            "captured storage state has no _csrf_token cookie; "
            "rerun the capture helper to refresh"
        )

    mutated_storage = json.dumps(parsed)
    artifact = load(LIST_FILE)
    extra = artifact.authentication.model_extra or {}
    csrf_block = extra.get("csrf_token") or {}

    method = SessionCookieMethod(
        config=SessionCookieConfig(
            cookie_names=tuple(extra.get("cookie_names") or ()),
            csrf=CSRFConfig(
                header_name=csrf_block["header_name"],
                cookie_name=csrf_block.get("cookie_name"),
                refresh_url=csrf_block.get("refresh_url"),
                extraction_path=csrf_block.get("extraction_path"),
                extraction_format=csrf_block.get("extraction_format", "json"),
            ),
        )
    )
    client = DispatchClient(
        artifact,
        auth_method=method,
        credential_resolver=lambda: {"storage_state": mutated_storage},
    )
    try:
        result = client.dispatch(
            "notebooklm_list_notebooks",
            query={"rpcids": "wXbhsf"},
            body=b"f.req=" + b"%5B%5B%5B%22wXbhsf%22%2C%22%5Bnull%2C1%5D%22%2Cnull%2C%22generic%22%5D%5D%5D&at=",
        )
    finally:
        client.close()
    assert isinstance(result, DispatchSuccess), (
        f"expected refresh-and-retry to succeed; got {result}"
    )


# ---------------------------------------------------------------------------
# 3. send_chat_message_success
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_send_chat_message_success() -> None:
    storage = _required_env("UACP_NOTEBOOKLM_STORAGE_STATE")
    notebook_id = _required_env("UACP_NOTEBOOKLM_TEST_NOTEBOOK_ID")
    message = os.environ.get(
        "UACP_NOTEBOOKLM_TEST_MESSAGE", "hello from uacp integration"
    )
    artifact = load(CHAT_FILE)
    client = _client_for(artifact, storage)
    try:
        # The body for send-chat-message is a URL-encoded f.req
        # carrying the notebook_id and message; we build a minimal
        # placeholder shape that real callers would construct via the
        # operation's body schema.
        from urllib.parse import quote

        f_req_payload = json.dumps(
            [[["BfMzVf", json.dumps([notebook_id, message]), None, "generic"]]]
        )
        body = f"f.req={quote(f_req_payload)}&at=".encode("utf-8")
        result = client.dispatch(
            "notebooklm_send_chat_message",
            query={"rpcids": "BfMzVf"},
            body=body,
        )
    finally:
        client.close()
    assert isinstance(result, DispatchSuccess), f"dispatch failed: {result}"


# ---------------------------------------------------------------------------
# 4. send_chat_message_with_invalid_notebook
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_send_chat_message_with_invalid_notebook() -> None:
    """An invalid notebook id returns a logical failure inside the
    batchexecute envelope; the dispatcher surfaces it as a
    DispatchError when the artifact declares failure_predicate, or as
    a DispatchSuccess carrying the error envelope otherwise. The
    NotebookLM artifact doesn't declare a failure_predicate (the RPC
    payload format is too provider-specific), so we accept either."""
    storage = _required_env("UACP_NOTEBOOKLM_STORAGE_STATE")
    artifact = load(CHAT_FILE)
    client = _client_for(artifact, storage)
    try:
        from urllib.parse import quote

        f_req_payload = json.dumps(
            [
                [
                    [
                        "BfMzVf",
                        json.dumps(
                            ["uacp-test-invalid-notebook-id", "test message"]
                        ),
                        None,
                        "generic",
                    ]
                ]
            ]
        )
        body = f"f.req={quote(f_req_payload)}&at=".encode("utf-8")
        result = client.dispatch(
            "notebooklm_send_chat_message",
            query={"rpcids": "BfMzVf"},
            body=body,
        )
    finally:
        client.close()
    # Any of these are acceptable — the test just confirms we get a
    # well-formed response shape without crashing.
    assert isinstance(result, (DispatchSuccess, DispatchError))


# ---------------------------------------------------------------------------
# 5. refusal_when_storage_state_missing
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_refusal_when_storage_state_missing() -> None:
    """When no storage_state is supplied, SessionCookieMethod.apply
    raises rather than silently dispatching. The dispatcher surfaces
    this as a typed exception, not a wire request."""
    from uacp_prototype.auth.session_cookie import SessionCookieAuthError

    artifact = load(LIST_FILE)
    extra = artifact.authentication.model_extra or {}
    csrf_block = extra.get("csrf_token") or {}
    method = SessionCookieMethod(
        config=SessionCookieConfig(
            cookie_names=tuple(extra.get("cookie_names") or ()),
            csrf=CSRFConfig(
                header_name=csrf_block["header_name"],
                cookie_name=csrf_block.get("cookie_name"),
                refresh_url=csrf_block.get("refresh_url"),
                extraction_path=csrf_block.get("extraction_path"),
                extraction_format=csrf_block.get("extraction_format", "json"),
            ),
        )
    )
    client = DispatchClient(
        artifact,
        auth_method=method,
        credential_resolver=lambda: {},  # no storage_state at all
    )
    try:
        with pytest.raises(SessionCookieAuthError):
            client.dispatch(
                "notebooklm_list_notebooks",
                query={"rpcids": "wXbhsf"},
            )
    finally:
        client.close()
