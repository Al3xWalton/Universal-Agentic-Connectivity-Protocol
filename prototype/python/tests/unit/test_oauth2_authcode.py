"""Tests for OAuth 2.0 authorization-code grant with PKCE per §2.2.1.

Mocks httpx via respx so the token-endpoint exchange can be exercised
deterministically without network IO.
"""

from __future__ import annotations

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from uacp_prototype.auth.base import AuthApplyResult
from uacp_prototype.auth.oauth2_authcode import (
    OAuth2AuthCodeConfig,
    OAuth2AuthCodeMethod,
    OAuth2Error,
    PKCEPair,
    TokenSet,
    build_auth_url,
    exchange_code,
    generate_pkce_pair,
    refresh,
)


def _config() -> OAuth2AuthCodeConfig:
    return OAuth2AuthCodeConfig(
        authorization_endpoint="https://accounts.example/oauth2/auth",
        token_endpoint="https://accounts.example/oauth2/token",
        client_id="app-1",
        redirect_uri="https://broker.example/cb",
        scopes=("read", "write"),
        client_secret_resolved="resolved-secret",
    )


# ---------------------------------------------------------------------------
# PKCE generation
# ---------------------------------------------------------------------------


def test_pkce_pair_S256() -> None:
    pair = generate_pkce_pair()
    assert pair.method == "S256"
    # Verifier is at least 43 characters, at most 128 per RFC 7636.
    assert 43 <= len(pair.verifier) <= 128
    # Challenge is BASE64URL(SHA256(verifier)) without padding — 43 chars.
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    assert pair.challenge == expected
    assert "=" not in pair.challenge  # padding stripped


def test_pkce_pair_unique() -> None:
    a = generate_pkce_pair()
    b = generate_pkce_pair()
    assert a.verifier != b.verifier


# ---------------------------------------------------------------------------
# Authorization-URL construction
# ---------------------------------------------------------------------------


def test_build_auth_url_well_formed() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    url = build_auth_url(cfg, state="abc123", pkce=pkce)
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.example"
    assert parsed.path == "/oauth2/auth"
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["app-1"]
    assert qs["redirect_uri"] == ["https://broker.example/cb"]
    assert qs["scope"] == ["read write"]
    assert qs["state"] == ["abc123"]
    assert qs["code_challenge"] == [pkce.challenge]
    assert qs["code_challenge_method"] == ["S256"]


def test_build_auth_url_rejects_plain_method() -> None:
    cfg = _config()
    bad = PKCEPair(verifier="x" * 64, challenge="x" * 64, method="plain")
    with pytest.raises(ValueError, match="S256"):
        build_auth_url(cfg, state="s", pkce=bad)


def test_build_auth_url_extra_params_pass_through() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    url = build_auth_url(cfg, state="s", pkce=pkce, extra_params={"prompt": "consent", "access_type": "offline"})
    qs = parse_qs(urlparse(url).query)
    assert qs["prompt"] == ["consent"]
    assert qs["access_type"] == ["offline"]


def test_build_auth_url_extra_params_cannot_override_reserved() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    with pytest.raises(ValueError):
        build_auth_url(cfg, state="s", pkce=pkce, extra_params={"client_id": "evil"})


# ---------------------------------------------------------------------------
# Token-endpoint exchange
# ---------------------------------------------------------------------------


@respx.mock
def test_exchange_code_returns_tokens() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "read write",
            },
        )
    )
    tokens = exchange_code(cfg, code="auth-code-from-callback", pkce=pkce)
    assert tokens.access_token == "at-1"
    assert tokens.refresh_token == "rt-1"
    assert tokens.scope == "read write"
    assert tokens.expires_at > time.time()
    assert tokens.expires_at <= time.time() + 3601


@respx.mock
def test_exchange_code_passes_required_form_fields() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    route = respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 60})
    )
    exchange_code(cfg, code="auth-code", pkce=pkce)
    request = route.calls[0].request
    body_bytes = request.read()
    body = parse_qs(body_bytes.decode("ascii"))
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["auth-code"]
    assert body["redirect_uri"] == ["https://broker.example/cb"]
    assert body["client_id"] == ["app-1"]
    assert body["code_verifier"] == [pkce.verifier]
    # client_secret is included for confidential clients
    assert body["client_secret"] == ["resolved-secret"]


@respx.mock
def test_exchange_code_handles_400_from_authorization_server() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    with pytest.raises(OAuth2Error, match="invalid_grant"):
        exchange_code(cfg, code="bad-code", pkce=pkce)


@respx.mock
def test_exchange_code_handles_non_json_response() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(200, text="<html>error</html>")
    )
    with pytest.raises(OAuth2Error, match="non-JSON"):
        exchange_code(cfg, code="ok", pkce=pkce)


@respx.mock
def test_exchange_code_handles_missing_access_token() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(200, json={"refresh_token": "rt-1"})
    )
    with pytest.raises(OAuth2Error, match="no access_token"):
        exchange_code(cfg, code="ok", pkce=pkce)


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


@respx.mock
def test_refresh_returns_new_tokens() -> None:
    cfg = _config()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-2",
                "refresh_token": "rt-2",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )
    )
    tokens = refresh(cfg, refresh_token="rt-1")
    assert tokens.access_token == "at-2"
    assert tokens.refresh_token == "rt-2"


@respx.mock
def test_refresh_without_rotation_returns_no_refresh_token() -> None:
    """Per §5.3, when the response omits refresh_token the caller retains
    the prior one. The TokenSet returned here has refresh_token=None;
    lifecycle.refresh handles the retention.
    """
    cfg = _config()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "at-2", "expires_in": 3600},
        )
    )
    tokens = refresh(cfg, refresh_token="rt-1")
    assert tokens.access_token == "at-2"
    assert tokens.refresh_token is None


@respx.mock
def test_refresh_passes_grant_type_and_refresh_token() -> None:
    cfg = _config()
    route = respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 60})
    )
    refresh(cfg, refresh_token="rt-1")
    request = route.calls[0].request
    body = parse_qs(request.read().decode("ascii"))
    assert body["grant_type"] == ["refresh_token"]
    assert body["refresh_token"] == ["rt-1"]
    assert body["client_id"] == ["app-1"]
    assert body["scope"] == ["read write"]


@respx.mock
def test_refresh_invalid_grant_raises() -> None:
    cfg = _config()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Token has been revoked."}
        )
    )
    with pytest.raises(OAuth2Error, match="invalid_grant"):
        refresh(cfg, refresh_token="rt-stale")


# ---------------------------------------------------------------------------
# Apply-on-dispatch
# ---------------------------------------------------------------------------


def test_method_apply_sets_bearer_header() -> None:
    method = OAuth2AuthCodeMethod()
    req = httpx.Request("GET", "https://api.example.com/v1/foo")
    result = method.apply(req, credentials={"access_token": "at-bearer"})
    assert isinstance(result, AuthApplyResult)
    assert result.headers == {"Authorization": "Bearer at-bearer"}
    assert result.query == {}


def test_method_apply_missing_access_token_raises() -> None:
    method = OAuth2AuthCodeMethod()
    req = httpx.Request("GET", "https://api.example.com/v1/foo")
    with pytest.raises(OAuth2Error, match="missing access_token"):
        method.apply(req, credentials={})


# ---------------------------------------------------------------------------
# Confidential vs public client (client_secret optional)
# ---------------------------------------------------------------------------


@respx.mock
def test_exchange_code_omits_client_secret_for_public_client() -> None:
    cfg = OAuth2AuthCodeConfig(
        authorization_endpoint="https://accounts.example/oauth2/auth",
        token_endpoint="https://accounts.example/oauth2/token",
        client_id="public-1",
        redirect_uri="https://broker.example/cb",
        scopes=("read",),
        client_secret_resolved=None,  # public client; PKCE alone authenticates
    )
    pkce = generate_pkce_pair()
    route = respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 60})
    )
    exchange_code(cfg, code="ok", pkce=pkce)
    body = parse_qs(route.calls[0].request.read().decode("ascii"))
    assert "client_secret" not in body
