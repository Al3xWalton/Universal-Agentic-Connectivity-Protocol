"""Pluggable HTTP transport abstraction per §4.10 (added in v1.1).

The dispatch runtime described in §4.1 — §4.9 specifies *what* a
Conforming Implementation does — HTTPS-only, retry policy, pagination
loops, rate-limit handling, error normalization, streaming patterns —
without naming the HTTP client library that carries the bytes. §4.10
codifies that any transport backend MAY be substituted provided the
externally-observable behavior at §4.1 — §4.9 is preserved.

This module exposes:

  - The ``Transport`` Protocol — the minimal surface the dispatch
    client consumes: ``request(method, url, *, headers, content,
    timeout) -> httpx.Response`` plus ``close()``. Returning an
    ``httpx.Response`` keeps the response-handling code in
    ``dispatch/client.py`` transport-agnostic — every backend adapts
    to that shape.
  - ``HttpxTransport`` — the default backend, wrapping ``httpx.Client``.
    Identical behavior to v1.0 dispatch.
  - ``ScraplingTransport`` — an optional anti-bot-evading backend
    backed by Scrapling's ``StealthyFetcher`` (Camoufox-driven). Lazy-
    imports Scrapling so the prototype runs without the optional
    ``stealth`` extras. Per §4.10 conformance, ScraplingTransport MUST
    preserve the §4.1 — §4.9 contract: HTTPS-only, retry behavior at
    the dispatch layer (not at the transport), no rate-limit bypass,
    canonical errors. The transport itself does NOT implement retry /
    rate-limit / pagination — those live above the transport boundary
    in ``dispatch/client.py`` and apply identically across backends.

Selection logic — which transport a given Connection uses — lives in
``dispatch/client.py`` (per §4.10's "selection mechanism is
implementation-defined" rule).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx


@runtime_checkable
class Transport(Protocol):
    """Minimal transport surface the dispatch client consumes.

    Every implementation MUST return an ``httpx.Response`` regardless
    of the underlying HTTP client. Backend-specific exception types
    MUST be re-raised as ``httpx.HTTPError`` / ``httpx.TimeoutException``
    so the dispatch loop's existing catch-blocks remain transport-
    neutral, per §4.10's "Backend-specific exception types MUST NOT
    leak past the dispatch boundary" rule.
    """

    name: str

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        timeout: float | None = None,
    ) -> httpx.Response: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# HttpxTransport — the v1.0 default, unchanged behavior.
# ---------------------------------------------------------------------------


class HttpxTransport:
    """Default transport backend wrapping ``httpx.Client``.

    Behavior matches the pre-v1.1 dispatch — every existing test that
    didn't specify a transport receives this implicitly. Per §4.10 the
    dispatch contract is preserved at the dispatch-loop level (not
    here); the transport only carries bytes.
    """

    name = "default"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout: float | None = None,
    ) -> None:
        self._owned = client is None
        if client is None:
            client = httpx.Client(
                timeout=timeout,
                follow_redirects=False,  # dispatch handles redirects per §4.2
            )
        self._client = client

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        return self._client.request(
            method,
            url,
            headers=headers,
            content=content,
            timeout=timeout,
        )

    def close(self) -> None:
        if self._owned:
            self._client.close()


# ---------------------------------------------------------------------------
# ScraplingTransport — anti-bot-evading backend (optional `stealth` extras).
# ---------------------------------------------------------------------------


class ScraplingNotInstalledError(RuntimeError):
    """Raised when ScraplingTransport is requested but the optional
    ``stealth`` extras are not installed.

    The error carries a clear remediation message — install the extras
    or fall back to HttpxTransport. Per §4.10's graceful-degradation
    posture, the dispatch client surfaces a user-visible warning when
    a requested transport isn't available.
    """


class ScraplingTransport:
    """Anti-bot-evading transport backed by Scrapling's StealthyFetcher.

    Scrapling provides Camoufox-driven (Firefox) and Chromium-driven
    fetchers with TLS fingerprint matching, browser-equivalent header
    ordering, and JS evaluation for challenge pages. This transport
    consumes the sync ``StealthyFetcher.get`` / ``post`` / etc. surface
    and adapts the result to an ``httpx.Response`` so the dispatch
    loop's response-handling code is unchanged.

    Per §4.10 conformance, this transport:

    - Carries bytes only — retry, rate-limit, pagination, and error
      normalization all live at the dispatch-loop layer above.
    - Re-raises backend exceptions as ``httpx.HTTPError`` /
      ``httpx.TimeoutException`` so dispatch catch-blocks remain
      backend-neutral.
    - Refuses ``http://`` URLs (HTTPS-only per §4.1 / §4.2). Scrapling
      itself permits HTTP, but UACP doesn't, so the check lands here.
    - Does NOT bypass ``Provider``-side rate limits as a feature. The
      stealth posture is about request-shape fingerprinting, not about
      ignoring 429 responses; backoff still applies at the dispatch
      layer.

    The Stage 11.0 implementation is intentionally minimal — it covers
    the request/response cycle that the prototype's session_cookie
    integration tests exercise. Future minor releases MAY extend the
    surface (cookie persistence across dispatches, JS-evaluated
    challenge handling, browser-pool reuse) provided the §4.10
    conformance posture is preserved.
    """

    name = "stealth"

    def __init__(
        self,
        *,
        timeout: float | None = None,
        cookies: list[dict[str, Any]] | None = None,
        headless: bool = True,
    ) -> None:
        # Lazy import — the prototype runs without the stealth extras.
        try:
            from scrapling.fetchers import StealthyFetcher  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover — import-time gate
            raise ScraplingNotInstalledError(
                "ScraplingTransport requires the optional 'stealth' "
                "extras. Install with `uv pip install "
                "uacp-prototype[stealth]` or `uv add "
                "--optional stealth scrapling`. To use the default "
                "HTTPX-backed transport instead, set "
                "dispatch.transport='default' in the .uacp file or "
                "omit the field entirely."
            ) from e

        self._fetcher = StealthyFetcher
        self._timeout = timeout
        self._cookies = cookies or []
        self._headless = headless

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        # §4.10 conformance: HTTPS-only enforced at the transport
        # boundary as defense-in-depth (the dispatch layer also checks).
        if not url.startswith("https://"):
            raise httpx.HTTPError(
                f"ScraplingTransport refuses non-HTTPS URL per §4.1 / §4.2: {url!r}"
            )

        effective_timeout = timeout if timeout is not None else self._timeout

        # Adapt the request to Scrapling's API. Scrapling's StealthyFetcher
        # exposes per-method functions (get, post, ...). For methods it
        # doesn't natively support, this implementation falls back with a
        # clear error rather than silently routing through GET/POST.
        method_upper = method.upper()
        try:
            if method_upper == "GET":
                resp = self._fetcher.get(
                    url,
                    headers=headers,
                    cookies=self._cookies or None,
                    timeout=effective_timeout,
                    headless=self._headless,
                )
            elif method_upper == "POST":
                resp = self._fetcher.post(
                    url,
                    headers=headers,
                    cookies=self._cookies or None,
                    data=content,
                    timeout=effective_timeout,
                    headless=self._headless,
                )
            elif method_upper == "PUT":
                resp = self._fetcher.put(
                    url,
                    headers=headers,
                    cookies=self._cookies or None,
                    data=content,
                    timeout=effective_timeout,
                    headless=self._headless,
                )
            elif method_upper == "DELETE":
                resp = self._fetcher.delete(
                    url,
                    headers=headers,
                    cookies=self._cookies or None,
                    timeout=effective_timeout,
                    headless=self._headless,
                )
            else:
                raise httpx.HTTPError(
                    f"ScraplingTransport does not support HTTP method "
                    f"{method!r}; this is a Stage 11.0 limitation. "
                    "Switch the operation to dispatch via dispatch.transport='default' "
                    "(httpx) or extend ScraplingTransport in a follow-up release."
                )
        except httpx.HTTPError:
            raise  # already canonical
        except TimeoutError as e:
            raise httpx.TimeoutException(str(e)) from e
        except Exception as e:  # pragma: no cover — best-effort wrapping
            raise httpx.HTTPError(
                f"ScraplingTransport: backend error: {type(e).__name__}: {e}"
            ) from e

        return _scrapling_response_to_httpx(method, url, headers, content, resp)

    def close(self) -> None:
        # StealthyFetcher's surface manages browser lifecycles per call;
        # there's no client-level resource to close in the v0.3 API.
        return None


def _scrapling_response_to_httpx(
    method: str,
    url: str,
    headers: dict[str, str] | None,
    content: bytes | None,
    scrapling_response: Any,
) -> httpx.Response:
    """Adapt Scrapling's response shape to an ``httpx.Response``.

    Scrapling's response carries ``status``, ``headers``, ``content``,
    ``text``, ``url``. We construct a real ``httpx.Response`` so the
    dispatch loop's existing handling code is reused without
    modification. Per §4.10's Backend-specific exception types MUST
    NOT leak rule, the response we return is an ordinary
    ``httpx.Response``.
    """
    status = getattr(scrapling_response, "status", None) or getattr(
        scrapling_response, "status_code", 0
    )
    body = getattr(scrapling_response, "content", None)
    if body is None:
        text = getattr(scrapling_response, "text", "")
        body = text.encode("utf-8") if isinstance(text, str) else bytes(text or b"")
    raw_headers = getattr(scrapling_response, "headers", None) or {}
    if hasattr(raw_headers, "items"):
        header_pairs = [(str(k), str(v)) for k, v in raw_headers.items()]
    else:
        header_pairs = [(str(k), str(v)) for k, v in raw_headers]
    request = httpx.Request(method, url, headers=headers, content=content)
    return httpx.Response(
        status_code=int(status),
        headers=header_pairs,
        content=bytes(body),
        request=request,
    )


# ---------------------------------------------------------------------------
# Selection helpers — `dispatch/client.py` consumes these.
# ---------------------------------------------------------------------------


def is_scrapling_available() -> bool:
    """Return True when the ``stealth`` optional extras are installed.

    Used by the dispatch client's transport-selection logic to decide
    whether to honor a session_cookie connection's auth-method affinity
    (default to stealth) or fall back to httpx with a warning.
    """
    try:
        import scrapling  # type: ignore[import-not-found]  # noqa: F401
        return True
    except ImportError:
        return False


def select_transport_for_artifact(artifact: Any) -> Transport:
    """Per §4.10 selection mechanism.

    Decision tree:

      1. If ``dispatch.transport`` is declared on the artifact, honor
         it. Recognized values:

           - ``"default"`` → ``HttpxTransport``.
           - ``"stealth"`` → ``ScraplingTransport`` if the optional
             ``stealth`` extras are installed; falls back to
             ``HttpxTransport`` with a logged warning when not, per
             §4.10's "fall back with a warning, not refuse" rule.
           - Any ``x-`` namespaced identifier → falls back to
             ``HttpxTransport`` with a logged warning (the prototype
             registers no ``x-`` transports).
           - Unknown identifier → falls back to ``HttpxTransport`` with
             a logged warning.

      2. Otherwise apply auth-method affinity. ``session_cookie`` auth
         (per §2.10) defaults to ``ScraplingTransport`` when the
         stealth extras are installed because providers reachable
         through session_cookie typically have browser-fingerprint
         defenses; falls back to ``HttpxTransport`` when not.

      3. Otherwise return ``HttpxTransport``.

    The graceful-degradation posture is the §4.10 conformance rule —
    implementations don't refuse to dispatch when a requested
    transport isn't available, they fall back with a warning so the
    user can decide whether to install extras or update the artifact.
    """
    import logging
    log = logging.getLogger("uacp.dispatch.transport")

    dispatch_extra = getattr(artifact.dispatch, "model_extra", None) or {}
    requested = dispatch_extra.get("transport")
    auth_method = getattr(artifact.authentication, "method", None)

    if requested is not None:
        if requested == "default":
            return HttpxTransport()
        if requested == "stealth":
            if is_scrapling_available():
                return ScraplingTransport()
            log.warning(
                "dispatch.transport='stealth' requested but the optional 'stealth' "
                "extras are not installed; falling back to HttpxTransport. "
                "Install with `uv sync --extra stealth` to enable."
            )
            return HttpxTransport()
        # Unknown / x-namespaced — graceful fallback per §4.10.
        log.warning(
            "dispatch.transport=%r is not a registered backend identifier in this "
            "implementation; falling back to HttpxTransport.",
            requested,
        )
        return HttpxTransport()

    # Auth-method affinity: session_cookie → stealth when available.
    if auth_method == "session_cookie" and is_scrapling_available():
        log.info(
            "session_cookie auth detected and 'stealth' extras installed; "
            "selecting ScraplingTransport per §4.10 auth-method affinity."
        )
        return ScraplingTransport()

    return HttpxTransport()


__all__ = [
    "HttpxTransport",
    "ScraplingNotInstalledError",
    "ScraplingTransport",
    "Transport",
    "is_scrapling_available",
    "select_transport_for_artifact",
]
