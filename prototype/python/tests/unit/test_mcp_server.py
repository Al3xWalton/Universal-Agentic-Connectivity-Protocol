"""Unit tests for the MCP server adapter at uacp_prototype.mcp.server.

These tests exercise the loading + tool-mapping + dispatch-routing
surface without spawning an MCP transport. The MCP-transport-level
tests (server startup, tools/list, tool execution via the MCP SDK
client) live in tests/integration/test_mcp_composition.py and are
marked @pytest.mark.mcp_integration.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from uacp_prototype.dispatch.client import DispatchError, DispatchSuccess
from uacp_prototype.mcp.server import (
    LoadedOperation,
    UACPServer,
    load_directory,
    normalize_tool_name,
)
from uacp_prototype.spec.models import UACPArtifact


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"


# ---------------------------------------------------------------------------
# normalize_tool_name
# ---------------------------------------------------------------------------


def test_normalize_tool_name_safe_ascii() -> None:
    assert normalize_tool_name("google", "gmail_users_messages_send") == "google_gmail_users_messages_send"


def test_normalize_tool_name_strips_dots_and_slashes() -> None:
    # The §10 anti-rule from the AVA repo: dotted names are universally
    # rejected by major LLM providers' tool-name validation.
    assert "." not in normalize_tool_name("acme.corp", "send.message")
    assert "/" not in normalize_tool_name("acme/corp", "send/message")
    assert normalize_tool_name("acme.corp", "send.message") == "acme_corp_send_message"


def test_normalize_tool_name_caps_at_128() -> None:
    long = "x" * 200
    assert len(normalize_tool_name(long, long)) == 128


def test_normalize_tool_name_preserves_underscores_and_hyphens() -> None:
    name = normalize_tool_name("provider_one", "do-the-thing")
    assert name == "provider_one_do-the-thing"


# ---------------------------------------------------------------------------
# load_directory: 10 example artifacts → 10 LoadedOperations
# ---------------------------------------------------------------------------


def test_load_directory_against_examples() -> None:
    loaded = load_directory(EXAMPLES_DIR)
    assert len(loaded) == 10, f"expected 10 ops; got {len(loaded)}"
    expected_names = {
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
    actual = {op.tool_name for op in loaded}
    assert actual == expected_names


def test_load_directory_provider_resolution() -> None:
    loaded = load_directory(EXAMPLES_DIR)
    by_provider = {}
    for op in loaded:
        by_provider.setdefault(op.provider, []).append(op.tool_name)
    assert set(by_provider) == {"google", "slack", "aws", "github", "notebooklm"}
    assert len(by_provider["google"]) == 2
    assert len(by_provider["slack"]) == 2
    assert len(by_provider["aws"]) == 2
    assert len(by_provider["github"]) == 2
    assert len(by_provider["notebooklm"]) == 2


def test_load_directory_raises_on_nonexistent_dir(tmp_path: Path) -> None:
    nonexistent = tmp_path / "does-not-exist"
    with pytest.raises(ValueError, match="UACP directory does not exist"):
        load_directory(nonexistent)


def test_load_directory_skips_invalid_artifacts(tmp_path: Path) -> None:
    """A malformed .uacp file is skipped with a warning, not a hard
    failure. The MCP server is best-effort: one bad file shouldn't
    take down the rest of the operations directory."""
    (tmp_path / "broken.uacp").write_text('{"not valid": "uacp"}')
    # Copy one good artifact for comparison
    src = EXAMPLES_DIR / "google" / "gmail-send.uacp"
    (tmp_path / "google").mkdir()
    (tmp_path / "google" / "gmail-send.uacp").write_text(src.read_text())
    loaded = load_directory(tmp_path)
    # Good artifact still loaded; broken one skipped.
    assert any(op.tool_name == "google_gmail_users_messages_send" for op in loaded)


# ---------------------------------------------------------------------------
# Tool definitions: schema derivation
# ---------------------------------------------------------------------------


def test_tool_definitions_carry_input_schema() -> None:
    loaded = load_directory(EXAMPLES_DIR)
    for op in loaded:
        td = op.tool_definition()
        assert td.inputSchema["type"] == "object"
        assert "properties" in td.inputSchema
        # extra_headers is universal (the dispatcher merges them)
        assert "extra_headers" in td.inputSchema["properties"]


def test_tool_definitions_include_summary_and_method() -> None:
    loaded = load_directory(EXAMPLES_DIR)
    for op in loaded:
        td = op.tool_definition()
        # description carries the summary + method/path + source filename
        assert op.operation.summary in td.description
        assert op.operation.request.method in td.description
        assert op.artifact_path.name in td.description


def test_query_parameters_appear_when_declared() -> None:
    loaded = load_directory(EXAMPLES_DIR)
    list_for_user = next(op for op in loaded if op.tool_name == "github_repos_list_for_user")
    schema = list_for_user.tool_definition().inputSchema
    assert "query" in schema["properties"]


def test_path_parameters_appear_when_declared() -> None:
    loaded = load_directory(EXAMPLES_DIR)
    repos_get = next(op for op in loaded if op.tool_name == "github_repos_get")
    schema = repos_get.tool_definition().inputSchema
    assert "path_params" in schema["properties"]


# ---------------------------------------------------------------------------
# UACPServer routing (with mocked dispatch_factory)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_factory_server() -> tuple[UACPServer, MagicMock]:
    """A UACPServer wired to a mock dispatch_factory. The factory
    returns a MagicMock DispatchClient whose dispatch() the test can
    assert against."""
    captured_calls: list[dict[str, Any]] = []

    def factory(artifact: UACPArtifact):
        client = MagicMock()
        client.dispatch.return_value = DispatchSuccess(
            status=200,
            headers={"Content-Type": "application/json"},
            body={"echo": "ok"},
        )

        def remember(*args, **kwargs):
            captured_calls.append({"artifact": artifact, "args": args, "kwargs": kwargs})
            return DispatchSuccess(
                status=200,
                headers={"Content-Type": "application/json"},
                body={"echo": "ok"},
            )

        client.dispatch.side_effect = remember
        return client

    server = UACPServer(uacp_dir=EXAMPLES_DIR, dispatch_factory=factory)
    factory_mock = MagicMock(wraps=factory)
    factory_mock.captured = captured_calls
    return server, factory_mock


def test_server_advertises_all_tools(mock_factory_server) -> None:
    server, _ = mock_factory_server
    assert len(server.tool_names) == 10
    assert "google_gmail_users_messages_send" in server.tool_names


def test_invoke_unknown_tool_returns_error_payload(mock_factory_server) -> None:
    server, _ = mock_factory_server
    result = asyncio.run(server._invoke("nonexistent_tool", {}))
    assert len(result) == 1
    payload = json.loads(result[0].text)
    assert payload["error"] == "tool_not_found"
    assert "google_gmail_users_messages_send" in payload["known_tools"]


def test_invoke_routes_to_dispatch_factory(mock_factory_server) -> None:
    server, _ = mock_factory_server
    # The server's stored factory is the original; spy by replacing.
    captured: list[Any] = []

    def factory(artifact):
        client = MagicMock()
        client.dispatch.return_value = DispatchSuccess(
            status=200,
            headers={},
            body={"name": "octocat"},
        )
        captured.append(artifact)
        return client

    server.dispatch_factory = factory
    result = asyncio.run(
        server._invoke(
            "github_repos_get",
            {"path_params": {"owner": "octocat", "repo": "hello-world"}},
        )
    )
    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert payload["status"] == 200
    assert payload["body"] == {"name": "octocat"}
    assert len(captured) == 1


def test_invoke_surfaces_dispatch_error_canonically(mock_factory_server) -> None:
    server, _ = mock_factory_server

    def factory(artifact):
        client = MagicMock()
        client.dispatch.return_value = DispatchError(
            status=403,
            code="forbidden",
            message="missing scope: repo",
            details={"required_scope": "repo"},
        )
        return client

    server.dispatch_factory = factory
    result = asyncio.run(server._invoke("github_repos_get", {}))
    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert payload["code"] == "forbidden"
    assert payload["status"] == 403
    assert payload["message"] == "missing scope: repo"
    assert payload["details"]["required_scope"] == "repo"


def test_invoke_surfaces_credential_resolution_failure(mock_factory_server) -> None:
    server, _ = mock_factory_server

    def factory(artifact):
        raise RuntimeError("API key not found in env UACP_GITHUB_API_KEY.")

    server.dispatch_factory = factory
    result = asyncio.run(server._invoke("github_repos_get", {}))
    payload = json.loads(result[0].text)
    assert payload["error"] == "credential_resolution_failed"
    assert "UACP_GITHUB_API_KEY" in payload["message"]


def test_invoke_passes_arguments_through(mock_factory_server) -> None:
    server, _ = mock_factory_server
    captured: list[dict[str, Any]] = []

    def factory(artifact):
        client = MagicMock()

        def remember(operation_id, **kwargs):
            captured.append({"operation_id": operation_id, **kwargs})
            return DispatchSuccess(status=200, headers={}, body={"ok": True})

        client.dispatch.side_effect = remember
        return client

    server.dispatch_factory = factory
    asyncio.run(
        server._invoke(
            "github_repos_list_for_user",
            {
                "path_params": {"username": "octocat"},
                "query": {"per_page": 30},
                "extra_headers": {"X-Test": "1"},
            },
        )
    )
    assert len(captured) == 1
    call = captured[0]
    assert call["operation_id"] == "repos_list_for_user"
    assert call["path_params"] == {"username": "octocat"}
    assert call["query"] == {"per_page": 30}
    assert call["extra_headers"] == {"X-Test": "1"}


# ---------------------------------------------------------------------------
# Server property + tool definitions visible through MCP types
# ---------------------------------------------------------------------------


def test_server_exposes_mcp_server_object(mock_factory_server) -> None:
    server, _ = mock_factory_server
    from mcp.server import Server

    assert isinstance(server.server, Server)


def test_tool_definitions_are_valid_mcp_types(mock_factory_server) -> None:
    import mcp.types as mcp_types

    server, _ = mock_factory_server
    for op in server.operations:
        td = op.tool_definition()
        assert isinstance(td, mcp_types.Tool)
        assert td.name
        assert td.description
        assert isinstance(td.inputSchema, dict)
