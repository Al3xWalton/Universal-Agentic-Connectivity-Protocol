"""OAuth 2.0 authorization-code grant with PKCE per §2.2.1.

Implements the four-step flow:

  1. ``build_auth_url(config, state)`` — construct the authorization
     endpoint URL with PKCE S256 challenge.
  2. ``exchange_code(config, code, code_verifier)`` — POST to the token
     endpoint, returning the access token, refresh token, and expiry.
  3. ``store_tokens(connection_id, tokens, secret_store)`` — persist via
     the security.secrets resolver (Commit 6).
  4. ``refresh(config, refresh_token)`` — POST to the token endpoint with
     ``grant_type=refresh_token``; handle rotation atomically per §5.3.

Per §2.2.1, PKCE MUST be supported and MUST be used for non-confidential
clients. The prototype always uses PKCE with S256; ``plain`` is never
emitted.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import AuthApplyResult


@dataclass(frozen=True)
class OAuth2AuthCodeConfig:
    """Parsed OAuth 2.0 authorization-code config from a `.uacp` artifact.

    Field names match the §2.2.1 wire shape verbatim except for
    `client_secret_resolved` — the dispatcher resolves the
    `client_secret_ref` from the artifact through the secret store at
    dispatch time and substitutes the plaintext here. The plaintext lives
    in this dataclass's instance for the duration of one auth exchange and
    is not persisted.
    """

    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]
    code_challenge_method: str = "S256"
    client_secret_resolved: str | None = None  # for confidential clients


@dataclass(frozen=True)
class TokenSet:
    """The credential material returned by a token-endpoint exchange."""

    access_token: str
    refresh_token: str | None
    expires_at: float  # Unix seconds; absolute timestamp
    token_type: str = "Bearer"
    scope: str | None = None


@dataclass(frozen=True)
class PKCEPair:
    """A PKCE code_verifier and its derived code_challenge."""

    verifier: str
    challenge: str
    method: str = "S256"


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def generate_pkce_pair() -> PKCEPair:
    """Generate a PKCE code_verifier and S256 code_challenge per RFC 7636.

    The verifier is a 96-byte URL-safe random string (the spec permits 43-128
    characters; 96 bytes base64url-encoded → 128 characters, the maximum).
    The challenge is BASE64URL(SHA256(verifier)) without padding.
    """
    # secrets.token_urlsafe(96) returns ~128 url-safe chars (96 bytes encoded)
    verifier = secrets.token_urlsafe(96)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return PKCEPair(verifier=verifier, challenge=challenge, method="S256")


# ---------------------------------------------------------------------------
# Auth-URL construction
# ---------------------------------------------------------------------------


def build_auth_url(
    config: OAuth2AuthCodeConfig,
    *,
    state: str,
    pkce: PKCEPair,
    extra_params: dict[str, str] | None = None,
) -> str:
    """Construct the authorization-endpoint URL the user navigates to."""
    if pkce.method != "S256":
        raise ValueError(
            "Prototype OAuth 2.0 authcode requires PKCE S256; "
            "plain is permitted by the spec only when the authorization "
            "server cannot accept S256 (§2.2.1)."
        )
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": " ".join(config.scopes),
        "state": state,
        "code_challenge": pkce.challenge,
        "code_challenge_method": pkce.method,
    }
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
# Token-endpoint exchanges
# ---------------------------------------------------------------------------


def _build_token_request(
    config: OAuth2AuthCodeConfig, body: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Construct the headers and body for a token-endpoint POST."""
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    if config.client_secret_resolved is not None:
        body["client_secret"] = config.client_secret_resolved
    return headers, body


def _parse_token_response(payload: dict[str, Any]) -> TokenSet:
    """Convert a parsed token-endpoint response into a TokenSet.

    Handles both the standard `expires_in` field (seconds-from-now) and
    the absence of one (some providers omit it for long-lived tokens).
    """
    if "access_token" not in payload:
        raise OAuth2Error(
            f"token endpoint returned no access_token; got keys {sorted(payload.keys())}"
        )
    expires_in = payload.get("expires_in")
    if expires_in is None:
        # Default to 1 hour when the provider does not specify; the
        # refresh window logic in §5.2 will still re-check at the
        # right time.
        expires_in = 3600
    expires_at = time.time() + float(expires_in)
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_at=expires_at,
        token_type=payload.get("token_type", "Bearer"),
        scope=payload.get("scope"),
    )


class OAuth2Error(Exception):
    """OAuth 2.0 protocol error from the authorization server."""


def exchange_code(
    config: OAuth2AuthCodeConfig,
    *,
    code: str,
    pkce: PKCEPair,
    client: httpx.Client | None = None,
) -> TokenSet:
    """Exchange an authorization code for an access token."""
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
        "client_id": config.client_id,
        "code_verifier": pkce.verifier,
    }
    headers, body = _build_token_request(config, body)

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
    config: OAuth2AuthCodeConfig,
    *,
    refresh_token: str,
    client: httpx.Client | None = None,
) -> TokenSet:
    """Exchange a refresh token for a fresh access token.

    Handles refresh-token rotation per §5.3: if the response includes a new
    `refresh_token`, the returned TokenSet carries it and the caller MUST
    persist atomically via lifecycle.refresh.refresh_with_rotation. If the
    response omits `refresh_token`, the caller retains the prior one.
    """
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": config.client_id,
    }
    if config.scopes:
        body["scope"] = " ".join(config.scopes)
    headers, body = _build_token_request(config, body)

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


def _parse_response_or_raise(resp: httpx.Response) -> TokenSet:
    """Parse a token-endpoint response, raising OAuth2Error on failure."""
    if resp.status_code >= 400:
        try:
            error_payload = resp.json()
        except ValueError:
            error_payload = {"error": resp.text}
        raise OAuth2Error(
            f"token endpoint returned {resp.status_code}: {error_payload!r}"
        )
    try:
        payload = resp.json()
    except ValueError as e:
        raise OAuth2Error(f"token endpoint returned non-JSON body: {resp.text!r}") from e
    return _parse_token_response(payload)


# ---------------------------------------------------------------------------
# Apply-on-dispatch surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuth2AuthCodeMethod:
    """Adapter implementing the AuthMethod Protocol from `auth.base`.

    On every dispatch the runtime calls `apply` with the current access
    token from the connection's TokenSet. The token is presented as a
    Bearer credential per RFC 6750 (the most common shape; alternative
    shapes like access_token=... query parameters are out of scope for
    Stage 8a).
    """

    method: str = "oauth2_authorization_code"

    def apply(
        self, request: httpx.Request, *, credentials: dict[str, Any]
    ) -> AuthApplyResult:
        access_token = credentials.get("access_token")
        if not access_token:
            raise OAuth2Error(
                "OAuth2AuthCodeMethod.apply: credentials missing access_token; "
                "the connection must be in `active` state with a valid TokenSet"
            )
        return AuthApplyResult(headers={"Authorization": f"Bearer {access_token}"})


__all__ = [
    "OAuth2AuthCodeConfig",
    "OAuth2AuthCodeMethod",
    "OAuth2Error",
    "PKCEPair",
    "TokenSet",
    "build_auth_url",
    "exchange_code",
    "generate_pkce_pair",
    "refresh",
]
