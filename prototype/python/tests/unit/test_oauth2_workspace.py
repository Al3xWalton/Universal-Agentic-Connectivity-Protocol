"""Tests for the Slack workspace-scoped OAuth flavor."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from uacp_prototype.auth.base import AuthApplyResult
from uacp_prototype.auth.oauth2_authcode import OAuth2Error, generate_pkce_pair
from uacp_prototype.auth.oauth2_workspace import (
    OAuth2WorkspaceConfig,
    OAuth2WorkspaceMethod,
    WorkspaceTokens,
    build_auth_url,
    exchange_code,
    refresh,
)


def _config(
    *,
    user_scopes: tuple[str, ...] = ("identity.basic",),
    client_secret: str | None = "resolved-secret",
) -> OAuth2WorkspaceConfig:
    return OAuth2WorkspaceConfig(
        authorization_endpoint="https://slack.com/oauth/v2/authorize",
        token_endpoint="https://slack.com/api/oauth.v2.access",
        client_id="123.456",
        redirect_uri="https://broker.example/cb",
        scopes=("chat:write", "channels:read"),
        user_scopes=user_scopes,
        client_secret_resolved=client_secret,
    )


# ---------------------------------------------------------------------------
# Authorization-URL construction
# ---------------------------------------------------------------------------


def test_auth_url_emits_both_scope_and_user_scope() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    url = build_auth_url(cfg, state="s-1", pkce=pkce)
    qs = parse_qs(urlparse(url).query)
    # Slack uses comma-separated scopes (not space).
    assert qs["scope"] == ["chat:write,channels:read"]
    assert qs["user_scope"] == ["identity.basic"]
    assert qs["client_id"] == ["123.456"]
    assert qs["redirect_uri"] == ["https://broker.example/cb"]
    assert qs["state"] == ["s-1"]
    assert qs["response_type"] == ["code"]
    assert qs["code_challenge"] == [pkce.challenge]
    assert qs["code_challenge_method"] == ["S256"]


def test_auth_url_omits_scope_when_no_bot_scopes() -> None:
    cfg = _config(user_scopes=("identity.basic",))
    cfg = OAuth2WorkspaceConfig(
        authorization_endpoint=cfg.authorization_endpoint,
        token_endpoint=cfg.token_endpoint,
        client_id=cfg.client_id,
        redirect_uri=cfg.redirect_uri,
        scopes=(),
        user_scopes=("identity.basic",),
        client_secret_resolved=cfg.client_secret_resolved,
    )
    pkce = generate_pkce_pair()
    url = build_auth_url(cfg, state="s", pkce=pkce)
    qs = parse_qs(urlparse(url).query)
    assert "scope" not in qs
    assert qs["user_scope"] == ["identity.basic"]


def test_auth_url_omits_user_scope_when_none() -> None:
    cfg = OAuth2WorkspaceConfig(
        authorization_endpoint="https://slack.com/oauth/v2/authorize",
        token_endpoint="https://slack.com/api/oauth.v2.access",
        client_id="123.456",
        redirect_uri="https://broker.example/cb",
        scopes=("chat:write",),
        user_scopes=(),
    )
    pkce = generate_pkce_pair()
    url = build_auth_url(cfg, state="s", pkce=pkce)
    qs = parse_qs(urlparse(url).query)
    assert qs["scope"] == ["chat:write"]
    assert "user_scope" not in qs


def test_auth_url_rejects_empty_scopes_and_user_scopes() -> None:
    cfg = OAuth2WorkspaceConfig(
        authorization_endpoint="https://slack.com/oauth/v2/authorize",
        token_endpoint="https://slack.com/api/oauth.v2.access",
        client_id="123.456",
        redirect_uri="https://broker.example/cb",
        scopes=(),
        user_scopes=(),
    )
    pkce = generate_pkce_pair()
    with pytest.raises(ValueError, match="non-empty"):
        build_auth_url(cfg, state="s", pkce=pkce)


def test_auth_url_rejects_plain_pkce() -> None:
    cfg = _config()
    bad = generate_pkce_pair()
    bad = type(bad)(verifier=bad.verifier, challenge=bad.challenge, method="plain")
    with pytest.raises(ValueError, match="S256"):
        build_auth_url(cfg, state="s", pkce=bad)


def test_auth_url_extra_params_pass_through() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    url = build_auth_url(cfg, state="s", pkce=pkce, extra_params={"team": "T0001"})
    qs = parse_qs(urlparse(url).query)
    assert qs["team"] == ["T0001"]


def test_auth_url_extra_params_cannot_override_reserved() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    with pytest.raises(ValueError):
        build_auth_url(cfg, state="s", pkce=pkce, extra_params={"client_id": "evil"})


# ---------------------------------------------------------------------------
# Token-endpoint exchange — Slack's response shape
# ---------------------------------------------------------------------------


@respx.mock
def test_exchange_code_parses_bot_and_user_tokens() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-bot-1",
                "token_type": "bot",
                "scope": "chat:write,channels:read",
                "bot_user_id": "U_BOT",
                "app_id": "A_APP",
                "team": {"id": "T_TEAM", "name": "acme"},
                "authed_user": {
                    "id": "U_USER",
                    "scope": "identity.basic",
                    "access_token": "xoxp-user-1",
                    "token_type": "user",
                },
            },
        )
    )
    tokens = exchange_code(cfg, code="auth-code", pkce=pkce)
    assert isinstance(tokens, WorkspaceTokens)
    assert tokens.bot_access_token == "xoxb-bot-1"
    assert tokens.user_access_token == "xoxp-user-1"
    assert tokens.team_id == "T_TEAM"
    assert tokens.bot_user_id == "U_BOT"
    assert tokens.app_id == "A_APP"
    assert tokens.bot_scope == "chat:write,channels:read"
    assert tokens.user_scope == "identity.basic"
    # Non-rotating tokens have no expiry.
    assert tokens.bot_expires_at is None
    assert tokens.bot_refresh_token is None


@respx.mock
def test_exchange_code_handles_no_user_token() -> None:
    """A bot-only install: scope present, user_scope omitted at auth time."""
    cfg = _config(user_scopes=())
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-bot-1",
                "scope": "chat:write",
                "team": {"id": "T_TEAM"},
                "authed_user": {"id": "U_USER"},
            },
        )
    )
    tokens = exchange_code(cfg, code="auth-code", pkce=pkce)
    assert tokens.bot_access_token == "xoxb-bot-1"
    assert tokens.user_access_token is None
    assert tokens.user_scope is None


@respx.mock
def test_exchange_code_with_token_rotation_parses_expires_in_and_refresh_token() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-bot-rotating",
                "expires_in": 43200,  # 12h, Slack's default rotation window
                "refresh_token": "xoxe-1-bot-refresh",
                "scope": "chat:write",
                "team": {"id": "T"},
                "authed_user": {"id": "U", "access_token": "xoxp-1"},
            },
        )
    )
    before = time.time()
    tokens = exchange_code(cfg, code="ok", pkce=pkce)
    assert tokens.bot_refresh_token == "xoxe-1-bot-refresh"
    assert tokens.bot_expires_at is not None
    assert tokens.bot_expires_at >= before + 43000
    assert tokens.bot_expires_at <= before + 43400


@respx.mock
def test_exchange_code_form_fields_include_pkce_verifier_and_secret() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    route = respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "access_token": "xoxb-1", "team": {"id": "T"}},
        )
    )
    exchange_code(cfg, code="auth-code", pkce=pkce)
    body = parse_qs(route.calls[0].request.read().decode("ascii"))
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["auth-code"]
    assert body["redirect_uri"] == ["https://broker.example/cb"]
    assert body["client_id"] == ["123.456"]
    assert body["code_verifier"] == [pkce.verifier]
    assert body["client_secret"] == ["resolved-secret"]


@respx.mock
def test_exchange_code_handles_ok_false_with_error_string() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_code"}),
    )
    with pytest.raises(OAuth2Error, match="ok=false"):
        exchange_code(cfg, code="bad-code", pkce=pkce)


@respx.mock
def test_exchange_code_handles_4xx_with_envelope() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(400, json={"ok": False, "error": "invalid_client_id"}),
    )
    with pytest.raises(OAuth2Error, match="400"):
        exchange_code(cfg, code="ok", pkce=pkce)


@respx.mock
def test_exchange_code_handles_non_json_response() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(500, text="<html>oops</html>"),
    )
    with pytest.raises(OAuth2Error, match="non-JSON"):
        exchange_code(cfg, code="ok", pkce=pkce)


@respx.mock
def test_exchange_code_handles_ok_true_but_no_access_token() -> None:
    cfg = _config()
    pkce = generate_pkce_pair()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(200, json={"ok": True, "team": {"id": "T"}}),
    )
    with pytest.raises(OAuth2Error, match="no access_token"):
        exchange_code(cfg, code="ok", pkce=pkce)


# ---------------------------------------------------------------------------
# Refresh (token rotation)
# ---------------------------------------------------------------------------


@respx.mock
def test_refresh_returns_new_workspace_tokens() -> None:
    cfg = _config()
    respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-bot-2",
                "expires_in": 43200,
                "refresh_token": "xoxe-1-bot-refresh-2",
                "scope": "chat:write",
                "team": {"id": "T"},
                "authed_user": {"id": "U"},
            },
        )
    )
    tokens = refresh(cfg, refresh_token="xoxe-1-bot-refresh-1")
    assert tokens.bot_access_token == "xoxb-bot-2"
    assert tokens.bot_refresh_token == "xoxe-1-bot-refresh-2"


@respx.mock
def test_refresh_passes_grant_type_refresh_token() -> None:
    cfg = _config()
    route = respx.post(cfg.token_endpoint).mock(
        return_value=httpx.Response(
            200, json={"ok": True, "access_token": "xoxb-x", "team": {"id": "T"}}
        )
    )
    refresh(cfg, refresh_token="xoxe-1-foo")
    body = parse_qs(route.calls[0].request.read().decode("ascii"))
    assert body["grant_type"] == ["refresh_token"]
    assert body["refresh_token"] == ["xoxe-1-foo"]
    assert body["client_id"] == ["123.456"]


# ---------------------------------------------------------------------------
# Apply-on-dispatch
# ---------------------------------------------------------------------------


def test_apply_bot_token_default() -> None:
    method = OAuth2WorkspaceMethod()
    req = httpx.Request("POST", "https://slack.com/api/chat.postMessage")
    result = method.apply(req, credentials={"bot_access_token": "xoxb-1"})
    assert isinstance(result, AuthApplyResult)
    assert result.headers == {"Authorization": "Bearer xoxb-1"}


def test_apply_falls_back_to_access_token_alias() -> None:
    """make_credential_resolver may yield ``access_token`` rather than
    ``bot_access_token`` when an artifact's secret_refs use the generic
    name. Accept either."""
    method = OAuth2WorkspaceMethod()
    req = httpx.Request("POST", "https://slack.com/api/chat.postMessage")
    result = method.apply(req, credentials={"access_token": "xoxb-fallback"})
    assert result.headers == {"Authorization": "Bearer xoxb-fallback"}


def test_apply_user_token_when_kind_is_user() -> None:
    method = OAuth2WorkspaceMethod(token_kind="user")
    req = httpx.Request("POST", "https://slack.com/api/users.profile.set")
    result = method.apply(req, credentials={"user_access_token": "xoxp-1"})
    assert result.headers == {"Authorization": "Bearer xoxp-1"}


def test_apply_bot_missing_token_raises() -> None:
    method = OAuth2WorkspaceMethod()
    req = httpx.Request("POST", "https://slack.com/api/chat.postMessage")
    with pytest.raises(OAuth2Error, match="bot_access_token"):
        method.apply(req, credentials={})


def test_apply_user_missing_token_raises() -> None:
    method = OAuth2WorkspaceMethod(token_kind="user")
    req = httpx.Request("POST", "https://slack.com/api/users.profile.set")
    with pytest.raises(OAuth2Error, match="user_access_token"):
        method.apply(req, credentials={"bot_access_token": "xoxb-1"})


def test_method_name_is_x_namespaced() -> None:
    """Per §7.3 in-development extensions; until the spec promotes
    workspace-scoped OAuth to a registered identifier, the method
    selector in `.uacp` files MUST use the ``x-`` prefix.
    """
    method = OAuth2WorkspaceMethod()
    assert method.method == "x-oauth2-workspace"
    assert method.method.startswith("x-")
