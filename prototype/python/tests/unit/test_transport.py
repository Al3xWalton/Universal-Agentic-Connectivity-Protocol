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
