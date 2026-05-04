# UACP Governance

This document records the governance posture of UACP at version `v0.1`. The model is intentionally simple while the specification is small and has a single steward; the second section describes the path to a more open process for `v2` and beyond.

## v1.x stewardship

UACP `v1.x` evolves under the stewardship of **Autonomous Virtual Assistants** (the organization that initiated the protocol). Day-to-day decision-making — issue triage, pull-request review, version tagging, and editorial direction — sits with the maintainers listed in the repository's GitHub team.

The steward's commitments while UACP is in `v1.x`:

- **Public process.** All design discussion happens in public, in this repository's issues and pull requests. Off-repository discussion that affects normative content is summarized back into a public issue before any change is merged.
- **Backward compatibility.** Every `v1.x` revision MUST be backward-compatible with `v1.0`, per [Stage 1 — Principles](./docs/01-principles.md) §6 ("Wire-format stability"). The steward MAY decline a contribution that would require a breaking change; such contributions are queued against the future `v2` RFC process.
- **Outside contribution.** Editorial fixes and clarifying revisions from outside contributors are reviewed and merged on a low bar. Normative changes follow the discussion process described in [`CONTRIBUTING.md`](./CONTRIBUTING.md).
- **No private fork.** The steward MUST NOT maintain a divergent private specification. The published `v1.x` documents are the canonical specification at every point in time.

## v2 and beyond

Once `v1.0` is frozen, evolution past `v1.x` will follow a public RFC process governed by [Stage 7 — Versioning](./docs/07-versioning.md) (forthcoming). The Stage 7 document will specify, at minimum:

- How an RFC is proposed, who can propose one, and the required structure.
- How the RFC is reviewed: the review window, the venue, and the standard for acceptance.
- How disputes are resolved when reasonable parties disagree.
- How the steward's role changes — if at all — when the RFC process is active.

Until Stage 7 is published, major-change proposals are filed as issues with the `[RFC]` prefix and held for promotion into the formal process.

## Trademark policy

The names "UACP" and "Universal Agentic Connectivity Protocol", and any associated logos and conformance marks, are the trademarks of the steward (or are intended to become so once registration is complete). The intended trademark posture is permissive use for accurate description ("supports UACP", "implements UACP v1.0") and restricted use for branding that could imply endorsement or conformance certification.

A complete trademark policy will be published before `v1.0` is frozen. Until then, contributors and implementers MAY refer to the protocol by its name and SHOULD NOT apply the name, logo, or any conformance mark to a product in a way that implies official endorsement, certified conformance, or affiliation with the steward.

## Amendments to this document

`GOVERNANCE.md` itself is normative for the project's governance posture but is not part of the technical specification. The steward MAY amend this document with the same low bar that applies to editorial fixes elsewhere; substantive changes (for example, transferring stewardship, altering the RFC process before Stage 7 is published) are announced through a pinned issue and held open for public comment for at least fourteen days before merge.
