"""Tests for the spec layer: pydantic models + JSON Schema validation.

Exercises §3.1 / §3.2 / §3.3 / §3.4 / §3.10 against the canonical Operation
form. Each test is named for the rule it exercises.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uacp_prototype.spec.loader import load, load_dict
from uacp_prototype.spec.models import UACPArtifact
from uacp_prototype.spec.schema import SpecValidationError


def _minimal_artifact() -> dict[str, object]:
    """A small artifact that satisfies every spec rule. Tests mutate copies of
    this dict to exercise individual failure paths.
    """
    return {
        "$schema": "https://uacp.spec/v1/schema.json",
        "authentication": {
            "method": "oauth2_authorization_code",
            "authorization_endpoint": "https://example.com/oauth2/authorize",
            "token_endpoint": "https://example.com/oauth2/token",
            "client_id": "app-1",
            "client_secret_ref": "secret://vault/example/client_secret",
            "scopes": ["read"],
            "redirect_uri": "https://broker.example/cb",
        },
        "dispatch": {
            "base_url": "https://api.example.com",
        },
        "operations": [
            {
                "id": "send_message",
                "summary": "Send a message.",
                "request": {
                    "method": "POST",
                    "path": "/v1/messages",
                    "body": {
                        "media_type": "application/json",
                        "schema": {
                            "type": "object",
                            "required": ["text"],
                            "properties": {"text": {"type": "string"}},
                        },
                    },
                },
                "response": {
                    "200": {"description": "ok", "body": "none"},
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_minimal_artifact_loads_clean() -> None:
    art = load_dict(_minimal_artifact())
    assert isinstance(art, UACPArtifact)
    assert len(art.operations) == 1
    assert art.operations[0].id == "send_message"


def test_load_from_disk_round_trips(tmp_path: Path) -> None:
    p = tmp_path / "test.uacp"
    p.write_text(json.dumps(_minimal_artifact()))
    art = load(p)
    assert art.operations[0].request.method == "POST"


def test_default_schema_url_is_placeholder() -> None:
    raw = _minimal_artifact()
    raw.pop("$schema")
    art = load_dict(raw)
    assert art.schema_url == "https://uacp.spec/v1/schema.json"


# ---------------------------------------------------------------------------
# Structural validation (§3.1, §3.2, §3.3)
# ---------------------------------------------------------------------------


def test_id_charset_rejects_uppercase() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["id"] = "SendMessage"
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_id_charset_rejects_dot() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["id"] = "send.message"
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_summary_required() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["summary"] = ""
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_method_must_be_uppercase_permitted() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["method"] = "post"
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_method_TRACE_is_rejected() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["method"] = "TRACE"
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_path_with_query_string_rejected() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["path"] = "/v1/messages?foo=bar"
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_response_key_invalid_rejected() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["response"] = {"banana": {"description": "x"}}
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_response_status_range_accepted() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["response"]["4xx"] = {"description": "client error"}
    art = load_dict(raw)
    assert "4xx" in art.operations[0].response


def test_response_default_accepted() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["response"]["default"] = {"description": "fallback"}
    art = load_dict(raw)
    assert "default" in art.operations[0].response


def test_response_required_at_least_one() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["response"] = {}
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_body_string_must_be_none() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["body"] = "wat"
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_body_object_must_have_schema_or_ref() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["body"] = {"foo": "bar"}
    with pytest.raises(SpecValidationError):
        load_dict(raw)


# ---------------------------------------------------------------------------
# Bidirectional path-parameter rule (§3.2)
# ---------------------------------------------------------------------------


def test_path_param_in_path_but_not_declared_rejected() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["path"] = "/v1/messages/{message_id}"
    with pytest.raises(SpecValidationError, match="bidirectional"):
        load_dict(raw)


def test_path_param_declared_but_not_in_path_rejected() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["path_parameters"] = {
        "type": "object",
        "required": ["message_id"],
        "properties": {"message_id": {"type": "string"}},
    }
    with pytest.raises(SpecValidationError, match="bidirectional"):
        load_dict(raw)


def test_path_param_balanced_accepted() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["path"] = "/v1/messages/{message_id}"
    raw["operations"][0]["request"]["path_parameters"] = {
        "type": "object",
        "required": ["message_id"],
        "properties": {"message_id": {"type": "string"}},
    }
    art = load_dict(raw)
    assert art.operations[0].request.path == "/v1/messages/{message_id}"


# ---------------------------------------------------------------------------
# Operation id uniqueness (§3.5, §3.10)
# ---------------------------------------------------------------------------


def test_duplicate_operation_id_rejected() -> None:
    raw = _minimal_artifact()
    raw["operations"].append(dict(raw["operations"][0]))
    with pytest.raises(SpecValidationError, match="duplicates"):
        load_dict(raw)


# ---------------------------------------------------------------------------
# Pagination (§3.4)
# ---------------------------------------------------------------------------


def test_cursor_pagination_cross_reference() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["method"] = "GET"
    raw["operations"][0]["request"]["body"] = "none"
    raw["operations"][0]["request"]["query_parameters"] = {
        "type": "object",
        "properties": {"page_token": {"type": "string"}},
    }
    raw["operations"][0]["pagination"] = {
        "pattern": "cursor",
        "request_cursor_parameter": "page_token",
        "response_cursor_path": "$.nextPageToken",
    }
    art = load_dict(raw)
    assert art.operations[0].pagination is not None
    assert art.operations[0].pagination.pattern == "cursor"


def test_cursor_pagination_missing_param_rejected() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["method"] = "GET"
    raw["operations"][0]["request"]["body"] = "none"
    raw["operations"][0]["pagination"] = {
        "pattern": "cursor",
        "request_cursor_parameter": "page_token",
        "response_cursor_path": "$.nextPageToken",
    }
    with pytest.raises(SpecValidationError, match="cross-reference"):
        load_dict(raw)


def test_offset_pagination_requires_terminator() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["method"] = "GET"
    raw["operations"][0]["request"]["body"] = "none"
    raw["operations"][0]["request"]["query_parameters"] = {
        "type": "object",
        "properties": {"offset": {"type": "integer"}, "limit": {"type": "integer"}},
    }
    raw["operations"][0]["pagination"] = {
        "pattern": "offset",
        "request_offset_parameter": "offset",
        "request_limit_parameter": "limit",
    }
    with pytest.raises(SpecValidationError):
        load_dict(raw)


# ---------------------------------------------------------------------------
# Embedded credentials (§3.10, §6.5)
# ---------------------------------------------------------------------------


def test_embedded_client_secret_rejected() -> None:
    raw = _minimal_artifact()
    raw["authentication"]["client_secret"] = "literal-secret-value"
    with pytest.raises(SpecValidationError, match="credential-bearing"):
        load_dict(raw)


def test_ref_field_must_be_secret_uri() -> None:
    raw = _minimal_artifact()
    raw["authentication"]["client_secret_ref"] = "literal-secret-value"
    with pytest.raises(SpecValidationError, match="secret://"):
        load_dict(raw)


def test_ref_field_with_valid_secret_uri_accepted() -> None:
    raw = _minimal_artifact()
    raw["authentication"]["client_secret_ref"] = "secret://aws-secrets-manager/my-secret"
    art = load_dict(raw)
    assert art.operations[0].id == "send_message"


# ---------------------------------------------------------------------------
# Local-$ref rule (§3.10)
# ---------------------------------------------------------------------------


def test_remote_ref_rejected() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["body"] = {"$ref": "https://example.com/schemas/foo.json"}
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_local_ref_must_resolve_to_definitions() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["request"]["body"] = {"$ref": "#/definitions/Missing"}
    with pytest.raises(SpecValidationError, match="not present"):
        load_dict(raw)


def test_local_ref_resolves() -> None:
    raw = _minimal_artifact()
    raw["definitions"] = {
        "MessageRequest": {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        }
    }
    raw["operations"][0]["request"]["body"] = {"$ref": "#/definitions/MessageRequest"}
    art = load_dict(raw)
    assert art.definitions["MessageRequest"]["type"] == "object"


# ---------------------------------------------------------------------------
# HTTPS-only base URL (Principle 11; §4.1)
# ---------------------------------------------------------------------------


def test_http_base_url_rejected() -> None:
    raw = _minimal_artifact()
    raw["dispatch"]["base_url"] = "http://api.example.com"
    with pytest.raises(SpecValidationError, match="HTTPS"):
        load_dict(raw)


# ---------------------------------------------------------------------------
# Authentication method registry (§2.1, §7.3)
# ---------------------------------------------------------------------------


def test_unregistered_method_rejected() -> None:
    raw = _minimal_artifact()
    raw["authentication"]["method"] = "made_up_method"
    with pytest.raises(SpecValidationError):
        load_dict(raw)


def test_x_namespaced_method_accepted() -> None:
    raw = _minimal_artifact()
    raw["authentication"]["method"] = "x-internal-method"
    art = load_dict(raw)
    assert art.authentication.method == "x-internal-method"


# ---------------------------------------------------------------------------
# Inferred-source provenance (§3.8, §3.10)
# ---------------------------------------------------------------------------


def test_inferred_source_complete() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["source"] = {
        "type": "inferred",
        "model": "anthropic/claude-sonnet-4.6",
        "description": "the user said send an email",
        "confidence": "medium",
        "reviewed_at": "2026-05-04T00:00:00Z",
    }
    art = load_dict(raw)
    assert art.operations[0].source is not None


def test_inferred_source_missing_reviewed_at_rejected() -> None:
    raw = _minimal_artifact()
    raw["operations"][0]["source"] = {
        "type": "inferred",
        "model": "anthropic/claude-sonnet-4.6",
        "description": "x",
        # reviewed_at deliberately absent
    }
    with pytest.raises(SpecValidationError):
        load_dict(raw)


# ---------------------------------------------------------------------------
# Encrypted-secrets recursion (§6.2)
# ---------------------------------------------------------------------------


def test_inline_encrypted_recursive_key_ref_rejected() -> None:
    raw = _minimal_artifact()
    raw["encrypted_secrets"] = {
        "blob1": {
            "ciphertext": "AAAA",
            "algorithm": "AES-256-GCM",
            "key_ref": "secret://inline-encrypted/blob2",
            "iv": "AAAA",
            "tag": "AAAA",
        }
    }
    with pytest.raises(SpecValidationError, match="recursion"):
        load_dict(raw)


# ---------------------------------------------------------------------------
# Forward-compat: unknown fields preserved
# ---------------------------------------------------------------------------


def test_unknown_top_level_field_preserved() -> None:
    raw = _minimal_artifact()
    raw["x-vendor-meta"] = {"customer": "acme"}
    art = load_dict(raw)
    # extra="allow" preserves the field on the model (per §3.11)
    assert art.model_extra is not None
    assert art.model_extra.get("x-vendor-meta") == {"customer": "acme"}
