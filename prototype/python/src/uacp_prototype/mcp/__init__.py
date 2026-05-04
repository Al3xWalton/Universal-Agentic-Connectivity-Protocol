"""UACP ↔ MCP composition adapter.

Exposes a Model Context Protocol (MCP) server that surfaces every
operation in a directory of `.uacp` artifacts as an MCP tool. Any
MCP-aware client (Claude Code, Claude Desktop, Cursor, others) can
connect over stdio and call UACP-defined operations as tools — Stage 9
makes Principle 4 (composability with MCP) concrete.

The composition is mechanical: each `.uacp` file in the configured
directory contributes one MCP tool per operation, with the tool name
derived from `<provider>_<operation_id>` and the input schema derived
from the operation's request shape per §3.2. Tool execution dispatches
through the existing UACP runtime (`auth/`, `dispatch/`, `lifecycle/`,
`security/`) so the same security and dispatch invariants the spec
enforces for direct UACP consumers apply transparently to MCP-side
callers.

Production callers run `python -m uacp_prototype.mcp --uacp-dir <path>`;
tests exercise the surface via `tests/integration/test_mcp_composition.py`
(marked `@pytest.mark.mcp_integration`, skipped by default).
"""

from .server import UACPServer, build_dispatch_client_default, normalize_tool_name

__all__ = [
    "UACPServer",
    "build_dispatch_client_default",
    "normalize_tool_name",
]
