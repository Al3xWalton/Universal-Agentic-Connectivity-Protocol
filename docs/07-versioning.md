# UACP Versioning and Extensibility

This document specifies the versioning scheme of UACP, the rules that distinguish breaking from non-breaking changes within a major version, the extension points by which the spec and implementations grow without breaking compatibility, the deprecation process by which registered identifiers are retired, the governance model under which `v1.x` evolves, and the transition path to a future `v2`. The conformance keywords ("MUST", "MUST NOT", "SHOULD", "SHOULD NOT", "MAY") in this document are interpreted per BCP 14 [[RFC2119](https://datatracker.ietf.org/doc/html/rfc2119)] [[RFC8174](https://datatracker.ietf.org/doc/html/rfc8174)] as established in [Stage 0 — Primer](./00-primer.md).

This document is consistent with the foundational principles in [Stage 1 — Principles](./01-principles.md) — particularly Principle 6 (wire-format stability) and Principle 12 (open governance) — and with the registries established in Stages 2 through 6.

## 7.0 Overview

A protocol specification is a contract. Implementations on every side of the contract — the producers of `.uacp` artifacts, the consumers that load and validate them, the dispatch runtimes that execute against them, the secret stores that resolve credential references, the audit pipelines that consume the events — depend on the contract's stability. Breaking the contract without notice breaks every implementation that trusted it; even non-breaking changes, if not announced and tracked, fragment the implementation landscape.

UACP's versioning scheme is designed to keep the contract trustworthy: `v1.x` is backward-compatible across its entire span, breaking changes require a `v2` major version that is itself the subject of a public RFC process, and the spec's evolution within `v1.x` happens in the open with each registered change traceable to the release that introduced it.

This stage closes the spec proper at the protocol level. Stage 8 (conformance test suite) and Stage 9 (prototype) operationalize the spec; Stage 10 (`v1.0` freeze) sets the moment after which the rules in this document begin to bind in earnest. Until `v1.0` freeze, `v0.x` is unstable and subject to revision; Stage 7's rules describe the stability target, not the current state.

### In scope

- The semver-based versioning scheme and the role of the `$schema` URL in artifact-version identification (§7.1).
- The classification of changes as breaking or non-breaking, with examples on each side (§7.2).
- The extension points across the spec and the namespacing convention for in-development extensions (§7.3).
- The deprecation process for registered identifiers slated for removal in `v2` (§7.4).
- The governance model for `v1.x` evolution: who decides, how changes propose, what cadence applies (§7.5).
- The path to `v2`: the conditions that motivate it, the RFC process placeholder, and the relationship between `v1.x` deprecations and `v2` (§7.6).
- Conformance summary for version handling (§7.7).

### Out of scope

- **The conformance test suite.** The mechanism by which an implementation demonstrates it conforms to a particular `v1.x` version is **Stage 8 (conformance)**.
- **The prototype implementation.** The reference `Conforming Implementation` that validates the spec is buildable is **Stage 9 (prototype)**.
- **The `v1.0` freeze itself.** The act of declaring `v1.0` complete and binding the backward-compatibility rules is **Stage 10 (freeze)**.
- **The `v2` design.** This document specifies the path to `v2`, not its content. `v2`'s wire format, registries, and conformance requirements are undefined until the RFC process produces them.

## 7.1 Versioning scheme

UACP adopts semantic versioning ([[SemVer 2.0.0](https://semver.org/spec/v2.0.0.html)]) over the wire format and the conformance vocabulary. Versions are of the form `MAJOR.MINOR.PATCH`:

- **MAJOR** — Incremented for breaking changes per §7.2. `v1` is the first major version; `v2` is the next.
- **MINOR** — Incremented for non-breaking additions per §7.2: registry growth, optional fields, new conformance MAY items. `v1.0` → `v1.1` → `v1.2` → ... is the typical progression.
- **PATCH** — Incremented for editorial fixes only: typos, broken cross-references, formatting consistency, ambiguity-removing rephrasing without semantic change. `v1.0` → `v1.0.1` → `v1.0.2` does not change what `Conforming Implementation`s do.

### `$schema` identification

A `.uacp` artifact identifies the version it was authored against through its top-level `$schema` field, introduced in §3.10. The canonical URL form is finalized in [Stage 9 — Prototype](./09-prototype.md); for the duration of `v0.x`, the placeholder `https://uacp.spec/v1/schema.json` is used, and artifacts pinning the placeholder MUST be re-pinnable at freeze without semantic change.

The `$schema` URL identifies the MAJOR.MINOR version. PATCH releases do not change the URL, because PATCH releases do not change the wire format. An artifact authored against `v1.2.0` and an artifact authored against `v1.2.3` reference the same `$schema` URL.

### Artifact version handling

A `Conforming Implementation` of `v1.x` MUST validate the `$schema` URL of every `.uacp` artifact it loads:

- **Same major version, same or earlier minor.** The implementation supports the artifact directly. An implementation supporting `v1.2` accepts `v1.0`, `v1.1`, and `v1.2` artifacts.
- **Same major version, later minor.** The implementation MAY accept the artifact under forward-compatibility rules (§7.2). Unknown fields introduced in the later minor are preserved on round-trip per §3.11; unknown registered identifiers (auth methods, pagination patterns, secret-store types) are declined silently per §2.8 / §3.4 / §6.2.
- **Different major version (`v2.x` to a `v1.x` implementation; `v1.x` to a `v2.x` implementation).** The implementation MUST reject the artifact. The rejection produces a `bad_input` failure with a clear message indicating the version mismatch.

The asymmetry in the second case — a later-minor artifact loaded by an older-minor implementation — is what makes minor-version evolution practical. If implementations rejected anything later than their build version, every spec release would force an immediate implementation update; instead, implementations gracefully degrade by rejecting the unknown surface and accepting the rest.

### Version self-declaration

An implementation MUST declare which `v1.x` version (or versions) it claims to support. The declaration is a property of the implementation, surfaced through whatever means the implementation makes available — a CLI flag, a runtime API, a static documentation page. The conformance test suite (Stage 8) consumes this declaration to know which test set to run.

## 7.2 What counts as breaking vs non-breaking

Within `v1.x`, every change is one of:

- **Editorial.** Typo fixes, formatting corrections, link repairs. Released as PATCH bumps. No effect on conformance.
- **Non-breaking.** Permitted under the wire-format-stability commitment (Principle 6). Released as MINOR bumps.
- **Breaking.** Forbidden within `v1.x`. Requires a `v2` MAJOR bump.

This section enumerates each category.

### Non-breaking changes (MAY be released within `v1.x`)

The following changes are non-breaking and MAY be incorporated into a `v1.x` minor release without invalidating any artifact valid against earlier `v1.x`:

- **Adding new registered identifiers to a registry.**
  - New auth methods to §2.1's authentication-method registry.
  - New pagination patterns to §3.4's pagination-pattern registry.
  - New secret-store types to §6.2's secret-store registry.
  - New streaming patterns to §4.7's streaming-pattern registry.
  - New canonical-error code variants to §4.6's mapping table (refinements that produce a more specific code from envelope context, when the spec is silent on the specific case).
- **Adding optional fields to existing schemas.**
  - New optional fields to the `Operation` shape (§3.1).
  - New optional fields to the `request` and `response` shapes (§3.2 and §3.3).
  - New optional fields to the `dispatch` block (§4.1).
  - New optional fields to existing authentication-method shapes (§2.2 through §2.6).

  An implementation that does not recognize the new field MUST preserve it on round-trip (§3.11) and MAY ignore it for dispatch behavior; an implementation that does recognize it gains the field's behavior.
- **Adding new conformance MAY items.**
  - New optional behaviors implementations MAY support without affecting their conformance level. Examples include the `Idempotency-Key` injection in §4.8, which entered the spec as MAY; analogous additions in future minor versions are non-breaking.
- **Relaxing constraints (with strong caution).**
  - Demoting a MUST to a SHOULD, or a SHOULD to a MAY, is technically non-breaking because a MUST-conforming implementation continues to satisfy a SHOULD or MAY.
  - This kind of relaxation is **discouraged** in `v1.x` because consumers may have relied on the stricter form. Implementations consume the spec as a contract; relaxation invalidates assumptions even when it doesn't invalidate artifacts.
  - When relaxation is necessary (a MUST has proven impossible to satisfy across the implementation surface; a SHOULD has proven irrelevant), the spec maintainer MUST document the relaxation prominently in the release notes and MUST consider whether the relaxation is the symptom of a deeper design issue that warrants a `v2` discussion.
- **Adding deprecation markers** (§7.4). A deprecated identifier remains functional for the rest of `v1.x`; the marker informs consumers that the identifier will be removed in `v2` but does not change `v1.x` behavior.

### Breaking changes (require `v2`)

The following changes are breaking and require a `v2` MAJOR bump:

- **Removing or renaming a registered identifier.**
  - Removing an auth method from §2.1.
  - Removing a pagination pattern from §3.4.
  - Removing a secret-store type from §6.2.
  - Removing a streaming pattern from §4.7.
  - Removing a canonical-error code from Principle 8's vocabulary.
  - Renaming any of the above (rename is remove-plus-add of a different identifier; old artifacts referencing the old identifier no longer resolve).
- **Tightening constraints.**
  - Promoting a SHOULD to a MUST. Implementations that satisfied the prior SHOULD by skipping it are now non-conforming.
  - Promoting a MAY to a SHOULD or MUST.
  - Adding a new MUST or MUST NOT requirement that did not previously exist.
- **Changing the `.uacp` file structure.**
  - Renaming a top-level key (`authentication`, `operations`, `dispatch`, `definitions`, `encrypted_secrets`).
  - Changing the type of an existing field (a string becoming an array, an object becoming a string).
  - Removing an existing field (with or without renaming) from any schema.
- **Changing canonical URI schemes.**
  - The `secret://` scheme is the v1 baseline. Renaming it (to `uacp-secret://`, for example) breaks every artifact referencing credentials.
  - The `$schema` URL changes between MAJOR versions but not within them; a structural change to how versions are identified is itself breaking.
- **Changing wire-format substrate.**
  - JSON is the v1 wire format. Switching to a different format (CBOR, MessagePack, YAML) is breaking.
- **Changing dispatch or lifecycle semantics.**
  - Modifying the retry policy defaults (§4.3) so a previously-retried failure is no longer retried.
  - Modifying the state-machine transitions in §5.1 so a previously-permitted transition is forbidden.
  - These are breaking even when the artifact-level wire format is unchanged, because the runtime behavior is part of the contract.
- **Removing a deprecated identifier.**
  - Once an identifier is deprecated in `v1.x` per §7.4, it remains functional for the rest of `v1.x`. Removing it requires `v2`.

### Editorial changes (MAY be released as PATCH)

- Typo fixes, grammar corrections.
- Cross-reference repairs, broken-link fixes.
- Formatting consistency, spec-document structure improvements.
- Rephrasing for clarity that does not change meaning.

Editorial changes do not require a MINOR bump and MUST NOT introduce normative content. The `CONTRIBUTING.md` document at the repo root specifies how editorial fixes are accepted.

## 7.3 Extension points

Several places in the spec call out registries and protocols that implementations and the spec itself can extend without breaking compatibility. The extension points:

| Registry / point | Section | What extends |
|---|---|---|
| Authentication methods | §2.1 | New entries via §2.8's registration mechanism. |
| Pagination patterns | §3.4 | New entries (keyset, page-number, timestamp-windowed, etc.). |
| Secret stores | §6.2 | New entries (cloud-specific managers, K8s secrets, etc.). |
| Streaming patterns | §4.7 | New entries beyond chunked transfer / SSE / NDJSON / WebSocket. |
| HTTP methods | §3.2 | New methods beyond GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS, when long-tail providers warrant. |
| HMAC substitution language | §2.5.2 | New `${...}` substitutions beyond the v1.0 minimal set. |
| Request body media types | §3.2 | New `media_type` values beyond `application/json` defaults. |
| Canonical-error code mapping | §4.6 | Refinements that produce more specific codes from envelope context. |

Each registered extension follows §2.8's process: an `[RFC]`-prefixed issue, maintainer review in public, landing as an editorial revision to the relevant section plus the conformance summary plus `SPEC.md`. The release in which the extension lands is a MINOR bump.

### In-development namespacing

When an implementation experiments with an unregistered identifier — a new auth method specific to a particular vendor, a new secret-store type for a niche cloud, a new streaming pattern for an emerging transport — the experiment MUST use an `x-` prefix to namespace the identifier and avoid clashing with future registered names.

The conventions:

- Auth methods: `x-vendor-method` rather than `vendor-method`. Example: `x-acme-bank-auth` for an experimental bank-specific auth method that hasn't been promoted to the registry.
- Secret stores: `x-vendor-store` rather than `vendor-store`. Example: `secret://x-internal-vault/path` for an experimental secret-store integration.
- Streaming patterns: `x-vendor-stream` rather than `vendor-stream`.
- Pagination patterns: `x-vendor-pagination`.

A `Conforming Implementation` MAY support `x-`-prefixed identifiers; another implementation that doesn't recognize the identifier declines the artifact silently per §2.8. The `x-` namespace is the safe space for experimentation that doesn't risk collision when the spec's registries grow.

When an `x-`-namespaced identifier is mature enough for registration, the registration process produces a name without the `x-` prefix; the original `x-` identifier MAY remain as an alias for one minor cycle to ease migration, but the spec's registered name is the canonical from registration onward.

### What is not an extension point

The following are not extension points and are not subject to the `x-` namespacing convention because they don't extend the spec at the registry level:

- **Adding fields to one's own implementation's logs, telemetry, or internal data structures.** These are private to the implementation and do not interact with the wire format.
- **Adding operations to a `.uacp` artifact.** Operations are content within the artifact, not extensions to the spec; their `id`s follow §3.1's naming rules but do not need `x-` prefixes.
- **Adding fields to one's own audit-event detail payloads.** §6.6's per-event field set is the floor; implementations adding additional fields do not register them.

## 7.4 Deprecation process

When a registered identifier is to be removed in `v2`, `v1.x` first marks it deprecated. The deprecation announcement, the continued-functioning period, and the removal cadence:

### Deprecation announcement

A deprecation lands in a `v1.x` minor release, alongside whatever motivated it. The release notes explicitly call out the deprecated identifier; the spec document is updated to mark the identifier deprecated; the `Deprecated` subsection of the relevant section lists the identifier with the version it was deprecated in and the version it will be removed in.

The format of the spec entry:

> **`<identifier>`** — *Deprecated in `v1.<n>`; will be removed in `v2`.* Reason: <one or two sentences>. Replacement: <pointer to the replacement identifier or a description of how to migrate>.

### Continued functioning

A deprecated identifier MUST continue to function for the remainder of `v1.x`. `Conforming Implementation`s of `v1.<n+1>` MUST recognize and execute deprecated identifiers from `v1.<n>` exactly as they did in `v1.<n>`. The deprecation marker does not change the runtime behavior; it informs consumers that future migration is required.

### Deprecation surface

A `Conforming Implementation` MAY surface deprecation warnings to the user when a `.uacp` artifact uses a deprecated identifier. The warning surface is implementation-defined — a CLI message at validation, a UI banner during artifact authoring, a structured-log event at dispatch time. The warning is informational; it does not block.

### Removal in `v2`

When `v2` lands, deprecated `v1.x` identifiers are eligible for removal. The `v2` RFC process (§7.6) decides which deprecated identifiers are actually removed and which are retained for additional reasons (perhaps the deprecation was reconsidered; perhaps the migration path is not yet ready). Removal is a property of the `v2` design, not an automatic consequence of deprecation.

### Adding new entries to the `Deprecated` list

The `Deprecated` subsection of each spec section may be empty in `v1.0` and populate over time. The first deprecation is a notable event in the spec's lifecycle and MUST be announced in the release notes. Subsequent deprecations follow the same pattern.

## 7.5 `v1.x` evolution governance

UACP `v1.x` evolves under the stewardship of the protocol's maintainer per `GOVERNANCE.md`. The mechanics:

### Proposal

Changes are proposed via GitHub issues in the spec repository. The issue prefix conventions per `CONTRIBUTING.md`:

- `[RFC]` — Major-change proposal: a new registered identifier, a new section, a deprecation, or any other change that affects normative content.
- (No prefix) — Editorial proposal, question, clarification request, bug report against a code-block example or a cross-reference.

Issues `[RFC]`-prefixed are the candidates for the registration mechanism per §2.8 and the deprecation process per §7.4. Issues without the prefix are editorial or discussion.

### Decision

The maintainer is the decider for `v1.x` per `GOVERNANCE.md`. The maintainer reviews proposals in public, records the decision (accept, decline, defer to `v2`), and merges the corresponding changes when accepted. Outside contributors comment per `CONTRIBUTING.md`; their contributions are weighed but the decision is the maintainer's.

The single-maintainer model is appropriate for `v1.x` because:

- The protocol is small enough that a single maintainer can hold the full spec in mind.
- Reaching consensus through a public RFC process with no implementer base would slow evolution without improving quality.
- The maintainer's accountability is public — every decision is in the issue history.

The `v2` transition (§7.6) introduces a more open governance model.

### Release cadence

`v1.x` releases are not on a fixed schedule. A release lands when:

- An accepted issue is merged into the spec.
- The release notes are written.
- The `$schema` URL is bumped (for MINOR or MAJOR releases) or unchanged (for PATCH releases).
- The release is tagged in the spec repository.

The expected cadence for `v1.x` early minor releases is "as needed" — likely several per year as the protocol gains its first implementation experience, slowing as the registries stabilize. PATCH releases may be more frequent during the early-implementation phase as documentation issues surface.

### Release content

A MINOR release MAY include:

- One or more newly registered identifiers per §2.8.
- One or more new optional fields on existing schemas.
- One or more new conformance MAY items.
- One or more new deprecation markers per §7.4.
- An accompanying `CHANGELOG.md` entry summarizing the changes.
- Updates to the spec document(s) reflecting the changes, with the `$schema` URL bumped.

A PATCH release includes:

- Editorial fixes only.
- An accompanying `CHANGELOG.md` entry.
- No `$schema` URL change.

A `Conforming Implementation` of `v1.<n>` need not update its declared support to consume `v1.<n>.<m>` artifacts; the artifacts are functionally identical to `v1.<n>` artifacts at the wire-format level. A MINOR release may motivate an implementation update if the implementation wants to gain the new capability, but the existing support claim remains valid against artifacts from earlier minor releases per §7.1's same-major-earlier-or-same-minor rule.

### `v1.0` freeze

`v1.0` is reached after [Stage 8 — Conformance](./08-conformance.md) and [Stage 9 — Prototype](./09-prototype.md) validate that the spec is implementable, and after [Stage 10 — Freeze](./10-freeze.md) declares the spec frozen. The cycle:

1. Stages 0 through 7 are the design stages — this document is the last of them.
2. Stage 8 specifies the conformance test suite that demonstrates `Conforming Implementation` claims are testable.
3. Stage 9 builds a reference implementation against the design.
4. Stage 10 freezes the spec at `v1.0`. From that moment, the backward-compatibility rules in §7.2 begin to bind.

Until `v1.0` freeze, `v0.x` is unstable. The spec documents are written in normative voice because they specify what `v1.x` will be, not what `v0.x` is. The current state is a *target*, not a *contract*; implementations building against `v0.x` accept that the spec may change underneath them.

## 7.6 Path to `v2`

`v2` is undefined. This document does not specify what `v2` will look like; specifying it now would defeat the purpose of `v1.x`'s stability commitment, since `v1.x` consumers should not need to track `v2` design discussions to plan their integrations.

What this document specifies is the *path* — the conditions under which `v2` becomes worth pursuing, and the governance shape of the transition.

### When `v2` becomes worth pursuing

`v2` becomes worth pursuing when one of the following is true:

- **Accumulated deprecations.** `v1.x` has accumulated enough deprecated identifiers (auth methods, pagination patterns, secret stores) that `v1.x`'s registry feels weighted by historical compatibility. `v2` is the moment when the deprecated identifiers are actually removed.
- **Foundational constraints proven inadequate.** A constraint baked into `v1.x` — JSON wire format, HTTPS-only transport, single major version per artifact — has proven insufficient for the use cases the spec needs to address. The constraint cannot be relaxed within `v1.x` because the relaxation would either be breaking (forbidden) or harmful (the relaxation discussion in §7.2 already covers when relaxation is inappropriate). `v2` is the venue for revisiting.
- **Adoption surface demands new capabilities.** The implementations and `Provider`s using UACP `v1.x` have built up a corpus of feature requests that don't fit `v1.x`'s extension model. `v2` is the venue for addressing the cluster.

The single maintainer judges when the conditions above apply. The judgment is public — a `v2` discussion begins as a `[RFC v2]`-prefixed issue with a problem statement.

### `v2` RFC process

The `v2` RFC process is intentionally not specified here. `v1.x` evolves under single-maintainer stewardship; `v2` and beyond evolve under a public RFC process per Principle 12 and `GOVERNANCE.md`. The shape of that process — who participates, what voting or consensus mechanism applies, how proposals are sequenced — is a property of the implementation surface at the time of the transition.

What this document commits to:

- The `v2` design will not begin in private. The first `v2` discussion is a public issue.
- The `v2` design will solicit input from `v1.x` implementers. Migration cost is a first-class concern.
- The `v1.x` to `v2` migration path will be documented before `v2` releases, including which deprecated identifiers are removed, which are retained, and the equivalent in `v2` for each removed identifier.

### `v1.x` to `v2` coexistence

When `v2` releases, `v1.x` does not immediately become unsupported. The spec's posture is that `v1.x` continues to receive PATCH releases for editorial fixes for some period after `v2` lands; the period is undefined and depends on the implementation surface. New MINOR releases of `v1.x` after `v2` are unlikely (innovation moves to `v2`) but not categorically ruled out — security-critical fixes might motivate them.

Implementations MAY support `v1.x` and `v2` simultaneously, with the `$schema` URL determining which version's rules apply per artifact. Implementations MAY support only `v1.x` indefinitely, accepting that they will not consume `v2` artifacts but continuing to function against `v1.x` artifacts.

The transition is gradual by design.

## 7.7 Conformance summary

This section summarizes the versioning conformance requirements for a `Conforming Implementation` of `v1.x`. The summary is parallel to §2.9, §3.11, §4.9, §5.7, and §6.9.

### MUST requirements

A `Conforming Implementation` MUST:

- Validate the `$schema` URL of every `.uacp` artifact it loads, per §7.1.
- Accept artifacts whose `$schema` URL identifies a `v1.x` minor version equal to or earlier than the implementation's declared support.
- Reject artifacts whose `$schema` URL identifies a `v2.x` (or later) major version, with a `bad_input` failure indicating the version mismatch.
- Declare which `v1.x` version (or versions) it claims to support, surfaced through some implementation-defined means.
- Support every MUST requirement of every section in the version it claims to support.
- Recognize and execute deprecated identifiers in `v1.x` exactly as they functioned before deprecation.

### MUST NOT requirements

A `Conforming Implementation` MUST NOT:

- Silently substitute one major version's behavior for another (treat a `v2.x` artifact as if it were `v1.x`, or vice versa).
- Refuse a `v1.x` artifact whose minor version is earlier than the implementation's claimed version (an implementation supporting `v1.2` MUST accept `v1.0` and `v1.1` artifacts).
- Treat editorial-PATCH-bumped artifacts (`v1.2.0` vs `v1.2.3`) as different at the wire-format level. The `$schema` URL is the same; the artifacts are equivalent.
- Allow a removed identifier from a deprecation cycle to be recognized in a later `v1.x` release (once `v2` removes a deprecated identifier, `v1.x` MUST NOT add it back unless the deprecation itself is reverted in a `v1.x` minor release before `v2` lands).

### SHOULD requirements

A `Conforming Implementation` SHOULD:

- Accept artifacts whose `$schema` URL identifies a later `v1.x` minor version than the implementation's claimed version, under forward-compatibility rules. Unknown fields are preserved; unknown registered identifiers are declined silently per §2.8.
- Surface deprecation warnings to the user when a `.uacp` artifact uses a deprecated identifier, per §7.4.
- Document its supported `v1.x` version range alongside the implementation's other public-facing documentation.
- Update its declared support promptly when adopting newly registered identifiers from a MINOR release.

### MAY requirements

A `Conforming Implementation` MAY:

- Support multiple `v1.x` minor versions simultaneously, with the `$schema` URL determining which version's rules apply per artifact.
- Support both `v1.x` and `v2.x` simultaneously after `v2` lands, with the `$schema` URL routing.
- Use the `x-` namespace for in-development extensions, per §7.3.
- Decline an `x-`-namespaced identifier silently per §2.8 when the implementation does not support that experimental extension.
- Implement a strict mode that rejects forward-compatible artifacts (artifacts whose `$schema` is a later minor version than the implementation supports) instead of accepting under forward-compat rules. The strict mode is a security posture some deployments prefer; the lenient default is the spec's recommendation.

### Cumulative conformance

The cumulative effect of the MUSTs and MUST NOTs is that every `Conforming Implementation` of `v1.x` participates in the same versioning contract: artifacts authored against any earlier-or-equal `v1.x` minor are loadable, the spec's registries grow without breaking existing consumers, deprecated identifiers are honored through their announced lifetime, and the transition to `v2` is a deliberate choice rather than an accident. The versioning layer is what turns the spec from a snapshot into a contract — implementations and artifacts can rely on the spec's stability commitments because the rules in this section bind every release that claims `v1.x` conformance.
