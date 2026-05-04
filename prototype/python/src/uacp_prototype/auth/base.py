"""AuthMethod Protocol shared by every registered authentication method.

Per §2.1 the artifact's `authentication.method` field selects the method;
each registered method has its own module under `auth/` that implements
the Protocol below. The dispatch runtime composes the request through
Stage 4 §4.1's composition order: the authentication subsystem applies
its credential-bearing fields LAST, after default headers and operation
overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx


@dataclass
class AuthApplyResult:
    """The output of `AuthMethod.apply` — what the dispatcher sets on the
    outgoing request. The dispatcher merges these into the wire request per
    §4.1 step 7. The AuthMethod returns the deltas; the dispatcher applies.
    """

    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    # `body_transform` is reserved for signed-request schemes that need to
    # alter the body (e.g., signature over the canonical body). v1 OAuth
    # methods do not use it.
    body_transform: Any = None


@runtime_checkable
class AuthMethod(Protocol):
    """Protocol every registered auth method implements.

    Methods MAY return a coroutine if the auth flow needs network IO; the
    dispatcher awaits as appropriate. For OAuth 2.0 authcode the flows are
    network-bearing (token exchange, refresh) and async; static API-key
    methods return synchronously.
    """

    method: str

    def apply(self, request: httpx.Request, *, credentials: dict[str, Any]) -> AuthApplyResult:
        """Compute the credential-bearing fields the dispatcher sets on the
        outgoing wire request.
        """
        ...


__all__ = ["AuthMethod", "AuthApplyResult"]
