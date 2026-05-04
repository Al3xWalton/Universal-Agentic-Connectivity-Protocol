"""OAuth 2.0 with workspace-scoped tokens — Slack's flavor of authorization-code.

Slack's OAuth 2.0 flow is RFC 6749 authorization-code-with-PKCE (so the §2.2.1
machinery applies) but diverges in three ways that don't fit the vanilla
contract in `oauth2_authcode.py`:

1. **Two scope parameters at the authorization endpoint.** Slack's
   ``scope=`` carries bot scopes; ``user_scope=`` carries user scopes.
   RFC 6749 defines only one ``scope`` parameter, so this is a Slack-
   specific extension.

2. **Two tokens in the token-endpoint response.** Slack returns a bot
   token (``xoxb-...``) at ``access_token`` plus a user token
   (``xoxp-...``) at ``authed_user.access_token``. Each is scoped
   separately. Most operations dispatch with the bot token; some
   user-facing operations need the user token.

3. **Tokens don't expire by default.** Slack added optional token
   rotation in 2021; apps must opt in. Without rotation, bot tokens are
   long-lived and never enter the §5.2 refresh-window logic. With
   rotation, the response includes ``expires_in`` + ``refresh_token``
   and the standard §5.3 atomic-replace pattern applies.

This module is implemented as a separate `AuthMethod` rather than
overloading `oauth2_authcode.py` because the latter's contract is RFC
6749 vanilla and the ``user_scope`` parameter would silently break
consumers that pass it through `extra_params`. PKCE primitives
(`generate_pkce_pair`, `PKCEPair`) and the `OAuth2Error` exception are
re-used from `oauth2_authcode.py` directly — no duplication.

Per §7.3 (in-development extensions), `.uacp` artifacts that select this
flow declare ``authentication.method`` as the x-namespaced identifier
``x-oauth2-workspace`` until the spec promotes it to a registered
identifier in a future v1.x release.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import AuthApplyResult
from .oauth2_authcode import (  # re-use RFC 7636 PKCE + the OAuth2Error type
    OAuth2Error,
    PKCEPair,
    generate_pkce_pair,
)


__all__ = [
    "OAuth2WorkspaceConfig",
    "OAuth2WorkspaceMethod",
    "WorkspaceTokens",
    "build_auth_url",
    "exchange_code",
    "generate_pkce_pair",  # re-exported for caller convenience
    "refresh",
]


@dataclass(frozen=True)
class OAuth2WorkspaceConfig:
    """Parsed workspace-scoped OAuth config from a `.uacp` artifact.

    Fields parallel ``OAuth2AuthCodeConfig`` plus:

    - ``user_scopes`` — Slack's user-scope set, distinct from bot scopes.
    - The ``scopes`` field carries bot scopes (Slack's ``scope=``
      parameter), to keep the field-name parallel with the vanilla
      authcode shape.
    """

    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]                    # bot scopes
    user_scopes: tuple[str, ...] = ()          # user scopes; MAY be empty
    code_challenge_method: str = "S256"
    client_secret_resolved: str | None = None  # confidential clients only


@dataclass(frozen=True)
class WorkspaceTokens:
    """Workspace-scoped credential material from a Slack token exchange.

    Slack returns the bot token at ``access_token`` and the optional user
    token at ``authed_user.access_token``. The dataclass keeps both plus
    the workspace identity (``team_id``) and the OAuth 2021 rotation
    payload (``refresh_token``, ``expires_at``) when present.

    Per §5.2 the prototype's `is_in_refresh_window` honors None / 0
    expiry as "non-expiring"; non-rotating Slack tokens land here with
    ``bot_expires_at = None`` and never enter the refresh-window logic.
    """

    bot_access_token: str
    user_access_token: str | None
    team_id: str | None
    bot_user_id: str | None
    app_id: str | None
    bot_scope: str | None
    user_scope: str | None
    bot_refresh_token: str | None = None
    bot_expires_at: float | None = None        # absolute ts; None → non-expiring
    user_refresh_token: str | None = None
    user_expires_at: float | None = None
    token_type: str = "Bearer"


# ---------------------------------------------------------------------------
# Auth-URL construction
# ---------------------------------------------------------------------------


def build_auth_url(
    config: OAuth2WorkspaceConfig,
    *,
    state: str,
    pkce: PKCEPair,
    extra_params: dict[str, str] | None = None,
) -> str:
    """Construct the workspace-scoped authorization-endpoint URL.

    Slack accepts both ``scope`` (bot scopes) and ``user_scope`` (user
    scopes) at the authorization endpoint. Either MAY be empty; at least
    one MUST be non-empty.
    """
    if pkce.method != "S256":
        raise ValueError(
            "workspace-scoped OAuth requires PKCE S256; "
            "Slack's authorization endpoint accepts S256."
        )
    if not config.scopes and not config.user_scopes:
        raise ValueError(
            "build_auth_url: at least one of scopes / user_scopes must be non-empty"
        )

    params: dict[str, str] = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "state": state,
        "code_challenge": pkce.challenge,
        "code_challenge_method": pkce.method,
    }
    if config.scopes:
        params["scope"] = ",".join(config.scopes)  # Slack uses comma, not space
    if config.user_scopes:
        params["user_scope"] = ",".join(config.user_scopes)

    if extra_params:
        for k, v in extra_params.items():
            if k in params:
                raise ValueError(
                    f"extra_params override of reserved parameter {k!r} not permitted"
                )
            params[k] = v

    sep = "&" if "?" in config.authorization_endpoint else "?"
    return f"{config.authorization_endpoint}{sep}{urlencode(params)}"


# ---------------------------------------------------------------------------
# Token-endpoint exchange
# ---------------------------------------------------------------------------


def _build_token_request_body(
    config: OAuth2WorkspaceConfig, body: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Build headers + body for the Slack token-endpoint POST.

    Slack's ``oauth.v2.access`` endpoint expects ``application/x-www-form-
    urlencoded``. ``client_secret`` is form-encoded (Slack also accepts
    HTTP Basic, but form-encoded is universally supported).
    """
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    if config.client_secret_resolved is not None:
        body["client_secret"] = config.client_secret_resolved
    return headers, body


def _parse_workspace_response(payload: dict[str, Any]) -> WorkspaceTokens:
    """Convert a parsed Slack token-endpoint response into WorkspaceTokens.

    Slack's response wraps everything in {ok: bool, ...} the same way the
    rest of its API does. Token-endpoint failures arrive as
    ``{ok: false, error: "..."}`` rather than RFC 6749's ``{error: "..."}``,
    so the `_parse_response_or_raise` here looks for ``ok`` first.
    """
    if not payload.get("ok"):
        raise OAuth2Error(
            f"Slack token endpoint returned ok=false: {payload!r}"
        )

    bot_access_token = payload.get("access_token")
    if not bot_access_token:
        raise OAuth2Error(
            f"Slack token endpoint returned ok=true but no access_token: {sorted(payload.keys())}"
        )

    authed_user = payload.get("authed_user") or {}
    team = payload.get("team") or {}

    bot_expires_in = payload.get("expires_in")
    bot_expires_at = (
        time.time() + float(bot_expires_in) if bot_expires_in is not None else None
    )
    user_expires_in = authed_user.get("expires_in")
    user_expires_at = (
        time.time() + float(user_expires_in) if user_expires_in is not None else None
    )

    return WorkspaceTokens(
        bot_access_token=bot_access_token,
        user_access_token=authed_user.get("access_token"),
        team_id=team.get("id") if isinstance(team, dict) else None,
        bot_user_id=payload.get("bot_user_id"),
        app_id=payload.get("app_id"),
        bot_scope=payload.get("scope"),
        user_scope=authed_user.get("scope"),
        bot_refresh_token=payload.get("refresh_token"),
        bot_expires_at=bot_expires_at,
        user_refresh_token=authed_user.get("refresh_token"),
        user_expires_at=user_expires_at,
        token_type=payload.get("token_type", "Bearer"),
    )


def exchange_code(
    config: OAuth2WorkspaceConfig,
    *,
    code: str,
    pkce: PKCEPair,
    client: httpx.Client | None = None,
) -> WorkspaceTokens:
    """Exchange an authorization code for workspace-scoped tokens."""
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
        "client_id": config.client_id,
        "code_verifier": pkce.verifier,
    }
    headers, body = _build_token_request_body(config, body)

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=30)
    assert client is not None

    try:
        resp = client.post(config.token_endpoint, headers=headers, data=body)
    finally:
        if own_client:
            client.close()

    return _parse_response_or_raise(resp)


def refresh(
    config: OAuth2WorkspaceConfig,
    *,
    refresh_token: str,
    client: httpx.Client | None = None,
) -> WorkspaceTokens:
    """Refresh a workspace-scoped token (only when rotation is enabled).

    Slack's rotation flow uses the standard ``grant_type=refresh_token``
    shape. The response is the same workspace-tokens envelope as the
    initial exchange. Per §5.3 the caller atomically replaces the prior
    refresh token with the new one returned here.
    """
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": config.client_id,
    }
    headers, body = _build_token_request_body(config, body)

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=30)
    assert client is not None

    try:
        resp = client.post(config.token_endpoint, headers=headers, data=body)
    finally:
        if own_client:
            client.close()

    return _parse_response_or_raise(resp)


def _parse_response_or_raise(resp: httpx.Response) -> WorkspaceTokens:
    """Slack token-endpoint failures arrive as 200 + ok=false OR as 4xx.

    Both shapes are surfaced as OAuth2Error.
    """
    try:
        payload = resp.json()
    except ValueError as e:
        raise OAuth2Error(
            f"Slack token endpoint returned non-JSON body: {resp.text!r}"
        ) from e

    # 4xx: Slack typically still wraps as {ok: false, ...} but be defensive.
    if resp.status_code >= 400 and not payload.get("ok"):
        raise OAuth2Error(
            f"Slack token endpoint returned {resp.status_code}: {payload!r}"
        )

    # 200 with ok=false also fails (the body-predicate gap that Stage 8b
    # surfaced; here it applies to the token endpoint, not the data plane).
    if not payload.get("ok"):
        raise OAuth2Error(
            f"Slack token endpoint returned ok=false: {payload!r}"
        )

    return _parse_workspace_response(payload)


# ---------------------------------------------------------------------------
# Apply-on-dispatch surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuth2WorkspaceMethod:
    """Adapter implementing AuthMethod for workspace-scoped OAuth.

    On every dispatch the runtime calls ``apply`` with the resolved
    credential dict. The adapter expects EITHER:

      - ``credentials["bot_access_token"]`` — the default; selects the
        bot token for the dispatch.
      - ``credentials["user_access_token"]`` — selects the user token,
        used when the dispatched operation declares
        ``authentication.token_kind = "user"`` in the artifact (an
        x-namespaced extension; see operation metadata in
        `.uacp` files).

    The default is bot-token. The selection rule is implementation-
    defined; the prototype reads the operation's
    ``model_extra["authentication_token_kind"]`` field when present and
    falls back to "bot".

    Slack's API authenticates with ``Authorization: Bearer xoxb-...`` (or
    ``Bearer xoxp-...``) per RFC 6750.
    """

    method: str = "x-oauth2-workspace"
    token_kind: str = "bot"  # "bot" | "user"

    def apply(
        self, request: httpx.Request, *, credentials: dict[str, Any]
    ) -> AuthApplyResult:
        if self.token_kind == "user":
            tok = credentials.get("user_access_token")
            if not tok:
                raise OAuth2Error(
                    "OAuth2WorkspaceMethod(user): credentials missing user_access_token; "
                    "the connection's user_scope set may be empty or the operation's "
                    "token_kind=user is misconfigured"
                )
        else:
            tok = credentials.get("bot_access_token") or credentials.get("access_token")
            if not tok:
                raise OAuth2Error(
                    "OAuth2WorkspaceMethod(bot): credentials missing bot_access_token; "
                    "the connection must be in `active` state with valid WorkspaceTokens"
                )

        return AuthApplyResult(headers={"Authorization": f"Bearer {tok}"})
