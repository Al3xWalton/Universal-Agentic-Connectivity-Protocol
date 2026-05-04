# UACP Specification — Index

The canonical UACP specification is composed of the documents under [`docs/`](./docs/), indexed by stage. Each stage is a separate document so that revisions can target a single layer without disturbing the others. This file is the entry point and the authoritative status table.

| Stage | Document | Status | Scope |
|---|---|---|---|
| 0 | [`docs/00-primer.md`](./docs/00-primer.md) | **Complete** | Abstract, terminology, scope, prior-art comparison, document conventions. |
| 1 | [`docs/01-principles.md`](./docs/01-principles.md) | **Complete** | Foundational design principles that constrain every later stage. |
| 2 | `docs/02-authentication.md` | Pending | Authentication subsystem: core methods, extension mechanism, credential storage rules. |
| 3 | `docs/03-schema.md` | Pending | Schema layer: `.uacp` artifact shape, JSON Schema profile, validation rules. |
| 4 | `docs/04-dispatch.md` | Pending | Dispatch runtime: invocation surface, parameter binding, transport rules, error normalization. |
| 5 | `docs/05-lifecycle.md` | Pending | Connection lifecycle: creation, refresh, revocation, observability. |
| 6 | `docs/06-security.md` | Pending | Security model: secret storage, scope enforcement, threat model. |
| 7 | `docs/07-versioning.md` | Pending | Versioning policy and the public RFC process for `v2` and beyond. |
| 8 | `docs/08-conformance.md` | Pending | Conformance test suite definition and the procedure for self-certification. |
| 9 | `docs/09-prototype.md` | Pending | Reference-implementation guidance and prototype freeze criteria. |
| 10 | — (separate repository) | Pending | Reference implementation lives in the AVA monorepo at `backend/services/connections-broker/`. |

A `Conforming Implementation` of UACP `v1.x` is one that satisfies every `MUST` across every stage marked **Complete** at the published version. Until `v1.0` is frozen, conformance is provisional and subject to revision.

## How to read the specification

Read the documents in stage order. Each document assumes the terminology and principles established in earlier stages; reading later stages first is reliable only after Stage 0 and Stage 1 are internalized.

When this index disagrees with a stage document about whether a stage is complete, the stage document's own front matter is authoritative.
