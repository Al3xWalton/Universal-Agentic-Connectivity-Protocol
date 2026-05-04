"""Load `.uacp` artifacts from disk or parsed-dict form.

Implements the user-facing entry point of Stage 3's load surface. The loader
reads a `.uacp` file (JSON), parses it through the pydantic models in
`models.py`, runs the cross-cutting semantic validations in `schema.py`,
and returns a fully-typed `UACPArtifact`.

Failure surfaces:
  - JSON parse failure → SpecValidationError wrapping the json error.
  - Pydantic validation failure → SpecValidationError wrapping the pydantic
    error message (with operation index pointing at the offending entry).
  - Spec validation failure (bidirectional path rule, embedded credentials,
    local-$ref rule, etc.) → SpecValidationError directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import UACPArtifact
from .schema import SpecValidationError, validate_artifact


def load(path: str | Path) -> UACPArtifact:
    """Load and validate a `.uacp` file from disk."""
    path = Path(path)
    if not path.exists():
        raise SpecValidationError(f"`.uacp` file not found: {path}")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SpecValidationError(f"`.uacp` file at {path} is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise SpecValidationError(
            f"`.uacp` file at {path} must be a JSON object at the top level; got {type(raw).__name__}"
        )
    return load_dict(raw, path=path)


def load_dict(raw: dict[str, Any], *, path: Path | None = None) -> UACPArtifact:
    """Load and validate an in-memory dict as if it were a `.uacp` file."""
    location = f" at {path}" if path is not None else ""
    try:
        artifact = UACPArtifact.model_validate(raw)
    except ValidationError as e:
        raise SpecValidationError(
            f"`.uacp` file{location} failed structural validation:\n{e}"
        ) from e

    try:
        validate_artifact(artifact, raw)
    except SpecValidationError:
        raise
    except Exception as e:  # pragma: no cover — defensive
        raise SpecValidationError(
            f"`.uacp` file{location} failed semantic validation: {e}"
        ) from e

    return artifact


__all__ = ["load", "load_dict"]
