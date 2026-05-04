# Contributing to UACP

UACP is developed in public. Outside contributions are welcome at every stage of the specification's life. This document describes how to participate.

## Code of conduct

All participation in this project — issues, pull requests, discussions, and any other interaction — is governed by the [Code of Conduct](./CODE_OF_CONDUCT.md). By participating, you agree to abide by its terms.

## Where to start

If you are new to UACP, read the documents in this order:

1. [`README.md`](./README.md) — what UACP is and why it exists.
2. [`docs/00-primer.md`](./docs/00-primer.md) — terminology, scope, document conventions.
3. [`docs/01-principles.md`](./docs/01-principles.md) — the design principles every later stage must satisfy.
4. [`SPEC.md`](./SPEC.md) — index of the full specification by stage.

Once you are oriented, the kinds of contribution this repository accepts are described below.

## Issues

GitHub Issues are the primary venue for design discussion. File an issue when you want to:

- Raise a question about the meaning or scope of an existing requirement.
- Propose a non-trivial change to a stage that is already complete.
- Surface a bug in a code-block example, schema fragment, or cross-reference.
- Request clarification on the relationship between UACP and another specification.

When filing an issue, include the affected stage document, the specific section or paragraph, and (where applicable) the artifact or scenario that motivates the question. Vague issues ("the spec is confusing") are less actionable than specific ones ("Stage 4 §3.2 does not say how `bad_input` differs from `forbidden` when a scope is missing").

## Pull requests

Pull requests are welcome for:

- **Editorial fixes.** Typos, grammar, broken links, formatting inconsistencies, missing cross-references. These are reviewed quickly and merged on a low bar.
- **Clarifying revisions.** Re-wording an existing requirement to remove ambiguity, without changing its meaning. Include in the PR description what you believe the requirement currently says, what you read it as saying, and why your wording is closer to the intent.
- **Editorial reorganization** within a stage document. The structure of each document is intentional but not sacred; reorganizations that improve readability without changing normative content are welcome.

Pull requests that change normative content — adding, removing, narrowing, or broadening a `MUST` / `SHOULD` / `MAY` — should be preceded by an issue that records the design discussion. Direct PRs against normative content may be closed with a request to open a discussion issue first.

### Commit message convention

Spec edits use the format `fix(spec): <stage> — <description>` for editorial fixes against an already-stable stage (for example, `fix(spec): Stage 4 — repair broken cross-reference to §3.2`), and `feat(spec): v1.x — <description>` for non-breaking additions per [§7.2](./docs/07-versioning.md). Prototype edits use `feat(prototype): <description>` or `fix(prototype): <description>`. Keep the subject line under 70 characters; explain the *why* in the body.

### What makes a good PR description

State the kind of change (editorial fix / clarifying revision / non-breaking addition); summarize the *why* in one or two sentences; for clarifying revisions, state the prior reading, the proposed reading, and the reason the wording is closer to intent; link any related issue. Reviewers should not have to read the diff to understand what the PR is for.

### Pull-request checklist

- The change is in scope for editorial fixes, clarifying revisions, or editorial reorganization (see above).
- The PR description states the kind of change.
- For clarifying revisions, the PR description states the prior reading, the proposed reading, and the reason.
- The change does not introduce a new normative requirement without a prior issue link.
- All cross-references and links still resolve.
- The commit message follows the convention above.

## Major changes (RFC process)

A major change to UACP — proposing a `v2` revision, broadening the scope of the protocol beyond HTTPS dispatch, or any change that would render `v1.x`-valid artifacts invalid — is governed by the public RFC process documented in [Stage 7 — Versioning](./docs/07-versioning.md) §7.6. File the proposal as a `[RFC v2]`-prefixed issue with a problem statement; the maintainer decides when the cluster of reports justifies opening the formal process. Non-breaking additions to `v1.x` (new auth methods, new pagination patterns, new secret stores, new transport backends) follow the lighter `[RFC v1.x]` path described in [`GOVERNANCE.md`](./GOVERNANCE.md).

## Reporting security concerns

UACP is a specification, not a running service, so most "security issues" in this repository are wording concerns: a requirement that, if interpreted literally, would lead an implementer to build something insecure. File those as ordinary issues.

If you have identified a security concern that you believe should be triaged privately before public disclosure, contact the maintainer using the address listed in the [Code of Conduct](./CODE_OF_CONDUCT.md).

## Licensing

By contributing to this repository, you agree that your contribution is licensed under the [Apache License 2.0](./LICENSE), the same license as the rest of the specification.

## Stewardship and decision-making

The current governance model — who is empowered to merge changes, how disputes are resolved, and how `v2` will transition to a more open process — is documented in [`GOVERNANCE.md`](./GOVERNANCE.md).
