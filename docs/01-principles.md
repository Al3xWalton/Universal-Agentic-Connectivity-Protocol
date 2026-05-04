# UACP Foundational Principles

This document records the foundational design principles that constrain every later stage of the UACP specification. The conformance keywords ("MUST", "SHOULD", "MAY", and so forth) appearing in this document are interpreted per BCP 14 [[RFC2119](https://datatracker.ietf.org/doc/html/rfc2119)] [[RFC8174](https://datatracker.ietf.org/doc/html/rfc8174)] as established in [Stage 0 — Primer](./00-primer.md).

The principles below are normative. Subsequent stages MUST be consistent with each principle; an apparent conflict between a later stage and a principle SHALL be resolved by amending the later stage, never by silently overriding the principle.

## 1. Layered architecture

UACP separates the connectivity surface into five layers: **Authentication** (how an agent proves identity to a `Provider`), **Schema** (what operations a `Provider` exposes and the JSON shapes that bind to them), **Dispatch** (how an agent invokes those operations and how failures normalize), **State** (the `Connection` lifecycle from creation through revocation), and **Security** (how secrets are stored, how scopes are enforced, and how the protocol resists abuse). Each layer is described in its own subsequent stage document and MUST be addressable in isolation. A change to one layer SHOULD NOT require coordinated edits to another, and a `Conforming Implementation` MUST be implementable as five composable subsystems even if it ships them as a single binary. The layered shape is the structural commitment that keeps UACP small as it grows.

## 2. Universal by design

Any external service describable through a supported `Authentication Method` and a sequence of HTTPS calls MUST be reachable through a valid `.uacp` artifact, without prior admission to a registry, catalog, or vendor relationship. The protocol explicitly rejects the curated-catalog model in which a central party decides which `Provider`s exist; long-tail providers are first-class and follow the same authoring path as any household-name service. A `Conforming Implementation` MUST NOT predicate `Provider` reachability on membership in a particular vendor's directory. Implementations MAY maintain optional curated lists for discoverability or quality assurance, but those lists MUST NOT gate dispatch.

## 3. AI-native authoring

The primary authoring surface for new `.uacp` artifacts is natural language interpreted by an agent — typically the same agent that will later dispatch against the resulting `Connection`. The protocol does not specify the agent, the prompt structure, the human-review user interface, or the validation flow used during authoring; those are implementation concerns. The protocol specifies what a canonical `.uacp` artifact looks like once authored, and the rules a `Conforming Implementation` MUST apply before storing one. Direct human authoring of `.uacp` files is permitted and supported, but it is not the expected default; the wire format is JSON because JSON is machine-readable, not because humans are expected to type it.

## 4. Composability with MCP

UACP is a peer to the Model Context Protocol, not a competitor. UACP-defined connections SHOULD be exposable through MCP servers as tools, and a `Conforming Implementation` SHOULD provide a documented translation from a UACP `Action` to an MCP tool definition. An agent that already speaks MCP SHOULD be able to consume a UACP-backed MCP server with no UACP-specific code on the agent side. The reciprocal direction — invoking MCP tools through UACP — is out of scope for v1; UACP describes the `Connection` to a `Provider`, not the surface between agent and runtime.

## 5. Pluggable authentication, pluggable dispatch

Both the authentication subsystem and the dispatch runtime MUST support extension. A new `Authentication Method` (for example, a vendor-specific HMAC scheme or a future post-OAuth standard) and a new dispatch transport (for example, GraphQL, gRPC, or a computer-use surface, once added through the [Stage 7](./07-versioning.md) process) MUST be addable without invalidating any artifact valid under the prior version. Extensions MUST register through a documented mechanism so that consumers can advertise which methods and transports they accept. Implementations MAY ship subsets of the registered set; they MUST surface their supported set explicitly rather than failing dispatch silently.

## 6. Wire-format stability

Once `v1.0` is frozen, every `v1.x` revision MUST be backward-compatible with `v1.0`: an artifact valid against `v1.0` MUST remain valid against every later `v1.x`, and a `Conforming Implementation` of any `v1.x` MUST accept every `v1.0` artifact. Breaking changes — including, but not limited to, removing a field, narrowing the value space of a field, changing a field's type, or altering the dispatch contract — REQUIRE a `v2` major-version bump. The `v2` evolution path follows the public RFC process specified in [Stage 7](./07-versioning.md). Backward-compatibility is asserted at the artifact level and at the dispatch-behavior level; an artifact that parses but dispatches differently between minor versions is a breaking change.

## 7. Security by default

Credentials MUST NOT be embedded in `.uacp` artifacts. Artifacts describe how to authenticate (the `Authentication Method`, the authorization endpoints, the scope set) and how to dispatch (the `Action` set and their schemas), but the credentials themselves — access tokens, refresh tokens, API keys, signing secrets — MUST live in a separate secret store referenced by an opaque identifier. Tokens at rest MUST be encrypted; the encryption mechanism is an implementation choice but a `Conforming Implementation` MUST document its choice in a form accessible to security reviewers. `.uacp` artifacts are designed to be safe to share publicly: a published artifact MUST NOT, by itself, grant access to any user's account.

## 8. Failure-mode uniformity

A dispatch failure MUST surface through a small, fixed vocabulary regardless of the underlying `Provider`'s native error vocabulary. The v1 set is `auth_expired`, `rate_limited`, `bad_input`, `upstream_error`, `not_found`, `forbidden`, and `cancelled`; subsequent stages MAY refine the set but MUST NOT remove members within `v1.x`. Provider-native error payloads MAY be retained as auxiliary data on the failure object for diagnosis, but consumers MUST be able to act on the normalized code without parsing provider-specific details. Uniform failure modes are what allow agent-side retry, fallback, and user-facing messaging to be written once across the long tail of `Provider`s.

## 9. Determinism

Given a `.uacp` artifact, an `active` `Connection`, and a dispatch invocation, two `Conforming Implementation`s MUST produce indistinguishable observable behavior up to the `Provider`'s own non-determinism. Specifically, the choice of which HTTPS endpoint to call, which headers to send, how parameters bind into the request, and how the response normalizes into the dispatch return shape MUST be fully determined by the artifact and the runtime inputs. Constructs that introduce implementation-specific behavior — remote `$ref` resolution at dispatch time, time-of-dispatch artifact mutation, ambient global state — MUST be excluded from the schema profile or normalized away before dispatch. Determinism is the property that lets `.uacp` artifacts be audited, signed, version-pinned, and trusted.

## 10. Public artifacts, private secrets

A `.uacp` artifact SHOULD be considered shareable by default. Authors and tooling MUST treat the artifact and the credentials it points at as separate concerns: an artifact MAY be checked into version control, indexed by search engines, or attached to support tickets without compromising any user's account. This separation makes community curation, third-party review, and AI-assisted authoring practical at scale. Implementations that need to mark an artifact as private (for example, to protect a competitive integration) MAY do so out-of-band, but the protocol does not provide an in-artifact privacy flag.

## 11. Transport minimalism

UACP `v1.0` mandates HTTPS as the sole `Provider` transport. Additional transports — WebSocket, gRPC, computer-use through screen and input control, and any other future shape — MAY be added through the [Stage 7](./07-versioning.md) process, but `v1.0` `Conforming Implementation`s MUST support HTTPS dispatch and MUST reject artifacts that demand unsupported transports with a clear `bad_input` failure. The minimalist v1 surface is intentional: it covers the overwhelming majority of external services without committing the protocol to ambient runtime capabilities (such as desktop control) that have unrelated security properties.

## 12. Open governance

UACP `v1.x` evolves under the stewardship of the protocol's authoring organization, with public issue tracking and pull-request review for editorial fixes. `v2` and beyond evolve through a public RFC process, the rules of which are specified in [Stage 7](./07-versioning.md). Outside contributors MAY propose changes at any time through the mechanisms described in this repository's `CONTRIBUTING.md`; the steward MUST respond in public. Trademark and brand policy is documented separately (see `GOVERNANCE.md`); the protocol's name, logo, and conformance marks MUST NOT be applied to non-conforming implementations.
