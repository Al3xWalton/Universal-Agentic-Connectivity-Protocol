"""API-key authentication per §2.4.

Two flavors registered in v1.0:

  - ``api_key_header`` (§2.4.1) — the most common shape. Inject the key
    in an HTTP request header, optionally with a prefix. Examples:
    ``Authorization: Bearer <key>`` (GitHub PATs, most modern APIs);
    ``X-API-Key: <key>`` (vendor-specific, no prefix); ``Authorization:
    Token <key>`` (GitHub legacy, OpenAI legacy, several SaaS APIs).

  - ``api_key_query`` (§2.4.2) — disrecommended; query-parameter API
    keys leak into server logs / browser history / proxy logs. The
    spec keeps this method registered because some providers require
    it; the prototype implements it but emits a runtime warning when
    the artifact's ``param_name`` value is used.

Both are simple at request-time: resolve the key from the secret store
(handled by the dispatcher's credential resolver), then inject in the
right place. No flow, no token exchange, no per-request signing — the
simplest auth shape in UACP's registry.

Per §5.2, API keys without rotation flows are non-expiring; the
lifecycle state machine treats them as ``active`` until ``revoked``,
no ``expiring`` / ``refreshing`` / ``expired`` cycle. The §5.2
``is_in_refresh_window`` helper short-circuits cleanly when
``token_expires_at`` is None.

GitHub's three PAT formats (``ghp_``, ``github_pat_``, ``gho_``) all
land at the wire as ``Authorization: Bearer <token>``. UACP doesn't
distinguish them — the artifact says "API key in Authorization header
with prefix Bearer "; the runtime injects whatever key the secret
store resolved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .base import AuthApplyResult


__all__ = [
    "APIKeyAuthError",
    "APIKeyHeaderConfig",
    "APIKeyHeaderMethod",
    "APIKeyQueryConfig",
    "APIKeyQueryMethod",
]


log = logging.getLogger("uacp.auth.api_key")


class APIKeyAuthError(Exception):
    """API-key auth failed (missing key, malformed config)."""


# ---------------------------------------------------------------------------
# api_key_header — §2.4.1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class APIKeyHeaderConfig:
    """Parsed `api_key_header` config from a `.uacp` artifact.

    `header_name` is the HTTP header name. RFC 9110 §5.1 makes header
    names case-insensitive; we preserve the artifact's case for the
    wire-emit so downstream proxies / logs see the artifact's
    intended form.

    `header_prefix` is the literal string prepended to the key value
    before assignment. Common values include ``"Bearer "`` (with
    trailing space; GitHub, OpenAI, modern OAuth providers),
    ``"Token "`` (GitHub legacy), or ``""`` (no prefix; vendor
    headers like ``X-API-Key`` typically don't have one).
    """

    header_name: str
    header_prefix: str = ""


@dataclass(frozen=True)
class APIKeyHeaderMethod:
    """Adapter implementing AuthMethod for `api_key_header`."""

    method: str = "api_key_header"
    config: APIKeyHeaderConfig | None = None

    def apply(
        self, request: Any, *, credentials: dict[str, Any]
    ) -> AuthApplyResult:
        if self.config is None:
            raise APIKeyAuthError(
                "APIKeyHeaderMethod.apply: config not set; constructed without "
                "header_name/header_prefix. The dispatcher MUST construct this "
                "adapter from the artifact's authentication block."
            )
        key = credentials.get("key") or credentials.get("api_key")
        if not key:
            raise APIKeyAuthError(
                "APIKeyHeaderMethod.apply: credentials missing key. The "
                "artifact's `key_ref` MUST resolve to a non-empty value."
            )
        return AuthApplyResult(
            headers={self.config.header_name: f"{self.config.header_prefix}{key}"}
        )


# ---------------------------------------------------------------------------
# api_key_query — §2.4.2
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class APIKeyQueryConfig:
    """Parsed `api_key_query` config. The `param_name` is the query
    parameter name; the resolved key value is set as the parameter's
    value at dispatch time.
    """

    param_name: str


@dataclass(frozen=True)
class APIKeyQueryMethod:
    """Adapter implementing AuthMethod for `api_key_query`.

    Per §2.4.2 implementations MAY emit a runtime warning when
    ``api_key_query`` is used and the provider's documentation
    indicates that ``api_key_header`` is also supported. The prototype
    emits an unconditional warning the first time the method is
    applied for a given artifact, since the implementation has no
    knowledge of provider capabilities at the spec layer.
    """

    method: str = "api_key_query"
    config: APIKeyQueryConfig | None = None

    def apply(
        self, request: Any, *, credentials: dict[str, Any]
    ) -> AuthApplyResult:
        if self.config is None:
            raise APIKeyAuthError(
                "APIKeyQueryMethod.apply: config not set; constructed without "
                "param_name."
            )
        key = credentials.get("key") or credentials.get("api_key")
        if not key:
            raise APIKeyAuthError(
                "APIKeyQueryMethod.apply: credentials missing key. The "
                "artifact's `key_ref` MUST resolve to a non-empty value."
            )
        # Per §2.4.2 disrecommendation; emit once per process via the
        # standard logging surface so the dispatcher's audit pipeline
        # can capture it without polluting stdout.
        log.warning(
            "api_key_query is disrecommended per §2.4.2 (key leaks into "
            "logs / browser history / proxy logs); prefer api_key_header "
            "when the provider accepts both."
        )
        return AuthApplyResult(query={self.config.param_name: str(key)})
