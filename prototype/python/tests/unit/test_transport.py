"""Tests for the pluggable transport abstraction per §4.10.

Three layers:

  - Transport-Protocol-level tests: HttpxTransport behaves identically
    to the v1.0 dispatch path (verified via respx mocks); the
    DispatchClient defaults to HttpxTransport when no transport is
    supplied; the back-compat httpx_client= alias still works.
  - ScraplingTransport-not-installed tests: instantiating the stealth
    transport without the optional ``stealth`` extras raises
    ScraplingNotInstalledError with a clear remediation message.
    These tests run in environments without the extras (the default
    test environment) and verify the graceful-failure path mandated
    by §4.10's "fall back with a warning, not refuse to dispatch"
    rule when the dispatcher selects a transport.
  - .uacp transport-field selection tests: the artifact's optional
    ``dispatch.transport`` field round-trips through the spec loader
    and is consumed by the dispatch-client factory introduced in
    Commit 5.

Tests under ``@pytest.mark.scrapling`` exercise the live Scrapling
backend; they're skipped by default. Run with::

    uv sync --extra stealth
    uv run pytest tests/unit/test_transport.py -m scrapling
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from uacp_prototype.dispatch.client import DispatchClient, DispatchSuccess
from uacp_prototype.dispatch.transport import (
    HttpxTransport,
    ScraplingNotInstalledError,
    ScraplingTransport,
    Transport,
    is_scrapling_available,
)
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"


# ---------------------------------------------------------------------------
# Protocol membership
# ---------------------------------------------------------------------------


def test_httpx_transport_implements_transport_protocol() -> None:
    t = HttpxTransport()
    assert isinstance(t, Transport)
    assert t.name == "default"
    t.close()


def test_httpx_transport_request_returns_httpx_response() -> None:
    """The Transport contract requires an httpx.Response; verifying via
    respx so the dispatch loop's response-handling code stays
    transport-agnostic."""
    with respx.mock:
        respx.get("https://example.com/probe").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        t = HttpxTransport(timeout=2.0)
        resp = t.request("GET", "https://example.com/probe")
        try:
            assert isinstance(resp, httpx.Response)
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
        finally:
            t.close()


def test_httpx_transport_close_is_idempotent_for_external_clients() -> None:
    """When an httpx.Client is supplied externally, the transport
    MUST NOT close it on its own — caller owns the lifecycle."""
    client = httpx.Client()
    try:
        t = HttpxTransport(client=client)
        t.close()
        assert not client.is_closed, "external httpx.Client must not be closed by HttpxTransport"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# DispatchClient default-transport selection + back-compat
# ---------------------------------------------------------------------------


def _load_first_artifact():
    return load(EXAMPLES_DIR / "github" / "repos-get.uacp")


def _trivial_resolver():
    return {"api_key": "test"}


def _trivial_auth():
    from uacp_prototype.auth.api_key import APIKeyHeaderConfig, APIKeyHeaderMethod

    return APIKeyHeaderMethod(
        config=APIKeyHeaderConfig(header_name="Authorization", header_prefix="Bearer ")
    )


def test_dispatch_client_defaults_to_httpx_transport() -> None:
    artifact = _load_first_artifact()
    client = DispatchClient(
        artifact,
        auth_method=_trivial_auth(),
        credential_resolver=_trivial_resolver,
    )
    try:
        assert isinstance(client.transport, HttpxTransport)
        assert client.transport.name == "default"
    finally:
        client.close()


def test_dispatch_client_accepts_explicit_transport() -> None:
    artifact = _load_first_artifact()
    explicit = HttpxTransport(timeout=5.0)
    client = DispatchClient(
        artifact,
        auth_method=_trivial_auth(),
        credential_resolver=_trivial_resolver,
        transport=explicit,
    )
    try:
        assert client.transport is explicit
    finally:
        client.close()


def test_dispatch_client_back_compat_httpx_client_alias() -> None:
    """Existing tests pass ``httpx_client=`` directly. The alias still
    works in v1.1 — it wraps the supplied client in HttpxTransport."""
    artifact = _load_first_artifact()
    raw_client = httpx.Client()
    try:
        client = DispatchClient(
            artifact,
            auth_method=_trivial_auth(),
            credential_resolver=_trivial_resolver,
            httpx_client=raw_client,
        )
        try:
            assert isinstance(client.transport, HttpxTransport)
        finally:
            client.close()
    finally:
        raw_client.close()


def test_dispatch_client_rejects_both_transport_and_httpx_client() -> None:
    artifact = _load_first_artifact()
    raw_client = httpx.Client()
    try:
        with pytest.raises(ValueError, match="not both"):
            DispatchClient(
                artifact,
                auth_method=_trivial_auth(),
                credential_resolver=_trivial_resolver,
                transport=HttpxTransport(),
                httpx_client=raw_client,
            )
    finally:
        raw_client.close()


# ---------------------------------------------------------------------------
# ScraplingTransport — graceful failure when extras aren't installed
# ---------------------------------------------------------------------------


def test_scrapling_transport_raises_when_extras_missing() -> None:
    """Per §4.10 graceful-degradation: when the stealth extras aren't
    installed, instantiating ScraplingTransport raises a clear,
    actionable error. The dispatcher's selection logic catches this
    and surfaces a user-visible warning + falls back to httpx.
    """
    if is_scrapling_available():
        pytest.skip("stealth extras installed; skipping not-installed-path test")
    with pytest.raises(ScraplingNotInstalledError) as excinfo:
        ScraplingTransport()
    msg = str(excinfo.value)
    assert "stealth" in msg
    assert "uacp-prototype" in msg or "scrapling" in msg.lower()


def test_is_scrapling_available_reports_extras_state() -> None:
    """Smoke test the helper returns a bool. The actual value depends
    on whether the test environment has the extras installed."""
    assert isinstance(is_scrapling_available(), bool)


# ---------------------------------------------------------------------------
# .uacp transport field — round-trip through the spec loader
# ---------------------------------------------------------------------------


def test_artifact_transport_field_round_trips_through_loader(tmp_path: Path) -> None:
    """The optional dispatch.transport field is preserved on load
    (forward-compat per §3.11; explicit recognition per §4.10)."""
    src = EXAMPLES_DIR / "notebooklm" / "list-notebooks.uacp"
    raw = json.loads(src.read_text())
    raw["dispatch"]["transport"] = "stealth"
    out = tmp_path / "list-with-transport.uacp"
    out.write_text(json.dumps(raw))

    artifact = load(out)
    extra = artifact.dispatch.model_extra or {}
    assert extra.get("transport") == "stealth"


def test_artifact_transport_field_default_is_omission() -> None:
    """An artifact without dispatch.transport is the v1.0 baseline —
    the field is absent and implementations apply their default
    transport per §4.10."""
    artifact = load(EXAMPLES_DIR / "github" / "repos-get.uacp")
    extra = artifact.dispatch.model_extra or {}
    assert "transport" not in extra


# ---------------------------------------------------------------------------
# §4.10 conformance: HTTPS-only at the transport boundary
# ---------------------------------------------------------------------------


def test_scrapling_transport_refuses_http_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense-in-depth: even though the dispatcher already enforces
    HTTPS, the stealth transport refuses http:// URLs at the transport
    boundary. Verified by stubbing the lazy import so we don't need
    the extras installed.
    """
    # Stub the import inside ScraplingTransport.__init__ so we can
    # exercise the request-time check without real Scrapling.
    fake_fetcher = type("FakeFetcher", (), {})
    import sys, types

    fake_module = types.ModuleType("scrapling.fetchers")
    fake_module.StealthyFetcher = fake_fetcher
    monkeypatch.setitem(sys.modules, "scrapling", types.ModuleType("scrapling"))
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", fake_module)

    t = ScraplingTransport()
    with pytest.raises(httpx.HTTPError, match="HTTPS"):
        t.request("GET", "http://example.com/forbidden")


# ---------------------------------------------------------------------------
# Live Scrapling backend (optional; gated on extras)
# ---------------------------------------------------------------------------


@pytest.mark.scrapling
def test_scrapling_transport_instantiates_when_extras_installed() -> None:
    """When the stealth extras are installed, instantiation succeeds
    and the transport reports name='stealth'. No live network call —
    instantiation only."""
    t = ScraplingTransport()
    assert t.name == "stealth"
    t.close()


# ---------------------------------------------------------------------------
# Selection logic — select_transport_for_artifact
# ---------------------------------------------------------------------------


from uacp_prototype.dispatch.transport import select_transport_for_artifact


def test_selection_no_field_no_session_cookie_returns_httpx() -> None:
    """The default path: artifact without dispatch.transport AND
    auth.method != session_cookie → HttpxTransport."""
    artifact = load(EXAMPLES_DIR / "github" / "repos-get.uacp")
    t = select_transport_for_artifact(artifact)
    assert isinstance(t, HttpxTransport)
    t.close()


def test_selection_explicit_default_returns_httpx(tmp_path: Path) -> None:
    src = EXAMPLES_DIR / "notebooklm" / "list-notebooks.uacp"
    raw = json.loads(src.read_text())
    raw["dispatch"]["transport"] = "default"
    out = tmp_path / "list-default.uacp"
    out.write_text(json.dumps(raw))
    artifact = load(out)
    t = select_transport_for_artifact(artifact)
    assert isinstance(t, HttpxTransport)
    t.close()


def test_selection_explicit_stealth_no_extras_falls_back_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per §4.10 graceful-degradation: when 'stealth' is requested but
    the extras aren't installed, the selector logs a warning and
    returns HttpxTransport rather than refusing to dispatch."""
    if is_scrapling_available():
        pytest.skip("stealth extras installed; skipping fallback-path test")
    src = EXAMPLES_DIR / "notebooklm" / "list-notebooks.uacp"
    artifact = load(src)
    # The example already declares dispatch.transport=stealth as of v1.1.
    extra = artifact.dispatch.model_extra or {}
    assert extra.get("transport") == "stealth"

    import logging

    with caplog.at_level(logging.WARNING, logger="uacp.dispatch.transport"):
        t = select_transport_for_artifact(artifact)
    assert isinstance(t, HttpxTransport)
    assert any(
        "stealth" in r.message and "extras" in r.message for r in caplog.records
    )
    t.close()


def test_selection_unknown_transport_falls_back_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Unknown transport identifier (including x-namespaced) → fall
    back to HttpxTransport with a warning per §4.10."""
    src = EXAMPLES_DIR / "github" / "repos-get.uacp"
    raw = json.loads(src.read_text())
    raw["dispatch"]["transport"] = "x-experimental-backend"
    out = tmp_path / "experimental.uacp"
    out.write_text(json.dumps(raw))
    artifact = load(out)

    import logging

    with caplog.at_level(logging.WARNING, logger="uacp.dispatch.transport"):
        t = select_transport_for_artifact(artifact)
    assert isinstance(t, HttpxTransport)
    assert any("x-experimental-backend" in r.message for r in caplog.records)
    t.close()


def test_selection_session_cookie_no_extras_returns_httpx() -> None:
    """When session_cookie auth is declared but the stealth extras
    aren't installed, the auth-method affinity quietly degrades to
    HttpxTransport. The user-visible signal is that the transport is
    httpx; provider-side anti-bot measures will likely cause
    DispatchErrors visible at runtime, which surface the gap."""
    if is_scrapling_available():
        pytest.skip("stealth extras installed; skipping no-extras-path test")
    # Use the chat-message NotebookLM example which lives at the same
    # transport=stealth posture; the field is honored explicitly above.
    # Here we test the auth-method affinity path by stripping the
    # transport field.
    src = EXAMPLES_DIR / "notebooklm" / "list-notebooks.uacp"
    raw = json.loads(src.read_text())
    raw["dispatch"].pop("transport", None)
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".uacp", delete=False) as f:
        f.write(json.dumps(raw))
        path = Path(f.name)
    artifact = load(path)
    assert artifact.authentication.method == "session_cookie"
    t = select_transport_for_artifact(artifact)
    assert isinstance(t, HttpxTransport)  # graceful degradation
    t.close()


def test_notebooklm_examples_declare_stealth_transport() -> None:
    """The NotebookLM examples ship with dispatch.transport='stealth'
    as of v1.1, advertising that they target a fingerprint-defended
    Provider. Fall-back to httpx is graceful per §4.10 when the
    extras aren't installed."""
    for name in ("list-notebooks.uacp", "send-chat-message.uacp"):
        artifact = load(EXAMPLES_DIR / "notebooklm" / name)
        extra = artifact.dispatch.model_extra or {}
        assert extra.get("transport") == "stealth", f"{name} should declare transport=stealth"
