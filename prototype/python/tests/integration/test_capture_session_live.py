"""Live capture-session integration tests per §3.12.

Marked ``@pytest.mark.capture_integration``; skipped by default.
Launches a real Playwright-driven Chromium against a safe target
(httpbin.org) and asserts the capture pipeline records traffic
end-to-end. Run with::

    uv sync --extra capture
    uv run playwright install chromium
    uv run pytest tests/integration/test_capture_session_live.py -m capture_integration
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from uacp_prototype.capture import (
    BrowserRecorder,
    CaptureArtifact,
    PlaywrightNotInstalledError,
    load_capture,
    store_capture,
)


pytestmark = pytest.mark.capture_integration


def _require_playwright() -> None:
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip(
            "playwright not installed — run `uv sync --extra capture && uv "
            "run playwright install chromium`"
        )


def test_live_capture_against_httpbin(tmp_path: Path) -> None:
    """End-to-end live capture against httpbin.org. Confirms:
    Playwright launches, navigates, fires request/response events,
    records at least one entry, and the artifact persists through
    encrypted storage + load_capture round-trip cleanly."""
    _require_playwright()
    rec = BrowserRecorder(
        browser_backend="playwright",
        headless=True,
        in_progress_dir=tmp_path / "in-progress",
    )
    rec.start("https://httpbin.org/anything")
    # Brief wait for the initial page-load traffic to land.
    import time

    deadline = time.time() + 10.0
    while rec.requests_captured() == 0 and time.time() < deadline:
        time.sleep(0.2)
    artifact = rec.stop()

    assert isinstance(artifact, CaptureArtifact)
    assert len(artifact.entries) >= 1
    first = artifact.entries[0]
    assert first.request["url"].startswith("https://httpbin.org/")
    assert first.response["status"] in {200, 301, 302, 304}

    # Persist + reload to confirm encryption-at-rest survives the
    # live-Playwright path identically to the unit-test path.
    stored = store_capture(artifact, base_dir=tmp_path / "secrets")
    loaded = load_capture(stored.ref, base_dir=tmp_path / "secrets")
    assert loaded.capture_id == artifact.capture_id
    assert len(loaded.entries) == len(artifact.entries)


def test_live_capture_initial_url_unreachable_surfaces_clearly(tmp_path: Path) -> None:
    """Per the brief: 'Initial URL is unreachable — surface the
    error clearly; don't store an empty artifact.' Verified by
    pointing the recorder at a deliberately-unroutable URL."""
    _require_playwright()
    rec = BrowserRecorder(
        browser_backend="playwright",
        headless=True,
        in_progress_dir=tmp_path / "in-progress",
    )
    from uacp_prototype.capture import CaptureError

    with pytest.raises(CaptureError, match="navigation"):
        rec.start("https://this-host-does-not-exist-uacp-test-12345.invalid/")
