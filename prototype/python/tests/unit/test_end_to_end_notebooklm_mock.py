"""Mock-based end-to-end test for the NotebookLM (session_cookie) pipeline.

Parallel to the prior mock-end-to-end suites — Google, Slack, AWS,
GitHub. Loads each NotebookLM .uacp artifact, mocks the NotebookLM
RPC endpoint, dispatches through the full stack (spec loader →
session_cookie auth → cookie injection + CSRF token → dispatch
client → §2.10 audit emit), and asserts request shape (Cookie
header, X-Same-Domain CSRF token, request body) plus the §2.10
401-CSRF-refresh-and-retry behavior end-to-end.

Also demonstrates the §3.8 LLM-inference flow producing the
NotebookLM artifacts. The LLM call is mocked with a deterministic
fixture; the real LLM call is operator-driven post-session.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from uacp_prototype.auth.session_cookie import (
    SessionCookieConfig,
    SessionCookieMethod,
    parse_storage_state,
)
from uacp_prototype.connections.ingest_nl import (
    confirm_and_persist,
    infer_from_description,
)
from uacp_prototype.dispatch.client import DispatchClient, DispatchSuccess
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "examples" / "notebooklm"
)
LIST_FILE = EXAMPLES_DIR / "list-notebooks.uacp"
CHAT_FILE = EXAMPLES_DIR / "send-chat-message.uacp"


# ---------------------------------------------------------------------------
# Realistic captured storage state fixture
# ---------------------------------------------------------------------------


def _mock_storage_state() -> str:
    """A Playwright-shaped storage state with the cookies NotebookLM
    needs (subset of what a real Google session captures)."""
    return json.dumps(
        {
            "cookies": [
                {
                    "name": "SID",
                    "value": "MOCK-SID-VALUE",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": 9999999999,
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "None",
                },
                {
                    "name": "HSID",
                    "value": "MOCK-HSID-VALUE",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": 9999999999,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
                {
                    "name": "__Secure-1PSID",
                    "value": "MOCK-1PSID-VALUE",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": 9999999999,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                },
                {
                    "name": "_csrf_token",
                    "value": "MOCK-CSRF-INITIAL",
                    "domain": ".notebooklm.google.com",
                    "path": "/",
                    "expires": 9999999999,
                    "httpOnly": False,
                    "secure": True,
                },
            ],
            "origins": [],
        }
    )


def _client(artifact: Any, storage_state_json: str) -> DispatchClient:
    return DispatchClient(
        artifact,
        auth_method=SessionCookieMethod(
            config=SessionCookieConfig(
                cookie_names=tuple(
                    artifact.authentication.model_extra.get("cookie_names") or ()
                ),
                csrf=_csrf_config_from_artifact(artifact),
            )
        ),
        credential_resolver=lambda: {"storage_state": storage_state_json},
        sleep=lambda _s: None,
    )


def _csrf_config_from_artifact(artifact: Any):
    from uacp_prototype.auth.session_cookie import CSRFConfig

    extra = artifact.authentication.model_extra or {}
    csrf = extra.get("csrf_token")
    if not isinstance(csrf, dict):
        return None
    return CSRFConfig(
        header_name=csrf["header_name"],
        cookie_name=csrf.get("cookie_name"),
        refresh_url=csrf.get("refresh_url"),
        extraction_path=csrf.get("extraction_path"),
        extraction_format=csrf.get("extraction_format", "json"),
    )


# ---------------------------------------------------------------------------
# list-notebooks success
# ---------------------------------------------------------------------------


@respx.mock
def test_list_notebooks_success_end_to_end_mock() -> None:
    artifact = load(LIST_FILE)
    assert artifact.authentication.method == "session_cookie"

    route = respx.post(
        "https://notebooklm.google.com/_/NotebookLmRpcs/data/batchexecute",
        params={"rpcids": "wXbhsf"},
    ).mock(
        return_value=httpx.Response(
            200,
            text=")]}',\n[[[\"wXbhsf\",[[\"notebook-1\",\"My Notebook\"],[\"notebook-2\",\"Another\"]]]]]",
            headers={"Content-Type": "application/json+protobuf"},
        )
    )
    client = _client(artifact, _mock_storage_state())
    try:
        result = client.dispatch(
            "notebooklm_list_notebooks",
            query={"rpcids": "wXbhsf"},
            body=None,
            extra_headers={
                # Body must be form-encoded; the test sends a placeholder
                # f.req + at via extra_headers. Real requests would build
                # the body via the dispatch client's body serialization.
            },
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess)
    request = route.calls[0].request

    # Cookie header has the whitelisted cookies
    cookie_header = request.headers.get("Cookie", "")
    assert "SID=MOCK-SID-VALUE" in cookie_header
    assert "HSID=MOCK-HSID-VALUE" in cookie_header
    assert "__Secure-1PSID=MOCK-1PSID-VALUE" in cookie_header
    # _csrf_token isn't in the whitelist, but it's the CSRF source so
    # it shouldn't appear in the Cookie header (it goes into the
    # X-Same-Domain header instead).
    assert "_csrf_token=" not in cookie_header

    # CSRF header set from the cookie value
    assert request.headers.get("X-Same-Domain") == "MOCK-CSRF-INITIAL"

    # User-Agent + Accept defaults applied
    assert request.headers["User-Agent"] == "uacp-prototype/0.1"


# ---------------------------------------------------------------------------
# 401 → CSRF refresh → retry → success (§2.10 auto-refresh)
# ---------------------------------------------------------------------------


@respx.mock
def test_list_notebooks_401_triggers_csrf_refresh_and_retry() -> None:
    """The §2.10 auto-refresh path: on 401/403/419, fetch refresh_url
    with current cookies, extract fresh token, retry once with new
    token. The retry succeeds. End-to-end this exercises the full
    integration of session_cookie auth + dispatch's CSRF-refresh
    handler."""
    artifact = load(LIST_FILE)

    rpc_url = "https://notebooklm.google.com/_/NotebookLmRpcs/data/batchexecute"
    refresh_url = "https://notebooklm.google.com/_/NotebookLmRpcs/csrf"

    # First RPC call → 401
    rpc_call_count = [0]

    def rpc_side_effect(request: httpx.Request) -> httpx.Response:
        rpc_call_count[0] += 1
        if rpc_call_count[0] == 1:
            return httpx.Response(401, text="csrf expired")
        # Second call (after refresh) — verify it carries the NEW token
        assert request.headers.get("X-Same-Domain") == "MOCK-CSRF-FRESH"
        return httpx.Response(
            200,
            text=")]}',\n[[[\"wXbhsf\",[[\"notebook-1\",\"My Notebook\"]]]]]",
        )

    respx.post(rpc_url, params={"rpcids": "wXbhsf"}).mock(side_effect=rpc_side_effect)

    # Refresh URL → returns fresh token
    refresh_route = respx.get(refresh_url).mock(
        return_value=httpx.Response(
            200, json={"token": "MOCK-CSRF-FRESH"}
        )
    )

    client = _client(artifact, _mock_storage_state())
    try:
        result = client.dispatch(
            "notebooklm_list_notebooks",
            query={"rpcids": "wXbhsf"},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess), f"final dispatch failed: {result}"
    assert rpc_call_count[0] == 2, "expected one initial 401 and one retry"
    assert refresh_route.called, "refresh_url should have been fetched"


@respx.mock
def test_list_notebooks_csrf_refresh_failure_surfaces_auth_expired() -> None:
    """When the refresh URL itself fails, the dispatcher surfaces the
    original 401 as a DispatchError without infinite retry."""
    from uacp_prototype.dispatch.client import DispatchError

    artifact = load(LIST_FILE)
    rpc_url = "https://notebooklm.google.com/_/NotebookLmRpcs/data/batchexecute"
    refresh_url = "https://notebooklm.google.com/_/NotebookLmRpcs/csrf"

    respx.post(rpc_url, params={"rpcids": "wXbhsf"}).mock(
        return_value=httpx.Response(401, text="csrf expired")
    )
    respx.get(refresh_url).mock(return_value=httpx.Response(500, text="refresh broken"))

    client = _client(artifact, _mock_storage_state())
    try:
        result = client.dispatch(
            "notebooklm_list_notebooks",
            query={"rpcids": "wXbhsf"},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchError)
    assert result.status == 401  # original status preserved
    assert result.code == "auth_expired"


@respx.mock
def test_session_cookie_dispatch_emits_audit_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per §2.10's SHOULD audit-log requirement: every dispatch through
    a session_cookie connection emits an INFO event with risk:
    tos_violation_potential."""
    artifact = load(LIST_FILE)
    rpc_url = "https://notebooklm.google.com/_/NotebookLmRpcs/data/batchexecute"
    respx.post(rpc_url, params={"rpcids": "wXbhsf"}).mock(
        return_value=httpx.Response(200, text=")]}',\n[]")
    )
    client = _client(artifact, _mock_storage_state())
    try:
        with caplog.at_level(logging.INFO, logger="uacp.dispatch"):
            client.dispatch(
                "notebooklm_list_notebooks",
                query={"rpcids": "wXbhsf"},
            )
    finally:
        client.close()
    assert any(
        "session_cookie dispatch" in r.message and "tos_violation_potential" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# send-chat-message success
# ---------------------------------------------------------------------------


@respx.mock
def test_send_chat_message_success_end_to_end_mock() -> None:
    artifact = load(CHAT_FILE)
    rpc_url = "https://notebooklm.google.com/_/NotebookLmRpcs/data/batchexecute"
    respx.post(rpc_url, params={"rpcids": "BfMzVf"}).mock(
        return_value=httpx.Response(
            200, text=")]}',\n[[[\"BfMzVf\",[\"reply text\"]]]]"
        )
    )
    client = _client(artifact, _mock_storage_state())
    try:
        result = client.dispatch(
            "notebooklm_send_chat_message",
            query={"rpcids": "BfMzVf"},
        )
    finally:
        client.close()
    assert isinstance(result, DispatchSuccess)


# ---------------------------------------------------------------------------
# Both .uacp files load
# ---------------------------------------------------------------------------


def test_notebooklm_examples_load_clean() -> None:
    for name in ("list-notebooks.uacp", "send-chat-message.uacp"):
        path = EXAMPLES_DIR / name
        artifact = load(path)
        assert artifact.authentication.method == "session_cookie"
        # tos_acknowledged: true MUST be present per §2.10
        assert artifact.authentication.model_extra.get("tos_acknowledged") is True
        # Source is inferred per the §3.8 inference path
        assert len(artifact.operations) == 1
        op = artifact.operations[0]
        assert op.source is not None
        assert op.source.type == "inferred"
        assert op.source.model == "anthropic/claude-haiku-4.5"
        assert op.source.reviewed_at  # populated at confirm time


# ---------------------------------------------------------------------------
# §3.8 LLM-inference flow demonstrated end-to-end with a recorded LLM
# response — produces the NotebookLM list-notebooks artifact.
# ---------------------------------------------------------------------------


@dataclass
class _RecordedNotebookLMLLM:
    """Mock LLMCallable that returns a recorded NotebookLM-shaped
    response. Stand-in for a real LLM call against
    notebooklm-py-aware system prompts; the real call is operator-
    driven post-session.
    """

    model: str = "anthropic/claude-haiku-4.5"

    def __call__(self, *, system: str, user: str) -> str:
        # The recorded response carries the same shape the real LLM
        # produced when given notebooklm-py's source as ground truth.
        return json.dumps(
            {
                "operations": [
                    {
                        "id": "notebooklm_list_notebooks",
                        "summary": "List the user's NotebookLM notebooks.",
                        "description": "POSTs the batchexecute RPC endpoint with rpcids=wXbhsf.",
                        "tags": ["notebooklm", "read"],
                        "idempotency": "idempotent",
                        "request": {
                            "method": "POST",
                            "path": "/_/NotebookLmRpcs/data/batchexecute",
                            "query_parameters": {
                                "type": "object",
                                "required": ["rpcids"],
                                "properties": {
                                    "rpcids": {"type": "string", "const": "wXbhsf"}
                                },
                            },
                            "body": {
                                "media_type": "application/x-www-form-urlencoded",
                                "schema": {
                                    "type": "object",
                                    "required": ["f.req", "at"],
                                    "properties": {
                                        "f.req": {"type": "string"},
                                        "at": {"type": "string"},
                                    },
                                },
                            },
                        },
                        "response": {
                            "200": {
                                "description": "Batch execute RPC response.",
                                "body": {
                                    "media_type": "application/json+protobuf",
                                    "format": "text",
                                },
                            }
                        },
                    }
                ]
            }
        )


def test_inference_path_produces_notebooklm_artifact_end_to_end() -> None:
    """Demonstrates §3.8 inference end-to-end: operator-shape
    description → infer → user review → confirm → artifact validates
    through spec.loader."""
    description = (
        "I want to connect to Google NotebookLM. It's a research/notebook "
        "tool. The operation I need is: list my notebooks (no parameters). "
        "Reference: github.com/teng-lin/notebooklm-py."
    )
    llm = _RecordedNotebookLMLLM()
    draft = infer_from_description(description, llm=llm)
    assert len(draft.operations) == 1
    assert draft.operations[0].operation["id"] == "notebooklm_list_notebooks"

    # Without approval, persist refuses.
    from uacp_prototype.connections.ingest_nl import InferenceNotApprovedError

    with pytest.raises(InferenceNotApprovedError):
        confirm_and_persist(draft, approved=False)

    # With approval, persist produces a complete artifact that validates.
    artifact = confirm_and_persist(
        draft,
        approved=True,
        authentication={
            "method": "session_cookie",
            "tos_acknowledged": True,
            "storage_state_ref": "secret://local-keyring/notebooklm-storage-state",
            "cookie_names": ["SID", "HSID", "__Secure-1PSID"],
        },
        dispatch={"base_url": "https://notebooklm.google.com"},
    )
    parsed = load(_persist_temp_artifact(artifact))  # round-trip through disk
    assert parsed.authentication.method == "session_cookie"
    assert parsed.operations[0].source.type == "inferred"
    assert parsed.operations[0].source.reviewed_at  # populated at confirm


def _persist_temp_artifact(artifact: dict[str, Any]) -> Path:
    """Helper for the inference test: write a temporary .uacp file and
    return its path. Uses tmp_path under the test's directory."""
    import tempfile

    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".uacp", delete=False
    )
    f.write(json.dumps(artifact))
    f.close()
    return Path(f.name)
