"""Connection state machine per §5.1.

Seven states: pending, active, expiring, refreshing, expired, revoked, error.
Spec-mandated transitions are implemented as explicit methods on the
Connection class; invalid transitions raise InvalidTransition. The state
machine is deliberately small — no implicit transitions; every move is
caller-driven.

The class focuses on state correctness; persistence per §5.6 is handled
by the implementation's chosen storage backend (the prototype uses the
filesystem-simulated local-keyring; see security/secrets.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ConnectionState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRING = "expiring"
    REFRESHING = "refreshing"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


# Transitions allowed per §5.1. The set is the spec-mandated transitions
# plus the implementation-discretion transitions the prototype implements.
# revoked is terminal (no out-edges except via re-auth, which produces a
# fresh Connection — see lifecycle/refresh.py).
_ALLOWED_TRANSITIONS: dict[ConnectionState, set[ConnectionState]] = {
    ConnectionState.PENDING: {
        ConnectionState.ACTIVE,
        ConnectionState.REVOKED,
        ConnectionState.ERROR,
    },
    ConnectionState.ACTIVE: {
        ConnectionState.EXPIRING,
        ConnectionState.EXPIRED,
        ConnectionState.REFRESHING,
        ConnectionState.REVOKED,
        ConnectionState.ERROR,
    },
    ConnectionState.EXPIRING: {
        ConnectionState.REFRESHING,
        ConnectionState.ACTIVE,  # rare: window passed without refresh
        ConnectionState.EXPIRED,
        ConnectionState.REVOKED,
        ConnectionState.ERROR,
    },
    ConnectionState.REFRESHING: {
        ConnectionState.ACTIVE,
        ConnectionState.EXPIRED,  # transient refresh failure; will retry
        ConnectionState.REVOKED,
        ConnectionState.ERROR,
    },
    ConnectionState.EXPIRED: {
        ConnectionState.REFRESHING,
        ConnectionState.REVOKED,
        ConnectionState.ERROR,
    },
    ConnectionState.ERROR: {
        ConnectionState.REFRESHING,
        ConnectionState.REVOKED,
    },
    ConnectionState.REVOKED: set(),  # terminal
}


class InvalidTransition(Exception):
    """Raised on an attempted state transition not permitted by §5.1."""


@dataclass
class Connection:
    """Runtime model of a single Connection.

    Identifier-stable across re-auth per §5.5. The credential material is
    referenced by `secret_refs` (resolved via security.secrets at dispatch
    time) and the lifecycle layer tracks expiry timestamps for the
    refresh-window logic in `refresh.py`.

    Per §5.6, `connection_id`, `state`, the artifact reference, and the
    expiry timestamps MUST be persisted to durable storage. The dataclass
    holds the in-memory shape; the persistence binding is the
    implementation's responsibility.
    """

    connection_id: str
    state: ConnectionState = ConnectionState.PENDING
    secret_refs: dict[str, str] = field(default_factory=dict)
    access_token_expires_at: float | None = None  # Unix seconds; None for non-expiring
    refresh_token_expires_at: float | None = None
    last_dispatched_at: float | None = None
    scopes_granted: tuple[str, ...] | None = None
    last_refreshed_at: float | None = None
    error_history: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def transition(self, target: ConnectionState) -> None:
        """Move to `target`. Raises InvalidTransition if not permitted."""
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransition(
                f"transition {self.state.value} → {target.value} is not permitted "
                f"by §5.1; revoked is terminal and recovery requires re-auth"
            )
        self.state = target

    # Convenience accessors for the canonical transitions per §5.1. These
    # exist because they're the lifecycle's most-frequently-used calls and
    # naming them clearly tightens the call sites.

    def mark_active(self) -> None:
        self.transition(ConnectionState.ACTIVE)

    def mark_expiring(self) -> None:
        self.transition(ConnectionState.EXPIRING)

    def mark_refreshing(self) -> None:
        self.transition(ConnectionState.REFRESHING)

    def mark_expired(self) -> None:
        self.transition(ConnectionState.EXPIRED)

    def mark_revoked(self) -> None:
        self.transition(ConnectionState.REVOKED)

    def mark_error(self, *, reason: str | None = None) -> None:
        if reason is not None:
            self.error_history.append({"reason": reason})
        self.transition(ConnectionState.ERROR)

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        """True if no auto-transition out is possible. Only `revoked` is
        truly terminal per §5.1; `error` is transient-terminal and can
        recover via auto-retry or manual re-auth.
        """
        return self.state == ConnectionState.REVOKED

    def is_dispatchable(self) -> bool:
        """True if dispatch attempts are permitted in the current state.
        Per §5.1, dispatch is permitted in `active` and `expiring`; the
        refresh-then-retry path handles `expired`/`refreshing`/`error`
        recovery.
        """
        return self.state in (ConnectionState.ACTIVE, ConnectionState.EXPIRING)


__all__ = [
    "Connection",
    "ConnectionState",
    "InvalidTransition",
]
