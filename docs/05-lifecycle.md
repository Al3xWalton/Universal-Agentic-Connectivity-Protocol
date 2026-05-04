# UACP Connection Lifecycle

This document specifies the connection lifecycle of UACP `v1.x`. It defines the state machine a `Connection` traverses from creation through termination, the refresh policies that keep credentials current, the rotation handling that prevents refresh-token loss, the propagation rules for revocation, the re-authentication path that recovers a terminal connection, and the persistence requirements that let a connection survive process restarts. The conformance keywords ("MUST", "MUST NOT", "SHOULD", "SHOULD NOT", "MAY") in this document are interpreted per BCP 14 [[RFC2119](https://datatracker.ietf.org/doc/html/rfc2119)] [[RFC8174](https://datatracker.ietf.org/doc/html/rfc8174)] as established in [Stage 0 — Primer](./00-primer.md).

This document is consistent with the foundational principles in [Stage 1 — Principles](./01-principles.md), the authentication subsystem in [Stage 2 — Authentication](./02-authentication.md), the schema and discovery layer in [Stage 3 — Schema](./03-schema.md), and the dispatch runtime in [Stage 4 — Dispatch](./04-dispatch.md). Where this document refines a principle, it does so by narrowing detail; it does not override.

## 5.0 Overview

A UACP `Connection` is a long-lived object. It is created when a user authenticates against a `Provider`, becomes available for dispatch when authentication succeeds, requires periodic refresh to keep its access token current, may be revoked at any time by the user or the `Provider`, and may transition into a terminal error state from which only re-authentication can recover. This stage specifies the rules that govern those transitions.

The lifecycle layer sits between the authentication subsystem (Stage 2, which produces credential material) and the dispatch runtime (Stage 4, which consumes it). Stage 2 specifies *how* a `Connection` obtains its credentials; this stage specifies *when* those credentials are refreshed, *how* a refresh is coordinated with concurrent dispatch, *what happens* when the credentials are revoked, and *what state* a `Connection` carries across process restarts.

### In scope

- The `Connection` state machine: states, transitions, and which transitions are spec-mandated versus implementation-discretion (§5.1).
- Refresh policies — lazy, proactive, reactive — and the refresh window definition (§5.2).
- Refresh-token rotation handling: the atomic-swap requirement and the failure modes that destroy connections when ignored (§5.3).
- Revocation propagation: user-initiated, `Provider`-initiated, and webhook-driven revocation paths (§5.4).
- Re-authentication: how a terminal `Connection` is recovered, what stays stable across re-auth, and the user-experience requirement that re-auth is surfaced clearly rather than silently failing (§5.5).
- Persistence: the metadata fields that MUST survive process restarts for the lifecycle to remain coherent (§5.6).
- Conformance summary (§5.7).

### Out of scope

The following are deferred to later stages and MUST NOT be inferred from this document:

- **Secret-store implementations and encryption.** The on-disk format of stored credentials, the encryption algorithm used for credentials at rest, the key-management surface, and the rotation of encryption keys are **Stage 6 (security)**. This stage requires that certain fields be persisted (§5.6); the *form* of persistence — and in particular the encryption posture per Principle 7 — is Stage 6's responsibility.
- **Audit logging.** What state transitions, refresh events, and revocation events MUST be logged, the audit-event schema, and the integrity guarantees on audit logs are **Stage 6 (security)**. This stage names the lifecycle events that occur; Stage 6 specifies how they are recorded.
- **Authentication-method specifics.** The wire shape of OAuth 2.0 authorization-code flows, device authorization grants, OAuth 1.0a request signing, AWS SigV4, and the other registered methods is **Stage 2**. This stage references "the refresh response" and "the token endpoint" as Stage 2 specifies them; the protocol exchange itself is not redefined here.
- **Dispatch behavior.** Retry policy, error normalization, pagination, and streaming are **Stage 4**. This stage references dispatch interactions (a dispatch attempt observes `auth_expired`, triggers refresh, retries) but does not redefine the dispatch surface.

Where a section approaches one of those boundaries, the boundary is named explicitly.

## 5.1 Connection state machine

A `Connection` exists in one of seven states at any moment. The states and the transitions among them are normative.

### States

- **`pending`** — The `Connection` has been initiated but has not yet completed authentication. For interactive grants (OAuth 2.0 authorization code, OAuth 2.0 device code, OAuth 1.0a), `pending` covers the period from grant initiation through the user's consent and the token exchange. For non-interactive grants (OAuth 2.0 client credentials, API key, AWS SigV4 with pre-existing keys), `pending` is brief or skipped — the `Connection` can transition directly from creation to `active`.
- **`active`** — The `Connection` holds valid credentials and is available for dispatch. The dispatch runtime issues calls against `active` `Connection`s freely. `active` is the steady state.
- **`expiring`** — The `Connection` holds credentials whose access token is within the *refresh window* (§5.2). Dispatch from `expiring` is permitted; the runtime SHOULD initiate a proactive refresh and MAY queue subsequent dispatches behind the refresh until it completes. `expiring` is a refinement of `active`; implementations that do not perform proactive refresh MAY collapse this state into `active` and transition directly from `active` to `expired`.
- **`refreshing`** — A token-refresh request is in flight. Dispatch issued during `refreshing` MUST be queued or rejected per the implementation's chosen policy (see "Concurrency" below); it MUST NOT proceed using a token the runtime knows to be expiring.
- **`expired`** — The access token has expired and refresh has not yet succeeded. Dispatch from `expired` MUST NOT issue a wire request with the expired token; the runtime initiates refresh and queues or rejects the dispatch.
- **`revoked`** — The `Connection`'s credentials have been invalidated. The invalidation may be user-initiated (the user clicked "Disconnect") or `Provider`-initiated (the `Provider`'s authorization server revoked the refresh token). `revoked` is **terminal**: a `revoked` `Connection` MUST NOT be transitioned back to `active` by any path other than re-authentication, which produces a fresh `Connection` (typically with the same `Connection` identifier per §5.5).
- **`error`** — A non-revocation failure has rendered the `Connection` unusable, but the state may yet recover. Examples include refresh-endpoint failures that exhausted the retry budget without a definitive revoked-token response (the refresh server was down), or unexpected response shapes that the implementation cannot map. `error` is *transient terminal*: the `Connection` is not currently usable, but a future refresh attempt may succeed. Implementations MAY auto-retry refresh from `error` on a backoff schedule, and SHOULD surface the state to the user with a manual retry affordance.

### Transitions

The normative transition diagram, expressed as `from → to: trigger`:

```
(creation) → pending: connection initiated, credentials not yet obtained
(creation) → active: credentials obtained synchronously (e.g., API key paste)
pending → active: authentication completed; credentials persisted
pending → revoked: user cancelled the auth flow; or the Provider denied consent
pending → error: auth flow timed out or returned an error response

active → expiring: access token's remaining lifetime entered the refresh window (§5.2)
active → expired: access token expired without entering an explicit refresh-window state (some implementations skip expiring)
active → refreshing: refresh initiated proactively or reactively
active → revoked: user-initiated revocation; or webhook-driven Provider revocation event
active → error: dispatch surfaced a non-recoverable error specific to the credential (e.g., scope-revocation indication)

expiring → refreshing: refresh initiated
expiring → active: refresh window passed without action and the token has not yet expired (rare; possible when a Provider issues a longer-than-advertised token lifetime)
expiring → expired: token expired before refresh was initiated
expiring → revoked: user-initiated revocation
expiring → error: dispatch surfaced a non-recoverable error

refreshing → active: refresh succeeded; new access token (and new refresh token if rotated) persisted
refreshing → expired: refresh failed transiently; runtime will retry per the refresh policy
refreshing → revoked: refresh response indicated the refresh token is invalid (RFC 6749 §5.2 invalid_grant); the Provider has revoked the credential
refreshing → error: refresh failed in a way that is neither clean revocation nor transient (malformed response, unexpected status, etc.)

expired → refreshing: dispatch attempted; refresh initiated reactively
expired → revoked: user-initiated revocation
expired → error: dispatch attempted; refresh failed terminally

error → refreshing: implementation auto-retry, or user-initiated retry
error → revoked: user-initiated revocation (the user gave up on recovery and disconnected)

revoked → (re-auth) → pending or active: re-authentication produces a fresh Connection per §5.5
```

### Spec-mandated transitions

The following transitions MUST be implemented by every `Conforming Implementation`:

- `pending → active` on successful authentication completion.
- `active → revoked` on explicit user-initiated revocation.
- `refreshing → active` on successful refresh.
- `refreshing → revoked` on `invalid_grant` from the refresh endpoint or any equivalent definitive revocation indication.
- `expired → refreshing` when dispatch is attempted against an `expired` `Connection`.
- `revoked → (terminal)` — `revoked` is terminal; no implementation may auto-transition out of it.

### Implementation-discretion transitions

The following are permitted but not required:

- `active → expiring`. Implementations that do not do proactive refresh MAY skip this state.
- `expiring → refreshing` proactively. Implementations that do reactive-only refresh perform `expired → refreshing` instead.
- `error → refreshing` automatically. Implementations MAY auto-retry on backoff; they MAY also require explicit user action to retry, especially after several auto-retries have failed.

### Concurrency

A `Connection` is shared across concurrent dispatch calls. The state machine governs that sharing:

- A `Connection` MUST NOT be in `refreshing` for more than one in-flight refresh at a time. Implementations MUST coordinate concurrent refresh attempts (typically with a per-`Connection` lock or a single-flight pattern) so a burst of dispatches does not produce a burst of refresh requests.
- Dispatches issued while the `Connection` is in `refreshing` MUST either queue (block until the refresh resolves, then proceed against the fresh token) or fail fast with `auth_expired` and let the caller retry. The choice is implementation-defined; queueing is the typical pattern because it preserves the caller's expectations.
- Once a refresh resolves to `active`, all queued dispatches proceed against the new token. If the refresh resolves to `revoked` or `error`, queued dispatches MUST NOT proceed; they fail with `auth_expired` (for `revoked`, accompanied by guidance per §5.5) or `upstream_error` (for `error`).

## 5.2 Refresh policies

A `Connection` whose credentials have a finite lifetime — the OAuth 2.0 access token, the access token returned from a device-code grant, the access token from an OAuth 1.0a flow that issues short-lived tokens — must be refreshed periodically to remain `active`. Static-credential methods (API key, AWS SigV4 with long-lived keys) do not refresh and skip this section.

UACP defines three refresh strategies. Implementations MUST support at least lazy refresh; SHOULD support proactive refresh; MAY support reactive refresh.

### Lazy refresh

Lazy refresh fires on dispatch. When a dispatch is initiated and the `Connection`'s access token is `expired` (or `expiring`, in implementations that observe that state), the runtime initiates a refresh, waits for the refresh to complete, and then dispatches against the fresh token.

- Lazy refresh adds latency to the first dispatch after token expiry (typically a few hundred milliseconds for the refresh exchange).
- Lazy refresh is simple: the implementation does not need a background scheduler.
- Lazy refresh requires no additional persistent state beyond the access token's expiry timestamp.

A `Conforming Implementation` of `v1.x` MUST support lazy refresh.

### Proactive refresh

Proactive refresh fires on a schedule, ahead of expiry. A background worker observes `Connection`s, identifies those whose access tokens are within the refresh window (definition below), and refreshes them before any dispatch needs to wait.

- Proactive refresh masks refresh latency from the dispatch path. Calls to `active` `Connection`s never wait on a refresh exchange; the refresh has already happened.
- Proactive refresh requires a background scheduler — typically a small worker that wakes periodically, examines `Connection` expiry timestamps, and queues refresh work.
- Proactive refresh requires care under failure: a refresh that fails transiently while a dispatch is also in flight should not double-refresh. The state machine's `refreshing` state and the per-`Connection` single-flight rule (§5.1) cover this.

A `Conforming Implementation` of `v1.x` SHOULD support proactive refresh.

### Reactive refresh

Reactive refresh is a recovery pattern: a dispatch fires against an `active` `Connection`, the `Provider` returns `401`, the runtime catches the `401`, refreshes, and retries the original dispatch once.

- Reactive refresh is the safety net for cases where the runtime's local view of token expiry is wrong (the `Provider` revoked the token early, the local clock is skewed, the token was issued with a shorter lifetime than advertised).
- Reactive refresh costs one wasted dispatch attempt per stale-token incident — the runtime issues the request, gets `401`, then refreshes and retries.
- Reactive refresh is fragile against `Provider`s that do not return `401` cleanly on expired tokens (some return `403`, some return a `2xx` with an `ok: false` envelope, some return `500`). The runtime's `auth_expired` mapping per §4.6 is what makes reactive refresh feasible; an implementation that maps a `Provider`'s expired-token response to `auth_expired` correctly can reactively refresh.

A `Conforming Implementation` of `v1.x` MAY support reactive refresh. When supported:

- The retry-after-refresh is performed exactly once per dispatch call. A dispatch that fails with `auth_expired`, refreshes, retries, and fails again with `auth_expired` is surfaced as `auth_expired` to the caller; the runtime does not refresh-and-retry in a loop.
- Reactive refresh is coordinated with the same single-flight lock as proactive and lazy refresh (§5.1). Concurrent dispatches that all observe `auth_expired` produce one refresh exchange, not many.

### Refresh window definition

A `Connection`'s access token is "in the refresh window" when its remaining lifetime is less than the larger of:

- **60 seconds**, or
- **`expires_in` × 0.1** (10% of the token's nominal lifetime).

The dual definition handles short-lived tokens (a 5-minute access token's window is 30 seconds at 10%, but the 60-second floor rounds it up so refresh is initiated before expiry) and long-lived tokens (a 1-hour access token's window is 6 minutes; a 12-hour access token's window is 1 hour and 12 minutes).

Implementations MAY use a more conservative window (refresh earlier) but MUST NOT use a more aggressive window (refresh later than the spec defines). An implementation that refreshes "a few seconds before expiry" risks the token expiring mid-flight and the refresh racing against a `401`; the spec's window is large enough to absorb typical clock skew and refresh-exchange latency.

The refresh window applies to the access token's `expires_in`, not the refresh token's lifetime. The refresh token's expiry — when it has one — is treated separately: the refresh-token-expiry timestamp is persisted (§5.6), and a `Connection` whose refresh token has expired is `expired` rather than refreshable.

### Refresh and lifecycle interaction

The refresh exchange follows the wire shape specified by Stage 2's authentication-method-specific sections: §2.2.1 (authorization code grant), §2.2.3 (device authorization grant), §2.2.4 (refresh tokens), with the rotation requirement from §5.3. The runtime observes the refresh response, applies rotation if applicable, persists the new token material, and transitions the `Connection` to `active`.

## 5.3 Refresh-token rotation

OAuth 2.0 authorization servers MAY return a new refresh token alongside each access token in a refresh response (RFC 6749 §6 permits but does not require it; many `Provider`s now require it as a security best practice, and several specifications including the OAuth 2.1 draft mandate rotation). Rotation is a defense against refresh-token theft: a stolen refresh token, once used by the attacker, is invalidated by the rotation, and the legitimate client's next refresh fails — a signal that the `Connection` has been compromised.

UACP `v1.x` requires correct rotation handling.

### Atomicity

When a refresh response includes a `refresh_token` field, the implementation MUST replace the `Connection`'s active refresh token with the returned value. The replacement MUST be **atomic**:

- Either the new refresh token replaces the old one and the old one is permanently discarded, or the operation fails entirely with the old refresh token still active.
- A partial write — the new refresh token is persisted but the old one is not yet discarded — is acceptable as a transient intermediate state, because the implementation MUST re-discard the old one on the next opportunity.
- A partial write that LOSES the new refresh token while the old one has been invalidated by the `Provider` is a **connection-killing bug**. The `Connection` becomes unrefreshable: the old refresh token is invalid (the `Provider` rotated it away on the refresh response), and the new one was lost. This is the canonical bug rotation handling exists to prevent.

Implementations MUST persist the new refresh token to durable storage **before** considering the refresh exchange complete and before any subsequent dispatch consumes the new access token. The order of operations:

1. Refresh response arrives with new access token and new refresh token.
2. Implementation persists the new refresh token, the new access token, and the new expiry timestamps to durable storage.
3. Persistence completes successfully (the durability guarantee of the storage backend has fired — the write has been flushed).
4. The `Connection` transitions from `refreshing` to `active` and begins serving dispatches against the new tokens.

A failure between step 2 and step 3 — the persistence write was issued but the storage backend's durability has not yet been confirmed — is the most common source of rotation bugs. Implementations SHOULD use a storage backend that confirms durability synchronously, or SHOULD retry the persistence operation until durability is confirmed before transitioning the `Connection`.

### Detection of rotation requirement

Some `Provider`s rotate refresh tokens on every refresh; some rotate sometimes; some never rotate. The implementation does not need to know the `Provider`'s policy in advance — the rule is purely reactive: if the refresh response contains a `refresh_token` field, replace; if it does not, retain the prior refresh token.

A response that omits `refresh_token` MUST be treated as "the prior refresh token is still valid"; the implementation MUST NOT discard the prior refresh token in this case. This is the symmetric error to the loss-bug above: discarding a still-valid refresh token because the response omitted a new one would also break the `Connection`.

### Audit hook

Refresh-token rotation events are audit events; the audit-event schema is **Stage 6**. This stage records that the events exist and that they MUST be persisted alongside the rotation; Stage 6 specifies what the persisted record looks like.

## 5.4 Revocation propagation

A `Connection` is revoked when its credentials are no longer valid. Revocation has three sources:

1. **User-initiated.** The user clicks "Disconnect" (or the equivalent) in the implementation's UI. The implementation transitions the `Connection` to `revoked` and SHOULD additionally inform the `Provider` via the `Provider`'s revocation endpoint when one is available.
2. **`Provider`-initiated, observed at refresh.** The `Provider`'s authorization server returns `invalid_grant` (RFC 6749 §5.2) or an equivalent definitive-revocation indication on a refresh attempt. The implementation transitions the `Connection` from `refreshing` to `revoked`.
3. **`Provider`-initiated, observed via webhook.** Some `Provider`s push revocation events to a webhook the implementation has registered. The implementation receives the event and transitions the affected `Connection` to `revoked`.

### Behavior in `revoked` state

A `Conforming Implementation` MUST stop accepting dispatch calls against `revoked` `Connection`s. The runtime returns an error to the caller (typically `auth_expired` accompanied by guidance per §5.5 that re-authentication is required); it MUST NOT attempt the call against the `Provider` even speculatively.

Operations queued at the moment of revocation — dispatches that were waiting on a refresh, or pending in a per-`Connection` queue — MUST be drained with the appropriate failure code. Queued operations MUST NOT be retried after revocation; they fail.

### Calling the `Provider`'s revocation endpoint

When revocation is user-initiated and the `Provider` exposes a token-revocation endpoint per RFC 7009 [[RFC7009](https://datatracker.ietf.org/doc/html/rfc7009)] (typically advertised in the `Provider`'s server metadata per §2.2.5), the implementation SHOULD call it. The call passes the access token (and optionally the refresh token) to the revocation endpoint, asking the `Provider` to invalidate them.

Calling the revocation endpoint:

- Reduces the window during which a leaked credential is usable.
- Is courteous to the `Provider` (some `Provider`s charge for active credentials, and revoking explicitly frees resources).
- Is best-effort. If the revocation call fails (transport error, the endpoint returns an unexpected status), the implementation MUST still transition the `Connection` to `revoked` locally — the local state is the source of truth for whether the implementation will dispatch.

The revocation call is fire-and-forget from the user's perspective: the user's "disconnect" action completes immediately at the local level, and the revocation-endpoint call happens in the background.

For `Provider`-initiated revocation observed at refresh (the `invalid_grant` case), the local transition to `revoked` is immediate and no additional revocation call is needed — the `Provider` has already revoked.

For webhook-driven revocation, the local transition to `revoked` is triggered by the webhook event; no additional revocation call is needed because the `Provider` was the source.

### Webhook-driven revocation

Some `Provider`s expose webhook events that signal revocation: account closure, scope revocation, application uninstall, password reset (which sometimes revokes outstanding tokens), and similar. A `Conforming Implementation` MAY listen for these webhooks; when it does, the webhook event triggers the same `active → revoked` transition as the other paths.

Webhook listening is `MAY` rather than `SHOULD` because not every implementation has a webhook-receiving surface, and not every `Provider` exposes revocation webhooks. Implementations that do listen MUST validate the webhook's authenticity (per the `Provider`'s webhook signing scheme — typically `hmac_signature` per §2.5.2) before acting on the event; an unauthenticated revocation webhook is a denial-of-service vector against the implementation's `Connection` set.

### Cascade of revocation across operations

Revocation is `Connection`-wide, not per-operation. When a `Connection` is revoked, every operation it exposed becomes unreachable through that `Connection`. The agent that was using `gmail.send` and `gmail.list_messages` against the same `Connection` loses access to both at once.

Implementations that need finer-grained revocation (revoke only the `gmail.send` operation but keep `gmail.list_messages` available) cannot achieve it through this `Connection`'s lifecycle; they must instead model the desired surface as two `Connection`s, each with the appropriate scope. UACP does not specify how to do that; it is an authoring concern.

## 5.5 Re-authentication

When a `Connection` enters `revoked` or terminal `error`, recovering it means re-running the authentication flow from Stage 2. UACP specifies two normative properties of the re-authentication path: identifier stability and user-experience clarity.

### Identifier stability

A `Connection` has an identifier — typically a UUID — that is independent of the credential material it holds. When a `revoked` `Connection` is re-authenticated, the resulting `active` `Connection`:

- SHOULD retain the same `Connection` identifier.
- MUST reference fresh credentials at the secret-store URI.
- MUST update the persisted metadata (per §5.6) to reflect the fresh credentials' expiry and the most-recent state transition.

Identifier stability is what makes historical references coherent. The audit log entries that reference the `Connection` by identifier remain meaningful after re-auth; the agent's references to operations within the `Connection` continue to resolve. From the agent's perspective, the `Connection` "came back online" after a brief disruption, rather than being replaced by a new entity.

The identifier-retention property is `SHOULD` rather than `MUST` because some implementations have constraints (a strict immutable-record audit posture, for example) that require minting a new identifier on re-auth and recording the relationship between old and new identifiers separately. The recommended default is to retain the identifier; deviations are acceptable when the implementation has a clear reason and records the identifier transition explicitly.

### Credential replacement

The `secret://` URIs that the `Connection` references (per §2.7) point at credential material. On re-authentication, the credential material changes — a new access token, a new refresh token, possibly new client-credential material if the user authorized through a fresh OAuth client. The URIs themselves MAY remain stable (the same `secret://vault/connection-id/refresh_token` path), with the credential at the URI being overwritten; or the URIs MAY change (a fresh path per re-auth). Either pattern is conforming. The persisted `Connection` metadata MUST be updated to reflect the current URI set.

### User-experience requirement

Re-authentication is a user-visible event. Implementations MUST provide a clear path from the `revoked` or terminal `error` state to a re-authenticated `active` state. The minimum surface:

- The runtime MUST detect the terminal state on dispatch and surface a recognizable indication that re-authentication is required. The dispatch failure code is `auth_expired`; the canonical error message SHOULD include language such as "this connection needs to be re-authenticated" so callers (and ultimately users) understand that action is required.
- The implementation MUST NOT silently retry indefinitely against a `revoked` `Connection`. Each dispatch fails immediately with the indicative error.
- The implementation SHOULD expose a re-authentication affordance to the user — a "Reconnect" button, a CLI command, or an equivalent surface — that initiates the appropriate Stage 2 flow.

A `Conforming Implementation` MUST satisfy the dispatch-side requirement (clear `auth_expired` with re-auth indication); the user-interface surface is `SHOULD` because not every implementation has a UI.

### Re-authentication and concurrent dispatch

When a `Connection` is in `revoked` or `error` and a re-authentication flow is initiated, the implementation:

- Continues to fail dispatch attempts against the (still-revoked) `Connection` until re-authentication completes.
- Models the re-authentication flow as a separate transaction: the user goes through the OAuth consent screen (or equivalent) and the Stage 2 flow runs to completion before the `Connection`'s state changes.
- On re-authentication success, the `Connection` transitions to `active` and queued or new dispatch attempts proceed.

The implementation MAY model re-authentication as a transition through `pending` (the `Connection` becomes `pending` for the duration of the consent flow, then transitions to `active` on completion); this is the natural shape, mirroring the original creation path.

## 5.6 Connection metadata persistence

For a `Connection` to survive a process restart, certain metadata MUST be persisted to durable storage. This section enumerates the required fields. The persistence *format* is implementation-defined; the spec's requirement is that the listed fields exist somewhere recoverable.

### Required fields

A `Conforming Implementation` MUST persist the following fields per `Connection`:

- **`connection_id`** — the `Connection`'s stable identifier (typically a UUID).
- **`state`** — the current state from the §5.1 state machine.
- **`uacp_artifact`** — either the full `.uacp` artifact content or a reference to it (a path, a URL, an identifier into another store). The artifact is the static description of the `Provider` and the operations available on the `Connection`; it is not itself credential material.
- **`secret_refs`** — the `secret://` URIs that point at the `Connection`'s credential material. The URIs themselves are persisted; the credentials they reference are stored separately (per Stage 6).
- **`access_token_expires_at`** — the absolute timestamp at which the current access token is expected to expire, in RFC 3339 format with timezone. Used by the refresh-window logic (§5.2). MAY be null when the access token is non-expiring (long-lived API key, AWS SigV4 with static keys).
- **`refresh_token_expires_at`** — the absolute timestamp at which the current refresh token expires, when known. Some `Provider`s do not expose refresh-token expiry; the field MAY be null in those cases. When non-null and the refresh-token expiry has passed, the `Connection` is `expired` and not refreshable; re-authentication is required.
- **`last_dispatched_at`** — the timestamp of the most-recent successful dispatch against this `Connection`. Used for stale-`Connection` detection (a `Connection` with no recent dispatches may be a candidate for proactive re-auth or for archival).

### Optional fields

A `Conforming Implementation` MAY additionally persist:

- **`scopes_granted`** — the set of OAuth scopes the `Provider` granted at consent time. Useful for surfacing to users ("this connection has these capabilities") and for the local scope-enforcement check that Stage 6 specifies.
- **`last_refreshed_at`** — the timestamp of the most-recent successful refresh. Useful for telemetry and for diagnosing rotation-handling bugs.
- **`error_history_sample`** — a bounded sample of recent error-state transitions, with timestamps and root causes. Useful for surfacing to users and for diagnosing recurrent failures.
- **`provider_account_metadata`** — `Provider`-supplied identifying metadata about the connected account (the user's email at the `Provider`, the workspace name, etc.). Useful for user-facing labels but not load-bearing for the lifecycle.

### Persistence boundaries

The persistence layer's responsibilities end at the metadata above. The lifecycle layer reads the metadata at process start, reconstructs each `Connection`'s state, and resumes its operation from that state. The credential store (the resolution endpoint of the `secret://` URIs) is consulted lazily on dispatch and refresh; the lifecycle does not eagerly load all credentials at startup.

The persistence format — relational database, key-value store, encrypted on-disk file — is implementation-defined. The encryption posture for the metadata at rest is **Stage 6 (security)**; this stage requires the fields exist, not how they are encrypted.

### Concurrency at restart

When a process restarts, multiple `Connection`s recover from persisted state simultaneously. The implementation MUST NOT assume that all `Connection`s are valid at startup; some may have transitioned to `revoked` during the downtime (a `Provider` revoked the credential), and the implementation will only learn of the revocation on the next dispatch or refresh attempt. The persisted `state` is an optimistic record; the authoritative state is what the `Provider` returns.

## 5.7 Conformance summary

This section summarizes the lifecycle conformance levels for a `Conforming Implementation` of `v1.x`.

### MUST requirements

A `Conforming Implementation` MUST:

- Implement the §5.1 state machine with all the spec-mandated transitions.
- Hold a per-`Connection` single-flight lock around refresh so concurrent dispatches produce at most one refresh exchange.
- Support lazy refresh: when dispatch is attempted against an `expired` `Connection`, refresh and then dispatch.
- Handle refresh-token rotation atomically: when a refresh response includes a new `refresh_token`, replace the prior one in durable storage before transitioning to `active`.
- Stop accepting dispatch calls against `revoked` `Connection`s and surface `auth_expired` with a clear re-authentication indication.
- Implement the re-authentication path that recovers a terminal `Connection`.
- Persist the `connection_id`, `state`, `uacp_artifact` reference, `secret_refs`, `access_token_expires_at`, `refresh_token_expires_at`, and `last_dispatched_at` per §5.6.
- Treat `revoked` as terminal: never auto-transition out of `revoked` to `active` without re-authentication.
- Drain operations queued at the moment of revocation with the appropriate failure code.

### MUST NOT requirements

A `Conforming Implementation` MUST NOT:

- Issue dispatch wire requests using a token the runtime knows to be expired.
- Lose the new refresh token from a rotation response while invalidating the old one (the connection-killing bug).
- Discard the prior refresh token when the refresh response omits a new `refresh_token` field.
- Auto-transition a `revoked` `Connection` back to `active` without re-authentication.
- Silently retry dispatch indefinitely against a `revoked` or terminal-`error` `Connection`.
- Act on revocation webhooks without verifying their authenticity.

### SHOULD requirements

A `Conforming Implementation` SHOULD:

- Support proactive refresh: a background scheduler refreshes tokens within the refresh window before dispatch needs them.
- Use the `expiring` state to coordinate proactive refresh with concurrent dispatch.
- Call the `Provider`'s revocation endpoint (per RFC 7009) on user-initiated revocation when the endpoint is available.
- Retain the `Connection` identifier across re-authentication for audit and reference coherence.
- Expose a re-authentication affordance to the user.
- Auto-retry refresh from terminal `error` state on a backoff schedule, with a manual-retry escape hatch.
- Use a durable-storage backend that confirms write durability synchronously for refresh-token rotation persistence.

### MAY requirements

A `Conforming Implementation` MAY:

- Skip the `expiring` state and transition directly from `active` to `expired` (if proactive refresh is not implemented).
- Support reactive refresh: refresh-then-retry on `auth_expired` from dispatch.
- Listen for revocation webhooks from `Provider`s that expose them.
- Persist `scopes_granted`, `last_refreshed_at`, `error_history_sample`, and `provider_account_metadata` in addition to the required fields.
- Mint a new `Connection` identifier on re-authentication when its audit posture requires it, recording the old-to-new identifier mapping separately.
- Use a more conservative refresh window (refresh earlier than the spec-defined window).

### Cumulative conformance

The cumulative effect of the above is that a `Conforming Implementation` of `v1.x` keeps `Connection`s `active` across token expiration without dispatch-path latency surprises, recovers from refresh-token-rotation events without losing credentials, propagates revocation correctly across local state and across the `Provider` boundary, gives users a clear path back from terminal failures, and survives process restarts without losing the lifecycle context. The lifecycle layer is the layer where UACP's long-term operational contract lives — Stage 4 dispatched a single call; this stage manages the steady-state operation of `Connection`s across the time horizon of months and years.
