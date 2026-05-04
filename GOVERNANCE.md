# UACP Governance

This document records the governance posture of UACP from `v1.0.0` (frozen 2026-05-04) onward. The model is intentionally simple while the specification is small; the trademark and amendments sections describe posture that holds across versions.

## v1.x stewardship

UACP `v1.x` evolves under the stewardship of its maintainer. `v2` and beyond will follow a public RFC process if and when the protocol's user base justifies one. Until that point, all design discussion happens in public — in this repository's issues and pull requests — and outside contributions follow the rules in [`CONTRIBUTING.md`](./CONTRIBUTING.md).

## How to influence `v1.x` evolution

`v1.x` is **stable**. The wire format, the registered identifier sets across §2.1 / §3.4 / §6.2 / §7.3, the conformance vocabulary, and the canonical `$schema` URL pinned at each minor release are frozen for the rest of `v1.x`. Within those constraints, `v1.x` continues to grow:

- **New auth methods** — register additional `Authentication Method`s through the §2.8 mechanism. File a `[RFC v1.x]`-prefixed issue with the wire shape, the conformance posture, and at least one real provider exercising it.
- **New pagination patterns** — keyset, page-number, timestamp-windowed, and similar patterns are non-breaking additions to §3.4. Same proposal path.
- **New secret-store types** — additional §6.2 secret-store identifiers (cloud-specific managers, K8s secrets, HSM-backed stores) follow the same registration mechanism.
- **New transport backends** — §4.10 opened the door for pluggable HTTP backends. Implementation-specific transports MAY use `x-`-namespaced identifiers per §7.3 without a registry change; promoting a backend identifier into the registered set is a `[RFC v1.x]` proposal.
- **Editorial fixes** — typos, broken links, formatting, ambiguity-removing rephrasing without semantic change. Land directly as PRs per [`CONTRIBUTING.md`](./CONTRIBUTING.md).
- **New conformance MAY items** — clarifying language that adds optional behavior without narrowing existing implementations.

Outside contributors propose; the maintainer decides. Non-breaking additions land as PRs after the issue discussion converges. Breaking changes wait for `v2`.

## When `v2` becomes worth pursuing

`v2` consideration begins when one of the following thresholds is crossed:

- **Accumulated deprecations.** `v1.x` carries three or more deprecated identifiers across its registries (§2.1 authentication methods, §3.4 pagination patterns, §6.2 secret stores, §4.7 streaming patterns, §7.3 extension points). At that point the registry weight motivates a clearance pass that only a major-version bump permits.
- **Foundational constraints proven inadequate.** A constraint baked into `v1.x` — JSON wire format, HTTPS-only transport, the registered-or-`x-`-namespaced extension model, the `secret://` URI convention — has proven structurally insufficient for emerging auth, dispatch, or security patterns. Implementations report this through `[RFC v2]`-prefixed issues with a problem statement; the maintainer decides when the cluster of reports justifies opening the v2 RFC process.
- **Adoption demand.** A coherent corpus of feature requests from `v1.x` implementers describes capability the `v1.x` extension model can't accommodate without breaking changes.

The maintainer judges when conditions apply. The judgment is public — a v2 discussion begins as a `[RFC v2]`-prefixed issue with a problem statement, per [§7.6](./docs/07-versioning.md). The thresholds above are signals, not bright lines; the maintainer MAY open the v2 RFC process earlier if implementation surface dynamics warrant.

## Trademark policy

The names "UACP" and "Universal Agentic Connectivity Protocol", and any associated logos and conformance marks, are the trademarks of the maintainer (or are intended to become so once registration is complete). The intended trademark posture is permissive use for accurate description ("supports UACP", "implements UACP v1.0") and restricted use for branding that could imply endorsement or conformance certification.

A complete trademark policy remains to be published as a post-freeze deliverable. Contributors and implementers MAY refer to the protocol by its name and SHOULD NOT apply the name, logo, or any conformance mark to a product in a way that implies official endorsement, certified conformance, or affiliation with the maintainer.

## Amendments to this document

`GOVERNANCE.md` itself is normative for the project's governance posture but is not part of the technical specification. The maintainer MAY amend this document with the same low bar that applies to editorial fixes elsewhere; substantive changes (for example, transferring stewardship, opening the RFC process before `v2`) are announced through a pinned issue and held open for public comment for at least fourteen days before merge.
