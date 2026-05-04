"""Tests for the dispatch client per Stage 4.

Mocks httpx via respx; mocks the sleep callable to make retries
deterministic and zero-time.
"""

from __future__ import annotations

import random
from typing import Any

import httpx
import pytest
import respx

from uacp_prototype.auth.base import AuthApplyResult, AuthMethod
from uacp_prototype.dispatch.client import (
    DispatchClient,
    DispatchError,
    DispatchSuccess,
    DispatchException,
    _compute_backoff,
    _parse_retry_after,
    _status_to_canonical_code,
    DEFAULT_MAX_ATTEMPTS,
)
from uacp_prototype.spec.loader import load_dict
from uacp_prototype.spec.models import UACPArtifact


class StaticAuth:
    """A trivial AuthMethod that adds an Authorization header from
    credentials['access_token']. Used by every test in this module.
    """

    method = "x-test-static"

    def apply(self, request: httpx.Request, *, credentials: dict[str, Any]) -> AuthApplyResult:
        token = credentials.get("access_token", "test-token")
        return AuthApplyResult(headers={"Authorization": f"Bearer {token}"})


def _silent_sleep(_secs: float) -> None:
    pass


def _artifact(operations: list[dict[str, Any]] | None = None) -> UACPArtifact:
    raw = {
        "$schema": "https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.0.0/schemas/uacp.json",
        "authentication": {
            "method": "x-test-static",
        },
        "dispatch": {
            "base_url": "https://api.example.com",
            "default_headers": {"Accept": "application/json"},
            "default_user_agent": "uacp-test/0.1",
            "default_timeout_ms": 5000,
        },
        "operations": operations
        or [
            {
                "id": "get_thing",
                "summary": "Get a thing.",
                "idempotency": "idempotent",
                "request": {
                    "method": "GET",
                    "path": "/v1/things/{id}",
                    "path_parameters": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {"id": {"type": "string"}},
                    },
                },
                "response": {
                    "200": {"description": "ok"},
                    "default": {"description": "fallback"},
                },
            }
        ],
    }
    return load_dict(raw)


def _client(art: UACPArtifact, *, sleep: Any = _silent_sleep) -> DispatchClient:
    return DispatchClient(
        art,
        auth_method=StaticAuth(),
        credential_resolver=lambda: {"access_token": "tok-1"},
        sleep=sleep,
        rng=random.Random(0),
    )


# ---------------------------------------------------------------------------
# Helpers (status mapping, backoff, retry-after parsing)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "bad_input"),
        (401, "auth_expired"),
        (403, "forbidden"),
        (404, "not_found"),
        (408, "upstream_error"),
        (409, "bad_input"),
        (422, "bad_input"),
        (429, "rate_limited"),
        (418, "bad_input"),  # generic 4xx default
        (500, "upstream_error"),
        (502, "upstream_error"),
        (503, "upstream_error"),
        (504, "upstream_error"),
        (599, "upstream_error"),
    ],
)
def test_status_to_canonical_code(status: int, code: str) -> None:
    assert _status_to_canonical_code(status) == code


def test_compute_backoff_grows_exponentially() -> None:
    rng = random.Random(0)
    delays = [
        _compute_backoff(i, jitter=0, rng=rng)
        for i in range(1, 6)
    ]
    # 250ms * 2^(i-1), capped at 5000ms
    assert delays == [0.25, 0.5, 1.0, 2.0, 4.0]


def test_compute_backoff_respects_cap() -> None:
    delay = _compute_backoff(20, jitter=0)
    assert delay == 5.0  # capped at max_delay_ms


def test_parse_retry_after_seconds() -> None:
    assert _parse_retry_after("30", now=0) == 30.0
    assert _parse_retry_after("0", now=0) == 0.0


def test_parse_retry_after_http_date() -> None:
    # Wed, 21 Oct 2026 07:28:00 GMT — far enough that the test holds across
    # any reasonable test-time clock skew.
    result = _parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT", now=0)
    assert result is not None
    assert result > 1_000_000


def test_parse_retry_after_invalid_returns_none() -> None:
    assert _parse_retry_after("not-a-date", now=0) is None
    assert _parse_retry_after(None, now=0) is None


# ---------------------------------------------------------------------------
# Composition order (§4.1)
# ---------------------------------------------------------------------------


@respx.mock
def test_dispatch_composes_url_with_path_params() -> None:
    art = _artifact()
    route = respx.get("https://api.example.com/v1/things/abc-123").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    with _client(art) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc-123"})
    assert isinstance(result, DispatchSuccess)
    assert route.called
    # default headers + user agent + auth applied
    request = route.calls[0].request
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == "uacp-test/0.1"
    assert request.headers["Authorization"] == "Bearer tok-1"


@respx.mock
def test_dispatch_serializes_json_body() -> None:
    art = _artifact(
        operations=[
            {
                "id": "create_thing",
                "summary": "Create a thing.",
                "idempotency": "not_idempotent",
                "request": {
                    "method": "POST",
                    "path": "/v1/things",
                    "body": {
                        "media_type": "application/json",
                        "schema": {"type": "object"},
                    },
                },
                "response": {"201": {"description": "created"}},
            }
        ]
    )
    route = respx.post("https://api.example.com/v1/things").mock(
        return_value=httpx.Response(201, json={"id": "new"})
    )
    with _client(art) as c:
        result = c.dispatch("create_thing", body={"name": "n", "value": 1})
    assert isinstance(result, DispatchSuccess)
    sent = route.calls[0].request
    assert sent.headers["Content-Type"] == "application/json"
    body = sent.read()
    import json

    assert json.loads(body) == {"name": "n", "value": 1}


@respx.mock
def test_dispatch_https_only_at_runtime() -> None:
    """The base_url validation rejects http:// at load time, but defense-in-
    depth at dispatch is also normative. We can't easily build the bad URL
    via the spec layer, but the dispatch layer's _https_only check fires
    when base_url somehow leaks an http:// path — exercise the helper.
    """
    from uacp_prototype.dispatch.client import _https_only

    with pytest.raises(DispatchException, match="HTTPS"):
        _https_only("http://api.example.com/v1/foo")


# ---------------------------------------------------------------------------
# Retry policy (§4.3)
# ---------------------------------------------------------------------------


@respx.mock
def test_idempotent_get_retries_5xx() -> None:
    art = _artifact()
    sleeps: list[float] = []
    route = respx.get("https://api.example.com/v1/things/abc").mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.Response(503, text="busy"),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with _client(art, sleep=sleeps.append) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchSuccess)
    assert route.call_count == 3
    assert len(sleeps) == 2  # two backoff sleeps before the third attempt


@respx.mock
def test_idempotent_get_gives_up_after_max_attempts() -> None:
    art = _artifact()
    route = respx.get("https://api.example.com/v1/things/abc").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with _client(art) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchError)
    assert result.code == "upstream_error"
    assert result.status == 500
    assert route.call_count == DEFAULT_MAX_ATTEMPTS


@respx.mock
def test_non_idempotent_post_does_not_retry_5xx() -> None:
    art = _artifact(
        operations=[
            {
                "id": "create_thing",
                "summary": "Create.",
                "idempotency": "not_idempotent",
                "request": {
                    "method": "POST",
                    "path": "/v1/things",
                    "body": {
                        "media_type": "application/json",
                        "schema": {"type": "object"},
                    },
                },
                "response": {"201": {"description": "ok"}},
            }
        ]
    )
    route = respx.post("https://api.example.com/v1/things").mock(
        return_value=httpx.Response(503, text="busy")
    )
    with _client(art) as c:
        result = c.dispatch("create_thing", body={"name": "n"})
    assert isinstance(result, DispatchError)
    assert route.call_count == 1


@respx.mock
def test_non_idempotent_post_retries_when_declared_idempotent() -> None:
    art = _artifact(
        operations=[
            {
                "id": "create_thing",
                "summary": "Create idempotently.",
                "idempotency": "idempotent",
                "request": {
                    "method": "POST",
                    "path": "/v1/things",
                    "body": {
                        "media_type": "application/json",
                        "schema": {"type": "object"},
                    },
                },
                "response": {"201": {"description": "ok"}},
            }
        ]
    )
    route = respx.post("https://api.example.com/v1/things").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(201, json={"id": "x"}),
        ]
    )
    with _client(art) as c:
        result = c.dispatch("create_thing", body={"name": "n"})
    assert isinstance(result, DispatchSuccess)
    assert route.call_count == 2


@respx.mock
def test_4xx_does_not_retry() -> None:
    art = _artifact()
    route = respx.get("https://api.example.com/v1/things/abc").mock(
        return_value=httpx.Response(404, json={"error": "not_found"})
    )
    with _client(art) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchError)
    assert result.code == "not_found"
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# Rate-limit handling (§4.5)
# ---------------------------------------------------------------------------


@respx.mock
def test_429_with_retry_after_seconds_honored() -> None:
    art = _artifact()
    sleeps: list[float] = []
    route = respx.get("https://api.example.com/v1/things/abc").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}, text="slow down"),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with _client(art, sleep=sleeps.append) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchSuccess)
    assert route.call_count == 2
    # First sleep should be at least 2 seconds from the Retry-After
    assert sleeps[0] >= 1.99


@respx.mock
def test_429_without_retry_after_uses_backoff() -> None:
    art = _artifact()
    sleeps: list[float] = []
    route = respx.get("https://api.example.com/v1/things/abc").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with _client(art, sleep=sleeps.append) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchSuccess)
    assert route.call_count == 2
    assert sleeps  # some delay applied


@respx.mock
def test_429_retry_after_exceeds_cap_surfaces_rate_limited() -> None:
    art = _artifact()
    route = respx.get("https://api.example.com/v1/things/abc").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "9999"})
    )
    with _client(art) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchError)
    assert result.code == "rate_limited"
    assert route.call_count == 1  # bailed before retrying


@respx.mock
def test_429_after_retries_exhausted_returns_rate_limited() -> None:
    art = _artifact()
    route = respx.get("https://api.example.com/v1/things/abc").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"})
    )
    with _client(art) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchError)
    assert result.code == "rate_limited"
    assert route.call_count == 4  # original + 3 retries (DEFAULT_RATE_LIMIT_RETRIES)


# ---------------------------------------------------------------------------
# Error envelope handling (§4.6)
# ---------------------------------------------------------------------------


@respx.mock
def test_envelope_message_extracted() -> None:
    art = _artifact()
    respx.get("https://api.example.com/v1/things/abc").mock(
        return_value=httpx.Response(
            403,
            json={
                "error": "permission_denied",
                "message": "user lacks write scope",
                "extra": {"scope": "write"},
            },
        )
    )
    with _client(art) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchError)
    assert result.code == "forbidden"
    assert result.message == "user lacks write scope"
    assert result.details["error"] == "permission_denied"
    assert result.details["extra"] == {"scope": "write"}


@respx.mock
def test_non_json_response_carries_text_in_raw() -> None:
    art = _artifact()
    respx.get("https://api.example.com/v1/things/abc").mock(
        return_value=httpx.Response(404, text="not json")
    )
    with _client(art) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchError)
    assert result.code == "not_found"
    assert result.raw == "not json"


# ---------------------------------------------------------------------------
# Operation lookup
# ---------------------------------------------------------------------------


def test_unknown_operation_id_raises() -> None:
    art = _artifact()
    with _client(art) as c:
        with pytest.raises(DispatchException, match="not found"):
            c.dispatch("nonexistent")


def test_path_param_missing_raises() -> None:
    art = _artifact()
    with _client(art) as c:
        with pytest.raises(DispatchException, match="path parameter"):
            c.dispatch("get_thing")  # no path_params dict


# ---------------------------------------------------------------------------
# Redirects (§4.2)
# ---------------------------------------------------------------------------


@respx.mock
def test_307_redirect_preserves_method() -> None:
    art = _artifact()
    respx.get("https://api.example.com/v1/things/abc").mock(
        return_value=httpx.Response(
            307, headers={"Location": "https://api.example.com/v2/things/abc"}
        )
    )
    respx.get("https://api.example.com/v2/things/abc").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    with _client(art) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchSuccess)


@respx.mock
def test_redirect_to_http_rejected() -> None:
    art = _artifact()
    respx.get("https://api.example.com/v1/things/abc").mock(
        return_value=httpx.Response(
            302, headers={"Location": "http://api.example.com/v2/things/abc"}
        )
    )
    with _client(art) as c:
        result = c.dispatch("get_thing", path_params={"id": "abc"})
    assert isinstance(result, DispatchError)
    assert "HTTPS" in result.message or "https" in result.message.lower()


@respx.mock
def test_method_changing_redirect_default_refused() -> None:
    art = _artifact(
        operations=[
            {
                "id": "create_thing",
                "summary": "Create.",
                "idempotency": "not_idempotent",
                "request": {
                    "method": "POST",
                    "path": "/v1/things",
                    "body": {
                        "media_type": "application/json",
                        "schema": {"type": "object"},
                    },
                },
                "response": {"201": {"description": "ok"}, "default": {"description": "fallback"}},
            }
        ]
    )
    respx.post("https://api.example.com/v1/things").mock(
        return_value=httpx.Response(303, headers={"Location": "https://api.example.com/v1/things/123"})
    )
    with _client(art) as c:
        result = c.dispatch("create_thing", body={"name": "n"})
    assert isinstance(result, DispatchError)
    assert "method-changing" in result.message
