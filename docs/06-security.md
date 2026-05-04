# UACP Security Model

This document specifies the security model of UACP `v1.x`. It defines the threat model, the secret-store registry that resolves the `secret://` references introduced in Stage 2, the encryption-at-rest requirement for persisted credentials, the credential lifecycle from generation through deletion, the local scope-enforcement check that defends against accidental scope creep, the audit-logging requirements, the trust posture for ingested artifacts, and the compliance posture a `Conforming Implementation` carries by virtue of meeting the spec's requirements. The conformance keywords ("MUST", "MUST NOT", "SHOULD", "SHOULD NOT", "MAY") in this document are interpreted per BCP 14 [[RFC2119](https://datatracker.ietf.org/doc/html/rfc2119)] [[RFC8174](https://datatracker.ietf.org/doc/html/rfc8174)] as established in [Stage 0 — Primer](./00-primer.md).

This document is consistent with the foundational principles in [Stage 1 — Principles](./01-principles.md), the authentication subsystem in [Stage 2 — Authentication](./02-authentication.md), the schema and discovery layer in [Stage 3 — Schema](./03-schema.md), the dispatch runtime in [Stage 4 — Dispatch](./04-dispatch.md), and the connection lifecycle in [Stage 5 — Lifecycle](./05-lifecycle.md). Where this document refines a principle, it does so by narrowing detail; it does not override.

## 6.0 Overview

Stage 6 closes UACP's security story. The earlier stages establish a structural posture — credentials referenced not embedded (Stage 2), schemas validated for credential-free content (Stage 3), dispatch over HTTPS with TLS minimums (Stage 4), lifecycle that handles rotation and revocation (Stage 5) — and this stage specifies the controls that make the structural posture load-bearing in production: the threat model that names what UACP defends against, the secret-store registry that resolves credential references, the encryption requirement for credentials at rest, the credential lifecycle that prevents orphaned material, the local scope-enforcement check that prevents accidental privilege escalation through artifact edits, the audit logging that makes operational security observable, and the trust posture for the three schema sources from Stage 3.

This is the last stage that establishes normative security requirements. Stage 7 covers versioning of the spec itself, which has its own security implications (the `$schema` URL must be authentic), but the security closure for `v1.x` artifacts and `Conforming Implementation`s lives here.

### In scope

- Threat model and the explicit out-of-scope list (§6.1).
- Secret-store registry: resolution semantics for `vault`, `aws-secrets-manager`, `local-keyring`, `inline-encrypted` (§6.2).
- Encryption-at-rest: when credentials must be encrypted, the recommended algorithm, key rotation via envelope encryption (§6.3).
- Credential lifecycle: generation, persistence, rotation, revocation, deletion; the orphaned-credential prohibition (§6.4).
- Scope enforcement at dispatch time: the local check that catches over-broad operations before the wire request (§6.5).
- Audit logging: the events that MUST be logged, the per-event field set, the integrity expectations (§6.6).
- Trust model for ingested artifacts: OpenAPI ingestion warnings, `curl`-paste user trust, LLM-inference review and destructive-verb highlighting (§6.7).
- Compliance posture: how `Conforming Implementation`s map to common compliance frameworks (§6.8).
- Conformance summary (§6.9).

### Out of scope

Stage 6 is the security closure stage; few things are deferred from here. The boundaries that remain:

- **Versioning security.** The integrity of the `$schema` URL — how an implementation knows it has loaded a genuine UACP `v1.x` schema and not a malicious substitute — is a property of the spec's distribution and is bounded by **Stage 7 (versioning)** and **Stage 9 (prototype freeze)** when the canonical URL is finalized.
- **Implementation-internal security.** Memory protection during in-memory credential handling, side-channel resistance in cryptographic operations, supply-chain attestation for the implementation's own dependencies, and physical-host security are properties of the implementation's deployment environment. UACP's threat model includes these as *out of scope*; they are not absent from the security posture, but they are the implementation's responsibility, not the protocol's.
- **Cryptographic primitives.** Specific algorithms for encryption-at-rest are recommended (§6.3) but not normatively mandated; the spec specifies *that* credentials are encrypted and *what properties* the encryption must satisfy, not *which cipher*. The recommendation is AES-256-GCM as a sensible default.

## 6.1 Threat model

UACP's threat model enumerates the attacks the protocol's design defends against and the defenses that block them. Threats outside this list are not in UACP's scope; they are not implicitly addressed.

### Threats UACP defends against

- **Credential theft from `.uacp` artifacts.** A `.uacp` artifact is publicly shareable per Principle 10. An attacker who obtains a copy of an artifact must not gain access to the underlying `Provider` account.
  - *Defense*: §2.7's credential-reference convention requires every credential-shaped field to be a `secret://` URI; embedded plaintext credentials are forbidden, and §3.10's validation rejects artifacts containing them. An obtained artifact yields the structure of the integration but no credentials.

- **Credential theft from server compromise.** An attacker who gains read access to the implementation's storage backend must not be able to extract usable credentials.
  - *Defense*: §6.3's encryption-at-rest requirement. Credentials persisted by the implementation (per Stage 5 §5.6) are encrypted with a key the storage backend itself does not hold. An attacker with read-only storage access obtains ciphertext and must additionally compromise the key-management surface to recover plaintext.

- **Over-broad authorization escalation through artifact edits.** An attacker (or a misbehaving authoring tool) modifies a `.uacp` artifact to add operations whose required scopes exceed the consent the user originally granted. Without a defense, the runtime would dispatch the over-broad operation and rely on the `Provider` to reject it; some `Provider`s do not enforce scope strictly at every endpoint, and the dispatch could succeed.
  - *Defense*: §6.5's local scope-enforcement check. The dispatcher MUST verify that an operation's required scopes are within the credential's granted scopes before issuing the wire request, using the locally-recorded scope set from the consent flow. A scope mismatch fails locally with `forbidden` and the canonical code `INSUFFICIENT_SCOPE_LOCAL`, distinct from `Provider`-side scope rejection.

- **Replay attacks on signed-request schemes.** An attacker captures a signed request (from network observation or log compromise) and replays it against the `Provider`. The `Provider` accepts the replayed request because the signature validates; the attacker has effected the original action without holding the credentials.
  - *Defense*: Signed-request schemes registered in §2.5 — AWS SigV4 and `hmac_signature` — embed a timestamp in the signed canonical representation. The `Provider`'s acceptance window is bounded (typically a few minutes); replays outside the window fail at the `Provider`. UACP's responsibility is that timestamps are present in signatures and that the dispatch runtime does not strip them; both are satisfied by Stage 2's signing rules and Stage 4's authentication-applied-last composition order (§4.1).

- **Audit-trail evasion.** An attacker (or a careless operator) tampers with the audit log to hide a credential-leak event, an unauthorized dispatch, or a state-machine transition.
  - *Defense*: §6.6's audit-log integrity expectations. Audit logs SHOULD be append-only — either via append-only storage (write-once-read-many backends) or via cryptographic chaining (each entry includes a hash of the prior entry, making tampering detectable). The `SHOULD` recognizes that not every implementation has an append-only storage option; the integrity expectation is the floor.

- **Supply-chain attacks on `.uacp` files.** A malicious or hijacked source distributes `.uacp` artifacts that misdirect dispatch (a crafted `base_url` pointing at an attacker-controlled service that proxies to the real `Provider` while logging credentials), over-claim scopes, or include destructive operations whose effects the user has not anticipated.
  - *Defenses*: (a) Stage 3's schema validation (§3.10) prevents malformed artifacts from loading and prevents embedded credentials from being smuggled. (b) §6.7's trust model for ingested artifacts requires implementations to warn the user when the source's origin is suspicious (an OpenAPI URL that doesn't match the `Provider`'s known canonical domain, an LLM-inferred operation that includes a destructive verb). (c) The mandatory user review for LLM-inferred schemas (§3.8) is the human-in-the-loop check against agent-injected malicious operations.

### Threats explicitly out of scope

The following are recognized threats that are NOT in UACP's scope. Implementers facing them are referred to their host-environment's security controls.

- **Post-compromise blast-radius reduction within the agent's runtime.** If the agent's process is compromised, the attacker has access to whatever credentials the agent's process can resolve. UACP's encryption-at-rest defense protects against storage-only compromise; runtime compromise is the agent host's concern (memory protection, sandboxing, principal-of-least-privilege).
- **Nation-state-class adversaries.** Adversaries with the resources to compromise certificate authorities, plant hardware backdoors, or perform supply-chain attacks on cryptographic libraries are outside UACP's threat model. UACP's defenses are commercial-grade; resistance to state-actor-level adversaries requires controls beyond protocol design.
- **Side-channel attacks on implementation cryptography.** Timing attacks, cache-line attacks, electromagnetic-emission attacks on the implementation's signing operations are properties of the implementation's cryptographic library, not of the protocol. UACP recommends using vetted cryptographic libraries; the recommendation does not extend to a normative requirement on side-channel resistance.
- **Denial of service against the `Provider`.** UACP's rate-limit handling (§4.5) attempts to be a good citizen, but UACP cannot prevent a malicious caller from issuing legitimately-authenticated requests at a rate that overwhelms a `Provider`. The `Provider`'s rate limits and the implementation's policies are the lines of defense.
- **`Provider`-side bugs.** Bugs in the `Provider`'s authentication or authorization implementations are outside UACP's scope. UACP can warn the user when an integration looks suspicious (the `.uacp` artifact's documented behavior diverges from the actual `Provider` behavior); it cannot fix the `Provider`.

## 6.2 Secret store registry

Stage 2 §2.7 introduced the `secret://<store>/<id>` reference convention and named four registered stores for `v1.0`. This section specifies each store's resolution semantics, the URI shape, the failure modes on resolution failure, and the implementation expectations.

### `vault` — HashiCorp Vault

URI shape: `secret://vault/<vault-path>`

Resolution: The `<vault-path>` is interpreted as a Vault KV v2 path. The implementation issues a Vault API request — typically `GET v1/secret/data/<vault-path>` — using its configured Vault token. The response's `data.data` field carries the secret material; the implementation uses the field's keys to resolve sub-fields (a single Vault path may carry multiple secrets, e.g., `client_id` and `client_secret` under one path).

Sub-field selection: When the Vault path holds multiple fields, a `.uacp` artifact MAY use a fragment-style selector: `secret://vault/<vault-path>#<field>` resolves to the named field within the Vault object. The fragment is not a JSON Pointer; it is a single-field selector. Multi-level selection (selecting a field within a nested structure) is not supported in `v1.0`; implementations needing it MUST flatten the Vault structure or use a different store.

Configuration: The implementation's Vault configuration — server URL, auth method, namespace — is implementation-defined and outside UACP's scope. UACP requires only that the implementation be able to resolve a `secret://vault/...` URI to plaintext at dispatch time.

Failure on resolution: When the Vault path does not exist, when the implementation's Vault token has insufficient permission, or when Vault is unreachable, the implementation MUST surface the failure as `auth_expired` with a diagnostic message indicating the secret-store path that failed to resolve. The dispatch MUST NOT proceed without the credential.

### `aws-secrets-manager` — AWS Secrets Manager

URI shape: `secret://aws-secrets-manager/<secret-name>` or `secret://aws-secrets-manager/<secret-name>?version=<version>`

Resolution: The `<secret-name>` is the name (or ARN suffix) of an AWS Secrets Manager secret. The implementation issues a `GetSecretValue` API call. The response's `SecretString` field carries the secret material.

Sub-field selection: AWS Secrets Manager allows a secret value to be a JSON object. Sub-field selection follows the same fragment convention as `vault`: `secret://aws-secrets-manager/<secret-name>#<field>` selects the named field from the parsed JSON object.

Versioning: The optional `version` query parameter selects a specific secret version. When omitted, the implementation requests `AWSCURRENT`. The query-parameter form is canonical; implementations MUST support both the bare URI and the versioned URI.

Configuration: AWS credentials and region come from the implementation's AWS configuration (typically the SDK's default credential chain); they are not encoded in the URI.

Failure on resolution: Same as `vault`.

### `local-keyring` — OS-level keyring

URI shape: `secret://local-keyring/<service>/<account>`

Resolution: The implementation uses the host operating system's keyring API:

- macOS: Keychain Services. The `<service>` is the keychain item's `kSecAttrService`; the `<account>` is `kSecAttrAccount`.
- Windows: Credential Manager. The `<service>/<account>` pair is hashed or concatenated into the credential's target name per the implementation's convention.
- Linux: Secret Service via D-Bus (GNOME Keyring, KWallet). The `<service>` and `<account>` are stored as attributes on the secret item.

Cross-platform implementations SHOULD use a library that abstracts the per-OS surface (`keyring` in Python, `keytar` in Node.js, equivalents in other ecosystems).

The `<service>` and `<account>` segments together form the keyring's lookup key. Sub-field selection is not supported; each keyring entry holds a single secret.

Failure on resolution: Same as `vault`. Note that `local-keyring` failures often indicate the user's keyring is locked (the user has not unlocked it after login); the implementation SHOULD surface the locked-keyring case distinguishably so the user can take recovery action.

### `inline-encrypted` — Encrypted blob in the `.uacp` artifact

URI shape: `secret://inline-encrypted/<blob-id>`

Resolution: The `inline-encrypted` store carries the encrypted secret inside the `.uacp` artifact itself, as part of an `encrypted_secrets` block at the artifact's top level. The `<blob-id>` selects which entry within `encrypted_secrets` to resolve. The encryption key comes from another secret store; the artifact's `encrypted_secrets` block declares how to find it.

The artifact's shape:

```json
{
  "encrypted_secrets": {
    "<blob-id>": {
      "ciphertext": "<base64-encoded ciphertext>",
      "algorithm": "AES-256-GCM",
      "key_ref": "secret://vault/example/inline_key",
      "iv": "<base64-encoded IV>",
      "tag": "<base64-encoded auth tag>"
    }
  }
}
```

The implementation:

1. Recursively resolves `key_ref` (which MUST point at a different store than `inline-encrypted` to avoid resolution cycles; resolving an `inline-encrypted` URI through another `inline-encrypted` URI is forbidden).
2. Decrypts `ciphertext` using the resolved key, the declared `algorithm`, the `iv`, and the `tag`.
3. Returns the decrypted plaintext as the resolved credential.

Use case: `inline-encrypted` is the migration store. It exists for cases where the artifact must travel with its credentials — typically during the bootstrap of a new implementation against an existing credential set, or when the implementation needs to ship credentials alongside the artifact through a single channel. Once the migration completes, the `inline-encrypted` references are typically rewritten to point at one of the other stores.

Failure on resolution: A failure in any step (key not resolvable, ciphertext decryption fails, auth tag mismatch) is `auth_expired` with the same diagnostic posture as the other stores.

### Common rules across stores

- A `Conforming Implementation` MUST recognize the `secret://` scheme and MUST attempt resolution against the registered store named by `<store>`.
- A `Conforming Implementation` MUST resolve credentials at dispatch time, not at artifact load time. Eager resolution at load time would aggregate credentials in the implementation's process memory longer than necessary.
- A `Conforming Implementation` MUST NOT cache resolved plaintext credentials beyond the duration of the dispatch call (and any associated retry sequence within that call). The implementation MAY cache resolved credentials within a single dispatch call's lifetime to avoid double-resolution; it MUST clear the cache when the dispatch resolves.
- A `Conforming Implementation` MAY register additional stores for proprietary or cloud-specific surfaces (`gcp-secret-manager`, `azure-key-vault`, `kubernetes-secrets`); registration follows §2.8's extension mechanism.

## 6.3 Encryption-at-rest

When a `Conforming Implementation` persists credentials — either directly (the access token, the refresh token, the API key) or indirectly (via the `inline-encrypted` store, which carries ciphertext at rest in the artifact) — the credentials MUST be encrypted at rest.

### Normative requirement

A `Conforming Implementation` MUST NOT persist plaintext credentials. The `MUST NOT` is absolute: temporary plaintext caches in memory are permitted (per §6.2's per-call cache rule), but durable storage MUST hold ciphertext only.

The encryption MUST satisfy:

- **Confidentiality.** A reader of the ciphertext without the decryption key MUST NOT be able to recover the plaintext.
- **Integrity.** Modifications to the ciphertext MUST be detectable. Authenticated-encryption modes (AEAD) are the natural fit.
- **Per-`Connection` separation.** Compromise of the ciphertext for one `Connection` MUST NOT compromise the ciphertext for other `Connection`s. This is achievable through per-`Connection` data-encryption keys (the envelope-encryption pattern below) or through any other scheme that satisfies the cryptographic-isolation property.

### Recommended algorithm

The recommended cipher is **AES-256-GCM** (256-bit key, Galois/Counter Mode for AEAD). AES-256-GCM is widely available, performant, side-channel-resistant in well-vetted implementations, and provides confidentiality and integrity in a single pass. ChaCha20-Poly1305 is an acceptable alternative.

The recommendation is normative as the floor: implementations that use a weaker algorithm (AES-128, CBC mode without HMAC, RC4, etc.) are not conforming. Implementations MAY use a stronger algorithm if such an algorithm becomes standard during `v1.x`'s lifetime.

### Key rotation

Implementations SHOULD support **key rotation** of the encryption key without requiring re-authentication of every `Connection`. The standard pattern is **envelope encryption**:

1. Each `Connection`'s credentials are encrypted with a per-`Connection` *data encryption key* (DEK).
2. The DEK is itself encrypted with a *key encryption key* (KEK), which is the master key managed in the implementation's key-management surface.
3. The encrypted DEK is stored alongside the ciphertext credentials.
4. Rotation rotates the KEK only: each DEK is decrypted with the old KEK, re-encrypted with the new KEK, and stored. The credentials themselves are untouched; the ciphertext remains valid.

Envelope encryption decouples the volume of ciphertext-at-rest from the rotation cost. Rotating the KEK is cheap (one DEK decrypt-encrypt per `Connection`); rotating the actual credentials would require re-authenticating each `Connection`, which is expensive for the user.

The KEK itself is managed in the implementation's key-management surface — AWS KMS, GCP KMS, HashiCorp Vault Transit, an on-prem HSM, etc. UACP does not specify which; the requirement is that the KEK is held in a system that supports its own access controls and rotation.

### Key-rotation events

Key-rotation events are audit events; the audit-event schema is in §6.6. Rotation events MUST be logged, including the rotation timestamp, the actor that initiated the rotation (a system schedule, a security-incident response, etc.), the old key identifier, and the new key identifier. The credential plaintexts MUST NOT appear in the rotation audit event.

### What encryption-at-rest does not protect

Encryption-at-rest protects against storage-only compromise — an attacker reading the database, the filesystem, or the backup media. It does NOT protect against:

- Runtime compromise of the implementation's process. A process that has the KEK can decrypt; an attacker inside the process gets plaintext.
- Compromise of the key-management surface itself. If the AWS KMS account, the Vault root, or the HSM is compromised, the KEK is exposed and the encryption is bypassed.
- Side-channel observation of the encryption operation. Out of scope per §6.1.

The threat model is explicit about these boundaries; encryption-at-rest is one defense, not the only defense.

## 6.4 Credential lifecycle

Credentials traverse a lifecycle from generation through deletion. UACP's posture across the lifecycle:

### Generation

UACP does not generate credentials. The `Provider`'s authorization server (for OAuth flows), the user (for API-key paste), or an external system (for AWS SigV4 keys generated through AWS IAM) is the credential source. UACP receives credentials, persists them per §6.3, and references them through `secret://` URIs.

### Persistence

Persistence is encrypted per §6.3. The persistence target is the `secret://` store the artifact references. Persistence MUST be atomic per §5.3 in the rotation case: a partial-write that loses credentials is the connection-killing bug Stage 5 enumerates.

### Rotation

Two flavors of rotation:

- **Refresh-token rotation** per §5.3 — the `Provider` rotates refresh tokens on each refresh; the implementation atomically replaces.
- **Periodic forced rotation** — the implementation's own policy may schedule rotation at fixed intervals (90 days is a common choice for static API keys; refresh tokens are typically not on a forced schedule because they rotate on use). Forced rotation for OAuth-flow credentials means re-authenticating the `Connection` (per §5.5) to mint fresh tokens; for static API keys it means generating a new key at the `Provider` and updating the secret-store entry.

UACP does not specify a forced-rotation cadence. Implementations choose per their security posture.

### Revocation

Revocation per §5.4 transitions the `Connection` to `revoked` and stops accepting dispatches. The credentials in the secret store remain present until the `Connection` is deleted (below); they are unusable but not yet purged. Implementations MAY immediately overwrite the credential ciphertext on revocation as defense-in-depth; this is permitted and a reasonable choice.

### Deletion

When a `Connection` is permanently deleted — the user removes it, an automated retention policy expires it, an account closure cascades through — the implementation MUST delete every associated secret-store entry. Orphaned credentials (a `Connection` deleted with its credentials still present in the secret store) are a **MUST NOT**: they accumulate as unattributable secrets, complicate audit, and represent latent compromise risk if the secret store is breached.

The deletion sequence:

1. The `Connection`'s state is recorded as deleted in the implementation's persistence (or the persistence record is removed entirely, depending on the audit posture).
2. Every `secret://` URI referenced by the `Connection` has its target deleted from the corresponding store. Some stores support hard delete (Vault `vault delete`, AWS Secrets Manager `DeleteSecret` with `ForceDeleteWithoutRecovery`); some retain a tombstone for a recovery window (the default for AWS Secrets Manager). The implementation MAY use either, but MUST ensure the credential is not resolvable through `secret://` after the deletion completes.
3. The deletion is logged (per §6.6).

Implementations targeting GDPR-style data-deletion compliance SHOULD use hard-delete options on the underlying stores; tombstone deletes remain GDPR-compliant if the recovery window is bounded and the recovered credential is itself encrypted with a key that has been rotated, but hard-delete is the simpler posture.

### Lifecycle audit

Each lifecycle event — generation (the credential first arrived), persistence (the credential was written), rotation (the credential was replaced), revocation (the `Connection` was revoked), deletion (the credential was destroyed) — is an audit event per §6.6. Implementations MUST emit these events; the event schema is in §6.6.

## 6.5 Scope enforcement at dispatch time

A `Connection`'s OAuth scopes (or the equivalent for non-OAuth methods — granted permissions, IAM roles, API-key permissions) define what the credential is authorized to do. The dispatch runtime MUST verify that the operation it is about to dispatch is within the credential's authorized scope set, *locally*, before issuing the wire request.

### The check

When a dispatch begins, the runtime:

1. Reads the operation's required scope set. The scope set is declared in operation metadata (a `required_scopes` field on the operation, optional but used when present) or inferred from the `Provider`'s mapping of operations to scopes (some artifacts annotate operations with scope hints derived from the `Provider`'s documentation during authoring).
2. Reads the `Connection`'s granted scope set. This is persisted per Stage 5 §5.6's optional `scopes_granted` field; when an implementation does not persist granted scopes, the local check degrades to a no-op for that `Connection` and the runtime relies on the `Provider`'s remote enforcement.
3. Computes the difference: required scopes that are not in the granted set.
4. If the difference is empty, the dispatch proceeds.
5. If the difference is non-empty, the dispatch fails locally with `forbidden` and the canonical code `INSUFFICIENT_SCOPE_LOCAL`. The wire request is NOT issued.

### Distinguishability

The local-rejection code `INSUFFICIENT_SCOPE_LOCAL` MUST be distinguishable from the `Provider`-side rejection code, which is the canonical code `INSUFFICIENT_SCOPE_REMOTE` (or `forbidden` more generally) attached to a `403` response from the `Provider`. The two codes differ in semantic:

- `INSUFFICIENT_SCOPE_LOCAL` means "the implementation knew the operation's scope requirement exceeded the granted scopes and refused to call." This is the defense against accidental scope creep through artifact edits.
- `INSUFFICIENT_SCOPE_REMOTE` means "the implementation called and the `Provider` rejected." This is the `Provider`'s authoritative answer when the local check did not catch the issue (because granted scopes were not persisted, because the operation's required scopes were not declared, or because the `Provider`'s scope mapping diverged from the artifact's).

Distinguishability matters because the recovery path differs. `INSUFFICIENT_SCOPE_LOCAL` is recoverable by re-authenticating with broader scopes; `INSUFFICIENT_SCOPE_REMOTE` is recoverable by the same path *if* the user's broader scope grant is accepted by the `Provider`, but the divergence between local and remote scope sets often indicates a deeper problem (the artifact has stale scope information; the `Provider` changed its policy) that warrants investigation.

### Why local enforcement matters

A `.uacp` artifact is editable. An attacker (or a misbehaving authoring tool) who gains the ability to modify an artifact can add operations whose required scopes exceed the original consent. The artifact would still validate against the schema (Stage 3 doesn't enforce scope-vs-grant matching at validation), and the dispatch runtime would otherwise issue the wire request and rely on the `Provider`'s rejection. Some `Provider`s are inconsistent in scope enforcement at the operation level — a token granted only `read:user` may successfully call an operation documented as requiring `write:user` if the `Provider`'s authorization server didn't tag the endpoint correctly.

The local check is a pre-flight validation that catches the discrepancy in the implementation, where it is recoverable, instead of trusting it to be caught by the `Provider`, where some `Provider`s fail soft.

### Limitations

Local scope enforcement requires that the granted scope set is persisted (Stage 5 §5.6 makes this `MAY` rather than `MUST`) and that operations declare their required scope set. When either is missing, the local check degrades to no-op and the runtime relies on the `Provider`. Implementations that take security seriously SHOULD persist granted scopes and SHOULD have authoring tools that derive operation scope requirements from the `Provider`'s documentation when ingesting OpenAPI (the OpenAPI security-requirements block is the natural source).

## 6.6 Audit logging

A `Conforming Implementation` MUST log a defined set of events to an implementation-chosen destination. The events, the per-event field set, and the integrity expectations are normative; the destination (stdout, syslog, a structured log pipeline, a SIEM) is implementation-defined.

### Events that MUST be logged

- **`Connection` state transitions.** Every transition in the §5.1 state machine. The event captures the from-state, the to-state, the trigger (per the §5.1 transition table), and the timestamp.
- **Credential lifecycle events.** Generation (§6.4), persistence write, rotation (`refresh_token` rotation per §5.3 and forced rotation), revocation, deletion.
- **Dispatch attempts.** One log per dispatch *call* (not per retry; the call is the unit), regardless of outcome. The event captures the operation `id`, the `Connection` `id`, the canonical outcome code (per §4.6), the wall-clock duration, and whether retries were performed.
- **Scope-enforcement rejections.** Every `INSUFFICIENT_SCOPE_LOCAL` event from §6.5. The event captures the operation `id`, the required scopes, the granted scopes, and the difference.
- **Authentication failures.** Refresh failures that resulted in `expired` or `revoked`; initial-authentication failures that resulted in `pending → revoked`. The event captures the `Connection` `id`, the failure category (refresh-token rotation, transient refresh failure, definitive revocation), and a `Provider`-supplied error code if available.
- **Encryption-key-rotation events.** Per §6.3. The event captures the rotation timestamp, the actor (system schedule, manual operator action), the old and new key identifiers, the count of DEKs re-wrapped.
- **Revocation events.** Both user-initiated and `Provider`-initiated. User-initiated events capture the user identifier (or system actor); `Provider`-initiated events capture the source (refresh-time `invalid_grant`, webhook).

### Per-event field set

Every audit event MUST include:

- **`timestamp`** — the event's wall-clock time, RFC 3339 with timezone.
- **`actor`** — the user identifier or system identifier that caused the event. For user-initiated events, this is the user's identifier within the implementation's authentication system. For system-initiated events (background refresh, scheduled rotation, webhook-driven), this is a system actor identifier.
- **`connection_id`** — the affected `Connection`'s identifier, when the event is `Connection`-scoped. May be absent for events scoped to the implementation as a whole (encryption-key rotation across all `Connection`s, for example).
- **`event_type`** — a stable string identifier for the event class (`connection.state_transition`, `credential.rotation`, `dispatch.attempt`, etc.). The vocabulary is implementation-defined; UACP recommends a dotted hierarchy and recommends using a stable set so audit analyses across implementations are tractable.
- **`outcome`** — `success`, `failure`, or an event-specific value (`success_with_retry` for dispatch, `partial` for rotation that touched a subset of DEKs, etc.).
- **`detail`** — an event-specific structured payload. For state transitions, the from-state and to-state. For dispatch attempts, the operation id and duration. For credential events, the rotation algorithm and key identifiers. The `detail` field MUST NOT contain plaintext credentials; an event that touches a credential refers to the credential by its `secret://` URI, not its value.

Implementations MAY include additional fields for telemetry purposes (request id, trace span id, host id, process id) without affecting conformance.

### Integrity expectations

Audit logs SHOULD be append-only. The `SHOULD` admits two implementations:

- **Append-only storage.** The log destination is a write-once-read-many (WORM) backend, an immutable log service, or a database table with INSERT-only permissions. Tampering requires compromising the storage layer's access controls.
- **Cryptographic chaining.** Each log entry includes a hash of the prior entry's content (and ideally a hash of all prior entries, in the form of a Merkle root). Tampering with a historical entry invalidates the chain from that entry forward; verification detects the tampering.

Either approach satisfies the `SHOULD`. A non-append-only log (a flat file that anyone with write access can edit) does not satisfy the `SHOULD` and renders the audit weaker, but a `Conforming Implementation` that uses such a log is not non-conforming if the spec's `SHOULD` is interpreted strictly — the integrity expectation is best-effort. Implementations that target SOC 2, HIPAA, or similar audit-sensitive compliance frameworks MUST use one of the integrity-preserving approaches.

### Audit retention

UACP does not specify a normative retention period. Implementations choose per their compliance posture; common choices are 12 months for general-purpose audit, 7 years for financial-services-flavored compliance, indefinite for security-critical events.

### What is NOT logged

- **Plaintext credentials.** Per §6.6's per-event field set — the `detail` field MUST NOT contain plaintext credentials. The same applies across every event class.
- **Full request and response bodies.** A dispatch event records that a dispatch occurred and its outcome; it does not (by default) record the request body or the response body. Recording bodies is permitted for diagnostic purposes when a dispatch fails, but the implementation MUST scrub credentials from any such captures, and the spec's audit requirement is satisfied by the metadata alone.
- **Personally identifying information beyond the `actor` field.** The `actor` identifies who caused the event; additional PII is not part of the audit-event spec.

## 6.7 Trust model for ingested artifacts

Stage 3 introduced three sources from which `Operation` schemas can arrive: OpenAPI ingestion (§3.6), `curl`-paste (§3.7), and LLM inference (§3.8). Each carries a different trust posture.

### OpenAPI ingestion

OpenAPI specifications are documents the `Provider` (or a third party) publishes. An OpenAPI spec from an arbitrary URL CAN be malicious:

- **Misdirected `base_url`.** The `servers` block of the OpenAPI spec points at an attacker-controlled host that proxies to the real `Provider` while logging credentials.
- **Over-claimed scopes.** The spec's `securitySchemes` block claims scopes the user does not need, encouraging the user to consent to broader access.
- **Phantom destructive operations.** The spec advertises operations that don't exist on the real `Provider` but match commonly-targeted attack surfaces, and the user is encouraged to integrate them.

A `Conforming Implementation` SHOULD warn the user when ingesting an OpenAPI spec from a URL whose origin does not match the `Provider`'s known canonical domain. The match check:

- The implementation maintains a list of well-known `Provider`s and their canonical domains (`api.example.com`, `*.example.com`). The list is maintained by the implementation's authoring tools or a curated registry.
- When ingesting a spec from `https://api.example.com/openapi.yaml`, the origin matches the canonical; the warning is suppressed.
- When ingesting from `https://random-host.tld/example-openapi.yaml`, the origin does not match; the warning surfaces to the user with text along the lines of "This OpenAPI spec is hosted at a URL not on Example.com's canonical domain. Verify the source before using."

The warning is not a block; the user MAY proceed if they have verified the source out-of-band. The `SHOULD` recognizes that not every implementation has access to a canonical-domain registry, and that for niche `Provider`s no such registry exists; in those cases the warning MAY be unconditional ("this is an unfamiliar source; verify it") or MAY be suppressed per implementation policy.

### `curl`-paste

`curl` invocations are user-supplied. They inherit the user's trust: the user copied the `curl` from somewhere, and the implementation processes it as the user provided it. UACP does not add a trust layer over `curl`-paste because doing so would be ineffective — the user is the trust authority, and the implementation has no independent way to verify the `curl`'s origin.

The mitigation is the strip-and-warn behavior in §3.7: authentication-bearing artifacts in the `curl` are stripped (preventing accidental credential embedding) and surfaced to the user with a recommendation to move them to the artifact's `authentication` block. Beyond that, the implementation trusts the user.

### LLM-inferred schemas

LLM-inferred schemas inherit the trust level of the LLM. Two layers of defense apply:

- **Mandatory user review** per §3.8 — every inferred schema MUST be presented to the user before persistence. The user is the trust authority; the LLM is the source.
- **Destructive-verb highlighting** — A `Conforming Implementation` SHOULD highlight inferred operations whose HTTP method is `DELETE`, or whose path contains commonly-destructive segments (`/delete`, `/remove`, `/purge`, `/drop`), or whose `summary` contains destructive language. The highlighting is a visual cue during review; it does not block approval, but it draws the user's attention to operations whose effects are irreversible.

The destructive-verb highlight is `SHOULD` rather than `MUST` because not every implementation has a UI for highlighting; the underlying signal (the operation's method and path) is always available, and an implementation that exposes that signal to the user via any surface (a console flag, a CLI prompt) satisfies the spirit of the requirement.

### LLM-inferred schemas: the agent-injection case

A subtler threat: the LLM-inference path is invoked when the user describes a `Provider` in natural language. An adversarial input — a prompt-injection attempt embedded in a user-pasted `Provider` description, or an LLM agent that has been compromised — could produce an inferred operation that does something other than what the user intended. The inferred operation might dispatch to an attacker-controlled URL, request scopes the user does not need, or contain a `description` that misleads the user during review.

The defenses:

- The mandatory user-review step is the human-in-the-loop check. Adversarial inferences must survive review by the user; a sufficiently obvious malicious operation (a `base_url` that doesn't match the `Provider`'s domain, a wildly over-broad scope set) triggers the user's review attention.
- The destructive-verb highlighting draws attention to operations with high consequences.
- The provenance metadata (§3.8) records the LLM model and the original natural-language description; a future audit can reconstruct what was input and what was approved, even if the malicious inference slipped through.

UACP cannot eliminate prompt-injection-class risks at the protocol layer; the inference pipeline's security is part of the implementation's responsibility. UACP's contribution is the structural requirement that no inferred operation reaches dispatch without human review, and that the review is presented with the information needed to catch obvious problems.

## 6.8 Compliance posture

A `Conforming Implementation` of `v1.x` carries a baseline compliance posture by virtue of meeting the spec's requirements. UACP does NOT certify implementations against any compliance framework; the spec specifies the controls, and an implementation that passes a third-party compliance audit claims its own posture.

The baseline:

- **SOC 2.** SOC 2 requires demonstrable controls around security, availability, processing integrity, confidentiality, and privacy. UACP's audit-logging requirements (§6.6), encryption-at-rest (§6.3), credential lifecycle (§6.4), scope enforcement (§6.5), and threat model (§6.1) collectively cover the security and confidentiality criteria; availability is the implementation's runtime concern and out of UACP's scope. An implementation that satisfies UACP's MUST and SHOULD requirements has the technical controls a SOC 2 audit looks for in this domain.
- **GDPR.** GDPR's data-subject rights include deletion (right to be forgotten). UACP's deletion requirement (§6.4 — orphaned credentials are MUST NOT) and the implementation's broader connection-deletion path together satisfy the deletion right for credential material. GDPR additionally requires processing transparency; UACP's audit logging covers the technical foundation. The legal-process surface (consent forms, data-subject access requests) is the implementation's product responsibility.
- **HIPAA.** HIPAA's encryption-at-rest requirement is satisfied by UACP's §6.3. HIPAA's audit-trail requirement is satisfied by UACP's §6.6 with the integrity-preserving append-only or chained variant. HIPAA additionally requires access controls around protected health information; UACP's local scope enforcement (§6.5) is one component; the broader access control around the implementation's user surface is the implementation's concern.
- **ISO 27001.** ISO 27001 is a risk-management framework; UACP's threat model (§6.1) is the foundational input. The control catalog (Annex A) maps to UACP's controls in the obvious places (cryptography, access control, operational security, supplier relationships).

### What this section is and is not

This section is informational. It describes the relationship between UACP's controls and common compliance frameworks so implementers planning their own compliance audits can use UACP as scaffolding. It is NOT a certification, a guarantee, or a substitute for an actual compliance audit. UACP's spec compliance is a necessary-but-not-sufficient condition for the compliance frameworks above; the implementation's deployment, operations, governance, and product surface contribute the rest.

## 6.9 Conformance summary

This section summarizes the security conformance levels for a `Conforming Implementation` of `v1.x`. The summary is parallel to §2.9, §3.11, §4.9, and §5.7.

### MUST requirements

A `Conforming Implementation` MUST:

- Encrypt persisted credentials at rest with an algorithm satisfying confidentiality, integrity, and per-`Connection` separation, per §6.3.
- Resolve `secret://` URIs against the registered store named by `<store>` per §6.2; the `vault`, `aws-secrets-manager`, `local-keyring`, and `inline-encrypted` stores are the v1.0 baseline.
- Resolve credentials at dispatch time, not at artifact load time, per §6.2.
- Surface `auth_expired` with a diagnostic message when secret-store resolution fails, per §6.2.
- Delete every associated secret-store entry when a `Connection` is permanently deleted; orphaned credentials are forbidden per §6.4.
- Perform local scope enforcement at dispatch time when granted scopes are persisted and required scopes are declared; reject with `INSUFFICIENT_SCOPE_LOCAL` (`forbidden`) per §6.5.
- Distinguish `INSUFFICIENT_SCOPE_LOCAL` from `INSUFFICIENT_SCOPE_REMOTE` per §6.5.
- Log `Connection` state transitions, credential lifecycle events, dispatch attempts, scope-enforcement rejections, authentication failures, encryption-key-rotation events, and revocation events, per §6.6.
- Include `timestamp`, `actor`, `connection_id` (when scoped), `event_type`, `outcome`, and `detail` in every audit event per §6.6.
- Atomically replace refresh tokens on rotation per §5.3, mirrored here as a security requirement.

### MUST NOT requirements

A `Conforming Implementation` MUST NOT:

- Persist plaintext credentials. Per §6.3 — the `MUST NOT` is absolute.
- Cache resolved plaintext credentials beyond the duration of a single dispatch call (per §6.2).
- Embed plaintext credentials in any audit-event field, including `detail`.
- Allow recursive `inline-encrypted` resolution (an `inline-encrypted` URI whose `key_ref` resolves to another `inline-encrypted` URI), per §6.2.
- Use a weaker encryption algorithm than the spec's recommended floor — AES-128, CBC-without-HMAC, or other non-AEAD modes — per §6.3.
- Leave orphaned credentials in the secret store after `Connection` deletion, per §6.4.

### SHOULD requirements

A `Conforming Implementation` SHOULD:

- Use append-only storage or cryptographic chaining for audit logs, per §6.6.
- Support envelope-encryption-based key rotation, per §6.3.
- Persist `scopes_granted` per Stage 5 §5.6 to enable local scope enforcement.
- Warn the user when ingesting an OpenAPI spec from a URL whose origin doesn't match the `Provider`'s known canonical domain, per §6.7.
- Highlight inferred operations whose method is `DELETE` or whose path/summary suggests destructiveness, per §6.7.
- Use a hard-delete option on the underlying secret store when supported, for GDPR-compliance posture, per §6.4.
- Surface locked-keyring failures distinguishably for `local-keyring` resolution, per §6.2.
- Support sub-field selection via fragment for `vault` and `aws-secrets-manager` URIs, per §6.2.

### MAY requirements

A `Conforming Implementation` MAY:

- Register additional `<store>` tokens for proprietary or cloud-specific surfaces (`gcp-secret-manager`, `azure-key-vault`, `kubernetes-secrets`) per §2.8.
- Use ChaCha20-Poly1305 in place of AES-256-GCM per §6.3.
- Support a per-`Connection` cache of resolved credentials within a single dispatch call's lifetime per §6.2.
- Listen for `Provider`-pushed revocation webhooks per §5.4.
- Emit additional audit fields beyond the per-event field set per §6.6.
- Schedule periodic forced rotation of credentials per §6.4.
- Immediately overwrite credential ciphertext on revocation as defense-in-depth per §6.4.

### Cumulative conformance

The cumulative effect of the MUSTs and MUST NOTs is that a `Conforming Implementation` of `v1.x` keeps credentials encrypted at rest, resolves them lazily, separates one `Connection`'s credentials from another's at the cryptographic level, deletes credentials cleanly when a `Connection` is removed, prevents over-broad authorization through artifact edits via the local scope check, and produces an audit trail sufficient for security review. The security model closes the loop with the structural posture established in earlier stages — credentials are referenced not embedded (Stage 2), schemas are credential-free (Stage 3), the wire is over HTTPS (Stage 4), and the lifecycle handles rotation correctly (Stage 5) — turning UACP's commitment that `.uacp` artifacts are publicly safe to share into a load-bearing property rather than an aspirational claim.
