"""Tests for session_cookie authentication per §2.10."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
import respx

from uacp_prototype.auth.base import AuthApplyResult
from uacp_prototype.auth.session_cookie import (
    Cookie,
    CSRFConfig,
    CSRFState,
    SessionCookieAuthError,
    SessionCookieConfig,
    SessionCookieMethod,
    StorageState,
    extract_csrf_from_payload,
    filter_cookies_for_url,
    format_cookie_header,
    inject_cookies,
    parse_storage_state,
    refresh_csrf,
    tos_violation_warning,
)


# ---------------------------------------------------------------------------
# parse_storage_state
# ---------------------------------------------------------------------------


def test_parse_storage_state_dict() -> None:
    raw = {
        "cookies": [
            {"name": "SID", "value": "abc", "domain": ".google.com", "path": "/"},
            {"name": "HSID", "value": "def", "domain": ".google.com"},
        ],
        "origins": [],
    }
    state = parse_storage_state(raw)
    assert len(state.cookies) == 2
    assert state.cookies[0].name == "SID"
    assert state.cookies[0].domain == ".google.com"


def test_parse_storage_state_json_string() -> None:
    raw = json.dumps(
        {"cookies": [{"name": "k", "value": "v", "domain": "example.com"}]}
    )
    state = parse_storage_state(raw)
    assert state.cookies[0].name == "k"


def test_parse_storage_state_bytes() -> None:
    raw = json.dumps(
        {"cookies": [{"name": "k", "value": "v", "domain": "example.com"}]}
    ).encode("utf-8")
    state = parse_storage_state(raw)
    assert state.cookies[0].value == "v"


def test_parse_storage_state_drops_malformed_cookies() -> None:
    raw = {
        "cookies": [
            {"name": "ok", "value": "v", "domain": "example.com"},
            {"name": "bad-no-domain", "value": "v"},
            "not a dict",
            {"value": "v", "domain": "example.com"},  # no name
        ]
    }
    state = parse_storage_state(raw)
    assert len(state.cookies) == 1
    assert state.cookies[0].name == "ok"


def test_parse_storage_state_invalid_json_raises() -> None:
    with pytest.raises(SessionCookieAuthError, match="not valid JSON"):
        parse_storage_state("{ not json")


def test_parse_storage_state_unsupported_type_raises() -> None:
    with pytest.raises(SessionCookieAuthError, match="JSON"):
        parse_storage_state(12345)  # type: ignore[arg-type]


def test_parse_storage_state_preserves_optional_fields() -> None:
    raw = {
        "cookies": [
            {
                "name": "SID",
                "value": "abc",
                "domain": ".google.com",
                "path": "/api",
                "expires": 1234567890,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ]
    }
    state = parse_storage_state(raw)
    c = state.cookies[0]
    assert c.path == "/api"
    assert c.expires == 1234567890
    assert c.httpOnly is True
    assert c.secure is True
    assert c.sameSite == "Lax"


# ---------------------------------------------------------------------------
# Domain matching (RFC 6265 §5.1.3)
# ---------------------------------------------------------------------------


def test_domain_match_exact() -> None:
    state = StorageState(
        cookies=(Cookie(name="k", value="v", domain="api.example.com"),)
    )
    cookies = filter_cookies_for_url(state, "https://api.example.com/v1/x")
    assert len(cookies) == 1


def test_domain_match_parent_with_dot() -> None:
    """Cookie with .example.com matches api.example.com."""
    state = StorageState(
        cookies=(Cookie(name="k", value="v", domain=".example.com"),)
    )
    cookies = filter_cookies_for_url(state, "https://api.example.com/")
    assert len(cookies) == 1


def test_domain_no_match_different_origin() -> None:
    state = StorageState(
        cookies=(Cookie(name="k", value="v", domain="other.com"),)
    )
    cookies = filter_cookies_for_url(state, "https://api.example.com/")
    assert len(cookies) == 0


def test_domain_host_only_does_not_match_subdomain() -> None:
    """Cookie domain "example.com" (no leading dot) matches example.com
    only, not api.example.com per RFC 6265 §5.1.3 host-only behavior.
    """
    state = StorageState(
        cookies=(Cookie(name="k", value="v", domain="example.com"),)
    )
    cookies = filter_cookies_for_url(state, "https://api.example.com/")
    assert len(cookies) == 0
    # But the host-exact match works.
    cookies2 = filter_cookies_for_url(state, "https://example.com/")
    assert len(cookies2) == 1


# ---------------------------------------------------------------------------
# Secure flag enforcement (RFC 6265 §4.1.2.5)
# ---------------------------------------------------------------------------


def test_secure_cookie_blocked_on_http_url() -> None:
    state = StorageState(
        cookies=(
            Cookie(name="k", value="v", domain="example.com", secure=True),
        )
    )
    cookies = filter_cookies_for_url(state, "http://example.com/")
    assert len(cookies) == 0


def test_secure_cookie_allowed_on_https() -> None:
    state = StorageState(
        cookies=(
            Cookie(name="k", value="v", domain="example.com", secure=True),
        )
    )
    cookies = filter_cookies_for_url(state, "https://example.com/")
    assert len(cookies) == 1


# ---------------------------------------------------------------------------
# Path matching
# ---------------------------------------------------------------------------


def test_path_root_matches_everything() -> None:
    state = StorageState(
        cookies=(Cookie(name="k", value="v", domain="example.com", path="/"),)
    )
    cookies = filter_cookies_for_url(state, "https://example.com/v1/deep/path")
    assert len(cookies) == 1


def test_path_specific_matches_subpath() -> None:
    state = StorageState(
        cookies=(Cookie(name="k", value="v", domain="example.com", path="/api"),)
    )
    cookies = filter_cookies_for_url(state, "https://example.com/api/v1")
    assert len(cookies) == 1


def test_path_specific_does_not_match_unrelated() -> None:
    state = StorageState(
        cookies=(Cookie(name="k", value="v", domain="example.com", path="/api"),)
    )
    cookies = filter_cookies_for_url(state, "https://example.com/other")
    assert len(cookies) == 0


# ---------------------------------------------------------------------------
# Cookie header formatting
# ---------------------------------------------------------------------------


def test_format_cookie_header_simple() -> None:
    cookies = [
        Cookie(name="SID", value="abc", domain="example.com"),
        Cookie(name="HSID", value="def", domain="example.com"),
    ]
    assert format_cookie_header(cookies) == "SID=abc; HSID=def"


def test_format_cookie_header_empty() -> None:
    assert format_cookie_header([]) == ""


# ---------------------------------------------------------------------------
# inject_cookies
# ---------------------------------------------------------------------------


def test_inject_cookies_sends_cookie_header() -> None:
    state = parse_storage_state(
        {
            "cookies": [
                {"name": "SID", "value": "abc", "domain": ".google.com"},
                {"name": "HSID", "value": "def", "domain": ".google.com"},
            ]
        }
    )
    cfg = SessionCookieConfig()
    req = httpx.Request("GET", "https://notebooklm.google.com/v1/notebooks")
    result = inject_cookies(req, storage_state=state, config=cfg)
    assert isinstance(result, AuthApplyResult)
    assert "Cookie" in result.headers
    cookie_str = result.headers["Cookie"]
    assert "SID=abc" in cookie_str
    assert "HSID=def" in cookie_str


def test_inject_cookies_whitelist_filtering() -> None:
    """When config.cookie_names is non-empty, only listed names are sent."""
    state = parse_storage_state(
        {
            "cookies": [
                {"name": "SID", "value": "abc", "domain": ".google.com"},
                {"name": "ANALYTICS", "value": "xyz", "domain": ".google.com"},
            ]
        }
    )
    cfg = SessionCookieConfig(cookie_names=("SID",))
    req = httpx.Request("GET", "https://google.com/")
    result = inject_cookies(req, storage_state=state, config=cfg)
    assert "SID=abc" in result.headers["Cookie"]
    assert "ANALYTICS" not in result.headers["Cookie"]


def test_inject_cookies_no_match_raises() -> None:
    state = parse_storage_state(
        {"cookies": [{"name": "SID", "value": "abc", "domain": ".other.com"}]}
    )
    cfg = SessionCookieConfig()
    req = httpx.Request("GET", "https://google.com/")
    with pytest.raises(SessionCookieAuthError, match="no cookies"):
        inject_cookies(req, storage_state=state, config=cfg)


def test_inject_cookies_with_csrf_from_cookie() -> None:
    state = parse_storage_state(
        {
            "cookies": [
                {"name": "SID", "value": "sess", "domain": ".google.com"},
                {"name": "_csrf_token", "value": "tok-xyz", "domain": ".google.com"},
            ]
        }
    )
    cfg = SessionCookieConfig(
        csrf=CSRFConfig(header_name="X-CSRF-Token", cookie_name="_csrf_token")
    )
    req = httpx.Request("GET", "https://google.com/")
    result = inject_cookies(req, storage_state=state, config=cfg)
    assert result.headers["X-CSRF-Token"] == "tok-xyz"


def test_inject_cookies_with_csrf_state_overrides_cookie() -> None:
    """When the runtime CSRFState carries a fresh token (post-refresh),
    it takes precedence over the cookie-derived value."""
    state = parse_storage_state(
        {
            "cookies": [
                {"name": "SID", "value": "sess", "domain": ".google.com"},
                {"name": "_csrf_token", "value": "stale", "domain": ".google.com"},
            ]
        }
    )
    cfg = SessionCookieConfig(
        csrf=CSRFConfig(header_name="X-CSRF-Token", cookie_name="_csrf_token")
    )
    csrf_state = CSRFState(token="fresh-token-from-refresh")
    req = httpx.Request("GET", "https://google.com/")
    result = inject_cookies(
        req, storage_state=state, config=cfg, csrf_state=csrf_state
    )
    assert result.headers["X-CSRF-Token"] == "fresh-token-from-refresh"


# ---------------------------------------------------------------------------
# CSRF extraction
# ---------------------------------------------------------------------------


def test_extract_csrf_json_jsonpath_subset() -> None:
    body = b'{"csrf": {"token": "extracted-token"}}'
    out = extract_csrf_from_payload(
        body, extraction_path="$.csrf.token", extraction_format="json"
    )
    assert out == "extracted-token"


def test_extract_csrf_json_missing_field() -> None:
    out = extract_csrf_from_payload(
        b'{"other": "x"}', extraction_path="$.csrf", extraction_format="json"
    )
    assert out is None


def test_extract_csrf_regex() -> None:
    body = b'<meta name="csrf" content="abc123"/>'
    out = extract_csrf_from_payload(
        body,
        extraction_path=r'name="csrf" content="([^"]+)"',
        extraction_format="regex",
    )
    assert out == "abc123"


def test_extract_csrf_unknown_format_returns_none() -> None:
    out = extract_csrf_from_payload(
        b"x", extraction_path="$.x", extraction_format="yaml"
    )
    assert out is None


# ---------------------------------------------------------------------------
# refresh_csrf
# ---------------------------------------------------------------------------


@respx.mock
def test_refresh_csrf_fetches_and_extracts() -> None:
    state = parse_storage_state(
        {"cookies": [{"name": "SID", "value": "abc", "domain": ".google.com"}]}
    )
    respx.get("https://notebooklm.google.com/_/RpcCsrf").mock(
        return_value=httpx.Response(200, content=b'{"token": "fresh-csrf"}')
    )
    client = httpx.Client()
    try:
        out = refresh_csrf(
            "https://notebooklm.google.com/_/RpcCsrf",
            storage_state=state,
            extraction_path="$.token",
            extraction_format="json",
            http_client=client,
        )
    finally:
        client.close()
    assert out == "fresh-csrf"


@respx.mock
def test_refresh_csrf_no_matching_cookies_raises() -> None:
    state = parse_storage_state(
        {"cookies": [{"name": "SID", "value": "abc", "domain": ".other.com"}]}
    )
    client = httpx.Client()
    try:
        with pytest.raises(SessionCookieAuthError, match="no cookies"):
            refresh_csrf(
                "https://notebooklm.google.com/_/RpcCsrf",
                storage_state=state,
                extraction_path="$.token",
                extraction_format="json",
                http_client=client,
            )
    finally:
        client.close()


@respx.mock
def test_refresh_csrf_4xx_response_raises() -> None:
    state = parse_storage_state(
        {"cookies": [{"name": "SID", "value": "abc", "domain": ".google.com"}]}
    )
    respx.get("https://notebooklm.google.com/_/RpcCsrf").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    client = httpx.Client()
    try:
        with pytest.raises(SessionCookieAuthError, match="403"):
            refresh_csrf(
                "https://notebooklm.google.com/_/RpcCsrf",
                storage_state=state,
                extraction_path="$.token",
                extraction_format="json",
                http_client=client,
            )
    finally:
        client.close()


@respx.mock
def test_refresh_csrf_extraction_fails_raises() -> None:
    state = parse_storage_state(
        {"cookies": [{"name": "SID", "value": "abc", "domain": ".google.com"}]}
    )
    respx.get("https://notebooklm.google.com/_/RpcCsrf").mock(
        return_value=httpx.Response(200, content=b'{"other": "field"}')
    )
    client = httpx.Client()
    try:
        with pytest.raises(SessionCookieAuthError, match="no token"):
            refresh_csrf(
                "https://notebooklm.google.com/_/RpcCsrf",
                storage_state=state,
                extraction_path="$.token",
                extraction_format="json",
                http_client=client,
            )
    finally:
        client.close()


# ---------------------------------------------------------------------------
# SessionCookieMethod adapter
# ---------------------------------------------------------------------------


def test_method_emits_tos_warning_on_construct(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per §2.10 the method's construction MUST surface the ToS-
    violation warning. The prototype emits via stdlib logging at
    WARNING level so audit pipelines capture it regardless of UI."""
    with caplog.at_level(logging.WARNING, logger="uacp.auth.session_cookie"):
        SessionCookieMethod(config=SessionCookieConfig())
    assert any(
        "ToS" in r.message or "Terms of Service" in r.message for r in caplog.records
    )


def test_method_adapter_apply_works() -> None:
    state = parse_storage_state(
        {"cookies": [{"name": "SID", "value": "abc", "domain": ".google.com"}]}
    )
    method = SessionCookieMethod(config=SessionCookieConfig())
    req = httpx.Request("GET", "https://google.com/v1/x")
    result = method.apply(req, credentials={"storage_state": state})
    assert "Cookie" in result.headers


def test_method_adapter_accepts_raw_dict_storage_state() -> None:
    """make_credential_resolver yields plaintext bytes for secret://
    refs; for session_cookie that's the JSON content of the cookie
    jar. The adapter parses it on-the-fly."""
    method = SessionCookieMethod(config=SessionCookieConfig())
    req = httpx.Request("GET", "https://google.com/v1/x")
    result = method.apply(
        req,
        credentials={
            "storage_state": json.dumps(
                {"cookies": [{"name": "k", "value": "v", "domain": ".google.com"}]}
            )
        },
    )
    assert "k=v" in result.headers["Cookie"]


def test_method_adapter_missing_config_raises() -> None:
    method = SessionCookieMethod()
    req = httpx.Request("GET", "https://google.com/")
    with pytest.raises(SessionCookieAuthError, match="config not set"):
        method.apply(req, credentials={"storage_state": "{}"})


def test_method_adapter_missing_storage_state_raises() -> None:
    method = SessionCookieMethod(config=SessionCookieConfig())
    req = httpx.Request("GET", "https://google.com/")
    with pytest.raises(SessionCookieAuthError, match="storage_state"):
        method.apply(req, credentials={})


def test_tos_violation_warning_text_is_canonical() -> None:
    """The warning text is exposed for other surfaces (CLI, UI) to
    reuse. The exact wording is part of §2.10's contract."""
    text = tos_violation_warning()
    assert "Terms of Service" in text or "ToS" in text
    assert "session_cookie" in text
    assert "§2.10" in text


def test_method_identifier_is_registered() -> None:
    method = SessionCookieMethod(config=SessionCookieConfig())
    assert method.method == "session_cookie"
