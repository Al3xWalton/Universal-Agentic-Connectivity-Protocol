"""Tests for API-key authentication per §2.4."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from uacp_prototype.auth.api_key import (
    APIKeyAuthError,
    APIKeyHeaderConfig,
    APIKeyHeaderMethod,
    APIKeyQueryConfig,
    APIKeyQueryMethod,
)
from uacp_prototype.auth.base import AuthApplyResult


# ---------------------------------------------------------------------------
# api_key_header — §2.4.1
# ---------------------------------------------------------------------------


def test_header_authorization_bearer() -> None:
    """The GitHub case: Authorization: Bearer <PAT>."""
    method = APIKeyHeaderMethod(
        config=APIKeyHeaderConfig(header_name="Authorization", header_prefix="Bearer ")
    )
    req = httpx.Request("GET", "https://api.github.com/user")
    result = method.apply(req, credentials={"key": "ghp_xyz123"})
    assert isinstance(result, AuthApplyResult)
    assert result.headers == {"Authorization": "Bearer ghp_xyz123"}


def test_header_authorization_token_legacy() -> None:
    """GitHub legacy / OpenAI legacy: Authorization: Token <key>."""
    method = APIKeyHeaderMethod(
        config=APIKeyHeaderConfig(header_name="Authorization", header_prefix="Token ")
    )
    req = httpx.Request("GET", "https://api.github.com/user")
    result = method.apply(req, credentials={"key": "abcd"})
    assert result.headers == {"Authorization": "Token abcd"}


def test_header_x_api_key_no_prefix() -> None:
    """Vendor-specific X-API-Key with no prefix."""
    method = APIKeyHeaderMethod(
        config=APIKeyHeaderConfig(header_name="X-API-Key", header_prefix="")
    )
    req = httpx.Request("GET", "https://api.example.com/v1/x")
    result = method.apply(req, credentials={"key": "raw-key-value"})
    assert result.headers == {"X-API-Key": "raw-key-value"}


def test_header_preserves_artifact_case() -> None:
    """RFC 9110 §5.1 makes header names case-insensitive at the wire,
    but the prototype emits the artifact's case so downstream tooling
    sees the intended form."""
    method = APIKeyHeaderMethod(
        config=APIKeyHeaderConfig(header_name="x-custom-Auth", header_prefix="")
    )
    req = httpx.Request("GET", "https://api.example.com/")
    result = method.apply(req, credentials={"key": "k"})
    assert "x-custom-Auth" in result.headers


def test_header_credentials_alias_api_key() -> None:
    """make_credential_resolver may yield ``api_key`` rather than
    ``key`` when the artifact's secret_refs use that name. Accept
    either."""
    method = APIKeyHeaderMethod(
        config=APIKeyHeaderConfig(header_name="Authorization", header_prefix="Bearer ")
    )
    req = httpx.Request("GET", "https://api.example.com/")
    result = method.apply(req, credentials={"api_key": "alias-key"})
    assert result.headers == {"Authorization": "Bearer alias-key"}


def test_header_missing_key_raises() -> None:
    method = APIKeyHeaderMethod(
        config=APIKeyHeaderConfig(header_name="Authorization", header_prefix="Bearer ")
    )
    req = httpx.Request("GET", "https://api.example.com/")
    with pytest.raises(APIKeyAuthError, match="missing key"):
        method.apply(req, credentials={})


def test_header_empty_string_key_raises() -> None:
    method = APIKeyHeaderMethod(
        config=APIKeyHeaderConfig(header_name="Authorization", header_prefix="Bearer ")
    )
    req = httpx.Request("GET", "https://api.example.com/")
    with pytest.raises(APIKeyAuthError, match="missing key"):
        method.apply(req, credentials={"key": ""})


def test_header_method_without_config_raises() -> None:
    method = APIKeyHeaderMethod()
    req = httpx.Request("GET", "https://api.example.com/")
    with pytest.raises(APIKeyAuthError, match="config not set"):
        method.apply(req, credentials={"key": "k"})


def test_header_method_identifier_is_registered() -> None:
    """Per §2.1 registered methods, the identifier MUST be the literal
    `api_key_header`."""
    method = APIKeyHeaderMethod()
    assert method.method == "api_key_header"


# ---------------------------------------------------------------------------
# api_key_query — §2.4.2
# ---------------------------------------------------------------------------


def test_query_basic_injection() -> None:
    method = APIKeyQueryMethod(config=APIKeyQueryConfig(param_name="api_key"))
    req = httpx.Request("GET", "https://api.example.com/v1/x")
    result = method.apply(req, credentials={"key": "k123"})
    assert result.query == {"api_key": "k123"}
    assert result.headers == {}


def test_query_credentials_alias_api_key() -> None:
    method = APIKeyQueryMethod(config=APIKeyQueryConfig(param_name="apikey"))
    req = httpx.Request("GET", "https://api.example.com/")
    result = method.apply(req, credentials={"api_key": "alias-value"})
    assert result.query == {"apikey": "alias-value"}


def test_query_missing_key_raises() -> None:
    method = APIKeyQueryMethod(config=APIKeyQueryConfig(param_name="api_key"))
    req = httpx.Request("GET", "https://api.example.com/")
    with pytest.raises(APIKeyAuthError, match="missing key"):
        method.apply(req, credentials={})


def test_query_emits_disrecommendation_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Per §2.4.2 the implementation emits a runtime warning when
    api_key_query is used, since the disrecommendation is not
    enforceable at the spec layer (some providers only accept the
    query form)."""
    import logging

    method = APIKeyQueryMethod(config=APIKeyQueryConfig(param_name="key"))
    req = httpx.Request("GET", "https://api.example.com/")
    with caplog.at_level(logging.WARNING, logger="uacp.auth.api_key"):
        method.apply(req, credentials={"key": "k"})
    assert any("§2.4.2" in r.message or "disrecommended" in r.message for r in caplog.records)


def test_query_method_without_config_raises() -> None:
    method = APIKeyQueryMethod()
    req = httpx.Request("GET", "https://api.example.com/")
    with pytest.raises(APIKeyAuthError, match="config not set"):
        method.apply(req, credentials={"key": "k"})


def test_query_method_identifier_is_registered() -> None:
    method = APIKeyQueryMethod()
    assert method.method == "api_key_query"


# ---------------------------------------------------------------------------
# Header value with reserved characters
# ---------------------------------------------------------------------------


def test_header_value_with_special_characters_passed_through() -> None:
    """The prototype doesn't transform key values; whatever the secret
    store returns lands at the wire. Tokens with `/`, `+`, `=`, etc.
    (common in base64-encoded keys) pass through unchanged."""
    method = APIKeyHeaderMethod(
        config=APIKeyHeaderConfig(header_name="Authorization", header_prefix="Bearer ")
    )
    req = httpx.Request("GET", "https://api.example.com/")
    result = method.apply(req, credentials={"key": "abc+def/ghi=="})
    assert result.headers == {"Authorization": "Bearer abc+def/ghi=="}


def test_header_value_long_key_passed_through() -> None:
    """GitHub fine-grained PATs are ~80 characters; OpenAI keys ~50.
    No length truncation."""
    method = APIKeyHeaderMethod(
        config=APIKeyHeaderConfig(header_name="Authorization", header_prefix="Bearer ")
    )
    req = httpx.Request("GET", "https://api.example.com/")
    long_key = "github_pat_" + "X" * 80
    result = method.apply(req, credentials={"key": long_key})
    assert result.headers == {"Authorization": f"Bearer {long_key}"}
