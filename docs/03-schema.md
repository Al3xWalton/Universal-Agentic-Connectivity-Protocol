# UACP Schema and Discovery

This document specifies the schema and discovery layer of UACP `v1.x`. It defines the canonical form of an `Operation` entry in a `.uacp` artifact — the wire shape that describes what an external `Provider`'s API surface looks like — and the three source-to-canonical mappings by which an `Operation` arrives at that form: ingestion of a published OpenAPI specification, parsing of pasted `curl` invocations, and inference from a natural-language description by an LLM. The conformance keywords ("MUST", "MUST NOT", "SHOULD", "SHOULD NOT", "MAY") in this document are interpreted per BCP 14 [[RFC2119](https://datatracker.ietf.org/doc/html/rfc2119)] [[RFC8174](https://datatracker.ietf.org/doc/html/rfc8174)] as established in [Stage 0 — Primer](./00-primer.md).

This document is consistent with the foundational principles in [Stage 1 — Principles](./01-principles.md) and the authentication subsystem in [Stage 2 — Authentication](./02-authentication.md). Where this document refines a principle, it does so by narrowing detail; it does not override.

## 3.0 Overview

A UACP `Connection` is a pairing between an `Authentication Method` (Stage 2) and a description of the external `Provider`'s callable surface (this stage). The `authentication` block of a `.uacp` artifact says *how* the agent proves identity to the `Provider`; the `operations` block says *what* the agent can ask the `Provider` to do once authenticated. The two blocks are independent: an `Operation` does not declare its own authentication, and the file's `authentication` applies to every `Operation` declared in the file. Per-`Operation` authentication overrides are out of scope for `v1.0` and SHOULD be a future `v1.x` addition added through the registration mechanism in §2.8.

The schema layer specifies three things:

1. **The canonical `Operation` form** (Sections 3.1 through 3.5). This is the normative shape that every `Operation` MUST conform to once it is part of a stored `.uacp` artifact, regardless of how the `Operation` originated.
2. **The three source-to-canonical mappings** (Sections 3.6 through 3.8). UACP's distinguishing bet is that schemas can come from any of three sources — published OpenAPI specifications, user-pasted `curl` commands, or LLM inference from natural-language description — and converge to the same canonical form. The canonical form is normative; the three sources are lossy mappings *into* that form, not three competing source formats.
3. **Source priority, validation, and conformance** (Sections 3.9 through 3.11). When sources contribute to the same artifact, how conflicts resolve. What MUST validate before storage. What level of support each source carries for a `Conforming Implementation` of `v1.x`.

### In scope

- The shape of an `Operation` entry: `id`, `summary`, `request`, `response`, optional `description`, `tags`, `deprecated`, `idempotency`, `pagination`, `source` (§3.1 through §3.5).
- The three source mappings: OpenAPI 3.x (and 2.0) ingestion (§3.6), `curl`-paste parsing (§3.7), LLM-inferred drafts with mandatory user review (§3.8).
- Source priority when multiple sources contribute to the same artifact (§3.9).
- Schema validation rules at parse and store time (§3.10).
- Conformance levels for each source (§3.11).

### Out of scope

The following are deliberately deferred to later stages and MUST NOT be inferred from this document:

- **Dispatch behavior.** How an `Operation` is invoked at runtime — pagination loop control (when to stop, how many pages by default), retry policy under transport failure or `5xx` responses, error-recovery behavior, rate-limit handling, streaming-response runtime semantics, parameter binding from agent inputs to the wire request, and normalization of upstream errors into UACP's failure-mode vocabulary — is **Stage 4 (dispatch)**. This stage declares the *schema metadata* that informs dispatch (for example, the pagination *pattern* in §3.4 and the `idempotency` field in §3.1); it does not specify the runtime that consumes the metadata.
- **Connection lifecycle.** The `pending` / `active` / `revoked` state machine, refresh-worker scheduling, observability hooks, and the relationship between schema changes and lifecycle transitions are **Stage 5 (lifecycle)**.
- **Secrets, encryption, scope enforcement, audit.** Secret-store implementations, encryption-at-rest, scope enforcement at dispatch time, audit logging of schema-source decisions, and the threat model are **Stage 6 (security)**. This stage requires that schemas not embed credentials (§3.10); the broader security posture is Stage 6's responsibility.

Where a section in this document approaches one of those boundaries, the boundary is named explicitly.

## 3.1 Canonical operation schema

A `.uacp` artifact contains an `operations` block: an array of `Operation` entries describing the callable surface the artifact's `authentication` block authorizes against. The `Operation` is the smallest unit of dispatch in UACP; a `Connection` exposes its `Operation`s by `id` and the agent invokes them by `id`.

Every `Operation` MUST include four fields:

- **`id`** (required, string) — a stable string identifier, unique within the artifact. The identifier MUST match the regular expression `[a-z][a-z0-9_-]{0,127}`: it begins with a lowercase letter, contains only lowercase ASCII letters, digits, underscores, and hyphens, and is at most 128 characters long. Either `kebab-case` or `snake_case` is permitted; mixing within a single artifact is permitted but discouraged. The `id` is the field the agent's runtime references; it MUST be stable across revisions of the artifact (renaming an `id` is a breaking change to consumers of the artifact).
- **`summary`** (required, string) — a one-sentence human-readable description of what the operation does, written in user-intent vocabulary. The `summary` is the field an agent reads when selecting an operation in response to a user's natural-language request; it SHOULD describe the operation's *effect on the world* rather than its *HTTP shape* ("Send an email" is preferred over "POST to /v1/messages"). Length is not capped, but summaries SHOULD fit on a single line of typical terminal width (under 100 characters).
- **`request`** (required, object) — the request shape per Section 3.2.
- **`response`** (required, object) — the response shape per Section 3.3.

Every `Operation` MAY include the following optional fields:

- **`description`** (optional, string) — longer prose, multiple paragraphs permitted. Used for documentation surfaces and for AI authoring tools that need fuller context than `summary` provides.
- **`tags`** (optional, array of strings) — a flat list of grouping labels. Tags are not hierarchical and have no UACP-defined semantics; they are agent-facing hints used to disambiguate operations and to group them in user interfaces. Examples: `["email", "send"]`, `["read", "messages"]`. Tag values MUST match the same character set as `id`.
- **`deprecated`** (optional, boolean, default `false`) — when `true`, the operation is retained in the artifact for backward compatibility but agents SHOULD prefer alternatives where they exist. Deprecation does not block dispatch; it surfaces as a warning to authoring tools and may surface to users.
- **`idempotency`** (optional, string, default `unknown`) — one of `idempotent`, `not_idempotent`, or `unknown`. This is schema metadata declaring whether re-issuing the request is safe; it informs Stage 4's retry policy (an `idempotent` operation MAY be retried on transport failure; a `not_idempotent` one MUST NOT be auto-retried). Stage 4 specifies the dispatch consumption of this field; Stage 3 specifies only its declaration.
- **`pagination`** (optional, object) — per Section 3.4. When omitted, the operation is treated as `pagination: {pattern: "none"}`.
- **`source`** (optional, object) — provenance metadata per Sections 3.6 through 3.8. When omitted, the artifact is treated as hand-authored.

### Minimal example

The following is a complete, valid `Operation` entry with only the required fields:

```json
{
  "id": "send_message",
  "summary": "Send a chat message to a channel.",
  "request": {
    "method": "POST",
    "path": "/api/chat.postMessage",
    "body": {
      "type": "object",
      "required": ["channel", "text"],
      "properties": {
        "channel": { "type": "string" },
        "text": { "type": "string" }
      }
    }
  },
  "response": {
    "200": {
      "description": "Message posted.",
      "body": {
        "type": "object",
        "required": ["ok", "ts"],
        "properties": {
          "ok": { "type": "boolean" },
          "ts": { "type": "string" }
        }
      }
    }
  }
}
```

### Fully populated example

The following example exercises every optional field at the operation level:

```json
{
  "id": "list_messages",
  "summary": "List messages in a channel, newest first.",
  "description": "Returns up to 100 messages per page from the named channel. The caller pages with the cursor returned in the previous response. Soft-deleted messages are excluded by default; pass include_deleted=true to retrieve them.",
  "tags": ["messages", "read"],
  "deprecated": false,
  "idempotency": "idempotent",
  "request": {
    "method": "GET",
    "path": "/api/conversations.history",
    "query_parameters": {
      "type": "object",
      "required": ["channel"],
      "properties": {
        "channel": { "type": "string" },
        "cursor": { "type": "string" },
        "limit": { "type": "integer", "minimum": 1, "maximum": 100, "default": 100 },
        "include_deleted": { "type": "boolean", "default": false }
      }
    }
  },
  "response": {
    "200": {
      "description": "A page of messages.",
      "body": {
        "type": "object",
        "required": ["ok", "messages"],
        "properties": {
          "ok": { "type": "boolean" },
          "messages": {
            "type": "array",
            "items": { "type": "object" }
          },
          "response_metadata": {
            "type": "object",
            "properties": {
              "next_cursor": { "type": "string" }
            }
          }
        }
      }
    },
    "4xx": {
      "description": "Client error. The body carries a structured error envelope.",
      "body": {
        "type": "object",
        "required": ["ok", "error"],
        "properties": {
          "ok": { "type": "boolean", "const": false },
          "error": { "type": "string" }
        }
      }
    }
  },
  "pagination": {
    "pattern": "cursor",
    "request_cursor_parameter": "cursor",
    "response_cursor_path": "$.response_metadata.next_cursor"
  },
  "source": {
    "type": "openapi",
    "url": "https://example.com/openapi.yaml",
    "ingested_at": "2026-05-04T15:32:11Z"
  }
}
```

## 3.2 Request shape

The `request` object on an `Operation` describes the wire shape of the HTTP request that dispatch will issue. It MAY contain the following fields:

- **`method`** (required, string) — the HTTP method. MUST be one of `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`, in uppercase. Other methods (including `TRACE`, `CONNECT`, and provider-specific extensions like `MKCOL`) are out of scope for `v1.0` and MUST be rejected at validation time with `bad_input`. Future `v1.x` releases MAY register additional methods through §2.8's extension mechanism applied to this layer.
- **`path`** (required, string) — the request path expressed as a URI Template per RFC 6570 [[RFC6570](https://datatracker.ietf.org/doc/html/rfc6570)]. Path parameters appear in braces, for example `/users/{user_id}/messages/{message_id}`. The base URL of the `Provider` is declared elsewhere in the `.uacp` artifact (Stage 4 specifies the base-URL field; this stage assumes its existence). The `path` field carries only the path component and any embedded path parameters; query parameters are declared separately in `query_parameters` and MUST NOT appear in `path`.
- **`path_parameters`** (conditional, object) — a JSON Schema 2020-12 [[JSON-Schema-2020-12](https://json-schema.org/draft/2020-12)] object describing the path parameters. The schema is an `object` whose `properties` keys MUST be the exact names referenced inside braces in `path`, and whose `required` array SHOULD contain every parameter that has no default (path parameters are typically all required). Required if and only if `path` contains any brace-delimited parameter; absent and rejected as invalid otherwise. See the bidirectional rule below.
- **`query_parameters`** (optional, object) — a JSON Schema 2020-12 object describing the query string. The schema is an `object` whose `properties` describe each query-string parameter. Repeated parameters (RFC 3986 §3.4 permits them) are expressed as an `array`-typed property. Encoding follows `application/x-www-form-urlencoded`; UACP imposes no further restriction.
- **`headers`** (optional, object) — a JSON Schema 2020-12 object describing HTTP request headers other than the authentication-bearing headers managed by the file's `authentication` block (Stage 2). The schema's `properties` keys are header names, matched case-insensitively per RFC 9110 §5.1. Headers carrying credentials (for example, `Authorization`, `X-API-Key` when used as the API-key destination) MUST NOT appear in `headers`; they are produced by the authentication subsystem at dispatch time. The `Content-Type` header SHOULD be derived from `body.media_type` rather than declared in `headers`.
- **`body`** (optional) — one of:
  - the literal value `"none"` (the operation has no request body; equivalent to omitting the field);
  - an inline object `{"media_type": "<type>", "schema": <JSON Schema>}` declaring the request body's media type and JSON Schema; or
  - an object containing a `$ref` pointing into a top-level `definitions` block of the `.uacp` artifact, of the form `{"$ref": "#/definitions/<name>"}` per JSON Schema 2020-12 §8.2. Remote `$ref` resolution at dispatch time is forbidden by Principle 9 (determinism); local `$ref` is permitted because it resolves at parse time against the static artifact.

  When `body` is an inline object, `media_type` defaults to `application/json` if omitted. When the operation accepts more than one body shape (a small minority of providers; the typical case is "JSON or form-encoded fallback"), the body schema SHOULD use a JSON Schema `oneOf` to express the alternatives:

  ```json
  "body": {
    "media_type": "application/json",
    "schema": {
      "oneOf": [
        { "$ref": "#/definitions/JsonRequest" },
        { "$ref": "#/definitions/FormRequest" }
      ]
    }
  }
  ```

  Multipart and binary bodies are permitted; the schema SHOULD describe the parts at a level the dispatch runtime can serialize. The runtime serialization rules are Stage 4.

### Bidirectional rule on path parameters

Every parameter named between braces in `path` MUST be defined as a property in `path_parameters`, and every property of `path_parameters` MUST appear at least once between braces in `path`. The two sides MUST agree exactly: a parameter named in `path` but not declared in `path_parameters` (or vice versa) is a schema-validation failure that MUST cause the artifact to be rejected at parse time per §3.10. This rule is an audit hook: it makes path-parameter typos catchable before dispatch, where the consequence would be a runtime `bad_input` against the `Provider`.

### Content negotiation

A single `Operation` describes a single intended request shape. When a `Provider` exposes "the same operation" with materially different request shapes (for example, REST vs. GraphQL endpoints over the same resource), UACP treats them as separate `Operation`s with distinct `id`s. When the `Provider` exposes one endpoint that accepts multiple body shapes interchangeably, the `oneOf` pattern above is the canonical form.

## 3.3 Response shape

The `response` object on an `Operation` describes the wire shape of the responses the `Provider` may return. The object's keys are status codes or status ranges; the values describe the response for that status.

### Status keys

Permitted status keys are:

- An exact three-digit HTTP status code, written as a string: `"200"`, `"201"`, `"204"`, `"401"`, `"404"`, `"429"`, `"500"`, etc.
- A range: `"1xx"`, `"2xx"`, `"3xx"`, `"4xx"`, `"5xx"`. The range matches every status in the corresponding RFC 9110 §15 family.
- The literal `"default"`, matching any status not otherwise covered. Useful when a `Provider`'s responses follow a uniform error envelope across most non-success statuses.

When a status key collides with a range or `default`, the more specific key wins for that status: `"401"` overrides `"4xx"`, which in turn overrides `"default"`. UACP imposes no requirement that every status the `Provider` may emit be enumerated; status codes not described by any key dispatch as opaque per Stage 4.

### Response entry shape

Each value under a status key is an object with the following fields:

- **`description`** (required, string) — a one-sentence human-readable description of what the response represents. Like `summary` on the operation itself, the description is read by agents and authoring tools.
- **`body`** (optional) — the response body's JSON Schema and media type, in the same shape as `request.body` (§3.2): either `"none"`, an inline `{"media_type": ..., "schema": ...}`, or a `$ref` into local `definitions`. When omitted, the response body is treated as opaque (the dispatch runtime may pass it through but does not validate or extract from it).
- **`headers`** (optional, object) — a JSON Schema describing response headers the agent may want to read. Headers used by the dispatch runtime for control-flow purposes (for example, the RFC 8288 `Link` header consumed by `link_header` pagination per §3.4) SHOULD be declared here so that the agent's introspection of the response is grounded in the schema rather than ad-hoc.
- **`streaming`** (optional, boolean, default `false`) — when `true`, the response body is a stream of chunks rather than a single response body. The `body` schema in this case describes one chunk's shape; the dispatch runtime is responsible for delivering each chunk through Stage 4's streaming surface. The chunk-delimiter convention (Server-Sent Events, newline-delimited JSON, length-prefixed framing, gRPC streaming) is part of the dispatch contract and is specified in Stage 4. This stage declares only that the response is streaming and that the schema describes one chunk.

### Canonical error envelopes

Many `Provider`s return errors in a structured envelope rather than a free-form body — for example, `{"ok": false, "error": "invalid_auth"}` for Slack, `{"errors": [{"message": "...", "code": "..."}]}` for GraphQL providers, or `{"type": "/errors/invalid", "title": "...", "detail": "..."}` for problem+json [[RFC9457](https://datatracker.ietf.org/doc/html/rfc9457)] providers.

A `.uacp` artifact SHOULD declare these envelopes under the appropriate status range (`"4xx"`, `"5xx"`) so that the dispatch runtime can extract a meaningful error code and message rather than treating the entire body as opaque. Declaring the envelope is metadata only; how the dispatch runtime *uses* the envelope to populate UACP's normalized failure-mode vocabulary is Stage 4's responsibility, and the recovery behavior (retry, escalation, surfacing to the user) is also Stage 4. This stage's contribution is the schema declaration; Stage 4 picks up from there.

The `oneOf` pattern is appropriate when the same status range carries more than one envelope shape (success-with-warnings vs. structured-error, for example).

## 3.4 Pagination metadata

When an `Operation` paginates, the `Operation` declares the pagination pattern via the `pagination` object. The dispatch runtime uses the pattern declaration to know how to advance between pages and where in the response the next-page identifier lives; the runtime's loop-control behavior (when to stop, how many pages to fetch by default, how to surface partial results to the agent) is Stage 4's responsibility.

The `pagination` object MUST include a `pattern` field. Permitted patterns in `v1.0`:

### `cursor`

The `Provider` issues opaque cursors. The request carries a cursor parameter; the response includes the next cursor. The first request omits the cursor; a response without a next cursor (typically a missing or empty field) terminates the sequence.

```json
"pagination": {
  "pattern": "cursor",
  "request_cursor_parameter": "cursor",
  "response_cursor_path": "$.response_metadata.next_cursor"
}
```

Fields:

- `request_cursor_parameter` (required, string) — the name of the query parameter or body field carrying the cursor on the request. The parameter MUST be declared in `request.query_parameters` (or `request.body` for body-bound cursors) per the bidirectional rule in §3.2.
- `response_cursor_path` (required, string) — a JSONPath expression per the JSONPath baseline [[RFC9535](https://datatracker.ietf.org/doc/html/rfc9535)] that locates the next cursor in the response body. An empty or missing value at the resolved path indicates the end of the sequence.

### `offset`

The `Provider` paginates by integer offset and limit. The request carries `offset` and `limit` parameters; the response indicates total count or whether more pages exist.

```json
"pagination": {
  "pattern": "offset",
  "request_offset_parameter": "offset",
  "request_limit_parameter": "limit",
  "response_total_path": "$.total",
  "response_has_more_path": null
}
```

Fields:

- `request_offset_parameter` (required, string) — the request parameter carrying the offset.
- `request_limit_parameter` (required, string) — the request parameter carrying the page size.
- `response_total_path` (optional, string) — a JSONPath locating the total record count in the response body. Either `response_total_path` or `response_has_more_path` MUST be present.
- `response_has_more_path` (optional, string) — a JSONPath locating a boolean field in the response body indicating whether more pages exist. Either this field or `response_total_path` MUST be present; both MAY be present, in which case `response_has_more_path` takes precedence at dispatch time per Stage 4.

### `link_header`

The `Provider` follows RFC 8288 [[RFC8288](https://datatracker.ietf.org/doc/html/rfc8288)] Web Linking: the response's `Link` header carries `rel="next"` (and optionally `rel="prev"`, `rel="first"`, `rel="last"`) values pointing at subsequent pages.

```json
"pagination": {
  "pattern": "link_header"
}
```

The pattern carries no additional fields. The dispatch runtime MUST follow the `rel="next"` link until the header omits a `next` relation. The response's `headers` schema (§3.3) SHOULD declare the `Link` header so that the response shape is grounded in the artifact rather than ad-hoc.

### `none`

The operation does not paginate. Equivalent to omitting the `pagination` object.

```json
"pagination": { "pattern": "none" }
```

### Future patterns

Patterns such as keyset pagination, page-number pagination distinct from `offset`, and timestamp-windowed pagination are recognized in the long tail but not registered in `v1.0`. Future `v1.x` releases MAY register additional patterns through the §2.8 mechanism applied to this layer; new patterns are additive and MUST NOT alter the semantics of registered patterns.

## 3.5 Operation references and discovery

This section specifies how operations are named, how the agent finds the right operation given a user's intent, and the constraints that make discovery deterministic across `Conforming Implementation`s.

### Naming and namespace

Within a single `.uacp` artifact, operations form a flat namespace identified by `id`. Hierarchical naming, sub-namespaces, and grouping by tag are all surface conveniences for authoring tools and user interfaces; they do not affect the lookup contract. A `Conforming Implementation` MUST be able to resolve any operation by its `id` in O(1) time relative to the `Operation` count, using a hash-style lookup at load time.

Two normative rules govern operation naming:

- **Uniqueness.** Two operations within the same `.uacp` artifact MUST NOT share an `id`. Validation per §3.10 rejects duplicates at parse time.
- **Stability.** An operation's `id` SHOULD remain stable across revisions of an artifact. Renaming an `id` is a breaking change to consumers of the artifact (the agent that learned to dispatch `gmail.send` will not find `gmail.send_message` after a rename); when a rename is unavoidable, the prior `id` SHOULD be retained as a `deprecated: true` operation pointing at the new one for at least one revision cycle. The cycle length and the deprecation surface are properties of the artifact's authoring process, not of UACP.

### Agent-driven discovery

The primary use of operation metadata at runtime is for the agent to select an operation in response to a user's natural-language intent. When the user says "send an email," the agent reads the `summary` and `tags` fields of the available operations, picks the closest match, and dispatches.

Two implications follow for artifact authors:

- **`summary` is the discovery surface.** Authoring tools (and especially the LLM-inference path in §3.8) SHOULD write `summary` values in user-intent vocabulary rather than provider-marketing vocabulary or HTTP-shape vocabulary. "Send an email" is a usable summary; "POST /v1/messages" and "Use the Gmail API to perform a message-send operation against the v1 endpoint" are both worse for discovery (the first because it mirrors the wire, the second because it contains stop-words and provider names that match many operations equally well).
- **`tags` are disambiguators.** When two operations have similar summaries — for example, `get_user_profile` and `update_user_profile` — well-chosen tags (`["read"]` vs. `["write"]`) cut the ambiguity at the cost of a single token in the agent's selection prompt. UACP does not enforce a tag taxonomy; conventions are left to authoring practice.

UACP does not specify the agent's selection algorithm. Implementations MAY use embedding similarity, keyword matching, or LLM-based selection; the spec's only normative requirement is that operations remain resolvable by `id` once the agent has selected one.

### Surface guarantees for the dispatch runtime

The dispatch runtime (Stage 4) MUST be able to look up any operation by its `id`, MUST be able to enumerate all operations in the artifact, and MUST be able to filter by `tags`. The artifact's storage format makes these properties trivially available; this stage records them as obligations on the load step.

## 3.6 Schema source: OpenAPI ingestion

When a `Provider` publishes an OpenAPI specification [[OAS-3.1](https://spec.openapis.org/oas/v3.1.0)], that specification is the lowest-friction source of UACP `Operation`s. UACP MAY ingest a published OpenAPI 3.0 or 3.1 document and derive the `operations` block of a `.uacp` artifact directly from it. Ingestion is a one-time mapping at authoring time; the produced `.uacp` artifact is canonical from that point onward and is not re-derived from the OpenAPI source on every dispatch.

### Mapping

The mapping from OpenAPI to UACP is normative for `Conforming Implementation`s that perform ingestion. The fields below are mapped in both directions of the spec — implementations that generate OpenAPI from a `.uacp` artifact (the symmetric direction, supported but not required) follow the inverse.

| OpenAPI field | UACP field | Notes |
|---|---|---|
| `paths.<path>.<method>.operationId` | `id` | OpenAPI `operationId` is optional; when missing, implementations SHOULD synthesize a stable `id` from `<method>_<path>` with characters outside `[a-z0-9_-]` replaced by `_`. The synthesis rule MUST be deterministic. |
| `paths.<path>.<method>.summary` | `summary` | Direct copy. When OpenAPI `summary` is missing, implementations MAY use the first sentence of `description` as a fallback; otherwise the field is absent and the artifact fails validation per §3.1. |
| `paths.<path>.<method>.description` | `description` | Direct copy when present. |
| `paths.<path>.<method>.tags` | `tags` | Direct copy when present, with values normalized to the `[a-z0-9_-]` charset. |
| `paths.<path>.<method>.deprecated` | `deprecated` | Direct copy. |
| `paths.<path>` (the path key) and the method | `request.path` and `request.method` | OpenAPI path templating uses `{name}` syntax that aligns with RFC 6570 Level 1 templates; the value transfers verbatim. The method transfers as uppercase. |
| `paths.<path>.<method>.parameters[in=path]` | `request.path_parameters` | Each path parameter is a property of the `path_parameters` JSON Schema object. The `required` flag on each parameter MUST map to membership in the JSON Schema `required` array. |
| `paths.<path>.<method>.parameters[in=query]` | `request.query_parameters` | Same mapping as path parameters. Repeated parameters (`style: form, explode: true` over array types) become array-typed JSON Schema properties. |
| `paths.<path>.<method>.parameters[in=header]` | `request.headers` | Header parameters that participate in authentication MUST be excluded; see "Excluded fields" below. |
| `paths.<path>.<method>.requestBody` | `request.body` | The first declared `content[*]` entry is taken as the canonical body shape; when multiple media types are declared, they map to a JSON Schema `oneOf`. The OpenAPI `required` on `requestBody` corresponds to whether `body` is present at all; UACP treats body as required by default when declared, and `body: "none"` is the explicit absence. |
| `paths.<path>.<method>.responses` | `response` | Each response key transfers verbatim (HTTP status codes and `default` line up). The `description` and `content[*].schema` map to UACP `description` and `body`. Response `headers` map to UACP response `headers`. |
| `components.schemas.<name>` | `definitions.<name>` | OpenAPI `components.schemas` becomes the UACP artifact's local `definitions` block; `$ref` strings of the form `#/components/schemas/<name>` are rewritten to `#/definitions/<name>`. |

### Pagination, idempotency, source

OpenAPI does not have first-class fields for the pagination patterns in §3.4 or for the `idempotency` field in §3.1. Implementations performing ingestion:

- MAY infer pagination by inspecting parameter names against a registered set of conventions (parameters named `cursor` and a response field named `next_cursor` strongly suggest `cursor` pagination; parameters named `offset` and `limit` suggest `offset` pagination; an `Link` response header suggests `link_header` pagination). When inference is performed, the produced `pagination` object SHOULD be flagged for review by the user before storage.
- SHOULD set `idempotency` based on the HTTP method: `GET`, `HEAD`, `OPTIONS`, `PUT`, and `DELETE` default to `idempotent` per RFC 9110 §9.2.2; `POST` and `PATCH` default to `unknown`. The user MAY override these defaults during authoring.
- MUST emit a `source` field on every ingested `Operation`, of the shape `{"type": "openapi", "url": "<source url>", "ingested_at": "<RFC 3339 timestamp>"}`. The `url` field is the source's canonical URL when available; for ingested-from-file sources the field MAY carry a file URI or a placeholder identifier. The `ingested_at` field uses RFC 3339 [[RFC3339](https://datatracker.ietf.org/doc/html/rfc3339)] timestamp format with timezone.

### Excluded fields

The following OpenAPI fields are not mapped during ingestion:

- **`security` and `securitySchemes`.** Authentication is Stage 2's responsibility. An implementation that ingests OpenAPI MAY surface the OpenAPI security schemes as a hint to the user during authoring (so the user can choose the appropriate Stage 2 method), but the ingested `.uacp` artifact's `authentication` block is authored separately and MUST NOT be derived solely from OpenAPI metadata.
- **`servers`.** The `Provider`'s base URL is a top-level field of the `.uacp` artifact (Stage 4 specifies the field). When the OpenAPI document carries a single `servers` entry, implementations SHOULD pre-populate the `.uacp` artifact's base URL from it; when there are multiple, implementations SHOULD prompt the user to choose during authoring. Per-operation `servers` overrides are out of scope for `v1.0`.
- **`callbacks`, `webhooks`.** These describe `Provider`-initiated interactions, which are out of scope for UACP `v1.0`'s HTTPS-only, agent-initiated transport (Principle 11 and Stage 4). Implementations MAY warn the user that callbacks are present; they MUST NOT silently drop them without surfacing the omission.

### Conformance for ingestion

A `Conforming Implementation` of `v1.x` MUST support ingestion of OpenAPI 3.0 and 3.1 documents per the mapping above. A `Conforming Implementation` SHOULD support ingestion of OpenAPI 2.0 (Swagger) documents for legacy `Provider`s; OpenAPI 2.0's structural differences (separate `consumes`/`produces` arrays instead of `content` keyed by media type; `definitions` instead of `components.schemas`; security schemes shaped differently) are mechanical translations that mature ingestion libraries already provide.

## 3.7 Schema source: curl-paste parsing

When the user has a working `curl` invocation against a `Provider` — typically the form copied from the `Provider`'s API documentation, browser developer tools, or a colleague's terminal history — UACP MAY parse it into one or more `Operation` entries. `curl`-paste is a high-friction-low-fidelity source: it produces request shapes only, lacks response and pagination metadata, and requires the user (or LLM, in the AI-mediated authoring path) to supply `id` and `summary` separately.

### Supported flag set

A `Conforming Implementation` that supports `curl`-paste parsing MUST handle the following flags. Flags outside this set MAY be supported; absent or unsupported flags MUST cause the implementation to either succeed for the parts it understands and surface a warning, or decline parsing and surface an error. Silent dropping is forbidden.

| Flag | Meaning | UACP destination |
|---|---|---|
| `-X <method>` / `--request <method>` | HTTP method | `request.method`. When absent, the method defaults to `GET` if no body flag is present, or `POST` if `-d` / `--data` / `--data-raw` is present. |
| `-H <name: value>` / `--header <name: value>` | Request header | `request.headers` JSON Schema property describing the header. Authentication-bearing headers are detected and stripped per "Authentication detection" below. |
| `-d <data>` / `--data <data>` / `--data-raw <data>` | Request body | `request.body`. The body's media type defaults to `application/x-www-form-urlencoded` per `curl`'s own default; `application/json` is inferred when the body parses as JSON, or when an explicit `Content-Type: application/json` header is present. |
| `-G` / `--get` | Force GET; body data sent as query string | The data is parsed into `request.query_parameters` rather than `request.body`. |
| `--data-urlencode <data>` | URL-encode and send as body (or query with `-G`) | Same destination as `-d`, with the URL-encoding applied; under `-G`, contributes to `request.query_parameters`. |
| URL (positional argument) | The full URL | Decomposed: scheme + host become the artifact's base URL (offered to the user for confirmation; not silently overwriting an existing base URL); path becomes `request.path`; query string becomes `request.query_parameters`. |

The following are explicitly out of scope for `v1.0` `curl`-paste support; implementations encountering them MAY surface a clear error and decline to parse:

- `--form` / `-F` (multipart form data) — multipart bodies are permitted in `.uacp` artifacts but are not inferable from `curl`-paste in `v1.0`. The user MAY hand-author the multipart body schema after the rest of the operation parses.
- `--cookie` / `-b` — cookie-based session auth is not a registered Stage 2 method; cookies parsed from `curl`-paste SHOULD be flagged to the user as out-of-band session state rather than dropped silently.
- `-u <user:password>` / `--user` — basic auth is not a registered Stage 2 method; encountered values are flagged for the user to move into the `.uacp` `authentication` block (typically as `api_key_header` against `Authorization: Basic ...`).
- Compressed response handling, certificate pinning, proxy configuration, retries, and other transport-only flags — these are dispatch concerns (Stage 4) or environmental concerns; the `Operation` schema does not encode them.

### Multiple invocations

A user MAY paste several `curl` invocations in a single block. The implementation parses each invocation as a separate prospective `Operation`. Because `curl` carries no `id` or `summary` metadata, the AI-mediated authoring path (per Principle 3 and §3.8's user-review pattern) is the typical surface for assigning `id` and `summary` to each parsed invocation; pure-mechanical parsers MAY synthesize `id`s from the URL path (per the OpenAPI synthesis rule in §3.6) and leave `summary` blank, with the artifact failing §3.10 validation until the user supplies one.

### Authentication detection and stripping

Authentication-bearing artifacts in `curl`-paste — `-H "Authorization: Bearer <token>"`, `-H "X-API-Key: <key>"`, `-u <user:password>`, query-string parameters with names like `api_key` — MUST be detected and MUST NOT be embedded in the produced `Operation`. The recommended behavior is:

1. Strip the credential from the parsed operation.
2. Surface the detection to the user with a recommendation: "this `curl` carried an `Authorization: Bearer` token; move it to the file's `authentication` block as an `oauth2_authorization_code` flow (or another appropriate Stage 2 method)."
3. Do not auto-populate the `authentication` block; the choice of Stage 2 method is the user's, and the credential value is never persisted in the artifact in any case (per §2.7).

This is the same posture as the rest of UACP: credentials never enter the artifact, even transiently during authoring.

### Response shapes

`curl` is a request-only artifact; a parsed `curl` invocation cannot describe `response`. The produced `Operation` enters the artifact with `response` either absent (failing §3.10 validation until the user supplies one) or set to a placeholder `{"default": {"description": "Response unknown.", "body": "none"}}` to permit interim editing. The placeholder is implementation-defined; the spec's only requirement is that an artifact with a placeholder response MUST NOT pass §3.10 validation if the placeholder is a literal `"none"` body in production storage.

### Conformance for curl-paste

A `Conforming Implementation` of `v1.x` SHOULD support `curl`-paste parsing for the flag set above. Implementations MAY decline complex `curl` invocations (multipart, custom transport, exotic flag combinations) and surface a clear error indicating which part of the invocation could not be parsed. Implementations MUST NOT silently drop unparseable parts; the user's `curl` and the produced `Operation` must remain comparable.

## 3.8 Schema source: LLM-inferred schemas

The third source — LLM inference from natural-language description — is UACP's distinguishing affordance. When neither an OpenAPI specification nor a working `curl` invocation is available, the user describes the `Provider` in English and an LLM generates a draft `.uacp` artifact. This path is what makes Principle 3 (AI-native authoring) real: it is the difference between a protocol that *permits* hand-authoring and a protocol that *expects* AI-mediated authoring.

UACP does NOT specify the LLM, the prompt structure, the inference pipeline, or the user-interface surface that captures the natural-language description and presents the result. Those are implementation concerns, varying with the agent's host environment, the user's tolerance for review friction, and the LLM available. UACP DOES specify the artifact-level constraints on the inferred result and the user-review obligations the implementation MUST satisfy before storage.

### Mandatory user review

An LLM-inferred schema MUST be presented to the user for explicit review before being persisted into a stored `.uacp` artifact. This is a normative requirement, not a recommended workflow:

- A `Conforming Implementation` MUST NOT persist an LLM-inferred schema without an explicit user-approval step in the authoring flow. "Persist" means writing the artifact to durable storage from which dispatch will subsequently load it; in-memory drafts during the authoring session are not persistence and are not subject to this rule.
- The review presentation MUST include three things at minimum: (a) the canonical JSON form of the inferred operation(s), (b) a human-readable summary of each operation's intent (the `summary` field of each operation, plus any explanatory commentary the implementation chooses to surface), and (c) the source description text the LLM was given. The user MUST be able to read all three before approving.
- The user MUST be able to edit the inferred schema before approval. An implementation that only offers "approve as-is" or "reject" is non-conforming; the user must have a path to refine wording, correct field types, fix path templates, and otherwise bring the inferred schema into agreement with what the `Provider` actually accepts.

The user-approval step is the single most important safety property in the inference path. An LLM that produces a wrong path template, a wrong query-parameter type, a fabricated endpoint, or an inferred operation against an entirely different `Provider`'s API will manifest as a runtime dispatch error if persisted unchecked. The review step is the chance to catch these before they reach dispatch.

### Provenance metadata

An LLM-inferred `Operation` MUST carry a `source` object recording its provenance. The shape:

```json
"source": {
  "type": "inferred",
  "model": "anthropic/claude-sonnet-4.6",
  "description": "The Acme Widget API exposes a POST /widgets endpoint that creates a widget given a name and a color, and returns the widget's id.",
  "confidence": "medium",
  "reviewed_at": "2026-05-04T16:42:09Z"
}
```

Field requirements:

- `type` (required, string) — the literal `"inferred"`.
- `model` (required, string) — a stable identifier for the LLM that produced the draft. The format is `<provider>/<model>` matching the OpenRouter-style identifier convention (for example, `anthropic/claude-sonnet-4.6`, `openai/gpt-4-turbo`); other formats are permitted, but the field MUST be non-empty and MUST identify the model unambiguously enough that a reviewer can later determine which model produced the draft.
- `description` (required, string) — the original natural-language description the user supplied to the LLM. Verbatim; no truncation. This is the field a future reviewer reads to understand what the user *thought* they were asking for.
- `confidence` (optional, string) — one of `low`, `medium`, `high`. A self-reported confidence hint from the inference pipeline (the LLM's own confidence assessment, a heuristic based on prompt clarity, or a calibration from prior successful inferences). The field is informational; it does not gate use, and dispatch behaves identically regardless of confidence. Agents and authoring tools MAY surface confidence to users when an inferred operation behaves unexpectedly.
- `reviewed_at` (required, string) — RFC 3339 timestamp of the user's approval. This field is the load-bearing audit hook: an `Operation` with `source.type == "inferred"` and no `reviewed_at` MUST NOT pass §3.10 validation.

### Refinement

LLM-inferred schemas are not write-once. The expected workflow is iterative: the user describes the `Provider`, the LLM drafts, the user reviews and either approves or refines. Refinement MAY happen at first review, or later when the inferred operation produces unexpected results in dispatch.

The post-creation refinement workflow:

1. The user encounters an unexpected dispatch result against an inferred operation (a `bad_input` from the `Provider`, an unparseable response, or an obviously-wrong shape returned).
2. The user pastes an actual response example, an updated description, or a corrected request shape into the authoring tool.
3. The implementation re-invokes the LLM with the additional evidence; the LLM produces a refined draft.
4. The refined draft goes through the same mandatory-review step in this section.
5. On approval, the operation's `source.description` is updated to include the refinement context (the implementation chooses the format; appending under a separator is typical), the `reviewed_at` timestamp is updated to the current time, and the `Operation`'s `id` MUST remain the same.

The `id` stability rule (§3.5) is what makes refinement compatible with already-deployed agents: the agent learned `gmail.send`, refines the schema underneath, and the agent's existing dispatch references continue to resolve.

### What inference does not produce

The inference path is bounded by what the LLM can reliably produce from a natural-language description:

- Inferred schemas are best-effort. The LLM does not have a live connection to the `Provider`; it cannot test its draft. The dispatch surface (Stage 4) is where inference's accuracy gets exercised in practice.
- Inferred schemas SHOULD prefer permissive JSON Schemas over precise ones. An overly-strict schema that rejects a real response shape causes dispatch failures that look like `Provider` bugs; an overly-permissive schema that accepts more than the `Provider` returns is harmless until it isn't, and refinement is the recovery path. UACP does not normatively prefer one over the other; this is authoring guidance.
- Inferred schemas MUST NOT include credentials, even transiently in `description`. The mandatory-review step is the last line of defense; an implementation MAY scrub the description for credential-shaped strings before passing it to the LLM, and SHOULD surface any detection to the user during review.

### Conformance for inference

A `Conforming Implementation` of `v1.x` MAY support LLM-inference. An implementation that does not support inference MUST decline `.uacp` artifacts whose operations carry `source.type == "inferred"` if the implementation cannot validate the rules in this section against its own state — specifically, an implementation that loads a stored artifact does not need to re-run inference (the user-review step has already occurred at authoring time, and `reviewed_at` records that fact); the conformance burden falls on the *authoring* surface, not on the *load* surface.

## 3.9 Source priority and conflict resolution

A `.uacp` artifact MAY contain operations from multiple sources. The typical pattern: start from an OpenAPI ingestion to scaffold the bulk of the operations, then paste a `curl` invocation for an operation the OpenAPI document missed, then use the LLM-inference path for an operation neither source describes. Each operation carries its own `source` field reflecting how it arrived.

When two sources contribute drafts of the *same* operation (same intended `id`, same intended endpoint), UACP defines a normative priority order. Higher-priority sources win.

### Priority order

From highest to lowest priority:

1. **Explicit user input.** Hand-authored canonical JSON that the user typed or edited directly. This includes operations whose `source` field is absent and operations whose `source` is set to user-driven (the `source` field's `type` value for hand-authored operations is left to implementation convention; the spec does not register a `"hand"` or `"user"` value because the absence of `source` is sufficient).
2. **`curl`-paste.** Operations parsed from a `curl` invocation per §3.7. These reflect a working request the user verified outside UACP and SHOULD be trusted over machine-derived alternatives.
3. **OpenAPI ingestion.** Operations derived from a published OpenAPI specification per §3.6. These are authoritative when the `Provider` publishes them but represent the `Provider`'s self-description, which may diverge from runtime reality.
4. **LLM-inferred.** Operations produced by the inference path per §3.8. These have the highest review burden and the lowest precedence by default.

### Conflict resolution at authoring time

When two prospective operations would share an `id` during authoring, the higher-priority source's contribution wins. The lower-priority source's contribution MAY be:

- **Dropped silently.** The implementation discards the lower-priority draft and surfaces no notification.
- **Surfaced to the user as a warning.** The implementation tells the user "your `curl`-paste added an operation `send_email` that was already present from OpenAPI ingestion; the `curl`-paste version replaced the OpenAPI version. Discard or compare?" and offers a comparison view.

The choice between silent-drop and warning-surface is implementation-defined. The spec does not mandate which; both are conforming. Implementations that prioritize quiet authoring SHOULD still log dropped contributions to a session-local trace so that the user can recover them within the authoring session.

### Conflict resolution at load time

An already-stored `.uacp` artifact contains operations whose conflicts have already been resolved at authoring time. At load time, conflicts manifest as duplicate `id` values within the artifact; this is a §3.10 validation failure, not a conflict to resolve. Load-time validation rejects duplicates rather than picking one.

## 3.10 Schema validation rules

A `.uacp` artifact MUST validate against the published JSON Schema for UACP `v1.x` before a `Conforming Implementation` accepts it for storage or load. This section specifies the validation rules; the JSON Schema document itself is referenced by `$schema` URL inside each artifact, and the canonical URL form is finalized in [Stage 9 — Prototype](./09-prototype.md) when the spec is frozen. Until that point, implementations MAY use a placeholder URL of the form `https://uacp.spec/v1/schema.json` with the understanding that the URL will be replaced before `v1.0` freeze; artifacts pinning a specific URL today MUST be re-pinnable at freeze time without semantic change.

### Required validations

A `Conforming Implementation` MUST perform the following validations at parse time. The validation list is normative; implementations MAY perform additional checks beyond this list.

- **`$schema` reference present.** The artifact's top level MUST include a `$schema` field pointing at the canonical UACP `v1.x` schema URL (or the placeholder above pending Stage 9).
- **Top-level structure.** The artifact MUST be a JSON object with at minimum the fields specified in earlier stages: `authentication` (per Stage 2), `operations` (per this stage), and any base-URL or `Provider`-identification fields specified by Stage 4. Additional top-level fields permitted by the JSON Schema are accepted.
- **Operation `id` uniqueness.** No two operations in the `operations` array share an `id`. Duplicates MUST cause rejection.
- **Operation `id` charset.** Every `id` matches `[a-z][a-z0-9_-]{0,127}` per §3.1.
- **Operation `summary` present and non-empty.** Per §3.1.
- **Method validity.** `request.method` MUST be one of `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`, in uppercase.
- **Bidirectional path-parameter rule.** Every parameter named between braces in `request.path` is a property of `request.path_parameters`, and every property of `request.path_parameters` appears at least once between braces in `request.path`. Per §3.2.
- **Response key validity.** Every key under `response` is either an exact three-digit HTTP status code, a range (`1xx` through `5xx`), or the literal `default`. Per §3.3.
- **Pagination consistency.** When `pagination.pattern` is `cursor`, the `request_cursor_parameter` named MUST exist as a property of `request.query_parameters` or `request.body`. When the pattern is `offset`, `request_offset_parameter` and `request_limit_parameter` MUST both exist as properties of `request.query_parameters`. The cross-reference rule extends the bidirectional posture from §3.2 into §3.4.
- **No embedded credentials.** No field of any operation, anywhere in the artifact, contains a literal credential value. The credential-reference convention from §2.7 — the `_ref` suffix on credential-shaped fields, with values being `secret://` URLs — is the only path by which credentials may appear in the artifact.
- **Inferred operation provenance complete.** Every operation with `source.type == "inferred"` carries a non-empty `source.model`, a non-empty `source.description`, and a `source.reviewed_at` RFC 3339 timestamp. Per §3.8.
- **`$ref` resolution local.** Every `$ref` inside the artifact resolves to a JSON Pointer per JSON Schema 2020-12 §8.2 against the artifact's own `definitions` block. Remote `$ref`s (HTTP/HTTPS URLs, file URIs, JSON Pointers into other artifacts) are forbidden; this is the schema-layer expression of Principle 9 (determinism).

### Failure behavior

When validation fails, a `Conforming Implementation` MUST:

- **Reject the artifact.** No partial loading. An artifact whose third operation has a duplicate `id` is rejected entirely, not loaded with two operations and an error.
- **Surface a clear error.** The error MUST identify which operation (by `id` or array index, depending on whether the operation has a parseable `id`) and which constraint failed. "`operations[2].id` 'send_message' is duplicated by `operations[5]`" is the shape of a usable error; "validation failed" is not.
- **Not dispatch.** Operations from a failed-validation artifact MUST NOT be dispatched. The artifact is unavailable until the failure is corrected.

These behaviors correspond to the `bad_input` failure-mode code in Principle 8's vocabulary; Stage 4 specifies how the failure surfaces through the dispatch contract when validation occurs at runtime against an incoming artifact (for example, during a hot-reload).

### What validation does not do

Validation in this stage does not test that the artifact's described operations actually work against the live `Provider`. Reaching the `Provider` is a dispatch-time activity; testing it is a Stage 4 / Stage 9 (prototype) concern. Validation in this stage is purely structural: the artifact is well-formed and internally consistent. A well-formed artifact that describes a non-existent endpoint passes validation and produces an `upstream_error` at dispatch time.

## 3.11 Conformance summary

This section summarizes the conformance level of each schema source and the load-time behaviors that a `Conforming Implementation` of `v1.x` MUST satisfy.

### Schema sources

| Source | Section | Conformance |
|---|---|---|
| Hand-authored canonical JSON | §3.1 — §3.5 | **MUST support** |
| OpenAPI 3.0 / 3.1 ingestion | §3.6 | **MUST support** |
| OpenAPI 2.0 (Swagger) ingestion | §3.6 | SHOULD support |
| `curl`-paste parsing | §3.7 | SHOULD support |
| LLM inference | §3.8 | MAY support |

The conformance levels split along the line between "the implementation must be able to load any well-formed `v1.x` artifact" (everything `MUST` above) and "the implementation may offer additional authoring affordances" (the `SHOULD` and `MAY` rows). An implementation that supports only hand-authored and OpenAPI-3.x-ingested artifacts is conforming; an implementation that adds `curl`-paste, OpenAPI-2.0, and LLM-inference support is conforming-with-richer-authoring.

The asymmetry between *load* and *author* matters: a stored `.uacp` artifact's `source.type == "inferred"` field does not require the loading implementation to support inference at the time of load. The `reviewed_at` timestamp is the durable record of the authoring-time review; once the artifact is stored, every loading implementation treats it identically regardless of inference support.

### Load-time MUSTs

A `Conforming Implementation` of `v1.x` MUST satisfy all of the following at artifact load time:

- **MUST** validate the artifact per §3.10. Failure to validate causes rejection, never partial load.
- **MUST** resolve every operation by its `id` per §3.5.
- **MUST** apply the bidirectional path-parameter rule per §3.2.
- **MUST** treat every key in `response` per the rules in §3.3 (exact status, range, or `default`).

### MUST NOT items

The following requirements are normative and apply to every `Conforming Implementation` of `v1.x`:

- A `Conforming Implementation` **MUST NOT** silently persist an LLM-inferred schema without an explicit user-approval step that satisfies the requirements of §3.8.
- A `Conforming Implementation` **MUST NOT** load `.uacp` artifacts that fail JSON Schema validation per §3.10.
- A `Conforming Implementation` **MUST NOT** include credentials, in plaintext or any other form, in any field of any operation. The credential-reference convention of §2.7 is the only path; this rule is the schema-layer restatement of Principle 7 (security by default).
- A `Conforming Implementation` **MUST NOT** resolve remote `$ref`s at dispatch time. All `$ref` resolution is local to the artifact's `definitions` block, performed at parse time. Per Principle 9 (determinism).
- A `Conforming Implementation` **MUST NOT** silently drop fields it does not understand from a `.uacp` artifact. Unknown fields are permitted (forward compatibility) but their presence is preserved on round-trip; an implementation that re-serializes the artifact and writes back to storage MUST round-trip unknown fields verbatim. The round-trip property is what makes the §3.9 source-priority story load-bearing across implementation versions.

The cumulative effect of the MUSTs and MUST NOTs is that a `Conforming Implementation` of `v1.x` reliably loads, validates, and dispatches against any well-formed `v1.x` artifact regardless of the source the artifact came from, and that the inference path's safety property (mandatory user review) is enforced at the moment it matters — at authoring time — rather than deferred into runtime where it would be too late.
