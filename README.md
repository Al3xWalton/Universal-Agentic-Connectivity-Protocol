# UACP — Universal Agentic Connectivity Protocol

> A universal protocol for AI agents to authenticate to and dispatch operations against any HTTPS service.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)
[![Spec: v1.1.0](https://img.shields.io/badge/Spec-v1.1.0-success)](./SPEC.md)
[![Tests: 596 passing](https://img.shields.io/badge/Tests-596_passing-success)](./prototype/python/)
[![Status: Stable](https://img.shields.io/badge/Status-Stable-success)](./SPEC.md)

UACP is a peer to the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). Where MCP standardizes how an agent calls a tool, UACP standardizes how the tool, once called, reaches the external service it connects to. The two protocols compose: a UACP-defined connection can be exposed through an MCP server as a tool, and an MCP-aware agent does not need UACP-specific code to consume one.

## Why UACP

A user who wants their agent to act against a long-tail service today has two choices: wait for a curated catalog (Composio, Zapier, similar) to admit it, or hand-roll a bespoke integration. The first option puts a single party between every agent and every service it might ever need to reach; the second makes the long tail unreachable in practice.

UACP rejects this trade-off. The bet is universal-by-design beats curated catalogs at scale: any service describable through standard authentication and standard HTTPS dispatch is reachable, and the description itself is a small JSON document that an AI agent can produce from a natural-language brief, validate against a public JSON Schema, and persist alongside the credentials it references.

The result is a wire format and runtime contract that is small enough to specify completely in eight short documents and broad enough to cover OAuth-protected APIs, AWS-signed services, API-key services, browser-session-replay grey-zone services, and the long tail in between — all under one protocol, all reachable through the same dispatch path.

## Specification at a glance

UACP `v1.1.0` covers:

- **10 authentication methods** — OAuth 2.0 authorization-code (with PKCE), OAuth 2.0 client-credentials, OAuth 2.0 device-code, OAuth 1.0a, API key in header, API key in query string, AWS SigV4, generic HMAC, session_cookie (with mandatory ToS gate), and a `custom_auth` extension point.
- **4 schema sources** — OpenAPI ingestion, curl-paste, LLM inference, and browser-captured sessions. All converge on the same canonical operation form.
- **4 pagination patterns** — cursor, offset, RFC 8288 link-header, and none — with explicit conformance rules for each.
- **Lifecycle states** — `pending`, `active`, `revoked`, with atomic refresh-token rotation and revocation propagation.
- **Security model** — `secret://` credential references (no plaintext credentials in `.uacp` files), four registered secret stores, AES-256-GCM envelope encryption at rest, scope enforcement, and audit logging.
- **MCP composition** — UACP-defined operations expose cleanly as MCP tools through a thin server adapter.
- **Pluggable transports** — implementations MAY substitute HTTP backends (e.g., anti-bot-evading clients for grey-zone providers) without breaking the dispatch contract.

The full specification lives in [`SPEC.md`](./SPEC.md), which indexes the eight stage documents under [`docs/`](./docs/).

## What a `.uacp` file looks like

A complete `.uacp` artifact for sending Gmail, abridged for the README:

```json
{
  "$schema": "https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.1.0/schemas/uacp.json",
  "name": "google-gmail",
  "version": "0.1.0",
  "authentication": {
    "method": "oauth2_authorization_code",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "client_secret_ref": "secret://local-keyring/google#client_secret",
    "scopes": ["https://www.googleapis.com/auth/gmail.send"],
    "code_challenge_method": "S256"
  },
  "dispatch": {
    "base_url": "https://gmail.googleapis.com",
    "default_headers": {"Accept": "application/json"},
    "default_timeout_ms": 30000
  },
  "operations": [
    {
      "id": "gmail_users_messages_send",
      "summary": "Send an email message.",
      "idempotency": "not_idempotent",
      "request": {
        "method": "POST",
        "path": "/gmail/v1/users/{userId}/messages/send",
        "path_parameters": {
          "type": "object",
          "required": ["userId"],
          "properties": {"userId": {"type": "string", "default": "me"}}
        },
        "body": {"media_type": "application/json", "schema": {"$ref": "#/definitions/Message"}}
      },
      "response": {
        "200": {"body": {"media_type": "application/json", "schema": {"$ref": "#/definitions/Message"}}}
      },
      "source": {"type": "openapi", "url": "https://www.googleapis.com/discovery/v1/apis/gmail/v1/rest"}
    }
  ]
}
```

Credentials are referenced through `secret://` URIs and resolved at dispatch time against a configured secret store; `.uacp` files MUST NOT carry plaintext secrets. The full file is at [`prototype/python/examples/google/gmail-send.uacp`](./prototype/python/examples/google/gmail-send.uacp).

## Reference implementation

A complete Python reference implementation lives at [`prototype/python/`](./prototype/python/). It covers every `MUST` of every stage and is the source of UACP's conformance evidence:

- **596 unit tests passing**, 36 integration tests deselected by default (25 provider integration + 8 MCP integration + 3 capture / stealth-transport).
- **Five validated providers**: Google (Gmail + Calendar), Slack, AWS S3, GitHub, NotebookLM. Each provider session surfaced one or zero non-breaking spec amendments and shipped both `.uacp` example files and integration tests.
- **MCP server adapter** at `prototype/python/src/uacp_prototype/mcp/` — exposes UACP operations as MCP tools through the official MCP Python SDK.
- **Browser capture pipeline** at `prototype/python/src/uacp_prototype/capture/` — records a browser-demonstrated session, encrypts the artifact at rest, and synthesizes draft `.uacp` operations for explicit user review per §3.12 + §3.8.

Setup instructions, examples, and the MCP composition guide are in [`prototype/python/README.md`](./prototype/python/README.md).

## MCP composability

UACP composes with MCP rather than replacing it. An agent that already speaks MCP — Claude Code, Claude Desktop, Cursor, any MCP-aware host — can consume UACP-defined connections without modification: the prototype's `uacp_prototype.mcp` server walks a directory of `.uacp` files, derives one MCP tool per operation, and dispatches tool calls through the UACP runtime so authentication, retry, pagination, error normalization, and audit logging all apply transparently to the MCP-side caller.

In one direction: UACP is what's behind the MCP tool. In the other direction: anything reachable via UACP is automatically reachable from any MCP host.

## Specification structure

The specification is composed of eight stage documents under [`docs/`](./docs/), read in order:

| Stage | Document | Scope |
|---|---|---|
| 0 | [Primer](./docs/00-primer.md) | Abstract, terminology, scope, prior-art comparison, document conventions. |
| 1 | [Principles](./docs/01-principles.md) | Twelve foundational design principles every later stage must satisfy. |
| 2 | [Authentication](./docs/02-authentication.md) | Ten registered authentication methods, the `secret://` credential-reference convention, and the registration mechanism for additional methods. |
| 3 | [Schema](./docs/03-schema.md) | `.uacp` artifact shape, JSON Schema profile, four pagination patterns, four schema sources (OpenAPI / curl / inferred / capture), validation rules. |
| 4 | [Dispatch](./docs/04-dispatch.md) | HTTPS transport, retry policy, pagination loops, rate-limit handling, error-envelope normalization, pluggable transport backends. |
| 5 | [Lifecycle](./docs/05-lifecycle.md) | Connection state machine, refresh policies, atomic rotation, revocation propagation, persistence. |
| 6 | [Security](./docs/06-security.md) | Threat model, secret-store registry, encryption-at-rest, scope enforcement, audit logging, trust posture for ingested artifacts. |
| 7 | [Versioning](./docs/07-versioning.md) | Semver scheme, the `$schema` URL identification scheme, deprecation process, governance for `v1.x`, path to `v2`. |

Visual references for the high-level architecture, the four-source schema convergence, MCP composition, and the capture pipeline live under [`docs/diagrams/`](./docs/diagrams/).

`SPEC.md` is the canonical entry point for serious readers; the eight stage docs are for implementers; this README is for first-time visitors.

## Status

`v1.1.0` is the current release of UACP, frozen on 2026-05-05. `v1.0.0` was the first stable release on 2026-05-04. Both releases are stable and supported. Subsequent `v1.x` releases are non-breaking per [Stage 7 — Versioning](./docs/07-versioning.md) §7.2; `v2` consideration begins when accumulated demand justifies it, and follows the public RFC process described in §7.6.

The canonical JSON Schema artifact is at [`schemas/uacp.json`](./schemas/uacp.json), pinned at the v1.1.0 git tag through every artifact's `$schema` URL.

## License and governance

UACP specification documents and the reference implementation are licensed under the Apache License 2.0. See [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE).

UACP is a personal project maintained by Alexander Walton. `v1.x` evolves under maintainer stewardship; outside contributors are welcome — see [`CONTRIBUTING.md`](./CONTRIBUTING.md). The full governance posture (including the path to `v2` and the trademark stance) is in [`GOVERNANCE.md`](./GOVERNANCE.md). Security concerns are handled through [`SECURITY.md`](./SECURITY.md). All participation is governed by the [`Code of Conduct`](./CODE_OF_CONDUCT.md).

# Please note this project is entirely vibecoded and built intially for a bespoke purpose, use at your own risk.
