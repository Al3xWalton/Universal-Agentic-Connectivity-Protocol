"""Tests for refresh policies per §5.2 + rotation per §5.3."""

from __future__ import annotations

import time
from typing import Any

import pytest

from uacp_prototype.auth.oauth2_authcode import OAuth2Error, TokenSet
from uacp_prototype.lifecycle.refresh import (
    REFRESH_WINDOW_FLOOR_SECONDS,
    is_expired,
    is_in_refresh_window,
    refresh_with_rotation,
)
from uacp_prototype.lifecycle.state import Connection, ConnectionState


# ---------------------------------------------------------------------------
# Refresh window (§5.2)
# ---------------------------------------------------------------------------


def test_window_uses_60s_floor_for_short_lived_tokens() -> None:
    # 5-minute access token; 10% is 30s, but the floor is 60s.
    # Token expires_at 100s from now → remaining = 100s, threshold = 60s,
    # so NOT in window yet.
    now = 1_000_000.0
    assert is_in_refresh_window(now + 100, now=now, expires_in_hint=300) is False
    # Token expires_at 30s from now → in window (remaining < 60s).
    assert is_in_refresh_window(now + 30, now=now, expires_in_hint=300) is True


def test_window_uses_10pct_for_long_lived_tokens() -> None:
    # 1-hour token; 10% is 360s, larger than the 60s floor.
    now = 1_000_000.0
    # Remaining = 400s; threshold = 360s; NOT in window.
    assert is_in_refresh_window(now + 400, now=now, expires_in_hint=3600) is False
    # Remaining = 300s; threshold = 360s; in window.
    assert is_in_refresh_window(now + 300, now=now, expires_in_hint=3600) is True


def test_window_already_expired_counts_as_in_window() -> None:
    now = 1_000_000.0
    assert is_in_refresh_window(now - 10, now=now, expires_in_hint=3600) is True


def test_window_no_expiry_returns_false() -> None:
    """Non-expiring credentials never enter the refresh window."""
    assert is_in_refresh_window(None) is False


def test_is_expired_basic() -> None:
    now = 1_000_000.0
    assert is_expired(now - 1, now=now) is True
    assert is_expired(now + 1, now=now) is False
    assert is_expired(None) is False


# ---------------------------------------------------------------------------
# refresh_with_rotation — happy paths
# ---------------------------------------------------------------------------


def _conn() -> Connection:
    c = Connection(
        connection_id="conn-x",
        state=ConnectionState.REFRESHING,
        access_token_expires_at=time.time() - 10,
    )
    return c


def test_refresh_success_with_rotation() -> None:
    """Provider returns new refresh_token → new one is persisted; prior is
    discarded; connection ends in active."""
    c = _conn()
    persisted: dict[str, Any] = {}

    def refresh_callable(rt: str) -> TokenSet:
        assert rt == "rt-old"
        return TokenSet(
            access_token="at-new",
            refresh_token="rt-new",
            expires_at=time.time() + 3600,
        )

    def persist(connection: Connection, tokens: TokenSet, new_refresh: str | None) -> None:
        persisted["access"] = tokens.access_token
        persisted["refresh"] = new_refresh

    outcome = refresh_with_rotation(
        c,
        prior_refresh_token="rt-old",
        refresh_callable=refresh_callable,
        persist_callable=persist,
    )

    assert outcome.success is True
    assert outcome.tokens is not None
    assert outcome.tokens.access_token == "at-new"
    assert outcome.prior_refresh_token_retained is False
    assert persisted == {"access": "at-new", "refresh": "rt-new"}
    assert c.state == ConnectionState.ACTIVE
    assert c.last_refreshed_at is not None


def test_refresh_success_without_rotation_retains_prior() -> None:
    """Provider omits refresh_token → prior is retained per §5.3."""
    c = _conn()
    persisted: dict[str, Any] = {}

    def refresh_callable(rt: str) -> TokenSet:
        return TokenSet(
            access_token="at-new",
            refresh_token=None,  # no rotation
            expires_at=time.time() + 3600,
        )

    def persist(connection: Connection, tokens: TokenSet, new_refresh: str | None) -> None:
        persisted["access"] = tokens.access_token
        persisted["refresh"] = new_refresh

    outcome = refresh_with_rotation(
        c,
        prior_refresh_token="rt-old",
        refresh_callable=refresh_callable,
        persist_callable=persist,
    )

    assert outcome.success is True
    assert outcome.prior_refresh_token_retained is True
    assert persisted["refresh"] is None  # signal to caller: keep prior
    assert c.state == ConnectionState.ACTIVE


# ---------------------------------------------------------------------------
# refresh_with_rotation — failure paths
# ---------------------------------------------------------------------------


def test_invalid_grant_revokes_connection() -> None:
    """invalid_grant is the §5.4 definitive-revocation indication."""
    c = _conn()

    def refresh_callable(_rt: str) -> TokenSet:
        raise OAuth2Error("token endpoint returned 400: {'error': 'invalid_grant'}")

    def persist(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("persist must not be called on invalid_grant")

    outcome = refresh_with_rotation(
        c,
        prior_refresh_token="rt-stale",
        refresh_callable=refresh_callable,
        persist_callable=persist,
    )

    assert outcome.success is False
    assert outcome.revoked is True
    assert c.state == ConnectionState.REVOKED


def test_transient_refresh_failure_returns_to_expired() -> None:
    """Transport / unknown failures leave the connection in expired so a
    future dispatch can re-attempt."""
    c = _conn()

    def refresh_callable(_rt: str) -> TokenSet:
        raise OAuth2Error("transport error: connection reset")

    def persist(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("persist must not be called on failure")

    outcome = refresh_with_rotation(
        c,
        prior_refresh_token="rt-old",
        refresh_callable=refresh_callable,
        persist_callable=persist,
    )

    assert outcome.success is False
    assert outcome.revoked is False
    assert c.state == ConnectionState.EXPIRED


def test_persistence_failure_transitions_to_error() -> None:
    """Persistence write failure leaves the lifecycle in `error` so the
    caller can investigate. The new tokens are LOST in this case (the
    canonical "connection-killing bug" §5.3 warns against), but the
    persist_callable's exception ensures the lifecycle records the
    failure rather than silently advancing."""
    c = _conn()

    def refresh_callable(_rt: str) -> TokenSet:
        return TokenSet(
            access_token="at-new",
            refresh_token="rt-new",
            expires_at=time.time() + 3600,
        )

    def persist(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("storage unavailable")

    outcome = refresh_with_rotation(
        c,
        prior_refresh_token="rt-old",
        refresh_callable=refresh_callable,
        persist_callable=persist,
    )

    assert outcome.success is False
    assert "persistence failed" in (outcome.error_message or "")
    assert c.state == ConnectionState.ERROR


def test_refresh_called_in_wrong_state_raises() -> None:
    """The caller MUST transition the connection to refreshing before
    invoking refresh_with_rotation. Calling from any other state is a
    programming error."""
    c = Connection(connection_id="x", state=ConnectionState.ACTIVE)

    def refresh_callable(_rt: str) -> TokenSet:
        pytest.fail("refresh must not be invoked")

    def persist(*_a: Any, **_kw: Any) -> None:
        pytest.fail()

    with pytest.raises(RuntimeError, match="expected refreshing"):
        refresh_with_rotation(
            c,
            prior_refresh_token="rt",
            refresh_callable=refresh_callable,
            persist_callable=persist,
        )
