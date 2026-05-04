# UACP Specification — `v1.1.0`

**Version**: `v1.1.0` — non-breaking minor release on top of `v1.0.0`.
**Status**: **Stable**. This document is the canonical specification index for UACP `v1.1`.
**Released**: 2026-05-04 (`v1.0.0` freeze) + 2026-05-05 (`v1.1.0`).

## Status

`v1.1.0` is the first non-breaking minor release on top of the `v1.0.0` freeze. Per [§7.2](./docs/07-versioning.md), every artifact valid against `v1.0` remains valid against `v1.1`; `v1.1` is a forward-compatible extension. The release adds two new sections: §3.12 (session-capture schema source) and §4.10 (pluggable transport backends). `v2` continues to follow the public RFC process described in [§7.6](./docs/07-versioning.md).

`v1.1` additions in summary:

- **§3.12 Session capture** — a new schema source sibling to §3.6 / §3.7 / §3.8. Records HTTP traffic from a browser-demonstrated session and infers `.uacp` operations from the recorded requests. Canonical pairing with §2.10 `session_cookie` auth. MAY-level conformance with mandatory user review before persistence.
- **§4.10 Pluggable transport backends** — codifies the conformance posture under which an implementation MAY substitute a different HTTP client library per `Connection`, per auth method, or per artifact (via the new optional `dispatch.transport` field). The §4.1 — §4.9 contract is preserved across backend substitutions; anti-bot evasion does not excuse implementations from rate limits, audit logs, or canonical error mapping.

`v1.0.0` (the underlying baseline): the wire format, the registered identifier sets across §2.1 / §3.4 / §6.2 / §7.3, the conformance vocabulary, and the canonical `$schema` URL are frozen. Subsequent `v1.x` releases are non-breaking per [§7.2](./docs/07-versioning.md); `v2` will follow the public RFC process described in [§7.6](./docs/07-versioning.md) if and when accumulated demand justifies it.

A `Conforming Implementation` of UACP `v1.x` is one that satisfies every `MUST` across every stage at the published version. Conformance is asserted against the union of the per-stage conformance summaries (§2.9, §3.11, §4.9, §5.7, §6.9, §7.7).

## Reading order

The specification is composed of eight stage documents under [`docs/`](./docs/). Read them in order — each builds on the terminology, principles, and registries established in the prior stages.

| Stage | Document | Status | Scope |
|---|---|---|---|
| 0 | [`docs/00-primer.md`](./docs/00-primer.md) | **Stable** | Abstract, terminology, scope, prior-art comparison, document conventions. |
| 1 | [`docs/01-principles.md`](./docs/01-principles.md) | **Stable** | Foundational design principles that constrain every later stage. |
| 2 | [`docs/02-authentication.md`](./docs/02-authentication.md) | **Stable** | Authentication subsystem: ten registered methods (§2.1), credential-reference convention (§2.7), extension mechanism (§2.8), session_cookie + ToS gate (§2.10). |
| 3 | [`docs/03-schema.md`](./docs/03-schema.md) | **Stable** | Schema layer: `.uacp` artifact shape, JSON Schema profile, body-predicate failure detection (§3.3), body-format discriminator (§3.3), pagination patterns (§3.4), source provenance (§3.5–§3.8 + §3.12 session capture, added in `v1.1`), validation rules (§3.10). |
| 4 | [`docs/04-dispatch.md`](./docs/04-dispatch.md) | **Stable** | Dispatch runtime: HTTPS transport, retry policy, pagination loops, rate-limit handling, error-envelope normalization, body-predicate evaluation (§4.6), pluggable transport backends (§4.10, added in `v1.1`). |
| 5 | [`docs/05-lifecycle.md`](./docs/05-lifecycle.md) | **Stable** | Connection lifecycle: state machine, refresh policies, atomic rotation, revocation propagation, persistence. |
| 6 | [`docs/06-security.md`](./docs/06-security.md) | **Stable** | Security model: secret-store registry (§6.2), encryption-at-rest, scope enforcement (§6.5), audit logging (§6.6), trust model (§6.7). |
| 7 | [`docs/07-versioning.md`](./docs/07-versioning.md) | **Stable** | Versioning policy, the `$schema` URL identification scheme, deprecation process, governance for `v1.x`, path to `v2`. |

## Conformance

Each stage closes with a Conformance subsection enumerating the MUST / MUST NOT / SHOULD / MAY items of the stage. A `Conforming Implementation` of `v1.x` satisfies every MUST across every section and behaves consistently with every SHOULD.

| Section | Document |
|---|---|
| §2.9 — Authentication conformance | [`docs/02-authentication.md`](./docs/02-authentication.md) |
| §3.11 — Schema conformance | [`docs/03-schema.md`](./docs/03-schema.md) |
| §4.9 — Dispatch conformance | [`docs/04-dispatch.md`](./docs/04-dispatch.md) |
| §5.7 — Lifecycle conformance | [`docs/05-lifecycle.md`](./docs/05-lifecycle.md) |
| §6.9 — Security conformance | [`docs/06-security.md`](./docs/06-security.md) |
| §7.7 — Versioning conformance | [`docs/07-versioning.md`](./docs/07-versioning.md) |

## Schema

The canonical JSON Schema 2020-12 artifact validating `.uacp` files is at [`schemas/uacp.json`](./schemas/uacp.json), referenced by every `.uacp` artifact through its top-level `$schema` field. The canonical URL pinned at this release:

```
https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.1.0/schemas/uacp.json
```

The `v1.0.0` schema URL (`.../v1.0.0/schemas/uacp.json`) continues to resolve and remains a valid reference for `v1.0`-pinned artifacts; the v1.1 schema is forward-compatible per §7.2, so `v1.0.0`-pinned artifacts continue to validate against the `v1.1.0` schema URL when implementations choose to re-pin.

The schema covers structural validation: top-level shape, the ten registered authentication methods per §2.1 (with method-specific `if`/`then` constraints, including the §2.10 session_cookie + literal-true `tos_acknowledged` rule), the request/response/pagination shapes per §3.2–§3.4, the body format discriminator per §3.3, the source provenance per §3.5–§3.8 plus §3.12 (capture, added in v1.1), the optional `dispatch.transport` hint per §4.10 (added in v1.1), the secret-reference convention per §2.7, and the inline-encrypted secret shape per §6.2. Semantic validations beyond pure structural checking (bidirectional path-parameter coverage, embedded-credential detection, `$ref` locality, inferred-source provenance completeness, capture-source provenance completeness) remain the implementation's responsibility per §3.10.

## Versioning

UACP follows semantic versioning per [§7.1](./docs/07-versioning.md). The `$schema` URL identifies MAJOR.MINOR; PATCH releases do not change the URL. v1.x revisions are non-breaking per §7.2. v2 follows a public RFC process per §7.6.

## License and governance

- **License.** UACP specification documents are licensed under the Apache License 2.0. See [`LICENSE`](./LICENSE).
- **Governance.** UACP is a personal project maintained by Alexander Walton. v1.x evolves under maintainer stewardship; v2 follows the public RFC process per [`GOVERNANCE.md`](./GOVERNANCE.md).
- **Contributing.** See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for issue + PR conventions.

## Changes after freeze

Any spec change after the v1.0.0 freeze MUST be either a patch-level editorial fix (`fix(spec): editorial — <description>`, no `$schema` URL change, PATCH bump) or a v1.x non-breaking addition per §7.2 (`feat(spec): v1.x — <description>`, `$schema` URL bumped to `v1.<minor>.0`, MINOR bump). Breaking changes require v2 per §7.6.
