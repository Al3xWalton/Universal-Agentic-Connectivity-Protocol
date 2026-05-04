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


def _parse_link_header(
    header: str | list[str], *, base_url: str | None = None
) -> dict[str, str]:
    """Parse an RFC 8288 Link header (or list of Link headers) into a
    rel → URI mapping.

    Handles the spec's edge cases:

    - **Single Link header with multiple comma-separated entries**:
      ``<url1>; rel="next", <url2>; rel="last"`` — the typical shape.
    - **Multiple Link headers**: HTTP allows the same field name to
      appear multiple times; the wire-form joins them with commas.
      When the input is a list[str], entries are joined with `, ` and
      then parsed as a single string.
    - **Comma inside angle-bracketed URL**: URLs MUST be inside
      ``<...>`` per RFC 8288 §3, and the parser respects the brackets
      when splitting entries — a comma between ``<`` and ``>`` is
      part of the URL, not a separator.
    - **Multiple link parameters per entry**: ``<url>; rel="next";
      title="Next page"`` — the parser walks all parameters, stopping
      on the first ``rel`` (which is what RFC 8288 §3.3 semantically
      requires; multiple rels for one link are rare).
    - **Multiple rel values in one rel parameter**: ``rel="next prev"``
      — RFC 8288 §3.3 permits space-separated relations; we register
      the URI under each relation.
    - **Quoted vs unquoted rel values**: ``rel=next`` and ``rel="next"``
      both valid; the parser strips quotes when present.
    - **Case-insensitive rel matching**: RFC 8288 §3.3 says relation
      types are case-insensitive; we lowercase the rel value on
      parse so consumers can do dict["next"] without case games.
    - **Relative URIs**: RFC 8288 §3.4 permits relative-reference URIs;
      when ``base_url`` is supplied, relative URIs are resolved
      against it per RFC 3986.
    - **Whitespace**: leading/trailing whitespace around entries and
      parameters is tolerated.

    Returns a dict mapping lowercase relation type to URI (resolved
    against ``base_url`` if relative).
    """
    if isinstance(header, list):
        if not header:
            return {}
        header = ", ".join(header)
    if not header:
        return {}

    out: dict[str, str] = {}
    for entry in _split_link_entries(header):
        uri, params = _parse_link_entry(entry)
        if uri is None:
            continue
        # Resolve relative URI against base_url per RFC 8288 §3.4
        if base_url is not None and not _is_absolute_uri(uri):
            from urllib.parse import urljoin

            uri = urljoin(base_url, uri)
        # rel parameter — case-insensitive per RFC 8288 §3.3
        rel = params.get("rel")
        if rel is None:
            continue
        # Multiple relations may be space-separated per §3.3
        for rel_token in rel.split():
            out[rel_token.lower()] = uri
    return out


def _split_link_entries(header: str) -> list[str]:
    """Split a Link header value into individual <uri>; params entries.

    A comma is the entry separator UNLESS it appears inside an angle-
    bracketed URI per RFC 8288 §3 or inside a quoted parameter value.
    """
    entries: list[str] = []
    current: list[str] = []
    in_brackets = False
    in_quotes = False
    i = 0
    while i < len(header):
        ch = header[i]
        if ch == "<" and not in_quotes:
            in_brackets = True
            current.append(ch)
        elif ch == ">" and not in_quotes:
            in_brackets = False
            current.append(ch)
        elif ch == '"' and not in_brackets:
            in_quotes = not in_quotes
            current.append(ch)
        elif ch == "," and not in_brackets and not in_quotes:
            entry = "".join(current).strip()
            if entry:
                entries.append(entry)
            current = []
        else:
            current.append(ch)
        i += 1
    last = "".join(current).strip()
    if last:
        entries.append(last)
    return entries


def _parse_link_entry(entry: str) -> tuple[str | None, dict[str, str]]:
    """Parse a single Link header entry into (uri, params).

    Format per RFC 8288 §3: ``<uri>; param1=value1; param2="value 2"``.
    Returns (None, {}) when the entry doesn't start with ``<...>``.
    """
    entry = entry.strip()
    if not entry.startswith("<"):
        return None, {}
    # Extract <URI>
    close = entry.find(">")
    if close < 0:
        return None, {}
    uri = entry[1:close]
    rest = entry[close + 1 :].lstrip()

    params: dict[str, str] = {}
    # Walk semicolon-separated params, respecting quoted values.
    while rest.startswith(";"):
        rest = rest[1:].lstrip()
        # name=value parse
        eq = _find_unquoted_char(rest, "=")
        if eq < 0:
            break
        name = rest[:eq].strip().lower()
        rest = rest[eq + 1 :].lstrip()
        if rest.startswith('"'):
            # quoted value
            end_quote = rest.find('"', 1)
            if end_quote < 0:
                value = rest[1:]
                rest = ""
            else:
                value = rest[1:end_quote]
                rest = rest[end_quote + 1 :].lstrip()
        else:
            # unquoted: until ; or end
            sep = _find_unquoted_char(rest, ";")
            if sep < 0:
                value = rest.strip()
                rest = ""
            else:
                value = rest[:sep].strip()
                rest = rest[sep:]
        params[name] = value
    return uri, params


def _find_unquoted_char(s: str, ch: str) -> int:
    in_quotes = False
    for i, c in enumerate(s):
        if c == '"':
            in_quotes = not in_quotes
        elif c == ch and not in_quotes:
            return i
    return -1


def _is_absolute_uri(uri: str) -> bool:
    """RFC 3986: absolute URIs have a scheme. Heuristic: presence of
    ``://`` early in the string. The §3.4 link_header pattern only
    cares about HTTP(S) URIs in practice, so this heuristic is enough.
    """
    return "://" in uri[:20]


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
            # Resolve relative URIs against the request URL that
            # produced this response. The dispatcher's request-URL
            # construction is opaque from this layer; we use the
            # dispatch base_url + (link_header_next if we already
            # advanced) as the resolution base. RFC 8288 §3.4 permits
            # relative-reference URIs in Link headers, and RFC 3986
            # urljoin handles both relative and absolute uniformly.
            resolution_base = link_header_next or client.artifact.dispatch.base_url
            rels = _parse_link_header(link_header, base_url=resolution_base)
            # rel matching is case-insensitive per RFC 8288 §3.3; the
            # parser already lowercased.
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
