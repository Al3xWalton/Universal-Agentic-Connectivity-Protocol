"""Refresh policies per §5.2 (lazy refresh in the prototype).

The prototype implements lazy refresh as the §5.2 floor (`MUST` for every
`Conforming Implementation`). Proactive refresh (`SHOULD`) and reactive
refresh (`MAY`) are both feasible extensions; the prototype focuses on
the minimum-conforming surface.

Two helpers:

  - `is_in_refresh_window(token_expires_at, now, expires_in_hint)` — per
    §5.2 the access token enters the refresh window when remaining
    lifetime is < max(60s, expires_in × 0.1).
  - `refresh_with_rotation(connection, refresh_token, refresh_callable,
    persist_callable)` — invokes the refresh callable, applies §5.3
    rotation atomically, and persists the new tokens. The
    refresh_callable is whatever the auth method's refresh function is
    (e.g., `oauth2_authcode.refresh`); the persist_callable handles the
    secret-store write.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..auth.oauth2_authcode import OAuth2Error, TokenSet
from .state import Connection, ConnectionState

REFRESH_WINDOW_FLOOR_SECONDS = 60
REFRESH_WINDOW_FRACTION = 0.10


def is_in_refresh_window(
    token_expires_at: float | None,
    *,
    now: float | None = None,
    expires_in_hint: float | None = None,
) -> bool:
    """Per §5.2: a token is in the refresh window when remaining lifetime is
    less than max(60s, expires_in × 0.1).

    `expires_in_hint` is the original `expires_in` from the token-endpoint
    response, used to compute the 10% slice. When unknown, fall back to
    the floor of 60s.
    """
    if token_expires_at is None:
        return False  # non-expiring credentials never enter the window
    now = now if now is not None else time.time()
    remaining = token_expires_at - now
    if remaining <= 0:
        return True  # already expired counts as in-window
    if expires_in_hint is not None:
        threshold = max(REFRESH_WINDOW_FLOOR_SECONDS, expires_in_hint * REFRESH_WINDOW_FRACTION)
    else:
        threshold = float(REFRESH_WINDOW_FLOOR_SECONDS)
    return remaining < threshold


def is_expired(token_expires_at: float | None, *, now: float | None = None) -> bool:
    if token_expires_at is None:
        return False
    now = now if now is not None else time.time()
    return token_expires_at <= now


@dataclass(frozen=True)
class RefreshOutcome:
    """Result of a refresh attempt.

    `tokens` is the new TokenSet on success; `prior_refresh_token_retained`
    is true when the provider did not rotate (the response omitted a new
    `refresh_token` field) and the caller should retain the prior one
    per §5.3.
    """

    success: bool
    tokens: TokenSet | None = None
    prior_refresh_token_retained: bool = False
    error_message: str | None = None
    revoked: bool = False


def refresh_with_rotation(
    connection: Connection,
    *,
    prior_refresh_token: str,
    refresh_callable: Callable[[str], TokenSet],
    persist_callable: Callable[[Connection, TokenSet, str | None], None],
) -> RefreshOutcome:
    """Execute the refresh exchange and apply §5.3 rotation atomically.

    `refresh_callable(refresh_token)` produces a TokenSet; failures raise
    OAuth2Error (or any subclass / equivalent the auth method defines).
    `persist_callable(connection, tokens, new_refresh_token)` writes the
    new credentials to durable storage; failure to persist surfaces as
    a refresh failure (the connection is left in `refreshing` and the
    caller decides whether to retry or transition to error).

    Behavior per §5.3:
      - If the refresh response includes a new `refresh_token`, the new
        one is persisted and the prior is discarded.
      - If the response omits `refresh_token`, the prior is RETAINED
        (the persist_callable is called with `new_refresh_token=None`,
        signaling "no rotation; keep prior").
      - If the response indicates `invalid_grant` (definitive
        revocation), the connection transitions to `revoked` and the
        outcome's `revoked` flag is true.
      - If the refresh fails for transport / unknown reasons, the
        outcome's `error_message` is populated and the connection
        is left in `refreshing` for the caller to handle.

    Concurrency is the caller's responsibility: per §5.1 the
    per-Connection single-flight lock around refresh ensures only one
    invocation of this function is in flight per Connection at a time.
    """
    # The state machine requires we be in REFRESHING before the exchange
    # starts. Caller is expected to call connection.mark_refreshing() prior.
    if connection.state != ConnectionState.REFRESHING:
        raise RuntimeError(
            f"refresh_with_rotation: connection {connection.connection_id} is "
            f"in state {connection.state.value}, expected refreshing. The "
            f"per-Connection single-flight lock ensures only one refresh "
            f"is in flight at a time (§5.1)."
        )

    try:
        tokens = refresh_callable(prior_refresh_token)
    except OAuth2Error as e:
        message = str(e)
        if "invalid_grant" in message:
            # Definitive revocation per §5.4.
            connection.mark_revoked()
            return RefreshOutcome(success=False, error_message=message, revoked=True)
        # Transient failure — go back to `expired` so a future dispatch
        # can retry. The caller MAY retry or transition to error.
        connection.mark_expired()
        return RefreshOutcome(success=False, error_message=message)
    except Exception as e:  # broader transport errors
        connection.mark_expired()
        return RefreshOutcome(success=False, error_message=f"transport error: {e}")

    # Success — atomically persist before flipping state to active.
    new_refresh_token = tokens.refresh_token  # None means no rotation per §5.3
    try:
        persist_callable(connection, tokens, new_refresh_token)
    except Exception as e:
        connection.mark_error(reason=f"persistence failed during refresh: {e}")
        return RefreshOutcome(
            success=False,
            error_message=f"persistence failed: {e}",
        )

    # Update lifecycle metadata
    connection.access_token_expires_at = tokens.expires_at
    connection.last_refreshed_at = time.time()

    connection.mark_active()
    return RefreshOutcome(
        success=True,
        tokens=tokens,
        prior_refresh_token_retained=(new_refresh_token is None),
    )


__all__ = [
    "REFRESH_WINDOW_FLOOR_SECONDS",
    "REFRESH_WINDOW_FRACTION",
    "RefreshOutcome",
    "is_expired",
    "is_in_refresh_window",
    "refresh_with_rotation",
]
