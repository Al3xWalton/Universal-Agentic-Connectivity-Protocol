"""MCP server surfacing UACP operations as MCP tools.

Reads a directory of `.uacp` artifacts, registers one MCP tool per
operation, and dispatches tool calls through the existing UACP runtime.
Tool naming: ``<provider>_<operation_id>``, where ``<provider>`` comes
from the artifact's parent directory (or its `name` field when the
artifact lives directly in the configured root). Tool input schema:
derived from the operation's request shape per §3.2.

The server is constructed with a directory path plus a
``dispatch_factory`` callable that produces a ``DispatchClient`` for a
given artifact. The default factory (``build_dispatch_client_default``)
handles credential resolution from the local-keyring + env-var fallback
shapes the prototype already supports; tests inject their own factory
that returns a mock-dispatching client.

Run as ``python -m uacp_prototype.mcp --uacp-dir <path>``. The transport
is stdio per the MCP standard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import mcp.types as mcp_types
from mcp.server import Server

from ..auth.api_key import APIKeyHeaderConfig, APIKeyHeaderMethod, APIKeyQueryConfig, APIKeyQueryMethod
from ..auth.base import AuthMethod
from ..auth.oauth2_authcode import OAuth2AuthCodeConfig, OAuth2AuthCodeMethod
from ..auth.oauth2_workspace import OAuth2WorkspaceConfig, OAuth2WorkspaceMethod
from ..auth.aws_sigv4 import AWSSigV4Config, AWSSigV4Method
from ..auth.session_cookie import (
    CSRFConfig,
    SessionCookieConfig,
    SessionCookieMethod,
)
from ..dispatch.client import (
    DispatchClient,
    DispatchError,
    DispatchSuccess,
)
from ..dispatch.transport import select_transport_for_artifact
from ..spec.loader import load
from ..spec.models import Operation, UACPArtifact

log = logging.getLogger("uacp.mcp")


# Tool names per OpenAI / Anthropic / Google validation are
# ^[a-zA-Z0-9_-]{1,128}$ — dots and slashes are rejected.
_TOOL_NAME_INVALID = re.compile(r"[^a-zA-Z0-9_-]")


def normalize_tool_name(provider: str, operation_id: str) -> str:
    """Combine ``<provider>_<operation_id>`` into an MCP-tool-name-safe
    identifier. Dots, slashes, and other invalid characters become
    underscores. The 128-char cap is enforced by truncation; collisions
    on truncated names are an artifact-naming concern not an MCP one.
    """
    raw = f"{provider}_{operation_id}"
    safe = _TOOL_NAME_INVALID.sub("_", raw)
    return safe[:128]


# ---------------------------------------------------------------------------
# Loading + tool schema construction
# ---------------------------------------------------------------------------


@dataclass
class LoadedOperation:
    """One operation exposed as an MCP tool. Carries the artifact +
    operation + computed tool name so the dispatch handler doesn't have
    to re-resolve the mapping per call."""

    artifact: UACPArtifact
    artifact_path: Path
    provider: str
    operation: Operation
    tool_name: str

    def tool_definition(self) -> mcp_types.Tool:
        return mcp_types.Tool(
            name=self.tool_name,
            description=self._description(),
            inputSchema=self._input_schema(),
        )

    def _description(self) -> str:
        parts = [self.operation.summary]
        if self.operation.description:
            parts.append(self.operation.description)
        parts.append(
            f"Provider: {self.provider}. "
            f"Method: {self.operation.request.method} {self.operation.request.path}. "
            f"UACP source: {self.artifact_path.name}."
        )
        return "\n\n".join(parts)

    def _input_schema(self) -> dict[str, Any]:
        """Derive the tool's input JSON schema from the operation's
        request shape per §3.2. The MCP tool sees a single JSON object
        with optional sub-objects for path_params / query / body /
        extra_headers — matching the keyword arguments the
        DispatchClient.dispatch method already accepts.
        """
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        req = self.operation.request

        if req.path_parameters:
            schema["properties"]["path_params"] = req.path_parameters
        if req.query_parameters:
            schema["properties"]["query"] = req.query_parameters
        if isinstance(req.body, dict) and "schema" in req.body:
            schema["properties"]["body"] = req.body["schema"]
        elif req.body == "none" or req.body is None:
            pass
        else:
            schema["properties"]["body"] = {"type": "object"}

        schema["properties"]["extra_headers"] = {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Optional caller-supplied request headers (merged with dispatch defaults).",
        }
        return schema


def _provider_for(artifact: UACPArtifact, artifact_path: Path, root: Path) -> str:
    """Provider name resolution. Prefer the artifact's parent-directory
    name when the artifact lives in a subdirectory under the configured
    root; fall back to the artifact's stem when it lives directly at
    the root.
    """
    rel = artifact_path.relative_to(root)
    if len(rel.parts) > 1:
        return rel.parts[0]
    extra = getattr(artifact, "model_extra", None) or {}
    name = extra.get("name")
    if isinstance(name, str) and name:
        return name.replace("/", "_").replace(".", "_")
    return artifact_path.stem


def load_directory(uacp_dir: Path) -> list[LoadedOperation]:
    """Walk ``uacp_dir`` for ``*.uacp`` files; load + validate each;
    return the flattened list of operations with their MCP tool names.
    """
    if not uacp_dir.is_dir():
        raise ValueError(f"UACP directory does not exist: {uacp_dir}")
    loaded: list[LoadedOperation] = []
    seen_tool_names: set[str] = set()
    for path in sorted(uacp_dir.rglob("*.uacp")):
        try:
            artifact = load(path)
        except Exception as e:
            log.warning("skipping %s — load failed: %s", path, e)
            continue
        provider = _provider_for(artifact, path, uacp_dir)
        for op in artifact.operations:
            tool_name = normalize_tool_name(provider, op.id)
            if tool_name in seen_tool_names:
                log.warning(
                    "duplicate tool name %r — skipping operation %s in %s",
                    tool_name,
                    op.id,
                    path,
                )
                continue
            seen_tool_names.add(tool_name)
            loaded.append(
                LoadedOperation(
                    artifact=artifact,
                    artifact_path=path,
                    provider=provider,
                    operation=op,
                    tool_name=tool_name,
                )
            )
    return loaded


# ---------------------------------------------------------------------------
# Default dispatch-client factory (production use)
# ---------------------------------------------------------------------------


def build_dispatch_client_default(artifact: UACPArtifact) -> DispatchClient:
    """Production-side dispatch-client construction.

    Builds the appropriate ``AuthMethod`` for the artifact's declared
    authentication and a credential resolver that pulls from the local
    keyring (for OAuth tokens) or environment variables (for API keys
    and AWS-style long-lived credentials). The resolver scheme:

      - ``oauth2_authorization_code`` / ``oauth2_workspace``: tokens from
        the local-keyring, keyed by artifact name (operator MUST
        complete the OAuth flow first via the prototype's existing CLI
        or test-harness paths).
      - ``api_key_header`` / ``api_key_query``: read from
        ``UACP_<PROVIDER>_API_KEY`` environment variable.
      - ``aws_sigv4``: read from standard AWS environment variables
        (``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` /
        ``AWS_SESSION_TOKEN``).
      - ``session_cookie``: read storage_state from
        ``UACP_<PROVIDER>_STORAGE_STATE`` (a path).

    Tests typically inject a different factory via ``UACPServer``'s
    constructor to mock at the dispatch boundary.
    """
    auth = artifact.authentication
    method_name = auth.method
    extra = auth.model_extra or {}

    auth_method, resolver = _build_auth_for_method(method_name, extra, artifact)
    transport = select_transport_for_artifact(artifact)
    return DispatchClient(
        artifact,
        auth_method=auth_method,
        credential_resolver=resolver,
        transport=transport,
    )


def _provider_env_token(artifact: UACPArtifact) -> str:
    extra = artifact.model_extra or {}
    name = extra.get("name") or "default"
    return name.upper().replace("-", "_").replace(".", "_")


def _build_auth_for_method(
    method_name: str, extra: dict[str, Any], artifact: UACPArtifact
) -> tuple[AuthMethod, Callable[[], dict[str, Any]]]:
    """Return (auth_method, credential_resolver) for the given method.

    The resolver closures over the artifact + env shape so that calling
    the resolver later (at dispatch time) reads the most-current value
    from the configured store.
    """
    provider_token = _provider_env_token(artifact)

    if method_name == "oauth2_authorization_code":
        config = OAuth2AuthCodeConfig(
            authorization_endpoint=extra["authorization_endpoint"],
            token_endpoint=extra["token_endpoint"],
            client_id=extra["client_id"],
            scopes=tuple(extra.get("scopes") or ()),
            redirect_uri=extra["redirect_uri"],
        )
        method = OAuth2AuthCodeMethod(config=config)

        def resolver() -> dict[str, Any]:
            token = os.environ.get(f"UACP_{provider_token}_ACCESS_TOKEN")
            if not token:
                raise RuntimeError(
                    f"OAuth access token not found in env "
                    f"UACP_{provider_token}_ACCESS_TOKEN. Run the OAuth "
                    f"flow first via tests/providers/test_*.py or set the "
                    f"env directly for ad-hoc dispatch."
                )
            return {"access_token": token}

        return method, resolver

    if method_name == "x-oauth2-workspace":
        config = OAuth2WorkspaceConfig(
            authorization_endpoint=extra["authorization_endpoint"],
            token_endpoint=extra["token_endpoint"],
            client_id=extra["client_id"],
            scopes=tuple(extra.get("scopes") or ()),
            user_scopes=tuple(extra.get("user_scopes") or ()),
            redirect_uri=extra["redirect_uri"],
        )
        method = OAuth2WorkspaceMethod(config=config)

        def resolver() -> dict[str, Any]:
            token = os.environ.get(f"UACP_{provider_token}_BOT_TOKEN")
            if not token:
                raise RuntimeError(
                    f"workspace bot token not found in env "
                    f"UACP_{provider_token}_BOT_TOKEN."
                )
            return {"bot_access_token": token}

        return method, resolver

    if method_name == "api_key_header":
        config = APIKeyHeaderConfig(
            header_name=extra["header_name"],
            header_prefix=extra.get("header_prefix", ""),
        )
        method = APIKeyHeaderMethod(config=config)

        def resolver() -> dict[str, Any]:
            key = os.environ.get(f"UACP_{provider_token}_API_KEY")
            if not key:
                raise RuntimeError(
                    f"API key not found in env UACP_{provider_token}_API_KEY."
                )
            return {"api_key": key}

        return method, resolver

    if method_name == "api_key_query":
        config = APIKeyQueryConfig(parameter_name=extra["parameter_name"])
        method = APIKeyQueryMethod(config=config)

        def resolver() -> dict[str, Any]:
            key = os.environ.get(f"UACP_{provider_token}_API_KEY")
            if not key:
                raise RuntimeError(
                    f"API key not found in env UACP_{provider_token}_API_KEY."
                )
            return {"api_key": key}

        return method, resolver

    if method_name == "aws_sigv4":
        config = AWSSigV4Config(
            region=extra["region"],
            service=extra["service"],
        )
        method = AWSSigV4Method(config=config)

        def resolver() -> dict[str, Any]:
            access = os.environ.get("AWS_ACCESS_KEY_ID")
            secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
            session_token = os.environ.get("AWS_SESSION_TOKEN")
            if not access or not secret:
                raise RuntimeError(
                    "AWS credentials not found in env "
                    "(AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY required)."
                )
            creds: dict[str, Any] = {"access_key": access, "secret_key": secret}
            if session_token:
                creds["session_token"] = session_token
            return creds

        return method, resolver

    if method_name == "session_cookie":
        csrf_block = extra.get("csrf_token") if isinstance(extra, dict) else None
        csrf = None
        if isinstance(csrf_block, dict):
            csrf = CSRFConfig(
                header_name=csrf_block["header_name"],
                cookie_name=csrf_block.get("cookie_name"),
                refresh_url=csrf_block.get("refresh_url"),
                extraction_path=csrf_block.get("extraction_path"),
                extraction_format=csrf_block.get("extraction_format", "json"),
            )
        config = SessionCookieConfig(
            cookie_names=tuple(extra.get("cookie_names") or ()),
            csrf=csrf,
        )
        method = SessionCookieMethod(config=config)

        def resolver() -> dict[str, Any]:
            path = os.environ.get(f"UACP_{provider_token}_STORAGE_STATE")
            if not path:
                raise RuntimeError(
                    f"storage state path not found in env "
                    f"UACP_{provider_token}_STORAGE_STATE."
                )
            return {"storage_state": Path(path).read_text()}

        return method, resolver

    raise NotImplementedError(
        f"MCP server credential resolution not yet wired for method {method_name!r}. "
        f"Inject a custom dispatch_factory via UACPServer for now."
    )


# ---------------------------------------------------------------------------
# UACPServer
# ---------------------------------------------------------------------------


@dataclass
class UACPServer:
    """MCP server adapter. Construct with a directory of `.uacp` files
    plus an optional ``dispatch_factory`` (defaults to
    ``build_dispatch_client_default`` for production use).
    """

    uacp_dir: Path
    dispatch_factory: Callable[[UACPArtifact], DispatchClient] = field(
        default=build_dispatch_client_default
    )
    server_name: str = "uacp"

    def __post_init__(self) -> None:
        self.operations = load_directory(self.uacp_dir)
        self._by_tool_name = {op.tool_name: op for op in self.operations}
        self._server: Server = self._build_server()

    @property
    def tool_names(self) -> list[str]:
        return [op.tool_name for op in self.operations]

    def _build_server(self) -> Server:
        server: Server = Server(self.server_name)

        @server.list_tools()
        async def _list_tools() -> list[mcp_types.Tool]:
            return [op.tool_definition() for op in self.operations]

        @server.call_tool()
        async def _call_tool(
            name: str, arguments: dict[str, Any]
        ) -> list[mcp_types.TextContent]:
            return await self._invoke(name, arguments)

        return server

    @property
    def server(self) -> Server:
        return self._server

    async def _invoke(
        self, name: str, arguments: dict[str, Any]
    ) -> list[mcp_types.TextContent]:
        loaded = self._by_tool_name.get(name)
        if loaded is None:
            return [
                mcp_types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": "tool_not_found",
                            "message": f"unknown tool {name!r}",
                            "known_tools": sorted(self._by_tool_name),
                        }
                    ),
                )
            ]

        try:
            client = self.dispatch_factory(loaded.artifact)
        except Exception as e:
            return [
                mcp_types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": "credential_resolution_failed",
                            "message": str(e),
                        }
                    ),
                )
            ]

        try:
            result = client.dispatch(
                loaded.operation.id,
                path_params=arguments.get("path_params") or {},
                query=arguments.get("query") or {},
                body=arguments.get("body"),
                extra_headers=arguments.get("extra_headers") or {},
            )
        finally:
            client.close()

        return [_serialize_result(result)]

    async def run_stdio(self) -> None:
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(),
            )


def _serialize_result(result: Any) -> mcp_types.TextContent:
    """Convert a DispatchResult into MCP TextContent. Successes carry
    the parsed body; failures carry the canonical error shape per §4.6.
    """
    if isinstance(result, DispatchSuccess):
        payload = {
            "ok": True,
            "status": result.status,
            "headers": result.headers,
            "body": _coerce_for_json(result.body),
        }
    elif isinstance(result, DispatchError):
        payload = {
            "ok": False,
            "status": result.status,
            "code": result.code,
            "message": result.message,
            "details": result.details,
        }
    else:
        payload = {"ok": False, "error": "unexpected_result_type", "type": type(result).__name__}
    return mcp_types.TextContent(type="text", text=json.dumps(payload, default=str))


def _coerce_for_json(value: Any) -> Any:
    """Best-effort JSON-friendly coercion of dispatch bodies. Bytes are
    base64'd; everything else is JSON-passable as-is or via str().
    """
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        import base64

        return {"__binary_b64__": base64.b64encode(bytes(value)).decode("ascii")}
    return str(value)


# ---------------------------------------------------------------------------
# CLI entry point: python -m uacp_prototype.mcp
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m uacp_prototype.mcp",
        description=(
            "MCP server exposing UACP operations as MCP tools. "
            "Reads a directory of .uacp files; advertises one tool per "
            "operation; dispatches through the prototype's UACP runtime."
        ),
    )
    parser.add_argument(
        "--uacp-dir",
        required=True,
        help="directory containing .uacp files (recursively walked)",
    )
    parser.add_argument(
        "--server-name",
        default="uacp",
        help="MCP server name advertised on initialize (default: uacp)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    server = UACPServer(
        uacp_dir=Path(args.uacp_dir).expanduser().resolve(),
        server_name=args.server_name,
    )
    log.info(
        "uacp-mcp ready: %d operations across %d artifacts",
        len(server.operations),
        len({op.artifact_path for op in server.operations}),
    )
    asyncio.run(server.run_stdio())
    return 0


__all__ = [
    "LoadedOperation",
    "UACPServer",
    "build_dispatch_client_default",
    "load_directory",
    "main",
    "normalize_tool_name",
]
