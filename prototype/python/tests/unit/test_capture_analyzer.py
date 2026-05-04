"""Tests for the deterministic capture analyzer per §3.12.

The analyzer runs before the LLM synthesis pass; these tests pin the
clustering + parameter-inference behavior against synthetic HAR
fixtures so synthesis quality has a stable foundation.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import pytest

from uacp_prototype.capture import (
    AnalysisResult,
    CandidateOperation,
    CaptureArtifact,
    HarEntry,
    analyze_capture,
)
from uacp_prototype.capture.analyzer import (
    _classify_segment,
    _path_signature,
    _singularize,
    _pearson_overlap,
)


# ---------------------------------------------------------------------------
# Fixture helpers — synthetic captures
# ---------------------------------------------------------------------------


def _entry(
    *,
    method: str = "GET",
    url: str,
    request_headers: dict[str, str] | None = None,
    request_body: str | None = None,
    status: int = 200,
    response_headers: dict[str, str] | None = None,
    response_body: str | None = None,
) -> HarEntry:
    return HarEntry(
        started_at=datetime.now(timezone.utc),
        time_ms=10.0,
        request={
            "method": method,
            "url": url,
            "headers": request_headers or {"User-Agent": "test"},
            "body": request_body,
        },
        response={
            "status": status,
            "status_text": "OK",
            "headers": response_headers or {"Content-Type": "application/json"},
            "body": response_body or '{"ok": true}',
        },
    )


def _artifact(entries: list[HarEntry], *, storage_state: dict | None = None) -> CaptureArtifact:
    return CaptureArtifact(
        capture_id="test",
        captured_at=datetime.now(timezone.utc),
        browser_backend="test",
        initial_url="https://api.example.com/",
        final_url="https://api.example.com/",
        entries=entries,
        storage_state=storage_state,
        metadata={},
    )


# ---------------------------------------------------------------------------
# Variable-shape classifier
# ---------------------------------------------------------------------------


def test_classify_uuid() -> None:
    assert _classify_segment("550e8400-e29b-41d4-a716-446655440000") == "uuid"
    assert _classify_segment("550E8400-E29B-41D4-A716-446655440000") == "uuid"


def test_classify_integer() -> None:
    assert _classify_segment("12345") == "integer"
    assert _classify_segment("0") == "integer"
    assert _classify_segment("-7") == "integer"


def test_classify_email() -> None:
    assert _classify_segment("alice@example.com") == "email"


def test_classify_hex_token() -> None:
    assert _classify_segment("a1b2c3d4e5f6") == "hex"
    # 11 chars: short of the threshold; falls through to slug-with-digits
    # (since 'abc12345def' contains digits) — that's an acceptable
    # call. The classifier prefers the more specific shape.
    assert _classify_segment("abcdefghij") == ""  # all alpha, length 10 < 12 → literal


def test_classify_slug_with_digits() -> None:
    assert _classify_segment("post-12345-title") == "slug"
    assert _classify_segment("year-2025") == "slug"


def test_classify_pure_alpha_is_literal() -> None:
    assert _classify_segment("users") == ""
    assert _classify_segment("v2") == ""  # too short / alpha-dominant
    assert _classify_segment("api") == ""


def test_classify_empty_is_literal() -> None:
    assert _classify_segment("") == ""


# ---------------------------------------------------------------------------
# Path signature
# ---------------------------------------------------------------------------


def test_path_signature_collapses_variables() -> None:
    sig = _path_signature(["users", "12345", "posts", "abcdef-1234"])
    assert sig == ("users", ":var", "posts", ":var")


def test_path_signature_preserves_literals() -> None:
    sig = _path_signature(["api", "v2", "users"])
    assert sig == ("api", "v2", "users")


# ---------------------------------------------------------------------------
# Singularizer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plural,singular",
    [
        ("users", "user"),
        ("categories", "category"),
        ("boxes", "box"),
        ("classes", "class"),  # ses → e (cheap heuristic)
        ("posts", "post"),
        ("api", "api"),  # too short
        ("foo", "foo"),
    ],
)
def test_singularize(plural: str, singular: str) -> None:
    assert _singularize(plural) == singular


# ---------------------------------------------------------------------------
# Pearson / Jaccard overlap
# ---------------------------------------------------------------------------


def test_overlap_full() -> None:
    assert _pearson_overlap({"a", "b"}, {"a", "b"}) == 1.0


def test_overlap_disjoint() -> None:
    assert _pearson_overlap({"a"}, {"b"}) == 0.0


def test_overlap_partial() -> None:
    # 1 shared / 3 union = 0.333...
    score = _pearson_overlap({"a", "b"}, {"a", "c"})
    assert abs(score - 1 / 3) < 0.01


# ---------------------------------------------------------------------------
# Clustering — basic correctness
# ---------------------------------------------------------------------------


def test_same_method_same_path_clusters_together() -> None:
    art = _artifact(
        [
            _entry(url="https://api.example.com/v1/users"),
            _entry(url="https://api.example.com/v1/users"),
            _entry(url="https://api.example.com/v1/users"),
        ]
    )
    result = analyze_capture(art)
    assert len(result.candidate_operations) == 1
    op = result.candidate_operations[0]
    assert op.path_template == "/v1/users"
    assert op.method == "GET"
    assert len(op.invocations) == 3


def test_different_methods_split_into_separate_operations() -> None:
    art = _artifact(
        [
            _entry(method="GET", url="https://api.example.com/v1/users"),
            _entry(method="POST", url="https://api.example.com/v1/users"),
        ]
    )
    result = analyze_capture(art)
    assert len(result.candidate_operations) == 2
    methods = {c.method for c in result.candidate_operations}
    assert methods == {"GET", "POST"}


def test_path_with_integer_id_clusters_with_path_parameter() -> None:
    art = _artifact(
        [
            _entry(url="https://api.example.com/v1/users/123"),
            _entry(url="https://api.example.com/v1/users/456"),
            _entry(url="https://api.example.com/v1/users/789"),
        ]
    )
    result = analyze_capture(art)
    assert len(result.candidate_operations) == 1
    op = result.candidate_operations[0]
    assert "{user_id}" in op.path_template
    # Preceding-segment heuristic gives a high-confidence name from "users".
    var = op.path_parameters[0]
    assert var.value == "user_id"
    assert var.shape == "integer"
    assert var.confidence >= 0.8


def test_path_with_uuid_inferred_as_uuid_shape() -> None:
    art = _artifact(
        [
            _entry(url="https://api.example.com/things/550e8400-e29b-41d4-a716-446655440000"),
            _entry(url="https://api.example.com/things/aaaa1111-2222-3333-4444-555566667777"),
        ]
    )
    result = analyze_capture(art)
    assert len(result.candidate_operations) == 1
    var = result.candidate_operations[0].path_parameters[0]
    assert var.shape == "uuid"
    # Preceding segment is 'things', so the name is 'thing_id'.
    assert var.value == "thing_id"


def test_two_path_parameters_clusterable() -> None:
    art = _artifact(
        [
            _entry(url="https://api.example.com/users/12/posts/34"),
            _entry(url="https://api.example.com/users/56/posts/78"),
        ]
    )
    result = analyze_capture(art)
    assert len(result.candidate_operations) == 1
    op = result.candidate_operations[0]
    assert op.path_template == "/users/{user_id}/posts/{post_id}"


def test_different_literal_segments_split_into_separate_operations() -> None:
    """Same shape but different literal route — cluster split correctly."""
    art = _artifact(
        [
            _entry(url="https://api.example.com/users/12"),
            _entry(url="https://api.example.com/posts/34"),
        ]
    )
    result = analyze_capture(art)
    assert len(result.candidate_operations) == 2
    templates = {c.path_template for c in result.candidate_operations}
    assert templates == {"/users/{user_id}", "/posts/{post_id}"}


def test_query_parameters_become_candidate_parameters() -> None:
    art = _artifact(
        [
            _entry(url="https://api.example.com/v1/search?q=hello&limit=10"),
            _entry(url="https://api.example.com/v1/search?q=world&limit=10"),
            _entry(url="https://api.example.com/v1/search?q=foo"),  # no limit
        ]
    )
    result = analyze_capture(art)
    assert len(result.candidate_operations) == 1
    op = result.candidate_operations[0]
    by_name = {p.name: p for p in op.query_parameters}
    assert by_name["q"].is_required is True
    assert by_name["q"].seen == 3 and by_name["q"].total == 3
    assert by_name["limit"].is_required is False
    assert by_name["limit"].seen == 2 and by_name["limit"].total == 3


# ---------------------------------------------------------------------------
# Body-shape clustering
# ---------------------------------------------------------------------------


def test_consistent_body_shape_clusters_with_body_keys() -> None:
    art = _artifact(
        [
            _entry(
                method="POST",
                url="https://api.example.com/v1/messages",
                request_body='{"channel": "general", "text": "hi"}',
                request_headers={"Content-Type": "application/json"},
            ),
            _entry(
                method="POST",
                url="https://api.example.com/v1/messages",
                request_body='{"channel": "random", "text": "hello"}',
                request_headers={"Content-Type": "application/json"},
            ),
        ]
    )
    result = analyze_capture(art)
    op = result.candidate_operations[0]
    body_keys = {b.name for b in op.body_keys}
    assert body_keys == {"channel", "text"}
    assert op.body_shape_ambiguous is False
    assert all(b.is_required for b in op.body_keys)


def test_divergent_body_shapes_flagged_ambiguous() -> None:
    art = _artifact(
        [
            _entry(
                method="POST",
                url="https://api.example.com/v1/things",
                request_body='{"a": 1, "b": 2}',
            ),
            _entry(
                method="POST",
                url="https://api.example.com/v1/things",
                request_body='{"x": 1, "y": 2, "z": 3}',
            ),
        ]
    )
    result = analyze_capture(art)
    assert result.candidate_operations[0].body_shape_ambiguous is True


def test_form_encoded_body_keys_extracted() -> None:
    art = _artifact(
        [
            _entry(
                method="POST",
                url="https://api.example.com/v1/login",
                request_body="username=alice&password=secret",
                request_headers={"Content-Type": "application/x-www-form-urlencoded"},
            ),
        ]
    )
    result = analyze_capture(art)
    body_keys = {b.name for b in result.candidate_operations[0].body_keys}
    assert body_keys == {"username", "password"}


# ---------------------------------------------------------------------------
# Noise filtering
# ---------------------------------------------------------------------------


def test_third_party_host_filtered_to_noise() -> None:
    art = _artifact(
        [
            _entry(url="https://api.example.com/v1/data"),
            _entry(url="https://api.example.com/v1/data"),
            _entry(url="https://cdn.cloudflare.com/foo.js"),
            _entry(url="https://google-analytics.com/collect?id=123"),
        ]
    )
    result = analyze_capture(art)
    # Primary host = api.example.com (most requests). The two
    # third-party requests land in noise.
    assert result.primary_host == "api.example.com"
    assert len(result.candidate_operations) == 1
    assert len(result.noise_requests) == 2


def test_image_content_type_filtered_to_noise() -> None:
    art = _artifact(
        [
            _entry(url="https://api.example.com/v1/data"),
            _entry(
                url="https://api.example.com/static/logo.png",
                response_headers={"Content-Type": "image/png"},
            ),
            _entry(
                url="https://api.example.com/static/font.woff",
                response_headers={"Content-Type": "font/woff2"},
            ),
        ]
    )
    result = analyze_capture(art)
    assert len(result.candidate_operations) == 1
    assert len(result.noise_requests) == 2


def test_options_preflight_filtered_to_noise() -> None:
    art = _artifact(
        [
            _entry(method="OPTIONS", url="https://api.example.com/v1/data"),
            _entry(method="POST", url="https://api.example.com/v1/data"),
        ]
    )
    result = analyze_capture(art)
    assert len(result.candidate_operations) == 1
    assert result.candidate_operations[0].method == "POST"


def test_favicon_request_filtered_to_noise() -> None:
    art = _artifact(
        [
            _entry(url="https://api.example.com/v1/data"),
            _entry(url="https://api.example.com/favicon.ico"),
        ]
    )
    result = analyze_capture(art)
    assert len(result.noise_requests) == 1
    assert result.noise_requests[0].request["url"].endswith("/favicon.ico")


# ---------------------------------------------------------------------------
# Auth artifacts — extracted but never carrying values
# ---------------------------------------------------------------------------


def test_auth_artifacts_extract_cookie_names_only() -> None:
    art = _artifact(
        [
            _entry(
                url="https://api.example.com/v1/data",
                request_headers={
                    "Cookie": "sid=SECRET",
                    "Authorization": "Bearer NEVER-LOG",
                    "X-Csrf-Token": "csrf123",
                },
            ),
        ],
        storage_state={
            "cookies": [
                {"name": "sid", "value": "SECRET", "domain": "example.com"},
                {"name": "_csrf_token", "value": "another-secret"},
            ]
        },
    )
    result = analyze_capture(art)
    auth = result.auth_artifacts
    assert set(auth["cookie_names"]) == {"sid", "_csrf_token"}
    assert auth["authorization_header_seen"] == 1
    assert auth["csrf_header_seen"] == 1
    # Cookie/Authorization values must NOT appear anywhere in the
    # serialized auth_artifacts.
    serialized = repr(auth)
    assert "SECRET" not in serialized
    assert "NEVER-LOG" not in serialized
    assert "csrf123" not in serialized


# ---------------------------------------------------------------------------
# Auth headers stripped from candidate operations' request_headers
# ---------------------------------------------------------------------------


def test_authorization_header_excluded_from_candidate_summary() -> None:
    art = _artifact(
        [
            _entry(
                url="https://api.example.com/v1/data",
                request_headers={
                    "Authorization": "Bearer SECRET",
                    "Cookie": "sid=alsosecret",
                    "X-Custom": "kept",
                },
            ),
        ],
    )
    result = analyze_capture(art)
    summary = result.candidate_operations[0].to_summary()
    header_names = {h["name"] for h in summary["request_headers"]}
    assert "X-Custom" in header_names
    assert "Authorization" not in header_names
    assert "Cookie" not in header_names


# ---------------------------------------------------------------------------
# Determinism + speed
# ---------------------------------------------------------------------------


def test_clustering_is_deterministic() -> None:
    art = _artifact(
        [
            _entry(method="GET", url="https://api.example.com/users/1"),
            _entry(method="POST", url="https://api.example.com/users"),
            _entry(method="GET", url="https://api.example.com/users/2"),
            _entry(method="DELETE", url="https://api.example.com/users/3"),
        ]
    )
    a = analyze_capture(art)
    b = analyze_capture(art)
    # Compare the to_summary outputs — fully serializable, deterministic.
    assert a.to_summary() == b.to_summary()


def test_analysis_runs_under_100ms_target() -> None:
    """Brief target: <100ms per artifact analysis. Verified with a
    50-entry synthetic capture (the LLM call dominates real
    sessions; analysis itself stays near-instant)."""
    entries: list[HarEntry] = []
    for i in range(50):
        entries.append(_entry(url=f"https://api.example.com/users/{i}"))
        entries.append(
            _entry(
                method="POST",
                url=f"https://api.example.com/users/{i}/posts",
                request_body=f'{{"title": "post {i}", "body": "..."}}',
            )
        )
    art = _artifact(entries)
    t0 = time.perf_counter()
    result = analyze_capture(art)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 100.0, f"analyzer took {elapsed_ms:.1f}ms (target <100ms)"
    # 100 entries → 2 candidate operations (GET /users/{user_id}, POST /users/{user_id}/posts)
    assert len(result.candidate_operations) == 2


# ---------------------------------------------------------------------------
# Summary shape — feeds the LLM prompt
# ---------------------------------------------------------------------------


def test_to_summary_carries_expected_fields() -> None:
    art = _artifact(
        [
            _entry(
                url="https://api.example.com/v1/users/123?include=posts",
            ),
        ]
    )
    result = analyze_capture(art)
    summary = result.to_summary()
    assert summary["primary_host"] == "api.example.com"
    assert summary["domain_summary"] == {"api.example.com": 1}
    assert summary["noise_request_count"] == 0
    assert len(summary["candidate_operations"]) == 1
    cand = summary["candidate_operations"][0]
    assert cand["method"] == "GET"
    assert cand["path_template"] == "/v1/users/{user_id}"
    assert cand["invocation_count"] == 1
    assert any(p["name"] == "user_id" for p in cand["path_parameters"])
    assert any(p["name"] == "include" for p in cand["query_parameters"])


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


def test_empty_capture_produces_empty_result() -> None:
    art = _artifact([])
    result = analyze_capture(art)
    assert result.candidate_operations == []
    assert result.noise_requests == []
    assert result.primary_host == ""


def test_capture_with_only_noise() -> None:
    art = _artifact(
        [
            _entry(url="https://api.example.com/favicon.ico"),
            _entry(
                url="https://api.example.com/static/app.js",
                response_headers={"Content-Type": "application/javascript"},
            ),
        ]
    )
    result = analyze_capture(art)
    assert result.candidate_operations == []
    assert len(result.noise_requests) == 2
