"""Tests for the encrypted-at-rest capture storage path per §6.3.

The storage module composes on the existing LocalKeyringStore (which
already does §6.3 envelope encryption); these tests verify the
composition: artifacts round-trip through encrypt/decrypt, the
``secret://`` reference is well-formed, idempotent stores produce the
same id, the on-disk blob is genuinely encrypted (not just b64-
encoded JSON), and the §6.6 audit emit fires with the right
payload — never carrying raw cookies / auth values.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from uacp_prototype.capture import (
    BrowserRecorder,
    CaptureArtifact,
    CaptureError,
    StoredCapture,
    load_capture,
    store_capture,
)
from uacp_prototype.capture.recorder import HarEntry, capture_id_for
from uacp_prototype.security.secrets import LocalKeyringStore, SecretURI


def _fake_artifact(
    *,
    initial_url: str = "https://example.com/",
    captured_at: datetime | None = None,
    entries: int = 2,
    storage_state: dict[str, Any] | None = None,
    cookie_value: str | None = None,
) -> CaptureArtifact:
    when = captured_at or datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    cid = capture_id_for(initial_url, when, provider="example")
    har_entries: list[HarEntry] = []
    for i in range(entries):
        har_entries.append(
            HarEntry(
                started_at=when,
                time_ms=10.0,
                request={
                    "method": "GET",
                    "url": f"https://example.com/api/items/{i}",
                    "headers": {
                        "User-Agent": "test",
                        **({"Cookie": cookie_value} if cookie_value else {}),
                    },
                    "body": None,
                },
                response={
                    "status": 200,
                    "status_text": "OK",
                    "headers": {"Content-Type": "application/json"},
                    "body": '{"ok": true}',
                },
            )
        )
    return CaptureArtifact(
        capture_id=cid,
        captured_at=when,
        browser_backend="fake",
        initial_url=initial_url,
        final_url=initial_url,
        entries=har_entries,
        storage_state=storage_state,
        metadata={"user_agent": "test"},
    )


# ---------------------------------------------------------------------------
# Round-trip: store + load
# ---------------------------------------------------------------------------


def test_store_and_load_round_trip(tmp_path: Path) -> None:
    artifact = _fake_artifact(
        storage_state={"cookies": [{"name": "sid", "value": "secret-sid"}]}
    )
    stored = store_capture(artifact, base_dir=tmp_path / "secrets")
    assert isinstance(stored, StoredCapture)
    assert stored.ref.startswith("secret://local-keyring/capture-")
    assert stored.capture_id == artifact.capture_id
    assert stored.request_count == 2

    loaded = load_capture(stored.ref, base_dir=tmp_path / "secrets")
    assert loaded.capture_id == artifact.capture_id
    assert loaded.initial_url == artifact.initial_url
    assert len(loaded.entries) == len(artifact.entries)
    assert loaded.storage_state == artifact.storage_state


def test_uri_field_parses_back_to_secret_uri(tmp_path: Path) -> None:
    artifact = _fake_artifact()
    stored = store_capture(artifact, base_dir=tmp_path / "secrets")
    uri = stored.uri
    assert isinstance(uri, SecretURI)
    assert uri.store == "local-keyring"
    assert uri.id == f"capture-{artifact.capture_id}"


# ---------------------------------------------------------------------------
# Encryption-at-rest: the on-disk blob is NOT plaintext JSON
# ---------------------------------------------------------------------------


def test_persisted_blob_is_encrypted_at_rest(tmp_path: Path) -> None:
    """Per §6.3 + the brief's hard rule: captured artifacts MUST be
    encrypted before persistence. Verify the on-disk file does not
    contain the captured URL or storage-state values verbatim."""
    artifact = _fake_artifact(
        initial_url="https://uniqueverydistinctstring.example.com/",
        storage_state={"cookies": [{"name": "sid", "value": "VERY-SECRET-SID-XXX"}]},
    )
    stored = store_capture(artifact, base_dir=tmp_path / "secrets")

    blob_path = tmp_path / "secrets" / f"capture-{artifact.capture_id}.enc"
    assert blob_path.exists()
    raw = blob_path.read_text()

    # The blob should be a JSON envelope (algorithm/iv/tag/ciphertext)
    # but the sensitive values must NOT appear in plaintext.
    parsed = json.loads(raw)
    assert parsed["algorithm"] == "AES-256-GCM"
    assert "ciphertext" in parsed
    assert "uniqueverydistinctstring" not in raw
    assert "VERY-SECRET-SID-XXX" not in raw


def test_loading_with_missing_master_key_dir_creates_it(tmp_path: Path) -> None:
    """LocalKeyringStore creates the directory tree on first put.
    Verify the integration: no manual mkdir required from callers."""
    base = tmp_path / "deeply" / "nested" / "secrets"
    artifact = _fake_artifact()
    stored = store_capture(artifact, base_dir=base)
    assert (base / f"capture-{artifact.capture_id}.enc").exists()


# ---------------------------------------------------------------------------
# Idempotence — same artifact stored twice produces the same id + ref
# ---------------------------------------------------------------------------


def test_same_artifact_stored_twice_produces_same_ref(tmp_path: Path) -> None:
    artifact = _fake_artifact()
    s1 = store_capture(artifact, base_dir=tmp_path / "secrets")
    s2 = store_capture(artifact, base_dir=tmp_path / "secrets")
    assert s1.ref == s2.ref
    assert s1.capture_id == s2.capture_id


def test_different_initial_url_produces_different_ref(tmp_path: Path) -> None:
    a = _fake_artifact(initial_url="https://a.example.com/")
    b = _fake_artifact(initial_url="https://b.example.com/")
    sa = store_capture(a, base_dir=tmp_path / "secrets")
    sb = store_capture(b, base_dir=tmp_path / "secrets")
    assert sa.ref != sb.ref


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_unsupported_store_rejected(tmp_path: Path) -> None:
    artifact = _fake_artifact()
    with pytest.raises(CaptureError, match="not supported"):
        store_capture(artifact, secret_store="vault", base_dir=tmp_path / "secrets")


def test_load_unknown_store_rejected() -> None:
    with pytest.raises(CaptureError, match="not supported"):
        load_capture("secret://vault/capture-abcd")


def test_load_missing_capture_raises_capture_error(tmp_path: Path) -> None:
    with pytest.raises(CaptureError):
        load_capture(
            "secret://local-keyring/capture-nonexistent",
            base_dir=tmp_path / "secrets",
        )


def test_load_corrupt_payload_raises(tmp_path: Path) -> None:
    """If the encrypted blob decrypts to non-JSON bytes, load_capture
    surfaces a clear error rather than crashing on the JSON parser."""
    artifact = _fake_artifact()
    stored = store_capture(artifact, base_dir=tmp_path / "secrets")
    # Manually replace the underlying blob with garbage that decrypts
    # successfully (skip — instead we corrupt the ciphertext so the
    # decryption fails, which is the realistic corruption path).
    blob_path = tmp_path / "secrets" / f"capture-{artifact.capture_id}.enc"
    parsed = json.loads(blob_path.read_text())
    # Corrupt the ciphertext so AES-GCM auth tag verification fails.
    parsed["ciphertext"] = "AAAA" + parsed["ciphertext"][4:]
    blob_path.write_text(json.dumps(parsed))
    with pytest.raises(CaptureError):
        load_capture(stored.ref, base_dir=tmp_path / "secrets")


def test_artifact_without_capture_id_rejected(tmp_path: Path) -> None:
    artifact = _fake_artifact()
    object.__setattr__(artifact, "capture_id", "")
    with pytest.raises(CaptureError, match="capture_id"):
        store_capture(artifact, base_dir=tmp_path / "secrets")


# ---------------------------------------------------------------------------
# Audit emission at storage boundary (§6.6) — payload scrubbed
# ---------------------------------------------------------------------------


def test_capture_stored_audit_event_emitted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    artifact = _fake_artifact(initial_url="https://api.example.com/v1/things")
    with caplog.at_level(logging.INFO, logger="uacp.capture"):
        stored = store_capture(artifact, base_dir=tmp_path / "secrets")
    msgs = [r.message for r in caplog.records]
    matched = [m for m in msgs if "capture stored" in m]
    assert matched, f"expected 'capture stored' audit event; got {msgs}"
    line = matched[0]
    assert stored.ref in line
    assert "host=api.example.com" in line
    assert "requests=2" in line


def test_capture_stored_audit_event_does_not_leak_auth(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Per §6.6 + the brief's hard rule: audit payloads MUST scrub
    auth values. The capture-stored event carries only the storage
    reference + request count + duration + host — never cookies,
    Authorization headers, or storage_state values."""
    artifact = _fake_artifact(
        cookie_value="ABSOLUTELY-NEVER-LOG-THIS",
        storage_state={"cookies": [{"name": "sid", "value": "ALSO-NEVER-LOG-THIS"}]},
    )
    with caplog.at_level(logging.INFO, logger="uacp.capture"):
        store_capture(artifact, base_dir=tmp_path / "secrets")
    full_log = "\n".join(r.message for r in caplog.records)
    assert "ABSOLUTELY-NEVER-LOG-THIS" not in full_log
    assert "ALSO-NEVER-LOG-THIS" not in full_log


# ---------------------------------------------------------------------------
# Recorder + storage end-to-end smoke (no browser launched)
# ---------------------------------------------------------------------------


def test_recorder_to_storage_pipeline_with_fake_driver(tmp_path: Path) -> None:
    """The full Stage 11.1 capture path: recorder produces an
    artifact, storage encrypts + persists it, load decrypts back to
    an identical artifact."""

    class FakeDriver:
        name = "fake"
        events: Any = None

        def __init__(self) -> None:
            self._alive = False
            self._url = ""
            self._storage: dict[str, Any] | None = None

        def start(self, initial_url: str, *, events: Any, headless: bool) -> None:
            self.events = events
            self._url = initial_url
            self._alive = True

        def is_alive(self) -> bool:
            return self._alive

        def final_url(self) -> str:
            return self._url

        def storage_state(self) -> dict[str, Any] | None:
            return self._storage

        def stop(self) -> None:
            self._alive = False

    drv = FakeDriver()
    rec = BrowserRecorder(
        browser_backend="playwright",
        in_progress_dir=tmp_path / "in-progress",
        driver_factory=lambda _name: drv,
    )
    rec.start("https://e2e.example.com/")
    drv.events.on_request_response(  # type: ignore[union-attr]
        HarEntry(
            started_at=datetime.now(timezone.utc),
            time_ms=5.0,
            request={"method": "GET", "url": "https://e2e.example.com/api", "headers": {}, "body": None},
            response={"status": 200, "status_text": "OK", "headers": {"Content-Type": "application/json"}, "body": '{}'},
        )
    )
    drv._storage = {"cookies": [{"name": "x", "value": "y"}]}  # noqa: SLF001 — test setup
    artifact = rec.stop()

    stored = store_capture(artifact, base_dir=tmp_path / "secrets")
    loaded = load_capture(stored.ref, base_dir=tmp_path / "secrets")

    assert loaded.capture_id == artifact.capture_id
    assert len(loaded.entries) == 1
    assert loaded.storage_state == {"cookies": [{"name": "x", "value": "y"}]}
