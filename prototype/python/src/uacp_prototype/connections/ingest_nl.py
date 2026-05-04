"""LLM-inference ingestion per §3.8.

Stub for Stage 8e (custom-auth provider session, which doubles as the
inference-path validation). Calling any function in this module raises
NotImplementedError until that session lands.
"""

from __future__ import annotations


def from_natural_language(*args: object, **kwargs: object) -> None:
    raise NotImplementedError(
        "LLM-inference ingestion is implemented in Stage 8e; "
        "this stub is intentional in Stage 8a."
    )
