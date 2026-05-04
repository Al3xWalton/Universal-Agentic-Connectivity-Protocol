"""Tests for body-predicate failure detection per §3.3 + §4.6.

The body-predicate machinery permits providers like Slack that wrap
both logical success and logical failure in the same HTTP status
(typically 200 + ``{ok: false, error: "..."}``). The envelope module
evaluates the predicate; the dispatch client converts a matched
predicate into a DispatchError before declaring 2xx success.
"""

from __future__ import annotations

import random
from typing import Any

import httpx
import pytest
import respx

from uacp_prototype.auth.base import AuthApplyResult
from uacp_prototype.dispatch.client import (
    DispatchClient,
    DispatchError,
    DispatchSuccess,
)
from uacp_prototype.dispatch.envelope import (
    evaluate_failure_predicate,
    extract_failure_details,
    resolve_jsonpath,
    select_response_entry,
)
from uacp_prototype.spec.loader import load_dict
from uacp_prototype.spec.models import FailurePredicate, ResponseEntry, UACPArtifact


# ---------------------------------------------------------------------------
# Pure-function unit tests
# ---------------------------------------------------------------------------


def test_resolve_jsonpath_simple() -> None:
    assert resolve_jsonpath({"ok": False}, "$.ok") is False


def test_resolve_jsonpath_nested() -> None:
    body = {"response_metadata": {"next_cursor": "tok-1"}}
    assert resolve_jsonpath(body, "$.response_metadata.next_cursor") == "tok-1"


def test_resolve_jsonpath_missing_returns_none() -> None:
    assert resolve_jsonpath({"ok": True}, "$.absent") is None
    assert resolve_jsonpath({"a": "string"}, "$.a.b") is None


def test_resolve_jsonpath_invalid_prefix_raises() -> None:
    with pytest.raises(ValueError, match="must start with"):
        resolve_jsonpath({}, "ok")


def test_evaluate_predicate_match() -> None:
    pred = FailurePredicate(path="$.ok", equals=False)
    assert evaluate_failure_predicate(pred, {"ok": False, "error": "x"}) is True


def test_evaluate_predicate_no_match() -> None:
    pred = FailurePredicate(path="$.ok", equals=False)
    assert evaluate_failure_predicate(pred, {"ok": True}) is False


def test_evaluate_predicate_missing_field() -> None:
    pred = FailurePredicate(path="$.ok", equals=False)
    # Missing field resolves to None → not equal to False → no match.
    assert evaluate_failure_predicate(pred, {}) is False


def test_evaluate_predicate_with_string_equals() -> None:
    pred = FailurePredicate(path="$.status", equals="error")
    assert evaluate_failure_predicate(pred, {"status": "error"}) is True
    assert evaluate_failure_predicate(pred, {"status": "ok"}) is False


def test_extract_failure_details_full() -> None:
    pred = FailurePredicate(
        path="$.ok",
        equals=False,
        code_path="$.error",
        message_path="$.error_description",
    )
    body = {"ok": False, "error": "channel_not_found", "error_description": "no such channel"}
    code, message = extract_failure_details(pred, body)
    assert code == "channel_not_found"
    assert message == "no such channel"


def test_extract_failure_details_partial() -> None:
    pred = FailurePredicate(path="$.ok", equals=False, code_path="$.error")
    body = {"ok": False, "error": "rate_limited"}
    code, message = extract_failure_details(pred, body)
    assert code == "rate_limited"
    assert message is None


def test_extract_failure_details_missing_code_returns_none() -> None:
    pred = FailurePredicate(path="$.ok", equals=False, code_path="$.error")
    body = {"ok": False}  # error field absent
    code, _msg = extract_failure_details(pred, body)
    assert code is None


def test_select_response_entry_exact_wins() -> None:
    entries = {
        "200": ResponseEntry(description="ok"),
        "2xx": ResponseEntry(description="2xx"),
        "default": ResponseEntry(description="default"),
    }
    assert select_response_entry(entries, 200).description == "ok"


def test_select_response_entry_range_when_no_exact() -> None:
    entries = {
        "2xx": ResponseEntry(description="2xx"),
        "default": ResponseEntry(description="default"),
    }
    assert select_response_entry(entries, 201).description == "2xx"


def test_select_response_entry_default_fallback() -> None:
    entries = {"default": ResponseEntry(description="default")}
    assert select_response_entry(entries, 418).description == "default"


def test_select_response_entry_no_match() -> None:
    entries = {"200": ResponseEntry(description="ok")}
    assert select_response_entry(entries, 404) is None


# ---------------------------------------------------------------------------
# Pydantic-side validation: failure_predicate is a permitted field
# ---------------------------------------------------------------------------


def test_failure_predicate_field_loads_through_spec() -> None:
    raw = {
        "$schema": "https://uacp.spec/v1/schema.json",
        "authentication": {"method": "x-test"},
        "dispatch": {"base_url": "https://api.example.com"},
        "operations": [
            {
                "id": "do_thing",
                "summary": "Do.",
                "request": {"method": "POST", "path": "/v1/do"},
                "response": {
                    "200": {
                        "description": "ok or logical failure",
                        "body": {"media_type": "application/json", "schema": {"type": "object"}},
                        "failure_predicate": {
                            "path": "$.ok",
                            "equals": False,
                            "code_path": "$.error",
                        },
                    }
                },
            }
        ],
    }
    art = load_dict(raw)
    assert isinstance(art, UACPArtifact)
    pred = art.operations[0].response["200"].failure_predicate
    assert pred is not None
    assert pred.path == "$.ok"
    assert pred.equals is False
    assert pred.code_path == "$.error"


def test_failure_predicate_path_must_start_with_dollar() -> None:
    raw = {
        "$schema": "https://uacp.spec/v1/schema.json",
        "authentication": {"method": "x-test"},
        "dispatch": {"base_url": "https://api.example.com"},
        "operations": [
            {
                "id": "do_thing",
                "summary": "Do.",
                "request": {"method": "POST", "path": "/v1/do"},
                "response": {
                    "200": {
                        "description": "ok",
                        "failure_predicate": {"path": "ok", "equals": False},
                    }
                },
            }
        ],
    }
    from uacp_prototype.spec.schema import SpecValidationError

    with pytest.raises(SpecValidationError):
        load_dict(raw)


# ---------------------------------------------------------------------------
# DispatchClient integration: 200 + ok=false → DispatchError
# ---------------------------------------------------------------------------


class StaticAuth:
    method = "x-test"

    def apply(self, request: httpx.Request, *, credentials: dict[str, Any]) -> AuthApplyResult:
        return AuthApplyResult(headers={"Authorization": "Bearer t"})


def _silent_sleep(_secs: float) -> None:
    pass


def _slack_artifact() -> UACPArtifact:
    raw = {
        "$schema": "https://uacp.spec/v1/schema.json",
        "authentication": {"method": "x-oauth2-workspace"},
        "dispatch": {"base_url": "https://slack.com"},
        "operations": [
            {
                "id": "chat_postmessage",
                "summary": "Send a Slack message.",
                "idempotency": "not_idempotent",
                "request": {
                    "method": "POST",
                    "path": "/api/chat.postMessage",
                    "body": {
                        "media_type": "application/json",
                        "schema": {
                            "type": "object",
                            "required": ["channel", "text"],
                            "properties": {
                                "channel": {"type": "string"},
                                "text": {"type": "string"},
                            },
                        },
                    },
                },
                "response": {
                    "200": {
                        "description": "Slack envelope. ok=true success or ok=false logical failure.",
                        "body": {
                            "media_type": "application/json",
                            "schema": {"type": "object"},
                        },
                        "failure_predicate": {
                            "path": "$.ok",
                            "equals": False,
                            "code_path": "$.error",
                        },
                    }
                },
            }
        ],
    }
    return load_dict(raw)


def _client(art: UACPArtifact) -> DispatchClient:
    return DispatchClient(
        art,
        auth_method=StaticAuth(),
        credential_resolver=lambda: {"access_token": "xoxb-1"},
        sleep=_silent_sleep,
        rng=random.Random(0),
    )


@respx.mock
def test_dispatch_200_ok_true_returns_success() -> None:
    art = _slack_artifact()
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1.2", "channel": "C1"})
    )
    with _client(art) as c:
        result = c.dispatch(
            "chat_postmessage", body={"channel": "C1", "text": "hi"}
        )
    assert isinstance(result, DispatchSuccess)
    assert result.body["ok"] is True


@respx.mock
def test_dispatch_200_ok_false_returns_dispatch_error() -> None:
    art = _slack_artifact()
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    with _client(art) as c:
        result = c.dispatch(
            "chat_postmessage", body={"channel": "Cx", "text": "hi"}
        )
    assert isinstance(result, DispatchError)
    assert result.status == 200  # status preserved per §4.6 canonical shape
    assert result.code == "not_found"  # mapped from channel_not_found
    assert "channel_not_found" in (result.message or "")
    assert result.details["error"] == "channel_not_found"
    assert result.raw == {"ok": False, "error": "channel_not_found"}


@respx.mock
def test_dispatch_200_ok_false_auth_error_maps_to_auth_expired() -> None:
    art = _slack_artifact()
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "not_authed"})
    )
    with _client(art) as c:
        result = c.dispatch("chat_postmessage", body={"channel": "C", "text": "hi"})
    assert isinstance(result, DispatchError)
    assert result.code == "auth_expired"


@respx.mock
def test_dispatch_200_ok_false_rate_limited_maps_to_rate_limited() -> None:
    art = _slack_artifact()
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "rate_limited"})
    )
    with _client(art) as c:
        result = c.dispatch("chat_postmessage", body={"channel": "C", "text": "hi"})
    assert isinstance(result, DispatchError)
    assert result.code == "rate_limited"


@respx.mock
def test_dispatch_200_ok_false_unknown_error_maps_to_upstream_error() -> None:
    art = _slack_artifact()
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "weird_provider_specific"})
    )
    with _client(art) as c:
        result = c.dispatch("chat_postmessage", body={"channel": "C", "text": "hi"})
    assert isinstance(result, DispatchError)
    assert result.code == "upstream_error"
    assert "weird_provider_specific" in result.message


@respx.mock
def test_dispatch_predicate_absent_falls_through_to_success() -> None:
    """An operation without failure_predicate behaves identically to
    Stage 8a: 2xx → DispatchSuccess regardless of body content."""
    raw = {
        "$schema": "https://uacp.spec/v1/schema.json",
        "authentication": {"method": "x-test"},
        "dispatch": {"base_url": "https://api.example.com"},
        "operations": [
            {
                "id": "no_predicate",
                "summary": "No predicate.",
                "idempotency": "idempotent",
                "request": {"method": "GET", "path": "/v1/x"},
                "response": {"200": {"description": "ok"}},
            }
        ],
    }
    art = load_dict(raw)
    respx.get("https://api.example.com/v1/x").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "wat"})
    )
    with _client(art) as c:
        result = c.dispatch("no_predicate")
    # No predicate declared → 2xx → success path. The body's ok=false is
    # data, not a failure signal, when the artifact doesn't say so.
    assert isinstance(result, DispatchSuccess)


@respx.mock
def test_dispatch_predicate_with_non_json_body_falls_through_to_success() -> None:
    """If the predicate is declared but the body isn't parseable JSON,
    the runtime falls back to the 2xx-success path. The artifact's
    response schema declared application/json, so a non-JSON body is
    surprising but not a failure signal."""
    art = _slack_artifact()
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, content=b"<html>oops</html>")
    )
    with _client(art) as c:
        result = c.dispatch("chat_postmessage", body={"channel": "C", "text": "hi"})
    assert isinstance(result, DispatchSuccess)


@respx.mock
def test_dispatch_predicate_match_preserves_status_in_error() -> None:
    """The DispatchError surfaces the original HTTP status (200) rather
    than mapping to a synthetic 4xx — §4.6 keeps `status` faithful for
    audit purposes; `code` carries the canonical normalization."""
    art = _slack_artifact()
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    with _client(art) as c:
        result = c.dispatch("chat_postmessage", body={"channel": "Cx", "text": "hi"})
    assert isinstance(result, DispatchError)
    assert result.status == 200
