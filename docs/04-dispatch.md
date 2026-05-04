# UACP Dispatch Runtime

This document specifies the dispatch runtime of UACP `v1.x`. It defines how a `Conforming Implementation` invokes an `Operation` declared in a `.uacp` artifact: the connection-level dispatch configuration, the HTTP transport rules, the retry policy under transient failure, the runtime semantics of the pagination patterns Stage 3 declares as metadata, the rate-limit handling contract, the error-envelope normalization, the streaming response contract, and the optional idempotency-key injection. The conformance keywords ("MUST", "MUST NOT", "SHOULD", "SHOULD NOT", "MAY") in this document are interpreted per BCP 14 [[RFC2119](https://datatracker.ietf.org/doc/html/rfc2119)] [[RFC8174](https://datatracker.ietf.org/doc/html/rfc8174)] as established in [Stage 0 — Primer](./00-primer.md).

This document is consistent with the foundational principles in [Stage 1 — Principles](./01-principles.md), the authentication subsystem in [Stage 2 — Authentication](./02-authentication.md), and the schema and discovery layer in [Stage 3 — Schema](./03-schema.md). Where this document refines a principle, it does so by narrowing detail; it does not override.

## 4.0 Overview

A UACP `Connection` is the runtime pairing of an `Authentication Method` (Stage 2) with a callable `Operation` set (Stage 3). The dispatch runtime is the layer that turns an `Operation` invocation into an HTTP request, manages the request's lifecycle through retries and rate limits, exposes the response — buffered or streaming — to the caller, and normalizes failures into the uniform vocabulary of Principle 8.

This stage specifies four classes of runtime behavior:

1. **Connection-level configuration.** The top-level `dispatch` block of a `.uacp` artifact, alongside `authentication` and `operations`, carries connection-wide defaults — base URL, default headers, default timeout — that compose with each `Operation`'s request shape from Stage 3 (Sections 4.1 through 4.2).
2. **Failure handling.** Retry policy under transport failure and `5xx` (Section 4.3); rate-limit handling under `429` (Section 4.5); error-envelope normalization across `Provider`-specific shapes (Section 4.6).
3. **Loop semantics.** Pagination loops, taking Stage 3's pagination metadata and turning it into the runtime contract that walks pages until exhaustion (Section 4.4).
4. **Response surfaces.** Streaming response transport patterns and parser expectations (Section 4.7); optional idempotency-key injection for non-idempotent operations (Section 4.8).

### In scope

- The `dispatch` configuration block, base URL composition, default headers, and timeout handling (§4.1).
- HTTPS-only transport, TLS minimum, redirect behavior, connection reuse (§4.2).
- Retry policy with exponential backoff and jitter, idempotency-aware retry decisions (§4.3).
- Pagination runtime semantics for the `cursor`, `offset`, `link_header`, and `none` patterns (§4.4), and the safety limit on page count.
- Rate-limit handling: `429` with `Retry-After`, `X-RateLimit-*` advisory headers, cross-operation rate-budget pooling within a single `Connection` (§4.5).
- Error-envelope extraction and the canonical error shape returned to callers (§4.6).
- Streaming response transport: chunked transfer, Server-Sent Events, NDJSON, WebSocket upgrade; per-pattern parser expectations and termination semantics (§4.7).
- Idempotency-key injection for non-idempotent operations (§4.8).
- Conformance summary (§4.9).

### Out of scope

The following are deferred to later stages and MUST NOT be inferred from this document:

- **Connection lifecycle.** State machine (`pending` / `active` / `expiring` / `refreshing` / `expired` / `revoked` / `error`), refresh-worker scheduling, refresh-token rotation, revocation propagation, and re-authentication are **Stage 5 (lifecycle)**. Where the dispatch runtime interacts with lifecycle state — for example, when a dispatched call surfaces an `auth_expired` failure that triggers refresh-then-retry — this stage states the runtime's *behavior at the boundary*; Stage 5 owns the lifecycle state transition itself.
- **Security model.** Encryption-at-rest of credentials, scope enforcement at dispatch time, audit logging of dispatch attempts and outcomes, the threat model, and the trust posture for ingested artifacts are **Stage 6 (security)**. Section 4.8's note about persisting injected idempotency keys for audit defers the audit-event schema to Stage 6.
- **Schema authoring.** How the `dispatch` block, the `Operation` schemas, or the pagination metadata arrived in the artifact — OpenAPI ingestion, `curl`-paste, LLM inference — is Stage 3's territory and not revisited here. The runtime consumes the schemas as canonical.

Where a section in this document approaches one of those boundaries, the boundary is named explicitly.

## 4.1 Connection-level dispatch configuration

A `.uacp` artifact MUST include a top-level `dispatch` object alongside `authentication` (Stage 2) and `operations` (Stage 3). The `dispatch` block carries the connection-wide configuration that the runtime composes with each `Operation`'s `request` shape to produce the wire request.

The `dispatch` block has the following fields:

- **`base_url`** (required, string) — the absolute HTTPS URL of the `Provider`'s API root. Every `Operation`'s `request.path` per §3.2 is resolved relative to `base_url`. The resolution rule is: the final URL is `base_url` with its path component replaced by `base_url`'s path joined to the operation's `request.path`, preserving exactly one `/` between them; the operation's path-template substitution from `request.path_parameters` is performed before this join. Query-string composition is described below. `base_url` MUST start with `https://`; values starting with `http://` MUST be rejected at validation time per §3.10 and Principle 11.
- **`default_headers`** (optional, object) — a flat map from HTTP header name to header value. These headers are added to every dispatched request before composition with the operation's `request.headers`. When an operation's `request.headers` declares the same header name as `default_headers`, the operation-level value wins. Authentication headers MUST NOT appear in `default_headers`; they are produced by the Stage 2 authentication subsystem.
- **`default_timeout_ms`** (optional, integer, default `30000`) — the request timeout in milliseconds, applied per dispatch attempt. The timeout governs the period from connection initiation to receipt of the final response byte (or the first chunk for streaming responses; see §4.7). An operation MAY override the timeout through a per-operation field; the override format is described below.
- **`default_user_agent`** (optional, string) — the value of the `User-Agent` request header, applied to every dispatched request. Some `Provider`s require or strongly prefer a stable `User-Agent` for abuse-detection and rate-limiting purposes; this field provides a single place to declare it. When omitted, the implementation chooses a default, typically of the form `uacp/<implementation-version>`.

### Example

```json
{
  "dispatch": {
    "base_url": "https://api.example.com",
    "default_headers": {
      "Accept": "application/json",
      "X-Provider-Tenant": "acme-prod"
    },
    "default_timeout_ms": 15000,
    "default_user_agent": "uacp-broker/0.1"
  }
}
```

### Composition order

The runtime composes a wire request in the following order. Earlier steps establish the request skeleton; later steps override or extend it.

1. The wire URL begins as `dispatch.base_url`.
2. The path is computed by substituting the operation's `request.path_parameters` into the operation's `request.path` template per RFC 6570; the resulting path is joined to `base_url`'s path with exactly one `/` separator.
3. The query string is computed by serializing the operation's `request.query_parameters` per `application/x-www-form-urlencoded` rules, with array-valued parameters serialized as repeated keys.
4. The wire headers begin as `dispatch.default_headers` (a copy; the original is not mutated).
5. The operation's `request.headers` are merged in, with operation-level header values overriding connection-level defaults of the same name (case-insensitive per RFC 9110 §5.1).
6. If `dispatch.default_user_agent` is present and the merged headers do not already contain a `User-Agent`, the default is added.
7. The Stage 2 authentication subsystem applies its credential-bearing fields per the `authentication.method` declared in the artifact: header-based methods set their header; query-parameter-based methods append to the query string; signed-request methods compute the signature over the request shape produced by steps 1-6 and add their signature header. Authentication-set fields override any colliding fields from earlier steps with no warning, because the authentication subsystem's correctness depends on its values reaching the wire unmodified.
8. The body, if any, is the operation's `request.body`, serialized per its declared `media_type`. The runtime sets `Content-Type` to `body.media_type` and `Content-Length` to the serialized body's byte length unless `Content-Type` is already explicit in the merged headers.

### Per-operation overrides

An operation MAY override `default_timeout_ms` by declaring `timeout_ms` at the top level of the operation entry alongside `request` and `response`:

```json
{
  "id": "long_running_export",
  "summary": "Export the full account history.",
  "timeout_ms": 600000,
  "request": { /* ... */ },
  "response": { /* ... */ }
}
```

`timeout_ms` is not a request-shape field — it does not appear inside `request` — because it is a runtime concern rather than a wire-shape concern. Operation-level retry policy overrides (§4.3) follow the same pattern.

### Base-URL resolution rule

When `dispatch.base_url` is, for example, `https://api.example.com/v3` and an operation's `request.path` is `/users/{user_id}`, the wire URL is `https://api.example.com/v3/users/{user_id}` (with `{user_id}` substituted before transmission). The single-slash join rule means an operation declaring `request.path = "users/{user_id}"` (no leading slash) and an operation declaring `request.path = "/users/{user_id}"` (with leading slash) produce the same wire URL; authoring tools SHOULD prefer the leading-slash form for clarity, but the runtime MUST treat them equivalently.

## 4.2 HTTP transport rules

UACP `v1.x` mandates HTTPS as the sole transport per Principle 11. This section specifies the transport-level rules that a `Conforming Implementation` MUST satisfy when dispatching a request.

### TLS

- A `Conforming Implementation` MUST use TLS 1.2 or higher when establishing a connection to a `Provider`. TLS 1.1, TLS 1.0, SSLv3, and SSLv2 are forbidden; an implementation that negotiates one of these MUST abort the connection with `bad_input` before sending any data.
- A `Conforming Implementation` SHOULD prefer TLS 1.3 when the `Provider` supports it.
- A `Conforming Implementation` MUST validate the `Provider`'s TLS certificate against a system-managed trust store. Disabling certificate validation ("insecure mode") MUST NOT be a default; implementations MAY expose it as a development-only configuration flag, but a flagged-as-insecure connection MUST surface to the dispatch runtime in a way that callers can refuse.
- A `Conforming Implementation` MAY pin certificates per `Provider`; pinning is an implementation feature, not a UACP requirement.

### Connection reuse

A `Conforming Implementation` SHOULD reuse TLS connections across dispatch calls to the same `Provider` (HTTP/1.1 keep-alive or HTTP/2 multiplexing). Connection reuse improves dispatch latency and reduces TLS handshake load on the `Provider`; it is not normatively required because some implementations deliberately use ephemeral connections for security or telemetry reasons. The connection-pool sizing, idle-eviction policy, and per-`Provider` connection limit are implementation-defined.

### HTTP version

A `Conforming Implementation` MUST support HTTP/1.1. A `Conforming Implementation` SHOULD support HTTP/2 when the `Provider` advertises it via ALPN. HTTP/3 (QUIC-based) MAY be supported; it is not required because the deployment surface for HTTP/3 against arbitrary long-tail `Provider`s is not yet uniform enough for a `MUST`.

### Redirects

`3xx` redirect responses are handled by the dispatch runtime, not the caller. The runtime MUST follow `301`, `302`, `303`, `307`, and `308` redirects up to a per-call redirect limit. The redirect rules:

- The redirect limit is **at most 5 redirects per dispatch call**. A `Conforming Implementation` MUST NOT follow more than 5 redirects in a single call. Exceeding the limit is a `upstream_error` per Principle 8.
- A `Conforming Implementation` MUST detect redirect loops (the same URL appearing twice in the redirect chain) and abort with `upstream_error` rather than continuing to the limit.
- For `301`, `302`, and `303`, when the original request method is not `GET` or `HEAD`, the runtime SHOULD NOT silently change the method to `GET` against the new URL. Some `Provider`s and some legacy specifications normatively required this behavior; UACP's stricter posture is that a method-changing redirect is surprising enough to warrant surfacing as `upstream_error` and letting the artifact author decide whether to add a separate `GET`-shaped operation. An implementation MAY be more permissive when the artifact's `dispatch` block carries an explicit `allow_method_changing_redirects: true` flag; when omitted, the strict default applies.
- For `307` and `308`, the original request method is preserved per RFC 9110 §15.4; the runtime MUST replay the request method, headers, and body against the new URL.
- The redirect target's URL MUST be HTTPS. A `3xx` response whose `Location` header points at an `http://` URL MUST be treated as `upstream_error`; the redirect MUST NOT be followed even if the artifact's strictness flag is permissive.
- Redirects across `Provider` origins (a different host than `dispatch.base_url`) MUST drop authentication-bearing headers from the replayed request to avoid leaking credentials to a different origin. The redirect is followed if the redirect-limit and HTTPS rules permit, but with no `Authorization` header, no `X-API-Key`, and no signature on the replay. The expectation is that cross-origin redirects either succeed because they are public endpoints (CDN-shaped resources, for example) or fail with `forbidden`, at which point the caller knows to investigate.

### Method semantics under retry

The HTTP method governs whether retries are safe. Per RFC 9110 §9.2.2, `GET`, `HEAD`, `PUT`, `DELETE`, and `OPTIONS` are idempotent and safe to retry from the protocol perspective; `POST` and `PATCH` are not. UACP's retry policy in §4.3 follows this baseline, with one refinement: an `Operation`'s `idempotency` field from §3.1 may upgrade a `POST` or `PATCH` to retryable when the artifact author has confirmed that a retry against the specific `Provider`'s endpoint is safe.

### Request body size

UACP imposes no normative limit on request body size; the limit is whatever the `Provider` accepts. Implementations SHOULD surface large body sizes (over a few megabytes) as a warning during authoring but MUST NOT reject at dispatch time on size grounds alone.

## 4.3 Retry policies

Transient failures — TCP resets, TLS handshake failures, DNS resolution timeouts, idle-connection drops, and `5xx` server errors that may resolve on retry — are common across the long-tail HTTPS surface UACP targets. The dispatch runtime retries these failures automatically when the operation's method semantics make retry safe; non-idempotent operations are not retried automatically because a retry could double a side effect.

### Default retry policy

A `Conforming Implementation` MUST implement the following default retry policy. The defaults are normative; they are what every `Conforming Implementation` produces when an operation does not declare an override.

| Aspect | Default |
|---|---|
| Maximum attempts | 3 (i.e., the original attempt plus up to 2 retries) |
| Initial backoff | 250 ms |
| Backoff multiplier | 2 (delay doubles each retry: 250 ms, 500 ms, 1000 ms) |
| Maximum backoff | 5000 ms (cap on the per-retry delay) |
| Jitter | ±25% of the computed delay, applied uniformly |
| Total time budget | None; the per-attempt timeout governs each individual attempt |

The jitter is applied uniformly: the actual delay is uniformly sampled from the interval `[delay × 0.75, delay × 1.25]`. Jitter prevents the synchronized-retry stampede that occurs when many clients of the same `Provider` retry on the same exponential schedule.

### When the runtime retries

The runtime retries a dispatched call when ALL of the following are true:

1. The attempt failed with one of: a TLS or TCP error before the request was sent in full; a connection reset or read timeout after the request was sent but before the response was complete; a `5xx` response status; or a transport-level error that an implementation classifies as transient (DNS resolution failure, connection-pool exhaustion that may resolve on retry).
2. The operation is retryable. An operation is retryable if its HTTP method is one of `GET`, `HEAD`, `PUT`, `DELETE`, `OPTIONS`, OR if its `idempotency` field per §3.1 is `idempotent`. `POST` and `PATCH` operations whose `idempotency` is `not_idempotent` or `unknown` MUST NOT be retried automatically.
3. The maximum attempt count for the operation has not yet been reached.

When the runtime retries, it dispatches the same wire request — same method, same path, same headers, same body — against the same `base_url`. Authentication-bearing headers are recomputed if the authentication subsystem is signature-based (some signatures embed timestamps); otherwise the headers are reused.

### When the runtime does not retry

The runtime does not retry a dispatched call when ANY of the following are true:

- The response status is `4xx` (except `429`, which has its own treatment per §4.5). `4xx` indicates a client error: the request as constructed is wrong, and retrying it unchanged will produce the same error. The runtime surfaces `4xx` as the appropriate failure code (`bad_input` for `400`, `auth_expired` or `forbidden` for `401`/`403` per the surrounding context, `not_found` for `404`).
- The operation is non-idempotent and the failure occurred after the request body was sent. Even if the response was a `5xx`, the side effect on the `Provider` may have been applied; retrying could double the side effect. The runtime surfaces `upstream_error` and lets the caller decide whether to retry at the application layer (typically with an idempotency key, per §4.8).
- The attempt was cancelled by the caller (the dispatch invocation's cancellation surface — implementation-defined, typically a context or signal — was triggered). Cancellation is not a failure; it is a deliberate caller decision and the runtime surfaces `cancelled`.

### Per-operation retry overrides

An operation MAY declare a `retry` object at its top level that overrides the default policy for that operation alone. The override schema:

```json
{
  "id": "fetch_user",
  "summary": "Fetch a user record by id.",
  "retry": {
    "max_attempts": 5,
    "initial_delay_ms": 100,
    "max_delay_ms": 2000,
    "jitter": 0.5
  },
  "request": { /* ... */ },
  "response": { /* ... */ }
}
```

Field semantics:

- `max_attempts` (optional, integer, default `3`) — overrides the maximum-attempts default. Must be at least `1` (which disables retry).
- `initial_delay_ms` (optional, integer, default `250`) — overrides the initial backoff.
- `max_delay_ms` (optional, integer, default `5000`) — caps the per-retry delay after exponential growth.
- `jitter` (optional, number in `[0, 1]`, default `0.25`) — fractional jitter applied symmetrically. `0` means no jitter; `0.5` means ±50%.
- `multiplier` (optional, number, default `2`) — backoff multiplier per retry.

A retry override on an operation does not change the operation's idempotency semantics. An operation declared as `idempotency: not_idempotent` with a retry override is still not retried automatically by the runtime; the override applies only to the operation's *retryable failures* (the connection-error class), not to `5xx` responses where retry would risk double side effects.

### Backoff state across retries

Backoff is per-operation, per-call. Two unrelated operations against the same `Provider` are not coordinated; each runs its own backoff schedule. Cross-operation coordination is the rate-limit handling in §4.5, which is `Connection`-wide rather than operation-wide.

## 4.4 Pagination loops

Stage 3 specified the metadata that declares an operation's pagination pattern (§3.4): `cursor`, `offset`, `link_header`, or `none`. This section specifies the runtime's iteration behavior given that metadata. The metadata is necessary; this stage is what makes it sufficient.

### General loop contract

A paginated operation invocation is a *loop*, not a *single call*. The dispatch runtime issues the first page request, examines the response to determine whether more pages exist, advances to the next page, and repeats until the loop terminates. From the caller's perspective, the result is a sequence of pages that the caller may consume incrementally (the typical streaming-API surface) or aggregate into a single buffered list.

The aggregation surface is implementation-defined. A `Conforming Implementation` MAY expose paginated dispatch as a streaming iterator (each page is an event), as a callback-on-page mechanism, or as an aggregating call that returns the concatenation of all page bodies. The spec specifies the loop's termination contract; the surface is left to implementations.

### Per-pattern iteration

#### `cursor`

The runtime extracts the next cursor from the response body using the JSONPath in `pagination.response_cursor_path`. The loop terminates when one of the following is true:

- The path evaluates to a missing value (no field, or `null`).
- The path evaluates to an empty string.
- The path evaluates to a value identical to the cursor used in the immediately preceding request (the `Provider` returned the same cursor twice — a degenerate case that some `Provider`s exhibit when the result set is empty; the loop MUST NOT proceed to a third request with the same cursor).

When the loop continues, the runtime adds the cursor to the next request's `request_cursor_parameter` (in the query string or body, per where the parameter is declared in the operation's request shape). The first request omits the cursor (the parameter is not added at all); some `Provider`s require the cursor parameter to be absent on the first call, and the runtime conforms to that expectation.

#### `offset`

The runtime tracks `offset` and `limit` across the loop. The first request uses `offset = 0` (or whatever default the operation's `request.query_parameters` declare for the offset parameter; the runtime SHOULD honor the declared default for the first call) and `limit` per the operation's declared default. Each subsequent request uses `offset = offset + page_size`, where `page_size` is the actual number of records returned in the previous page (which may be less than `limit` on the last page).

The loop terminates when:

- `pagination.response_has_more_path` is declared and the resolved value is `false`.
- `pagination.response_total_path` is declared and `offset + page_size >= total`.
- The previous page's record count is zero.
- The previous page's record count is less than `limit` AND `response_has_more_path` is not declared (a heuristic: a partial page is conventionally the last page, but this heuristic is overruled by an explicit `has_more`).

When both `response_has_more_path` and `response_total_path` are present, `response_has_more_path` takes precedence per §3.4.

#### `link_header`

The runtime parses the response's `Link` header per RFC 8288 [[RFC8288](https://datatracker.ietf.org/doc/html/rfc8288)] and follows the `rel="next"` URI for the next request. The next-page URI is used as the wire URL for the next request — replacing `dispatch.base_url + request.path + query_string` for that call only — and the next page's request method, headers, and body MUST match the original operation's. If the `Link` header is absent or contains no `rel="next"`, the loop terminates.

The `rel="next"` URI MUST be HTTPS and MUST share `dispatch.base_url`'s origin (scheme + host + port). A `Provider` returning a `Link: rel="next"` URI with a different origin is treated as a redirect across origins per §4.2, with the same authentication-stripping rule. A cross-origin `rel="next"` URI is an unusual and likely-misconfigured case; the runtime MUST surface a warning and SHOULD treat it as the end of the loop rather than continue with stripped authentication.

#### `none`

`none` is not a loop; the operation makes a single request and returns. Operations with `pagination: {pattern: "none"}` (or no `pagination` block at all) bypass the loop entirely.

### Safety: per-call max-pages

The runtime MUST honor a per-call maximum-pages limit. The default is **100 pages**. A `Conforming Implementation` MUST allow this limit to be configured at the `Connection`, the operation, or the per-call level; the configuration surface is implementation-defined.

Reaching the maximum-pages limit terminates the loop with a `upstream_error` distinct from a normal end-of-pages termination. The runtime MUST surface this as a distinguishable failure so callers can tell "the result set ended" from "the loop hit the safety limit." A misconfigured pagination pattern (a JSONPath that always returns a non-empty cursor; a `Provider` that never returns `has_more: false` even when the result set is exhausted) would otherwise loop indefinitely; the safety limit is the dispatch-level check that prevents this.

### Pagination interaction with retry

Each page request inside a loop is subject to §4.3's retry policy independently. A retry of page N does not reset progress through the loop; on success after retry, the loop continues to page N+1.

### Pagination interaction with streaming

When an operation declares both `pagination` (with a pattern other than `none`) and `response.streaming: true` per §3.3, each page is a complete stream that the runtime consumes in full before fetching the next page. The streaming chunks within a page are delivered to the caller in order, the page-end marker (per §4.7) terminates the within-page stream, the runtime examines the response shape for the next-cursor / Link-header / etc. (these MAY appear in stream metadata or as a final non-chunk frame; the artifact's response schema declares where), and then the next page is dispatched. The caller observes a continuous stream of chunks that happens to span multiple HTTP requests.

## 4.5 Rate-limit handling

`Provider`s rate-limit their `Connection`s, and a `Conforming Implementation` must handle rate-limit responses gracefully both when they fire (the `429` case) and, when the `Provider` advertises it, anticipatorily based on advisory headers.

### `429 Too Many Requests`

A `429` response per RFC 6585 is the signal that the runtime has exceeded the `Provider`'s rate limit. The runtime MUST:

1. **Honor `Retry-After` when present.** The `Retry-After` response header carries either a number of seconds to wait or an HTTP-date past which the request may be retried, per RFC 9110 §10.2.3. The runtime MUST respect this delay before retrying. When `Retry-After` is present and parseable, it overrides §4.3's exponential backoff for the retry. When `Retry-After` is missing or malformed, the runtime falls back to §4.3's default backoff, with the rate-limit retry counted against the `429`-specific maximum (below).
2. **Apply a `429`-specific maximum retry count.** The default is **3 retries** for `429` responses; this is independent of the `5xx` retry budget. After the third `429` retry fails (or `Retry-After` exceeds a sanity threshold; see below), the runtime surfaces `rate_limited` to the caller per Principle 8.
3. **Cap the `Retry-After` value.** Some `Provider`s return absurd `Retry-After` values (hours or days) under load. The runtime MUST cap the wait at a sanity threshold — the default is **120 seconds** — and surface `rate_limited` immediately if `Retry-After` exceeds the cap, rather than blocking the dispatch call for an unreasonable duration. The cap is configurable.

`429` retries do not consume the `5xx` retry budget; the two budgets are separate. An operation that fails with `503` then `429` then `503` exhausts its `5xx` budget at attempt 3 (per §4.3) regardless of the intervening `429`.

### Advisory headers

Many `Provider`s advertise rate-limit state in response headers before the limit is exceeded:

- `X-RateLimit-Limit` — the rate-limit ceiling (typically requests per minute or per hour).
- `X-RateLimit-Remaining` — the number of requests remaining in the current window.
- `X-RateLimit-Reset` — the time at which the window resets, either as a Unix timestamp or as seconds-until-reset (the encoding varies by `Provider`).

A `Conforming Implementation` SHOULD consume these headers when present and use them to anticipate rate limits. The recommended behavior:

- Track `remaining` per `Provider` (per `Connection` is the natural scope; see "Cross-operation pooling" below).
- When `remaining` drops below a configurable threshold (default: `5` requests), introduce a small delay before subsequent dispatches against the same `Connection` to spread load across the window.
- Treat `remaining: 0` with a `Reset` in the future as equivalent to a `429` response: pause until the reset time, then resume.

The advisory headers are not normatively required to be respected — some `Provider`s emit them but do not enforce, and some emit them with inconsistent semantics — but consuming them improves dispatch-success rates and reduces the volume of `429` responses overall.

`Provider`-specific header conventions (some use `X-Rate-Limit-*` rather than `X-RateLimit-*`, some use `RateLimit-*` per the in-progress IETF draft, some emit a `RateLimit-Policy` header describing the limit windows) are recognized in practice and a mature implementation handles them. The spec registers `X-RateLimit-Remaining` and `X-RateLimit-Reset` as the SHOULD-consumed pair for v1.0; future v1.x releases MAY register additional patterns through §2.8.

### Cross-operation rate-budget pooling

A `Provider`'s rate budget is typically shared across all operations issued on a single `Connection` (the same OAuth token, the same API key, the same client identifier). The dispatch runtime SHOULD track rate-limit state per `Connection`, not per `Operation`: a `429` returned by `list_messages` informs the runtime that subsequent calls to `send_message` against the same `Connection` are likely to be rate-limited too.

The pooling rule:

- The runtime SHOULD maintain per-`Connection` rate-limit state derived from the most recent `X-RateLimit-*` headers observed on any operation against that `Connection`.
- On a fresh `429`, the runtime SHOULD apply the indicated `Retry-After` to all subsequent dispatch calls against the same `Connection` until the wait elapses, not just to the operation that received the `429`.
- The runtime MUST NOT block forever; if two operations against the same `Connection` are concurrently dispatching when a `429` arrives, the runtime applies the `Retry-After` to both rather than independently retrying each and producing parallel `429`s.

The pooling behavior is `SHOULD` rather than `MUST` because some `Provider`s carve their rate budget per-operation (different endpoints have different limits) or per-scope (different OAuth scopes draw from different buckets). An implementation that pools across all operations MAY over-conservatively delay calls that would have succeeded; an implementation that does not pool MAY under-respect a `Connection`-wide limit. The trade-off is implementation-defined; the recommended default is to pool.

## 4.6 Error envelope handling

Stage 3's `response` schemas (§3.3) declare structured error envelopes under the appropriate status range or status code. The dispatch runtime extracts those envelopes and surfaces them to the caller in a canonical shape, regardless of the `Provider`-specific envelope variations.

### The canonical error shape

A `Conforming Implementation` MUST expose dispatch failures to the caller in the following shape:

```json
{
  "status": 401,
  "code": "auth_expired",
  "message": "Access token expired",
  "details": {
    "provider_error": "token_expired",
    "expired_at": "2026-05-04T15:00:00Z"
  },
  "raw": {
    "ok": false,
    "error": "token_expired",
    "expired_at": "2026-05-04T15:00:00Z"
  }
}
```

Field semantics:

- **`status`** (integer) — the HTTP status of the response that produced the failure, or `0` if the failure was at the transport layer (TLS error, DNS failure, etc.).
- **`code`** (string) — the normalized failure code from Principle 8's vocabulary: `auth_expired`, `rate_limited`, `bad_input`, `upstream_error`, `not_found`, `forbidden`, `cancelled`. The runtime computes this code from the HTTP status, the `Provider`'s envelope, and the dispatch context (a `5xx` after retry exhaustion is `upstream_error`; a `429` after retry exhaustion is `rate_limited`; a `401` is typically `auth_expired` though `forbidden` is appropriate when the credential is valid but the operation is denied).
- **`message`** (string) — a human-readable description of the failure. The runtime SHOULD prefer a message extracted from the `Provider`'s envelope when the envelope was matched; otherwise the runtime SHOULD synthesize a clear message from the HTTP status and the response body.
- **`details`** (object) — a structured object whose contents are extracted from the envelope when one was matched, or empty otherwise. The shape of `details` is `Provider`-specific; the runtime MUST preserve fields from the matched envelope verbatim under their original names.
- **`raw`** (any) — the response body as received from the `Provider`, parsed if the `Content-Type` is `application/json` and otherwise included as a string. `raw` is the escape hatch for diagnostic and audit purposes; callers SHOULD prefer `code`, `message`, and `details` for control-flow decisions.

### Envelope matching

When a response status matches one of the operation's declared response keys (per §3.3) and the matching response entry declares a `body` schema, the runtime attempts to parse the response body against that schema. When parsing succeeds, the runtime extracts envelope fields per implementation convention — the typical mapping is "any field that looks error-shaped (`error`, `error_code`, `code`, `message`, `detail`, `errors[].message`) becomes part of `details`" — and populates `message` from the most informative such field. The exact extraction rule is implementation-defined; the spec's normative requirement is that the canonical shape is produced and that `details` carries the envelope fields verbatim.

When parsing the response body against the declared schema *fails* — the body does not match the declared envelope — the runtime falls back to the unmatched-envelope path: `details` is empty, `message` is synthesized from the HTTP status and `raw` carries the unparsed body.

### Unmatched responses

When a response status is not declared in the operation's `response` block, the runtime maps the status to the canonical shape using only HTTP-status semantics: `4xx` becomes `bad_input` (default) or one of the more specific codes when the status is well-known (`401` → `auth_expired`, `403` → `forbidden`, `404` → `not_found`, `429` → `rate_limited`); `5xx` becomes `upstream_error`; transport errors become `upstream_error` with `status: 0`. `details` is empty in the unmatched case; `raw` carries the response body.

### `code` mapping

The default mapping from HTTP status to canonical `code`:

| HTTP status | Canonical `code` |
|---|---|
| 400 | `bad_input` |
| 401 | `auth_expired` |
| 403 | `forbidden` |
| 404 | `not_found` |
| 408 | `upstream_error` (treated as transient; retried per §4.3) |
| 409 | `bad_input` (conflict; the request is wrong against the current state) |
| 422 | `bad_input` |
| 429 | `rate_limited` |
| 500 | `upstream_error` |
| 502 | `upstream_error` |
| 503 | `upstream_error` |
| 504 | `upstream_error` |
| Other 4xx | `bad_input` |
| Other 5xx | `upstream_error` |

A `Conforming Implementation` MAY refine this mapping using envelope-derived context: a `400` response whose envelope clearly indicates a missing-permission failure SHOULD map to `forbidden` rather than `bad_input`, for example. The mapping defaults are normative as the floor; implementation refinements that produce a more specific (less "default") code from envelope data are permitted and encouraged.

### Body-predicate evaluation

When a response carries a `failure_predicate` per §3.3, the dispatch runtime MUST evaluate the predicate against the response body before treating the response as a logical success. A `Conforming Implementation`:

1. Selects the response entry matching the response's HTTP status per §3.3 (exact match wins over status-range match wins over `default`).
2. If the matched entry declares `failure_predicate`, parses the response body and resolves `failure_predicate.path` against it per the §3.4 minimal JSONPath subset.
3. If the resolved value is `failure_predicate.equals` (deep-equal comparison on JSON literals), the response is a logical failure and the runtime constructs the canonical error shape per "The canonical error shape" above. The `status` field carries the original HTTP status verbatim — typically `200` — so audit trails remain faithful; the canonical `code` carries the normalized failure category.
4. If `failure_predicate.code_path` is declared, the runtime resolves it against the body and includes the extracted string in `details` under its original field name. The runtime SHOULD use the extracted string as input to the `code` mapping per "`code` mapping" above's MAY refinement clause — typically a small per-`Provider` lookup table that converts the `Provider`-specific error string into the canonical Principle 8 vocabulary.
5. If `failure_predicate.message_path` is declared, the runtime resolves it and uses the extracted string as the canonical `message` field. When absent, the runtime synthesizes a message from the HTTP status, the extracted `code`, and the body.
6. If the resolved value at `failure_predicate.path` is missing or does not equal `failure_predicate.equals`, the response is a logical success and the runtime proceeds with the existing 2xx-success path. A missing field is a non-match, NOT a parse error.

When the response body is not parseable as JSON (the `Content-Type` doesn't match the declared `body.media_type`, or the body is malformed), the runtime falls back to the existing 2xx-success path and surfaces the unparsed body to the caller. A predicate cannot be evaluated against a non-JSON body; the spec leaves this case to the unmatched-envelope path rather than raising at dispatch time.

The predicate is opt-in per response entry. Operations whose responses don't declare `failure_predicate` retain the existing status-only success/failure behavior — the predicate machinery is purely additive.

This affordance was added in the `v1.x` release that includes the §3.3 amendment. Per Principle 6 (wire-format stability), absence of `failure_predicate` is the default; `Conforming Implementation`s built against earlier `v1.x` releases that pre-date the amendment MAY decline artifacts that declare `failure_predicate` per §2.8 silent-decline rules, but SHOULD upgrade to consume the field given how common the body-shape failure pattern is in practice.

### What error handling does not do

Recovery — refresh-then-retry on `auth_expired`, scope-elevation on `forbidden`, surfacing rate-limit waits to the user — is **Stage 5 (lifecycle)** for the `auth_expired` path and is the agent's application-layer concern for the others. This stage produces the canonical failure; what to do with it is upstream of the runtime.

## 4.7 Streaming responses

When an operation's response declares `streaming: true` per §3.3, the dispatch runtime exposes a stream rather than a buffered response body. The stream consists of zero or more chunks, each conforming to the response's `body` schema (which describes one chunk's shape, not the whole stream).

### Supported transport patterns

A `Conforming Implementation` SHOULD support the following streaming transport patterns. Pattern selection is determined from the response's `Content-Type` header and, where applicable, the response's `Transfer-Encoding`:

- **Chunked transfer encoding** — `Transfer-Encoding: chunked` with a non-streaming `Content-Type` (e.g., `application/json`, where the entire body is a single JSON document delivered in HTTP chunks). The runtime delivers the parsed body to the caller once, after the chunk-stream is fully assembled. This pattern is "streaming on the wire, buffered to the caller" and is more about network efficiency than streaming semantics; UACP does not generally treat it as streaming for the response-schema purpose, but mature implementations recognize it for accurate transport handling.
- **Server-Sent Events (SSE)** — `Content-Type: text/event-stream` per the HTML Living Standard. Each event is delimited by `\n\n` (a blank line); within an event, the `data:` fields are concatenated to form the chunk payload. The runtime emits one chunk per SSE event. The `event:` and `id:` fields are exposed to the caller as event metadata when present. The runtime closes the stream when the connection closes or when the SSE format's `[DONE]` sentinel is encountered (the latter is a `Provider`-specific convention popularized by OpenAI-compatible endpoints; UACP recognizes it as a common pattern but does not normatively require it).
- **Newline-delimited JSON (NDJSON / JSON Lines)** — `Content-Type: application/x-ndjson` or `application/jsonl` (the type registry has not yet stabilized; many `Provider`s use `application/json` with implicit line-delimiting). Each chunk is one line; lines are parsed as independent JSON values. The runtime emits one chunk per parsed line. The stream closes when the connection closes; there is no mid-stream sentinel.
- **WebSocket upgrade** — `Connection: Upgrade, Upgrade: websocket` per RFC 6455. UACP `v1.0` declares HTTPS as the sole transport (Principle 11); WebSocket upgrades originating from HTTPS are permissible because they remain on the TLS-protected connection, but they are uncommon for request/response-shaped APIs. A `Conforming Implementation` MAY support WebSocket-upgrade streaming; when it does, each WebSocket message becomes one chunk emitted to the caller. WebSocket-upgrade support is `MAY` rather than `SHOULD` because the surface is rare for the long-tail HTTPS APIs UACP targets; future v1.x releases MAY promote it.

### Per-pattern parser expectations

- **SSE.** The runtime MUST handle multi-line `data:` accumulation per the SSE specification (multiple `data:` lines within an event are joined with `\n` to produce the chunk payload). The runtime SHOULD expose `event:` and `id:` metadata when present. The runtime MUST handle the `:` comment line (no chunk emitted) and the `retry:` reconnection-time line per the SSE specification.
- **NDJSON.** The runtime MUST handle the corner case where the connection delivers a partial line at the end of the stream (the line is buffered and not emitted unless and until a newline arrives). Some `Provider`s flush a partial line on connection close without a trailing newline; the runtime SHOULD emit the buffered line in that case if it parses as valid JSON, and SHOULD discard it otherwise with a warning.
- **WebSocket.** The runtime delivers one chunk per WebSocket text or binary frame. Control frames (ping, pong, close) are handled at the transport layer and do not surface as chunks.

### Stream termination

A streaming response terminates when one of the following occurs:

1. The transport-level connection closes cleanly (TCP FIN, WebSocket close frame).
2. A pattern-specific end marker is encountered (SSE `[DONE]` when the artifact declares it; the artifact MAY include a `streaming.end_sentinel` field on the response entry to make this explicit).
3. The per-call timeout elapses.
4. The caller cancels the dispatch.
5. A transport-level error occurs (TLS abort, TCP reset, idle-connection timeout); the stream terminates with `upstream_error`.

The runtime MUST surface the termination cause to the caller. A clean termination ("the stream ended naturally") is a successful dispatch with however many chunks were emitted; a transport-error termination is a `upstream_error` failure regardless of how many chunks were emitted before the failure.

### Streaming and retry

Streaming responses are NOT retried automatically when the failure occurs after the first chunk has been delivered. Once the caller has begun consuming chunks, a retry would re-deliver chunks the caller has already seen, potentially producing duplicate side effects. The runtime MAY retry a streaming response that fails *before* the first chunk is delivered (the connection failed during the request phase, or the response status was a retryable `5xx` before the body started); §4.3's retry policy applies in that pre-first-chunk window. After the first chunk is delivered, the dispatch is committed; retry is the caller's concern.

### Streaming and timeouts

The per-call timeout from §4.1 governs the period from connection initiation to the *first chunk* of a streaming response, not to the entire stream. Once chunks have started flowing, the runtime tracks an *idle timeout* — the period between consecutive chunks — which defaults to the per-call timeout but MAY be configured separately. An idle timeout exceeding the threshold terminates the stream with `upstream_error`.

## 4.8 Idempotency keys

The `Idempotency-Key` request header is a convention popularized by Stripe and adopted by a growing number of API providers: the client sends a unique key with a non-idempotent request, and the `Provider` deduplicates: if the same key is replayed, the prior response is returned without re-applying the side effect. The convention is documented in the IETF draft on idempotency keys (work in progress) and in many `Provider`-specific guides.

UACP's dispatch runtime MAY support automatic injection of an `Idempotency-Key` header for operations whose `idempotency` field is `idempotent` per §3.1 *and* whose HTTP method is non-idempotent (`POST` or `PATCH`). The combination is precisely the case where the artifact author has confirmed the operation can be safely re-executed but the HTTP method's default semantics would otherwise prevent retry: an `Idempotency-Key` lets the runtime retry a `POST` without risking a duplicated side effect, because the `Provider` deduplicates.

### Behavior when supported

When an implementation supports automatic idempotency-key injection:

- The runtime injects an `Idempotency-Key` header on every dispatch attempt whose method is `POST` or `PATCH` and whose operation `idempotency` is `idempotent`.
- The header value is a UUIDv4 generated per-dispatch (one key per logical dispatch call, reused across retries of that same dispatch). The retry semantics are precisely why the key exists: the key is generated once when the dispatch begins, and every retry within that dispatch carries the same key so the `Provider` deduplicates correctly.
- The runtime MUST persist the generated key alongside the dispatch's request log for audit and diagnostic purposes. The persistence format is implementation-defined; the audit-event schema is **Stage 6 (security)**.
- An implementation MAY allow the artifact's `dispatch` block to declare the header name (some `Provider`s use `X-Idempotency-Key` or `Request-Id`) via a `dispatch.idempotency_key_header` field defaulting to `Idempotency-Key`.
- An implementation MAY allow the caller to supply the key explicitly, overriding the auto-generated UUIDv4. This is the path callers use to enable application-layer idempotency: the caller computes a key derived from the user's intent and passes it through the dispatch surface, getting deduplication that survives even if the dispatch is initiated multiple times by the user.

### Behavior when not supported

An implementation that does not support automatic idempotency-key injection still honors the operation's `idempotency: idempotent` field for retry-eligibility per §4.3 — the runtime is still allowed to retry the operation; it just doesn't inject a key. The risk of a duplicated side effect on retry is then implicitly accepted by the artifact author when they declared the operation idempotent without a `Provider`-level deduplication mechanism.

A `Conforming Implementation` of `v1.x` MAY decline to support automatic idempotency-key injection. The conformance level is `MAY` because the surface is uneven across `Provider`s; many `Provider`s do not honor any idempotency-key convention and silently ignore the header, in which case automatic injection is no-op overhead. Implementations targeting a curated set of `Provider`s (Stripe, a growing list of others) SHOULD support injection because the value is concrete; implementations targeting the full long tail MAY skip it.

## 4.9 Conformance summary

This section summarizes the conformance levels for the dispatch runtime of a `Conforming Implementation` of `v1.x`. The summary is parallel in structure to §2.9 and §3.11.

### MUST requirements

A `Conforming Implementation` MUST satisfy all of the following:

- **HTTPS-only transport.** Every dispatch request uses `https://`; `http://` is rejected at validation time per §4.1 and at dispatch time as a defense-in-depth check.
- **TLS 1.2 minimum.** TLS 1.1 and below are forbidden per §4.2.
- **Certificate validation.** The `Provider`'s TLS certificate is validated against a system-managed trust store; insecure mode MUST NOT be a default per §4.2.
- **Redirect safety.** At most 5 redirects per dispatch call; redirect loops detected and aborted; redirect targets MUST be HTTPS; cross-origin redirects strip authentication-bearing headers per §4.2.
- **Default retry policy for idempotent operations.** Up to 3 attempts with exponential backoff (250 ms initial, 2× multiplier, 5000 ms cap, ±25% jitter) for transient transport failures and `5xx` responses per §4.3.
- **No retry of non-idempotent operations.** `POST` and `PATCH` whose `idempotency` is `not_idempotent` or `unknown` MUST NOT be retried automatically per §4.3.
- **No retry of `4xx` responses.** Except `429`, which is handled by §4.5; other `4xx` responses surface immediately per §4.3.
- **Pagination max-pages safety limit.** Default 100; configurable; reaching the limit terminates the loop with a distinguishable `upstream_error` per §4.4.
- **Pagination retry independence.** Each page request inside a paginated loop is independently subject to retry per §4.3 and §4.4.
- **`Retry-After` honor on `429`.** The runtime respects the `Retry-After` header when present and parseable, capped at the sanity threshold (default 120 seconds) per §4.5.
- **Canonical error shape.** Failures are surfaced to the caller in the `{status, code, message, details, raw}` shape per §4.6.
- **Code mapping.** HTTP status to canonical `code` follows the table in §4.6 by default.

### MUST NOT requirements

A `Conforming Implementation` MUST NOT:

- Dispatch to `http://` URLs.
- Negotiate TLS 1.1 or below.
- Follow `3xx` redirects to `http://` targets.
- Forward authentication-bearing headers across origin redirects.
- Retry non-idempotent operations automatically.
- Retry `4xx` responses other than `429`.
- Block forever on `Retry-After` values exceeding the sanity threshold.
- Re-deliver chunks already delivered to the caller on a streaming-response retry per §4.7.
- Drop the recorded idempotency key for an injected request without persisting it for audit per §4.8 and Stage 6.

### SHOULD requirements

A `Conforming Implementation` SHOULD:

- Prefer TLS 1.3 when the `Provider` supports it.
- Reuse TLS connections across dispatch calls to the same `Provider`.
- Support HTTP/2 when the `Provider` advertises it via ALPN.
- Treat method-changing redirects (`301`, `302`, `303` for non-`GET`/`HEAD` requests) as `upstream_error` rather than silently rewriting the method.
- Consume `X-RateLimit-Remaining` and `X-RateLimit-Reset` advisory headers when present, to anticipate rate limits before `429` per §4.5.
- Pool rate-limit state per `Connection` across all operations on that `Connection` per §4.5.
- Surface a warning when a `Link: rel="next"` URI is cross-origin per §4.4.
- Synthesize `message` from a matched envelope's most-informative field per §4.6.
- Refine `code` mapping using envelope-derived context when available per §4.6.
- Support SSE, NDJSON, and chunked-transfer streaming patterns per §4.7.
- Expose SSE `event:` and `id:` metadata to the caller per §4.7.

### MAY requirements

A `Conforming Implementation` MAY:

- Pin `Provider` certificates.
- Support HTTP/3 when available.
- Permit method-changing redirects when the artifact declares `dispatch.allow_method_changing_redirects: true`.
- Support per-operation retry overrides via the `retry` object per §4.3.
- Support per-operation timeout overrides via the `timeout_ms` field per §4.1.
- Support WebSocket-upgrade streaming per §4.7.
- Support automatic `Idempotency-Key` header injection for `POST`/`PATCH` operations declared `idempotent` per §4.8.
- Allow caller-supplied idempotency keys overriding the auto-generated value per §4.8.
- Allow the `Idempotency-Key` header name to be configured via `dispatch.idempotency_key_header` per §4.8.
- Refine retry decisions on a per-operation basis using application-layer signals beyond the spec.
- Substitute pluggable HTTP transport backends per §4.10 (added in `v1.1`), provided the externally-observable behavior across §4.1 — §4.9 is preserved.

### Cumulative conformance

The cumulative effect of the above is that a `Conforming Implementation` of `v1.x` reliably dispatches against any well-formed `.uacp` artifact, retries safely under transient failure without risking duplicated side effects, paginates without unbounded loops, normalizes failures into a uniform vocabulary regardless of `Provider`-specific error shapes, and exposes streaming responses through a small set of recognized transport patterns. The dispatch runtime is the layer where the `.uacp` artifact's declarative description meets the wire — this stage's job is to make that meeting deterministic.

## 4.10 Pluggable transport backends

*Added in `v1.1`.* UACP does not mandate any specific HTTP client library. Implementations MAY substitute different transport backends per `Connection`, per authentication method, or per operation, provided the externally-observable behavior of §4.1 — §4.9 is preserved.

### Why pluggable transports

The dispatch sections 4.1 — 4.9 describe a uniform contract — HTTPS-only, retry policy, pagination loops, rate-limit handling, error normalization, streaming patterns. They describe *what* the runtime exposes to the caller, not *how* the request is sent on the wire. A `Conforming Implementation` of `v1.0` is free to use any reasonable HTTP client to send the request; the spec does not name `httpx`, `requests`, `aiohttp`, `OkHttp`, or any other library, and it never has.

`v1.1` makes this freedom explicit. The motivation is anti-bot resilience for the §3.12 / §2.10 capture-plus-`session_cookie` pattern: providers like Google NotebookLM, Cloudflare-protected SaaS surfaces, and similar grey-zone targets serve different responses to default `httpx` (or any plain-Python) clients than they do to a real browser, on the strength of TLS fingerprinting (JA3/JA4), HTTP/2 frame ordering, header-order matching, and similar request-shape signals. Implementations integrating with such providers benefit from substituting a stealth-oriented backend (Scrapling, curl-impersonate, Playwright-driven browsers) for the affected `Connection`s, while keeping the default backend for the long tail of well-behaved REST APIs where stealth is unnecessary overhead.

§4.10 codifies that a backend swap is permitted *only when* the swap preserves the §4.1 — §4.9 contract. Anti-bot evasion is not an excuse for skipping retries, ignoring rate limits, dropping audit logs, or surfacing non-canonical errors.

### Conformance posture

A `Conforming Implementation` MAY substitute the default HTTP transport with an alternative backend, per `Connection` or per authentication method. When it does, it MUST satisfy the following constraints regardless of which backend is in use:

- **HTTPS-only transport.** Per §4.1 / §4.2. The substitute backend MUST reject `http://` URLs and MUST refuse to negotiate TLS 1.1 or below. Stealth backends that emulate a real browser MUST do so only over HTTPS; emulating a browser over plaintext is forbidden.
- **Retry policy.** Per §4.3. The substitute backend MUST honor the default retry policy (3 attempts, exponential backoff, ±25% jitter) for transient failures and `5xx` responses on idempotent operations. Per-operation `retry` overrides apply identically across backends.
- **Pagination loops.** Per §4.4. The pagination runtime is implemented above the transport layer; the substitute backend MUST NOT change the pagination contract or the per-call max-pages safety limit.
- **Rate-limit handling.** Per §4.5. The substitute backend MUST honor `Retry-After` on `429`, MUST consume `X-RateLimit-Remaining` / `X-RateLimit-Reset` advisory headers when present, and MUST participate in the cross-operation rate-budget pooling. Stealth backends in particular MUST NOT bypass `Provider`-side rate limits as a feature; backing off on `429` is a `Provider`-respect-norm that doesn't bend just because the request shape mimics a browser.
- **Canonical error shape.** Per §4.6. The substitute backend MUST surface failures in the canonical `{status, code, message, details, raw}` shape and MUST map HTTP status to canonical `code` per the §4.6 table. Backend-specific exception types MUST NOT leak past the dispatch boundary.
- **Streaming response contract.** Per §4.7. When the substitute backend supports streaming, it MUST expose the recognized patterns (chunked, SSE, NDJSON, optionally WebSocket) with the parser expectations of §4.7. A backend that cannot stream MUST surface the operation's streaming response as `upstream_error` rather than buffering silently.
- **Audit logging.** Per §6.6. Every dispatch MUST emit the `dispatch.start` / `dispatch.success` / `dispatch.failure` audit events the spec requires, with the per-event field set unchanged. The transport-backend identity MAY be included as an additional field in the audit event (recommended for forensics on stealth-backed connections), but the floor field set is unchanged.
- **Idempotency-key injection.** Per §4.8. The substitute backend MUST inject the configured idempotency-key header on `POST` / `PATCH` operations declared `idempotent` when the implementation supports the §4.8 MAY behavior; the backend MUST NOT silently drop the key.

The substitute backend MAY add capabilities the default backend lacks — TLS fingerprint matching, browser-equivalent header ordering, cookie persistence beyond a single dispatch, JS-evaluation for challenge pages — provided these additions are observably equivalent at the §4.1 — §4.9 interface to a request that succeeded directly. Anti-bot bypass is observably equivalent to "the request worked"; nothing in §4.10 prevents implementations from making that more likely.

### Selection mechanism

The transport selection is implementation-defined. Recommended patterns:

- **Default backend per `Connection`.** Most `Connection`s use the implementation's default backend (typically a vanilla HTTPS client). This is the existing `v1.0` posture and remains the silent default.
- **Auth-method affinity.** Implementations MAY couple specific authentication methods to specific backends. The canonical pairing: `session_cookie` connections per §2.10 default to a stealth backend (where available) on the rationale that captures and session replay typically target providers with browser-fingerprint defenses, while OAuth-shaped connections stay on the default backend.
- **Per-artifact override.** A `.uacp` artifact MAY declare `dispatch.transport` as an optional string field. The value names the backend to use for every operation in the artifact. Recommended values: `"default"` (the implementation's default backend), `"stealth"` (an anti-bot-evading backend, when the implementation provides one), or any implementation-specific identifier with an `x-` prefix per §7.3. Implementations that do not recognize the named transport MUST fall back to the default backend with a warning surfaced to the user, rather than refuse to dispatch.

Implementations MAY support transport selection at finer granularity (per-operation, per-call) but UACP does not require it. Per-`Connection` or per-artifact selection is sufficient for the v1.1 use cases.

### Substitution and observability

Implementations that support pluggable backends SHOULD make the active backend visible to the operator — through a CLI flag, a runtime API, an audit-event field, or equivalent — so debugging and forensics can distinguish a default-backend failure from a stealth-backend failure. The audit events at §6.6 are the recommended carrier; implementations MAY include `transport: "<backend-name>"` in the per-event detail map.

Per §3.11's round-trip rule, implementations MUST preserve the artifact's `dispatch.transport` field on round-trip even when the implementation does not recognize the named backend. The unknown-field forward-compatibility rule covers the case where a `v1.2` artifact names a backend introduced after the loading implementation was built.

### Out of scope for `v1.1`

§4.10 does not define a registry of backend names. The `"default"` and `"stealth"` strings are conventions; the spec does not enumerate the implementations that satisfy them. A future `v1.x` minor MAY register specific backend identifiers (e.g., `httpx`, `scrapling`, `curl-impersonate`) under §2.8's registration mechanism if implementations report fragmentation that warrants it. Until that lands, implementations document their supported backends in their own user-facing documentation; the conformance posture above is what UACP enforces at the spec layer.

§4.10 also does not specify how the substitute backend obtains its configuration (cookie jars, browser profiles, fingerprint definitions). Configuration is implementation-specific; the captured-session artifact per §3.12 is the closest UACP comes to specifying the inputs a stealth backend consumes, and §2.10's `storage_state_ref` is the dispatch-time credential surface.
