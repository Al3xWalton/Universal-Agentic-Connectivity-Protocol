"""Pydantic models mirroring the canonical UACP `v1.x` operation schema.

Implements §3.1 (canonical operation schema), §3.2 (request shape), §3.3
(response shape), §3.4 (pagination metadata), and §3.5 (operation
references and discovery). The pydantic layer enforces the structural
rules; semantic rules (bidirectional path-parameter, embedded-credential
detection, $ref-local-only) are checked by `loader.validate_artifact`
after pydantic parsing.

The canonical placeholder $schema URL until Stage 9 freeze is
``https://uacp.spec/v1/schema.json``. Artifacts pinning this URL today
are re-pinnable at freeze without semantic change.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


# Stage 9 placeholder per §3.10 / §7.1. Not the canonical URL.
DEFAULT_SCHEMA_URL = "https://uacp.spec/v1/schema.json"

# §3.1 charset for operation `id` and tag values.
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,127}$")

# §3.2 permitted HTTP methods. Other methods are rejected at validation.
PERMITTED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})

# §3.3 valid response keys: exact 3-digit status, status range, or "default".
STATUS_RANGE_PATTERN = re.compile(r"^[1-5]xx$")
EXACT_STATUS_PATTERN = re.compile(r"^[1-5][0-9]{2}$")

# §3.4 registered pagination patterns in v1.0.
PaginationPattern = Literal["cursor", "offset", "link_header", "none"]

# §6.2 registered secret-store types for v1.0.
REGISTERED_SECRET_STORES = frozenset({"vault", "aws-secrets-manager", "local-keyring", "inline-encrypted"})


IdString = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,127}$", min_length=1)]


# ---------------------------------------------------------------------------
# Request and response shapes (§3.2, §3.3)
# ---------------------------------------------------------------------------


class StrictModel(BaseModel):
    """Base for every UACP model. Forbids unknown fields at the spec layer.

    Per §3.11's "MUST NOT silently drop fields it does not understand": a
    `Conforming Implementation` round-trips unknown fields verbatim. We use
    `extra="allow"` so unknown fields are preserved on round-trip; the
    validation layer in `schema.py` handles the strict-on-known surface.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class RequestShape(StrictModel):
    method: str
    path: str
    path_parameters: dict[str, Any] | None = None
    query_parameters: dict[str, Any] | None = None
    headers: dict[str, Any] | None = None
    # `body` is one of the literal "none" string, an inline {media_type, schema}
    # object, or a {$ref: "#/definitions/..."} object. Pydantic stores it as
    # the parsed structure and the validator below enforces the discriminator.
    body: str | dict[str, Any] | None = None

    @field_validator("method")
    @classmethod
    def _method_is_uppercase_permitted(cls, v: str) -> str:
        if v not in PERMITTED_METHODS:
            raise ValueError(
                f"method must be uppercase HTTP verb in {sorted(PERMITTED_METHODS)}, got {v!r}"
            )
        return v

    @field_validator("path")
    @classmethod
    def _path_no_query(cls, v: str) -> str:
        if "?" in v:
            raise ValueError(
                "request.path must not contain a query string; declare query_parameters separately"
            )
        return v

    @field_validator("body")
    @classmethod
    def _body_shape(cls, v: str | dict[str, Any] | None) -> str | dict[str, Any] | None:
        if v is None:
            return v
        if isinstance(v, str):
            if v != "none":
                raise ValueError("body string form must be the literal 'none'")
            return v
        if isinstance(v, dict):
            if "$ref" in v:
                if not isinstance(v["$ref"], str) or not v["$ref"].startswith("#/definitions/"):
                    raise ValueError("body $ref must be a local pointer of the form '#/definitions/<name>'")
                return v
            if "schema" in v:
                # inline form; media_type defaults applied in loader/validation
                return v
            raise ValueError("body object must be either {$ref: '#/definitions/...'} or {media_type, schema}")
        raise ValueError("body must be 'none', an inline object, or a $ref object")


class FailurePredicate(StrictModel):
    """Per §3.3 failure predicate — body-shape signal that distinguishes a
    logical failure from a logical success on a 2xx response.

    Permits providers whose API surface wraps both success and failure in
    the same HTTP status (typically 200 with {ok: false, error: "..."}).
    The predicate's `path` is a JSONPath expression in the same minimal
    subset as §3.4 (`$.field` and `$.field.subfield`); `equals` is the
    JSON literal that, when matching the resolved value, indicates
    failure. Optional `code_path` extracts a provider-specific error
    string from the body for inclusion in the canonical error's
    `details` per §4.6.
    """

    path: str
    equals: Any
    code_path: str | None = None
    message_path: str | None = None

    @field_validator("path", "code_path", "message_path")
    @classmethod
    def _jsonpath_subset(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.startswith("$."):
            raise ValueError(f"failure_predicate path must start with $.; got {v!r}")
        return v


class ResponseEntry(StrictModel):
    description: str
    body: str | dict[str, Any] | None = None
    headers: dict[str, Any] | None = None
    streaming: bool = False
    failure_predicate: FailurePredicate | None = None

    @field_validator("body")
    @classmethod
    def _body_shape(cls, v: str | dict[str, Any] | None) -> str | dict[str, Any] | None:
        if v is None:
            return v
        if isinstance(v, str):
            if v != "none":
                raise ValueError("response body string form must be the literal 'none'")
            return v
        if isinstance(v, dict):
            if "$ref" in v:
                if not isinstance(v["$ref"], str) or not v["$ref"].startswith("#/definitions/"):
                    raise ValueError("response body $ref must be a local pointer of the form '#/definitions/<name>'")
                return v
            if "schema" in v or "format" in v:
                # Inline form with `media_type` + `schema` (JSON shape) OR
                # with `format` discriminator (xml / binary / text /
                # json) per the §3.3 Stage 8c amendment. `format` makes
                # `schema` optional for non-JSON bodies — XML schemas
                # describe the post-parse dict; binary and text bodies
                # are opaque to validation.
                fmt = v.get("format")
                if fmt is not None and fmt not in {"json", "xml", "binary", "text"}:
                    raise ValueError(
                        f"response body format must be one of "
                        f"{{'json', 'xml', 'binary', 'text'}}; got {fmt!r}"
                    )
                return v
            raise ValueError("response body object must be either {$ref: '#/definitions/...'} or {media_type, schema} or {media_type, format[, schema]}")
        raise ValueError("response body must be 'none', an inline object, or a $ref object")


class CursorPagination(StrictModel):
    pattern: Literal["cursor"]
    request_cursor_parameter: str
    response_cursor_path: str


class OffsetPagination(StrictModel):
    pattern: Literal["offset"]
    request_offset_parameter: str
    request_limit_parameter: str
    response_total_path: str | None = None
    response_has_more_path: str | None = None

    @model_validator(mode="after")
    def _has_one_terminator(self) -> "OffsetPagination":
        if self.response_total_path is None and self.response_has_more_path is None:
            raise ValueError(
                "offset pagination requires either response_total_path or response_has_more_path"
            )
        return self


class LinkHeaderPagination(StrictModel):
    pattern: Literal["link_header"]


class NoPagination(StrictModel):
    pattern: Literal["none"]


Pagination = CursorPagination | OffsetPagination | LinkHeaderPagination | NoPagination


# ---------------------------------------------------------------------------
# Source (provenance) — §3.6 / §3.7 / §3.8
# ---------------------------------------------------------------------------


class OpenAPISource(StrictModel):
    type: Literal["openapi"]
    url: str
    ingested_at: str


class CurlSource(StrictModel):
    type: Literal["curl"]
    captured_at: str
    raw: str | None = None  # the raw curl invocation, for audit; MAY be stripped


class InferredSource(StrictModel):
    type: Literal["inferred"]
    model: str
    description: str
    confidence: Literal["low", "medium", "high"] | None = None
    reviewed_at: str

    @field_validator("model", "description", "reviewed_at")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("inferred source field must be non-empty")
        return v


SourceEntry = OpenAPISource | CurlSource | InferredSource


# ---------------------------------------------------------------------------
# Operation (§3.1)
# ---------------------------------------------------------------------------


class RetryOverride(StrictModel):
    max_attempts: int | None = Field(default=None, ge=1)
    initial_delay_ms: int | None = Field(default=None, ge=0)
    max_delay_ms: int | None = Field(default=None, ge=0)
    jitter: float | None = Field(default=None, ge=0, le=1)
    multiplier: float | None = Field(default=None, gt=0)


class Operation(StrictModel):
    id: IdString
    summary: str = Field(min_length=1)
    description: str | None = None
    tags: list[IdString] | None = None
    deprecated: bool = False
    idempotency: Literal["idempotent", "not_idempotent", "unknown"] = "unknown"
    request: RequestShape
    response: dict[str, ResponseEntry]
    pagination: Pagination | None = None
    source: SourceEntry | None = None
    timeout_ms: int | None = Field(default=None, gt=0)
    retry: RetryOverride | None = None

    @field_validator("response")
    @classmethod
    def _response_keys_valid(cls, v: dict[str, ResponseEntry]) -> dict[str, ResponseEntry]:
        if not v:
            raise ValueError("operation must declare at least one response entry")
        for key in v:
            if key == "default":
                continue
            if STATUS_RANGE_PATTERN.match(key):
                continue
            if EXACT_STATUS_PATTERN.match(key):
                continue
            raise ValueError(
                f"response key {key!r} is not a valid 3-digit status, status range "
                f"(1xx-5xx), or 'default'"
            )
        return v


# ---------------------------------------------------------------------------
# Top-level dispatch and authentication blocks
# ---------------------------------------------------------------------------


class DispatchConfig(StrictModel):
    base_url: str
    default_headers: dict[str, str] = Field(default_factory=dict)
    default_timeout_ms: int = Field(default=30000, gt=0)
    default_user_agent: str | None = None
    idempotency_key_header: str = "Idempotency-Key"
    allow_method_changing_redirects: bool = False

    @field_validator("base_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("base_url must be HTTPS per Principle 11; got " + v)
        return v


class AuthenticationBlock(StrictModel):
    """Top-level authentication block.

    The `method` field discriminates the registered method per §2.1; the
    remaining fields are method-specific. The pydantic layer keeps `extra`
    permissive so method-specific fields parse cleanly; per-method shape
    validation is the responsibility of the auth/<method>.py module.
    """

    method: str

    @field_validator("method")
    @classmethod
    def _registered_or_x_namespaced(cls, v: str) -> str:
        registered = {
            "oauth2_authorization_code",
            "oauth2_client_credentials",
            "oauth2_device_code",
            "oauth1a",
            "api_key_header",
            "api_key_query",
            "aws_sigv4",
            "hmac_signature",
            "custom_auth",
        }
        if v in registered:
            return v
        if v.startswith("x-"):
            return v
        raise ValueError(
            f"authentication.method {v!r} is neither in the v1.0 registered set "
            f"nor x-namespaced for in-development extension (per §7.3)"
        )


class EncryptedSecret(StrictModel):
    ciphertext: str
    algorithm: Literal["AES-256-GCM"] = "AES-256-GCM"
    key_ref: str
    iv: str
    tag: str

    @field_validator("key_ref")
    @classmethod
    def _key_ref_not_inline(cls, v: str) -> str:
        # §6.2 forbids recursive inline-encrypted resolution.
        if v.startswith("secret://inline-encrypted/"):
            raise ValueError(
                "encrypted_secrets.key_ref MUST NOT point at another inline-encrypted secret "
                "(recursion forbidden per §6.2)"
            )
        if not v.startswith("secret://"):
            raise ValueError("encrypted_secrets.key_ref must be a secret:// URI")
        return v


class UACPArtifact(StrictModel):
    schema_url: str = Field(alias="$schema", default=DEFAULT_SCHEMA_URL)
    authentication: AuthenticationBlock
    dispatch: DispatchConfig
    operations: list[Operation]
    definitions: dict[str, Any] = Field(default_factory=dict)
    encrypted_secrets: dict[str, EncryptedSecret] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @model_validator(mode="after")
    def _unique_operation_ids(self) -> "UACPArtifact":
        seen: set[str] = set()
        for idx, op in enumerate(self.operations):
            if op.id in seen:
                raise ValueError(
                    f"operations[{idx}].id {op.id!r} duplicates an earlier operation; "
                    f"ids MUST be unique within a `.uacp` artifact (§3.5, §3.10)"
                )
            seen.add(op.id)
        return self


__all__ = [
    "DEFAULT_SCHEMA_URL",
    "ID_PATTERN",
    "PERMITTED_METHODS",
    "REGISTERED_SECRET_STORES",
    "AuthenticationBlock",
    "CursorPagination",
    "CurlSource",
    "DispatchConfig",
    "EncryptedSecret",
    "FailurePredicate",
    "InferredSource",
    "LinkHeaderPagination",
    "NoPagination",
    "OffsetPagination",
    "OpenAPISource",
    "Operation",
    "Pagination",
    "RequestShape",
    "ResponseEntry",
    "RetryOverride",
    "SourceEntry",
    "UACPArtifact",
]
