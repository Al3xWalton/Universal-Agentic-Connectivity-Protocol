# UACP Authentication

This document specifies the authentication subsystem of UACP `v1.x`. It defines the registry of authentication methods that a `.uacp` artifact MAY declare, the wire shape of each method's `authentication` object, the credential-reference convention that keeps secrets out of artifacts, and the extension mechanism by which new methods are registered without breaking existing artifacts. The conformance keywords ("MUST", "MUST NOT", "SHOULD", "SHOULD NOT", "MAY") in this document are interpreted per BCP 14 [[RFC2119](https://datatracker.ietf.org/doc/html/rfc2119)] [[RFC8174](https://datatracker.ietf.org/doc/html/rfc8174)] as established in [Stage 0 — Primer](./00-primer.md).

This document is consistent with the foundational principles in [Stage 1 — Principles](./01-principles.md). Where this document refines a principle, it does so by narrowing detail; it does not override.

## 2.0 Overview

A UACP `Connection` becomes `active` only after an `Authentication Method` has produced credential material that the dispatch runtime can present to the `Provider`. UACP describes that exchange in two parts: the **method registry** (Section 2.1) lists the stable identifiers a `.uacp` artifact selects from when declaring how a `Provider` authenticates; and the **method specifications** (Sections 2.2 through 2.6 plus 2.10) describe the wire shape of each registered method and the rules a `Conforming Implementation` MUST follow when executing it.

Every `.uacp` artifact describing authentication MUST be safe to publish publicly. Artifacts describe the *procedure* by which credentials are obtained — endpoints, identifiers, signature schemes, scope sets — but never embed the credentials themselves. Credentials are referenced exclusively through the `secret://` reference convention defined in Section 2.7. This separation is the structural commitment that makes Principle 7 (security by default) and Principle 10 (public artifacts, private secrets) load-bearing rather than aspirational.

The authentication subsystem is **pluggable**. The registered set in Section 2.1 is the authoritative `v1.x` baseline; new methods MAY be added in any later `v1.x` release through the registration mechanism in Section 2.8. New methods MUST be backward-compatible additions and MUST NOT alter the semantics of any previously registered method, satisfying Principle 5 (pluggable authentication) and Principle 6 (wire-format stability) jointly.

### In scope

The `v1.x` registered set comprises ten `Authentication Method`s, grouped as follows:

- **OAuth 2.0** — three grant types (authorization code with PKCE, client credentials, device authorization) plus refresh-token semantics, server-metadata discovery, and JWT access-token handling (Section 2.2).
- **OAuth 1.0a** — the three-legged flow with HMAC-SHA1 signing, retained for parity with services that have not migrated (Section 2.3).
- **API key** — header-based and query-parameter-based variants, plus a composite shape for providers requiring multiple keys (Section 2.4).
- **Signed-request schemes** — AWS Signature Version 4 and a generic HMAC-signature mechanism for providers that sign each request (Section 2.5).
- **Custom authentication** — an explicit escape hatch for providers whose authentication does not fit any registered method, with constraints documented in Section 2.6.
- **Session-cookie authentication** — browser-equivalent session replay for grey-zone provider integrations whose authentication has no public API surface, with mandatory ToS-violation-risk acknowledgment (Section 2.10).

Section 2.7 specifies the `secret://` credential-reference format that all of the above methods MUST use. Section 2.8 specifies the extension mechanism. Section 2.9 summarizes the conformance level of each method for a `Conforming Implementation` of `v1.x`.

### Out of scope

The following are deliberately deferred to later stages and MUST NOT be inferred from this document:

- **Token storage encryption, secret-store implementations, scope enforcement at dispatch time, audit logging, and the threat model.** These are Stage 6 (security model). Section 2.7 defines the *reference format* that points at a secret store; the secret store's own properties are Stage 6's responsibility.
- **Token lifecycle.** The full lifecycle of a credential — proactive refresh windows, refresh-failure backoff, idle-revocation policy, the `pending`/`active`/`revoked` state machine, observability hooks — is Stage 5 (lifecycle). Section 2.2.4 specifies the *wire* of a single refresh exchange; the *scheduling* of refresh exchanges is not specified here.
- **Retry policy under transport failure.** Recovery from network errors, 5xx responses from authorization servers, and DNS failures is split between Stage 4 (dispatch) and Stage 5 (lifecycle); this document specifies the protocol exchange, not the retry envelope around it.
- **Authoring-time UX.** How an AI agent or human author *produces* an authentication block in a `.uacp` artifact is an implementation concern outside the protocol surface, per Principle 3 (AI-native authoring) and the Stage 0 Primer's §Authoring definition.

Where a section in this document approaches one of those boundaries, the boundary is named explicitly.

## 2.1 Authentication method registry

Every `.uacp` artifact that requires authentication declares a single top-level `authentication` object. That object has a `method` field whose value is a stable string identifier drawn from the registry below. The remaining fields of the `authentication` object are method-specific and are specified in the section listed in the **Specified in** column.

The `v1.0` registry contains ten `Authentication Method`s:

| Identifier | Family | Specified in |
|---|---|---|
| `oauth2_authorization_code` | OAuth 2.0 | §2.2.1 |
| `oauth2_client_credentials` | OAuth 2.0 | §2.2.2 |
| `oauth2_device_code` | OAuth 2.0 | §2.2.3 |
| `oauth1a` | OAuth 1.0a | §2.3 |
| `api_key_header` | API key | §2.4.1 |
| `api_key_query` | API key | §2.4.2 |
| `aws_sigv4` | Signed request | §2.5.1 |
| `hmac_signature` | Signed request | §2.5.2 |
| `session_cookie` | Browser-equivalent | §2.10 |
| `custom_auth` | Escape hatch | §2.6 |

The `session_cookie` method (§2.10) is intended for undocumented or restricted-API providers reachable only through browser-equivalent session replay. It carries a higher operational risk than the other registered methods (replaying browser-captured cookies against an undocumented API may violate the provider's Terms of Service); §2.10 specifies the conformance and warning requirements that follow from that risk.

Identifiers are lowercase ASCII, use underscores rather than hyphens, and contain no dots. They are the exact strings that appear in the `method` field; they are not abbreviations or aliases.

A `Conforming Implementation` of `v1.x` MUST recognize every registered identifier and MUST apply the wire-shape rules in the corresponding section. Whether the implementation supports *executing* a given method is a separate question governed by Section 2.9; an implementation that does not support a method MUST decline to dispatch artifacts that select it (per Section 2.8) but MUST still parse and validate the artifact's `authentication` object against the method's wire shape.

`v1.x` MAY register new identifiers in later releases. `v1.x` MUST NOT change the semantics of any identifier already registered: a `.uacp` artifact valid against `v1.0`'s `oauth2_authorization_code` shape MUST remain valid and MUST behave identically against every later `v1.x`. Removing a registered identifier or repurposing an identifier for a different method REQUIRES a `v2` major-version bump per Principle 6.

The `method` field is required. An `authentication` object that omits `method`, or that supplies a value not in the registry and not registered through the extension mechanism (§2.8), MUST be rejected at validation time with a `bad_input` failure code.

## 2.2 OAuth 2.0

OAuth 2.0 [[RFC6749](https://datatracker.ietf.org/doc/html/rfc6749)] is the most common `Authentication Method` family across the long-tail HTTPS-fronted services UACP targets. UACP embeds OAuth 2.0 unmodified: the authorization server, token endpoints, and credential exchange are governed by the OAuth specifications, and a `Conforming Implementation` MUST conform to those specifications. UACP adds the `.uacp` artifact shape that describes how a `Provider` participates in a given OAuth grant and the credential-reference convention that keeps client secrets and access tokens out of the artifact.

Three grant types are registered in `v1.0`: authorization code (§2.2.1), client credentials (§2.2.2), and device authorization (§2.2.3). Refresh-token behavior (§2.2.4), server-metadata discovery (§2.2.5), and JWT access-token handling (§2.2.6) apply across the grant types that use them.

### 2.2.1 Authorization code grant

The authorization code grant per RFC 6749 §4.1 is the registered method for any user-facing OAuth flow. PKCE [[RFC7636](https://datatracker.ietf.org/doc/html/rfc7636)] adds protection against authorization-code interception and is mandatory for native-application clients per RFC 8252 [[RFC8252](https://datatracker.ietf.org/doc/html/rfc8252)] §8.1.

UACP's position is more conservative than the OAuth specifications taken in isolation:

- A `Conforming Implementation` MUST support PKCE for `oauth2_authorization_code`.
- PKCE MUST be used for any OAuth 2.0 authorization-code flow originating from a non-confidential client. A `.uacp` artifact MAY omit PKCE only when the implementation that will execute the artifact is a confidential client capable of protecting `client_secret` per RFC 6749 §2.3.1; in every other case, PKCE MUST be present.
- `code_challenge_method` defaults to `S256` and SHOULD be `S256`; `plain` is permitted only for compatibility with authorization servers that do not implement `S256`, and a `.uacp` artifact MUST NOT specify `plain` unless the `Provider`'s authorization server is documented to reject `S256`.

The `authentication` object for the authorization code grant has the following shape:

```json
{
  "method": "oauth2_authorization_code",
  "authorization_endpoint": "https://example.com/oauth2/authorize",
  "token_endpoint": "https://example.com/oauth2/token",
  "client_id": "app-1234",
  "client_secret_ref": "secret://vault/example/client_secret",
  "scopes": ["read:user", "send:message"],
  "redirect_uri": "https://broker.example/oauth/callback/example",
  "code_challenge_method": "S256"
}
```

Field requirements:

- `authorization_endpoint` (required, string) — the absolute HTTPS URL of the authorization server's authorization endpoint per RFC 6749 §3.1.
- `token_endpoint` (required, string) — the absolute HTTPS URL of the token endpoint per RFC 6749 §3.2.
- `client_id` (required, string) — the OAuth client identifier issued by the authorization server. `client_id` is not a credential per OAuth's threat model and MAY appear in the artifact verbatim.
- `client_secret_ref` (conditional, string) — a credential reference per Section 2.7 resolving to the OAuth client secret. REQUIRED when the authorization server requires client authentication at the token endpoint (RFC 6749 §2.3); MUST be omitted for public clients that authenticate solely through PKCE. A `.uacp` artifact MUST NOT contain a literal `client_secret` field.
- `scopes` (required, array of strings) — the OAuth scope set requested at the authorization endpoint. The array MAY be empty when the `Provider` defines a default scope. Per-scope semantics are defined by the `Provider`; UACP imposes no restriction on scope syntax beyond OAuth 2.0's own.
- `redirect_uri` (required, string) — the absolute HTTPS URL of the implementation's authorization callback, registered with the authorization server out-of-band. UACP requires HTTPS per Principle 11; the `http://localhost` exception in RFC 8252 §7.3 is a property of the local-development implementation, not of the published artifact, and MUST NOT appear in an artifact intended for shared distribution.
- `code_challenge_method` (optional, string, default `S256`) — the PKCE challenge method per RFC 7636 §4.3. Permitted values are `S256` and `plain`; `plain` is constrained as above. When omitted, implementations MUST use `S256`.

Implementations MAY support additional optional fields (`audience`, `resource`, `prompt`, `login_hint`) that pass through to the authorization request. Such fields are pass-through only and MUST NOT alter the OAuth contract.

The authorization-code exchange itself follows RFC 6749 §4.1.1 through §4.1.4 with the PKCE additions in RFC 7636. Construction of `code_verifier` and `code_challenge`, generation of the `state` parameter, and validation of `state` on callback are properties of the implementation, not of the artifact.

### 2.2.2 Client credentials grant

The client credentials grant per RFC 6749 §4.4 is the registered method for service-to-service authentication that does not involve a user. The grant exchanges `client_id` and `client_secret` directly for an access token at the token endpoint.

The `authentication` object for the client credentials grant has the following shape:

```json
{
  "method": "oauth2_client_credentials",
  "token_endpoint": "https://example.com/oauth2/token",
  "client_id": "svc-9876",
  "client_secret_ref": "secret://aws-secrets-manager/example/svc_client_secret",
  "scope": "ingest:write"
}
```

Field requirements:

- `token_endpoint` (required, string) — the absolute HTTPS URL of the token endpoint.
- `client_id` (required, string) — the OAuth client identifier.
- `client_secret_ref` (required, string) — a credential reference per Section 2.7 resolving to the client secret. Required because the client credentials grant has no other authenticator. A `.uacp` artifact MUST NOT contain a literal `client_secret` field.
- `scope` (optional, string) — a single space-delimited scope string per RFC 6749 §3.3, conveyed in the `scope` request parameter. When omitted, the authorization server's default scope applies.

The token-endpoint exchange follows RFC 6749 §4.4 unchanged. Client credentials grants do not issue refresh tokens (RFC 6749 §4.4.3); §2.2.4 does not apply.

### 2.2.3 Device authorization grant

The device authorization grant per RFC 8628 [[RFC8628](https://datatracker.ietf.org/doc/html/rfc8628)] is the registered method for input-constrained devices — typically a CLI, a TV-style application, or any client that cannot run an embedded browser. The grant decouples authorization (performed on a second device with a browser) from token exchange (performed on the input-constrained device by polling the token endpoint).

The `authentication` object for the device authorization grant has the following shape:

```json
{
  "method": "oauth2_device_code",
  "device_authorization_endpoint": "https://example.com/oauth2/device_authorization",
  "token_endpoint": "https://example.com/oauth2/token",
  "client_id": "cli-5678",
  "scope": "read:user write:user"
}
```

Field requirements:

- `device_authorization_endpoint` (required, string) — the absolute HTTPS URL of the device authorization endpoint per RFC 8628 §3.1.
- `token_endpoint` (required, string) — the absolute HTTPS URL of the token endpoint per RFC 8628 §3.4.
- `client_id` (required, string) — the OAuth client identifier.
- `scope` (optional, string) — a single space-delimited scope string per RFC 6749 §3.3.

The device authorization grant defines polling-interval semantics through the `interval` field in the device-authorization response (RFC 8628 §3.2). The `.uacp` artifact does not encode the polling interval, because that interval is set by the authorization server at runtime, per request. A `Conforming Implementation` MUST honor the `interval` returned in the device-authorization response and MUST honor the `slow_down` token-error response per RFC 8628 §3.5 by extending the polling interval. Behavior on `expired_token`, `access_denied`, and other terminal token-error responses is dispatch-runtime behavior; see Stage 5 for the lifecycle treatment.

The device authorization grant MAY issue refresh tokens; when it does, refresh-token behavior follows §2.2.4.

### 2.2.4 Refresh tokens

Refresh tokens per RFC 6749 §6 apply to grants that issue them — in `v1.0`, the authorization-code grant (§2.2.1) and the device authorization grant (§2.2.3). The refresh exchange is initiated against the same `token_endpoint` declared in the artifact, with `grant_type=refresh_token` and the prior refresh token in the `refresh_token` parameter, per RFC 6749 §6.

UACP treats refresh as a single transition: from "the access token currently held is expired or about to expire" to "a fresh access token is held." The wire shape of the refresh request and response follows RFC 6749 §6 unchanged. Two behaviors are normative:

- **Token rotation.** When the authorization server returns a new refresh token in the refresh response, the new refresh token MUST replace the prior one in the secret store; the prior refresh token MUST NOT be retained. Authorization servers that rotate refresh tokens (a common practice; required by some) rely on this replacement to invalidate stolen tokens.
- **Atomicity.** A refresh exchange either succeeds — yielding a fresh access token and (if rotated) a fresh refresh token — or fails. There is no partial state. A refresh that fails for any reason leaves the prior credential material in whatever state it was in before the exchange started; the implementation does not silently retain a stale access token alongside a now-invalid refresh token.

Deeper aspects of refresh behavior — proactive refresh windows (refreshing before expiration to mask latency), retry under transport failure, the relationship between refresh failure and `Connection` lifecycle state transitions, observability of refresh outcomes — are Stage 5 (lifecycle) concerns and are not specified here.

### 2.2.5 Server metadata discovery

OAuth 2.0 Authorization Server Metadata [[RFC8414](https://datatracker.ietf.org/doc/html/rfc8414)] defines a `.well-known/oauth-authorization-server` document that publishes the authorization server's endpoints and capabilities. UACP supports server-metadata discovery as an alternative to enumerating endpoints in the artifact.

A `.uacp` artifact MAY include either explicit endpoints (`authorization_endpoint`, `token_endpoint`, `device_authorization_endpoint` as applicable) or a `server_metadata_url` field whose value is the absolute HTTPS URL of the authorization server's metadata document, or both. The combination rule is:

- **If both are present**, explicit endpoints win for the fields they specify. The `server_metadata_url` MAY be retained for fields the artifact does not pin (for example, `revocation_endpoint`), but the explicit fields override.
- **If only `server_metadata_url` is present**, the implementation MUST fetch the metadata document and read the endpoints from it before initiating the OAuth flow. The fetch MUST occur over HTTPS; the metadata document is not a credential and need not be cached, but the implementation MAY cache it for performance per RFC 8414 §3.
- **If only explicit endpoints are present** (the most common shape in artifacts authored against a specific provider), the implementation uses them directly.

Example with `server_metadata_url`:

```json
{
  "method": "oauth2_authorization_code",
  "server_metadata_url": "https://example.com/.well-known/oauth-authorization-server",
  "client_id": "app-1234",
  "client_secret_ref": "secret://vault/example/client_secret",
  "scopes": ["read:user"],
  "redirect_uri": "https://broker.example/oauth/callback/example",
  "code_challenge_method": "S256"
}
```

A `Conforming Implementation` MUST validate that the metadata document conforms to RFC 8414's schema before relying on it. If the metadata document is unreachable or malformed, the artifact MUST fail validation with `bad_input`.

### 2.2.6 JWT access tokens

OAuth 2.0 access tokens are opaque to the OAuth specifications; their format is a property of the authorization server. JWT-formatted access tokens [[RFC9068](https://datatracker.ietf.org/doc/html/rfc9068)] are a common case worth treating explicitly because the JWT structure exposes useful metadata to the dispatch runtime.

UACP does not mandate that access tokens be JWTs. A `.uacp` artifact does not declare token format; the format is determined by what the authorization server returns. The following rules apply when the returned access token is a JWT:

- The dispatch runtime SHOULD treat the JWT as opaque for transport: the access token is presented to the `Provider` as a bearer credential per RFC 6750, exactly as if it were any other opaque string. JWT structure MUST NOT influence the wire shape of the dispatch request unless the `Provider` documents otherwise.
- The dispatch runtime MAY introspect the JWT for the `exp` claim per RFC 9068 §2.2 to inform refresh timing. When introspection is performed, the runtime MUST validate the JWT only as far as parsing the payload and reading `exp`; full signature verification is a property of the resource server (the `Provider`), not of UACP, and MUST NOT be inferred from the act of reading `exp`.
- Other JWT claims (`iss`, `aud`, `sub`, `scope`) MAY be observed for telemetry or diagnostics but MUST NOT be used to authorize dispatch decisions inside UACP. Authorization decisions are the resource server's responsibility.

The exact treatment of `exp` in refresh-timing decisions is a Stage 5 (lifecycle) concern; this section establishes only that introspection of `exp` is a permitted optimization, not a mandated step.

## 2.3 OAuth 1.0a

OAuth 1.0a [[RFC5849](https://datatracker.ietf.org/doc/html/rfc5849)] is a legacy `Authentication Method`, retained in the `v1.0` registry for parity with services that have not migrated to OAuth 2.0. Notable holdouts include the Twitter API v1.1 and a small number of financial-sector APIs whose specifications continue to require OAuth 1.0a request signing. New `Provider` integrations SHOULD prefer OAuth 2.0 when both are offered; OAuth 1.0a is supported because it is unavoidable, not because it is recommended.

The OAuth 1.0a flow is three-legged:

1. The client requests a temporary credential ("request token") from the request-token endpoint.
2. The user authorizes the request token at the authorization endpoint.
3. The client exchanges the authorized request token for an access token (a "token credential") at the access-token endpoint.

Each request to a `Provider` is then signed using the access token as part of the OAuth 1.0a signature.

The `authentication` object for OAuth 1.0a has the following shape:

```json
{
  "method": "oauth1a",
  "request_token_url": "https://api.example.com/oauth/request_token",
  "authorize_url": "https://api.example.com/oauth/authorize",
  "access_token_url": "https://api.example.com/oauth/access_token",
  "consumer_key": "ck-1234",
  "consumer_secret_ref": "secret://vault/example/consumer_secret",
  "signature_method": "HMAC-SHA1",
  "realm": "Example API"
}
```

Field requirements:

- `request_token_url` (required, string) — the absolute HTTPS URL of the temporary-credential endpoint per RFC 5849 §2.1.
- `authorize_url` (required, string) — the absolute HTTPS URL of the resource-owner authorization endpoint per RFC 5849 §2.2.
- `access_token_url` (required, string) — the absolute HTTPS URL of the token-credential endpoint per RFC 5849 §2.3.
- `consumer_key` (required, string) — the OAuth 1.0a client identifier (the "consumer key"). The consumer key is not a credential under OAuth 1.0a's threat model and MAY appear in the artifact verbatim.
- `consumer_secret_ref` (required, string) — a credential reference per Section 2.7 resolving to the consumer secret. A `.uacp` artifact MUST NOT contain a literal `consumer_secret` field.
- `signature_method` (required, string) — the OAuth 1.0a signature method per RFC 5849 §3.4. Permitted values are `HMAC-SHA1` and `RSA-SHA1`. `PLAINTEXT` is explicitly excluded; see below.
- `realm` (optional, string) — the protection realm to include in the `Authorization` header per RFC 5849 §3.5.1. Most providers do not require this field.

Signature-method requirements:

- `HMAC-SHA1` — A `Conforming Implementation` MUST support `HMAC-SHA1`. It is the most widely deployed OAuth 1.0a signature method and the default for the holdout services.
- `RSA-SHA1` — A `Conforming Implementation` SHOULD support `RSA-SHA1`. When `RSA-SHA1` is selected, the artifact's `consumer_secret_ref` resolves to the RSA private key (in PEM or equivalent encoding determined by the secret store), not to a shared secret.
- `PLAINTEXT` — A `Conforming Implementation` MUST NOT support `PLAINTEXT`. A `.uacp` artifact whose `signature_method` is `PLAINTEXT` MUST be rejected at validation time with `bad_input`. The `PLAINTEXT` method transmits the consumer secret as part of every signed request and is incompatible with UACP's secret-handling posture.

The signing procedure itself follows RFC 5849 §3 unchanged: signature base string construction (§3.4.1), HMAC-SHA1 or RSA-SHA1 computation (§3.4.2 and §3.4.3 respectively), and encoding into the `Authorization` header per §3.5.1. UACP does not modify the OAuth 1.0a signing algorithm.

## 2.4 API key authentication

API key authentication is the simplest registered method family. The client presents a static, pre-issued credential on every request; there is no authorization-server exchange and no concept of refresh. API keys are common across the long-tail HTTPS services UACP targets and are typically the lowest-friction integration shape.

UACP registers three variants in `v1.0`: header-based (§2.4.1), query-parameter-based (§2.4.2), and a composite shape that combines API-key methods with signature methods for providers requiring both (§2.4.3).

### 2.4.1 Header-based (`api_key_header`)

The most common API-key variant places the key in an HTTP request header, typically `Authorization` (with a `Bearer` or vendor-specific prefix) or a vendor-specific header such as `X-API-Key`.

The `authentication` object for header-based API keys has the following shape:

```json
{
  "method": "api_key_header",
  "header_name": "X-API-Key",
  "header_prefix": "",
  "key_ref": "secret://vault/example/api_key"
}
```

Or, with a `Bearer` prefix:

```json
{
  "method": "api_key_header",
  "header_name": "Authorization",
  "header_prefix": "Bearer ",
  "key_ref": "secret://vault/example/api_key"
}
```

Field requirements:

- `header_name` (required, string) — the HTTP header name into which the key is placed. The name follows HTTP header-name syntax per RFC 9110 §5.1; UACP imposes no further restriction.
- `header_prefix` (optional, string, default empty) — a literal prefix prepended to the resolved key value before the combined string is set as the header value. Common values are `Bearer ` (note the trailing space, which is part of the prefix), `Token `, and the empty string. When omitted, no prefix is applied.
- `key_ref` (required, string) — a credential reference per Section 2.7 resolving to the API key. A `.uacp` artifact MUST NOT contain a literal `key` field.

The dispatch runtime resolves `key_ref`, prepends `header_prefix`, and sets the resulting string as the value of the header named by `header_name` on every dispatch request. The header is added to every request issued through this `Connection`; per-action overrides are not part of the authentication subsystem.

### 2.4.2 Query-parameter-based (`api_key_query`)

The query-parameter variant places the key in the request URL's query string. This shape exists because some `Provider`s require it, but UACP treats it as **disrecommended**: query-parameter API keys leak into server access logs, browser history, web-server analytics pipelines, and proxy logs that the operator does not control. A `.uacp` artifact SHOULD use `api_key_header` (§2.4.1) when the `Provider` accepts both shapes.

The `authentication` object for query-parameter-based API keys has the following shape:

```json
{
  "method": "api_key_query",
  "param_name": "api_key",
  "key_ref": "secret://local-keyring/example/api_key"
}
```

Field requirements:

- `param_name` (required, string) — the query-parameter name into which the key is placed. The name follows query-string syntax per RFC 3986 §3.4.
- `key_ref` (required, string) — a credential reference per Section 2.7 resolving to the API key.

The dispatch runtime resolves `key_ref` and adds the parameter named by `param_name` with the resolved value to the query string of every dispatch request. If the action's request URL already contains a parameter of the same name, the authentication parameter overrides; the action MUST NOT carry a parameter that collides with the authentication parameter.

A `Conforming Implementation` MAY emit a runtime warning when `api_key_query` is used and the `Provider`'s documentation indicates that `api_key_header` is also supported. The warning is informational; it does not block dispatch.

### 2.4.3 Composite

Some `Provider`s require multiple credentials presented in different positions on the same request — for example, a static API key in a header *and* a per-request signature derived from a separate signing secret. The composite shape composes a method from §2.4.1 or §2.4.2 with a signature method from §2.5 inside a single `authentication` block.

A composite `authentication` object uses one of the API-key methods at the top level and adds a `signature` sub-object whose contents follow the signed-request method's wire shape (with `method` repeated inside the sub-object for explicitness):

```json
{
  "method": "api_key_header",
  "header_name": "X-Public-Key",
  "header_prefix": "",
  "key_ref": "secret://vault/example/public_key",
  "signature": {
    "method": "hmac_signature",
    "algorithm": "HMAC-SHA256",
    "key_ref": "secret://vault/example/signing_secret",
    "signed_payload_template": "${method}\n${path}\n${timestamp}\n${body}",
    "header_name": "X-Signature"
  }
}
```

Composite shape requirements:

- The top-level `method` MUST be one of `api_key_header` or `api_key_query`. Composite shapes nesting OAuth methods or other signed-request methods are not registered in `v1.0`.
- The `signature` sub-object's `method` MUST be one of `hmac_signature` or `aws_sigv4`. The sub-object MUST contain every field required by the chosen signed-request method's wire shape, with the same semantics as in §2.5.
- The dispatch runtime applies both methods on every request: the API-key method places its credential per §2.4.1 or §2.4.2, and the signature method computes and places the signature per §2.5. The order in which the two operations modify the request is implementation-defined when they do not collide; when they would collide (for example, both methods targeting `Authorization`), the artifact MUST be rejected at validation time.

A `Conforming Implementation` MAY decline to support the composite shape; if so, it MUST do so per §2.8 (decline silently rather than substituting). Composite shapes are common enough among long-tail providers (notably some webhook-receiving APIs and several financial-data providers) that support is encouraged.

## 2.5 Signed-request schemes

Some `Provider`s require the dispatch runtime to compute a per-request signature and include it on every request, rather than presenting a static credential. Signed-request schemes provide replay protection and message-integrity guarantees that static API keys cannot.

UACP registers two signed-request methods in `v1.0`: AWS Signature Version 4 (§2.5.1), which covers AWS services and the small number of non-AWS providers that adopted the same scheme; and a generic HMAC-signature method (§2.5.2), which covers Stripe, Shopify webhooks, and the long tail of providers that defined custom HMAC schemes.

### 2.5.1 AWS Signature Version 4 (`aws_sigv4`)

AWS Signature Version 4 (SigV4) is the request-signing scheme used by AWS services and reused by a small number of S3-compatible and AWS-API-compatible providers. SigV4 derives a signing key from a secret access key, the request date, the AWS region, and the service identifier, then signs a canonical representation of the request. The full algorithm is specified by AWS in the *Signing AWS API Requests* documentation; a `Conforming Implementation` MUST implement that algorithm exactly.

The `authentication` object for `aws_sigv4` has the following shape:

```json
{
  "method": "aws_sigv4",
  "access_key_ref": "secret://aws-secrets-manager/example/access_key_id",
  "secret_key_ref": "secret://aws-secrets-manager/example/secret_access_key",
  "service": "s3",
  "region": "us-east-1",
  "session_token_ref": "secret://aws-secrets-manager/example/session_token"
}
```

Field requirements:

- `access_key_ref` (required, string) — a credential reference resolving to the AWS access key ID. The access key ID is identifier-shaped and not a credential in the strictest sense, but UACP keeps it in the secret store alongside the secret access key for operational consistency.
- `secret_key_ref` (required, string) — a credential reference resolving to the AWS secret access key. A `.uacp` artifact MUST NOT contain a literal secret access key.
- `service` (required, string) — the AWS service identifier per the SigV4 specification (for example, `s3`, `dynamodb`, `lambda`, `execute-api`).
- `region` (required, string) — the AWS region in SigV4's canonical form (for example, `us-east-1`, `eu-west-2`).
- `session_token_ref` (optional, string) — a credential reference resolving to a session token, present when the AWS credentials are temporary credentials issued by AWS STS. When present, the session token is added to the `X-Amz-Security-Token` header per the SigV4 specification.

The canonical-request construction, string-to-sign construction, signing-key derivation (HMAC chain over `AWS4` || secret access key, date, region, service, and the literal `aws4_request`), and final HMAC-SHA256 computation are specified by AWS and not duplicated here. A `Conforming Implementation` MUST follow AWS's published specification exactly; deviations are non-conforming. Implementations SHOULD prefer to delegate SigV4 computation to a vetted library rather than implementing it from scratch.

UACP registers SigV4 because it is the dominant scheme for AWS-shaped services and because it is non-obvious to implement correctly. Providers that use a SigV4-shaped scheme but with a different `Algorithm` token (some AWS-compatible cloud providers diverge here) MAY use `hmac_signature` (§2.5.2) when the divergence cannot be expressed as a parameter of `aws_sigv4`.

### 2.5.2 HMAC-signature schemes (`hmac_signature`)

The `hmac_signature` method covers the long tail of providers that defined custom HMAC schemes — the canonical examples are Stripe webhooks (HMAC-SHA256 over a timestamp-prefixed payload), Shopify webhooks (HMAC-SHA256 over the request body), GitHub webhooks (HMAC-SHA256 over the request body, with `sha256=` prefix), and a substantial number of financial-services and developer-tools APIs whose authentication is "compute an HMAC and put it here."

The diversity of these schemes is in *what gets signed* and *where the signature goes*. UACP captures both with a small templating language and an explicit header destination.

The `authentication` object for `hmac_signature` has the following shape:

```json
{
  "method": "hmac_signature",
  "algorithm": "HMAC-SHA256",
  "key_ref": "secret://vault/example/signing_secret",
  "signed_payload_template": "${timestamp}.${body}",
  "header_name": "Stripe-Signature"
}
```

Field requirements:

- `algorithm` (required, string) — the HMAC algorithm. Permitted values in `v1.0` are `HMAC-SHA256` and `HMAC-SHA512`. `HMAC-SHA1` is not registered for `hmac_signature`; providers that require HMAC-SHA1 are sufficiently legacy that they are most likely OAuth 1.0a (§2.3) anyway. Implementations MUST reject any other value at validation time with `bad_input`.
- `key_ref` (required, string) — a credential reference resolving to the signing secret. A `.uacp` artifact MUST NOT contain a literal signing secret.
- `signed_payload_template` (required, string) — a templated string describing what gets signed. Substitutions are bracketed in `${}`. The legal substitutions in `v1.0` are:
  - `${timestamp}` — the current Unix timestamp in seconds, as a decimal string. The implementation generates this at signing time. Providers that require milliseconds or ISO 8601 timestamps use this substitution and post-process — but pre-formatted timestamp variants are not registered in `v1.0`; if the `Provider` requires a non-Unix-seconds format, the artifact MUST use `custom_auth` (§2.6).
  - `${method}` — the HTTP method of the request (uppercase, e.g. `GET`, `POST`).
  - `${path}` — the path component of the request URL, including any leading slash but excluding scheme, host, port, and query string.
  - `${query}` — the query-string component of the request URL, without the leading `?`. Empty string when there is no query.
  - `${headers.<name>}` — the value of the request header named `<name>`. Header-name matching is case-insensitive per RFC 9110. If the named header is absent, the substitution resolves to the empty string.
  - `${body}` — the request body as a single byte string. Binary bodies are signed as bytes; UACP imposes no encoding transformation.
- `header_name` (required, string) — the HTTP header name into which the computed signature is placed. The signature is encoded as a lowercase hex string of the raw HMAC output (the most common convention). Providers that require a different encoding (base64, prefixed hex such as `sha256=...`) are out of `v1.0`'s scope and MUST use `custom_auth`.

The substitution language is intentionally minimal. The set above is the complete list of legal substitutions in `v1.0`; an artifact that uses any other `${...}` expression MUST be rejected at validation time. Future `v1.x` releases MAY register additional substitutions through §2.8, with the same backward-compatibility constraint that applies to method registration.

A `signed_payload_template` is a literal string with substitutions; characters outside `${}` (including newlines, dots, colons, and any other delimiters the `Provider` requires between fields) are passed through verbatim.

## 2.6 Custom authentication escape hatch (`custom_auth`)

`custom_auth` is the registered escape hatch for `Provider`s whose authentication does not fit any other registered method. The realistic motivating cases include:

- Some banking and financial APIs that use mutual TLS combined with a per-request signing scheme not expressible as `hmac_signature`.
- Some legacy enterprise systems that use proprietary single-sign-on schemes predating OAuth.
- Certificate-based authentication schemes that bind a long-lived client certificate to a `Connection`.
- Authentication schemes whose timestamp encoding, signature placement, or canonicalization deviates from §2.5.2's substitution set in ways not expressible in the substitution language.

The `authentication` object for `custom_auth` has the following shape:

```json
{
  "method": "custom_auth",
  "description": "Mutual TLS with client certificate plus a SHA-512 digest of the request body in the X-Body-Digest header. The Provider rejects requests over plain TLS regardless of credential presentation.",
  "parameters": {
    "client_certificate_ref": "secret://vault/example/client_cert_pem",
    "client_key_ref": "secret://vault/example/client_key_pem",
    "digest_header_name": "X-Body-Digest",
    "digest_algorithm": "SHA-512"
  }
}
```

Field requirements:

- `description` (required, string) — a free-text human-readable description of the authentication procedure. The description is the primary surface that a security reviewer (human or AI) consults to decide whether the implementation can execute the artifact safely. Authoring tools SHOULD write descriptions that name the procedure unambiguously: the `Provider`'s documentation reference, the cryptographic primitives involved, the placement of credential material in the request, and any non-obvious validation steps.
- `parameters` (required, object) — a string-keyed object whose values are either credential references per Section 2.7 (for any value that is a secret) or literal strings (for non-secret configuration). A `.uacp` artifact MUST NOT contain literal secret values inside `parameters`; every secret-shaped value MUST be a `secret://` reference. The keys of `parameters` are method-specific and not registered with UACP.

`custom_auth` is the **escape hatch of last resort.** Two constraints follow:

- A `Conforming Implementation` MAY decline to support `.uacp` artifacts that select `custom_auth` if it cannot verify the auth flow's safety from the `description` and `parameters` alone. Declining MUST follow §2.8: the implementation refuses to dispatch the artifact and surfaces the refusal explicitly; it MUST NOT silently substitute a different method or attempt to guess the procedure.
- New `Authentication Method`s that prove common — that is, more than one `Provider` is shipped using the same `custom_auth` shape — SHOULD be promoted to a registered method in §2.1 in a future `v1.x` release through §2.8. The `custom_auth` shape is appropriate for one-off and rare cases; it is not a substitute for registering a method that recurs.

`custom_auth` does not itself define a wire procedure; the procedure is whatever the `description` describes. This is the source of `custom_auth`'s power and its risk. UACP accepts the risk because the alternative — refusing to authenticate against `Provider`s that do not fit the registered methods — would violate Principle 2 (universal-by-design).

## 2.7 Credential references

Every `.uacp` artifact in this stage's specifications places credentials in a separate secret store and references them through an opaque identifier. This section defines the reference format that all `Authentication Method`s use.

A **credential reference** is a string of the form:

```
secret://<store>/<id>
```

where:

- The literal scheme is `secret://`.
- `<store>` is a non-empty token identifying the secret-store implementation that resolves the reference. Tokens are lowercase ASCII, MAY contain hyphens, and MUST NOT contain slashes or whitespace.
- `<id>` is a store-specific identifier. The identifier MAY contain slashes (to express a path-like structure when the store is path-shaped), MAY contain alphanumerics, hyphens, underscores, dots, and the slash separator, and MUST NOT contain whitespace or non-printable characters.

The `<store>` tokens registered in `v1.0` are:

| Token | Resolver |
|---|---|
| `vault` | A HashiCorp Vault deployment. The `<id>` is interpreted as a Vault path. |
| `aws-secrets-manager` | AWS Secrets Manager. The `<id>` is a Secrets Manager secret name or ARN suffix. |
| `local-keyring` | The host operating system's keyring (macOS Keychain, Windows Credential Manager, Secret Service on Linux). The `<id>` is the keyring item identifier. |
| `inline-encrypted` | An encrypted blob carried alongside the artifact (out-of-artifact, out-of-band) and decrypted at dispatch time. The `<id>` identifies which blob within the implementation's inline-encrypted set. |

A `Conforming Implementation` MUST recognize the `secret://` scheme and MUST attempt resolution against the registered store named by `<store>`. The resolver implementation per store, the encryption properties of stored secrets, the access-control model around the store, the audit posture of resolution events, and the rotation lifecycle are **Stage 6** (security model) concerns and are not specified here. Section 2.7 defines the *reference format only*.

Implementations MAY register additional `<store>` tokens for proprietary or cloud-specific stores (for example, `gcp-secret-manager`, `azure-key-vault`); the registration follows §2.8. New tokens MUST NOT shadow registered tokens.

**Two normative rules** govern artifact authoring:

- A `.uacp` artifact MUST NOT embed plaintext secrets in any field, under any name, anywhere in the artifact. Every field whose semantic role is "credential" MUST be a `secret://` reference, named with the suffix `_ref` (for example, `client_secret_ref`, `key_ref`, `consumer_secret_ref`, `access_key_ref`, `secret_key_ref`, `session_token_ref`). The `_ref` suffix is the audit hook: a published artifact can be scanned for unsuffixed secret-looking field names and rejected.
- A `Conforming Implementation` MUST reject, at validation time, any artifact in which a `_ref`-suffixed field's value is not a syntactically valid `secret://` URL. The validation checks the scheme, the presence and shape of `<store>`, and the presence of `<id>`. Resolution itself is a dispatch-time operation; validation is a wire-shape check.

The `secret://` scheme is local to UACP. It is not an IANA-registered URI scheme and is not intended to be resolved by general-purpose URL libraries; the scheme is recognized exclusively by UACP-aware code.

## 2.8 Auth method extension

UACP's authentication subsystem is pluggable per Principle 5. New `Authentication Method`s — and new `<store>` tokens for §2.7's credential-reference scheme — are added to the registry without breaking artifacts that were valid against earlier `v1.x` releases. This section specifies how.

### Backward-compatibility constraint

The registry in §2.1 (and the `<store>` token list in §2.7) is the authoritative `v1.x` baseline. A new `v1.x` release MAY register additional method identifiers and additional `<store>` tokens. Three rules constrain what a registration MAY do:

- **Additive only.** A new release MUST NOT remove or rename a previously registered identifier. A `.uacp` artifact valid against `v1.0` MUST remain valid and MUST behave identically against every later `v1.x`. Removing or renaming an identifier REQUIRES a `v2` major-version bump per Principle 6.
- **Semantically stable.** A new release MUST NOT alter the wire shape, the field semantics, or the dispatch-time behavior of a previously registered method. Adding *optional* fields to an existing method's `authentication` object is permitted when omitting the new field reproduces the prior behavior exactly; everything else is breaking.
- **Disjoint identifiers.** A newly registered identifier MUST NOT shadow, alias, or extend a previously registered identifier. New methods take new names.

### Registration mechanism

Registration of a new `Authentication Method` (or a new `<store>` token) in `v1.x` is a single-maintainer decision per `GOVERNANCE.md` (`v1.x` stewardship). The procedure is:

1. The proposed method or token is filed as an issue in the spec repository, prefixed `[RFC]` per `CONTRIBUTING.md`. The issue describes the wire shape, the rationale, the conformance level (MUST / SHOULD / MAY), and the prior art justifying registration.
2. The maintainer reviews the proposal in public. Outside contributors comment per `CONTRIBUTING.md`.
3. If accepted, the registration lands as an editorial revision to this document (§2.1, the relevant method-specific section, and §2.9), to §2.7 if a new `<store>` token is registered, and to `SPEC.md`.
4. The release in which the registration lands is a `v1.x` release where `x` increments. The new identifier is recognized from that release forward.

`v2` introduces a public RFC process for registrations per Principle 12; `v1.x` registrations are the maintainer's call. The single-maintainer decision is appropriate for `v1.x` because the protocol is small, the authentication-method surface is dominated by the well-known schemes already in §2.1, and reaching consensus through a public RFC process before there is a real implementer base would slow the protocol's evolution without improving its quality.

### Implementation behavior on unregistered methods

A `Conforming Implementation` MAY support or decline any registered method per its declared conformance level (§2.9). When an implementation encounters a `method` value that is not in the registry the implementation knows — that is, an identifier registered in a later `v1.x` release than the implementation was built against, or an identifier registered through a private extension the implementation is not aware of — the implementation:

- MUST decline the artifact silently, in the sense that it MUST NOT substitute a different method, MUST NOT guess at the wire shape, and MUST NOT attempt dispatch.
- MUST surface the decline as an explicit failure (per Stage 4's failure-mode vocabulary, this is a `bad_input` failure during validation, or a configuration-shaped failure during dispatch setup).

"Silently" in this rule means "without falling back to a substitute behavior the artifact did not request." It does not mean "without telemetry"; implementations SHOULD log the unregistered identifier for diagnostics.

The combination of the additive-only rule and the silent-decline rule is what makes the method registry safe to extend: an old implementation encountering a new method refuses cleanly rather than misinterpreting; a new implementation always understands every old method.

## 2.10 Session-cookie authentication (`session_cookie`)

`session_cookie` is the registered method for `Provider`s that have no public API surface but are reachable via a web interface. The user logs in to the `Provider` through a browser; the browser-captured cookie state is stored; UACP replays the cookies on every dispatched request, with optional CSRF token refresh on `401`/`403`/`419` responses.

The motivating use case is the long tail of useful services that lack documented APIs: research and notebook tools (Google NotebookLM is the canonical example, served only through the same RPC endpoint the web UI uses), internal enterprise dashboards reachable only through SSO, and consumer products whose reverse-engineered web APIs are the only access path. The reference implementation pattern is the open-source library `notebooklm-py` (https://github.com/teng-lin/notebooklm-py), which documents the cookie + CSRF dance for NotebookLM.

This method carries higher operational risk than the other registered methods. Replaying browser-captured cookies against an undocumented or restricted API is a grey-zone practice — it may violate the `Provider`'s Terms of Service, and the credential blast radius equals a stolen browser cookie. UACP supports the method because the use case is real and unsupported by any other registered method; the conformance rules below ensure the risk is surfaced explicitly to operators.

### Wire shape

```json
{
  "method": "session_cookie",
  "tos_acknowledged": true,
  "storage_state_ref": "secret://local-keyring/notebooklm-storage-state",
  "cookie_names": ["SID", "HSID", "SSID", "APISID", "SAPISID"],
  "csrf_token": {
    "header_name": "X-Same-Domain",
    "cookie_name": "_csrf_token",
    "refresh_url": "https://notebooklm.google.com/_/NotebookLmRpcs/csrf",
    "extraction_path": "$.token",
    "extraction_format": "json"
  }
}
```

Field requirements:

- **`tos_acknowledged`** (required, boolean, MUST be `true`) — the operator's explicit acknowledgment that they have evaluated the `Provider`'s Terms of Service and accept responsibility for compatibility. A `Conforming Implementation` MUST refuse to load `.uacp` files using `session_cookie` when this field is absent or set to anything other than literal `true`. This is an audit hook: the field's presence in the artifact is the artifact author's signature on the ToS-violation-risk evaluation, and §3.10 validation enforces it.
- **`storage_state_ref`** (required, string) — a `secret://` reference per §2.7 resolving to the cookie jar's contents. The cookie jar follows Playwright's [`storage_state.json`](https://playwright.dev/docs/api/class-browsercontext#browser-context-storage-state) format: a JSON object with a `cookies` array (each cookie has `name`, `value`, `domain`, `path`, `expires`, `httpOnly`, `secure`, `sameSite`) and optionally an `origins` array. UACP does not redefine the format.
- **`cookie_names`** (optional, array of strings) — a whitelist of cookie names to send. When omitted or empty, all cookies in the storage state matching the request URL are sent. When non-empty, only listed names are sent — useful when the `Provider` sets analytics or non-auth cookies that aren't needed for replay and reduce the credential surface.
- **`csrf_token`** (optional, object) — declares how a CSRF token is read and refreshed. Required for `Provider`s that enforce CSRF on the API endpoint; optional for those that don't.
  - `header_name` (required when `csrf_token` is declared, string) — the HTTP header where the token is placed on outgoing requests.
  - `cookie_name` (optional, string) — when the token is also a cookie, its name in the storage state. The dispatcher reads this cookie's value and replays it as the header.
  - `refresh_url` (optional, string) — when `Provider` returns `401`/`403`/`419` indicating CSRF expiry, the dispatcher fetches this URL with current cookies, extracts a fresh token via `extraction_path`, and retries the original request once. Without `refresh_url`, the connection effectively expires whenever the CSRF rotates and re-capture is required.
  - `extraction_path` (optional, string) — JSONPath in the §3.4 minimal subset (`$.field` / `$.field.subfield`) OR a regex with one capture group, depending on `extraction_format`.
  - `extraction_format` (optional, string, default `json`) — `json` or `regex`.

### Conformance

A `Conforming Implementation` of `v1.x`:

- **MAY** support `session_cookie`. The conformance level is `MAY` (lower than the `SHOULD` of registered grey-zone-free methods like AWS SigV4 and OAuth 1.0a) because of the operational risk. Implementations targeting curated `Provider` sets MAY decline session_cookie entirely; implementations covering the long tail SHOULD support it because it is the only reachable path for many useful services.
- **MUST** surface a clear ToS-violation-risk warning to the operator at connection-creation time when supporting `session_cookie`. The warning MUST be surfaced through the implementation's user-facing surface (CLI message, UI banner, IDE notification, equivalent); a docstring or a buried log line is insufficient. The exact wording is implementation-defined; the warning MUST mention that replaying browser-captured cookies may violate the `Provider`'s Terms of Service.
- **MUST** refuse to load `.uacp` files using `session_cookie` without `tos_acknowledged: true`. The §3.10 validator enforces this; the artifact's `authentication.tos_acknowledged` field MUST be present and MUST be the literal boolean `true` (not `"true"` string, not `1`, not absent). The refusal is `bad_input` per Principle 8.
- **SHOULD** log every dispatch through a `session_cookie` connection at audit-log level INFO with a `risk: tos_violation_potential` field per §6.6's audit requirements. The audit trail is the operational evidence that the operator's ToS evaluation extends across the lifetime of the connection.

### Cookie injection at request time

Cookies are sent in the `Cookie` header per RFC 6265. The implementation reads the cookie jar, filters by domain matching per RFC 6265 §5.1.3 (exact match for host-only cookies; parent-domain match for cookies with a leading-dot domain), filters by path matching per §5.1.4, and gates `Secure` cookies on HTTPS per §4.1.2.5 (which §4.2 already mandates as the only permitted scheme).

Cookie semantics across the request boundary:

- `HttpOnly` cookies are honored: the flag is for the server-side, not the client. Replay is correct and intentional.
- `Secure` cookies require HTTPS, which §4.2 already mandates.
- `SameSite` restrictions don't apply to programmatic clients. The request isn't a browser navigation; the `SameSite=Lax` / `SameSite=Strict` rules are browser-side enforcement, not wire-level.

When the storage state has no cookies matching the request URL (no domain match for any entry), the dispatcher MUST surface `auth_expired` per Principle 8 with a diagnostic message identifying the URL/domain mismatch. Continuing the request would either fail at the `Provider` or, worse, succeed without authentication and leak data into a public-API surface.

### CSRF token handling

When the `csrf_token` block is declared, the dispatcher's per-request flow:

1. On every dispatch, read the current CSRF token (from runtime state if a refresh recently completed, otherwise from the cookie named in `csrf_token.cookie_name`).
2. Add the token as the value of the header named `csrf_token.header_name`.
3. Dispatch the request through the standard §4.2 transport.
4. On `401`/`403`/`419` responses (per `Provider` convention; the dispatcher MAY widen to other statuses based on envelope inspection), invoke the refresh flow: fetch `csrf_token.refresh_url` with the current cookies, parse the response per `csrf_token.extraction_format`, extract the new token via `csrf_token.extraction_path`, update runtime state, retry the original request once.
5. If the retry also fails, surface `auth_expired` with a diagnostic message indicating CSRF refresh did not recover the connection.

Implementations SHOULD support the auto-refresh flow. Without it, sessions that rotate CSRF every few minutes (the common case for production-grade `Provider`s) become unusable; the spec keeps it `SHOULD` rather than `MUST` because some `Provider`s don't rotate CSRF and the refresh flow is dead code there.

### Storage-state format

The cookie jar follows Playwright's `storage_state.json` format. UACP does not redefine the format; conforming implementations MAY use any JSON-equivalent serialization but the field names MUST match Playwright's: `name`, `value`, `domain`, `path`, `expires` (Unix seconds; `-1` for session cookies), `httpOnly`, `secure`, `sameSite`. The `origins` array (used by Playwright for localStorage / sessionStorage) is permitted in the artifact but `Conforming Implementation`s MAY ignore it for cookie-only auth flows.

### Capture flow

UACP does not specify how the storage state is captured. The intended flow:

1. The operator runs a one-shot helper (typically a short Playwright script driven by the implementation's CLI) that opens a Chromium browser to the `Provider`'s login page.
2. The operator logs in normally — username/password, 2FA, whatever the `Provider` requires.
3. On browser close, the helper captures the storage state and writes it (encrypted at rest per §6.3) to the location the artifact's `storage_state_ref` resolves to.

The capture flow is operator-driven and runs outside the dispatch path; it has no normative `v1.x` spec requirements beyond producing a valid `storage_state.json` and writing it to the secret store.

## 2.9 Conformance summary

This section summarizes the conformance level of each registered `Authentication Method` for a `Conforming Implementation` of `v1.x`. The conformance levels are:

- **MUST support** — A `Conforming Implementation` MUST be able to execute the method end-to-end against any `Provider` whose `.uacp` artifact selects it. Failure to support the method MUST cause the implementation to fail conformance for `v1.x`.
- **SHOULD support** — A `Conforming Implementation` SHOULD support the method. Implementations that do not support a SHOULD-method MUST decline artifacts selecting it per §2.8 (silent decline) and MUST document the omission so that consumers know the implementation does not cover that surface.
- **MAY support** — A `Conforming Implementation` MAY support the method. Implementations that do not support a MAY-method MUST decline artifacts selecting it per §2.8.

| Method | Identifier | Conformance | Specified in |
|---|---|---|---|
| Authorization code grant (with PKCE) | `oauth2_authorization_code` | **MUST** | §2.2.1 |
| Client credentials grant | `oauth2_client_credentials` | **MUST** | §2.2.2 |
| Device authorization grant | `oauth2_device_code` | SHOULD | §2.2.3 |
| OAuth 1.0a | `oauth1a` | SHOULD | §2.3 |
| API key (header) | `api_key_header` | **MUST** | §2.4.1 |
| API key (query parameter) | `api_key_query` | **MUST** | §2.4.2 |
| AWS Signature Version 4 | `aws_sigv4` | SHOULD | §2.5.1 |
| HMAC signature (generic) | `hmac_signature` | SHOULD | §2.5.2 |
| Session-cookie replay | `session_cookie` | MAY | §2.10 |
| Custom authentication | `custom_auth` | MAY | §2.6 |

PKCE for `oauth2_authorization_code` is normative per §2.2.1: a `Conforming Implementation` MUST support PKCE, and PKCE MUST be used for any `oauth2_authorization_code` flow originating from a non-confidential client. The `MUST support` row for `oauth2_authorization_code` therefore implies `MUST support PKCE`.

The composite shape (§2.4.3) is supported when both the underlying API-key method and the underlying signed-request method are supported. A `Conforming Implementation` that supports `api_key_header` and `hmac_signature` therefore SHOULD support the `api_key_header` + `hmac_signature` composite, since both pieces are already conformant; an implementation MAY decline the composite specifically per §2.8 if its dispatch pipeline cannot compose the two operations.

### MUST NOT items

The following requirements are normative and apply to every `Conforming Implementation` of `v1.x`:

- A `Conforming Implementation` **MUST NOT** accept `.uacp` artifacts that contain embedded plaintext credentials in any form. Every credential-shaped field MUST be a `secret://` reference per §2.7. Validation MUST reject artifacts that fail this check.
- A `Conforming Implementation` **MUST NOT** support OAuth 1.0a `PLAINTEXT` signing per §2.3.
- A `Conforming Implementation` **MUST NOT** silently substitute a different `Authentication Method` for one it does not support per §2.8.
- A `Conforming Implementation` **MUST NOT** treat OAuth 2.0 `code_challenge_method=plain` as equivalent to `S256`; the values are distinct, `S256` is required by default, and `plain` is permitted only under the narrow conditions in §2.2.1.
- A `Conforming Implementation` **MUST NOT** load `.uacp` artifacts using `session_cookie` without `tos_acknowledged: true`. Per §2.10 the field MUST be present and MUST be the literal boolean `true`; absent or any other value rejects with `bad_input`.

The cumulative effect of the MUST and MUST NOT requirements in this document is that a `Conforming Implementation` of `v1.x` can authenticate to OAuth 2.0 authorization-code-with-PKCE providers, OAuth 2.0 client-credentials providers, header-based API-key providers, and query-parameter API-key providers without any optional capability, and SHOULD additionally cover device-code, OAuth 1.0a, AWS SigV4, and generic HMAC providers. This is sufficient to reach the great majority of the long-tail HTTPS services UACP targets; the `MAY` and unregistered surface is reserved for the residual cases that warrant `custom_auth` or future registration.
