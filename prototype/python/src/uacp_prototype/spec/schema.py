"""Validation rules per §3.10.

The pydantic layer in `models.py` enforces the structural rules. This module
adds the cross-cutting semantic validations:

  - Bidirectional path-parameter rule (§3.2): every {name} in `request.path`
    is a property of `request.path_parameters`, and every property of
    `request.path_parameters` appears at least once in `request.path`.
  - Pagination cross-references (§3.4): the named cursor / offset / limit
    parameter MUST exist in `request.query_parameters` (or `request.body` for
    body-bound cursors).
  - No embedded credentials anywhere (§3.10, §6.5): every credential-shaped
    field (anything ending in `_ref`, `_secret`, `_token`, or `_key` whose
    value is a string) MUST either be a `secret://` URI or be flagged as a
    spec violation.
  - Inferred-operation provenance complete (§3.8, §3.10): operations with
    `source.type == "inferred"` MUST carry non-empty `model`, `description`,
    `reviewed_at`.
  - Local `$ref` resolution (§3.10): every `$ref` resolves to a JSON Pointer
    against the artifact's own `definitions` block; remote `$ref`s are
    forbidden.

`validate_artifact` is the entry point. It accepts a parsed `UACPArtifact`
and a raw dict (for fields pydantic stripped or that need original-form
inspection) and raises `SpecValidationError` on the first failure with a
clear message identifying which operation / which field.
"""

from __future__ import annotations

import re
from typing import Any

from .models import UACPArtifact

# Field-name suffixes whose values must be `secret://` URIs (per §6.5 audit hook).
CREDENTIAL_SUFFIXES = ("_ref", "_secret", "_token", "_key")

# Field-name allowlist of credential-shaped fields whose values are NOT credentials.
# `client_id` is identifier-shaped per §2.2.1; `consumer_key` is the OAuth 1.0a
# client identifier per §2.3 (paired with `consumer_secret_ref`).
CREDENTIAL_FIELD_ALLOWLIST = frozenset(
    {
        "client_id",
        "consumer_key",
        # the access-key id is identifier-shaped per §2.5.1; the *_ref form holds
        # the reference to the actual secret. The plain "access_key" name without
        # the _ref suffix is rejected; only access_key_ref is permitted.
    }
)

PATH_PARAM_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class SpecValidationError(ValueError):
    """Raised when a `.uacp` artifact violates §3.10 validation rules."""


def _path_template_params(path: str) -> set[str]:
    return set(PATH_PARAM_PATTERN.findall(path))


def _validate_bidirectional_path_rule(artifact: UACPArtifact) -> None:
    for idx, op in enumerate(artifact.operations):
        path_params = _path_template_params(op.request.path)
        declared_params = set((op.request.path_parameters or {}).get("properties", {}).keys())

        missing_in_declaration = path_params - declared_params
        if missing_in_declaration:
            raise SpecValidationError(
                f"operations[{idx}].id={op.id!r}: path template references "
                f"{sorted(missing_in_declaration)} but they are not declared in "
                f"path_parameters (§3.2 bidirectional rule)"
            )

        missing_in_path = declared_params - path_params
        if missing_in_path:
            raise SpecValidationError(
                f"operations[{idx}].id={op.id!r}: path_parameters declares "
                f"{sorted(missing_in_path)} but they do not appear in path "
                f"(§3.2 bidirectional rule)"
            )

        # If path has parameters, path_parameters MUST be present.
        if path_params and not op.request.path_parameters:
            raise SpecValidationError(
                f"operations[{idx}].id={op.id!r}: path contains template parameters "
                f"{sorted(path_params)} but path_parameters is absent"
            )


def _query_param_names(op_request: Any) -> set[str]:
    if op_request.query_parameters is None:
        return set()
    properties = op_request.query_parameters.get("properties") or {}
    return set(properties.keys())


def _body_param_names(op_request: Any) -> set[str]:
    if op_request.body is None or isinstance(op_request.body, str):
        return set()
    inline_schema = op_request.body.get("schema") if isinstance(op_request.body, dict) else None
    if inline_schema is None:
        return set()
    properties = inline_schema.get("properties") or {}
    return set(properties.keys())


def _validate_pagination_cross_references(artifact: UACPArtifact) -> None:
    for idx, op in enumerate(artifact.operations):
        pag = op.pagination
        if pag is None:
            continue
        if pag.pattern == "cursor":
            param = pag.request_cursor_parameter  # type: ignore[union-attr]
            available = _query_param_names(op.request) | _body_param_names(op.request)
            if param not in available:
                raise SpecValidationError(
                    f"operations[{idx}].id={op.id!r}: pagination.request_cursor_parameter "
                    f"{param!r} is not a property of request.query_parameters or request.body "
                    f"(§3.4 cross-reference rule)"
                )
        elif pag.pattern == "offset":
            offset_param = pag.request_offset_parameter  # type: ignore[union-attr]
            limit_param = pag.request_limit_parameter  # type: ignore[union-attr]
            available = _query_param_names(op.request)
            for name in (offset_param, limit_param):
                if name not in available:
                    raise SpecValidationError(
                        f"operations[{idx}].id={op.id!r}: pagination parameter {name!r} "
                        f"is not a property of request.query_parameters "
                        f"(§3.4 cross-reference rule)"
                    )


def _walk_strings(node: Any, path: list[str | int]) -> list[tuple[list[str | int], str, str]]:
    """Yield (path, key, value) triples for every string-valued entry whose
    parent dict key matches a credential-shaped name.
    """
    out: list[tuple[list[str | int], str, str]] = []

    def walk(node: Any, current: list[str | int]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                key_path = current + [k]
                if isinstance(v, str):
                    out.append((key_path, k, v))
                else:
                    walk(v, key_path)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, current + [i])

    walk(node, path)
    return out


def _validate_no_embedded_credentials(raw: dict[str, Any]) -> None:
    """Walk the raw artifact and reject literal credentials in any field whose
    name implies it carries one.

    The rules:
      - Field whose key is exactly `password`, `secret`, `private_key`,
        `consumer_secret`, `client_secret`, `key`, or whose key ends in any
        of CREDENTIAL_SUFFIXES other than `_ref`: rejected unless the value
        is a `secret://` URI (defense-in-depth) or the key is allowlisted.
      - Field whose key ends in `_ref`: must be a `secret://` URI.

    The *_ref suffix is the audit hook from §2.7. A field named, e.g.,
    `client_secret` (no `_ref` suffix) carrying any string is the leak
    pattern this check defends against.
    """
    plain_credential_keys = {
        "password",
        "secret",
        "private_key",
        "consumer_secret",
        "client_secret",
        "access_key",  # plain form; only access_key_ref is permitted per §2.5.1
        "secret_key",  # plain form
        "session_token",
        "consumer_key_secret",
    }

    triples = _walk_strings(raw, [])
    for path, key, value in triples:
        if key in CREDENTIAL_FIELD_ALLOWLIST:
            continue

        if key in plain_credential_keys:
            raise SpecValidationError(
                f"field {'/'.join(str(p) for p in path)} has key {key!r} which "
                f"is a credential-bearing name; embedded plaintext credentials "
                f"are forbidden (§3.10, §6.5). Move the credential to a "
                f"`secret://` URI under a `{key}_ref` field instead."
            )

        if key.endswith("_ref"):
            if not value.startswith("secret://"):
                raise SpecValidationError(
                    f"field {'/'.join(str(p) for p in path)} has *_ref name {key!r} "
                    f"but value is not a `secret://` URI (§2.7, §3.10). Got: {value!r}"
                )


def _walk_refs(node: Any, path: list[str | int]) -> list[tuple[list[str | int], str]]:
    """Yield (path, ref_value) for every `$ref` entry in the artifact."""
    out: list[tuple[list[str | int], str]] = []

    def walk(node: Any, current: list[str | int]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    out.append((current + [k], v))
                walk(v, current + [k])
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, current + [i])

    walk(node, path)
    return out


def _validate_local_refs(raw: dict[str, Any]) -> None:
    refs = _walk_refs(raw, [])
    definitions = raw.get("definitions", {})
    for path, ref in refs:
        if not ref.startswith("#/definitions/"):
            raise SpecValidationError(
                f"$ref at {'/'.join(str(p) for p in path)} is not local "
                f"(§3.10 forbids remote $refs at the spec layer): {ref!r}"
            )
        ref_name = ref[len("#/definitions/") :]
        if ref_name not in definitions:
            raise SpecValidationError(
                f"$ref at {'/'.join(str(p) for p in path)} resolves to "
                f"#/definitions/{ref_name} but that key is not present in the "
                f"artifact's `definitions` block"
            )


def _validate_inferred_provenance(artifact: UACPArtifact) -> None:
    """The pydantic InferredSource model already enforces non-empty model /
    description / reviewed_at via field validators. This pass adds a final
    check that surfaces a uniform error message for the spec validation
    layer.
    """
    for idx, op in enumerate(artifact.operations):
        if op.source is None:
            continue
        if op.source.type != "inferred":
            continue
        # InferredSource model already enforces required fields; double-check
        # to surface a uniform spec-validation error.
        if not op.source.model or not op.source.description or not op.source.reviewed_at:
            raise SpecValidationError(
                f"operations[{idx}].id={op.id!r}: inferred source provenance "
                f"incomplete (§3.8 / §3.10)"
            )


def _validate_capture_provenance(artifact: UACPArtifact) -> None:
    """§3.12 (added in v1.1) — capture-sourced operations carry the
    same mandatory-user-review gate as §3.8 inferred operations:
    persistence is rejected when ``reviewed_at`` is missing or empty.
    The pydantic CaptureSource model enforces non-empty values via
    field validators; this pass surfaces a uniform spec-validation
    error mirroring the §3.8 path."""
    for idx, op in enumerate(artifact.operations):
        if op.source is None:
            continue
        if op.source.type != "capture":
            continue
        missing = [
            f
            for f in ("captured_at", "user_intent", "capture_ref", "reviewed_at")
            if not getattr(op.source, f, "")
        ]
        if missing:
            raise SpecValidationError(
                f"operations[{idx}].id={op.id!r}: capture source provenance "
                f"missing fields {missing} (§3.12 / §3.10). Capture-sourced "
                f"operations MUST NOT be persisted without explicit user "
                f"review (reviewed_at)."
            )


def validate_artifact(artifact: UACPArtifact, raw: dict[str, Any]) -> None:
    """Run §3.10 validation on a parsed artifact + its raw dict form.

    Raises SpecValidationError on the first failure.
    """
    _validate_bidirectional_path_rule(artifact)
    _validate_pagination_cross_references(artifact)
    _validate_no_embedded_credentials(raw)
    _validate_local_refs(raw)
    _validate_inferred_provenance(artifact)
    _validate_capture_provenance(artifact)


__all__ = ["SpecValidationError", "validate_artifact"]
