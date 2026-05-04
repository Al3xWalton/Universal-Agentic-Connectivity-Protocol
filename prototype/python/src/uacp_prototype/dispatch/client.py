"""HTTPS dispatch client per Stage 4.

Implements §4.1 (composition order) → §4.2 (HTTPS-only, redirects) →
§4.3 (retry policy) → §4.5 (rate-limit handling) → §4.6 (canonical error
shape).

The DispatchClient takes a parsed UACPArtifact, an AuthMethod adapter
from the auth/ package, and a credential resolver, and exposes one
entry point: ``dispatch(operation_id, params, body=...)`` returning a
DispatchResult. Pagination loops are implemented separately in
`pagination.py` and consume the dispatch client.

Streaming responses are §4.7 territory and stubbed for Stage 8a.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse, urlencode

import httpx

from ..spec.models import (
    DispatchConfig,
    Operation,
    RequestShape,
    UACPArtifact,
)
from ..auth.base import AuthMethod
from .body_format import decode_response_body
from .envelope import (
    evaluate_failure_predicate,
    extract_failure_details,
    select_response_entry,
)
from .transport import HttpxTransport, Transport

log = logging.getLogger("uacp.dispatch")

# §4.3 default retry policy
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_DELAY_MS = 250
DEFAULT_MULTIPLIER = 2.0
DEFAULT_MAX_DELAY_MS = 5000
DEFAULT_JITTER = 0.25

# §4.5 defaults
DEFAULT_RATE_LIMIT_RETRIES = 3
DEFAULT_RETRY_AFTER_CAP_SECONDS = 120

# §4.2 redirect cap
MAX_REDIRECTS = 5

PATH_PARAM_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


# ---------------------------------------------------------------------------
# Canonical failure shape (§4.6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchError:
    """Canonical error shape per §4.6.

    `code` is drawn from Principle 8's vocabulary: auth_expired,
    rate_limited, bad_input, upstream_error, not_found, forbidden,
    cancelled. `status` is the HTTP status (or 0 for transport-level
    failure). `details` carries the parsed envelope when one was matched;
    `raw` is the response body verbatim.
    """

    status: int
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


@dataclass(frozen=True)
class DispatchSuccess:
    """Successful dispatch result."""

    status: int
    headers: dict[str, str]
    body: Any  # parsed JSON body, or bytes for non-JSON

    def __bool__(self) -> bool:
        return True


DispatchResult = DispatchSuccess | DispatchError


class DispatchException(Exception):
    """Raised by dispatch when the caller is at fault (operation not found,
    parameter validation failure, etc.). Distinct from DispatchError, which
    is the structured failure surface for upstream / transport problems.
    """


# ---------------------------------------------------------------------------
# Status → canonical-code mapping (§4.6)
# ---------------------------------------------------------------------------


_STATUS_CODE_MAP = {
    400: "bad_input",
    401: "auth_expired",
    403: "forbidden",
    404: "not_found",
    408: "upstream_error",
    409: "bad_input",
    422: "bad_input",
    429: "rate_limited",
}


def _status_to_canonical_code(status: int) -> str:
    if status in _STATUS_CODE_MAP:
        return _STATUS_CODE_MAP[status]
    if 400 <= status < 500:
        return "bad_input"
    if 500 <= status:
        return "upstream_error"
    return "upstream_error"  # 1xx/3xx that leak past redirect handling


def _idempotent_method(method: str, idempotency: str) -> bool:
    """A request is retryable per §4.3 when the method is idempotent or the
    operation is explicitly declared idempotency: idempotent.
    """
    if method in {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}:
        return True
    return idempotency == "idempotent"


# ---------------------------------------------------------------------------
# Backoff helper (§4.3)
# ---------------------------------------------------------------------------


def _compute_backoff(
    attempt_number: int,
    *,
    initial_delay_ms: int = DEFAULT_INITIAL_DELAY_MS,
    multiplier: float = DEFAULT_MULTIPLIER,
    max_delay_ms: int = DEFAULT_MAX_DELAY_MS,
    jitter: float = DEFAULT_JITTER,
    rng: random.Random | None = None,
) -> float:
    """Compute the seconds to sleep before retry attempt N (1-indexed in
    terms of how many retries have happened; attempt 1 is the first retry,
    attempt 2 the second, etc.).
    """
    rng = rng or random.Random()
    base_ms = min(initial_delay_ms * (multiplier ** (attempt_number - 1)), max_delay_ms)
    low = base_ms * (1 - jitter)
    high = base_ms * (1 + jitter)
    return rng.uniform(low, high) / 1000.0  # → seconds


def _parse_retry_after(header_value: str | None, *, now: float) -> float | None:
    """Parse a Retry-After header value into seconds-from-now. Returns None
    if the header is missing or unparseable.
    """
    if header_value is None:
        return None
    header_value = header_value.strip()
    if not header_value:
        return None
    # Numeric (delta-seconds) form
    try:
        return max(0.0, float(header_value))
    except ValueError:
        pass
    # HTTP-date form
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(header_value)
        return max(0.0, dt.timestamp() - now)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Request building from operation + params
# ---------------------------------------------------------------------------


def _substitute_path_params(path: str, params: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise DispatchException(f"path parameter {name!r} not supplied")
        return str(params[name])

    return PATH_PARAM_PATTERN.sub(replace, path)


def _build_url(
    base_url: str,
    request: RequestShape,
    *,
    params: dict[str, Any],
    query: dict[str, Any],
) -> str:
    """Join base_url + path-substituted template + query string per §4.1
    composition steps 1-3.
    """
    path = _substitute_path_params(request.path, params)
    parsed = urlparse(base_url)
    base_path = parsed.path.rstrip("/")
    if path.startswith("/"):
        new_path = base_path + path
    else:
        new_path = base_path + "/" + path
    query_string = urlencode(query, doseq=True) if query else ""
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            new_path,
            parsed.params,
            query_string,
            "",
        )
    )


def _https_only(url: str) -> None:
    if not url.startswith("https://"):
        raise DispatchException(
            f"dispatch URL must be HTTPS per Principle 11; got {url!r}"
        )


# ---------------------------------------------------------------------------
# DispatchClient
# ---------------------------------------------------------------------------


class _TransportGetAdapter:
    """Adapter exposing ``.get(url, headers=...)`` over a Transport.

    Auth helpers (e.g. ``auth.session_cookie.refresh_csrf``) accept an
    ``http_client`` whose ``.get()`` shape mirrors ``httpx.Client.get``.
    Rather than expose the dispatch client's internal transport
    object directly, this adapter routes the auth helpers' GETs
    through the transport so that stealth-backed connections also use
    the stealth transport for CSRF refreshes — keeping the §4.10
    posture coherent across the dispatch + refresh paths.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        return self._transport.request("GET", url, headers=headers)


@dataclass
class _RateLimitState:
    """Per-Connection rate-limit state per §4.5 cross-operation pooling."""

    next_call_after: float = 0.0  # absolute timestamp; 0 means no wait
    remaining: int | None = None
    reset_at: float | None = None


class DispatchClient:
    """Stage 4 dispatch runtime.

    The client is bound to a single UACPArtifact and a single AuthMethod.
    The credential_resolver is a callable that returns the credentials
    dict the AuthMethod's apply() consumes — typically the access_token
    for OAuth methods. Stage 6 owns the secret-store resolution; the
    client just consumes the resolved credentials.

    Per §4.10 (added in v1.1) the underlying HTTP transport is
    pluggable. ``transport`` defaults to ``HttpxTransport`` (the v1.0
    behavior); callers MAY pass a different ``Transport`` (e.g.,
    ``ScraplingTransport`` for session_cookie connections that need
    anti-bot resilience) provided the substitute backend preserves
    the §4.1 — §4.9 contract.

    ``httpx_client`` is retained as a back-compat alias for tests that
    inject an ``httpx.Client`` directly; new callers SHOULD use
    ``transport=HttpxTransport(client=...)`` instead.
    """

    def __init__(
        self,
        artifact: UACPArtifact,
        *,
        auth_method: AuthMethod,
        credential_resolver: Callable[[], dict[str, Any]],
        transport: Transport | None = None,
        httpx_client: httpx.Client | None = None,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.artifact = artifact
        self.auth_method = auth_method
        self.credential_resolver = credential_resolver
        if transport is not None and httpx_client is not None:
            raise ValueError(
                "DispatchClient: pass either `transport` or `httpx_client`, not both"
            )
        if transport is None:
            transport = HttpxTransport(
                client=httpx_client,
                timeout=(
                    artifact.dispatch.default_timeout_ms / 1000.0
                    if httpx_client is None
                    else None
                ),
            )
        self._transport = transport
        self._rng = rng or random.Random()
        self._sleep = sleep
        self._rate_state = _RateLimitState()

        self._operations_by_id = {op.id: op for op in artifact.operations}

    @property
    def transport(self) -> Transport:
        """The active transport backend. Useful for tests + the §6.6
        audit-event ``transport`` field hint per §4.10."""
        return self._transport

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> "DispatchClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def dispatch(
        self,
        operation_id: str,
        *,
        path_params: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> DispatchResult:
        op = self._operation(operation_id)
        return self._dispatch_one(
            op,
            path_params=path_params or {},
            query=query or {},
            body=body,
            extra_headers=extra_headers or {},
        )

    def _operation(self, operation_id: str) -> Operation:
        if operation_id not in self._operations_by_id:
            raise DispatchException(
                f"operation {operation_id!r} not found in artifact "
                f"(known: {sorted(self._operations_by_id)})"
            )
        return self._operations_by_id[operation_id]

    # ------------------------------------------------------------------
    # Single-attempt dispatch with retry envelope
    # ------------------------------------------------------------------

    def _dispatch_one(
        self,
        op: Operation,
        *,
        path_params: dict[str, Any],
        query: dict[str, Any],
        body: Any,
        extra_headers: dict[str, str],
    ) -> DispatchResult:
        # Stash the in-flight operation so _build_success and
        # _check_failure_predicate can route the response through the
        # operation's declared format / failure_predicate.
        self._current_operation = op
        # Reset per-dispatch CSRF-refresh tracking (§2.10): only one
        # CSRF refresh-and-retry per dispatch call.
        self._csrf_refreshed_this_call = False
        # Per §2.10 audit emit: every dispatch through a session_cookie
        # connection logs at INFO with risk: tos_violation_potential.
        self._emit_session_cookie_audit_event(op)
        cfg = self.artifact.dispatch
        url = _build_url(cfg.base_url, op.request, params=path_params, query=query)
        _https_only(url)

        headers = dict(cfg.default_headers)
        if cfg.default_user_agent and "User-Agent" not in headers:
            headers["User-Agent"] = cfg.default_user_agent
        # The operation's request.headers schema declares which headers the
        # caller MAY supply; the wire-level header values come from
        # extra_headers. Headers from extra_headers override defaults.
        for k, v in extra_headers.items():
            headers[k] = v

        # Body serialization (§4.1 step 8). We don't validate against the
        # operation's body schema; the spec layer covers that. The dispatch
        # layer just serializes.
        request_body: bytes | None = None
        if body is not None:
            if isinstance(body, (bytes, bytearray)):
                request_body = bytes(body)
            else:
                import json as _json

                request_body = _json.dumps(body).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
                headers["Content-Length"] = str(len(request_body))

        # Apply auth (§4.1 step 7). Extracted so the §2.10 CSRF refresh
        # path can re-apply auth after refreshing the CSRF token.
        url, headers = self._apply_auth(op, url, headers, request_body)

        # Now the retry envelope.
        return self._with_retry(op, url, headers, request_body)

    def _apply_auth(
        self,
        op: Operation,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[str, dict[str, str]]:
        """Re-apply the AuthMethod's apply() to the in-flight request.

        Returns a new (url, headers) tuple. Used both for the initial
        dispatch and after a §2.10 CSRF refresh, when the runtime CSRF
        state has changed and the headers dict needs the freshly-derived
        CSRF header value rather than the stale one.
        """
        auth_input_request = httpx.Request(
            op.request.method, url, headers=headers, content=body
        )
        credentials = dict(self.credential_resolver() or {})
        # Inject runtime CSRF state (set by _session_cookie_csrf_refresh_attempt
        # on a prior iteration of this loop) so the AuthMethod's apply()
        # sees the refreshed token instead of the stale cookie-derived one.
        csrf_state = getattr(self, "_csrf_state", None)
        if csrf_state is not None:
            credentials.setdefault("csrf_state", csrf_state)
        auth_result = self.auth_method.apply(auth_input_request, credentials=credentials)
        new_headers = dict(headers)
        for hk, hv in auth_result.headers.items():
            new_headers[hk] = hv
        new_url = url
        if auth_result.query:
            parsed = urlparse(url)
            from urllib.parse import parse_qsl

            existing = dict(parse_qsl(parsed.query))
            existing.update(auth_result.query)
            new_url = urlunparse(
                parsed._replace(query=urlencode(existing, doseq=True))
            )
        return new_url, new_headers

    def _with_retry(
        self,
        op: Operation,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> DispatchResult:
        cfg = self.artifact.dispatch
        retry_cfg = op.retry
        max_attempts = (retry_cfg.max_attempts if retry_cfg and retry_cfg.max_attempts else DEFAULT_MAX_ATTEMPTS)
        initial_delay = (retry_cfg.initial_delay_ms if retry_cfg and retry_cfg.initial_delay_ms is not None else DEFAULT_INITIAL_DELAY_MS)
        max_delay = (retry_cfg.max_delay_ms if retry_cfg and retry_cfg.max_delay_ms is not None else DEFAULT_MAX_DELAY_MS)
        jitter = (retry_cfg.jitter if retry_cfg and retry_cfg.jitter is not None else DEFAULT_JITTER)
        multiplier = (retry_cfg.multiplier if retry_cfg and retry_cfg.multiplier is not None else DEFAULT_MULTIPLIER)

        retryable = _idempotent_method(op.request.method, op.idempotency)
        rate_limit_retries_remaining = DEFAULT_RATE_LIMIT_RETRIES

        attempt = 0
        last_error: DispatchError | None = None
        timeout_seconds = (op.timeout_ms or cfg.default_timeout_ms) / 1000.0

        while True:
            attempt += 1

            # Honor cross-operation rate budget (§4.5)
            now = time.time()
            if self._rate_state.next_call_after > now:
                wait = self._rate_state.next_call_after - now
                self._sleep(wait)

            try:
                response = self._send_with_redirects(
                    op.request.method, url, headers, body, timeout=timeout_seconds
                )
            except httpx.TimeoutException as e:
                err = DispatchError(
                    status=0,
                    code="upstream_error",
                    message=f"transport timeout after {timeout_seconds}s: {e}",
                )
                if retryable and attempt < max_attempts:
                    delay = _compute_backoff(
                        attempt,
                        initial_delay_ms=initial_delay,
                        multiplier=multiplier,
                        max_delay_ms=max_delay,
                        jitter=jitter,
                        rng=self._rng,
                    )
                    self._sleep(delay)
                    last_error = err
                    continue
                return err
            except httpx.HTTPError as e:
                err = DispatchError(
                    status=0,
                    code="upstream_error",
                    message=f"transport error: {type(e).__name__}: {e}",
                )
                if retryable and attempt < max_attempts:
                    delay = _compute_backoff(
                        attempt,
                        initial_delay_ms=initial_delay,
                        multiplier=multiplier,
                        max_delay_ms=max_delay,
                        jitter=jitter,
                        rng=self._rng,
                    )
                    self._sleep(delay)
                    last_error = err
                    continue
                return err

            # Update rate-limit advisory state from response headers
            self._update_rate_limit_state(response, now=time.time())

            # 2xx success — but check failure_predicate per §3.3 + §4.6
            # before declaring success. Providers that wrap logical
            # failures in {ok: false, ...} on a 200 status (Slack,
            # GraphQL-shaped APIs, several enterprise APIs) declare the
            # predicate; without one, this falls through to success.
            if 200 <= response.status_code < 300:
                envelope_result = self._check_failure_predicate(op, response)
                if envelope_result is not None:
                    return envelope_result
                return self._build_success(response)

            # 429 rate limit (§4.5)
            if response.status_code == 429:
                if rate_limit_retries_remaining <= 0:
                    return self._build_error(op, response)
                wait = _parse_retry_after(response.headers.get("Retry-After"), now=time.time())
                if wait is None:
                    wait = _compute_backoff(
                        DEFAULT_RATE_LIMIT_RETRIES - rate_limit_retries_remaining + 1,
                        initial_delay_ms=initial_delay,
                        multiplier=multiplier,
                        max_delay_ms=max_delay,
                        jitter=jitter,
                        rng=self._rng,
                    )
                if wait > DEFAULT_RETRY_AFTER_CAP_SECONDS:
                    return self._build_error(op, response)
                # Apply to per-Connection budget so concurrent ops also wait
                self._rate_state.next_call_after = max(
                    self._rate_state.next_call_after, time.time() + wait
                )
                self._sleep(wait)
                rate_limit_retries_remaining -= 1
                continue

            # 5xx upstream — retry if idempotent (§4.3)
            if 500 <= response.status_code < 600:
                err = self._build_error(op, response)
                if retryable and attempt < max_attempts:
                    delay = _compute_backoff(
                        attempt,
                        initial_delay_ms=initial_delay,
                        multiplier=multiplier,
                        max_delay_ms=max_delay,
                        jitter=jitter,
                        rng=self._rng,
                    )
                    self._sleep(delay)
                    last_error = err
                    continue
                return err

            # 4xx (other than 429) — try session_cookie CSRF refresh
            # per §2.10 if applicable, otherwise surface immediately.
            if response.status_code in (401, 403, 419):
                refreshed = self._session_cookie_csrf_refresh_attempt(op, response)
                if refreshed:
                    # Re-apply auth so the refreshed CSRF state lands in
                    # the request's CSRF header (the previous header value
                    # was derived from the old cookie token).
                    url, headers = self._apply_auth(op, url, headers, body)
                    last_error = None
                    continue
            return self._build_error(op, response)

    # ------------------------------------------------------------------
    # Redirect handling (§4.2)
    # ------------------------------------------------------------------

    def _send_with_redirects(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        timeout: float,
    ) -> httpx.Response:
        seen_urls: set[str] = set()
        current_method = method
        current_url = url
        current_headers = dict(headers)
        current_body = body
        for _ in range(MAX_REDIRECTS + 1):
            response = self._transport.request(
                current_method,
                current_url,
                headers=current_headers,
                content=current_body,
                timeout=timeout,
            )
            if response.status_code not in (301, 302, 303, 307, 308):
                return response

            if current_url in seen_urls:
                raise httpx.HTTPError(f"redirect loop detected at {current_url}")
            seen_urls.add(current_url)

            location = response.headers.get("Location")
            if not location:
                raise httpx.HTTPError(
                    f"{response.status_code} response without Location header"
                )

            # Resolve relative URLs
            new_url = httpx.URL(location)
            if not new_url.is_absolute_url:
                new_url = httpx.URL(current_url).join(new_url)

            # HTTPS-only target
            if new_url.scheme != "https":
                raise httpx.HTTPError(
                    f"redirect target {new_url} is not HTTPS; refusing to follow"
                )

            # Cross-origin: strip authentication-bearing headers
            old_origin = (httpx.URL(current_url).host, httpx.URL(current_url).port)
            new_origin = (new_url.host, new_url.port)
            if old_origin != new_origin:
                current_headers = {
                    k: v
                    for k, v in current_headers.items()
                    if k.lower() not in {"authorization", "x-api-key", "cookie"}
                }

            # Method handling per RFC 9110 §15.4
            if response.status_code in (301, 302, 303):
                if (
                    current_method not in {"GET", "HEAD"}
                    and not self.artifact.dispatch.allow_method_changing_redirects
                ):
                    raise httpx.HTTPError(
                        f"{response.status_code} method-changing redirect refused; "
                        "set dispatch.allow_method_changing_redirects=true to permit"
                    )
                if response.status_code == 303:
                    current_method = "GET"
                    current_body = None
            # 307 / 308 preserve method + body — already handled

            current_url = str(new_url)
        raise httpx.HTTPError(
            f"exceeded MAX_REDIRECTS ({MAX_REDIRECTS}) on {url!r}"
        )

    # ------------------------------------------------------------------
    # Response → result
    # ------------------------------------------------------------------

    def _build_success(self, response: httpx.Response) -> DispatchSuccess:
        # Per the §3.3 Stage 8c amendment, response entries may declare
        # a `format` discriminator (json / xml / binary / text). When
        # the matched entry's body declares format, decode accordingly;
        # otherwise fall back to the JSON-default behavior.
        operation = self._operation_for_response(response)
        format_hint: str | None = None
        media_type: str | None = response.headers.get("Content-Type")
        if operation is not None:
            entry = select_response_entry(operation.response, response.status_code)
            if entry is not None and isinstance(entry.body, dict):
                format_hint = entry.body.get("format")
                # When the artifact declares media_type, prefer it over
                # the wire Content-Type for routing. The wire Content-
                # Type may include charset suffixes that confuse simple
                # prefix matching.
                if entry.body.get("media_type"):
                    media_type = entry.body["media_type"]
        body = decode_response_body(
            response.content,
            format=format_hint,
            media_type=media_type,
        )
        return DispatchSuccess(
            status=response.status_code,
            headers=dict(response.headers),
            body=body,
        )

    def _operation_for_response(self, response: httpx.Response) -> Operation | None:
        """Return the operation whose response is being decoded.

        The dispatch client is constructed with a single artifact and
        invokes one operation per dispatch call. We track the
        in-flight operation on the instance so _build_success can find
        it; for v1 the prototype keeps it simple by stashing in
        ``_current_operation``.
        """
        return getattr(self, "_current_operation", None)

    def _session_cookie_csrf_refresh_attempt(
        self, op: Operation, response: httpx.Response
    ) -> bool:
        """Per §2.10 CSRF refresh on 401/403/419.

        When the artifact's authentication method is session_cookie and
        a csrf_token.refresh_url is configured, fetch the refresh URL
        with the current cookies, extract a fresh CSRF token via the
        configured extraction path, store it in the client's
        ``_csrf_state`` so the next dispatch call's credential resolver
        sees it, and return True. Returns False when the auth method
        isn't session_cookie, when no refresh is configured, when the
        refresh has already fired once for this dispatch (one-retry
        cap per the spec's "retry once" rule), or when the refresh
        fails for any reason.
        """
        if getattr(self.auth_method, "method", "") != "session_cookie":
            return False
        if getattr(self, "_csrf_refreshed_this_call", False):
            return False  # already refreshed once; don't loop
        # Pull the artifact's authentication block to find csrf_token config
        auth = self.artifact.authentication
        extra = auth.model_extra or {}
        csrf_block = extra.get("csrf_token")
        if not isinstance(csrf_block, dict):
            return False
        refresh_url = csrf_block.get("refresh_url")
        extraction_path = csrf_block.get("extraction_path")
        if not refresh_url or not extraction_path:
            return False
        extraction_format = csrf_block.get("extraction_format", "json")

        try:
            from ..auth.session_cookie import (
                CSRFState,
                SessionCookieAuthError,
                parse_storage_state,
                refresh_csrf,
            )

            credentials = self.credential_resolver()
            raw_state = credentials.get("storage_state")
            if raw_state is None:
                return False
            storage = (
                raw_state if hasattr(raw_state, "cookies")
                else parse_storage_state(raw_state)
            )
            new_token = refresh_csrf(
                refresh_url,
                storage_state=storage,
                extraction_path=extraction_path,
                extraction_format=extraction_format,
                http_client=_TransportGetAdapter(self._transport),
            )
            self._csrf_state = CSRFState(token=new_token)
            self._csrf_refreshed_this_call = True
            log.info(
                "session_cookie CSRF refreshed for op=%s (status=%d) — retrying once",
                op.id,
                response.status_code,
            )
            return True
        except SessionCookieAuthError as e:
            log.warning("session_cookie CSRF refresh failed: %s", e)
            return False
        except Exception as e:  # pragma: no cover — defensive
            log.warning("session_cookie CSRF refresh unexpected error: %s", e)
            return False

    def _emit_session_cookie_audit_event(self, op: Operation) -> None:
        """Per §2.10 SHOULD: emit an INFO-level audit event for every
        dispatch through a session_cookie connection, carrying
        risk: tos_violation_potential per §6.6 audit field convention.

        The prototype emits via stdlib logging on a dedicated audit
        logger so production deployments can route it independently.
        """
        if getattr(self.auth_method, "method", "") != "session_cookie":
            return
        # Use a structured-log-friendly format. Production deployments
        # will route this through their audit log pipeline.
        log.info(
            "session_cookie dispatch: op=%s risk=tos_violation_potential",
            op.id,
        )

    def _check_failure_predicate(
        self, op: Operation, response: httpx.Response
    ) -> DispatchError | None:
        """Per §3.3 + §4.6 body-predicate failure detection.

        Looks up the response entry matching the response's status. If
        the entry declares ``failure_predicate``, evaluates it against
        the parsed JSON body. On match, returns a DispatchError carrying
        the canonical error shape. On no-match (or no predicate
        declared), returns None and the caller proceeds with the
        2xx-success path.
        """
        entry = select_response_entry(op.response, response.status_code)
        if entry is None or entry.failure_predicate is None:
            return None
        try:
            body = response.json()
        except (ValueError, httpx.DecodingError):
            return None  # body isn't JSON → no predicate to evaluate

        if not evaluate_failure_predicate(entry.failure_predicate, body):
            return None

        provider_code, provider_message = extract_failure_details(
            entry.failure_predicate, body
        )

        # Code mapping when a provider error string is extracted: the
        # spec leaves the per-provider mapping as implementation
        # refinement (per §4.6 "MAY refine code from envelope context").
        # For Stage 8b the prototype maps a small set of common Slack-
        # shaped error strings; unknown codes default to upstream_error
        # (logical failure that the runtime can't categorize more
        # specifically).
        code = self._map_envelope_failure_code(provider_code)
        message = provider_message or (
            f"upstream returned logical failure"
            + (f" ({provider_code})" if provider_code else "")
        )

        details: dict[str, Any] = {}
        if isinstance(body, dict):
            details = {k: v for k, v in body.items()}

        return DispatchError(
            status=response.status_code,
            code=code,
            message=message,
            details=details,
            raw=body,
        )

    def _map_envelope_failure_code(self, provider_code: str | None) -> str:
        """Map a provider-specific envelope error string to a canonical
        code. Unknown / absent strings default to upstream_error.

        The mapping handles Slack's well-known errors (the Stage 8b
        target) plus a small set of universal patterns that recur
        across providers using the {ok: false} envelope shape.
        """
        if not provider_code:
            return "upstream_error"
        if provider_code in {
            "not_authed",
            "invalid_auth",
            "token_revoked",
            "token_expired",
            "no_permission",
        }:
            return "auth_expired"
        if provider_code in {"missing_scope", "account_inactive", "ekm_access_denied"}:
            return "forbidden"
        if provider_code in {
            "channel_not_found",
            "user_not_found",
            "message_not_found",
            "file_not_found",
        }:
            return "not_found"
        if provider_code in {"rate_limited", "ratelimited"}:
            return "rate_limited"
        if provider_code in {
            "invalid_arguments",
            "invalid_arg_name",
            "invalid_array_arg",
            "invalid_charset",
            "invalid_form_data",
            "invalid_post_type",
            "missing_post_type",
        }:
            return "bad_input"
        return "upstream_error"

    def _build_error(self, op: Operation, response: httpx.Response) -> DispatchError:
        code = _status_to_canonical_code(response.status_code)
        try:
            payload = response.json()
        except (ValueError, httpx.DecodingError):
            payload = None

        message = self._extract_message(payload, response)
        details = self._extract_details(op, payload, response.status_code)
        return DispatchError(
            status=response.status_code,
            code=code,
            message=message,
            details=details,
            raw=payload if payload is not None else response.text,
        )

    def _extract_message(self, payload: Any, response: httpx.Response) -> str:
        if isinstance(payload, dict):
            for key in ("message", "error_description", "error", "detail", "title"):
                v = payload.get(key)
                if isinstance(v, str) and v:
                    return v
        return f"HTTP {response.status_code} {response.reason_phrase}"

    def _extract_details(self, op: Operation, payload: Any, status: int) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        # When the operation declares a response schema for this status,
        # we've already validated structure at load time. The dispatch
        # layer extracts envelope fields verbatim.
        return {k: v for k, v in payload.items() if k not in {"raw"}}

    # ------------------------------------------------------------------
    # Rate-limit advisory headers (§4.5)
    # ------------------------------------------------------------------

    def _update_rate_limit_state(self, response: httpx.Response, *, now: float) -> None:
        remaining_header = response.headers.get("X-RateLimit-Remaining")
        if remaining_header is not None:
            try:
                self._rate_state.remaining = int(remaining_header)
            except ValueError:
                pass
        reset_header = response.headers.get("X-RateLimit-Reset")
        if reset_header is not None:
            try:
                # Two conventions: seconds-from-now, or Unix epoch.
                value = float(reset_header)
                if value > 1_000_000_000:  # epoch-shaped
                    self._rate_state.reset_at = value
                else:
                    self._rate_state.reset_at = now + value
            except ValueError:
                pass


__all__ = [
    "DispatchClient",
    "DispatchError",
    "DispatchException",
    "DispatchResult",
    "DispatchSuccess",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_INITIAL_DELAY_MS",
    "DEFAULT_MAX_DELAY_MS",
    "DEFAULT_JITTER",
    "DEFAULT_RATE_LIMIT_RETRIES",
    "DEFAULT_RETRY_AFTER_CAP_SECONDS",
    "MAX_REDIRECTS",
]
