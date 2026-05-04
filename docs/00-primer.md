# UACP Primer

## Abstract

The Universal Agentic Connectivity Protocol (UACP) is a wire format and runtime contract for describing how an AI agent authenticates to and dispatches operations against an external service. UACP is a peer to the Model Context Protocol (MCP): where MCP standardizes the agent-to-tool surface, UACP standardizes the agent-to-external-service surface, and the two compose by allowing UACP-defined connections to be exposed through MCP servers as tools.

## Status

This document is part of the canonical UACP specification, currently under active development. The specification is versioned `v0.1` and is on a path to a `v1.0` freeze. All `v0.x` documents are subject to change without backward-compatibility guarantees. Once `v1.0` is published, the rules in [Stage 7 — Versioning](./07-versioning.md) (forthcoming) govern subsequent changes; in particular, all `v1.x` revisions MUST remain backward-compatible with `v1.0`.

UACP is developed in public from day one. The reference implementation lives in the `connections-broker` service of the AVA monorepo (a separate repository); this repository contains specification documents only and contains no implementation code.

## Terminology

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document and all other UACP specification documents are to be interpreted as described in BCP 14 [[RFC2119](https://datatracker.ietf.org/doc/html/rfc2119)] [[RFC8174](https://datatracker.ietf.org/doc/html/rfc8174)] when, and only when, they appear in all capitals, as shown here.

The remaining definitions in this section are specific to UACP.

**Connection.** A `Connection` is the persistent association between an agent (acting on behalf of a user) and an external service. A `Connection` carries the credential material required to dispatch operations against that service, the metadata describing what operations are reachable, and the lifecycle state (`pending`, `active`, `revoked`, or an extension state). A `Connection` is the unit of revocation: revoking a `Connection` MUST render every operation it described unreachable for that agent.

**Provider.** A `Provider` is the external service that a `Connection` connects to. A `Provider` is identified by a stable, lowercase, dot-free string identifier (for example, `gmail`, `google_calendar`, `github`, `slack`). A `Provider` MAY be registered against a public catalog or MAY be private to an implementation; the protocol does not require a central registry.

**Authentication Method.** An `Authentication Method` is the procedure by which an agent proves identity to a `Provider` before a `Connection` becomes `active`. UACP v1.0 will define a small core set of authentication methods (OAuth 2.0 / 2.1 authorization-code flow with PKCE, OAuth 2.0 client-credentials, static API key, and HMAC-signed request) and a registration mechanism for additional methods. Multiple `Authentication Method`s MAY be supported by a single `Provider`; a `Connection` records the specific method it used.

**Dispatch.** `Dispatch` is the act of invoking an operation against a `Provider` through an `active` `Connection`. The dispatch surface is an `Action` identifier, an input payload validated against a JSON Schema, and a return shape validated against a JSON Schema. A dispatch MUST surface failure modes (`auth_expired`, `rate_limited`, `bad_input`, `upstream_error`, `not_found`, `forbidden`) in a uniform way regardless of the underlying `Provider`'s native error vocabulary.

**Schema.** A `Schema` is the JSON Schema document that describes the input or output of an `Action`. UACP specifies a profile of JSON Schema (Draft 2020-12) that all conforming `Schema` documents MUST validate against. The profile excludes constructs (such as remote `$ref` resolution at dispatch time) that would compromise determinism.

**Authoring.** `Authoring` is the process of producing a canonical `.uacp` artifact for a new `Provider`. Authoring MAY be performed manually by a human, programmatically by a tool, or — the primary intended pattern — by an AI agent interpreting a natural-language description supplied by a user. The protocol does not specify the agent, the prompt structure, or the validation flow used during authoring; it specifies only the validation rules the resulting artifact MUST satisfy before storage.

**Wire Format.** The `Wire Format` is the on-disk and on-wire representation of UACP artifacts. UACP defines a single wire format (see [Document Conventions](#document-conventions)) and assigns it a branded MIME type and file extension. Implementations MUST NOT define alternative wire formats and call them UACP.

**Conformance.** A `Conforming Implementation` is one that satisfies every `MUST` in the canonical specification documents and behaves consistently with every `SHOULD`. The conformance test suite is a [Stage 8](./08-conformance.md) deliverable (forthcoming) that exercises the `MUST` requirements of every prior stage. An implementation that fails any `MUST` is non-conforming, regardless of how the failure presents.

## Scope

UACP defines:

- A canonical, JSON-Schema-validated wire format describing how an agent connects to and dispatches operations against an external service.
- A small set of authentication methods sufficient for the long tail of HTTPS-fronted services, plus an extension mechanism for additional methods.
- A dispatch contract that maps `Action` invocations to underlying `Provider` HTTPS calls and normalizes failure modes.
- A `Connection` lifecycle (creation, refresh, revocation) consistent across `Provider`s.
- A versioning policy that allows `v1.x` to evolve without breaking conforming consumers.

UACP does not define:

- A tool-call protocol between an agent and the broader runtime that hosts it. That surface is the responsibility of MCP and similar protocols. UACP describes the connection; MCP (or an equivalent) carries the call.
- A workflow or orchestration framework. Multi-step plans, retries with backoff, fan-out / fan-in, and other higher-level patterns are out of scope.
- A transport other than HTTPS for `Provider` communication. Implementations MAY layer additional transports (WebSocket, gRPC, computer-use) onto a UACP `Connection` once those transports are added to the specification through the [Stage 7](./07-versioning.md) process; UACP v1.0 mandates HTTPS only.
- A central authority, a directory service, or a public catalog. `Provider` identifiers are drawn from a flat, conventionally agreed namespace; conformance does not require participation in any registry.
- A particular AI model, prompt structure, or authoring user interface. The protocol describes what a valid `.uacp` artifact looks like, not how it was produced.

## Comparison to prior art

UACP exists alongside several adjacent specifications and runtimes. This section locates UACP in that landscape; it does not attempt a complete survey.

### Model Context Protocol (MCP)

MCP standardizes the surface between an LLM application (the host) and the tools, resources, and prompts that the host exposes to the model. MCP is a peer to UACP: an MCP server MAY expose UACP-defined connections as tools, and an MCP client SHOULD treat such tools no differently from any other MCP tool. UACP and MCP do not overlap on scope. MCP describes how an agent calls a tool; UACP describes how a tool, once called, reaches the external service it connects to. The two protocols share JSON-Schema validation as a common substrate and adopt the same RFC 2119 / 8174 conformance vocabulary.

### OpenAPI

OpenAPI describes the HTTPS surface of a single service in detail. UACP overlaps with OpenAPI on `Action` shape and JSON Schema usage, but UACP additionally specifies the `Connection` lifecycle (authentication, refresh, revocation), the agent-side dispatch contract, the AI-authoring affordances that make `Provider` descriptions producible from natural language, and a uniform failure-mode vocabulary across `Provider`s. An OpenAPI document for a single `Provider` MAY be incorporated by reference into a `.uacp` artifact; UACP does not replace OpenAPI for service description.

### Composio and similar curated catalogs

Composio is a SaaS catalog that brokers OAuth flows and tool dispatch for a curated list of providers. UACP differs philosophically: it is universal-by-design, not catalog-curated. A long-tail provider is reachable via UACP whenever a user (or agent) can produce a valid `.uacp` artifact for it; no central party gates which providers are admitted to the protocol. A curated catalog MAY be built on top of UACP, but UACP itself takes no position on which `Provider`s exist.

### OAuth 2.0 / 2.1 RFC family

UACP's authentication subsystem subsumes OAuth 2.0 [[RFC6749](https://datatracker.ietf.org/doc/html/rfc6749)], OAuth 2.1 (in progress), PKCE [[RFC7636](https://datatracker.ietf.org/doc/html/rfc7636)], the device authorization grant [[RFC8628](https://datatracker.ietf.org/doc/html/rfc8628)], and related specifications as one `Authentication Method` family among several. UACP does not modify or extend the OAuth specifications; it embeds them. Where OAuth defines the credential exchange, UACP defines the surrounding `Connection` lifecycle and the dispatch surface that consumes the credentials.

## Document conventions

- **File extension.** UACP artifacts use the file extension `.uacp` (single dot; not `.uacp.json`). Tooling MUST recognize this extension.
- **MIME type.** UACP artifacts are served with MIME type `application/uacp+json`. The `+json` structured-syntax suffix [[RFC6839](https://datatracker.ietf.org/doc/html/rfc6839)] indicates that the wire format is JSON and that a generic JSON consumer MAY parse the bytes, while UACP-aware consumers MUST apply additional validation.
- **Wire format.** UACP artifacts are JSON documents ([[RFC8259](https://datatracker.ietf.org/doc/html/rfc8259)]) validated against a JSON Schema (Draft 2020-12). Each artifact references its schema version through a `$schema` URL whose specific form is finalized in Stage 3.
- **Versioning.** UACP adopts semantic versioning over the wire format and the conformance vocabulary. Within a major version (`v1.x`), all changes are backward-compatible: an artifact valid against `v1.0` MUST remain valid against `v1.x` for any later `x`. Breaking changes require a major-version bump.
- **Conformance language.** Every UACP specification document uses RFC 2119 / 8174 keywords as defined in [Terminology](#terminology). Conformance is asserted against the union of `MUST` and `MUST NOT` statements across all canonical documents.
- **Voice.** UACP specification documents are written in declarative, third-person prose. Conversational hedging, first-person voice, and time-bounded references ("currently", "as of this writing") are avoided in favor of explicit version references.

## Spec structure

The UACP specification is composed of the documents under `docs/`, indexed by stage. Each stage is a separate document so that revisions can target a single layer without disturbing the others.

| Stage | Document | Status | Scope |
|---|---|---|---|
| 0 | [`00-primer.md`](./00-primer.md) | Complete (this document) | Abstract, terminology, scope, prior-art comparison, document conventions. |
| 1 | [`01-principles.md`](./01-principles.md) | Complete | Foundational design principles that constrain every later stage. |
| 2 | `02-authentication.md` | Pending | Authentication subsystem: core methods, extension mechanism, credential storage rules. |
| 3 | `03-schema.md` | Pending | Schema layer: `.uacp` artifact shape, JSON Schema profile, validation rules. |
| 4 | `04-dispatch.md` | Pending | Dispatch runtime: invocation surface, parameter binding, transport rules, error normalization. |
| 5 | `05-lifecycle.md` | Pending | Connection lifecycle: creation, refresh, revocation, observability. |
| 6 | `06-security.md` | Pending | Security model: secret storage, scope enforcement, threat model. |
| 7 | `07-versioning.md` | Pending | Versioning policy and the public RFC process for `v2` and beyond. |
| 8 | `08-conformance.md` | Pending | Conformance test suite definition and the procedure for self-certification. |
| 9 | `09-prototype.md` | Pending | Reference-implementation guidance and prototype freeze criteria. |
| 10 | `10-freeze.md` | Pending | `v1.0` freeze rules and the migration path from `v0.x` artifacts. |

A reference implementation of `v1.x` lives outside this repository, in the AVA monorepo, at `backend/services/connections-broker/`. The specification is the canonical artifact; implementations follow.
