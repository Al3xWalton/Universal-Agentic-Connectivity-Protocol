# CHANGELOG — session-by-session work log

Append-only log of UACP design and prototype sessions. Each session writes one entry at the bottom. Format:

```
## YYYY-MM-DD — <short slug> — <area>

- What changed (1-5 bullets, code-ish, specific)
- What's different in CURRENT.md as a result
- Any decisions logged to docs/adr/ in the AVA monorepo (since UACP itself
  has no ADR directory; see ADR-036-uacp.md in the AVA monorepo for the
  AVA-side architectural anchor)
```

The entries below were migrated from the AVA monorepo's `docs/memory/CHANGELOG.md` on 2026-05-04 when UACP became self-documenting as a standalone public personal-project repo. Dates, verdicts, and commit hashes are preserved verbatim from the original entries — they are historical record. References to "AVA monorepo commit plan" inside the entries reflect the per-session commits that were filed in AVA at the time; those references are accurate as-of-the-session and are left intact.

---

## 2026-05-04 — UACP Stage 0 + 1 foundational — protocol-design / docs

**Foundation laid for AVA's peer-protocol-to-MCP for the agent ↔ external-service surface.** Stage 0 (ADR + Primer) and Stage 1 (Principles) only — subsequent stages (auth, schema, dispatch, lifecycle, security, versioning, conformance, prototype, freeze) are separate sessions.

**Locked decisions baked into all outputs verbatim** (not relitigated): JSON wire format validated against versioned `$schema`; MIME `application/uacp+json`; file extension `.uacp` (single dot); `v1.x` semver with backward-compatibility within the major; RFC 2119/8174 conformance language; AI-native authoring as the primary surface (LLM generates, human reviews); public-from-day-one with spec repo at `uacp-protocol/specification` (org standup deferred to operator); reference implementation lives at `backend/services/connections-broker/` (a future Ring 3 service); peer (not competitor) to MCP — UACP-defined connections SHOULD be exposable through MCP servers as tools.

**ADR-036 (`docs/adr/ADR-036-uacp.md`)** — Status Accepted, dated 2026-05-04. Records: AVA is building UACP as a peer protocol to MCP; rationale (sovereignty per ADR-035, scale beyond curated catalogs, AI-native authoring as differentiator); explicit non-goals (UACP is not a tool-call protocol, not a workflow engine, no transport beyond HTTPS in v1.0, no central authority); relationship to existing `connectivity-mcp` code (the curated `IntegrationProvider` path per ADR-035 is the v1 transition; UACP via `connections-broker` is the long-tail destination; the two paths coexist post-ship); 10-stage implementation sequencing; 6 open questions deferred into later stages rather than `docs/open-questions.md`.

**Spec repo scaffold** at `/Users/alexanderwalton/Desktop/uacp-protocol-specification/` — single git init + single commit `0734b46 chore: initial scaffold + UACP Stage 0/1 documents`. Contents:

- `README.md` — public-facing intro, MCP-comparison framing, status (early dev, v0.1 → v1.0).
- `LICENSE` — canonical Apache 2.0 (fetched from apache.org).
- `NOTICE` — `Copyright (c) 2026 Autonomous Virtual Assistants` (the standard Apache 2.0 attribution venue).
- `CODE_OF_CONDUCT.md` — canonical Contributor Covenant 2.1 (fetched from contributor-covenant.org); enforcement contact `conduct@uacp-protocol.org`.
- `CONTRIBUTING.md` — issues for design discussion, PRs for editorial fixes, `[RFC]` prefix for major-change proposals.
- `GOVERNANCE.md` — v1.x stewardship under AVA, v2 via the forthcoming Stage 7 RFC process, trademark policy placeholder.
- `SPEC.md` — 10-stage status table; Stages 0+1 Complete, 2-9 Pending, 10 = reference implementation in AVA repo.
- `.gitignore` — minimal markdown-spec-repo ignore set.
- `docs/00-primer.md` — Stage 0 Primer (RFC voice; Abstract / Status / Terminology / Scope / Comparison to prior art / Document conventions / Spec structure).
- `docs/01-principles.md` — Stage 1 Principles (RFC voice; 12 numbered principles, each stated then justified in 2-4 sentences).

**4 AVA-monorepo cross-references landed**:

- `CLAUDE.md` §3 — new invariant 12 ("Connections to external services MUST go through UACP. Direct HTTP calls to provider APIs are not allowed in tool / route / handler code outside `connections-broker/`. See ADR-036.").
- `AGENTS.md` §3 — same invariant 12 mirrored.
- `ORCHESTRATION.md` "Integration-work alignment" — one-line note that UACP is a multi-stage public-spec workstream tracked in ADR-036 and that Claude Code is the default agent for those sessions.
- `ARCHITECTURE.md` §6.3 — sentence after the existing ADR-035 sovereignty-divergence sentence noting the UACP destination + `connections-broker` future Ring 3 service.

**Hard rules honored**: Stage 0 + 1 only (no auth, no schema, no dispatch, no security-model details — Stages 2-6 are separate sessions); design-only (no code); locked decisions used verbatim; RFC voice throughout the public docs; references properly cited (RFC 2119, RFC 8174, RFC 6749, RFC 7636, RFC 8628, RFC 6839, RFC 8259, modelcontextprotocol.io).

**Open questions** deferred into ADR-036 rather than `docs/open-questions.md`: `$schema` URL form (Stage 3), authentication-method registration mechanism (Stage 2), failure-mode vocabulary refinement rules (Stage 4), per-provider OAuth scope strategy (Stage 2), trademark/conformance-mark policy (no later than Stage 10), `uacp-protocol` GitHub org + `uacp-protocol.org` domain standup (operator responsibility).

**AVA monorepo commit plan**: 3 commits — (1) `docs(adr): ADR-036 — Universal Agentic Connectivity Protocol`; (2) `docs: cross-reference UACP from CLAUDE/AGENTS/ORCHESTRATION/ARCHITECTURE`; (3) `memory: UACP Stage 0 + 1 session`. Spec repo: single commit already landed (`0734b46`). Operator pushes both manually per the brief.

**Other working-tree changes that are NOT this session's** (deliberately not touched): the connectivity-mcp `connections.entity_id` migration patches from session 2A; `docs/design/desktop-relay-multi-pod.md` from session 2C; the api-gateway rate-limiting work — all theirs to commit.

---

## 2026-05-04 — UACP rename cleanup — protocol-design / docs

Mechanical follow-up to the Stage 0 + 1 foundational session earlier today. Alexander decided UACP is a personal project at `github.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol`, not an organization-stewarded one — no `uacp-protocol` GitHub org, no `uacp-protocol.org` domain.

**Spec repo** moved on disk: `/Users/alexanderwalton/Desktop/uacp-protocol-specification/` → `/Users/alexanderwalton/Desktop/UACP/` via `mv` (history preserved; the prior commit `0734b46` is intact). Single new commit `be13aae chore: rename to Al3xWalton/Universal-Agentic-Connectivity-Protocol, drop org references` touched 7 files:

- `GOVERNANCE.md` — stewardship section rewritten to 3-4 sentences anchored on the brief-specified text ("UACP v1.x evolves under the stewardship of its maintainer. v2 and beyond will follow a public RFC process if and when the protocol's user base justifies one"). The dedicated "v2 and beyond" subsection is folded into stewardship. Trademark + Amendments unchanged in substance, with "the maintainer" wording where the docs previously said "the steward".
- `README.md` — Governance section reframes around personal-project shape; substantive UACP content (what / why / MCP comparison) untouched.
- `CONTRIBUTING.md` — singularized "the maintainers" → "the maintainer".
- `NOTICE` — copyright holder is `Alexander Walton` (single-maintainer attribution) rather than `Autonomous Virtual Assistants`.
- `CODE_OF_CONDUCT.md` — enforcement contact `conduct@uacp-protocol.org` → `TBD (placeholder; the maintainer supplies a contact address before any public announcement)`.
- `docs/00-primer.md` — wire-format bullet rephrased: the speculative `https://uacp-protocol.org/schema/v1.x/uacp.schema.json` URL is dropped. Replacement reads "Each artifact references its schema version through a `$schema` URL whose specific form is finalized in Stage 3" (no design-content change; placement identical).
- `docs/01-principles.md` Principle 12 — one-word polish: "the protocol's authoring organization" → "the protocol's maintainer". The open-governance principle's design content is unchanged; this is path-and-stewardship cleanup at the metadata level, not redesign.

**Substance untouched** in the spec docs: protocol name (UACP), file extension (`.uacp`), MIME type (`application/uacp+json`), wire format, terminology (Connection / Provider / Authentication Method / Dispatch / Schema / Authoring / Wire Format / Conformance), the twelve principles' design content, and the prior-art comparison.

**AVA monorepo** edit of `docs/adr/ADR-036-uacp.md` only — 7 verbatim references replaced:

- `uacp-protocol/specification` → `github.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol` (×3, in Context / Decision / Implementation Sequencing).
- `https://uacp-protocol.org/schema/v1.x/uacp.schema.json` → rephrased (×2, in Decision §Wire format and Open Questions §`$schema` URL form).
- `**Spec repo:** uacp-protocol/specification (operator stands up the GitHub organization). Local scaffold at /Users/alexanderwalton/Desktop/uacp-protocol-specification/...` → `**Spec repo:** github.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol (operator pushes from the local scaffold). Local scaffold at /Users/alexanderwalton/Desktop/UACP/; the initial-scaffold commit is 0734b46, and the cleanup commit is be13aae`.
- The `**uacp-protocol GitHub organization and uacp-protocol.org domain.**` open question is rewritten as `**Public release of github.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol.**` reflecting the no-org reality and the operator-push-from-local-scaffold sequencing.

The substance of the ADR (Decision, rationale, sequencing) is unchanged.

**`CLAUDE.md` / `AGENTS.md` / `ORCHESTRATION.md` / `ARCHITECTURE.md` had zero `uacp-protocol` references** when the prior session's cross-references landed (they referenced UACP-the-protocol but never UACP-the-org), so no edits and no second commit — the brief's "if needed" clause skips that commit.

**Grep verification**: zero `uacp-protocol` matches in either repo's live content (spec repo + AVA's CLAUDE/AGENTS/ORCHESTRATION/ARCHITECTURE/ADR-036). The 5 remaining `uacp-protocol` hits in `docs/memory/CURRENT.md` (prior-session demoted block) and this CHANGELOG (the prior session's entry) are historical context per the brief's "preserve original framing" exception.

**AVA monorepo commit plan**: 2 commits — (1) `docs(adr): update ADR-036 references for Al3xWalton/Universal-Agentic-Connectivity-Protocol path`; (2) `memory: UACP rename cleanup pass`. Spec repo: single commit already landed (`be13aae`). Operator pushes manually per the brief.

---

## 2026-05-04 — UACP Stage 2 — protocol-design / docs

Stage 2 of the UACP arc — the authentication subsystem. Spec-repo deliverable is a single new file, `docs/02-authentication.md`, plus status-table flips in `SPEC.md` and `docs/00-primer.md`. Single spec-repo commit `6e846ea feat(spec): UACP Stage 2 — authentication subsystem` on top of `21c9dab` (the prior Al3xWalton-rename commit). Design-only; no code.

**Nine `Authentication Method`s registered in v1.0** (§2.1) with stable string identifiers selected via the artifact's `authentication.method` field:

- `oauth2_authorization_code` — RFC 6749 §4.1 with PKCE per RFC 7636 + RFC 8252. PKCE MUST be supported by implementations and MUST be used for any authorization-code flow originating from a non-confidential client. `code_challenge_method` defaults to `S256`; `plain` permitted only when the authorization server documents it cannot accept `S256`. Wire shape: `authorization_endpoint`, `token_endpoint`, `client_id`, `client_secret_ref`, `scopes` (array), `redirect_uri`, `code_challenge_method`. Conformance: **MUST**.
- `oauth2_client_credentials` — RFC 6749 §4.4 service-to-service. Wire shape: `token_endpoint`, `client_id`, `client_secret_ref`, optional `scope` (single space-delimited string). No refresh tokens issued per RFC 6749 §4.4.3. Conformance: **MUST**.
- `oauth2_device_code` — RFC 8628 input-constrained devices. Wire shape: `device_authorization_endpoint`, `token_endpoint`, `client_id`, optional `scope`. Polling-interval semantics (the `interval` field, `slow_down` token-error response) are honored at *runtime* per the device-authorization-response payload, not encoded in the artifact. Conformance: **SHOULD**.
- `oauth1a` — RFC 5849 three-legged. Wire shape: `request_token_url`, `authorize_url`, `access_token_url`, `consumer_key`, `consumer_secret_ref`, `signature_method`, optional `realm`. **HMAC-SHA1 MUST be supported**, **RSA-SHA1 SHOULD be supported**, **PLAINTEXT MUST NOT be supported** (rejected at validation with `bad_input`). Retained as a legacy method for parity with services that still require it (Twitter API v1.1, some financial APIs). Conformance: **SHOULD**.
- `api_key_header` — header-based static key. Wire shape: `header_name`, optional `header_prefix` (e.g. `Bearer ` for `Authorization: Bearer <key>`), `key_ref`. Conformance: **MUST**.
- `api_key_query` — query-parameter-based static key, with explicit disrecommendation: query-parameter API keys leak into server logs / browser history / proxy logs. Wire shape: `param_name`, `key_ref`. Implementations MAY emit a runtime warning when the provider also accepts `api_key_header`. Conformance: **MUST**.
- `aws_sigv4` — AWS Signature Version 4 plus the few AWS-API-compatible providers that adopted the same scheme. Wire shape: `access_key_ref`, `secret_key_ref`, `service`, `region`, optional `session_token_ref`. Canonical-request construction defers to AWS's published spec which conforming implementations MUST follow exactly; the spec doc explicitly does not duplicate the algorithm. Conformance: **SHOULD**.
- `hmac_signature` — generic HMAC over a canonical request representation. Covers Stripe webhooks, Shopify webhooks, GitHub webhooks (after prefix-strip), and the long tail of custom HMAC schemes. Wire shape: `algorithm` ∈ {`HMAC-SHA256`, `HMAC-SHA512`}, `key_ref`, `signed_payload_template`, `header_name`. **Substitution language is intentionally minimal** — `${timestamp}` (Unix seconds), `${method}`, `${path}`, `${query}`, `${headers.<name>}`, `${body}` are the only legal substitutions in v1.0. Providers needing pre-encoded timestamps (ms, ISO 8601), base64-encoded signatures, or `sha256=`-prefixed hex fall through to `custom_auth`; future v1.x MAY register additional substitutions/encodings via §2.8. Conformance: **SHOULD**.
- `custom_auth` — escape hatch of last resort for providers whose authentication doesn't fit any registered method (some banking APIs with mTLS + body-digest, certificate-based schemes, legacy enterprise SSO). Wire shape: required `description` (free-text human-readable description, the primary surface a security reviewer consults) + required `parameters` object (string-keyed, values are credential refs or literal non-secret config). Implementations MAY decline `custom_auth` artifacts they can't verify safely; recurring `custom_auth` shapes SHOULD be promoted to a registered method via §2.8 in a future v1.x release. Conformance: **MAY**.

**Cross-cutting OAuth 2.0 subsections** (§2.2.4 through §2.2.6):

- **Refresh tokens** (§2.2.4) — applies to `oauth2_authorization_code` and `oauth2_device_code` (not client-credentials per RFC 6749 §4.4.3). Refresh framed as a single transition: current expired → fresh. Two normative behaviors: (a) **token rotation** — when the authorization server returns a new refresh token, it MUST replace the prior one (the prior one MUST NOT be retained); (b) **atomicity** — refresh either succeeds end-to-end or fails leaving prior state intact. Deeper lifecycle (proactive refresh windows, retry under transport failure, the `pending`/`active`/`revoked` state machine, observability) is **explicitly Stage 5**.
- **Server metadata discovery** (§2.2.5) — RFC 8414 `server_metadata_url` is a permitted alternative or complement to explicit endpoints. Combination rule: **explicit endpoints win** when both are present; `server_metadata_url` alone requires the implementation to fetch + validate the metadata document before initiating the OAuth flow.
- **JWT access tokens** (§2.2.6) — RFC 9068. UACP does **not** mandate that tokens be JWTs. When tokens are JWTs, dispatch SHOULD treat them as opaque for transport, MAY introspect `exp` for refresh-timing, and MUST NOT use other JWT claims (`iss`/`aud`/`sub`/`scope`) for authorization decisions inside UACP — those are the resource server's job.

**Composite shape** (§2.4.3) — composes `api_key_header`/`api_key_query` with `hmac_signature`/`aws_sigv4` for providers requiring both (public key in one header + per-request HMAC signature in another). Common across financial APIs and webhook receivers. Both methods apply on every request; collisions (both targeting the same header) MUST be rejected at validation.

**Credential references** (§2.7) — the `secret://<store>/<id>` scheme is **local to UACP, not IANA-registered**. Registered v1.0 stores: `vault`, `aws-secrets-manager`, `local-keyring`, `inline-encrypted`. Two normative rules govern artifact authoring: (a) artifacts MUST NOT embed plaintext secrets in any field, anywhere — every credential-shaped field takes the `_ref` suffix as an audit hook (`client_secret_ref`, `key_ref`, `consumer_secret_ref`, `access_key_ref`, `secret_key_ref`, `session_token_ref`); (b) implementations MUST reject any `_ref`-suffixed field whose value isn't a syntactically valid `secret://` URL at validation time (resolution is dispatch-time; validation is wire-shape only). Implementations MAY register additional `<store>` tokens (e.g. `gcp-secret-manager`, `azure-key-vault`) via §2.8 with the no-shadowing rule. **Secret-store implementations, encryption-at-rest, scope enforcement, audit logging, and the threat model are Stage 6** — §2.7 defines the *reference format only*.

**Auth-method extension** (§2.8) — three constraints on registrations: **additive-only** (no removal/rename in v1.x; that requires v2 per Principle 6), **semantically stable** (no altering wire shape / field semantics / dispatch behavior of registered methods; adding *optional* fields permitted only when omitting them reproduces prior behavior exactly), **disjoint identifiers** (new methods take new names, no shadowing/aliasing/extending). v1.x registration procedure: file as `[RFC]`-prefixed issue per `CONTRIBUTING.md` → maintainer reviews in public → if accepted, lands as editorial revision to §2.1 + the relevant section + §2.9 + `SPEC.md`. v2 introduces the public RFC process per Principle 12. Implementations encountering an unregistered method MUST decline silently — no substitution, no guessing — and surface `bad_input`. The combination of additive-only + silent-decline is what makes the registry safe to extend: old impls refuse cleanly rather than misinterpret; new impls always understand old methods.

**Conformance summary** (§2.9) — table summarizing each method's level for v1.x:

- 4 MUSTs: `oauth2_authorization_code` (with PKCE), `oauth2_client_credentials`, `api_key_header`, `api_key_query`
- 4 SHOULDs: `oauth2_device_code`, `oauth1a`, `aws_sigv4`, `hmac_signature`
- 1 MAY: `custom_auth`
- 4 MUST NOT items: no embedded plaintext credentials anywhere in any artifact; no OAuth 1.0a `PLAINTEXT` signing; no silent method substitution; no treating `code_challenge_method=plain` as equivalent to `S256`.

**Per-section word counts**: §2.0 = 575, §2.1 = 336, §2.2 = 1823, §2.3 = 482, §2.4 = 801, §2.5 = 970, §2.6 = 453, §2.7 = 462, §2.8 = 597, §2.9 = 541; total ~7197 words.

**Stage 2 closes two prior open questions** logged inside ADR-036:

- **Authentication-method registration mechanism** — resolved by §2.8. v1.x is single-maintainer with `[RFC]`-prefixed issues; v2 introduces the public RFC process.
- **Per-provider OAuth scope strategy** — resolved at the artifact-shape level by §2.2.1's `scopes` array and §2.2.2/§2.2.3's `scope` string. Incremental authorization (Google's strong recommendation) is a runtime/lifecycle choice that the artifact does not constrain — it's a Stage 5 lifecycle question.

**Hard rules honored**:

- Stayed within Stage 2's scope. No token-storage encryption (named as Stage 6); no refresh-worker scheduling / proactive-refresh windows / retry-on-failure (named as Stage 5); no retry policies under transport failure (named as Stage 4 + 5); no audit-log requirements (named as Stage 6); no scope enforcement at dispatch (named as Stage 6). Where the doc approached one of those boundaries, the boundary is named explicitly (§2.0's "out of scope" subsection plus pointers throughout).
- Did not relitigate locked decisions from Stages 0/1 — wire format JSON, `.uacp` extension, `application/uacp+json` MIME, AI-mediated authoring stance, public-spec stance, the twelve principles all baked in.
- Design-only — no code. JSON examples for `.uacp` shapes are in the doc; no Python or other implementation code.
- RFC voice throughout the public doc (third-person, declarative, MUST/SHOULD/MAY) per BCP 14.
- No retroactive conflicts with Stage 1's twelve principles (verified by walking each: layered, universal, AI-native, MCP-composable, pluggable, wire-stable, security-by-default, failure-mode-uniform, deterministic, public-artifacts/private-secrets, transport-minimal, open-governance — Stage 2 sits cleanly under each).

**No new entries to `docs/open-questions.md`** — the substitution-language minimalism in §2.5.2 is a known direction for future v1.x extension via §2.8, not a gap that blocks subsequent stages.

**AVA monorepo commit plan**: single memory commit — `memory: UACP Stage 2 session — authentication subsystem`. `git commit` denied by the harness on this run (same blocker pattern as many prior sessions); both memory files staged on top of `c202984` with the pre-formatted commit message on disk at `/tmp/uacp-stage2-memory-msg.txt` for operator-side manual `git commit -F` + push. Spec repo single commit already landed (`6e846ea` on top of `21c9dab`); operator pushes both manually per the brief.

**Other working-tree changes that are NOT this session's** (deliberately not touched): the connectivity-mcp `connections.entity_id` migration patches from prior session 2A; `docs/design/desktop-relay-multi-pod.md` from session 2C; the api-gateway rate-limiting work — all theirs to commit.

---

## 2026-05-04 — UACP Stage 3 — protocol-design / docs

Stage 3 of the UACP arc — the schema and discovery layer. Spec-repo deliverable is a single new file, `docs/03-schema.md`, plus status-table flips in `SPEC.md` and `docs/00-primer.md`. Single spec-repo commit `102ff85 feat(spec): UACP Stage 3 — schema + discovery` on top of `6e846ea` (the Stage 2 tip). Design-only; no code.

**Canonical operation form** (§3.1-§3.5) defines the wire shape every `Operation` MUST conform to once it's part of a stored `.uacp` artifact, regardless of how it originated:

- Required fields: `id` (`[a-z][a-z0-9_-]{0,127}`, kebab- or snake-case, unique within file, stable across artifact revisions), `summary` (one-sentence user-intent vocabulary; the discovery surface for agent NL→operation selection — "Send an email" beats "POST /v1/messages"), `request` (§3.2), `response` (§3.3).
- Optional fields: `description` (longer prose), `tags` (flat array, no taxonomy enforced), `deprecated` (boolean, default false), `idempotency` (`idempotent` / `not_idempotent` / `unknown` — schema metadata declaring retry-safety; Stage 4 specifies dispatch consumption), `pagination` (§3.4), `source` (provenance per §3.6-§3.8).
- **Request shape** (§3.2): `method` ∈ {`GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`} uppercase (others rejected at validation; future v1.x MAY register more via §2.8); `path` per RFC 6570 URI Template (path component only, no query string); `path_parameters` / `query_parameters` / `headers` as JSON Schema 2020-12 (auth headers explicitly excluded — Stage 2 owns those; `Content-Type` derives from `body.media_type`); `body` ∈ {`"none"`, inline `{media_type, schema}` defaulting to `application/json`, local `$ref` into `definitions`}. Multi-shape bodies use JSON Schema `oneOf`. **Bidirectional path-parameter rule** is load-bearing — every `{name}` in path MUST be in path_parameters and every property of path_parameters MUST appear in path; mismatch is a §3.10 validation failure (audit hook for typos catchable before dispatch).
- **Response shape** (§3.3): keyed by exact 3-digit status, status range (`1xx` through `5xx`), or literal `default`; collisions resolve more-specific-wins. Each entry has `description`, optional `body` (same shape as request body), optional `headers`, optional `streaming` boolean (chunk shape — runtime delimiter convention is Stage 4). **Canonical error envelopes SHOULD be declared** under `4xx`/`5xx` so dispatch can extract structured errors instead of treating bodies as opaque (Slack `{ok:false,error}`, GraphQL `{errors:[...]}`, RFC 9457 `problem+json`); how dispatch *uses* the envelope to map into Principle 8's failure-mode vocabulary is Stage 4.
- **Pagination** (§3.4) registered patterns in v1.0: `cursor` (with JSONPath into response per RFC 9535, `request_cursor_parameter` MUST exist in query/body params), `offset` (`request_offset_parameter` + `request_limit_parameter` MUST exist in query, with `total` or `has_more` JSONPath), `link_header` (RFC 8288 `Link: rel=next`), `none`. Pattern declares which fields participate; Stage 4 owns when-to-stop / how-many-pages-by-default / how-to-surface-partial-results. New patterns (keyset, page-number distinct from offset, timestamp-windowed) are recognized as long-tail but unregistered in v1.0; future v1.x MAY register via §2.8.
- **Discovery** (§3.5): flat namespace, O(1) lookup by `id`; agents read `summary` and `tags` for NL-driven selection; UACP does NOT specify the agent's selection algorithm (embedding similarity, keyword match, LLM-based selection are all conforming); the only normative requirement is `id`-based resolvability after selection. Per-operation authentication overrides are out of scope for v1.0; future v1.x addition via §2.8.

**Three sources** (§3.6-§3.8) — UACP's distinguishing bet that schemas can come from published OpenAPI specs, user-pasted `curl`, or LLM inference and converge to the same canonical form:

- **OpenAPI ingestion** (§3.6) — full mapping table: `operationId` → `id` (synthesize from `<method>_<path>` when missing; deterministic synthesis rule), `summary` → `summary`, `description` → `description`, `tags` → `tags` (charset-normalized), method+path → `request.method`+`request.path`, parameters by `in` location → `request.path_parameters`/`query_parameters`/`headers`, `requestBody` → `request.body` (multi-media-type maps to `oneOf`), `responses` → `response`, `components.schemas` → `definitions` with `$ref` rewriting `#/components/schemas/X` → `#/definitions/X`. Excluded from mapping: `security` + `securitySchemes` (Stage 2 territory; ingestion MAY surface as authoring hint), `servers` (Stage 4 base-URL territory; pre-populate from single entry, prompt user when multiple), `callbacks` + `webhooks` (out of scope for v1.0 HTTPS-only agent-initiated transport). Idempotency defaults from RFC 9110 §9.2.2: `GET`/`HEAD`/`OPTIONS`/`PUT`/`DELETE` → `idempotent`, `POST`/`PATCH` → `unknown`. Pagination MAY be inferred from convention (cursor + next_cursor names → `cursor`; offset + limit → `offset`; `Link` response header → `link_header`) but flagged for user review. Ingested operations carry `source = {type: "openapi", url, ingested_at}`.
- **`curl`-paste parsing** (§3.7) — supported flag set: `-X`/`--request`, `-H`/`--header`, `-d`/`--data`/`--data-raw`, `-G`/`--get`, `--data-urlencode`, URL positional argument (decomposed into base URL + path + query). Body media type defaults from `curl`'s own behavior: `application/x-www-form-urlencoded` unless explicit `Content-Type: application/json` or body parses as JSON. Multipart (`-F`/`--form`), `--cookie`/`-b`, `-u`/`--user`, transport-only flags (cert pinning, proxy, retries) are out of scope; implementations MAY decline complex invocations with clear errors but MUST NOT silently drop. **Authentication-bearing artifacts MUST be detected and stripped**: `Authorization: Bearer`/`Token`, `X-API-Key`, basic auth, query-param keys named `api_key`/etc. Surfaced to user with recommendation to move to Stage 2 authentication block; credentials never enter the artifact even transiently. `curl` is request-only — response shapes can't be inferred; placeholder permitted during interim editing but `body: "none"` placeholders MUST NOT pass §3.10 validation in production storage.
- **LLM inference** (§3.8) — most distinctive, most easily-broken section. UACP does NOT specify the LLM, prompt structure, inference pipeline, or review UI — implementation concerns. UACP DOES specify three load-bearing artifact-level rules:
  1. **Mandatory user review before persistence.** Implementation MUST NOT persist inferred schema without explicit user-approval step. Review presentation MUST include canonical JSON form + human-readable summaries + verbatim original NL description. User MUST be able to edit before approval — approve-as-is-or-reject is non-conforming.
  2. **Provenance metadata required.** `source = {type: "inferred", model: "<provider/model>" e.g. "anthropic/claude-sonnet-4.6", description: <verbatim NL the user supplied>, confidence: low|medium|high (informational only — does NOT gate use; agents MAY surface to users when inferred operation behaves unexpectedly), reviewed_at: <RFC 3339 timestamp of user approval>}`. Operations with `source.type=="inferred"` and missing/empty `reviewed_at` MUST fail validation.
  3. **Refinement preserves `id` and source attribution.** When dispatch surfaces unexpected results, the user MAY paste actual response example / corrected request shape / updated description; LLM re-drafts; refined draft goes through the same mandatory-review gate; the operation's `id` MUST remain the same so already-deployed agents keep working (this leans on the §3.5 stability rule).
  Inferred schemas SHOULD prefer permissive over precise (overly-strict rejects real responses; refinement is the recovery path). Inferred schemas MUST NOT include credentials even transiently in `description`; implementations MAY scrub before passing to LLM and SHOULD surface detection during review.

**Source priority** (§3.9): explicit user input > `curl`-paste > OpenAPI > LLM-inferred. When multiple sources contribute drafts of the same operation `id` at authoring time, higher-priority wins; lower-priority MAY be silently dropped or surfaced as warning (implementation choice — both conforming). Duplicate `id`s at load time are §3.10 validation failures, not conflicts to resolve — load-time validation rejects duplicates rather than picking one.

**Validation rules** (§3.10): `$schema` reference present at top level (canonical URL deferred to Stage 9 prototype freeze; placeholder `https://uacp.spec/v1/schema.json` until then; artifacts pinning specific URL today MUST be re-pinnable at freeze without semantic change); top-level structure with `authentication` + `operations`; unique `id`s; `id` charset; `summary` present and non-empty; `method` validity (uppercase from fixed set); bidirectional path-parameter rule; response-key validity; pagination cross-references (named cursor parameter MUST exist in query/body params; named offset+limit MUST exist in query); **no embedded credentials anywhere** — schema-layer restatement of Principle 7; inferred-operation provenance complete; **all `$ref`s local** — remote `$ref` resolution at dispatch time is forbidden, schema-layer expression of Principle 9 (determinism). **Failure behavior**: reject the artifact whole, no partial loading; surface a clear error identifying which operation (`id` or array index) and which constraint failed; do not dispatch. Validation is purely structural — testing against live Provider is Stage 4 / Stage 9 concern.

**Conformance summary** (§3.11):

- **2 MUSTs**: hand-authored canonical JSON (any `Conforming Implementation` MUST accept hand-authored `.uacp` files validated against the spec); OpenAPI 3.x ingestion.
- **2 SHOULDs**: OpenAPI 2.0 (Swagger) ingestion for legacy providers; `curl`-paste parsing.
- **1 MAY**: LLM inference (an implementation MAY decline if it doesn't have an LLM available; if it does, §3.8's rules apply).
- **5 MUST NOT items**: silent persistence of LLM-inferred schemas without user review; loading invalid artifacts (§3.10 rejection is mandatory); embedded credentials in any field of any operation; remote `$ref` resolution at dispatch time; silent drop of unknown fields on round-trip — implementations MAY accept unknown fields for forward-compat but MUST round-trip them verbatim when re-serializing.

The conformance line splits along *load* vs *author*: an implementation that supports only hand-authored and OpenAPI-3.x-ingested artifacts is conforming; richer authoring (curl-paste, OpenAPI 2.0, LLM-inference) is conforming-with-richer-authoring. The asymmetry matters because a stored artifact's `source.type=="inferred"` does not require the loading implementation to support inference at load time — `reviewed_at` is the durable record of authoring-time review.

**Per-section word counts** (~8181 words total): §3.0 = 517, §3.1 = 778, §3.2 = 705, §3.3 = 555, §3.4 = 487, §3.5 = 490, §3.6 = 880, §3.7 = 858, §3.8 = 1092, §3.9 = 405, §3.10 = 692, §3.11 = 520.

**Stage 3 partially resolves one ADR-036 open question and re-defers part of it**: `$schema` URL form — the *format* (versioned URL referenced from artifact's top-level `$schema` field, validated against JSON Schema 2020-12 dialect) is defined in §3.10; the *canonical URL* (host, path shape, per-artifact pinning rules) is deferred to Stage 9 (prototype) when the spec is frozen, with a placeholder `https://uacp.spec/v1/schema.json` permitted in the interim. The placeholder is replaceable without semantic change at freeze.

**Hard rules honored**:

- Stayed within Stage 3's scope. No dispatch loops / retry policies / error-recovery / rate-limit handling / streaming runtime semantics / parameter binding / base-URL resolution (named as Stage 4); no token storage / encryption / scope enforcement / audit logging (named as Stage 6); no connection-lifecycle states / refresh worker scheduling / revocation propagation (named as Stage 5). Where the doc approached one of those boundaries, the boundary is named explicitly (§3.0's "out of scope" plus pointers throughout).
- Did not relitigate Stage 0/1/2 decisions — wire format JSON, twelve principles, nine auth methods, secret:// references all baked in.
- Design-only — no code. JSON examples for `.uacp` operation shapes are in the doc; no Python or other implementation code.
- LLM-inference path (§3.8) treated with crisp user-review requirement and provenance rules; inference pipeline itself NOT over-specified (that's an implementation detail).
- RFC voice throughout per BCP 14.
- No retroactive conflicts with Stages 0/1/2 — verified by walking each principle and each Stage 2 method; Stage 3's request/response schemas exclude auth headers (Stage 2's territory) and reference credentials only via `secret://` (§2.7) consistent with Principle 7; flat namespace + `id` stability supports Principle 9 (determinism); `$ref`-local rule keeps determinism intact at the schema layer.

**No new entries to `docs/open-questions.md`** — the per-operation authentication overrides path is named as a future v1.x addition via §2.8; the `source.type` value for hand-authored operations is left as implementation convention (absence of `source` is sufficient); future pagination-pattern registrations (keyset, page-number, timestamp-windowed) are named as future-registration candidates.

**AVA monorepo commit plan**: single memory commit — `memory: UACP Stage 3 session — schema + discovery`. `git commit` denied by the harness on this run (same blocker pattern as Stage 2 and many prior sessions); both memory files staged on top of `6638d6e` with the pre-formatted commit message on disk at `/tmp/uacp-stage3-memory-msg.txt` for operator-side manual `git commit -F` + push. Spec repo single commit already landed (`102ff85` on top of `6e846ea`); operator pushes both manually per the brief.

**Other working-tree changes that are NOT this session's** (deliberately not touched): any in-flight work in connectivity-mcp / desktop-relay / api-gateway from prior sessions — all theirs to commit.

---

## 2026-05-04 — UACP GSD Stages 4-7 — protocol-design / docs

GSD mega-session covering Stages 4 through 7 of the UACP arc, all written and committed in one sitting per the brief's serialized-commit discipline ("commit after each stage before starting the next"). Four spec-repo commits, one combined AVA memory commit. Design-only; no code.

**Spec repo commit chain** on `main`, ascending: `26f4b0f feat(spec): UACP Stage 4 — dispatch runtime` → `510fb44 feat(spec): UACP Stage 5 — connection lifecycle` → `b55affb feat(spec): UACP Stage 6 — security model` → `fddd6a7 feat(spec): UACP Stage 7 — versioning + extensibility`. Stage 7's commit also bundles `SPEC.md` and `docs/00-primer.md` index updates flipping Stages 4-7 Pending → Complete with links. Tip is `fddd6a7`; prior tip was `102ff85` (Stage 3, prior session).

**Stage 4 — Dispatch runtime** (`docs/04-dispatch.md`, ~7445 words across §4.0-§4.9):

- **§4.1 Connection-level dispatch configuration.** Required top-level `dispatch` block alongside `authentication` and `operations`: `base_url` (HTTPS only), `default_headers`, `default_timeout_ms` (default 30000), `default_user_agent`. Composition order: URL from base_url + path-substituted RFC 6570 template + query string; headers merge with operation overrides winning; authentication subsystem applies last; body serialized per media_type. Per-operation `timeout_ms` and `retry` overrides at operation top level.
- **§4.2 HTTP transport.** HTTPS only per Principle 11; `http://` rejected at validation and as defense-in-depth at dispatch. TLS 1.2 minimum; SHOULD prefer TLS 1.3; certificate validation MUST be on by default. HTTP/1.1 MUST; HTTP/2 SHOULD when ALPN advertises; HTTP/3 MAY. Redirects: at most 5 per call, loop detection, HTTPS-only targets, authentication-bearing headers stripped on cross-origin redirects. 301/302/303 method-changing redirects SHOULD surface as `upstream_error` rather than silently rewriting; opt-in permissive flag available.
- **§4.3 Retry policies.** Default: 3 attempts, 250 ms initial backoff, 2× multiplier, 5000 ms cap, ±25% uniform jitter. Idempotent methods (GET/HEAD/PUT/DELETE/OPTIONS, or POST/PATCH with `idempotency: idempotent` per Stage 3 §3.1) retry on 5xx and transient transport errors. Non-idempotent operations MUST NOT retry automatically. 4xx responses except 429 surface immediately. Per-operation retry overrides via `retry` object.
- **§4.4 Pagination loops.** Per-pattern iteration semantics for cursor (terminate on empty/null/repeated cursor; first request omits cursor parameter), offset (terminate on `has_more=false`, total reached, empty/partial page; `has_more` takes precedence over total when both present), link_header (follow `rel=next` until absent; cross-origin URIs surface warning and SHOULD end loop rather than continue with stripped auth), none. Per-call max-pages safety limit, default 100, configurable — reaching it surfaces a distinguishable `upstream_error`. Each page is independently retryable; streaming pagination consumes each page's stream in full before fetching the next.
- **§4.5 Rate-limit handling.** 429 with `Retry-After` honored, capped at sanity threshold (default 120s); 429-specific retry budget separate from 5xx budget. `X-RateLimit-Remaining` / `X-RateLimit-Reset` advisory headers SHOULD be consumed to anticipate limits. SHOULD pool rate-limit state per Connection across operations.
- **§4.6 Error envelope handling.** Canonical error shape `{status, code, message, details, raw}` exposed to callers regardless of provider envelope variation. `code` drawn from Principle 8's vocabulary; HTTP-status → canonical-code default mapping table (400 → bad_input, 401 → auth_expired, 403 → forbidden, 404 → not_found, 408 → upstream_error, 409/422 → bad_input, 429 → rate_limited, 500/502/503/504 → upstream_error). Implementations MAY refine code from envelope context. Recovery (refresh-then-retry on auth_expired etc.) is Stage 5 for lifecycle paths and the agent's concern for others.
- **§4.7 Streaming responses.** SSE, NDJSON, chunked transfer SHOULD be supported; WebSocket upgrade MAY. One chunk per SSE event (multi-line `data:` accumulation), per NDJSON line, per WebSocket frame. Termination on connection close, end-sentinel (e.g., SSE `[DONE]` recognized but not mandated; artifact MAY declare `streaming.end_sentinel`), per-call timeout, caller cancellation, transport error. Streaming responses NOT retried after first chunk delivered; pre-first-chunk failures retry per §4.3. Idle timeout tracks per-chunk gap separately from initial timeout.
- **§4.8 Idempotency keys.** MAY support automatic `Idempotency-Key` header injection for POST/PATCH operations declared `idempotency: idempotent`. UUIDv4 per dispatch, reused across retries within that dispatch. Caller MAY supply explicit key overriding auto-generation. Persistence of injected key for audit defers to Stage 6.
- **Conformance §4.9**: 12 MUSTs / 9 MUST NOTs / 11 SHOULDs / 10 MAYs.

**Stage 5 — Connection lifecycle** (`docs/05-lifecycle.md`, ~5251 words across §5.0-§5.7):

- **§5.1 State machine.** Seven states: `pending`, `active`, `expiring`, `refreshing`, `expired`, `revoked`, `error`. Spec-mandated transitions (`pending→active`, `active→revoked`, `refreshing→active`, `refreshing→revoked` on `invalid_grant`, `expired→refreshing`, `revoked` terminal) plus implementation-discretion transitions (`active→expiring` optional, proactive vs reactive refresh, `error→refreshing` auto-retry). Concurrency: per-Connection single-flight lock around refresh; dispatches in `refreshing` queue or fail-fast (implementation choice); `revoked` drains queued operations with appropriate failure.
- **§5.2 Refresh policies.** Lazy MUST (refresh on dispatch against expired Connection); proactive SHOULD (background worker refreshes within window); reactive MAY (catch 401 → refresh → retry once; one retry max, not a loop). Refresh window: `max(60s, expires_in × 0.1)`. Implementations MAY use more conservative window; MUST NOT use more aggressive.
- **§5.3 Refresh-token rotation.** When refresh response includes `refresh_token`, MUST atomically replace prior; partial write that loses new token while old is invalidated is the connection-killing bug. Persist new tokens to durable storage before transitioning to `active` and serving dispatch. Response without `refresh_token` MUST retain prior — discarding still-valid refresh token is symmetric error.
- **§5.4 Revocation propagation.** Three sources: user-initiated, Provider-initiated at refresh (`invalid_grant`), webhook-driven. MUST stop accepting dispatch against revoked Connections; queued operations drain with failure. SHOULD call Provider's revocation endpoint per RFC 7009 on user-initiated revocation. Webhook listening MAY; MUST verify webhook authenticity before acting (DoS vector otherwise). Revocation is Connection-wide, not per-operation.
- **§5.5 Re-authentication.** Connection identifier SHOULD be retained across re-auth for audit/reference coherence. `secret://` URIs MAY remain stable with credentials overwritten, or MAY change. MUST surface clear "needs re-authentication" indication on dispatch against revoked/terminal-error Connection; MUST NOT silently retry indefinitely. SHOULD expose user-visible re-auth affordance.
- **§5.6 Persistence.** MUST persist: `connection_id`, `state`, `uacp_artifact` reference, `secret_refs`, `access_token_expires_at`, `refresh_token_expires_at`, `last_dispatched_at`. MAY persist: `scopes_granted`, `last_refreshed_at`, `error_history_sample`, `provider_account_metadata`. Persistence format implementation-defined; encryption posture for metadata at rest is Stage 6. At restart: persisted state is optimistic; authoritative state is what the Provider returns on next dispatch/refresh.
- **Conformance §5.7**: 9 MUSTs / 6 MUST NOTs / 7 SHOULDs / 6 MAYs.

**Stage 6 — Security model** (`docs/06-security.md`, ~6649 words across §6.0-§6.9):

- **§6.1 Threat model.** Defended threats: credential theft from `.uacp` artifacts (defense: secret://); credential theft from server compromise (defense: encryption-at-rest); over-broad authorization through artifact edits (defense: local scope enforcement); replay attacks on signed-request schemes (defense: timestamp validation); audit-trail evasion (defense: append-only or chained logs); supply-chain attacks on `.uacp` files (defenses: schema validation, trust warnings, mandatory user review for inferred schemas). Out-of-scope threats: post-compromise blast-radius reduction within agent runtime; nation-state-class adversaries; side-channel attacks; DoS against Provider; Provider-side bugs.
- **§6.2 Secret store registry.** Four registered stores in v1.0 with full resolution semantics — `vault` (KV v2 path, fragment sub-field selection), `aws-secrets-manager` (secret name with optional `version` query param, fragment sub-field), `local-keyring` (OS keyring with service/account; macOS Keychain / Windows Credential Manager / Linux Secret Service), `inline-encrypted` (encrypted blob in artifact's `encrypted_secrets` block with `key_ref` to another store; recursive `inline-encrypted` forbidden). Common rules: resolve at dispatch time not load; cache only within single dispatch call's lifetime; failure surfaces as `auth_expired`. MAY register additional stores via §2.8.
- **§6.3 Encryption-at-rest.** MUST NOT persist plaintext credentials. Encryption MUST satisfy confidentiality, integrity, per-Connection separation. Recommended algorithm AES-256-GCM (ChaCha20-Poly1305 acceptable). SHOULD support envelope-encryption key rotation (per-Connection DEKs wrapped by KEK in implementation's KMS / Vault Transit / HSM; rotate KEK without re-authenticating Connections).
- **§6.4 Credential lifecycle.** UACP doesn't generate credentials; persistence is encrypted; rotation handles refresh-token rotation (§5.3) plus periodic forced rotation per implementation policy; revocation per §5.4; deletion MUST remove every associated secret-store entry — orphaned credentials are MUST NOT.
- **§6.5 Local scope enforcement.** MUST verify operation's required scopes ⊆ Connection's granted scopes locally before issuing wire request. Local rejection code `INSUFFICIENT_SCOPE_LOCAL` distinguishable from Provider-side `INSUFFICIENT_SCOPE_REMOTE` — different recovery paths. Defense against accidental scope creep through artifact edits.
- **§6.6 Audit logging.** MUST log state transitions, credential lifecycle events, dispatch attempts (one per call, regardless of outcome — not per retry), scope-enforcement rejections, authentication failures, encryption-key-rotation events, revocation events. Per-event field set: `timestamp`, `actor`, `connection_id` (when scoped), `event_type`, `outcome`, `detail`. Plaintext credentials MUST NOT appear in `detail`. SHOULD use append-only storage or cryptographic chaining for integrity.
- **§6.7 Trust model for ingested artifacts.** OpenAPI ingestion SHOULD warn on origin mismatch with Provider's canonical domain; `curl`-paste inherits user trust; LLM-inferred schemas have mandatory user review (per §3.8) plus SHOULD destructive-verb highlighting (DELETE method, destructive path segments, destructive summary language). Defense against agent-injection-class adversarial inferences.
- **§6.8 Compliance posture.** Informational mapping to SOC 2, GDPR, HIPAA, ISO 27001 — UACP's controls cover the technical foundation; implementations claim their own compliance via audit. UACP does NOT certify implementations.
- **Conformance §6.9**: 10 MUSTs / 6 MUST NOTs / 8 SHOULDs / 7 MAYs.

**Stage 7 — Versioning + extensibility** (`docs/07-versioning.md`, ~4246 words across §7.0-§7.7):

- **§7.1 Versioning scheme.** Semver MAJOR.MINOR.PATCH. `$schema` URL identifies MAJOR.MINOR; PATCH releases don't change the URL. Implementations MUST validate `$schema`; accept same-major equal-or-earlier-minor; MAY accept later-minor under forward-compatibility (unknown fields preserved on round-trip per §3.11; unknown registered identifiers declined silently per §2.8); MUST reject different-major.
- **§7.2 Breaking vs non-breaking.** Non-breaking MAY be released within v1.x: registry growth, optional fields, new MAY items, deprecation markers. Relaxation of MUST→SHOULD discouraged. Breaking requires v2: removing/renaming registered identifiers, tightening constraints, changing file structure / canonical URI schemes / wire format substrate, changing dispatch or lifecycle semantics, removing deprecated identifiers. Editorial PATCH releases for typos, link fixes, formatting, clarifying rephrasing only.
- **§7.3 Extension points.** Auth methods (§2.1), pagination patterns (§3.4), secret stores (§6.2), streaming patterns (§4.7), HTTP methods (§3.2), HMAC substitution language (§2.5.2), media types (§3.2), canonical-error code refinements (§4.6). In-development extensions MUST use `x-` prefix to avoid collision with future registered names.
- **§7.4 Deprecation process.** Deprecation lands in v1.x minor release; deprecated identifier MUST continue to function for the rest of v1.x; deprecation surface (warning to user) is MAY; removal eligible in v2 via the v2 RFC process. Spec entry format documents version deprecated, version removed, reason, replacement.
- **§7.5 v1.x evolution governance.** Single-maintainer per `GOVERNANCE.md`; `[RFC]`-prefixed issues for major-change proposals; release cadence as-needed; MINOR releases bundle accepted changes + CHANGELOG entry + `$schema` URL bump; PATCH releases for editorial fixes only. v1.0 freeze comes after Stage 8 (conformance test suite) and Stage 9 (prototype) validate implementability.
- **§7.6 Path to v2.** v2 worth pursuing when accumulated deprecations warrant clearance, foundational constraints prove inadequate, or adoption surface demands new capabilities. v2 RFC process intentionally not specified here; per Principle 12 it's public RFC when it kicks in. v1.x and v2 may coexist; `$schema` routes between them; v1.x continues to receive PATCH for editorial fixes after v2 lands.
- **Conformance §7.7**: 6 MUSTs / 4 MUST NOTs / 4 SHOULDs / 5 MAYs.

**Aggregate across the four stages**: ~23,591 words; **37 MUSTs / 25 MUST NOTs / 30 SHOULDs / 28 MAYs**.

**Hard rules honored across the GSD**:

- Each stage is its own commit. Stage 4 committed before Stage 5 started; Stage 5 before Stage 6; Stage 6 before Stage 7. `git log --oneline -5` sanity-check between each. No bundling.
- Did not relitigate Stages 0/1/2/3 — wire format JSON, twelve principles, nine auth methods + secret://, canonical Operation form all baked in unchanged.
- Design-only — JSON examples for shapes appropriate; no Python or other implementation code.
- Conformance summaries (§4.9, §5.7, §6.9, §7.7) load-bearing and consistent in structure across the four stages; same MUST/MUST NOT/SHOULD/MAY breakdown each time.
- RFC voice throughout per BCP 14.
- No retroactive conflicts with Stages 0-3 — verified by walking each principle, each Stage 2 method, each Stage 3 schema rule. Stage 4's references to lifecycle states (`auth_expired` triggering refresh) sit cleanly at the Stage 4/5 boundary; Stage 5's references to encryption-at-rest sit cleanly at the Stage 5/6 boundary; Stage 6 closes everything without contradiction.

**No new entries to `docs/open-questions.md`** — no foundational ambiguities surfaced. The `$schema` canonical-URL deferral to Stage 9 was already noted in the Stage 3 session and is referenced again in §7.1 with the same placeholder. v2 design intentionally undefined per §7.6 and Principle 12 — that's a feature, not an open question. Destructive-verb highlighting in §6.7 leaves the specific destructive-pattern set as implementation-defined — recognized future-extension territory via §2.8, not a gap.

**Spec repo state after this session**: tip `fddd6a7` on `main`; eight files under `docs/` (Stages 0 through 7 all written); `SPEC.md` and `docs/00-primer.md` index tables show Stages 0-7 Complete with links, Stages 8-10 Pending. The design half of the UACP spec is closed at the protocol level. Stages 8 (conformance test suite) and 9 (prototype) operationalize; Stage 10 freezes v1.0.

**AVA monorepo commit plan**: single combined memory commit — `memory: UACP GSD Stages 4-7 — dispatch + lifecycle + security + versioning`. `git commit` denied by the harness on this run (same blocker pattern as Stages 2 and 3); both memory files staged on top of `8f50dc2` with the pre-formatted commit message on disk at `/tmp/uacp-gsd-stages-4-7-memory-msg.txt` for operator-side manual `git commit -F` + push. Spec repo's four commits already landed (`26f4b0f`, `510fb44`, `b55affb`, `fddd6a7` on top of `102ff85`); operator pushes both repos manually per the brief.

**Other working-tree changes that are NOT this session's** (deliberately not touched): any in-flight work in connectivity-mcp / desktop-relay / api-gateway from prior sessions — all theirs to commit.

---

## 2026-05-04 — UACP Stage 8a — Google OAuth prototype + Gmail/Calendar validation

First code session of the UACP arc; first per-provider session. The reference Python prototype now exists at `prototype/python/` in the spec repo and validates the MUST surface across Stages 2-6 against Google's Gmail and Calendar APIs. Subsequent provider sessions (Stage 8b Slack curl-paste, 8c AWS S3 SigV4, 8d GitHub, 8e custom-auth + LLM inference) build on this scaffold.

**Spec repo commit chain** on `main`, ascending, on top of `fddd6a7` (Stage 7 tip):

- `6844917 feat(prototype): bootstrap Python prototype scaffold` — pyproject.toml uv-managed py3.12 with httpx + pydantic + cryptography + jsonschema + pytest + respx; full module skeleton with one-line docstrings naming the spec section each implements; CLI scaffold; smoke test confirms package imports cleanly.
- `bff9fcb feat(prototype): implement spec loader + JSON Schema validation` — pydantic v2 models mirroring §3.1-§3.5; schema.py adds the cross-cutting §3.10 semantic validations; loader.load(path) is the entry point. **35 tests passed** covering happy path, charset/method/path-shape rules, bidirectional path rule, duplicate-id rejection, pagination cross-references, embedded credentials (literal client_secret rejected), `_ref`-must-be-secret-uri, $ref locality, HTTPS-only base URL, authentication method registry, inferred-source provenance, inline-encrypted recursion forbidden, forward-compat unknown fields preserved.
- `608a8af feat(prototype): implement OAuth 2.0 authorization-code + PKCE` — auth/base.py defines AuthMethod Protocol; auth/oauth2_authcode.py implements the §2.2.1 four-step flow with PKCE S256 always (plain refused). **18 tests passed** covering PKCE generation, build_auth_url, exchange_code, refresh, apply-on-dispatch.
- `2d6c049 feat(prototype): implement dispatch client + pagination + retries` — DispatchClient handles §4.1 composition + §4.2 HTTPS+TLS+redirects + §4.3 retry policy + §4.5 rate-limit + §4.6 canonical error shape; pagination.py implements cursor / offset / link_header / none with §4.4 termination + 100-page safety limit. **54 tests passed** covering status mapping, backoff, Retry-After parsing, composition order, retry policy under multiple conditions, rate-limit handling, error envelope, redirects, pagination per pattern, max-pages safety, failure mid-loop.
- `bb60ba0 feat(prototype): implement lifecycle state machine + refresh` — ConnectionState StrEnum + Connection class with explicit transitions per §5.1; refresh_with_rotation is the §5.3 atomic exchange. **37 tests passed** covering all state transitions, terminal-revoked enforcement, error-recovery, parametrized is_dispatchable, pending failure paths, rotation success/failure paths, refresh-window boundaries.
- `bda64ca feat(prototype): implement encryption-at-rest + secret resolver` — security/encryption.py implements AES-256-GCM envelope encryption per §6.3; security/secrets.py implements the `secret://<store>/<id>#<field>` parser with fragment sub-field selection; LocalKeyringStore is filesystem-simulated; vault and aws-secrets-manager are NotImplementedError stubs. **23 tests passed** covering URI parsing, master-key generation, encrypt/decrypt round-trip, tampering detection, master-key rotation, LocalKeyringStore, resolver routing, inline-encrypted resolution.
- `71dbecd feat(prototype): implement Google discovery-doc ingestion` — connections/ingest_openapi.py with `from_openapi()` and `from_discovery_doc()` per §3.6 mapping table. **15 tests passed** covering both entry points with realistic Gmail and Calendar fragments.
- `a622a1f feat(prototype): Gmail send + Calendar list .uacp + integration tests` — `examples/google/gmail-send.uacp` and `examples/google/google-calendar-list.uacp` both load and validate cleanly. cli.py with three subcommands (validate / ingest-openapi / dispatch). **5 mock-based end-to-end tests** asserting Gmail send + Calendar pagination flow through the full stack with mocked httpx; **5 @pytest.mark.integration tests** skipped by default via `addopts = -m 'not integration'` covering Gmail send + Calendar list + Calendar paginated + discovery-doc round-trip + refresh-then-redispatch.

**Aggregate test count**: **188 unit tests passing**, **5 integration tests deselected by default**, 0 failures. Run wall ~0.24s. Module-by-module: test_smoke=1, test_spec_loader=35, test_oauth2_authcode=18, test_dispatch_client=38, test_pagination=16, test_lifecycle_state=26, test_refresh=11, test_secrets=23, test_ingest_openapi=15, test_end_to_end_mock=5, providers/test_google=5 (deselected).

**Two `.uacp` files validating**: `gmail-send.uacp` and `google-calendar-list.uacp` both pass `spec.loader.load()` cleanly.

**Discovery-doc ingestion confirmed against Google's published descriptions**: the integration test `test_discovery_doc_ingestion_matches_hand_written` fetches `https://www.googleapis.com/discovery/v1/apis/{gmail/v1,calendar/v3}/rest`, parses both, and asserts that the produced operations match the hand-written `.uacp` files modulo §3.6's documented lossiness (idempotency defaults to `unknown` from POST ingestion; the hand-written artifact's `not_idempotent` for gmail send is more specific).

**Spec gaps surfaced**: zero. Every spec MUST that the prototype implements lined up cleanly with the docs; the only documented divergence is the §3.6 idempotency-default lossiness which is already named in the spec. No `fix(spec): ...` commits needed; no entries logged to `docs/open-questions.md` from prototype implementation.

**Stub-then-fill plan honored** for non-Google methods. The following stubs raise NotImplementedError with explicit "this stub is intentional in Stage 8a; implemented in Stage 8X" messages: `auth/aws_sigv4.py` (Stage 8c), `auth/custom.py` (Stage 8e), `connections/ingest_curl.py` (Stage 8b), `connections/ingest_nl.py` (Stage 8e), plus the `vault` and `aws-secrets-manager` SecretResolver entries.

**Operator setup steps for running the integration tests** (full instructions in `prototype/python/README.md`):

1. https://console.cloud.google.com/ → create or select a project.
2. APIs & Services → Library → enable Gmail API and Google Calendar API.
3. Credentials → Create Credentials → OAuth client ID → Desktop app. Download JSON.
4. OAuth consent screen → External → add scopes `gmail.send` + `calendar.readonly` → add yourself as test user.
5. Populate `prototype/python/.env` (or export):
    - `UACP_GOOGLE_CLIENT_ID=...`
    - `UACP_GOOGLE_CLIENT_SECRET=...`
    - `UACP_GOOGLE_TEST_EMAIL=...`
    - `UACP_GOOGLE_REDIRECT_URI=http://localhost:8765/oauth/callback`
6. `cd prototype/python && uv run pytest tests/providers -m integration`.

The first run launches the OAuth flow in a browser; subsequent runs reuse the persisted token.

**macOS UF_HIDDEN flag** on the editable-install `.pth` file in the venv had to be cleared once with `chflags nohidden` mid-session — same operational quirk AVA's CURRENT.md documents. Subsequent `uv sync` calls need the same treatment.

**Hard rules honored**: did not change spec docs (only `prototype/` was added); did not push either repo; integration tests stay decorated and skipped via `addopts`; non-Google providers stay stubs; no plaintext credentials in any logs; CLI is three subcommands max.

**AVA monorepo commit plan**: single memory commit — `memory: UACP Stage 8a — Google OAuth prototype + Gmail/Calendar validation`. Spec repo's eight commits already landed (`6844917`, `bff9fcb`, `608a8af`, `2d6c049`, `bb60ba0`, `bda64ca`, `71dbecd`, `a622a1f` on top of `fddd6a7`). Operator pushes both repos manually per the brief.

**Other working-tree changes that are NOT this session's** (deliberately not touched): any in-flight work in connectivity-mcp / desktop-relay / api-gateway from prior sessions — all theirs to commit.

---

## 2026-05-04 — memory + executor-coordination migration to UACP repo — meta / docs

UACP became self-documenting as a standalone public personal-project repo. Until this session, every UACP session logged its memory entries to AVA's `docs/memory/` because UACP started as an AVA architectural decision (ADR-036). UACP's memory and executor-coordination conventions now live in this repo.

**This commit + the next one bootstrap UACP's self-documentation surface**:

- `docs/memory/CHANGELOG.md` (this file) — append-only log of UACP sessions, with all six prior UACP-only entries (Stage 0 + 1 foundational, rename cleanup, Stage 2, Stage 3, GSD 4-7, Stage 8a) migrated verbatim from AVA's `docs/memory/CHANGELOG.md`. Dates, verdicts, and commit hashes preserved.
- `docs/memory/CURRENT.md` — current UACP state with `## Phase` / `## What's done` / `## What's next` / `## Blockers` sections.
- `docs/open-questions.md` — UACP's own open-questions register, numbered fresh from Q1. Migrated from ADR-036's "Open Questions" list.
- `CLAUDE.md`, `AGENTS.md`, `ORCHESTRATION.md` at repo root — executor-coordination docs for Claude Code and Codex agents working on UACP. Tighter than AVA's because UACP's scope is bounded (spec + prototype, no product surface).

**AVA repo pruned in lockstep** (same date): UACP-only CHANGELOG entries removed; UACP-specific top-of-file `CURRENT.md` summaries replaced with a redirect note pointing at this repo; UACP-only open questions removed. Mixed entries (the Stage 0 + 1 foundational session that created ADR-036 in AVA AND scaffolded the spec repo, and the rename cleanup that touched ADR-036's path references) remain in AVA's CHANGELOG with their UACP-specific detail trimmed and pointed here for the full record. ADR-036 itself stays in AVA — it's the AVA-side architectural decision, not a UACP session record.

**Hard rules honored**: did not change spec content (`docs/00-primer.md` through `docs/07-versioning.md` are immutable); did not change prototype code (`prototype/python/` is immutable); did not push either repo; preserved historical accuracy across migrated entries (dates, verdicts, commit hashes intact).

---

## 2026-05-04 — UACP Stage 8b — Slack OAuth prototype + body-predicate envelope handling

Second per-provider session. Validates UACP against Slack and lands the first `fix(spec):` of the prototype-validation arc — adding `failure_predicate` to §3.3 + §4.6 to handle the 200-with-ok=false envelope shape Slack (and GraphQL-flavored / many enterprise APIs) use.

**Spec repo commit chain on top of `fce9de7` (the migration tip)**:

- `b19526e feat(prototype): implement Slack workspace-scoped OAuth` — `auth/oauth2_workspace.py` as a separate AuthMethod from RFC 6749 vanilla. Handles three Slack divergences: scope= + user_scope= at the authorization endpoint (Slack's user-scope extension; comma-separated values); two tokens in the response (bot xoxb-... at access_token + user xoxp-... at authed_user.access_token, plus team_id / bot_user_id / app_id workspace identity); tokens don't expire by default with optional rotation per Slack 2021. PKCE primitives + OAuth2Error re-used from oauth2_authcode.py; no duplication. Per §7.3 in-development extensions, `.uacp` files use `x-oauth2-workspace` as the method identifier. **23 tests**.
- `619078b feat(prototype): handle response envelopes ({ok: bool}) at dispatch` — `dispatch/envelope.py` (resolve_jsonpath + evaluate_failure_predicate + extract_failure_details + select_response_entry) plus integration into `dispatch/client.py` at the 2xx branch. `_check_failure_predicate` selects the matching response entry, evaluates the predicate, extracts provider details, maps the provider code to a canonical Principle 8 code via `_map_envelope_failure_code`, and returns a DispatchError preserving the original HTTP status. spec/models.py gains FailurePredicate pydantic model + a failure_predicate field on ResponseEntry with field_validator enforcing the §3.4 JSONPath subset. **25 tests**.
- `412f7a1 fix(spec): §3.3 + §4.6 — body-predicate failure detection` — additive amendment. §3.3 gains a "Body-predicate failure detection" subsection defining the FailurePredicate shape (path, equals, optional code_path + message_path); §4.6 gains a "Body-predicate evaluation" subsection with a six-step evaluation rule. Predicate is opt-in per response entry; absent predicate retains existing status-only success/failure semantics. Non-breaking per Principle 6 / §7.2 — adding an optional field to ResponseEntry. Pre-amendment v1.x implementations MAY decline silently per §2.8 but SHOULD upgrade.
- `9146d2c feat(prototype): Slack chat.postMessage + conversations.list .uacp + integration tests` — `examples/slack/chat-postMessage.uacp` (POST /api/chat.postMessage with body-predicate detecting Slack's 200+ok=false envelope; idempotency=not_idempotent per Slack's docs explicit no-retry rule), `examples/slack/conversations-list.uacp` (GET /api/conversations.list with cursor pagination via the deeply-nested `$.response_metadata.next_cursor` JSONPath — exercises §3.4's subset against nesting beyond a top-level field). 5 mock-based end-to-end tests + 5 @pytest.mark.integration tests (deselected by default). README updated with Slack setup section.

**The body-predicate gap, the central spec finding of this session**:

Slack returns HTTP 200 regardless of logical outcome and discriminates via `{ok: true|false}` in the body. §3.3 keyed responses by HTTP status, so a 200 from Slack would always be treated as success. The session brief enumerated three handling paths: (A) spec change adding a `failure_predicate` field; (B) prototype-only Slack workaround; (C) status-code re-mapping at dispatch time. Path A chosen because the change is small (~600 words across §3.3 + §4.6), additive, non-breaking, and exactly what Stage 8 prototype validation is supposed to surface. Path B rejected: a fix this small shouldn't land as undocumented divergence. Path C rejected: lossy (the dispatcher's canonical `status` field MUST stay faithful to wire per §4.6 audit posture).

**Aggregate test count**: **241 unit tests passing**, **10 integration tests deselected** by default, 0 failures, 0 errors. Run wall ~0.39s. Module-by-module:

- test_smoke: 1 (unchanged)
- test_spec_loader: 35 (unchanged)
- test_oauth2_authcode: 18 (unchanged)
- test_dispatch_client: 38 (unchanged)
- test_pagination: 16 (unchanged)
- test_lifecycle_state: 26 (unchanged)
- test_refresh: 11 (unchanged)
- test_secrets: 23 (unchanged)
- test_ingest_openapi: 15 (unchanged)
- test_end_to_end_mock: 5 (unchanged Google)
- test_oauth2_workspace: 23 (NEW Stage 8b)
- test_envelope: 25 (NEW Stage 8b)
- test_end_to_end_slack_mock: 5 (NEW Stage 8b)
- providers/test_google: 5 (deselected)
- providers/test_slack: 5 (NEW Stage 8b, deselected)

**Four `.uacp` files validating** through `spec.loader.load()`: gmail-send, google-calendar-list, chat-postMessage, conversations-list — all clean.

**Spec gaps surfaced**: ONE (the body-predicate gap). Closed in this session via path A (additive spec change). No entries to `docs/open-questions.md`.

**Hard rules honored**: did not change Google's existing prototype code (only `spec/models.py` ResponseEntry got a new optional field, and `dispatch/client.py` got the envelope hook in the 2xx branch — those are additive and the 188 Stage 8a tests still pass unchanged); did not relitigate Stage 0-7 spec content beyond the §3.3 + §4.6 amendment which was filed as `fix(spec):`; Slack `.uacp` files validate through the unmodified spec loader (modulo the §3.3 amendment); did not push either repo. Stage 8a's `prototype/python/` work remains intact.

**UACP commit plan**: 4 prototype/spec commits already landed. Single memory commit — `memory: UACP Stage 8b — Slack OAuth prototype + envelope-failure handling`. Operator pushes manually.

---

## 2026-05-04 — UACP Stage 8c — AWS S3 / SigV4 prototype + body-format dispatching

Third per-provider session. Validates UACP against AWS S3, a provider whose authentication shape is structurally unlike OAuth (per-request signing, no flow, triple credential refs) and whose response shape diverges from JSON (binary content for GetObject, XML for ListObjectsV2). Lands the second `fix(spec):` of the prototype-validation arc — adding the `format` discriminator to §3.3 to handle non-JSON response bodies.

**Spec repo commit chain on top of `8de529f` (Stage 8b memory tip)**:

- `c1596d4 feat(prototype): implement AWS SigV4 signing` — full SigV4 from scratch using only stdlib `hashlib` + `hmac`. No boto3 / botocore. Canonical request, string-to-sign, signing-key derivation, Authorization header injection. Verified byte-for-byte against AWS-published IAM ListUsers worked example: kSigning hex matches `c4afb1cc5771d871...86da6ed3c154a4b9`; canonical request matches; string-to-sign matches; final signature `5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7` matches. Edge cases: empty body uses SHA-256 of empty string (NOT UNSIGNED-PAYLOAD; that's only for streaming uploads — flagged as a Stage 9 consideration); query-string multi-value sort by (name, value); header whitespace trimming + sequential-whitespace collapse; STS session token in `x-amz-security-token` participating in signed headers; S3 single-encoding vs non-S3 double-encoding for URL paths; default-port stripping in Host (`:443` for HTTPS); `x-amz-content-sha256` REQUIRED-and-signed only for S3 (matching AWS's IAM test vector exactly which omits it for non-S3 services). **23 tests**.
- `441f44d feat(prototype): handle XML and binary response bodies at dispatch` — `dispatch/body_format.py` with `decode_response_body` (routes by format / media_type) and `xml_to_dict` (stdlib `xml.etree.ElementTree`); `spec/models.py` ResponseEntry validator extended to accept the `format` field; `dispatch/client.py` `_build_success` extended to call format-aware decoder when response entry declares format. The dispatcher stashes the in-flight operation on `_current_operation` so `_build_success` can route through the operation's declared format / failure_predicate. **21 tests**.
- `5d72616 fix(spec): §3.3 — body format discriminator (json / xml / binary / text)` — additive amendment. §3.3 gains a "Body format" subsection between the response-entry shape and "Canonical error envelopes". Defines four permitted format values; XML→dict conversion rules documented for cross-implementation convergence; pagination interaction (response_cursor_path traverses the post-conversion dict); binary and text formats omit schema. Non-breaking per Principle 6 / §7.2.
- `29bbc39 feat(prototype): S3 GetObject + ListObjectsV2 .uacp + integration tests` — `examples/aws/s3-getobject.uacp` (GET /{bucket}/{key} with format=binary response, path-style addressing) and `examples/aws/s3-listobjectsv2.uacp` (GET /{bucket}?list-type=2&... with format=xml response and cursor pagination via `$.ListBucketResult.NextContinuationToken`). 5 mock-based end-to-end tests + 5 @pytest.mark.integration tests. README updated with AWS S3 setup section including security-by-default IAM policy guidance.

**The body-format gap — second clean spec finding of the prototype-validation arc**:

§3.3 modelled response bodies as JSON-only. S3 returns binary content from GetObject and XML from ListObjectsV2; many enterprise APIs return XML, some return text/CSV, image-generation endpoints return binary. Without spec backing, every implementation would diverge. Path A again — additive amendment introducing an optional `format` field on response body objects. Permitted values: `json` (default), `xml`, `binary`, `text`. The XML→dict conversion rules are documented (namespace stripping, `@`-prefixed attributes, repeated children → lists, leaf text → bare string, `#text` for mixed content) so two Conforming Implementations agree on the canonical shape. The same JSONPath subset from §3.4 traverses the post-conversion dict so cursor pagination over XML works without new pagination machinery. Path B (Slack-style provider-specific workaround) was rejected because S3's pattern (binary downloads, XML metadata) recurs across many providers. Path C (out-of-schema Content-Type-only handling at dispatch) was rejected because the schema layer should still describe the body — even if briefly — for §3.4 cursor extraction to work.

**The two "spec gaps" the brief flagged that turned out NOT to exist**:

- §2.7 "single secret_ref per method" — Investigated; doesn't exist. §2.5.1 already declares triple-credential refs (`access_key_ref`, `secret_key_ref`, optional `session_token_ref`) as separate per-method fields. §2.7 only specifies the URI shape; per-method auth blocks have always been free to declare multiple `_ref`-suffixed fields. No spec change needed.
- §2.5.1 "AWS published spec exactly" punt — Investigated; appropriate as written. AWS's spec covers per-service variants (S3 vs non-S3 path encoding; `x-amz-content-sha256` required-only-for-S3); the prototype matches the test vectors. No spec change needed.

**The streaming-upload UNSIGNED-PAYLOAD case** is flagged as a Stage 9 consideration if §4.7 streaming-request semantics are added later. v1's S3 use cases (GetObject + ListObjectsV2) don't need it; PutObject would, but PutObject is out of scope for this session.

**Aggregate test count**: **290 unit tests passing**, **15 integration tests deselected by default**, 0 failures, 0 errors. Run wall ~0.40s. Module-by-module:

- test_smoke: 1 (unchanged)
- test_spec_loader: 35 (unchanged)
- test_oauth2_authcode: 18 (unchanged)
- test_dispatch_client: 38 (unchanged)
- test_pagination: 16 (unchanged)
- test_lifecycle_state: 26 (unchanged)
- test_refresh: 11 (unchanged)
- test_secrets: 23 (unchanged)
- test_ingest_openapi: 15 (unchanged)
- test_end_to_end_mock: 5 (Google, unchanged)
- test_oauth2_workspace: 23 (Stage 8b, unchanged)
- test_envelope: 25 (Stage 8b, unchanged)
- test_end_to_end_slack_mock: 5 (Stage 8b, unchanged)
- test_aws_sigv4: 23 (NEW Stage 8c)
- test_xml_response: 21 (NEW Stage 8c)
- test_end_to_end_aws_mock: 5 (NEW Stage 8c)
- providers/test_google: 5 (deselected)
- providers/test_slack: 5 (deselected)
- providers/test_aws: 5 (NEW Stage 8c, deselected)

**Six `.uacp` files validating** through `spec.loader.load()`: gmail-send, google-calendar-list, chat-postMessage, conversations-list, s3-getobject, s3-listobjectsv2.

**Spec gaps surfaced**: ONE (body format). Closed via path A — additive amendment to §3.3. The two §2.7 / §2.5.1 gaps the brief flagged as candidates were investigated and confirmed non-existent.

**No new entries to `docs/open-questions.md`** — all gaps either closed in spec or flagged as Stage 9 design considerations within existing tracking.

**Hard rules honored**: did not depend on boto3 or botocore (SigV4 from scratch); did not change Google or Slack code (SigV4 is purely additive in `auth/aws_sigv4.py` replacing the stub); .uacp files validate cleanly through `spec.loader.load()` after the §3.3 format-field amendment; did not push the repo. AWS's published SigV4 test suite anchored the implementation correctness — the IAM ListUsers worked example is the load-bearing test vector.

**UACP commit plan**: 4 prototype/spec commits already landed. Single memory commit — `memory: UACP Stage 8c — AWS S3 / SigV4 prototype`. Operator pushes manually.

---

## 2026-05-04 — UACP Stage 8d — GitHub PAT prototype + RFC 8288 link-header pagination

Fourth per-provider session. The shortest of the prototype-validation arc: `api_key_header` is the simplest auth shape in UACP's registry (just inject a header), so the meaty work was tightening the §3.4 `link_header` pagination implementation against RFC 8288's edge cases.

**Spec repo commit chain on top of `feb8ae9` (Stage 8c memory tip)**:

- `3cf63e7 feat(prototype): implement API-key authentication (header + query)` — `auth/api_key.py` with both §2.4 flavors. APIKeyHeaderMethod handles Authorization+Bearer (GitHub PATs, OpenAI, modern OAuth-issued), Authorization+Token (legacy), X-API-Key+empty (vendor-specific), and arbitrary header_name + header_prefix combinations. Header name preserves artifact case; key value passes through unchanged (long keys, base64 +/=, all fine). APIKeyQueryMethod returns query parameter delta + emits §2.4.2 disrecommendation warning via stdlib logging. **17 tests**.
- `5ccd6e0 feat(prototype): implement RFC 8288 link-header pagination at dispatch` — `_parse_link_header` rewritten for full RFC 8288 conformance. Accepts string OR list[str] input. Comma-separated entry splitting respects angle brackets and quoted values (commas inside URIs or quoted titles aren't entry separators). Rel parameter case-insensitive per §3.3; space-separated multiple rels per entry register URI under each. Link parameters beyond rel tolerated. Quoted vs unquoted parameter values both accepted. Relative URIs resolved against the request URL per §3.4. Three helper functions exposed for testing: `_split_link_entries`, `_parse_link_entry`, `_is_absolute_uri`. The link_header pattern caller in `dispatch_paginated` passes the resolution base_url through (most-recent URL when already advanced, falling back to artifact base_url for the first page). **26 tests**.
- `290490e fix(spec): §3.4 — RFC 8288 conformance rules for link_header pagination` — additive clarifying amendment. §3.4 link_header subsection enumerates seven RFC 8288 conformance rules (case-insensitive rel; space-separated multiple rels; multi-Link-header concatenation; relative-URI resolution; link parameters beyond rel tolerated; quoted/unquoted parameter values; commas inside angle-brackets or quotes preserved). Cross-origin termination from §4.4 still applies. The amendment specifies behavior that was implicit but unspecified — Conforming Implementations would have had to handle these cases to interoperate with real providers like GitHub; the spec just makes the requirements normative. Non-breaking per Principle 6 / §7.2.
- `d7b9a8c feat(prototype): GitHub repos.get + repos.listForUser .uacp + integration tests` — `examples/github/repos-get.uacp` (GET /repos/{owner}/{repo} with Authorization: Bearer auth and GitHub's recommended Accept: application/vnd.github+json + X-GitHub-Api-Version: 2022-11-28 default headers) and `examples/github/repos-list-for-user.uacp` (GET /users/{username}/repos with link_header pagination). 6 mock-based end-to-end tests (including a three-page Link-header chain walk that exercises intermediate-page rel=prev/next/first/last and final-page rel=prev-without-rel=next termination) + 5 @pytest.mark.integration tests. README updated with GitHub setup section.

**The §3.4 link_header conformance gap, the central spec finding of this session**:

§3.4's link_header subsection was a one-paragraph punt — it said "follow RFC 8288's `Link` header" without enumerating which RFC 8288 conformance rules a Conforming Implementation MUST satisfy. The Stage 8a parser handled the typical `<url>; rel="next"` case but glossed over rel case-insensitivity, multiple rels per entry, multi-Link-header concatenation, relative-URI resolution, link parameters beyond rel, and the comma-inside-brackets edge case. Without spec backing, every Conforming Implementation would have to figure these out independently and likely diverge on real providers like GitHub. Path A again — additive clarifying amendment.

**The two probe questions the brief flagged that turned out NOT to require spec changes**:

- §2.4.1 "header injection cleanly expressed?" — Investigated; the existing `{method, header_name, header_prefix, key_ref}` shape covers Authorization+Bearer, X-API-Key+empty, Authorization+Token, and arbitrary vendor headers. No amendment needed.
- "Multiple PAT format support stays clean?" — Investigated and confirmed clean. GitHub's three PAT formats (`ghp_`, `github_pat_`, `gho_`) all use the same `Authorization: Bearer <token>` wire shape. UACP doesn't distinguish them; the artifact says "API key in Authorization header"; the runtime injects whatever the secret store resolved. Format-agnosticism by design — exactly the right abstraction.

**Aggregate test count**: **339 unit tests passing**, **20 integration tests deselected by default**, 0 failures. Run wall ~0.45s. Module-by-module:

- test_smoke: 1 (unchanged)
- test_spec_loader: 35 (unchanged)
- test_oauth2_authcode: 18 (unchanged)
- test_dispatch_client: 38 (unchanged)
- test_pagination: 16 (unchanged — original Stage 8a smoke test + general pagination still passes against the upgraded parser)
- test_lifecycle_state: 26 (unchanged)
- test_refresh: 11 (unchanged)
- test_secrets: 23 (unchanged)
- test_ingest_openapi: 15 (unchanged)
- test_end_to_end_mock: 5 (Google, unchanged)
- test_oauth2_workspace: 23 (Stage 8b, unchanged)
- test_envelope: 25 (Stage 8b, unchanged)
- test_end_to_end_slack_mock: 5 (Stage 8b, unchanged)
- test_aws_sigv4: 23 (Stage 8c, unchanged)
- test_xml_response: 21 (Stage 8c, unchanged)
- test_end_to_end_aws_mock: 5 (Stage 8c, unchanged)
- test_api_key: 17 (NEW Stage 8d)
- test_pagination_link_header: 26 (NEW Stage 8d)
- test_end_to_end_github_mock: 6 (NEW Stage 8d)
- providers/test_google: 5 (deselected)
- providers/test_slack: 5 (deselected)
- providers/test_aws: 5 (deselected)
- providers/test_github: 5 (NEW Stage 8d, deselected)

**Eight `.uacp` files validating** through `spec.loader.load()`: gmail-send, google-calendar-list, chat-postMessage, conversations-list, s3-getobject, s3-listobjectsv2, repos-get, repos-list-for-user.

**Spec gaps surfaced**: ONE (§3.4 link_header conformance rules). Closed via path A — additive clarifying amendment.

**No new entries to `docs/open-questions.md`** — link_header rules now spec-anchored; api_key shape needs no amendment; PAT format-agnosticism is a spec property not a gap.

**Hard rules honored**: did not change Google, Slack, or AWS code; did not relitigate Stage 0-7 spec content beyond the §3.4 link_header amendment which was filed as `fix(spec):`; GitHub `.uacp` files validate cleanly through `spec.loader.load()`; did not push the repo; link-header pagination code is in `dispatch/pagination.py` (the generic location) not a GitHub-specific module — Atlassian, GitLab, and many other REST APIs that use the same RFC 8288 pattern get the same parser without changes.

**UACP commit plan**: 4 prototype/spec commits already landed. Single memory commit — `memory: UACP Stage 8d — GitHub PAT prototype + link-header pagination`. Operator pushes manually.

## 2026-05-04 — Stage 8e — UACP repo

- Final per-provider Stage 8 session. Closes the §2.1 auth registry by adding `session_cookie` (the tenth method) for grey-zone provider integrations, and closes §3.8 by implementing the LLM-inferred schema authoring path with the mandatory user-review gate. Validates both against Google NotebookLM, which has no public API and no published OpenAPI.
- **§2.10 Session-cookie authentication** added as a new subsection. Shape: `method: session_cookie`, `tos_acknowledged: true` (literal boolean — string `"true"`, integer `1`, missing field, all rejected at the spec-loader level via Pydantic `model_validator(mode="after")`), `storage_state_ref` (Playwright `storage_state.json` URI), `cookie_names` (whitelist), optional `csrf_token` block (`header_name`, optional `cookie_name` OR `refresh_url` + `extraction_path` + `extraction_format`). Conformance: MUST surface ToS-violation-risk warning to the user before enabling; MUST emit audit log INFO with `risk: tos_violation_potential` per §6.6 on every dispatch; SHOULD recapture every 30 days per §6.7 staleness guidance. §2.1 registry table goes from 9 → 10 entries; §2.9 conformance summary adds the session_cookie row at MAY level + the "MUST NOT load .uacp files using session_cookie without tos_acknowledged: true" rule.
- **§6.1 threat-model** gains a Session-cookie credential theft entry (mitigation: file-mode 0600, no-git-commit, recapture cadence). **§6.7 trust-model** gains a Session-cookie connections subsection (30-day staleness warning; ToS-warning surface MUST through user-facing channel; production deployments route §6.6 audit emit through their pipeline).
- **`auth/session_cookie.py`** implemented from scratch using stdlib + httpx. Exposes `parse_storage_state` (Playwright shape), `filter_cookies_for_url` (RFC 6265 §5.1.3 domain matching + §5.1.4 path matching + secure-flag enforcement), `format_cookie_header`, `inject_cookies` (whitelist filtering with explicit `cookie_names`, raises if no cookies match), `refresh_csrf` (configurable extraction via JSONPath subset OR regex), `SessionCookieMethod` adapter implementing the `AuthMethod` protocol. Emits §2.10 ToS warning via stdlib logging (`uacp.auth.session_cookie`) on construction. 38 unit tests in `test_session_cookie.py`.
- **`connections/ingest_nl.py`** replaces the stub. Exposes `InferenceDraft`, `InferredOperation`, `InferenceProvenance` dataclasses; `LLMCallable` Protocol for pluggable LLM (default `build_default_openrouter_callable` wrapping the OpenRouter API; mock-friendly for tests); `infer_from_description` returns a draft with provenance (`reviewed_at` empty); `confirm_and_persist` REQUIRES `approved=True` else raises `InferenceNotApprovedError`; `refine_inference` preserves operation ids per the §3.8 refinement rule; `SYSTEM_PROMPT` specifies UACP v1.x operation shape rigorously; `_parse_llm_response` strips markdown fences. 26 unit tests in `test_ingest_nl.py`.
- **`dispatch/client.py`** extended with `_apply_auth(op, url, headers, body)` extracted so the §2.10 CSRF refresh-and-retry path re-applies auth correctly (initial implementation just `continue`d after refresh, but the headers dict still carried the original X-Same-Domain value). Dispatcher tracks `_csrf_refreshed_this_call` (one CSRF refresh per dispatch); on 401/403/419 attempts CSRF refresh against the configured `refresh_url`, stores the fresh token on `_csrf_state` so `credentials` resolves with it on the next apply, and continues the loop once. `_emit_session_cookie_audit_event` emits `INFO risk=tos_violation_potential` per §6.6.
- **`spec/models.py`** registered set includes `session_cookie`; `_session_cookie_requires_tos_ack` validator enforces the literal-boolean rule. 4 new tests in `test_spec_loader.py` verifying each rejection path.
- **NotebookLM `.uacp` files** at `examples/notebooklm/`: `list-notebooks.uacp` (POST `/_/NotebookLmRpcs/data/batchexecute?rpcids=wXbhsf` — RPC id reverse-engineered from `notebooklm-py`) and `send-chat-message.uacp` (rpcids=BfMzVf, `idempotency=not_idempotent`). Both carry `source.type=inferred`, `source.model=anthropic/claude-haiku-4.5`, `source.reviewed_at=2026-05-04T06:00:00Z`, the operator's natural-language description preserved verbatim, and `tos_acknowledged: true`. Cookie whitelist matches Google's session set: `SID`, `HSID`, `SSID`, `APISID`, `SAPISID`, `__Secure-1PSID`, `__Secure-3PSID`. CSRF block: `header_name: X-Same-Domain`, `cookie_name: _csrf_token`, `refresh_url`, `extraction_path: $.token`.
- **CLI** gains `capture-storage-state --provider <name> --output FILE` subcommand (stubbed — prints the manual Playwright capture recipe and exits 1; Stage 9+ replaces with an interactive Playwright launch). README documents the operator setup including the 30-day staleness guidance from §6.7 and the inference-path provenance carried by both example artifacts.
- **CURRENT.md** rewritten with the Stage 8e summary at the top; phase moves to "design phase complete; Stage 8 prototype validation **complete**; four spec amendments landed"; what's-next collapses to Stage 9 + Stage 10. Stage 8 status: complete.

**Aggregate test count after Stage 8e**: **414 unit tests passing** (339 Stage 8d baseline + 38 session_cookie + 26 ingest_nl + 4 spec-loader session_cookie tests + 7 NotebookLM end-to-end mocks), **25 integration tests deselected by default** (5 Google + 5 Slack + 5 AWS + 5 GitHub + 5 NotebookLM new), 0 failures. Run wall ~0.46s.

**Per-test-file breakdown after this session**:
- test_oauth2_authcode: 18 (Stage 8a, unchanged)
- test_dispatch_client: 38 (unchanged)
- test_pagination: 16 (unchanged)
- test_lifecycle_state: 26 (unchanged)
- test_refresh: 11 (unchanged)
- test_secrets: 23 (unchanged)
- test_ingest_openapi: 15 (unchanged)
- test_end_to_end_mock: 5 (Google, unchanged)
- test_oauth2_workspace: 23 (Stage 8b, unchanged)
- test_envelope: 25 (Stage 8b, unchanged)
- test_end_to_end_slack_mock: 5 (Stage 8b, unchanged)
- test_aws_sigv4: 23 (Stage 8c, unchanged)
- test_xml_response: 21 (Stage 8c, unchanged)
- test_end_to_end_aws_mock: 5 (Stage 8c, unchanged)
- test_api_key: 17 (Stage 8d, unchanged)
- test_pagination_link_header: 26 (Stage 8d, unchanged)
- test_end_to_end_github_mock: 6 (Stage 8d, unchanged)
- test_session_cookie: 38 (NEW Stage 8e)
- test_ingest_nl: 26 (NEW Stage 8e)
- test_spec_loader: +4 session_cookie tests (Stage 8e)
- test_end_to_end_notebooklm_mock: 7 (NEW Stage 8e)
- providers/test_google: 5 (deselected)
- providers/test_slack: 5 (deselected)
- providers/test_aws: 5 (deselected)
- providers/test_github: 5 (deselected)
- providers/test_notebooklm: 5 (NEW Stage 8e, deselected)

**Ten `.uacp` files validating** through `spec.loader.load()`: gmail-send, google-calendar-list, chat-postMessage, conversations-list, s3-getobject, s3-listobjectsv2, repos-get, repos-list-for-user, list-notebooks, send-chat-message.

**Spec gaps surfaced**: ONE (§2.10 session_cookie registration). Closed via path A — additive registration in §2.1 + new §2.10 subsection + §2.9 / §6.1 / §6.7 cross-references.

**No new entries to `docs/open-questions.md`** — session_cookie shape now spec-anchored at §2.10; LLM-inference review-gate spec-anchored at §3.8; tos_acknowledged enforcement is at the spec-loader level not a gap.

**Hard rules honored**: did not change Google, Slack, AWS, or GitHub code; `tos_acknowledged: true` enforced at the spec-loader level (Pydantic `model_validator`); §2.10 ToS-warning surfaces through user-facing channel (stdlib logging during prototype, audit pipeline in production); LLM-inference path REFUSES to persist without `approved=True`; Playwright capture flow intentionally stubbed (CLI subcommand prints the manual recipe; README documents both the stub and the manual capture path); NotebookLM `.uacp` files validate cleanly through the unmodified spec loader after the §2.10 amendment; did not push the repo.

**UACP commit chain on top of `30f3ea4` (Stage 8d memory tip)**: `7d1067d feat(prototype): implement session_cookie authentication` → `7ca10b6 feat(prototype): implement LLM-inferred schema authoring (§3.8)` → `13ae3ee fix(spec): §2.10 — register session_cookie as v1.x auth method` → `162dfe1 feat(prototype): NotebookLM examples + session_cookie dispatch wiring`. Tip is `162dfe1` until this memory commit lands.

**UACP commit plan**: 4 prototype/spec commits already landed. Single memory commit — `memory: UACP Stage 8e — session_cookie + LLM-inference + NotebookLM prototype`. Operator pushes manually.

## 2026-05-04 — Stage 9 — UACP repo

Closes the design phase. Five spec-repo commits + this memory commit + the `v1.0.0` git tag (created locally on the memory commit, not pushed). Stage 8 + Stage 9 status: complete. v1.0.0 frozen.

### Spec freeze

- **§3.10 placeholder `$schema` URL closed**. Canonical URL pinned at the v1.0.0 git tag: `https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.0.0/schemas/uacp.json`. Future minor releases publish their own MAJOR.MINOR-pinned URLs per §7.1's existing bumping convention. Replacement done via bulk `perl -i -pe` across `docs/03-schema.md`, `docs/07-versioning.md`, `prototype/python/src/`, `prototype/python/tests/`, all 10 example `.uacp` files, plus the prototype's `DEFAULT_SCHEMA_URL` constant. `docs/03-schema.md` §3.10 prose + `docs/07-versioning.md` §7.1 prose updated to drop the placeholder language.
- **`schemas/uacp.json` landed** — JSON Schema 2020-12 artifact validating the wire format. Covers top-level structure (authentication / dispatch / operations / definitions / encrypted_secrets); the ten registered authentication methods per §2.1 with method-specific shape constraints in `allOf`/`if-then` blocks (including the §2.10 session_cookie + literal-true `tos_acknowledged` rule); request method/path/parameters/body shapes per §3.2; response keys per §3.3; the body format discriminator per §3.3 Stage 8c; the FailurePredicate per §3.3 + §4.6 Stage 8b; the four pagination patterns per §3.4; the three source provenance shapes (openapi/curl/inferred) per §3.5–§3.8; the `secret://` URI convention per §2.7; EncryptedSecret with the no-recursion `key_ref` constraint per §6.2. **Verified**: well-formed JSON Schema 2020-12; all 10 example `.uacp` artifacts validate against it. Semantic checks beyond pure structural validation remain the implementation's responsibility per §3.10.
- **SPEC.md rewritten** as a real entry-point document — version + status + released date block; reading order with **Stable** flips for all eight stage docs; conformance summary section linking §2.9, §3.11, §4.9, §5.7, §6.9, §7.7; schema section pointing at `schemas/uacp.json` + the canonical URL; versioning + license/governance pointers; post-freeze changes policy. Was a 25-line placeholder pointing at the per-stage docs.
- **`docs/00-primer.md` Status section** flipped from "active development at v0.1, on a path to v1.0 freeze" to "v1.0.0 is the first stable release, frozen 2026-05-04". Per-stage status table inside the primer flipped Pending → **Stable** for Stages 0–7 (and dropped the Stage 8/9/10 rows since the conformance / prototype / freeze stages are now realized as memory commits + the v1.0.0 tag rather than pending stage docs).
- **README Status section** flipped likewise; mentions the prototype + production-reference path.
- **GOVERNANCE.md "When v2 becomes worth pursuing"** section added with concrete thresholds (3+ deprecated identifiers across registries; structural inadequacy of foundational constraints; adoption-driven feature-request cluster). Maintainer judgment remains the deciding mechanism per §7.6; thresholds are signals not bright lines. Trademark policy line updated to reflect that the policy remains a post-freeze deliverable.
- **`docs/02-authentication.md` §2.0 In-scope** updated from "nine `Authentication Method`s" to "ten" with the §2.10 session-cookie row added.
- **`docs/07-versioning.md` §7.5 v1.0 freeze subsection** rewritten in past tense — describes the design cycle that produced v1.0.0 rather than the prospective freeze gate. §7.1 reconciled with the existing MAJOR.MINOR-pinned-URL convention: the canonical URL pattern is `v1.<minor>.0` git tag, `v1.0.0` today, `v1.1.0` etc as future minor releases land.

### MCP composition validation

- **`prototype/python/src/uacp_prototype/mcp/`** — thin MCP server that walks a directory of `.uacp` files and exposes each operation as an MCP tool. Modules: `__init__.py` (public API), `__main__.py` (CLI entry point), `server.py` (the `UACPServer` class + `LoadedOperation` dataclass + `normalize_tool_name` + `build_dispatch_client_default` factory). Tool name = `<provider>_<operation_id>` with provider derived from the artifact's parent directory; dots / slashes normalize to underscores; 128-char cap enforced by truncation. Tool input schema derives from the operation's request shape per §3.2 — `path_params` / `query` / `body` / `extra_headers` as optional sub-objects matching `DispatchClient.dispatch`'s keyword API. Tool execution dispatches through the existing UACP runtime (`auth/`, `dispatch/`, `lifecycle/`, `security/`) so security and dispatch invariants apply transparently to MCP-side callers. Default credential resolver covers all five wired auth methods (oauth2_authorization_code, x-oauth2-workspace, api_key_header / api_key_query, aws_sigv4, session_cookie) reading from local-keyring + env vars per a documented scheme. Run as `python -m uacp_prototype.mcp --uacp-dir <path>`. Against `examples/` the server advertises 10 tools — one per operation across the five validated providers.
- **`tests/unit/test_mcp_server.py`** — 20 unit tests covering `normalize_tool_name` (safe ASCII, dot/slash stripping, 128-char cap, underscore/hyphen preservation); `load_directory` (10-tool count against examples/, provider resolution, nonexistent dir rejection, malformed-artifact skip); tool-definition derivation (input schema, summary/method in description, query_parameters surface, path_parameters surface); routing (unknown tool → tool_not_found envelope; known tool → DispatchSuccess passes through; DispatchError → canonical envelope; credential-resolution failure → credential_resolution_failed envelope; arguments split correctly into path_params/query/extra_headers); MCP type validity.
- **`tests/integration/test_mcp_composition.py`** — 8 `@pytest.mark.mcp_integration` tests pairing `UACPServer` with the MCP SDK's `ClientSession` over in-process memory streams (`mcp.shared.memory.create_connected_server_and_client_session`, no subprocess required). Tests: server_advertises_expected_tool_count, server_advertises_expected_tool_names, tool_schemas_match_uacp_request_shapes, tool_call_returns_dispatch_success, tool_call_arguments_pass_through_to_dispatch, dispatch_error_propagates_canonically_through_mcp, credential_resolution_failure_surfaces_through_mcp, multiple_providers_dispatch_independently. All 8 pass under `pytest -m mcp_integration`.
- **Adds `mcp_integration` pytest marker** (separate from `integration` because MCP tests use the mcp SDK but no real provider credentials). Adds `mcp>=1.0` to dependencies.

### Documentation

- **`prototype/python/README.md` "MCP Composition" section** — how to start the server, the 10 advertised tools across the five providers, the operation-to-tool naming rules, sample `.claude/mcp.json` config for connecting Claude Code, env-var conventions for credential resolution per auth method, instructions for running the mcp_integration test suite.
- **`RELEASE-v1.0.0.md`** (1396 words) — drafted GitHub release notes. Covers: design phase summary (Stages 0-7), prototype validation (Stage 8: five providers, four amendments tied to their provider rounds), Stage 9 MCP composition validation, what's stable in v1.0, deferred to v1.x, deferred to v2, spec-changes-after-freeze policy, links + acknowledgments. Operator pastes into GitHub's release form when publishing.

### Test count

**434 unit tests passing** (414 Stage 8e baseline + 20 MCP server unit tests), **33 integration tests deselected by default** (25 provider integration unchanged + 8 NEW MCP integration), 0 failures. Run wall ~0.56s. Word counts: SPEC.md 678 words; RELEASE-v1.0.0.md 1396 words.

### Spec gaps surfaced

ZERO. Stage 9 was a freeze + composition validation session; no new spec content was added beyond the v1.0 marker. The §3.10 placeholder `$schema` URL was the only outstanding spec item gating freeze, and it was resolved via the canonical URL choice.

### `docs/open-questions.md` updates

- **Q1 (`$schema` canonical URL)** flipped to **Resolved** — canonical URL captured, `schemas/uacp.json` artifact-creation commit hash recorded, per-minor-tag URL strategy documented.
- **Q2 (trademark policy)** remains Open as a post-freeze deliverable.
- **Q3 (operator-side public release)** remains Open as the operator-action item the external resolution of the canonical `$schema` URL depends on.

### Hard rules honored

Spec freeze happened BEFORE MCP composition (Commit 1 + 2 = spec freeze; Commit 3-4 = MCP work; Commit 5 = docs; Commit 6 = memory). v1.0.0 tag is the freeze marker (created locally on the memory commit, not pushed). Canonical `$schema` URL chosen and propagated. Did not push the repo or tag. MCP server wrapper is additive (no refactoring of existing prototype code beyond the auth method imports the server pulls in to wire credentials).

### UACP commit chain on top of `df4c2ef` (Stage 8e memory tip)

- `827e823 feat(spec): canonical $schema URL + schemas/uacp.json artifact`
- `638579d feat(spec): v1.0 freeze — finalize SPEC.md, README, GOVERNANCE, per-stage status`
- `125da94 feat(prototype): MCP server wrapper exposing UACP operations as MCP tools`
- `c01bbcb test(prototype): MCP composition validation against MCP Python SDK`
- `0a3f4be docs: MCP composition section in prototype README + GitHub release notes draft`

This memory commit will land on top of `0a3f4be`. The `v1.0.0` git tag is created locally pointing at the memory commit.

### Operator action items

(1) push the local commits to `origin/main`; (2) push the `v1.0.0` tag (`git push origin v1.0.0`); (3) make the GitHub repo public if not already (the canonical `$schema` URL doesn't resolve externally until then); (4) create the GitHub release using the drafted notes from `RELEASE-v1.0.0.md`.

### UACP commit plan

5 prototype/spec commits already landed. Single memory commit — `memory: UACP Stage 9 — v1.0 spec freeze + MCP composition validation`. Then `git tag -a v1.0.0 -m "..."`. Operator pushes manually.
