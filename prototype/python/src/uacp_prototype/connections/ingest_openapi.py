"""OpenAPI 3.x and Google-discovery ingestion per §3.6.

Two entry points:

  - ``from_openapi(doc_or_url)`` — canonical OpenAPI 3.0/3.1 document.
    Maps `paths.<p>.<m>` → UACP Operation per §3.6's mapping table.
  - ``from_discovery_doc(doc_or_url)`` — Google's discovery document
    format (https://developers.google.com/discovery/v1/reference). Not
    strict OpenAPI but similar enough that the same mapping logic
    applies after a translation pass.

Both produce a list of ``Operation`` objects ready to be dropped into a
`.uacp` artifact's `operations` array. The caller is responsible for
populating the surrounding `authentication` / `dispatch` blocks; the
ingestion layer focuses on operation surface only per §3.6.

Excluded from ingestion (per §3.6):
  - `security` / `securitySchemes` — Stage 2's territory.
  - `servers` — Stage 4's base URL, owned by the caller.
  - `callbacks` / `webhooks` — out of scope for v1.0 HTTPS-only
    agent-initiated transport.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from ..spec.models import (
    CursorPagination,
    LinkHeaderPagination,
    NoPagination,
    OffsetPagination,
    OpenAPISource,
    Operation,
    Pagination,
    RequestShape,
    ResponseEntry,
)


SAFE_ID_PATTERN = re.compile(r"[^a-z0-9_-]+")


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------


def _load(doc_or_url: str | dict | Path) -> tuple[dict, str]:
    """Resolve `doc_or_url` to a (parsed_dict, source_url) tuple.

    Accepts:
      - dict (already-parsed) → source_url is "<inline>"
      - str URL (https://) → fetch + parse
      - str / Path → read from disk
    """
    if isinstance(doc_or_url, dict):
        return doc_or_url, "<inline>"
    if isinstance(doc_or_url, Path):
        return json.loads(doc_or_url.read_text()), f"file://{doc_or_url.resolve()}"
    if isinstance(doc_or_url, str):
        if doc_or_url.startswith("https://") or doc_or_url.startswith("http://"):
            response = httpx.get(doc_or_url, timeout=30, follow_redirects=True)
            response.raise_for_status()
            return response.json(), doc_or_url
        # treat as path
        return json.loads(Path(doc_or_url).read_text()), f"file://{Path(doc_or_url).resolve()}"
    raise TypeError(f"unsupported doc_or_url type: {type(doc_or_url).__name__}")


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_id(raw: str) -> str:
    """Convert an arbitrary identifier to the §3.1 charset."""
    cleaned = SAFE_ID_PATTERN.sub("_", raw.lower())
    cleaned = cleaned.lstrip("_-")
    if not cleaned:
        cleaned = "op"
    if not cleaned[0].isalpha():
        cleaned = "op_" + cleaned
    return cleaned[:128]


def _idempotency_default(method: str) -> str:
    """Per §3.6: GET/HEAD/OPTIONS/PUT/DELETE → idempotent; POST/PATCH → unknown."""
    if method.upper() in {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}:
        return "idempotent"
    return "unknown"


# ---------------------------------------------------------------------------
# Reference rewriting
# ---------------------------------------------------------------------------


def _rewrite_refs(node: Any, *, prefix_in: str, prefix_out: str) -> Any:
    """Walk the schema and rewrite $ref strings from `prefix_in` to
    `prefix_out`. OpenAPI uses `#/components/schemas/X`; UACP uses
    `#/definitions/X` per §3.6.
    """
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith(prefix_in):
                out[k] = prefix_out + v[len(prefix_in) :]
            else:
                out[k] = _rewrite_refs(v, prefix_in=prefix_in, prefix_out=prefix_out)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(x, prefix_in=prefix_in, prefix_out=prefix_out) for x in node]
    return node


# ---------------------------------------------------------------------------
# Pagination heuristics (§3.6 may infer; flagged for review)
# ---------------------------------------------------------------------------


def _infer_pagination(
    method: str, query_params: dict[str, Any], response_schema: dict[str, Any] | None
) -> Pagination | None:
    """Recognize common pagination patterns from parameter / response shape.

    Heuristics (deliberately conservative — false positives are worse
    than false negatives because callers can always add pagination after
    ingestion):

      - cursor: query has page_token / pageToken / cursor / nextToken
        AND response schema has nextPageToken / next_cursor / etc.
      - offset: query has offset + limit (or page + per_page; not
        registered in v1 but heuristic still recognizes it).
      - link_header: cannot be inferred from schema alone.
      - none: default.

    The dispatch-time loop behavior already covers each pattern via
    pagination.py; the inferred Pagination object plugs into that loop
    directly when accepted by the user.
    """
    if method.upper() != "GET":
        return None
    properties = query_params.get("properties") or {}
    cursor_param_candidates = (
        "page_token",
        "pageToken",
        "cursor",
        "nextToken",
        "next_page_token",
    )
    cursor_field_candidates = (
        "$.nextPageToken",
        "$.next_page_token",
        "$.nextCursor",
        "$.next_cursor",
        "$.cursor",
    )
    for cand in cursor_param_candidates:
        if cand in properties:
            # Check response for the corresponding field
            resp_props = (response_schema or {}).get("properties") or {}
            for resp_name in (
                "nextPageToken",
                "next_page_token",
                "nextCursor",
                "next_cursor",
                "cursor",
            ):
                if resp_name in resp_props:
                    return CursorPagination(
                        pattern="cursor",
                        request_cursor_parameter=cand,
                        response_cursor_path=f"$.{resp_name}",
                    )
            # Param present without matching response field — still
            # infer cursor since the param presence is a strong signal.
            return CursorPagination(
                pattern="cursor",
                request_cursor_parameter=cand,
                response_cursor_path="$.nextPageToken",  # best-guess default
            )

    if "offset" in properties and "limit" in properties:
        return OffsetPagination(
            pattern="offset",
            request_offset_parameter="offset",
            request_limit_parameter="limit",
            response_total_path="$.total",
            response_has_more_path=None,
        )

    return None


# ---------------------------------------------------------------------------
# OpenAPI 3.x → UACP Operation
# ---------------------------------------------------------------------------


def _operation_from_openapi(
    *,
    path: str,
    method: str,
    operation_obj: dict[str, Any],
    components_schemas: dict[str, Any] | None = None,
    source_url: str,
    ingested_at: str,
) -> Operation:
    op_id = operation_obj.get("operationId") or _safe_id(f"{method.lower()}_{path}")
    summary = operation_obj.get("summary") or operation_obj.get("description") or op_id
    description = operation_obj.get("description")
    tags = operation_obj.get("tags") or []

    # Parameters — split by `in`
    path_params_props: dict[str, Any] = {}
    path_params_required: list[str] = []
    query_params_props: dict[str, Any] = {}
    query_params_required: list[str] = []
    header_params_props: dict[str, Any] = {}
    header_params_required: list[str] = []

    for param in operation_obj.get("parameters", []):
        location = param.get("in")
        name = param.get("name")
        if not name:
            continue
        schema = param.get("schema") or {}
        is_required = param.get("required", False) or location == "path"
        if location == "path":
            path_params_props[name] = schema
            if is_required:
                path_params_required.append(name)
        elif location == "query":
            query_params_props[name] = schema
            if is_required:
                query_params_required.append(name)
        elif location == "header":
            # Skip authentication-bearing header parameters per §3.6
            if name.lower() in {"authorization", "x-api-key"}:
                continue
            header_params_props[name] = schema
            if is_required:
                header_params_required.append(name)

    path_parameters = (
        {
            "type": "object",
            "required": path_params_required,
            "properties": path_params_props,
        }
        if path_params_props
        else None
    )
    query_parameters = (
        {
            "type": "object",
            "required": query_params_required,
            "properties": query_params_props,
        }
        if query_params_props
        else None
    )
    headers = (
        {
            "type": "object",
            "required": header_params_required,
            "properties": header_params_props,
        }
        if header_params_props
        else None
    )

    # Request body
    body: Any = None
    request_body = operation_obj.get("requestBody")
    if request_body:
        content = request_body.get("content", {})
        # Take application/json first; fall back to first declared media
        media_type, media_obj = None, None
        if "application/json" in content:
            media_type = "application/json"
            media_obj = content["application/json"]
        elif content:
            media_type = next(iter(content))
            media_obj = content[media_type]
        if media_obj is not None:
            schema = media_obj.get("schema") or {}
            body = {
                "media_type": media_type,
                "schema": _rewrite_refs(
                    schema, prefix_in="#/components/schemas/", prefix_out="#/definitions/"
                ),
            }

    # Responses
    responses: dict[str, ResponseEntry] = {}
    for status_key, resp_obj in operation_obj.get("responses", {}).items():
        # Normalize: OpenAPI uses "200", "default", "4xx" — same as UACP.
        normalized_key = status_key
        resp_description = resp_obj.get("description", "Response")
        resp_body: Any = None
        content = resp_obj.get("content", {})
        if "application/json" in content:
            schema = content["application/json"].get("schema") or {}
            resp_body = {
                "media_type": "application/json",
                "schema": _rewrite_refs(
                    schema, prefix_in="#/components/schemas/", prefix_out="#/definitions/"
                ),
            }
        elif content:
            media_type = next(iter(content))
            schema = content[media_type].get("schema") or {}
            resp_body = {
                "media_type": media_type,
                "schema": _rewrite_refs(
                    schema, prefix_in="#/components/schemas/", prefix_out="#/definitions/"
                ),
            }
        responses[normalized_key] = ResponseEntry(
            description=resp_description,
            body=resp_body,
        )
    # Ensure at least one response
    if not responses:
        responses["default"] = ResponseEntry(description="Response (auto-synthesized)")

    # Pagination heuristic
    pag: Pagination | None = None
    primary_resp_schema: dict[str, Any] | None = None
    if "200" in responses and isinstance(responses["200"].body, dict):
        primary_resp_schema = responses["200"].body.get("schema")
    pag = _infer_pagination(
        method,
        query_parameters or {},
        primary_resp_schema,
    )

    return Operation(
        id=_safe_id(op_id),
        summary=summary if isinstance(summary, str) else op_id,
        description=description if isinstance(description, str) else None,
        tags=[_safe_id(t) for t in tags] if tags else None,
        deprecated=bool(operation_obj.get("deprecated", False)),
        idempotency=_idempotency_default(method),
        request=RequestShape(
            method=method.upper(),
            path=path,
            path_parameters=path_parameters,
            query_parameters=query_parameters,
            headers=headers,
            body=body,
        ),
        response=responses,
        pagination=pag,
        source=OpenAPISource(
            type="openapi",
            url=source_url,
            ingested_at=ingested_at,
        ),
    )


@dataclass(frozen=True)
class IngestionResult:
    """Container for ingestion output.

    Carries the operations plus the suggested base_url and definitions
    block extracted from the source. The caller assembles the final
    artifact by combining these with an authentication block from
    Stage 2.
    """

    operations: list[Operation]
    base_url: str | None
    definitions: dict[str, Any]


def from_openapi(doc_or_url: str | dict | Path) -> IngestionResult:
    """Ingest a canonical OpenAPI 3.0/3.1 document into UACP operations."""
    doc, source_url = _load(doc_or_url)
    ingested_at = _now_rfc3339()

    if "openapi" not in doc:
        raise ValueError(
            "ingest_openapi.from_openapi: document missing required 'openapi' field; "
            "use from_discovery_doc for Google's format."
        )

    base_url: str | None = None
    servers = doc.get("servers")
    if servers and isinstance(servers, list) and servers:
        # Per §3.6, single server pre-populates base_url; multiple → caller chooses.
        base_url = servers[0].get("url") if isinstance(servers[0], dict) else None

    components = doc.get("components") or {}
    components_schemas = components.get("schemas") or {}
    definitions = {
        name: _rewrite_refs(
            schema, prefix_in="#/components/schemas/", prefix_out="#/definitions/"
        )
        for name, schema in components_schemas.items()
    }

    operations: list[Operation] = []
    for path, path_item in (doc.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            if method not in path_item:
                continue
            op_obj = path_item[method]
            if not isinstance(op_obj, dict):
                continue
            op = _operation_from_openapi(
                path=path,
                method=method,
                operation_obj=op_obj,
                components_schemas=components_schemas,
                source_url=source_url,
                ingested_at=ingested_at,
            )
            operations.append(op)

    return IngestionResult(operations=operations, base_url=base_url, definitions=definitions)


# ---------------------------------------------------------------------------
# Google discovery doc → UACP Operation
# ---------------------------------------------------------------------------


def _operation_from_discovery(
    *,
    method_id: str,
    method_obj: dict[str, Any],
    base_url: str,
    schemas: dict[str, Any],
    source_url: str,
    ingested_at: str,
) -> Operation:
    """Convert a Google discovery 'method' object into a UACP Operation.

    Discovery format reference:
    https://developers.google.com/discovery/v1/reference/apis
    """
    http_method = method_obj.get("httpMethod", "GET").upper()
    path = method_obj.get("path", "")
    if not path.startswith("/"):
        path = "/" + path

    # Discovery's path uses {name} which matches RFC 6570 already.

    summary = method_obj.get("description", method_id).split(".")[0] + "."
    description = method_obj.get("description")

    # Parameters: parameterOrder gives required path params (sometimes);
    # parameters dict has all of them with `location` field.
    parameters_dict = method_obj.get("parameters") or {}
    path_props: dict[str, Any] = {}
    path_required: list[str] = []
    query_props: dict[str, Any] = {}
    query_required: list[str] = []

    for name, param in parameters_dict.items():
        location = param.get("location", "query")
        is_required = param.get("required", False)
        prop_schema = _discovery_param_schema(param)
        if location == "path":
            path_props[name] = prop_schema
            if is_required or name in (method_obj.get("parameterOrder") or []):
                if name not in path_required:
                    path_required.append(name)
        elif location == "query":
            query_props[name] = prop_schema
            if is_required:
                query_required.append(name)

    path_parameters = (
        {"type": "object", "required": path_required, "properties": path_props}
        if path_props
        else None
    )
    query_parameters = (
        {"type": "object", "required": query_required, "properties": query_props}
        if query_props
        else None
    )

    # Request body: discovery's `request: {$ref: "Schema"}`
    body: Any = None
    request_obj = method_obj.get("request")
    if isinstance(request_obj, dict) and "$ref" in request_obj:
        ref = request_obj["$ref"]
        body = {
            "media_type": "application/json",
            "schema": {"$ref": f"#/definitions/{ref}"},
        }

    # Response: discovery's `response: {$ref: "Schema"}`
    responses: dict[str, ResponseEntry] = {}
    response_obj = method_obj.get("response")
    if isinstance(response_obj, dict) and "$ref" in response_obj:
        ref = response_obj["$ref"]
        responses["200"] = ResponseEntry(
            description="Successful response.",
            body={
                "media_type": "application/json",
                "schema": {"$ref": f"#/definitions/{ref}"},
            },
        )
    else:
        responses["200"] = ResponseEntry(description="Successful response.")

    # Default error response — Google APIs return JSON error envelopes
    responses["default"] = ResponseEntry(
        description="Error response (Google standard error envelope).",
        body={
            "media_type": "application/json",
            "schema": {
                "type": "object",
                "properties": {
                    "error": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "message": {"type": "string"},
                            "status": {"type": "string"},
                        },
                    }
                },
            },
        },
    )

    # Pagination inference for discovery — Google APIs use pageToken/nextPageToken
    pag: Pagination | None = None
    if http_method == "GET" and "pageToken" in query_props:
        pag = CursorPagination(
            pattern="cursor",
            request_cursor_parameter="pageToken",
            response_cursor_path="$.nextPageToken",
        )

    # Google's discovery `id` field is the canonical API-prefixed name
    # (e.g. "gmail.users.messages.send"); prefer it over the walked path
    # so operation ids include the service prefix.
    canonical_id = method_obj.get("id") or method_id
    op_id = canonical_id.replace(".", "_")
    return Operation(
        id=_safe_id(op_id),
        summary=summary,
        description=description,
        tags=None,
        deprecated=method_obj.get("deprecated", False),
        idempotency=_idempotency_default(http_method),
        request=RequestShape(
            method=http_method,
            path=path,
            path_parameters=path_parameters,
            query_parameters=query_parameters,
            body=body,
        ),
        response=responses,
        pagination=pag,
        source=OpenAPISource(
            type="openapi",
            url=source_url,
            ingested_at=ingested_at,
        ),
    )


def _discovery_param_schema(param: dict[str, Any]) -> dict[str, Any]:
    """Convert a Google discovery parameter object to JSON Schema."""
    out: dict[str, Any] = {}
    if "type" in param:
        out["type"] = param["type"]
    if "format" in param:
        out["format"] = param["format"]
    if "description" in param:
        out["description"] = param["description"]
    if "enum" in param:
        out["enum"] = param["enum"]
    if "default" in param:
        out["default"] = param["default"]
    if "minimum" in param:
        out["minimum"] = param["minimum"]
    if "maximum" in param:
        out["maximum"] = param["maximum"]
    if "pattern" in param:
        out["pattern"] = param["pattern"]
    return out


def _walk_discovery_methods(
    resources: dict[str, Any], prefix: str = ""
) -> Iterable[tuple[str, dict[str, Any]]]:
    """Recursively yield (method_id, method_obj) tuples from a discovery
    document's `resources` tree.
    """
    for resource_name, resource_obj in resources.items():
        new_prefix = f"{prefix}.{resource_name}" if prefix else resource_name
        methods = resource_obj.get("methods", {}) or {}
        for method_name, method_obj in methods.items():
            yield f"{new_prefix}.{method_name}", method_obj
        sub_resources = resource_obj.get("resources") or {}
        if sub_resources:
            yield from _walk_discovery_methods(sub_resources, new_prefix)


def from_discovery_doc(doc_or_url: str | dict | Path) -> IngestionResult:
    """Ingest a Google discovery document into UACP operations."""
    doc, source_url = _load(doc_or_url)
    ingested_at = _now_rfc3339()

    if doc.get("kind") != "discovery#restDescription":
        raise ValueError(
            "ingest_openapi.from_discovery_doc: document is not a Google "
            "discovery document (missing kind: 'discovery#restDescription'); "
            "use from_openapi for canonical OpenAPI 3.x."
        )

    # baseUrl / basePath / rootUrl define the base URL.
    root_url = doc.get("rootUrl", "")
    service_path = doc.get("servicePath", "")
    base_url = (root_url + service_path).rstrip("/")
    if not base_url.startswith("https://"):
        base_url = "https://" + base_url.lstrip("/")

    schemas = doc.get("schemas") or {}
    definitions = {
        name: _rewrite_discovery_schema_refs(schema)
        for name, schema in schemas.items()
    }

    operations: list[Operation] = []

    # Top-level methods
    top_methods = doc.get("methods") or {}
    for name, method_obj in top_methods.items():
        operations.append(
            _operation_from_discovery(
                method_id=name,
                method_obj=method_obj,
                base_url=base_url,
                schemas=schemas,
                source_url=source_url,
                ingested_at=ingested_at,
            )
        )

    # Resource-tree methods
    resources = doc.get("resources") or {}
    for method_id, method_obj in _walk_discovery_methods(resources):
        operations.append(
            _operation_from_discovery(
                method_id=method_id,
                method_obj=method_obj,
                base_url=base_url,
                schemas=schemas,
                source_url=source_url,
                ingested_at=ingested_at,
            )
        )

    return IngestionResult(operations=operations, base_url=base_url, definitions=definitions)


def _rewrite_discovery_schema_refs(schema: Any) -> Any:
    """Discovery references schemas by bare name (`$ref: "User"`), unlike
    OpenAPI's `#/components/schemas/User`. Rewrite to `#/definitions/User`.
    """
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k == "$ref" and isinstance(v, str) and not v.startswith("#"):
                out[k] = f"#/definitions/{v}"
            else:
                out[k] = _rewrite_discovery_schema_refs(v)
        return out
    if isinstance(schema, list):
        return [_rewrite_discovery_schema_refs(x) for x in schema]
    return schema


__all__ = [
    "IngestionResult",
    "from_discovery_doc",
    "from_openapi",
]
