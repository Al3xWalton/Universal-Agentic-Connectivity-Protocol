"""MCP composition validation tests.

Marked ``@pytest.mark.mcp_integration``; skipped by default. These
tests exercise the full MCP protocol surface (initialize, tools/list,
tools/call, error propagation) by pairing the prototype's
``UACPServer`` with the MCP SDK's ``ClientSession`` over in-process
memory streams. No subprocess spawning required — the SDK's
``create_connected_server_and_client_session`` runs the server and
client in the same event loop with paired send/receive streams.

The dispatch boundary is mocked: a custom ``dispatch_factory``
returns a MagicMock DispatchClient so tests don't need real OAuth
tokens, real network access, or operator-supplied credentials. The
end-to-end MCP-protocol behavior is what's being verified — that
Principle 4 (composability with MCP) holds when an MCP-aware client
connects to a UACP-backed MCP server.

Run with: ``uv run pytest tests/integration/test_mcp_composition.py
-m mcp_integration``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from uacp_prototype.dispatch.client import DispatchError, DispatchSuccess
from uacp_prototype.mcp.server import UACPServer


pytestmark = pytest.mark.mcp_integration


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"


def _build_server(dispatch_factory) -> UACPServer:
    return UACPServer(uacp_dir=EXAMPLES_DIR, dispatch_factory=dispatch_factory)


def _success_factory(body: Any, status: int = 200, headers: dict[str, str] | None = None):
    """Returns a dispatch_factory that produces clients always
    returning a DispatchSuccess with the given body."""

    def factory(_artifact):
        client = MagicMock()
        client.dispatch.return_value = DispatchSuccess(
            status=status,
            headers=headers or {"Content-Type": "application/json"},
            body=body,
        )
        return client

    return factory


def _error_factory(*, status: int, code: str, message: str, details: dict[str, Any] | None = None):
    def factory(_artifact):
        client = MagicMock()
        client.dispatch.return_value = DispatchError(
            status=status,
            code=code,
            message=message,
            details=details or {},
        )
        return client

    return factory


# ---------------------------------------------------------------------------
# Server startup & tool advertisement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_advertises_expected_tool_count() -> None:
    """One MCP tool per UACP operation — 10 across the prototype's
    examples/ tree."""
    server = _build_server(_success_factory({"ok": True}))
    async with create_connected_server_and_client_session(
        server.server, raise_exceptions=True
    ) as client:
        result = await client.list_tools()
    assert len(result.tools) == 10


@pytest.mark.asyncio
async def test_server_advertises_expected_tool_names() -> None:
    server = _build_server(_success_factory({"ok": True}))
    async with create_connected_server_and_client_session(
        server.server, raise_exceptions=True
    ) as client:
        result = await client.list_tools()
    names = {t.name for t in result.tools}
    expected = {
        "google_gmail_users_messages_send",
        "google_calendar_events_list",
        "slack_chat_postmessage",
        "slack_conversations_list",
        "aws_s3_getobject",
        "aws_s3_listobjectsv2",
        "github_repos_get",
        "github_repos_list_for_user",
        "notebooklm_notebooklm_list_notebooks",
        "notebooklm_notebooklm_send_chat_message",
    }
    assert names == expected


@pytest.mark.asyncio
async def test_tool_schemas_match_uacp_request_shapes() -> None:
    """Every advertised tool carries an input schema derived from the
    operation's request shape per §3.2 — verified by checking that
    operations with declared path / query / body parameters surface
    those as sub-objects in the tool's inputSchema.
    """
    server = _build_server(_success_factory({"ok": True}))
    async with create_connected_server_and_client_session(
        server.server, raise_exceptions=True
    ) as client:
        result = await client.list_tools()

    by_name = {t.name: t for t in result.tools}
    repos_get = by_name["github_repos_get"]
    assert repos_get.inputSchema["type"] == "object"
    assert "path_params" in repos_get.inputSchema["properties"]

    list_user = by_name["github_repos_list_for_user"]
    assert "query" in list_user.inputSchema["properties"]


# ---------------------------------------------------------------------------
# Tool execution (mocked at the dispatch boundary)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_returns_dispatch_success() -> None:
    """Calling a tool through MCP returns the same shape the underlying
    UACP DispatchSuccess produced — the {ok: True, status, headers,
    body} JSON envelope from _serialize_result."""
    server = _build_server(_success_factory({"name": "octocat", "stargazers_count": 42}))
    async with create_connected_server_and_client_session(
        server.server, raise_exceptions=True
    ) as client:
        call_result = await client.call_tool(
            "github_repos_get",
            arguments={"path_params": {"owner": "octocat", "repo": "hello-world"}},
        )

    assert len(call_result.content) == 1
    text = call_result.content[0].text
    payload = json.loads(text)
    assert payload["ok"] is True
    assert payload["status"] == 200
    assert payload["body"]["name"] == "octocat"


@pytest.mark.asyncio
async def test_tool_call_arguments_pass_through_to_dispatch() -> None:
    """The MCP arguments object lands on DispatchClient.dispatch with
    path_params / query / body / extra_headers split out — verified
    by capturing the dispatch call."""
    captured: list[dict[str, Any]] = []

    def factory(_artifact):
        client = MagicMock()

        def remember(operation_id, **kwargs):
            captured.append({"operation_id": operation_id, **kwargs})
            return DispatchSuccess(status=200, headers={}, body={"ok": True})

        client.dispatch.side_effect = remember
        return client

    server = _build_server(factory)
    async with create_connected_server_and_client_session(
        server.server, raise_exceptions=True
    ) as client:
        await client.call_tool(
            "github_repos_list_for_user",
            arguments={
                "path_params": {"username": "octocat"},
                "query": {"per_page": 30, "type": "all"},
                "extra_headers": {"X-Test": "1"},
            },
        )

    assert len(captured) == 1
    call = captured[0]
    assert call["operation_id"] == "repos_list_for_user"
    assert call["path_params"] == {"username": "octocat"}
    assert call["query"] == {"per_page": 30, "type": "all"}
    assert call["extra_headers"] == {"X-Test": "1"}


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_error_propagates_canonically_through_mcp() -> None:
    """An UACP-side §4.6 canonical error — for instance, a scope-
    enforcement rejection surfaced as forbidden — propagates through
    the MCP tool result with the canonical fields preserved (status,
    code, message, details)."""
    server = _build_server(
        _error_factory(
            status=403,
            code="forbidden",
            message="missing scope: repo",
            details={"required_scope": "repo", "have_scopes": ["public_repo"]},
        )
    )
    async with create_connected_server_and_client_session(
        server.server, raise_exceptions=True
    ) as client:
        call_result = await client.call_tool(
            "github_repos_get",
            arguments={"path_params": {"owner": "private", "repo": "secret"}},
        )

    payload = json.loads(call_result.content[0].text)
    assert payload["ok"] is False
    assert payload["status"] == 403
    assert payload["code"] == "forbidden"
    assert payload["message"] == "missing scope: repo"
    assert payload["details"]["required_scope"] == "repo"


@pytest.mark.asyncio
async def test_credential_resolution_failure_surfaces_through_mcp() -> None:
    """When the dispatch factory itself raises (typical pattern: missing
    env-var for the credential), the MCP tool result carries a
    credential_resolution_failed envelope rather than crashing the
    server."""

    def factory(_artifact):
        raise RuntimeError("API key not found in env UACP_GITHUB_API_KEY.")

    server = _build_server(factory)
    async with create_connected_server_and_client_session(
        server.server, raise_exceptions=True
    ) as client:
        call_result = await client.call_tool("github_repos_get", arguments={})

    payload = json.loads(call_result.content[0].text)
    assert payload["error"] == "credential_resolution_failed"
    assert "UACP_GITHUB_API_KEY" in payload["message"]


# ---------------------------------------------------------------------------
# Multiple-provider routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_providers_dispatch_independently() -> None:
    """A single MCP server hosts tools from multiple providers; each
    tool call routes to the correct artifact's auth + dispatch path.
    Verified by asserting different providers produce different
    captured artifact identities."""
    captured_artifacts: list[Any] = []

    def factory(artifact):
        captured_artifacts.append(artifact)
        client = MagicMock()
        client.dispatch.return_value = DispatchSuccess(
            status=200, headers={}, body={"provider_artifact_name": getattr(artifact, "model_extra", {}).get("name")}
        )
        return client

    server = _build_server(factory)
    async with create_connected_server_and_client_session(
        server.server, raise_exceptions=True
    ) as client:
        github_result = await client.call_tool(
            "github_repos_get",
            arguments={"path_params": {"owner": "octocat", "repo": "hello-world"}},
        )
        slack_result = await client.call_tool(
            "slack_conversations_list",
            arguments={},
        )

    assert len(captured_artifacts) == 2
    # Each artifact came from a different .uacp file
    assert captured_artifacts[0] is not captured_artifacts[1]

    github_payload = json.loads(github_result.content[0].text)
    slack_payload = json.loads(slack_result.content[0].text)
    assert github_payload["ok"] is True
    assert slack_payload["ok"] is True
