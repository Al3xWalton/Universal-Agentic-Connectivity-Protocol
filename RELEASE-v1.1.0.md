# UACP `v1.1.0` — session capture + pluggable transports

**Released**: 2026-05-05.
**Tag**: [`v1.1.0`](https://github.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/releases/tag/v1.1.0).
**Canonical schema URL**: <https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.1.0/schemas/uacp.json>.
**Baseline**: `v1.0.0` (frozen 2026-05-04).

`v1.1.0` is the first non-breaking minor release of UACP, on top of the `v1.0.0` freeze. Per [§7.2](./docs/07-versioning.md), every artifact valid against `v1.0` continues to validate against `v1.1` — the new sections are purely additive. Implementations claiming `v1.0` conformance need no changes to consume `v1.1` artifacts under the forward-compatibility rules of §3.11 and §7.2.

The release strengthens UACP's universality story before Stage 10 production work begins: §3.12 establishes a fourth schema source (browser-demonstrated session captures) for providers without published APIs, and §4.10 codifies the conformance posture for substituting HTTP transport backends — particularly anti-bot-evading backends for `session_cookie` connections to fingerprint-defended providers.

## What's new in `v1.1.0`

### §3.12 — Schema source: session capture

A new schema source sibling to §3.6 (OpenAPI), §3.7 (`curl`-paste), and §3.8 (LLM inference). When a `Provider` has no published API specification and the user can demonstrate operations in a browser, an authoring implementation MAY capture the resulting HTTP traffic and infer `.uacp` operations from the recorded requests. The canonical pairing is with §2.10 `session_cookie` auth — captured cookies become the `Connection`'s credentials, captured requests become the operations.

Highlights:

- **Capture format.** Canonical `{request, response, timestamp, browser_metadata}` shape per entry. HAR (HTTP Archive) format is RECOMMENDED but not mandated. Per-request-variable headers (`Date`, `Authorization`, anti-CSRF tokens) MUST be flagged as variable so downstream inference doesn't bake captured values into the artifact as constants.
- **Operation inference.** Clustering by endpoint+method, path-parameter extraction by URL pattern matching, parameter-required-by-frequency. The §3.8 LLM path refines the inference; capture sets the empirical floor.
- **Provenance.** `source.type: "capture"` with required `captured_at` + `user_intent` + `capture_ref` + `reviewed_at`. The capture artifact is stored under the `capture_ref` URI (encrypted at rest per §6.3) so future refinement passes can re-analyze.
- **User review.** MUST gate parallel to §3.8 — capture-sourced operations cannot be persisted without explicit user approval. Both §3.11 conformance summary and a new MUST NOT entry codify this.
- **Source priority.** §3.9 priority order updated: explicit user > `curl`-paste > **captured session** > OpenAPI > LLM-inferred. Captured sessions outrank OpenAPI because the captured request is what the live service actually accepted, not the provider's self-description.

### §4.10 — Pluggable transport backends

Codifies the conformance posture under which an implementation MAY substitute different HTTP client libraries per `Connection`, per auth method, or per artifact (via the new optional `dispatch.transport` field). Motivation: anti-bot resilience for the §3.12 / §2.10 capture-plus-`session_cookie` pattern, where providers like NotebookLM, Cloudflare-protected SaaS surfaces, and similar grey-zone targets serve different responses to plain Python HTTP clients than to a real browser based on TLS fingerprinting (JA3/JA4), HTTP/2 frame ordering, header-order matching, etc.

Hard rule: substituting backends is permitted ONLY when the §4.1 — §4.9 contract is preserved. Anti-bot evasion does not excuse implementations from honoring `Retry-After` (§4.5), retry policy (§4.3), audit logs (§6.6), or canonical error mapping (§4.6). Provider-respect-norms don't bend just because the request shape mimics a browser.

Highlights:

- **`dispatch.transport` artifact field.** Optional. Recommended values: `"default"`, `"stealth"`, or `x-`-namespaced implementation identifiers per §7.3. Implementations that don't recognize the named transport MUST fall back to default with a warning rather than refuse to dispatch — graceful-degradation per §3.11's round-trip rule and §7.2's forward-compatibility posture.
- **Backend-specific exception types MUST NOT leak past the dispatch boundary.** A substitute backend that uses Playwright, curl-impersonate, or any other library re-raises its native exceptions as the spec's canonical types so callers don't have to know which backend served their request.
- **Audit-log identity.** Implementations SHOULD include `transport: "<backend-name>"` in §6.6 audit events when running pluggable backends, so forensics can distinguish a default-backend failure from a stealth-backend failure.
- **Out of scope for v1.1.** No registry of backend names. The `"default"` and `"stealth"` strings are conventions; the spec doesn't enumerate which implementations satisfy them. A future v1.x MAY register identifiers under §2.8 if implementations report fragmentation.

### Schema changes

- `$id` bumped to `v1.1.0`. Title + description updated.
- New `Source` variant for `type: "capture"` with the four required provenance fields.
- New optional `dispatch.transport` property with the recommended-or-`x-`-namespaced pattern.
- All ten `v1.0`-shipped example `.uacp` artifacts continue to validate cleanly — verified by running them through the v1.1 validator.

### Prototype changes

- **`prototype/python/src/uacp_prototype/dispatch/transport.py`** — new module implementing the `Transport` Protocol, `HttpxTransport` (default), and `ScraplingTransport` (optional, gated on the `stealth` extras). Adapter from Scrapling's response shape to `httpx.Response` so the dispatch loop's response-handling code stays transport-neutral.
- **`prototype/python/src/uacp_prototype/dispatch/client.py`** — refactored to route requests through `self._transport`. The `httpx_client=` parameter is kept as a back-compat alias; `transport=` takes precedence. Existing tests pass unchanged.
- **`select_transport_for_artifact(artifact)`** — implements the §4.10 decision tree (honor `dispatch.transport` field, then auth-method affinity for `session_cookie`, then default).
- **MCP server's default factory** — uses `select_transport_for_artifact` so the prototype's MCP composition surface honors the same selection rules.
- **NotebookLM example artifacts** — updated with `dispatch.transport: "stealth"` to declare their affinity explicitly.
- **Test count**: 452 unit tests passing (was 434 at `v1.0.0` freeze → +18 transport tests), 34 integration tests deselected by default (25 provider integration + 8 MCP integration + 1 scrapling-marked test).

## Backward compatibility

Per §7.2, `v1.1` is non-breaking:

- Every `v1.0`-valid artifact validates against the `v1.1` schema.
- Every `v1.0` Conforming Implementation continues to operate against `v1.1` artifacts under the forward-compatibility rules of §3.11 and §7.2 (unknown fields preserved on round-trip; unknown registered identifiers declined silently per §2.8).
- The `v1.0.0` schema URL continues to resolve indefinitely. Artifacts pinning `v1.0.0` need no changes; artifacts that want to surface `v1.1` features SHOULD repin to the `v1.1.0` URL when those features are used.
- The pre-v1.1 dispatch behavior is preserved when the `dispatch.transport` field is absent — implementations apply their default backend, identical to `v1.0`.

## What's deferred to future `v1.x` or `v2`

- **Capture flow implementation.** §3.12 specifies the schema source; the actual browser instrumentation and traffic recording lands in subsequent prototype sessions (Stage 11.1+). The Stage 11.0 prototype establishes the spec for capture, not the capture pipeline.
- **Proactive crawling.** Captured sessions are operator-driven (the user demonstrates operations explicitly). Automated crawl-based capture — where the implementation explores the provider's surface to pre-populate operations — is out of scope for v1 and a candidate for `v2` discussion if implementations report demand.
- **Browser-driven dispatch.** Some providers can't be reached at the HTTPS-request layer at all; they require a live browser session executing JavaScript. UACP's HTTPS-only transport per §4.2 forecloses this for v1; a `v2` major could relax the constraint.
- **Backend identifier registry.** §4.10 explicitly declines to register specific transport backend names (`httpx`, `scrapling`, `curl-impersonate`) in `v1.1`. A future `v1.x` MAY register identifiers under §2.8's mechanism if fragmentation warrants.
- **Streaming-upload UNSIGNED-PAYLOAD support in SigV4.** Carried over from `v1.0` deferrals; remains a future-`v1.x` concern when §4.7 streaming-request semantics land.
- **Interactive Playwright `capture-storage-state` CLI.** Currently a stub printing the manual recipe; the interactive form lands when Stage 11.1+ implements the capture pipeline.

## Spec changes after `v1.1.0`

- **Editorial fixes** (PATCH): typos, broken links, formatting, ambiguity-removing rephrasing without semantic change. Filed as `fix(spec): editorial — <description>`. No `$schema` URL change.
- **Non-breaking additions** (MINOR): new registered identifiers, new optional fields, new conformance MAY items. Filed as `feat(spec): v1.x — <description>`. The `$schema` URL bumps to `v1.<minor>.0` per §7.1.
- **Breaking changes** (MAJOR): require a `v2` major bump and the public RFC process per §7.6.

## Links

- [Specification index](./SPEC.md) — start here.
- [JSON Schema artifact (v1.1.0)](./schemas/uacp.json).
- [Python reference implementation](./prototype/python/).
- [Governance](./GOVERNANCE.md).
- [Contributing](./CONTRIBUTING.md).
- [License (Apache 2.0)](./LICENSE).
- [v1.0.0 release notes](./RELEASE-v1.0.0.md).
