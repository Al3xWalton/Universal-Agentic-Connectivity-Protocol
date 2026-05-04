"""Pagination loops per §4.4.

Stage 3 declared the metadata (cursor / offset / link_header / none); this
module turns that metadata into a runtime iterator. ``dispatch_paginated``
yields each page's body until the loop terminates (per-pattern rules from
§4.4) or hits the safety limit (default 100 pages, configurable).

JSONPath in the cursor pattern is implemented as a minimal subset
sufficient for the v1.0 patterns: ``$.field`` and ``$.field.subfield``
are supported. Full JSONPath (RFC 9535) is out of scope for the
prototype; the field-path subset covers every Google API surface and
the great majority of public APIs.
"""

from __future__ import annotations

import re
from typing import Any, Iterator
from urllib.parse import urlparse

import httpx

from ..spec.models import (
    CursorPagination,
    LinkHeaderPagination,
    NoPagination,
    OffsetPagination,
    Operation,
)
from .client import (
    DispatchClient,
    DispatchError,
    DispatchSuccess,
)


DEFAULT_MAX_PAGES = 100


class PaginationError(Exception):
    """Pagination-specific errors that don't map cleanly to DispatchError."""


def _resolve_jsonpath(body: Any, path: str) -> Any:
    """Resolve a minimal JSONPath against `body`.

    Supports ``$.field`` and ``$.field.subfield`` — the field-path subset
    that covers the Google APIs and the typical long-tail. Returns None
    when any segment is missing.
    """
    if not path.startswith("$."):
        raise PaginationError(f"unsupported JSONPath {path!r}; must start with $.")
    segments = path[2:].split(".")
    current: Any = body
    for seg in segments:
        if not isinstance(current, dict):
            return None
        if seg not in current:
            return None
        current = current[seg]
    return current


def _has_more_offset(body: Any, pag: OffsetPagination, *, page_size: int, current_offset: int, declared_limit: int) -> bool:
    """Determine whether the offset loop should continue per §4.4."""
    # response_has_more_path takes precedence per §3.4 / §4.4
    if pag.response_has_more_path is not None:
        v = _resolve_jsonpath(body, pag.response_has_more_path)
        if isinstance(v, bool):
            return v
        return False
    if pag.response_total_path is not None:
        total = _resolve_jsonpath(body, pag.response_total_path)
        if isinstance(total, int):
            return current_offset + page_size < total
    # Heuristic per §4.4: a partial page with no has_more terminates.
    return page_size >= declared_limit


def _parse_link_header(header: str) -> dict[str, str]:
    """Parse RFC 8288 Link header into rel → URI mapping."""
    out: dict[str, str] = {}
    # Naive but adequate parser for typical inputs:
    # `<url>; rel="next", <url2>; rel="prev"`
    parts = re.split(r",\s*(?=<)", header)
    for part in parts:
        m = re.match(r'\s*<([^>]+)>\s*;\s*rel="?([^",]+)"?', part)
        if m:
            out[m.group(2)] = m.group(1)
    return out


def dispatch_paginated(
    client: DispatchClient,
    operation_id: str,
    *,
    path_params: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    body: Any = None,
    extra_headers: dict[str, str] | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> Iterator[DispatchSuccess]:
    """Dispatch the operation and yield each page.

    The iterator stops when one of:
    - The pagination pattern signals end-of-data per §4.4.
    - The safety max-pages limit is reached. The final iteration yields
      a synthesized DispatchError indicating the limit was hit.
    - A dispatch failure occurs. The iterator yields the failure as the
      last item (the DispatchError type) and stops.
    """
    op = client._operation(operation_id)  # accessing internal lookup is fine; same module-internal contract
    if op.pagination is None or isinstance(op.pagination, NoPagination):
        result = client.dispatch(
            operation_id,
            path_params=path_params,
            query=query,
            body=body,
            extra_headers=extra_headers,
        )
        yield result  # type: ignore[misc]  (DispatchSuccess | DispatchError)
        return

    pages_yielded = 0
    current_query = dict(query or {})
    current_extra_headers = dict(extra_headers or {})

    # Forward URL for link_header pattern (overrides path/query for next pages)
    link_header_next: str | None = None

    while True:
        if pages_yielded >= max_pages:
            yield DispatchError(  # type: ignore[misc]
                status=0,
                code="upstream_error",
                message=f"pagination max-pages safety limit ({max_pages}) reached",
                details={"reason": "pagination_max_pages"},
            )
            return

        if link_header_next is not None:
            # Issue a raw GET against the next URL — same headers, same auth.
            # Reuse the dispatch surface by overriding via an internal hook.
            result = _dispatch_raw_url(client, op, link_header_next, current_extra_headers)
        else:
            result = client.dispatch(
                operation_id,
                path_params=path_params,
                query=current_query,
                body=body,
                extra_headers=current_extra_headers,
            )

        if isinstance(result, DispatchError):
            yield result
            return

        yield result
        pages_yielded += 1

        if isinstance(op.pagination, CursorPagination):
            next_cursor = _resolve_jsonpath(result.body, op.pagination.response_cursor_path)
            if not next_cursor:
                return
            prior = current_query.get(op.pagination.request_cursor_parameter)
            if next_cursor == prior:
                # The Provider returned the same cursor — degenerate end-state per §4.4
                return
            current_query[op.pagination.request_cursor_parameter] = next_cursor

        elif isinstance(op.pagination, OffsetPagination):
            page_size = _page_size(result.body)
            current_offset = int(current_query.get(op.pagination.request_offset_parameter, 0))
            declared_limit = int(
                current_query.get(
                    op.pagination.request_limit_parameter,
                    _default_limit(op, op.pagination),
                )
            )
            if not _has_more_offset(
                result.body,
                op.pagination,
                page_size=page_size,
                current_offset=current_offset,
                declared_limit=declared_limit,
            ):
                return
            current_query[op.pagination.request_offset_parameter] = current_offset + page_size

        elif isinstance(op.pagination, LinkHeaderPagination):
            link_header = result.headers.get("link") or result.headers.get("Link")
            if not link_header:
                return
            rels = _parse_link_header(link_header)
            next_url = rels.get("next")
            if not next_url:
                return
            # Cross-origin check — surface a warning and end the loop per §4.4
            base_origin = urlparse(client.artifact.dispatch.base_url).netloc
            target_origin = urlparse(next_url).netloc
            if base_origin != target_origin:
                yield DispatchError(  # type: ignore[misc]
                    status=0,
                    code="upstream_error",
                    message=(
                        f"link_header pagination next URI {next_url!r} is cross-origin "
                        f"({target_origin} vs {base_origin}); ending loop per §4.4"
                    ),
                    details={"reason": "pagination_cross_origin"},
                )
                return
            link_header_next = next_url

        else:
            return


def _page_size(body: Any) -> int:
    """Heuristic: count the largest array in the response. The §4.4 offset
    semantics use the actual returned page size to advance the offset.
    """
    if not isinstance(body, dict):
        return 0
    largest = 0
    for v in body.values():
        if isinstance(v, list):
            largest = max(largest, len(v))
    return largest


def _default_limit(op: Operation, pag: OffsetPagination) -> int:
    """Look up the operation's declared default for the limit parameter."""
    if op.request.query_parameters is None:
        return 100
    properties = op.request.query_parameters.get("properties") or {}
    limit_prop = properties.get(pag.request_limit_parameter, {})
    return int(limit_prop.get("default", 100))


def _dispatch_raw_url(
    client: DispatchClient,
    op: Operation,
    url: str,
    extra_headers: dict[str, str],
) -> DispatchSuccess | DispatchError:
    """Issue a request against an absolute URL — used by link_header
    pagination's rel=next path. Bypasses path-template substitution but
    runs through the full retry / auth / redirect envelope.

    For the prototype, this delegates to the underlying httpx client
    after applying auth via the client's auth_method. Full retry
    coverage of these calls is left as a future-extension affordance.
    """
    credentials = client.credential_resolver()
    auth_input = httpx.Request(op.request.method, url)
    auth_result = client.auth_method.apply(auth_input, credentials=credentials)
    headers = dict(client.artifact.dispatch.default_headers)
    if client.artifact.dispatch.default_user_agent:
        headers.setdefault("User-Agent", client.artifact.dispatch.default_user_agent)
    headers.update(extra_headers)
    headers.update(auth_result.headers)
    response = client._client.request(  # noqa: SLF001 — internal access scoped to this module
        op.request.method, url, headers=headers
    )
    if 200 <= response.status_code < 300:
        return client._build_success(response)  # noqa: SLF001
    return client._build_error(op, response)  # noqa: SLF001


__all__ = [
    "DEFAULT_MAX_PAGES",
    "PaginationError",
    "dispatch_paginated",
]
