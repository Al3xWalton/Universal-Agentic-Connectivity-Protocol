"""Tests for the ``uacp capture-session`` CLI command per §3.12.

Mocks the BrowserRecorder; the CLI tests don't launch real browsers.
Coverage: argument parsing + secret-reference validation, the
orchestration loop, browser-disconnect handling, signal-handler
cleanup, persistence at the right URI.
"""

from __future__ import annotations

import io
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import uacp_prototype.cli as cli_module
from uacp_prototype.capture import BrowserRecorder, CaptureArtifact, CaptureError
from uacp_prototype.capture.recorder import HarEntry, capture_id_for
from uacp_prototype.security.secrets import SecretURI


# ---------------------------------------------------------------------------
# _validate_secret_ref
# ---------------------------------------------------------------------------


def test_validate_secret_ref_accepts_local_keyring() -> None:
    uri = cli_module._validate_secret_ref("secret://local-keyring/example-capture")
    assert uri.store == "local-keyring"
    assert uri.id == "example-capture"


def test_validate_secret_ref_rejects_missing_scheme() -> None:
    with pytest.raises(ValueError, match="secret:// URI"):
        cli_module._validate_secret_ref("local-keyring/example")


def test_validate_secret_ref_rejects_unknown_store() -> None:
    with pytest.raises(ValueError, match="not supported"):
        cli_module._validate_secret_ref("secret://vault/example")


def test_validate_secret_ref_rejects_field_selector() -> None:
    with pytest.raises(ValueError, match="#field"):
        cli_module._validate_secret_ref("secret://local-keyring/example#field")


# ---------------------------------------------------------------------------
# Mock recorder for orchestration tests
# ---------------------------------------------------------------------------


class _MockRecorder:
    """Stand-in BrowserRecorder for CLI tests.

    Implements the same surface (start / stop / requests_captured /
    is_alive / disconnect_event) without launching a real browser.
    Tests drive its lifecycle by setting flags + calling stop.
    """

    def __init__(self, *, request_count: int = 3, fire_disconnect: bool = False) -> None:
        self._started = False
        self._stopped = False
        self._request_count = request_count
        self._fire_disconnect = fire_disconnect
        self._disconnect_event = threading.Event()
        self.start_initial_url: str | None = None
        self.start_should_raise: Exception | None = None
        self.stop_should_raise: Exception | None = None

    def start(self, initial_url: str) -> None:
        if self.start_should_raise is not None:
            raise self.start_should_raise
        self._started = True
        self.start_initial_url = initial_url
        if self._fire_disconnect:
            # Simulate browser closing immediately after start.
            self._disconnect_event.set()

    def requests_captured(self) -> int:
        return self._request_count

    def is_alive(self) -> bool:
        return self._started and not self._stopped and not self._disconnect_event.is_set()

    def disconnect_event(self) -> threading.Event:
        return self._disconnect_event

    def stop(self) -> CaptureArtifact:
        if self.stop_should_raise is not None:
            raise self.stop_should_raise
        if not self._started:
            raise CaptureError("MockRecorder.stop: not started")
        self._stopped = True
        when = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        cid = capture_id_for(self.start_initial_url or "https://example.com/", when)
        entries = [
            HarEntry(
                started_at=when,
                time_ms=10.0,
                request={
                    "method": "GET",
                    "url": f"{self.start_initial_url}api/{i}",
                    "headers": {},
                    "body": None,
                },
                response={
                    "status": 200,
                    "status_text": "OK",
                    "headers": {"Content-Type": "application/json"},
                    "body": "{}",
                },
            )
            for i in range(self._request_count)
        ]
        return CaptureArtifact(
            capture_id=cid,
            captured_at=when,
            browser_backend="mock",
            initial_url=self.start_initial_url or "",
            final_url=self.start_initial_url or "",
            entries=entries,
            storage_state={"cookies": []},
            metadata={"user_agent": "mock"},
        )


# ---------------------------------------------------------------------------
# _await_user_signal_or_browser_close
# ---------------------------------------------------------------------------


def test_await_returns_user_stop_when_enter_pressed() -> None:
    rec = _MockRecorder()
    rec.start("https://example.com/")
    stdin = io.StringIO("\n")  # Enter pressed
    stdout = io.StringIO()
    sleeps: list[float] = []

    def fast_sleep(s: float) -> None:
        sleeps.append(s)
        # Don't actually sleep; let the loop spin until the daemon
        # thread reads the newline and sets the event.

    result = cli_module._await_user_signal_or_browser_close(
        recorder=rec,
        stdin=stdin,
        stdout=stdout,
        progress_interval=100.0,
        poll_interval=0.01,
        sleep=fast_sleep,
    )
    assert result == "user_stop"


def test_await_returns_browser_closed_on_disconnect() -> None:
    rec = _MockRecorder()
    rec.start("https://example.com/")
    rec._disconnect_event.set()  # disconnect already fired
    stdin = io.StringIO("")  # never reads
    stdout = io.StringIO()

    result = cli_module._await_user_signal_or_browser_close(
        recorder=rec,
        stdin=stdin,
        stdout=stdout,
        progress_interval=100.0,
        poll_interval=0.01,
        sleep=lambda _s: None,
    )
    assert result == "browser_closed"


class _BlockingStdin:
    """Stand-in for sys.stdin that blocks readline indefinitely so
    the daemon stdin-reader thread never fires enter_event."""

    def readline(self) -> str:
        # Block forever — the daemon thread is daemon=True so it
        # exits with the test process anyway.
        threading.Event().wait()
        return ""


def test_await_prints_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _MockRecorder(request_count=7)
    rec.start("https://example.com/")
    stdin = _BlockingStdin()
    stdout = io.StringIO()

    # First time() call seeds last_progress at 0. After the first
    # progress print fires, set disconnect_event so the loop exits
    # cleanly via the browser_closed branch.
    state = {"calls": 0}

    def fake_time() -> float:
        state["calls"] += 1
        if state["calls"] == 1:
            return 0.0
        if state["calls"] == 2:
            return 100.0  # triggers the progress print
        rec._disconnect_event.set()  # noqa: SLF001 — exit the loop
        return 200.0

    monkeypatch.setattr(cli_module.time, "time", fake_time)

    result = cli_module._await_user_signal_or_browser_close(
        recorder=rec,
        stdin=stdin,
        stdout=stdout,
        progress_interval=1.0,
        poll_interval=0.01,
        sleep=lambda _s: None,
    )
    assert result == "browser_closed"
    out = stdout.getvalue()
    assert "Captured 7 request(s)" in out


# ---------------------------------------------------------------------------
# _run_capture_session — full orchestration with mocked recorder
# ---------------------------------------------------------------------------


def test_run_capture_session_persists_artifact_and_returns_zero(tmp_path: Path) -> None:
    rec = _MockRecorder(request_count=4)
    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    uri = SecretURI(store="local-keyring", id="my-capture")

    # Patch base_dir so persistence lands inside tmp_path rather than
    # the user's real ~/.uacp.
    import uacp_prototype.capture.storage as storage_module
    from uacp_prototype.security.secrets import LocalKeyringStore

    real_default = LocalKeyringStore.default
    LocalKeyringStore.default = classmethod(  # type: ignore[assignment]
        lambda cls: cls(base_dir=tmp_path / "secrets")
    )
    try:
        rc = cli_module._run_capture_session(
            recorder=rec,
            initial_url="https://example.com/",
            output_uri=uri,
            stdin=stdin,
            stdout=stdout,
        )
    finally:
        LocalKeyringStore.default = real_default  # type: ignore[assignment]

    assert rc == 0
    out = stdout.getvalue()
    assert "Opening https://example.com/" in out
    assert "Captured 4 request(s)" in out
    assert "secret://local-keyring/my-capture" in out

    blob_path = tmp_path / "secrets" / "my-capture.enc"
    assert blob_path.exists()


def test_run_capture_session_handles_browser_disconnect(tmp_path: Path) -> None:
    rec = _MockRecorder(request_count=2, fire_disconnect=True)
    stdin = io.StringIO("")
    stdout = io.StringIO()
    uri = SecretURI(store="local-keyring", id="disconnect-capture")

    from uacp_prototype.security.secrets import LocalKeyringStore

    real_default = LocalKeyringStore.default
    LocalKeyringStore.default = classmethod(  # type: ignore[assignment]
        lambda cls: cls(base_dir=tmp_path / "secrets")
    )
    try:
        rc = cli_module._run_capture_session(
            recorder=rec,
            initial_url="https://example.com/",
            output_uri=uri,
            stdin=stdin,
            stdout=stdout,
        )
    finally:
        LocalKeyringStore.default = real_default  # type: ignore[assignment]

    assert rc == 0
    out = stdout.getvalue()
    assert "Browser closed" in out


def test_run_capture_session_surfaces_start_failure(tmp_path: Path) -> None:
    rec = _MockRecorder()
    rec.start_should_raise = CaptureError("navigation to 'https://nope/' failed")
    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    uri = SecretURI(store="local-keyring", id="failed")

    rc = cli_module._run_capture_session(
        recorder=rec,
        initial_url="https://nope/",
        output_uri=uri,
        stdin=stdin,
        stdout=stdout,
    )
    assert rc == 3


def test_run_capture_session_surfaces_stop_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = _MockRecorder()
    rec.stop_should_raise = CaptureError("driver crashed during stop")
    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    uri = SecretURI(store="local-keyring", id="stop-fail")
    rc = cli_module._run_capture_session(
        recorder=rec,
        initial_url="https://example.com/",
        output_uri=uri,
        stdin=stdin,
        stdout=stdout,
    )
    assert rc == 4


# ---------------------------------------------------------------------------
# main() argument parsing
# ---------------------------------------------------------------------------


def test_main_capture_session_validates_output_secret_ref(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_module.main(
        ["capture-session", "--initial-url", "https://example.com/", "--output", "not-a-uri"]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "secret:// URI" in err


def test_main_capture_session_rejects_unknown_store(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_module.main(
        [
            "capture-session",
            "--initial-url",
            "https://example.com/",
            "--output",
            "secret://vault/x",
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "not supported" in err


def test_main_capture_session_routes_through_recorder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Patch _build_capture_recorder to return a MockRecorder; verify
    the full main() → capture-session flow runs end-to-end."""
    captured_args: list[Any] = []

    def fake_build(args: Any) -> _MockRecorder:
        captured_args.append(args)
        return _MockRecorder(request_count=5)

    monkeypatch.setattr(cli_module, "_build_capture_recorder", fake_build)

    # Patch sys.stdin to "Enter pressed" so the await loop returns.
    monkeypatch.setattr(cli_module.sys, "stdin", io.StringIO("\n"))

    # Persist into tmp_path.
    from uacp_prototype.security.secrets import LocalKeyringStore

    real_default = LocalKeyringStore.default
    LocalKeyringStore.default = classmethod(  # type: ignore[assignment]
        lambda cls: cls(base_dir=tmp_path / "secrets")
    )

    try:
        rc = cli_module.main(
            [
                "capture-session",
                "--initial-url",
                "https://example.com/",
                "--output",
                "secret://local-keyring/end-to-end-test",
                "--browser",
                "playwright",
                "--provider",
                "test",
            ]
        )
    finally:
        LocalKeyringStore.default = real_default  # type: ignore[assignment]

    assert rc == 0
    assert len(captured_args) == 1
    args = captured_args[0]
    assert args.initial_url == "https://example.com/"
    assert args.browser == "playwright"
    assert args.provider == "test"


def test_main_capture_session_default_browser_is_playwright(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str] = []

    def fake_build(args: Any) -> _MockRecorder:
        seen.append(args.browser)
        return _MockRecorder(request_count=1)

    monkeypatch.setattr(cli_module, "_build_capture_recorder", fake_build)
    monkeypatch.setattr(cli_module.sys, "stdin", io.StringIO("\n"))

    from uacp_prototype.security.secrets import LocalKeyringStore

    real_default = LocalKeyringStore.default
    LocalKeyringStore.default = classmethod(  # type: ignore[assignment]
        lambda cls: cls(base_dir=tmp_path / "secrets")
    )
    try:
        cli_module.main(
            [
                "capture-session",
                "--initial-url",
                "https://example.com/",
                "--output",
                "secret://local-keyring/default-browser",
            ]
        )
    finally:
        LocalKeyringStore.default = real_default  # type: ignore[assignment]
    assert seen == ["playwright"]


def test_main_capture_session_browser_choice_validated(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """argparse rejects unknown --browser values with exit code 2."""
    with pytest.raises(SystemExit) as ei:
        cli_module.main(
            [
                "capture-session",
                "--initial-url",
                "https://example.com/",
                "--output",
                "secret://local-keyring/x",
                "--browser",
                "phantom",
            ]
        )
    assert ei.value.code == 2
