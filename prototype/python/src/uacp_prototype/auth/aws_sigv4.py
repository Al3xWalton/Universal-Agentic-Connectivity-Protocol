"""AWS Signature Version 4 per §2.5.1.

Stub for Stage 8c (AWS S3 session). Calling any function in this module
raises NotImplementedError until that session lands.
"""

from __future__ import annotations


def sign_request(*args: object, **kwargs: object) -> None:
    raise NotImplementedError(
        "aws_sigv4 is implemented in Stage 8c (AWS S3 provider session); "
        "this stub is intentional in Stage 8a."
    )
