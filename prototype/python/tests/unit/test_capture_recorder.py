"""Unit tests for the browser-instrumented capture recorder per §3.12.

Tests use a fake ``BrowserDriver`` that mimics Playwright's request /
response event surface without launching a real browser. Live-browser
tests (one or two safe targets like httpbin.org) live behind the
``@pytest.mark.capture_integration`` marker and are skipped by default.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from uacp_prototype.capture.recorder import (
    CHECKPOINT_INTERVAL_SECONDS,
    BrowserRecorder,
    CaptureArtifact,
    CaptureError,
    HarEntry,
    PlaywrightBackend,
    PlaywrightNotInstalledError,
    ScraplingBackend,
    ScraplingCaptureNotInstalledError,
    capture_id_for,
    recover_in_progress,
    _DriverEvents,
    _scrub_for_audit,
    _audit_url_pattern,
)


# ---------------------------------------------------------------------------
# Fake driver — mimics Playwright's surface for unit-test speed
# ---------------------------------------------------------------------------


class FakeDriver:
    """In-memory driver that fires recorder events on demand."""

    def __init__(self, *, name: str = "fake") -> None:
        self.name = name
        self.started = False
        self.stopped = False
        self.events: _DriverEvents | None = None
        self._initial_url = ""
        self._final_url = ""
        self._storage: dict[str, Any] | None = None
        self._alive = False
        self.last_headless: bool | None = None
        self.start_should_raise: Exception | None = None

    def start(self, initial_url: str, *, events: _DriverEvents, headless: bool) -> None:
        if self.start_should_raise is not None:
            raise self.start_should_raise
        self.started = True
        self.events = events
        self._initial_url = initial_url
        self._final_url = initial_url
        self._alive = True
        self.last_headless = headless

    def is_alive(self) -> bool:
        return self._alive

    def final_url(self) -> str:
        return self._final_url

    def storage_state(self) -> dict[str, Any] | None:
        return self._storage

    def stop(self) -> None:
        self.stopped = True
        self._alive = False

    # Test helpers — drive the recorder from outside.
    def fire(
        self,
        method: str = "GET",
        url: str = "https://example.com/api/v1/items",
        status: int = 200,
        request_headers: dict[str, str] | None = None,
        response_headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> None:
        assert self.events is not None
        entry = HarEntry(
            started_at=datetime.now(timezone.utc),
            time_ms=42.0,
            request={
                "method": method,
                "url": url,
                "headers": request_headers or {"User-Agent": "fake"},
                "body": None,
            },
            response={
                "status": status,
                "status_text": "OK",
                "headers": response_headers or {"Content-Type": "application/json"},
                "body": body or '{"ok": true}',
            },
        )
        self.events.on_request_response(entry)

    def navigate(self, url: str) -> None:
        self._final_url = url

    def set_storage_state(self, state: dict[str, Any]) -> None:
        self._storage = state

    def disconnect(self) -> None:
        self._alive = False
        if self.events is not None:
            self.events.on_disconnect()


def _make_recorder(tmp_path: Path, *, driver: FakeDriver | None = None) -> tuple[BrowserRecorder, FakeDriver]:
    drv = driver or FakeDriver()
    rec = BrowserRecorder(
        browser_backend="playwright",
        in_progress_dir=tmp_path / "in-progress",
        driver_factory=lambda _name: drv,
    )
    return rec, drv


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_capture_lifecycle_basic(tmp_path: Path) -> None:
    rec, drv = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    assert drv.started is True
    assert drv.last_headless is False  # default per the brief

    drv.fire(url="https://example.com/api/items")
    drv.fire(url="https://example.com/api/items/42", method="POST", body='{"x":1}')
    assert rec.requests_captured() == 2

    drv.set_storage_state({"cookies": [{"name": "sid", "value": "test"}]})
    drv.navigate("https://example.com/done")
    artifact = rec.stop()

    assert isinstance(artifact, CaptureArtifact)
    assert artifact.initial_url == "https://example.com/"
    assert artifact.final_url == "https://example.com/done"
    assert len(artifact.entries) == 2
    assert artifact.storage_state == {"cookies": [{"name": "sid", "value": "test"}]}
    assert artifact.browser_backend == "fake"  # the FakeDriver's name
    assert drv.stopped is True


def test_capture_id_is_deterministic() -> None:
    when = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    a = capture_id_for("https://example.com/", when, provider="example")
    b = capture_id_for("https://example.com/", when, provider="example")
    assert a == b
    assert len(a) == 16
    # Different inputs → different ids.
    assert capture_id_for("https://example.com/", when) != a
    assert capture_id_for("https://other.com/", when, provider="example") != a


def test_start_rejects_non_http_url(tmp_path: Path) -> None:
    rec, _ = _make_recorder(tmp_path)
    with pytest.raises(CaptureError, match="http://"):
        rec.start("ftp://example.com/")


def test_start_twice_raises(tmp_path: Path) -> None:
    rec, _ = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    try:
        with pytest.raises(CaptureError, match="already started"):
            rec.start("https://example.com/other")
    finally:
        rec.stop()


def test_stop_before_start_raises(tmp_path: Path) -> None:
    rec, _ = _make_recorder(tmp_path)
    with pytest.raises(CaptureError, match="not started"):
        rec.stop()


def test_stop_is_idempotent(tmp_path: Path) -> None:
    rec, drv = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    drv.fire()
    a1 = rec.stop()
    a2 = rec.stop()
    assert a1.capture_id == a2.capture_id
    assert len(a1.entries) == len(a2.entries) == 1


# ---------------------------------------------------------------------------
# HAR + internal serialization
# ---------------------------------------------------------------------------


def test_to_har_json_emits_har_1_2_shape(tmp_path: Path) -> None:
    rec, drv = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    drv.fire(url="https://example.com/api/items")
    artifact = rec.stop()
    har = artifact.to_har_json()
    assert har["log"]["version"] == "1.2"
    assert har["log"]["creator"]["name"] == "uacp-prototype/capture"
    assert len(har["log"]["entries"]) == 1
    entry = har["log"]["entries"][0]
    assert entry["request"]["method"] == "GET"
    assert entry["request"]["url"] == "https://example.com/api/items"
    assert entry["response"]["status"] == 200
    assert "startedDateTime" in entry
    assert "time" in entry
    assert isinstance(entry["request"]["headers"], list)


def test_to_internal_json_round_trips(tmp_path: Path) -> None:
    rec, drv = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    drv.fire(url="https://example.com/api/a", body='{"a":1}')
    drv.fire(url="https://example.com/api/b", method="POST", body='{"b":2}')
    drv.set_storage_state({"cookies": [{"name": "x", "value": "y"}]})
    artifact = rec.stop()
    internal = artifact.to_internal_json()
    round_tripped = CaptureArtifact.from_internal_json(internal)
    assert round_tripped.capture_id == artifact.capture_id
    assert round_tripped.initial_url == artifact.initial_url
    assert len(round_tripped.entries) == 2
    assert round_tripped.storage_state == artifact.storage_state


def test_har_omits_storage_state_internal_includes_it(tmp_path: Path) -> None:
    rec, drv = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    drv.set_storage_state({"cookies": [{"name": "sid", "value": "secret"}]})
    artifact = rec.stop()
    har = artifact.to_har_json()
    assert "storage_state" not in json.dumps(har)
    internal = artifact.to_internal_json()
    assert internal["storage_state"]["cookies"][0]["name"] == "sid"


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def test_default_backend_is_playwright() -> None:
    rec = BrowserRecorder()
    assert rec.browser_backend == "playwright"
    assert rec.headless is False


def test_unknown_backend_raises(tmp_path: Path) -> None:
    rec = BrowserRecorder(
        browser_backend="not-a-backend",  # type: ignore[arg-type]
        in_progress_dir=tmp_path / "in-progress",
    )
    with pytest.raises(CaptureError, match="unknown browser_backend"):
        rec.start("https://example.com/")


def test_playwright_backend_raises_when_extras_missing() -> None:
    """When the optional 'capture' extras aren't installed,
    PlaywrightBackend.start raises a clear remediation error."""
    try:
        import playwright  # noqa: F401  # pragma: no cover
    except ImportError:
        pass
    else:
        pytest.skip("capture extras installed; skipping not-installed-path test")

    backend = PlaywrightBackend()
    events = _DriverEvents(
        on_request_response=lambda _e: None, on_disconnect=lambda: None
    )
    with pytest.raises(PlaywrightNotInstalledError) as ei:
        backend.start("https://example.com/", events=events, headless=True)
    assert "capture" in str(ei.value)


def test_scrapling_backend_raises_when_stealth_missing() -> None:
    """ScraplingBackend requires the 'stealth' extras at construction
    time even though it ultimately delegates to PlaywrightBackend."""
    try:
        import scrapling  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("stealth extras installed; skipping not-installed-path test")

    with pytest.raises(ScraplingCaptureNotInstalledError) as ei:
        ScraplingBackend()
    assert "stealth" in str(ei.value)


# ---------------------------------------------------------------------------
# Browser-disconnect detection
# ---------------------------------------------------------------------------


def test_browser_disconnect_fires_event(tmp_path: Path) -> None:
    rec, drv = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    assert rec.is_alive() is True
    drv.disconnect()
    assert rec.disconnect_event().is_set()
    assert rec.is_alive() is False
    artifact = rec.stop()
    assert artifact is not None


def test_disconnect_before_any_request_still_produces_artifact(tmp_path: Path) -> None:
    rec, drv = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    drv.disconnect()
    artifact = rec.stop()
    assert len(artifact.entries) == 0
    assert artifact.capture_id


# ---------------------------------------------------------------------------
# Checkpoint resilience
# ---------------------------------------------------------------------------


def test_checkpoint_recovers_after_simulated_crash(tmp_path: Path) -> None:
    """Simulate a mid-session crash by manually invoking the checkpoint
    write, then reading the artifact back via recover_in_progress."""
    rec, drv = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    drv.fire(url="https://example.com/api/x")
    drv.fire(url="https://example.com/api/y")
    rec._write_checkpoint()  # noqa: SLF001 — intentional test access

    capture_id = rec._capture_id
    assert capture_id is not None
    cp_path = tmp_path / "in-progress" / f"{capture_id}.har.tmp"
    assert cp_path.exists()

    # Pretend the process crashed before stop(); recover from disk.
    recovered = recover_in_progress(capture_id, in_progress_dir=tmp_path / "in-progress")
    assert recovered is not None
    assert len(recovered.entries) == 2
    assert recovered.metadata.get("in_progress") is True

    # Clean up the underlying recorder so the test doesn't leak state.
    rec.stop()


def test_checkpoint_cleared_on_clean_stop(tmp_path: Path) -> None:
    rec, drv = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    drv.fire()
    rec._write_checkpoint()  # noqa: SLF001
    capture_id = rec._capture_id
    assert capture_id is not None
    cp_path = tmp_path / "in-progress" / f"{capture_id}.har.tmp"
    assert cp_path.exists()

    rec.stop()
    assert not cp_path.exists(), "checkpoint should be cleaned after clean stop"


def test_recover_in_progress_returns_none_when_no_checkpoint(tmp_path: Path) -> None:
    assert recover_in_progress("nonexistent", in_progress_dir=tmp_path / "in-progress") is None


def test_recover_in_progress_raises_on_corrupt_checkpoint(tmp_path: Path) -> None:
    in_progress = tmp_path / "in-progress"
    in_progress.mkdir(parents=True)
    (in_progress / "abcd.har.tmp").write_text("not valid json")
    with pytest.raises(CaptureError, match="unreadable"):
        recover_in_progress("abcd", in_progress_dir=in_progress)


# ---------------------------------------------------------------------------
# Audit emission
# ---------------------------------------------------------------------------


def test_audit_event_on_capture_started(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    rec, drv = _make_recorder(tmp_path)
    with caplog.at_level(logging.INFO, logger="uacp.capture"):
        rec.start("https://example.com/")
    rec.stop()
    assert any("capture started" in r.message for r in caplog.records)


def test_audit_event_on_capture_stopped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    rec, drv = _make_recorder(tmp_path)
    rec.start("https://example.com/")
    drv.fire()
    drv.fire()
    drv.fire()
    with caplog.at_level(logging.INFO, logger="uacp.capture"):
        rec.stop()
    msgs = [r.message for r in caplog.records]
    assert any("capture stopped" in m and "requests=3" in m for m in msgs)


def test_audit_payloads_do_not_log_auth_headers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Per §6.6 + the brief's hard rule: audit payloads MUST scrub
    captured cookies / session tokens. The recorder's audit emit
    only carries URL + count + duration; the scrubbing helper
    handles the per-header redaction for any future audit field."""
    rec, drv = _make_recorder(tmp_path)
    with caplog.at_level(logging.INFO, logger="uacp.capture"):
        rec.start("https://example.com/")
        drv.fire(
            url="https://example.com/api/items",
            request_headers={
                "Authorization": "Bearer SUPER-SECRET-TOKEN",
                "Cookie": "sid=SECRET-SID-VALUE",
                "User-Agent": "test",
            },
        )
        rec.stop()
    full_log = "\n".join(r.message for r in caplog.records)
    assert "SUPER-SECRET-TOKEN" not in full_log
    assert "SECRET-SID-VALUE" not in full_log


# ---------------------------------------------------------------------------
# Audit-helper unit tests
# ---------------------------------------------------------------------------


def test_scrub_for_audit_redacts_known_headers() -> None:
    scrubbed = _scrub_for_audit(
        {
            "Authorization": "Bearer xyz",
            "Cookie": "sid=abc",
            "Set-Cookie": "sid=def; HttpOnly",
            "X-API-Key": "abc",
            "X-Goog-AuthUser": "0",
            "Content-Type": "application/json",
            "User-Agent": "test",
        }
    )
    assert scrubbed["Authorization"] == "<redacted>"
    assert scrubbed["Cookie"] == "<redacted>"
    assert scrubbed["Set-Cookie"] == "<redacted>"
    assert scrubbed["X-API-Key"] == "<redacted>"
    assert scrubbed["X-Goog-AuthUser"] == "<redacted>"
    assert scrubbed["Content-Type"] == "application/json"
    assert scrubbed["User-Agent"] == "test"


def test_scrub_for_audit_is_case_insensitive() -> None:
    scrubbed = _scrub_for_audit({"authorization": "Bearer xyz", "cookie": "sid=abc"})
    assert scrubbed["authorization"] == "<redacted>"
    assert scrubbed["cookie"] == "<redacted>"


def test_audit_url_pattern_strips_query_strings() -> None:
    pattern = _audit_url_pattern("https://example.com/api/items?cursor=abc&limit=50")
    assert pattern == "https://example.com/api/items"


def test_audit_url_pattern_replaces_numeric_segments() -> None:
    assert _audit_url_pattern("https://example.com/api/items/12345") == "https://example.com/api/items/:id"


# ---------------------------------------------------------------------------
# Driver factory injection — confirm the abstraction holds
# ---------------------------------------------------------------------------


def test_driver_factory_receives_backend_name(tmp_path: Path) -> None:
    received: list[str] = []

    def factory(name: str) -> FakeDriver:
        received.append(name)
        return FakeDriver(name=f"factory-{name}")

    rec = BrowserRecorder(
        browser_backend="scrapling",
        in_progress_dir=tmp_path / "in-progress",
        driver_factory=factory,
    )
    rec.start("https://example.com/")
    rec.stop()
    assert received == ["scrapling"]
