"""Custom authentication escape hatch per §2.6.

Stub for Stage 8e (custom-auth provider session). Calling any function in this
module raises NotImplementedError until that session lands.
"""

from __future__ import annotations


def apply_custom_auth(*args: object, **kwargs: object) -> None:
    raise NotImplementedError(
        "custom_auth is implemented in Stage 8e (custom-auth provider session); "
        "this stub is intentional in Stage 8a."
    )
