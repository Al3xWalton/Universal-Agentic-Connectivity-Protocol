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

    `httpx_client` is injectable for testing; production callers can
    omit it and the client constructs its own.
    """

    def __init__(
        self,
        artifact: UACPArtifact,
        *,
        auth_method: AuthMethod,
        credential_resolver: Callable[[], dict[str, Any]],
        httpx_client: httpx.Client | None = None,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.artifact = artifact
        self.auth_method = auth_method
        self.credential_resolver = credential_resolver
        self._owned_client = httpx_client is None
        self._client = httpx_client or httpx.Client(
            timeout=artifact.dispatch.default_timeout_ms / 1000.0,
            follow_redirects=False,  # we handle redirects per §4.2
        )
        self._rng = rng or random.Random()
        self._sleep = sleep
        self._rate_state = _RateLimitState()

        self._operations_by_id = {op.id: op for op in artifact.operations}

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

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

        # Apply auth (§4.1 step 7): the auth subsystem applies LAST and may
        # add headers / query. We construct an httpx.Request only to hand
        # to the AuthMethod for inspection; we then merge.
        auth_input_request = httpx.Request(
            op.request.method, url, headers=headers, content=request_body
        )
        credentials = self.credential_resolver()
        auth_result = self.auth_method.apply(auth_input_request, credentials=credentials)
        for hk, hv in auth_result.headers.items():
            headers[hk] = hv
        if auth_result.query:
            # rebuild URL with merged query
            parsed = urlparse(url)
            from urllib.parse import parse_qsl

            existing = dict(parse_qsl(parsed.query))
            existing.update(auth_result.query)
            url = urlunparse(
                parsed._replace(query=urlencode(existing, doseq=True))
            )

        # Now the retry envelope.
        return self._with_retry(op, url, headers, request_body)

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

            # 2xx success
            if 200 <= response.status_code < 300:
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

            # 4xx (other than 429) — surface immediately (§4.3)
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
            response = self._client.request(
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
        try:
            body: Any = response.json()
        except (ValueError, httpx.DecodingError):
            body = response.content
        return DispatchSuccess(
            status=response.status_code,
            headers=dict(response.headers),
            body=body,
        )

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
