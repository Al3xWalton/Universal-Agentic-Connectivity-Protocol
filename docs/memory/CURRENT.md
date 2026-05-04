# CURRENT — where UACP is right now

**Last updated**: 2026-05-04 — **Memory + executor-coordination migrated into the UACP repo.** UACP is now self-documenting as a standalone public personal-project repo (`github.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol`); prior session memory has been carried over from AVA's `docs/memory/CHANGELOG.md`. The most recent prior session was **Stage 8a — Google OAuth prototype + Gmail/Calendar validation**: the Python reference implementation lives at `prototype/python/`, validates the MUST surface across Stages 2-6, and ships with two `.uacp` files (`gmail-send.uacp`, `google-calendar-list.uacp`), 188 unit tests passing, and 5 integration tests for operator-supplied OAuth credentials. Spec repo tip is `a622a1f` (Stage 8a final commit) until this migration's commits land.

## Phase

**Design phase complete; prototype validation in progress.**

- Stages 0-7 (Primer, Principles, Authentication, Schema, Dispatch, Lifecycle, Security, Versioning) are all written, committed, and indexed Complete in `SPEC.md` + `docs/00-primer.md`.
- Stage 8 — prototype validation across providers — is in progress. **Stage 8a (Google: Gmail send + Calendar list, OAuth 2.0 authcode + cursor pagination) is done.** Stages 8b-8e are pending.
- Stage 9 (prototype freeze, canonical `$schema` URL, conformance test suite) is the next meaningful gate after the per-provider sessions.
- Stage 10 (production reference implementation in AVA's `backend/services/connections-broker/`) is the terminal stage and is gated by Stage 9 freeze.

## What's done

- **Spec docs (Stages 0-7)** — eight design documents under `docs/`, totaling ~36k words across the eight design stages, RFC voice throughout, conformance summaries (MUST / MUST NOT / SHOULD / MAY) tabulated at the end of every numbered stage.
- **Repo scaffolding** — README, SPEC, GOVERNANCE, CONTRIBUTING, CODE_OF_CONDUCT, NOTICE, LICENSE (Apache 2.0) at repo root.
- **Stage 8a Google prototype** — Python reference implementation at `prototype/python/`. Modules: `spec/` (loader + validator + pydantic models), `auth/oauth2_authcode.py` (PKCE S256, four-step flow), `dispatch/` (HTTPS dispatch + retry + pagination + canonical error shape), `lifecycle/` (state machine + lazy refresh + atomic rotation), `security/` (AES-256-GCM envelope encryption + filesystem-simulated `local-keyring`), `connections/ingest_openapi.py` (canonical OpenAPI 3.x + Google discovery doc). 188 unit tests, 5 integration tests deselected by default. Two example `.uacp` files (`examples/google/`).
- **Stub plan** — non-Google methods explicitly stubbed with NotImplementedError messages naming the future provider session that fills them in: `auth/aws_sigv4.py` (Stage 8c), `auth/custom.py` (Stage 8e), `connections/ingest_curl.py` (Stage 8b), `connections/ingest_nl.py` (Stage 8e); secret-store resolvers `vault` and `aws-secrets-manager` are stubs in `security/secrets.py`.
- **AVA-side anchor** — `docs/adr/ADR-036-uacp.md` in the AVA monorepo records the AVA architectural decision to build UACP. ADR-036 stays in AVA; UACP's `docs/memory/` is the implementation/session record.

## What's next

1. **Stage 8b — Slack provider session.** Adds `curl`-paste parsing per §3.7 and the first non-OAuth provider integration. Auth method: most likely `oauth2_authorization_code` (Slack supports it) or `api_key_header` for a Slack bot token. Adds operations like `chat.postMessage` and `conversations.history` (cursor-paginated).
2. **Stage 8c — AWS S3 provider session.** Implements `auth/aws_sigv4.py` (`aws_sigv4` per §2.5.1) and the AWS Secrets Manager secret-store resolver. Adds operations against S3 (`PutObject`, `GetObject`, `ListObjectsV2`).
3. **Stage 8d — GitHub provider session.** Exercises `link_header` pagination per §3.4 (GitHub uses RFC 8288 `Link: rel="next"`). Auth method: `oauth2_authorization_code` (GitHub OAuth Apps) or `api_key_header` (Personal Access Tokens).
4. **Stage 8e — custom-auth + LLM-inference session.** Implements `auth/custom.py` per §2.6 against a non-trivial provider whose authentication doesn't fit any registered method (e.g., a banking API with mTLS + body-digest), and implements `connections/ingest_nl.py` per §3.8 with the mandatory user-review gate.
5. **Stage 9 — Prototype freeze.** Canonical `$schema` URL pinned (the placeholder `https://uacp.spec/v1/schema.json` is replaced with the production URL); conformance test suite extracted from the prototype's unit + integration tests.
6. **Stage 10 — Production reference implementation in AVA.** The prototype's design migrates into a Ring 3 service at `backend/services/connections-broker/` in the AVA monorepo. Coexists with the curated `IntegrationProvider` set per ADR-035; UACP handles the long tail.

## Blockers

None.

The `$schema` canonical-URL deferral to Stage 9 freeze (Q1 in `docs/open-questions.md`) is a known forward-pointer and the placeholder allows artifacts to be authored against a stable shape today; not a blocker.

The integration tests in Stage 8a require operator-supplied Google OAuth credentials per the setup steps in `prototype/python/README.md`; running them is operator-driven and out of session scope. Not a blocker for prototype work; required for live-provider validation before Stage 9 freeze.
