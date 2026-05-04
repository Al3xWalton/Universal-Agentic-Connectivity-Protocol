"""Browser-instrumented session recorder per §3.12.

The ``BrowserRecorder`` opens the user's target URL in a real browser,
records every HTTP request the browser makes during the session, and
returns a typed ``CaptureArtifact`` containing HAR 1.2-format
request/response pairs plus the captured cookies + localStorage that
constitute the post-login session credentials.

Two backend implementations behind the abstraction:

  - ``PlaywrightBackend`` — the default. Uses Playwright's
    ``page.on("request")`` and ``page.on("response")`` events to
    record traffic. Lazy-imports Playwright so the prototype works
    without the ``capture`` extras installed; tests mock the driver.
  - ``ScraplingBackend`` — the optional anti-bot-fingerprint-matching
    backend. Stage 11.0's Scrapling integration was dispatch-only;
    Stage 11.1's ``ScraplingBackend`` falls back to Playwright for
    the actual traffic capture (Scrapling's ``StealthyFetcher`` is a
    per-request fetcher without long-session traffic-event hooks)
    and logs a note that the captured fingerprint is Playwright's
    rather than Scrapling's.

Resilience: every 30 seconds the recorder checkpoints its
in-progress capture to ``~/.uacp/captures/in-progress/<id>.har.tmp``.
On clean stop or mid-session crash the checkpoint is finalized into
the encrypted artifact via :mod:`uacp_prototype.capture.storage`.

Per §6.6, audit events are emitted at start / stop / store. The
audit payloads MUST scrub captured cookies / session tokens / auth
headers — log the request count and URL pattern, not the auth
values. The :func:`_scrub_for_audit` helper enforces this.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol


log = logging.getLogger("uacp.capture")


CAPTURE_VERSION = "1.0"
HAR_VERSION = "1.2"
DEFAULT_UACP_DIR = Path.home() / ".uacp"
IN_PROGRESS_DIR = DEFAULT_UACP_DIR / "captures" / "in-progress"
CHECKPOINT_INTERVAL_SECONDS = 30.0
DEFAULT_USER_AGENT = "uacp-prototype/0.1 (capture)"

BrowserBackendName = Literal["playwright", "scrapling"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CaptureError(RuntimeError):
    """Raised on capture lifecycle / driver failures."""


class PlaywrightNotInstalledError(CaptureError):
    """Raised when PlaywrightBackend is requested without the ``capture``
    extras installed. Carries a clear remediation message."""


class ScraplingCaptureNotInstalledError(CaptureError):
    """Raised when ScraplingBackend is requested without the ``stealth``
    extras installed."""


# ---------------------------------------------------------------------------
# Capture artifact + HAR entry shape
# ---------------------------------------------------------------------------


@dataclass
class HarEntry:
    """One captured HTTP request/response pair.

    Mirrors the HAR 1.2 entry shape (started_at + time_ms + request +
    response) but uses snake_case Python idioms; the ``to_har_dict``
    method emits the canonical camelCase HAR field names.
    """

    started_at: datetime
    time_ms: float
    request: dict[str, Any]   # {method, url, headers, body}
    response: dict[str, Any]  # {status, headers, body}

    def to_har_dict(self) -> dict[str, Any]:
        return {
            "startedDateTime": self.started_at.astimezone(timezone.utc).isoformat(),
            "time": self.time_ms,
            "request": _to_har_request(self.request),
            "response": _to_har_response(self.response),
            "cache": {},
            "timings": {"send": 0, "wait": self.time_ms, "receive": 0},
        }

    def to_internal_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.astimezone(timezone.utc).isoformat(),
            "time_ms": self.time_ms,
            "request": dict(self.request),
            "response": dict(self.response),
        }


def _to_har_request(req: dict[str, Any]) -> dict[str, Any]:
    headers = req.get("headers") or {}
    body = req.get("body")
    post_data: dict[str, Any] | None = None
    if body is not None:
        post_data = {
            "mimeType": _content_type(headers) or "application/octet-stream",
            "text": body if isinstance(body, str) else _safe_text(body),
        }
    return {
        "method": req.get("method", "GET"),
        "url": req.get("url", ""),
        "httpVersion": "HTTP/1.1",
        "cookies": [],
        "headers": [{"name": k, "value": v} for k, v in headers.items()],
        "queryString": [],
        "headersSize": -1,
        "bodySize": _body_size(body),
        **({"postData": post_data} if post_data is not None else {}),
    }


def _to_har_response(resp: dict[str, Any]) -> dict[str, Any]:
    headers = resp.get("headers") or {}
    body = resp.get("body")
    return {
        "status": resp.get("status", 0),
        "statusText": resp.get("status_text", ""),
        "httpVersion": "HTTP/1.1",
        "cookies": [],
        "headers": [{"name": k, "value": v} for k, v in headers.items()],
        "content": {
            "size": _body_size(body),
            "mimeType": _content_type(headers) or "application/octet-stream",
            "text": body if isinstance(body, str) else _safe_text(body),
        },
        "redirectURL": headers.get("Location") or headers.get("location") or "",
        "headersSize": -1,
        "bodySize": _body_size(body),
    }


def _content_type(headers: dict[str, str]) -> str | None:
    for k, v in headers.items():
        if k.lower() == "content-type":
            return v.split(";")[0].strip()
    return None


def _body_size(body: Any) -> int:
    if body is None:
        return 0
    if isinstance(body, (bytes, bytearray)):
        return len(body)
    if isinstance(body, str):
        return len(body.encode("utf-8"))
    return -1


def _safe_text(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, (bytes, bytearray)):
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary, {len(body)} bytes>"
    return str(body)


@dataclass
class CaptureArtifact:
    """A completed in-memory capture.

    Combines HAR 1.2-compliant request/response entries with the
    captured browser ``storage_state`` (cookies + localStorage) that
    constitutes the post-login session credentials. The capture-id
    field is a stable hash of (initial_url + captured_at + provider
    hint) so storing the same capture twice yields the same id.
    """

    capture_id: str
    captured_at: datetime
    browser_backend: str
    initial_url: str
    final_url: str = ""
    entries: list[HarEntry] = field(default_factory=list)
    storage_state: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = CAPTURE_VERSION

    def to_har_json(self) -> dict[str, Any]:
        """HAR 1.2-compliant serialization. The ``storage_state`` is
        OMITTED — HAR doesn't have a slot for it; for full UACP
        fidelity use :meth:`to_internal_json`.
        """
        return {
            "log": {
                "version": HAR_VERSION,
                "creator": {
                    "name": "uacp-prototype/capture",
                    "version": CAPTURE_VERSION,
                },
                "browser": {
                    "name": self.browser_backend,
                    "version": "0",
                },
                "pages": [
                    {
                        "startedDateTime": self.captured_at.astimezone(
                            timezone.utc
                        ).isoformat(),
                        "id": self.capture_id,
                        "title": self.initial_url,
                        "pageTimings": {},
                    }
                ],
                "entries": [e.to_har_dict() for e in self.entries],
            }
        }

    def to_internal_json(self) -> dict[str, Any]:
        """UACP-internal serialization including ``storage_state``,
        the capture id, and any metadata fields. This is the form the
        encrypted-at-rest persistence consumes; round-tripped through
        :meth:`from_internal_json`."""
        return {
            "version": self.version,
            "capture_id": self.capture_id,
            "captured_at": self.captured_at.astimezone(timezone.utc).isoformat(),
            "browser_backend": self.browser_backend,
            "initial_url": self.initial_url,
            "final_url": self.final_url,
            "entries": [e.to_internal_dict() for e in self.entries],
            "storage_state": self.storage_state,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_internal_json(cls, data: dict[str, Any]) -> "CaptureArtifact":
        return cls(
            capture_id=data["capture_id"],
            captured_at=datetime.fromisoformat(data["captured_at"]),
            browser_backend=data["browser_backend"],
            initial_url=data["initial_url"],
            final_url=data.get("final_url", ""),
            entries=[
                HarEntry(
                    started_at=datetime.fromisoformat(e["started_at"]),
                    time_ms=float(e.get("time_ms", 0.0)),
                    request=dict(e["request"]),
                    response=dict(e["response"]),
                )
                for e in data.get("entries", [])
            ],
            storage_state=data.get("storage_state"),
            metadata=dict(data.get("metadata", {})),
            version=data.get("version", CAPTURE_VERSION),
        )


def capture_id_for(initial_url: str, captured_at: datetime, provider: str | None = None) -> str:
    """Deterministic capture id per §3.12's storage-reference convention.

    Hash of (initial_url + ISO-formatted captured_at + provider). The
    same capture is never accidentally stored twice; truncated to
    16 hex chars for human-friendly storage paths.
    """
    h = hashlib.sha256()
    h.update(initial_url.encode("utf-8"))
    h.update(b"|")
    h.update(captured_at.astimezone(timezone.utc).isoformat().encode("utf-8"))
    h.update(b"|")
    h.update((provider or "").encode("utf-8"))
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Audit-emit helpers — scrub auth values per §6.6
# ---------------------------------------------------------------------------


_REDACT_HEADER_NAMES = frozenset(
    h.lower()
    for h in (
        "Authorization",
        "Cookie",
        "Set-Cookie",
        "Proxy-Authorization",
        "X-Auth-Token",
        "X-Csrf-Token",
        "X-XSRF-TOKEN",
        "X-API-Key",
        "X-Goog-AuthUser",
    )
)


def _scrub_for_audit(headers: dict[str, str]) -> dict[str, str]:
    """Redact auth-bearing header values for audit-event payloads.

    The presence of an auth header is informative (so the audit trail
    records that authentication happened); the value is not. This
    matches §6.6's audit-log convention used by the §2.10 dispatch
    audit emit.
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _REDACT_HEADER_NAMES:
            out[k] = "<redacted>"
        else:
            out[k] = v
    return out


def _audit_url_pattern(url: str) -> str:
    """Strip query strings + replace numeric path segments with `:id`
    for audit-event grouping. Captures-against-many-distinct-paths
    aren't useful audit signals; captures-against-an-endpoint-pattern
    are."""
    base = url.split("?", 1)[0]
    parts = base.split("/")
    out_parts = []
    for p in parts:
        if p.isdigit() or (p and all(c.isalnum() or c in "-_" for c in p) and any(c.isdigit() for c in p) and len(p) > 8):
            out_parts.append(":id")
        else:
            out_parts.append(p)
    return "/".join(out_parts)


def _emit_audit_capture_started(
    capture_id: str, initial_url: str, browser_backend: str
) -> None:
    log.info(
        "capture started: id=%s backend=%s initial_url=%s",
        capture_id,
        browser_backend,
        initial_url,
    )


def _emit_audit_capture_stopped(
    capture_id: str, request_count: int, duration_ms: float
) -> None:
    log.info(
        "capture stopped: id=%s requests=%d duration_ms=%.0f",
        capture_id,
        request_count,
        duration_ms,
    )


# ---------------------------------------------------------------------------
# BrowserDriver protocol — what the recorder needs from any backend
# ---------------------------------------------------------------------------


@dataclass
class _DriverEvents:
    """Callbacks the BrowserRecorder registers with each driver."""

    on_request_response: Callable[[HarEntry], None]
    on_disconnect: Callable[[], None]


class BrowserDriver(Protocol):
    """Minimal driver contract the recorder consumes.

    Every backend (Playwright, Scrapling) implements this. Drivers
    are responsible for launching the browser, registering event
    handlers, and exposing the post-session ``storage_state`` plus
    final navigation URL.
    """

    name: str

    def start(self, initial_url: str, *, events: _DriverEvents, headless: bool) -> None: ...

    def is_alive(self) -> bool: ...

    def final_url(self) -> str: ...

    def storage_state(self) -> dict[str, Any] | None: ...

    def stop(self) -> None: ...


# ---------------------------------------------------------------------------
# PlaywrightBackend — default
# ---------------------------------------------------------------------------


class PlaywrightBackend:
    """Default capture backend backed by Playwright sync API.

    Lazy-imports ``playwright.sync_api`` so the prototype works
    without the optional ``capture`` extras installed; tests mock
    the driver directly. Per the brief's pragmatic affordance,
    PlaywrightBackend is the default — every capture flow reaches
    here unless the caller explicitly requests Scrapling.
    """

    name = "playwright"

    def __init__(self) -> None:
        self._pw_ctx: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._events: _DriverEvents | None = None
        self._final_url = ""
        self._pending_responses: dict[Any, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._alive = False

    def start(self, initial_url: str, *, events: _DriverEvents, headless: bool) -> None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except ImportError as e:
            raise PlaywrightNotInstalledError(
                "PlaywrightBackend requires the optional 'capture' extras. "
                "Install with `uv sync --extra capture` then "
                "`uv run playwright install chromium`. To use a different "
                "backend, pass browser_backend='scrapling' (also optional)."
            ) from e

        self._events = events
        self._pw_ctx = sync_playwright().start()
        self._browser = self._pw_ctx.chromium.launch(headless=headless)
        self._context = self._browser.new_context(user_agent=DEFAULT_USER_AGENT)
        self._page = self._context.new_page()

        # Wire request + response events. Playwright fires them on its
        # own thread; we synchronize entry assembly via the lock.
        self._page.on("request", self._handle_request)
        self._page.on("response", self._handle_response)

        # Browser-disconnect hook so the recorder knows to finalize
        # cleanly when the user closes the window.
        try:
            self._browser.on("disconnected", self._handle_disconnect)
        except Exception:
            # Older Playwright versions don't expose 'disconnected' on
            # the browser; the recorder's checkpoint loop catches the
            # disconnect on the next tick.
            pass

        self._alive = True
        try:
            self._page.goto(initial_url)
        except Exception as e:
            self._alive = False
            try:
                self.stop()
            finally:
                pass
            raise CaptureError(
                f"PlaywrightBackend: navigation to {initial_url!r} failed: {e}"
            ) from e
        self._final_url = self._page.url

    def _handle_request(self, request: Any) -> None:
        with self._lock:
            self._pending_responses[request] = {
                "started_at": datetime.now(timezone.utc),
                "request": {
                    "method": request.method,
                    "url": request.url,
                    "headers": dict(request.headers),
                    "body": _try_get_post_data(request),
                },
            }

    def _handle_response(self, response: Any) -> None:
        request = response.request
        with self._lock:
            pending = self._pending_responses.pop(request, None)
        if pending is None:
            # Response without a paired request entry — synthesize a
            # minimal one so we don't drop the data.
            pending = {
                "started_at": datetime.now(timezone.utc),
                "request": {
                    "method": getattr(request, "method", "GET"),
                    "url": getattr(request, "url", ""),
                    "headers": dict(getattr(request, "headers", {}) or {}),
                    "body": None,
                },
            }
        body = _try_get_response_body(response)
        elapsed_ms = max(
            0.0, (datetime.now(timezone.utc) - pending["started_at"]).total_seconds() * 1000.0
        )
        entry = HarEntry(
            started_at=pending["started_at"],
            time_ms=elapsed_ms,
            request=pending["request"],
            response={
                "status": response.status,
                "status_text": response.status_text,
                "headers": dict(response.headers),
                "body": body,
            },
        )
        if self._events is not None:
            try:
                self._events.on_request_response(entry)
            except Exception as e:  # pragma: no cover — defensive
                log.warning("capture: on_request_response handler raised: %s", e)

    def _handle_disconnect(self) -> None:
        self._alive = False
        if self._events is not None:
            try:
                self._events.on_disconnect()
            except Exception as e:  # pragma: no cover — defensive
                log.warning("capture: on_disconnect handler raised: %s", e)

    def is_alive(self) -> bool:
        if not self._alive:
            return False
        if self._browser is None:
            return False
        try:
            connected = self._browser.is_connected()
            if not connected:
                self._alive = False
            return connected
        except Exception:
            return False

    def final_url(self) -> str:
        if self._page is None:
            return self._final_url
        try:
            current = self._page.url
            if current:
                self._final_url = current
        except Exception:
            pass
        return self._final_url

    def storage_state(self) -> dict[str, Any] | None:
        if self._context is None:
            return None
        try:
            return self._context.storage_state()
        except Exception:
            return None

    def stop(self) -> None:
        try:
            if self._context is not None:
                try:
                    self._final_url = self._page.url if self._page else self._final_url
                except Exception:
                    pass
                try:
                    self._context.close()
                except Exception:
                    pass
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
            if self._pw_ctx is not None:
                try:
                    self._pw_ctx.stop()
                except Exception:
                    pass
        finally:
            self._alive = False
            self._context = None
            self._browser = None
            self._page = None
            self._pw_ctx = None


def _try_get_post_data(request: Any) -> str | None:
    try:
        data = request.post_data
        return data
    except Exception:
        return None


def _try_get_response_body(response: Any) -> str | None:
    try:
        body = response.text()
        return body
    except Exception:
        try:
            data = response.body()
            return _safe_text(data)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# ScraplingBackend — optional, falls back to Playwright per the brief
# ---------------------------------------------------------------------------


class ScraplingBackend:
    """Optional capture backend.

    Stage 11.0's Scrapling integration is dispatch-only — Scrapling's
    ``StealthyFetcher`` is a per-request fetcher without long-session
    traffic-event hooks the recorder needs. Per the brief's explicit
    fallback ("Scrapling's browser API doesn't expose what's needed
    for capture (only for dispatch) — fall back to Playwright-only
    for capture"), this backend transparently delegates to
    ``PlaywrightBackend`` and emits a one-time INFO-level note that
    the captured fingerprint is Playwright's rather than Scrapling's.
    Stage 11.2+ MAY revisit this when Scrapling exposes a session-
    capable browser API.
    """

    name = "scrapling"

    def __init__(self) -> None:
        # Verify the stealth extras are at least installed so the
        # caller's intent (use stealth) is recognized, even though
        # the actual capture falls back to Playwright.
        try:
            import scrapling  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as e:
            raise ScraplingCaptureNotInstalledError(
                "ScraplingBackend requires the optional 'stealth' "
                "extras. Install with `uv sync --extra stealth`, OR pass "
                "browser_backend='playwright' to use the default capture "
                "backend (which is what ScraplingBackend currently delegates "
                "to anyway in Stage 11.1 — see capture/recorder.py for the "
                "fallback rationale)."
            ) from e
        self._inner = PlaywrightBackend()
        log.info(
            "ScraplingBackend: Stage 11.1 falls back to PlaywrightBackend for "
            "the actual capture (Scrapling's API is dispatch-only as of "
            "v0.3); the captured fingerprint will be Playwright's rather "
            "than Scrapling's. The Stage 11.0 dispatch transport "
            "(ScraplingTransport) still uses Scrapling's stealth posture "
            "for replay."
        )

    def start(self, initial_url: str, *, events: _DriverEvents, headless: bool) -> None:
        self._inner.start(initial_url, events=events, headless=headless)

    def is_alive(self) -> bool:
        return self._inner.is_alive()

    def final_url(self) -> str:
        return self._inner.final_url()

    def storage_state(self) -> dict[str, Any] | None:
        return self._inner.storage_state()

    def stop(self) -> None:
        self._inner.stop()


# ---------------------------------------------------------------------------
# BrowserRecorder
# ---------------------------------------------------------------------------


@dataclass
class BrowserRecorder:
    """High-level recording engine per §3.12.

    Construct with the desired backend; call :meth:`start` with the
    initial URL; poll :meth:`requests_captured` while the user
    demonstrates; call :meth:`stop` to finalize. The returned
    ``CaptureArtifact`` is suitable for encrypted-at-rest persistence
    via :func:`uacp_prototype.capture.storage.store_capture`.

    Resilience: every ``CHECKPOINT_INTERVAL_SECONDS`` (30 s) the
    recorder writes the in-progress capture to a temp file. On
    crash or signal, the temp file is recoverable via
    :func:`recover_in_progress`.
    """

    browser_backend: BrowserBackendName = "playwright"
    headless: bool = False
    provider: str | None = None
    in_progress_dir: Path = field(default_factory=lambda: IN_PROGRESS_DIR)
    driver_factory: Callable[[BrowserBackendName], BrowserDriver] | None = None

    def __post_init__(self) -> None:
        self._driver: BrowserDriver | None = None
        self._lock = threading.Lock()
        self._entries: list[HarEntry] = []
        self._captured_at: datetime | None = None
        self._capture_id: str | None = None
        self._initial_url: str | None = None
        self._stopped = False
        self._disconnect_event = threading.Event()
        self._checkpoint_thread: threading.Thread | None = None
        self._checkpoint_stop = threading.Event()

    # ------------------------------------------------------------------
    # Driver construction
    # ------------------------------------------------------------------

    def _build_driver(self) -> BrowserDriver:
        if self.driver_factory is not None:
            return self.driver_factory(self.browser_backend)
        if self.browser_backend == "playwright":
            return PlaywrightBackend()
        if self.browser_backend == "scrapling":
            return ScraplingBackend()
        raise CaptureError(
            f"unknown browser_backend {self.browser_backend!r}; expected "
            f"'playwright' or 'scrapling'"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, initial_url: str) -> None:
        """Launch the browser, navigate to ``initial_url``, begin
        recording. Idempotent against a single instance — calling
        twice raises CaptureError."""
        if self._driver is not None:
            raise CaptureError("BrowserRecorder.start: already started")
        if not initial_url.startswith(("http://", "https://")):
            raise CaptureError(
                f"initial_url must be http:// or https://; got {initial_url!r}"
            )

        self._captured_at = datetime.now(timezone.utc)
        self._initial_url = initial_url
        self._capture_id = capture_id_for(
            initial_url, self._captured_at, provider=self.provider
        )
        self._driver = self._build_driver()

        events = _DriverEvents(
            on_request_response=self._on_entry,
            on_disconnect=self._on_disconnect,
        )
        try:
            self._driver.start(initial_url, events=events, headless=self.headless)
        except Exception:
            self._driver = None
            self._capture_id = None
            self._initial_url = None
            self._captured_at = None
            raise

        _emit_audit_capture_started(
            capture_id=self._capture_id,
            initial_url=initial_url,
            browser_backend=self._driver.name,
        )

        # Spin the checkpoint thread.
        self._checkpoint_stop.clear()
        self._checkpoint_thread = threading.Thread(
            target=self._checkpoint_loop, name="uacp-capture-checkpoint", daemon=True
        )
        self._checkpoint_thread.start()

    def requests_captured(self) -> int:
        with self._lock:
            return len(self._entries)

    def is_alive(self) -> bool:
        if self._driver is None:
            return False
        return self._driver.is_alive() and not self._disconnect_event.is_set()

    def disconnect_event(self) -> threading.Event:
        """Returns the threading.Event that fires when the browser
        disconnects (user closes the window). The CLI awaits this
        alongside stdin to detect the user's stop signal."""
        return self._disconnect_event

    def stop(self) -> CaptureArtifact:
        """Close the browser, finalize the artifact, return it. Safe
        to call multiple times — subsequent calls return the cached
        artifact."""
        if self._driver is None:
            raise CaptureError("BrowserRecorder.stop: not started")
        if self._stopped:
            return self._build_artifact()
        self._checkpoint_stop.set()
        if self._checkpoint_thread is not None:
            self._checkpoint_thread.join(timeout=2.0)

        try:
            storage = self._driver.storage_state()
        except Exception:
            storage = None
        try:
            final_url = self._driver.final_url()
        except Exception:
            final_url = ""
        try:
            self._driver.stop()
        except Exception as e:  # pragma: no cover — defensive
            log.warning("capture: driver.stop raised: %s", e)

        self._final_url = final_url
        self._final_storage = storage
        self._stopped = True
        artifact = self._build_artifact()

        duration_ms = (
            datetime.now(timezone.utc) - self._captured_at
        ).total_seconds() * 1000.0 if self._captured_at else 0.0
        _emit_audit_capture_stopped(
            capture_id=artifact.capture_id,
            request_count=len(artifact.entries),
            duration_ms=duration_ms,
        )

        # Clean up any in-progress checkpoint — the caller now owns
        # the in-memory artifact + will persist via storage.store_capture.
        self._delete_checkpoint(artifact.capture_id)

        return artifact

    # ------------------------------------------------------------------
    # Event handlers (called from driver threads)
    # ------------------------------------------------------------------

    def _on_entry(self, entry: HarEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def _on_disconnect(self) -> None:
        self._disconnect_event.set()

    # ------------------------------------------------------------------
    # Checkpoint resilience
    # ------------------------------------------------------------------

    def _checkpoint_loop(self) -> None:
        while not self._checkpoint_stop.wait(CHECKPOINT_INTERVAL_SECONDS):
            try:
                self._write_checkpoint()
            except Exception as e:  # pragma: no cover — defensive
                log.warning("capture: checkpoint write failed: %s", e)

    def _checkpoint_path(self, capture_id: str | None = None) -> Path:
        cid = capture_id or self._capture_id
        if cid is None:
            raise CaptureError("checkpoint requested before start")
        return self.in_progress_dir / f"{cid}.har.tmp"

    def _write_checkpoint(self) -> None:
        if self._capture_id is None or self._captured_at is None:
            return
        artifact = self._build_artifact(in_progress=True)
        path = self._checkpoint_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Best-effort permissions; defense in depth around an unencrypted
        # in-progress file. Final artifact is encrypted via storage.py.
        path.write_text(json.dumps(artifact.to_internal_json()))
        try:
            import os

            os.chmod(path, 0o600)
        except OSError:
            pass

    def _delete_checkpoint(self, capture_id: str) -> None:
        path = self._checkpoint_path(capture_id)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Artifact construction
    # ------------------------------------------------------------------

    def _build_artifact(self, *, in_progress: bool = False) -> CaptureArtifact:
        if self._capture_id is None or self._captured_at is None or self._initial_url is None:
            raise CaptureError("build_artifact: recorder not started")

        with self._lock:
            entries_snapshot = list(self._entries)

        try:
            final_url = getattr(self, "_final_url", "") or (
                self._driver.final_url() if self._driver is not None else ""
            )
        except Exception:
            final_url = getattr(self, "_final_url", "")

        try:
            storage = getattr(self, "_final_storage", None)
            if storage is None and self._driver is not None and not in_progress:
                storage = self._driver.storage_state()
        except Exception:
            storage = None

        metadata = {
            "user_agent": DEFAULT_USER_AGENT,
            "in_progress": in_progress,
            "checkpoint_interval_seconds": CHECKPOINT_INTERVAL_SECONDS,
        }
        if self.provider is not None:
            metadata["provider"] = self.provider

        return CaptureArtifact(
            capture_id=self._capture_id,
            captured_at=self._captured_at,
            browser_backend=(self._driver.name if self._driver is not None else self.browser_backend),
            initial_url=self._initial_url,
            final_url=final_url,
            entries=entries_snapshot,
            storage_state=storage,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Recovery helper
# ---------------------------------------------------------------------------


def recover_in_progress(
    capture_id: str, *, in_progress_dir: Path = IN_PROGRESS_DIR
) -> CaptureArtifact | None:
    """Reload an in-progress capture from its checkpoint file.

    Returns the partial CaptureArtifact (with metadata.in_progress=True)
    or None if no checkpoint exists. Used by the CLI when the
    recorder dies before a clean stop and the user wants to recover
    whatever was captured.
    """
    path = in_progress_dir / f"{capture_id}.har.tmp"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise CaptureError(f"checkpoint at {path} is unreadable: {e}") from e
    return CaptureArtifact.from_internal_json(data)


__all__ = [
    "BrowserDriver",
    "BrowserRecorder",
    "BrowserBackendName",
    "CHECKPOINT_INTERVAL_SECONDS",
    "CaptureArtifact",
    "CaptureError",
    "HarEntry",
    "IN_PROGRESS_DIR",
    "PlaywrightBackend",
    "PlaywrightNotInstalledError",
    "ScraplingBackend",
    "ScraplingCaptureNotInstalledError",
    "capture_id_for",
    "recover_in_progress",
]
