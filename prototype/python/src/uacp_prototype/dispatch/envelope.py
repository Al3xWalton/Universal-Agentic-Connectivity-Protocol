"""Body-predicate failure detection per §3.3 + §4.6.

When a response carries a ``failure_predicate``, the dispatcher MUST
evaluate the predicate against the response body before treating the
response as a success — even on a 2xx HTTP status. A predicate match
converts the response from `DispatchSuccess` to `DispatchError`,
regardless of the HTTP status.

This module is the §4.6 hook the dispatch client invokes after a 2xx
response; without a `failure_predicate` declared on the matching
response entry, the dispatcher's existing 2xx → success behavior is
unchanged.

Permits providers whose API surface wraps both logical success and
logical failure in the same HTTP status (typically 200 with
``{ok: false, error: "..."}`` for Slack, ``{errors: [...]}`` for
GraphQL providers, ``{success: false, ...}`` for several enterprise
APIs). Stage 8b validation against Slack surfaced this gap; the spec
amendment is in §3.3 (the `failure_predicate` field) + §4.6 (the
evaluation rule).
"""

from __future__ import annotations

from typing import Any

from ..spec.models import FailurePredicate, ResponseEntry


def resolve_jsonpath(body: Any, path: str) -> Any:
    """Resolve the §3.4 / §3.3 minimal JSONPath subset against a body.

    Supports ``$.field`` and ``$.field.subfield``. Returns None when any
    segment is missing. Identical contract to
    ``dispatch.pagination._resolve_jsonpath``; duplicated here so that
    the envelope module doesn't import from pagination (the layering
    keeps a clean dependency graph: pagination consumes envelope, not
    the other way around).
    """
    if not path.startswith("$."):
        raise ValueError(f"unsupported JSONPath {path!r}; must start with $.")
    segments = path[2:].split(".")
    current: Any = body
    for seg in segments:
        if not isinstance(current, dict):
            return None
        if seg not in current:
            return None
        current = current[seg]
    return current


def evaluate_failure_predicate(predicate: FailurePredicate, body: Any) -> bool:
    """Return True when the predicate's path resolves to a value equal to
    `predicate.equals` — i.e., the response is a logical failure.
    """
    resolved = resolve_jsonpath(body, predicate.path)
    return resolved == predicate.equals


def select_response_entry(
    response_dict: dict[str, ResponseEntry], status: int
) -> ResponseEntry | None:
    """Pick the response entry matching `status` per §3.3.

    Resolution order: exact 3-digit match > status-range match (1xx-5xx)
    > "default". Returns None when no entry matches.
    """
    exact = str(status)
    if exact in response_dict:
        return response_dict[exact]
    range_key = f"{status // 100}xx"
    if range_key in response_dict:
        return response_dict[range_key]
    if "default" in response_dict:
        return response_dict["default"]
    return None


def extract_failure_details(
    predicate: FailurePredicate, body: Any
) -> tuple[str | None, str | None]:
    """Extract the (provider_code, message) pair from a body when the
    predicate's optional `code_path` and `message_path` are populated.
    Returns (None, None) when the optional fields are absent or unresolved.
    """
    code: str | None = None
    message: str | None = None
    if predicate.code_path is not None:
        v = resolve_jsonpath(body, predicate.code_path)
        if isinstance(v, str):
            code = v
    if predicate.message_path is not None:
        v = resolve_jsonpath(body, predicate.message_path)
        if isinstance(v, str):
            message = v
    return code, message


__all__ = [
    "evaluate_failure_predicate",
    "extract_failure_details",
    "resolve_jsonpath",
    "select_response_entry",
]
