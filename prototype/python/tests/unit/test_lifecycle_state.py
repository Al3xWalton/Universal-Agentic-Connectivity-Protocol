"""Tests for the connection state machine per §5.1."""

from __future__ import annotations

import pytest

from uacp_prototype.lifecycle.state import (
    Connection,
    ConnectionState,
    InvalidTransition,
)


def _conn(state: ConnectionState = ConnectionState.PENDING) -> Connection:
    c = Connection(connection_id="conn-1")
    c.state = state
    return c


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_default_initial_state_is_pending() -> None:
    c = Connection(connection_id="x")
    assert c.state == ConnectionState.PENDING


# ---------------------------------------------------------------------------
# Spec-mandated transitions
# ---------------------------------------------------------------------------


def test_pending_to_active_succeeds() -> None:
    c = _conn(ConnectionState.PENDING)
    c.mark_active()
    assert c.state == ConnectionState.ACTIVE


def test_active_to_revoked_succeeds() -> None:
    c = _conn(ConnectionState.ACTIVE)
    c.mark_revoked()
    assert c.state == ConnectionState.REVOKED


def test_refreshing_to_active_succeeds() -> None:
    c = _conn(ConnectionState.REFRESHING)
    c.mark_active()
    assert c.state == ConnectionState.ACTIVE


def test_refreshing_to_revoked_succeeds() -> None:
    c = _conn(ConnectionState.REFRESHING)
    c.mark_revoked()
    assert c.state == ConnectionState.REVOKED


def test_expired_to_refreshing_succeeds() -> None:
    c = _conn(ConnectionState.EXPIRED)
    c.mark_refreshing()
    assert c.state == ConnectionState.REFRESHING


def test_active_to_expiring_to_refreshing_to_active() -> None:
    c = _conn(ConnectionState.ACTIVE)
    c.mark_expiring()
    c.mark_refreshing()
    c.mark_active()
    assert c.state == ConnectionState.ACTIVE


# ---------------------------------------------------------------------------
# Revoked is terminal
# ---------------------------------------------------------------------------


def test_revoked_to_active_is_invalid() -> None:
    c = _conn(ConnectionState.REVOKED)
    with pytest.raises(InvalidTransition, match="revoked"):
        c.mark_active()


def test_revoked_to_refreshing_is_invalid() -> None:
    c = _conn(ConnectionState.REVOKED)
    with pytest.raises(InvalidTransition):
        c.mark_refreshing()


def test_revoked_to_expired_is_invalid() -> None:
    c = _conn(ConnectionState.REVOKED)
    with pytest.raises(InvalidTransition):
        c.mark_expired()


def test_is_terminal_true_for_revoked() -> None:
    c = _conn(ConnectionState.REVOKED)
    assert c.is_terminal() is True


def test_is_terminal_false_for_error() -> None:
    """error is transient-terminal but not truly terminal — refresh can recover."""
    c = _conn(ConnectionState.ERROR)
    assert c.is_terminal() is False


# ---------------------------------------------------------------------------
# Error → refreshing (auto-retry from error per §5.1)
# ---------------------------------------------------------------------------


def test_error_to_refreshing_succeeds() -> None:
    c = _conn(ConnectionState.ERROR)
    c.mark_refreshing()
    assert c.state == ConnectionState.REFRESHING


def test_error_to_revoked_succeeds() -> None:
    """User abandons recovery from error and disconnects."""
    c = _conn(ConnectionState.ERROR)
    c.mark_revoked()
    assert c.state == ConnectionState.REVOKED


def test_error_to_active_directly_is_invalid() -> None:
    """Recovery from error MUST go through refreshing first."""
    c = _conn(ConnectionState.ERROR)
    with pytest.raises(InvalidTransition):
        c.mark_active()


# ---------------------------------------------------------------------------
# is_dispatchable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state,dispatchable",
    [
        (ConnectionState.PENDING, False),
        (ConnectionState.ACTIVE, True),
        (ConnectionState.EXPIRING, True),
        (ConnectionState.REFRESHING, False),
        (ConnectionState.EXPIRED, False),
        (ConnectionState.REVOKED, False),
        (ConnectionState.ERROR, False),
    ],
)
def test_is_dispatchable(state: ConnectionState, dispatchable: bool) -> None:
    c = _conn(state)
    assert c.is_dispatchable() is dispatchable


# ---------------------------------------------------------------------------
# Pending failure paths
# ---------------------------------------------------------------------------


def test_pending_to_revoked_is_valid() -> None:
    """User cancels the auth flow before it completes."""
    c = _conn(ConnectionState.PENDING)
    c.mark_revoked()
    assert c.state == ConnectionState.REVOKED


def test_pending_to_error_is_valid() -> None:
    """Auth flow timed out or returned an unexpected response."""
    c = _conn(ConnectionState.PENDING)
    c.mark_error(reason="timeout")
    assert c.state == ConnectionState.ERROR
    assert c.error_history == [{"reason": "timeout"}]


def test_pending_to_expiring_is_invalid() -> None:
    """Pending has no access token to expire yet."""
    c = _conn(ConnectionState.PENDING)
    with pytest.raises(InvalidTransition):
        c.mark_expiring()


# ---------------------------------------------------------------------------
# Generic transition() entry
# ---------------------------------------------------------------------------


def test_transition_method_routes_through_table() -> None:
    c = _conn(ConnectionState.ACTIVE)
    c.transition(ConnectionState.EXPIRED)
    assert c.state == ConnectionState.EXPIRED
