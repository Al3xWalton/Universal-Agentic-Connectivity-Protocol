# Open questions

UACP's open-questions register. Each entry is a known design ambiguity that the spec defers, an operator action item, or a question that surfaced during a session and needs a decision before a particular stage closes. Numbered fresh within UACP's namespace starting at Q1.

The current `docs/memory/CURRENT.md` notes the phase each open question gates; this file is the authoritative source for the question text and resolution status.

| # | Area | Question | Status | Resolution |
|---|---|---|---|---|
| 1 | `$schema` canonical URL | Stage 3 §3.10 commits artifacts to a `$schema` URL but defers the canonical URL to Stage 9 freeze, with `https://uacp.spec/v1/schema.json` as the working placeholder. The host (a maintainer-owned domain vs. `raw.githubusercontent.com` URL vs. permanent-redirect indirection), the path shape (versioned segment vs. content-negotiation), and the per-artifact pinning rules are open. Artifacts pinning the placeholder today MUST be re-pinnable at freeze without semantic change. | Open — gates Stage 9 | — |
| 2 | Trademark + conformance-mark policy | `GOVERNANCE.md` records a placeholder for trademark policy on the names "UACP" and "Universal Agentic Connectivity Protocol" and any associated conformance marks. The full policy is published before `v1.0` freeze. Contributors and implementers MAY refer to the protocol by name; SHOULD NOT apply the name, logo, or any conformance mark to a product in a way that implies official endorsement, certified conformance, or affiliation with the maintainer until the policy lands. | Open — gates Stage 10 freeze | — |
| 3 | Public release of `github.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol` | The local scaffold at `/Users/alexanderwalton/Desktop/UACP/` is committed but not pushed to GitHub. Pushing the repository to GitHub is the operator's responsibility and is scheduled before any externally visible `v0.1` release. No protocol-owning GitHub organization exists; UACP is a personal-project repository under Alexander's GitHub account. | Open — operator action | — |
| 4 | UACP GitHub organization standup | Whether to stand up a `uacp-protocol` (or similar) GitHub organization to own the spec repo, separate from the maintainer's personal account. Originally an open question in ADR-036 (in the AVA monorepo); resolved by Alexander's decision on 2026-05-04 that UACP is a personal project on his GitHub account, not an organization-stewarded one. | **Resolved** (2026-05-04) | UACP is a personal-project repository under `github.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol`. No `uacp-protocol` GitHub org. The brand "UACP" is held separately from any GitHub-account ownership decision. The spec repo's `GOVERNANCE.md`, `README.md`, `CONTRIBUTING.md`, `NOTICE`, `CODE_OF_CONDUCT.md`, and the `docs/00-primer.md` + `docs/01-principles.md` `$schema`-URL bullets were rewritten to reflect this on 2026-05-04 (spec-repo commit `21c9dab chore: rename to al3xwalton/UACP, drop org references`; AVA-monorepo update `b59f5d3 docs(adr): update ADR-036 references for al3xwalton/UACP path`). |

## How to add an entry

When a UACP session surfaces an ambiguity that doesn't belong inside a spec doc (because resolving it requires design thought outside the current stage's scope), append a new row here with the next Q number, a short Area tag, the question text, Status `Open`, and an em-dash for Resolution. When a decision lands, update the row in place and flip the Status — `Resolved (YYYY-MM-DD)` is the convention, with the resolution text noting the spec-repo commit hash that landed the decision when applicable.

Sessions that resolve a prior open question SHOULD also note the resolution in the corresponding `docs/memory/CHANGELOG.md` entry so the work log and the questions register stay in sync.

## What does NOT belong here

- **Spec ambiguities that the spec itself defers to a later stage.** These belong in the deferring section's prose, not here. (Example: §2.5.2's intentionally-minimal HMAC substitution language is a known direction for future v1.x extension via §2.8 — it lives in §2.5.2's text, not in this register, because the path forward is documented and no decision is pending.)
- **Implementation-internal questions.** The prototype under `prototype/python/` may have its own `TODO` markers, design tradeoffs, or follow-up notes; those live in code or in commit messages, not here. This register is for protocol-level questions.
- **AVA-monorepo questions.** AVA has its own `docs/open-questions.md`. Mixed questions that affect both projects stay in AVA; this register is for UACP-specific questions only.
