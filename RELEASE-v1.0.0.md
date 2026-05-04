# UACP `v1.0.0` — first stable release

**Released**: 2026-05-04.
**Tag**: [`v1.0.0`](https://github.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/releases/tag/v1.0.0).
**Canonical schema URL**: <https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.0.0/schemas/uacp.json>.

UACP — the Universal Agentic Connectivity Protocol — is a wire format and runtime contract for describing how an AI agent authenticates to and dispatches operations against an external service. UACP is a peer to the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/): MCP standardizes the agent-to-tool surface, UACP standardizes the agent-to-external-service surface, and the two compose. `v1.0.0` freezes UACP's design and validates it end-to-end against five providers and against an MCP-aware client.

This release closes the design phase. From this moment on, every change to `v1.x` is editorial (PATCH), non-breaking (MINOR), or held for `v2`. `v2` follows a public RFC process per [GOVERNANCE.md](./GOVERNANCE.md) and [§7.6](./docs/07-versioning.md).

## What's new in `v1.0.0`

### Stable specification — eight stage documents under [`docs/`](./docs/)

- [Stage 0 — Primer](./docs/00-primer.md): abstract, terminology, scope, prior-art comparison, document conventions.
- [Stage 1 — Principles](./docs/01-principles.md): twelve foundational design principles every later stage must satisfy.
- [Stage 2 — Authentication](./docs/02-authentication.md): ten registered authentication methods (OAuth 2.0 ×3, OAuth 1.0a, API key ×2, AWS SigV4, generic HMAC, session_cookie, custom_auth), the `secret://` credential-reference convention, the registration mechanism for additional methods.
- [Stage 3 — Schema](./docs/03-schema.md): `.uacp` artifact shape, JSON Schema profile, four pagination patterns (cursor / offset / link_header / none), three provenance shapes (openapi / curl / inferred), body-format discriminator (json/xml/binary/text), body-predicate failure detection.
- [Stage 4 — Dispatch](./docs/04-dispatch.md): HTTPS transport, retry policy with exponential backoff and jitter, rate-limit handling, error-envelope normalization, Principle 8's canonical error vocabulary.
- [Stage 5 — Lifecycle](./docs/05-lifecycle.md): connection state machine, refresh policies, atomic refresh-token rotation, revocation propagation, persistence requirements.
- [Stage 6 — Security](./docs/06-security.md): threat model, secret-store registry (vault / aws-secrets-manager / local-keyring / inline-encrypted), encryption-at-rest, scope enforcement, audit logging, trust posture for ingested artifacts.
- [Stage 7 — Versioning](./docs/07-versioning.md): semver scheme, `$schema` URL identification, deprecation process, governance for `v1.x` evolution, path to `v2`.

### Canonical artifacts

- **JSON Schema 2020-12** at [`schemas/uacp.json`](./schemas/uacp.json) validates the wire format. Referenced by every `.uacp` artifact through the canonical `$schema` URL pinned at this release.
- **Conformance summaries** at the end of every stage doc (§2.9, §3.11, §4.9, §5.7, §6.9, §7.7) enumerate the MUST / MUST NOT / SHOULD / MAY items a Conforming Implementation satisfies.

### Reference implementation in Python

A complete Python reference implementation lives at [`prototype/python/`](./prototype/python/). The implementation covers every `MUST` of every stage and is the source of the conformance evidence below.

- 434 unit tests + 33 integration tests (deselected by default — 25 provider integration + 8 MCP integration).
- 10 example `.uacp` artifacts at `prototype/python/examples/` covering all five validated providers.
- An MCP server adapter at `src/uacp_prototype/mcp/` that exposes UACP operations as MCP tools.

## Stage 8 prototype validation — five providers, four spec amendments

`v1.0.0`'s contents reflect five rounds of provider-validation against real services. Each round shipped a non-breaking amendment that closed a gap the implementation surfaced:

| Provider | Headline spec amendment | Auth method exercised |
|---|---|---|
| **Google** (Gmail send + Calendar list) | — (baseline; no amendment) | `oauth2_authorization_code` + cursor pagination |
| **Slack** (chat.postMessage + conversations.list) | §3.3 + §4.6 — body-predicate failure detection (the 200-with-`{ok: false}` envelope) | workspace-scoped OAuth (Slack flavor) + cursor pagination |
| **AWS S3** (GetObject + ListObjectsV2) | §3.3 — body format discriminator (json / xml / binary / text) | `aws_sigv4` + XML response handling |
| **GitHub** (repos.get + repos.listForUser) | §3.4 — RFC 8288 link-header conformance rules (case-insensitive rel matching, multi-rel entries, multi-Link-header concatenation, relative-URI resolution, link parameters beyond rel, comma-in-brackets edge cases) | `api_key_header` + RFC 8288 link-header pagination |
| **NotebookLM** (list-notebooks + send-chat-message) | §2.1 + new §2.10 — session_cookie auth registration with mandatory `tos_acknowledged: true` literal-boolean enforcement at the spec-loader level + §6.6 audit-log emit + §6.7 30-day staleness guidance | `session_cookie` + LLM-inferred schema authoring per §3.8 |

All four amendments are non-breaking per Principle 6 / §7.2 — they add optional fields or new registered identifiers, leaving every prior-stage artifact valid against the post-amendment spec.

## Stage 9 — MCP composition validation

Principle 4 (composability with MCP) is now demonstrated end-to-end. The prototype's `uacp_prototype.mcp` package wires a Model Context Protocol server that walks a directory of `.uacp` artifacts and exposes each operation as an MCP tool. Tool names normalize to `<provider>_<operation_id>` (with the OpenAI / Anthropic / Google `^[a-zA-Z0-9_-]{1,128}$` validation regex applied); tool input schemas derive from each operation's §3.2 request shape; tool execution dispatches through the existing UACP runtime so security and dispatch invariants are preserved.

The `tests/integration/test_mcp_composition.py` suite (8 tests, marked `@pytest.mark.mcp_integration`) pairs the prototype's `UACPServer` with the MCP SDK's `ClientSession` over in-process memory streams and verifies: server startup, tool advertisement, tool-schema derivation, tool execution returning `DispatchSuccess`, argument pass-through, canonical error propagation per §4.6, credential-resolution-failure surfacing, and multi-provider routing.

Setup instructions for connecting Claude Code, Cursor, or Claude Desktop to a UACP-backed MCP server are in [`prototype/python/README.md`](./prototype/python/README.md#mcp-composition).

## What's stable in `v1.0`

The wire format is frozen. `v1.x` MUST NOT introduce breaking changes:

- The ten `v1.0` registered authentication methods at §2.1 (with §2.10's session_cookie + ToS gate).
- The four pagination patterns at §3.4 (cursor, offset, link_header, none) plus the RFC 8288 conformance rules.
- The four registered secret-store types at §6.2 (vault, aws-secrets-manager, local-keyring, inline-encrypted).
- The body-format discriminator at §3.3 (json, xml, binary, text).
- The body-predicate failure detection shape at §3.3 + §4.6.
- The `secret://` URI convention at §2.7.
- The `Conforming Implementation` MUST set across §2.9, §3.11, §4.9, §5.7, §6.9, §7.7.
- The canonical `$schema` URL at the v1.0.0 git tag (future minor releases pin to their own MAJOR.MINOR git tags per §7.1).

## What's deferred to `v1.x`

Non-breaking additions accepted into future minor releases per §7.2 + the registration mechanism at §2.8:

- Additional registered authentication methods (when a provider surfaces a clean shape that doesn't fit any current method, and uptake justifies registration).
- Additional pagination patterns (keyset, page-number, timestamp-windowed).
- Additional secret-store types (cloud-specific managers, K8s secrets).
- Additional streaming patterns at §4.7 beyond the current registered set.
- The `auth/custom.py` per-§2.6 implementation in the prototype (the `custom_auth` method is registered at §2.1; the prototype's adapter is a Stage 9+ deliverable when a real provider needs it).
- Interactive Playwright `capture-storage-state` CLI for `session_cookie` (currently a stub printing the manual recipe).
- Streaming-upload `UNSIGNED-PAYLOAD` support in SigV4 (deferred until §4.7's streaming-request semantics are added).

## What's deferred to `v2`

Per §7.6, `v2` consideration begins when one of: 3+ deprecated identifiers accumulate across the registries, foundational constraints prove structurally inadequate, or adoption demand surfaces a feature cluster the `v1.x` extension model can't accommodate. None apply at `v1.0.0` release. The `v2` RFC process is intentionally not specified in advance per Principle 12.

Specific items already discussed as `v2` candidates if and when:

- A wire-format substrate other than JSON (CBOR, MessagePack) — would require a new MAJOR.
- A transport other than HTTPS for Provider communication (WebSocket, gRPC, computer-use) added through a major-version event rather than `v1.x`'s extension surface.
- Renaming any registered identifier (rename = remove + add, breaking).
- Removing a deprecated `v1.x` identifier (per §7.4 deprecations remain functional for the rest of `v1.x`; removal is a `v2` event).

## Spec changes after freeze

- **Editorial fixes** (PATCH): typos, broken links, formatting, ambiguity-removing rephrasing without semantic change. Filed as `fix(spec): editorial — <description>`. No `$schema` URL change.
- **Non-breaking additions** (MINOR): new registered identifiers, new optional fields, new conformance MAY items. Filed as `feat(spec): v1.x — <description>`. The `$schema` URL bumps to `v1.<minor>.0` per §7.1.
- **Breaking changes** (MAJOR): require a `v2` major bump and the public RFC process per §7.6.

## Links

- [Specification index](./SPEC.md) — start here.
- [JSON Schema artifact](./schemas/uacp.json).
- [Python reference implementation](./prototype/python/).
- [Governance](./GOVERNANCE.md).
- [Contributing](./CONTRIBUTING.md).
- [License (Apache 2.0)](./LICENSE).
- [Code of Conduct](./CODE_OF_CONDUCT.md).

## Acknowledgments

UACP is the result of the design + prototype-validation cycle Stages 0–9 walked through 2026-04 to 2026-05. The cycle was operated by Alexander Walton against AI coding agents (Claude Opus and Codex), with each session anchored against the prior session's memory commits in [`docs/memory/`](./docs/memory/). The five Stage 8 provider sessions surfaced four amendments that materially improved the spec; without that real-world contact, `v1.0.0` would have shipped weaker. UACP's design also benefited from prior art: the [OAuth 2.0 / 2.1 RFC family](https://datatracker.ietf.org/wg/oauth/about/), [OpenAPI](https://www.openapis.org/), the [Model Context Protocol](https://modelcontextprotocol.io/), AWS's published [SigV4 reference](https://docs.aws.amazon.com/general/latest/gr/sigv4_signing.html), and [RFC 8288](https://datatracker.ietf.org/doc/html/rfc8288). Where UACP embeds these, it does so without modification.
