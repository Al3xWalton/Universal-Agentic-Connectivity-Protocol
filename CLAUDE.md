# CLAUDE.md — Executor coordination for Claude Code sessions on UACP

> **Cross-agent note.** UACP is developed by two AI coding agents that may operate on this repository: Claude Code (this file) and Codex / OpenAI agents (`AGENTS.md` at the same path). The two files are kept tightly synchronized — when you make any architectural change documented here, mirror it to `AGENTS.md`, and vice versa. The shared coordination primitives are `docs/memory/CURRENT.md` (living state), `docs/memory/CHANGELOG.md` (append-only session log), and `docs/open-questions.md` (UACP-specific deferred decisions). Routing between Claude Code and Codex is governed by `ORCHESTRATION.md` at this repo's root.

## What UACP is

UACP — the **Universal Agentic Connectivity Protocol** — is a wire format and runtime contract for describing how an AI agent authenticates to and dispatches operations against an external service. UACP is a peer to the Model Context Protocol (MCP): MCP standardizes how an agent calls a tool; UACP standardizes how the tool, once called, reaches the external service it connects to. The repo is a spec under `docs/` plus a Python reference implementation under `prototype/python/`. The AVA-side architectural anchor is `docs/adr/ADR-036-uacp.md` in the AVA monorepo.

## Mandatory reading order

For any non-trivial task, read in this order before writing or editing:

1. `README.md` — what UACP is and the public framing.
2. `SPEC.md` — the per-stage status table.
3. `docs/00-primer.md` through `docs/07-versioning.md` — the eight design stages, in numerical order. Stages 0 (Primer) and 1 (Principles) are foundational; later stages reference them; reading out of order produces inconsistent context.
4. `docs/memory/CURRENT.md` — the **living state**. Always the last static doc you read, because its right-now state overrides any stale assumption in the spec docs above.
5. `docs/memory/CHANGELOG.md` — only when you need session archaeology.
6. `docs/open-questions.md` — UACP-specific deferred decisions and operator action items.
7. If your task touches the prototype: `prototype/python/README.md`, then the relevant module under `prototype/python/src/uacp_prototype/`.

Don't skim. The docs are short because they're written to be read.

## Invariants

These rules are not style preferences. Violating any of them breaks the spec's stability commitments or the prototype's correctness.

1. **Spec content is immutable post-stage-commit.** `docs/00-primer.md` through `docs/07-versioning.md` are committed and frozen at the spec layer. Edits are permitted only as `fix(spec): <stage> — <description>` commits when a session reveals an unambiguous bug — typo, broken cross-reference, internal inconsistency. Substantive design changes wait for Stage 9 freeze (then are governed by Principle 6 / §7.2). When in doubt, log to `docs/open-questions.md` instead of editing.
2. **No breaking spec changes outside Stage 9 freeze.** Within `v1.x`, every change MUST be backward-compatible per Principle 6 / §7.2. Removing or renaming registered identifiers, tightening constraints, changing the `.uacp` file structure, changing the canonical URI scheme for credential references, or changing dispatch / lifecycle semantics requires a `v2` major-version bump. The path to `v2` is the public RFC process specified in §7.6 and is intentionally not pre-baked.
3. **Conformance language is RFC 2119 / 8174 throughout.** MUST, MUST NOT, SHOULD, SHOULD NOT, MAY in all caps when used as conformance keywords. Per BCP 14, lowercase or mixed-case usage is descriptive prose, not normative. Don't sprinkle MUST into editorial paragraphs — it's a contract, not emphasis.
4. **Code lives only in `prototype/<lang>/`.** Today that's `prototype/python/`. Future prototypes (e.g., `prototype/typescript/`, `prototype/rust/`) are siblings. Spec docs MUST NOT contain implementation code; JSON examples for `.uacp` shapes are appropriate, Python or TypeScript or any other language is not.
5. **`.uacp` files MUST NOT contain plaintext credentials.** Every credential-shaped field takes the `_ref` suffix and resolves to a `secret://<store>/<id>[#<field>]` URI per §2.7. The §3.10 validator rejects artifacts that violate this; the prototype's `spec/schema.py` enforces it. This is a load-bearing security property — Stage 6's threat model assumes it.
6. **The §3.8 user-review requirement on inferred schemas is load-bearing.** Implementations MUST NOT persist an LLM-inferred `.uacp` operation without an explicit user-approval step. The provenance metadata (`source.type=="inferred"` with non-empty `model`, `description`, `reviewed_at`) is the durable audit hook that enforces this at validation time.

## Behavioral rules

- **Do what's asked. No fabrication.** When the spec is silent on a question, log to `docs/open-questions.md` rather than invent. When a session brief is ambiguous, ask. When a feature would require touching multiple stages of the spec, stop and confirm scope before editing.
- **Commit per-section, not bundled.** Spec-stage sessions land one commit per stage. Prototype sessions land one commit per logical unit (module + its tests). Memory sessions land one commit. Don't bundle unrelated work into a single commit.
- **Update memory at session end.** Before exiting, append a `## YYYY-MM-DD — <slug> — <area>` entry to `docs/memory/CHANGELOG.md` and refresh `docs/memory/CURRENT.md`'s top-of-file `Last updated` line. The convention is dense prose for `CURRENT.md` (one paragraph summarizing this session's deltas) and bullets for `CHANGELOG.md`.
- **Read before write.** Use the mandatory reading order above before any edit that touches more than a single file or a single test case. Sessions that skip this produce drift.

## File organization

```
UACP/
├── README.md                  # public-facing intro
├── SPEC.md                    # per-stage status table
├── GOVERNANCE.md              # v1.x stewardship + trademark posture
├── CONTRIBUTING.md            # how outside contributors participate
├── CODE_OF_CONDUCT.md         # Contributor Covenant 2.1
├── NOTICE                     # Apache 2.0 attribution
├── LICENSE                    # Apache 2.0
├── CLAUDE.md                  # this file
├── AGENTS.md                  # Codex equivalent of this file
├── ORCHESTRATION.md           # routing policy between Claude Code and Codex
├── docs/
│   ├── 00-primer.md ... 07-versioning.md   # eight design stages
│   ├── memory/
│   │   ├── CURRENT.md         # living state
│   │   └── CHANGELOG.md       # append-only session log
│   └── open-questions.md      # UACP-specific deferred decisions
└── prototype/
    └── python/                # Stage 8a reference implementation
        ├── pyproject.toml
        ├── README.md
        ├── src/uacp_prototype/  # spec / auth / dispatch / lifecycle / security / connections / cli
        ├── examples/google/   # gmail-send.uacp, google-calendar-list.uacp
        └── tests/             # unit/ + providers/ (integration tests)
```

Future prototype implementations live as siblings to `prototype/python/`. Future spec stages (8 conformance, 9 prototype freeze, 10 reference impl in AVA) extend the `docs/` numbering.

## Commit discipline

### Spec-stage commits

- Format: `feat(spec): UACP Stage <N> — <one-line summary>`. Example: `feat(spec): UACP Stage 4 — dispatch runtime`.
- One commit per stage. Stage 7's commit additionally bundles the `SPEC.md` + `docs/00-primer.md` index updates that flip the stage's row to **Complete**; subsequent stages follow the same pattern.
- Commit body: longer prose in RFC voice describing the stage's MUSTs / MUST NOTs / SHOULDs / MAYs and any deferred concerns. The reader is a future implementer, not the maintainer mid-session.

### Spec-fix commits

- Format: `fix(spec): <stage> — <description>`. Example: `fix(spec): Stage 4 — repair broken cross-reference to §3.2`.
- Reserved for typo / link / consistency fixes that don't change normative content. Substantive design changes wait for Stage 9.

### Prototype commits

- Format: `feat(prototype): <one-line summary>`. Example: `feat(prototype): implement OAuth 2.0 authorization-code + PKCE`.
- One commit per logical unit (module + its tests + the relevant index updates). Don't bundle unrelated modules.

### Memory + meta commits

- Format: `chore: <description>` or `memory: <description>`. Example: `chore: bootstrap docs/memory/ + open-questions.md`.
- Memory commits land at session end, after the work commits. One memory commit per session.

### Hard rules

- Never push without operator confirmation. The maintainer handles all pushes manually.
- Never bypass commit hooks (`--no-verify`) unless explicitly requested.
- Use HEREDOC for multi-line commit messages to preserve formatting.

## Routing reference

Claude Code is the default agent for UACP work because the work is design-first, ambiguous, and architecturally cross-cutting (the per-stage design sessions through Stage 7) or requires careful prototype implementation against a frozen spec (Stage 8 onwards). Codex is the right call for parallel bounded prototype tasks once Stage 8 is open and provider sessions are independent. See `ORCHESTRATION.md` for the full routing policy.

## Memory continuity

Two Claude Code instances may run concurrently against this repo (typical Alexander setup: one in the spec-doc terminal, one in `prototype/python/`). The memory system is what makes that work:

- Reads are free. Both instances read the same `docs/memory/CURRENT.md` and `docs/memory/CHANGELOG.md` files.
- Writes go through `CURRENT.md` only at session end. Mid-session writes to per-feature scratchpads (none currently exist; introduce `docs/memory/sessions/<slug>.md` if you need one) are fine.
- Conflicts surface in git. If two `chore: log session` commits hit `CURRENT.md` concurrently and produce a merge conflict, resolve manually — it means the two windows were working on different things and someone needs to reconcile.
- The human is the tiebreaker. If a Claude Code window sees a `CURRENT.md` that disagrees with what Alexander just said in chat, trust the human and update `CURRENT.md` immediately.
