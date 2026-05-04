"""Tests for the §3.12 LLM synthesis pipeline.

Mirrors the testing pattern from test_ingest_nl.py: a deterministic
mock LLM lets the synthesis flow be exercised without network calls.
The mock returns a recorded JSON response shaped like what a real
LLM would produce given a candidate-operations prompt; tests verify
the parsing, validation, dropping-of-hallucinations, refinement
loop, and persistence-requires-approval enforcement.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from uacp_prototype.capture import (
    BrowserRecorder,
    CaptureArtifact,
    HarEntry,
    analyze_capture,
)
from uacp_prototype.capture.recorder import capture_id_for
from uacp_prototype.capture.storage import store_capture
from uacp_prototype.connections.ingest_capture import (
    DEFAULT_MAX_REFINEMENT_ROUNDS,
    CaptureProvenance,
    CaptureSynthesisDraft,
    RefinementLimitExceeded,
    SynthesisNotApprovedError,
    SynthesizedOperation,
    SYSTEM_PROMPT,
    build_user_message,
    confirm_and_persist,
    refine_synthesis,
    synthesize_from_capture,
)


# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------


@dataclass
class MockLLM:
    """Records the (system, user) pairs it sees + returns a configured
    response. Tests assert on the recorded prompt structure."""

    response: str
    model: str = "mock/test-model"
    calls: list[dict[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def __call__(self, *, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        return self.response


# ---------------------------------------------------------------------------
# Fixture artifact + persistence helpers
# ---------------------------------------------------------------------------


def _entry(method: str, url: str, *, body: str | None = None, status: int = 200) -> HarEntry:
    return HarEntry(
        started_at=datetime.now(timezone.utc),
        time_ms=10.0,
        request={
            "method": method,
            "url": url,
            "headers": {"User-Agent": "test"},
            "body": body,
        },
        response={
            "status": status,
            "status_text": "OK",
            "headers": {"Content-Type": "application/json"},
            "body": '{"ok": true}',
        },
    )


def _make_artifact(entries: list[HarEntry]) -> CaptureArtifact:
    when = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    return CaptureArtifact(
        capture_id=capture_id_for("https://api.example.com/", when, provider="test"),
        captured_at=when,
        browser_backend="test",
        initial_url="https://api.example.com/",
        final_url="https://api.example.com/",
        entries=entries,
        storage_state={"cookies": [{"name": "sid", "value": "test-sid"}]},
        metadata={},
    )


def _persist(art: CaptureArtifact, tmp_path: Path) -> str:
    stored = store_capture(art, base_dir=tmp_path / "secrets")
    return stored.ref


# ---------------------------------------------------------------------------
# Build user message — prompt structure
# ---------------------------------------------------------------------------


def test_build_user_message_includes_intent_and_analysis() -> None:
    art = _make_artifact(
        [
            _entry("GET", "https://api.example.com/v1/items"),
            _entry("GET", "https://api.example.com/v1/items"),
        ]
    )
    analysis = analyze_capture(art)
    msg = build_user_message(
        user_intent="I listed all items in the catalog.",
        analysis=analysis,
        captured_at=art.captured_at,
    )
    assert "I listed all items in the catalog." in msg
    assert "candidate_operations" in msg
    assert "/v1/items" in msg
    # The intent + analysis are clearly separated so the LLM doesn't
    # confuse them.
    assert "User intent" in msg
    assert "Deterministic analysis" in msg


def test_build_user_message_strips_intent_whitespace() -> None:
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    analysis = analyze_capture(art)
    msg = build_user_message(
        user_intent="\n\n  hello world  \n\n",
        analysis=analysis,
        captured_at=art.captured_at,
    )
    assert "hello world" in msg


# ---------------------------------------------------------------------------
# synthesize_from_capture — happy path
# ---------------------------------------------------------------------------


def test_synthesize_returns_draft_with_provenance(tmp_path: Path) -> None:
    art = _make_artifact(
        [
            _entry("GET", "https://api.example.com/v1/items?limit=10"),
            _entry("GET", "https://api.example.com/v1/items?limit=20"),
        ]
    )
    ref = _persist(art, tmp_path)
    response = json.dumps(
        {
            "operations": [
                {
                    "id": "list_items",
                    "summary": "Lists items in the catalog with optional limit.",
                    "request": {
                        "method": "GET",
                        "path": "/v1/items",
                        "query_parameters": {
                            "type": "object",
                            "properties": {"limit": {"type": "integer"}},
                        },
                    },
                    "response": {
                        "200": {
                            "description": "List of items.",
                            "body": {"media_type": "application/json", "schema": {"type": "object"}},
                        }
                    },
                    "idempotency": "idempotent",
                }
            ]
        }
    )
    llm = MockLLM(response=response)
    draft = synthesize_from_capture(
        capture_ref=ref,
        user_intent="List all items in the catalog.",
        llm=llm,
        base_dir=tmp_path / "secrets",
    )
    assert len(draft.operations) == 1
    op = draft.operations[0]
    assert op.operation["id"] == "list_items"
    # Provenance — reviewed_at unset until confirmation.
    prov = op.provenance
    assert prov.type == "capture"
    assert prov.user_intent == "List all items in the catalog."
    assert prov.capture_ref == ref
    assert prov.captured_at == art.captured_at.astimezone(timezone.utc).isoformat()
    assert prov.confidence == "medium"
    assert prov.reviewed_at == ""
    # Mock LLM was called exactly once with the configured system prompt.
    assert llm.calls is not None
    assert len(llm.calls) == 1
    assert llm.calls[0]["system"] == SYSTEM_PROMPT


def test_synthesize_rejects_empty_user_intent(tmp_path: Path) -> None:
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    ref = _persist(art, tmp_path)
    with pytest.raises(ValueError, match="non-empty"):
        synthesize_from_capture(
            capture_ref=ref,
            user_intent="   ",
            llm=MockLLM(response='{"operations": []}'),
            base_dir=tmp_path / "secrets",
        )


def test_synthesize_carries_model_identifier(tmp_path: Path) -> None:
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    ref = _persist(art, tmp_path)
    llm = MockLLM(response='{"operations": []}', model="anthropic/claude-haiku-4.5")
    draft = synthesize_from_capture(
        capture_ref=ref,
        user_intent="trivial",
        llm=llm,
        base_dir=tmp_path / "secrets",
    )
    assert draft.model == "anthropic/claude-haiku-4.5"


# ---------------------------------------------------------------------------
# Hallucination dropping (the brief's hard rule)
# ---------------------------------------------------------------------------


def test_synthesize_drops_operations_not_in_candidate_list(tmp_path: Path) -> None:
    """Per the brief: 'an LLM that returns operations beyond the
    candidate list should have those operations dropped at validation.'
    Verified with an LLM that hallucinates a /v1/admin/secrets
    endpoint never in the captures."""
    art = _make_artifact(
        [
            _entry("GET", "https://api.example.com/v1/items"),
        ]
    )
    ref = _persist(art, tmp_path)
    response = json.dumps(
        {
            "operations": [
                {
                    "id": "list_items",
                    "summary": "Lists items.",
                    "request": {"method": "GET", "path": "/v1/items"},
                    "response": {"200": {"description": "ok", "body": "none"}},
                },
                {
                    # Hallucinated — NOT in the captures.
                    "id": "admin_secrets",
                    "summary": "List admin secrets.",
                    "request": {"method": "GET", "path": "/v1/admin/secrets"},
                    "response": {"200": {"description": "ok", "body": "none"}},
                },
                {
                    # Wrong method — NOT in the captures.
                    "id": "delete_items",
                    "summary": "Delete items.",
                    "request": {"method": "DELETE", "path": "/v1/items"},
                    "response": {"204": {"description": "ok", "body": "none"}},
                },
            ]
        }
    )
    draft = synthesize_from_capture(
        capture_ref=ref,
        user_intent="List items.",
        llm=MockLLM(response=response),
        base_dir=tmp_path / "secrets",
    )
    assert len(draft.operations) == 1
    assert draft.operations[0].operation["id"] == "list_items"
    # Both hallucinations land in dropped_operations for visibility.
    assert len(draft.dropped_operations) == 2
    dropped_ids = {d.get("id") for d in draft.dropped_operations}
    assert "admin_secrets" in dropped_ids
    assert "delete_items" in dropped_ids


def test_synthesize_handles_markdown_fenced_response(tmp_path: Path) -> None:
    """The shared _parse_llm_response from ingest_nl strips markdown
    fences. Verify the capture path inherits the same tolerance."""
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    ref = _persist(art, tmp_path)
    response = (
        "```json\n"
        '{"operations": [{"id": "get_x", "summary": "Gets x.", '
        '"request": {"method": "GET", "path": "/v1/x"}, '
        '"response": {"200": {"description": "ok", "body": "none"}}}]}\n'
        "```"
    )
    draft = synthesize_from_capture(
        capture_ref=ref,
        user_intent="Get x.",
        llm=MockLLM(response=response),
        base_dir=tmp_path / "secrets",
    )
    assert len(draft.operations) == 1


def test_synthesize_rejects_invalid_json(tmp_path: Path) -> None:
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    ref = _persist(art, tmp_path)
    with pytest.raises(ValueError, match="not valid JSON"):
        synthesize_from_capture(
            capture_ref=ref,
            user_intent="x",
            llm=MockLLM(response="this is not JSON"),
            base_dir=tmp_path / "secrets",
        )


def test_synthesize_rejects_response_without_operations(tmp_path: Path) -> None:
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    ref = _persist(art, tmp_path)
    with pytest.raises(ValueError, match="operations"):
        synthesize_from_capture(
            capture_ref=ref,
            user_intent="x",
            llm=MockLLM(response='{"foo": []}'),
            base_dir=tmp_path / "secrets",
        )


# ---------------------------------------------------------------------------
# confirm_and_persist — mandatory user review
# ---------------------------------------------------------------------------


def _trivial_draft(ref: str = "secret://local-keyring/test-cap") -> CaptureSynthesisDraft:
    return CaptureSynthesisDraft(
        operations=[
            SynthesizedOperation(
                operation={
                    "id": "list_items",
                    "summary": "Lists items.",
                    "request": {"method": "GET", "path": "/v1/items"},
                    "response": {"200": {"description": "ok", "body": "none"}},
                },
                provenance=CaptureProvenance(
                    captured_at="2026-05-06T12:00:00+00:00",
                    user_intent="trivial",
                    capture_ref=ref,
                    confidence="medium",
                ),
            )
        ],
        raw_llm_response="{}",
        user_intent="trivial",
        capture_ref=ref,
        captured_at="2026-05-06T12:00:00+00:00",
        model="mock/test",
        analysis=analyze_capture(_make_artifact([])),
    )


def test_confirm_and_persist_requires_approval() -> None:
    draft = _trivial_draft()
    with pytest.raises(SynthesisNotApprovedError):
        confirm_and_persist(draft, approved=False)


def test_confirm_and_persist_stamps_reviewed_at() -> None:
    draft = _trivial_draft()
    when = datetime(2026, 5, 6, 14, 30, 0, tzinfo=timezone.utc)
    artifact = confirm_and_persist(draft, approved=True, now=when)
    assert artifact["operations"][0]["source"]["reviewed_at"] == "2026-05-06T14:30:00Z"
    assert artifact["operations"][0]["source"]["type"] == "capture"
    assert artifact["operations"][0]["source"]["capture_ref"] == draft.capture_ref


def test_confirm_and_persist_writes_to_disk(tmp_path: Path) -> None:
    draft = _trivial_draft()
    output = tmp_path / "out.uacp"
    confirm_and_persist(
        draft,
        approved=True,
        output_path=str(output),
        authentication={"method": "session_cookie", "tos_acknowledged": True},
        dispatch={"base_url": "https://api.example.com"},
    )
    on_disk = json.loads(output.read_text())
    assert on_disk["authentication"]["method"] == "session_cookie"
    assert on_disk["dispatch"]["base_url"] == "https://api.example.com"
    assert "reviewed_at" in on_disk["operations"][0]["source"]


def test_confirm_and_persist_uses_v1_1_schema_url_by_default() -> None:
    draft = _trivial_draft()
    artifact = confirm_and_persist(draft, approved=True)
    assert "v1.1.0" in artifact["$schema"]


def test_persisted_artifact_validates_through_spec_loader(tmp_path: Path) -> None:
    """End-to-end: a draft → confirm → write → spec.loader.load
    cycle produces an artifact the prototype's spec loader accepts.
    The §3.12 capture provenance + reviewed_at requirement go through
    the full validation path."""
    from uacp_prototype.spec.loader import load

    draft = _trivial_draft()
    out = tmp_path / "out.uacp"
    confirm_and_persist(
        draft,
        approved=True,
        output_path=str(out),
        authentication={"method": "session_cookie", "tos_acknowledged": True},
        dispatch={"base_url": "https://api.example.com"},
    )
    parsed = load(out)
    assert len(parsed.operations) == 1
    op = parsed.operations[0]
    assert op.id == "list_items"
    # Source provenance round-trips intact.
    assert op.source is not None
    assert getattr(op.source, "type", None) == "capture" or op.source.type == "capture"


# ---------------------------------------------------------------------------
# refine_synthesis — the iteration cap
# ---------------------------------------------------------------------------


def test_refine_synthesis_increments_round_counter(tmp_path: Path) -> None:
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    ref = _persist(art, tmp_path)
    llm_first = MockLLM(
        response='{"operations": [{"id": "get_x", "summary": "x.", '
        '"request": {"method": "GET", "path": "/v1/x"}, '
        '"response": {"200": {"description": "ok", "body": "none"}}}]}'
    )
    draft = synthesize_from_capture(
        capture_ref=ref,
        user_intent="Get x.",
        llm=llm_first,
        base_dir=tmp_path / "secrets",
    )
    assert draft.refinement_round == 0

    llm_refine = MockLLM(
        response='{"operations": [{"id": "get_x", "summary": "Refined x.", '
        '"request": {"method": "GET", "path": "/v1/x"}, '
        '"response": {"200": {"description": "ok", "body": "none"}}}]}'
    )
    refined = refine_synthesis(
        draft, "rename summary to 'Refined x.'", llm=llm_refine
    )
    assert refined.refinement_round == 1
    assert refined.operations[0].operation["summary"] == "Refined x."

    # The refinement prompt includes the prior id and method+path so
    # the LLM preserves them per the §3.12 + §3.8 stability rule.
    assert llm_refine.calls is not None
    refine_user_msg = llm_refine.calls[0]["user"]
    assert "get_x" in refine_user_msg
    assert "GET /v1/x" in refine_user_msg


def test_refine_synthesis_caps_at_three_rounds(tmp_path: Path) -> None:
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    ref = _persist(art, tmp_path)
    response = (
        '{"operations": [{"id": "get_x", "summary": "x.", '
        '"request": {"method": "GET", "path": "/v1/x"}, '
        '"response": {"200": {"description": "ok", "body": "none"}}}]}'
    )
    llm = MockLLM(response=response)
    draft = synthesize_from_capture(
        capture_ref=ref, user_intent="x", llm=llm, base_dir=tmp_path / "secrets"
    )
    # Burn through all three rounds.
    draft = refine_synthesis(draft, "round 1", llm=llm)
    draft = refine_synthesis(draft, "round 2", llm=llm)
    draft = refine_synthesis(draft, "round 3", llm=llm)
    assert draft.refinement_round == 3
    # The fourth refinement attempt raises.
    with pytest.raises(RefinementLimitExceeded):
        refine_synthesis(draft, "one too many", llm=llm)


def test_refine_synthesis_rejects_empty_feedback(tmp_path: Path) -> None:
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    ref = _persist(art, tmp_path)
    response = (
        '{"operations": [{"id": "get_x", "summary": "x.", '
        '"request": {"method": "GET", "path": "/v1/x"}, '
        '"response": {"200": {"description": "ok", "body": "none"}}}]}'
    )
    draft = synthesize_from_capture(
        capture_ref=ref,
        user_intent="x",
        llm=MockLLM(response=response),
        base_dir=tmp_path / "secrets",
    )
    with pytest.raises(ValueError, match="non-empty"):
        refine_synthesis(draft, "", llm=MockLLM(response=response))


def test_default_refinement_max_is_three() -> None:
    assert DEFAULT_MAX_REFINEMENT_ROUNDS == 3


# ---------------------------------------------------------------------------
# Audit emission per §6.6
# ---------------------------------------------------------------------------


def test_synthesis_started_audit_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    ref = _persist(art, tmp_path)
    response = (
        '{"operations": [{"id": "get_x", "summary": "x.", '
        '"request": {"method": "GET", "path": "/v1/x"}, '
        '"response": {"200": {"description": "ok", "body": "none"}}}]}'
    )
    with caplog.at_level(logging.INFO, logger="uacp.connections.ingest_capture"):
        synthesize_from_capture(
            capture_ref=ref,
            user_intent="Test intent for audit verification.",
            llm=MockLLM(response=response),
            base_dir=tmp_path / "secrets",
        )
    msgs = "\n".join(r.message for r in caplog.records)
    assert "synthesis started" in msgs
    assert "candidate_operations=1" in msgs
    assert "synthesis llm-call completed" in msgs
    assert "kept_ops=1" in msgs


def test_spec_loader_rejects_capture_source_without_reviewed_at(tmp_path: Path) -> None:
    """Spec-level enforcement of the §3.12 mandatory-user-review rule:
    a `.uacp` file with source.type=capture and missing reviewed_at
    fails to load. Verifies the rule lives below the CLI in
    spec/schema.py, not just in the user-review prompt — exactly the
    "spec-level enforcement, not just a UX prompt" property the brief
    calls out."""
    from uacp_prototype.spec.loader import load_dict
    from uacp_prototype.spec.schema import SpecValidationError

    artifact = {
        "$schema": "https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.1.0/schemas/uacp.json",
        "authentication": {"method": "session_cookie", "tos_acknowledged": True},
        "dispatch": {"base_url": "https://api.example.com"},
        "operations": [
            {
                "id": "list_items",
                "summary": "Lists items.",
                "request": {"method": "GET", "path": "/v1/items"},
                "response": {"200": {"description": "ok", "body": "none"}},
                "source": {
                    "type": "capture",
                    "captured_at": "2026-05-06T12:00:00Z",
                    "user_intent": "List items.",
                    "capture_ref": "secret://local-keyring/test-cap",
                    "confidence": "medium",
                    # NO reviewed_at — rejected by both pydantic field
                    # validation AND the spec/schema.py capture-
                    # provenance check.
                },
            }
        ],
    }
    with pytest.raises((SpecValidationError, ValueError)):
        load_dict(artifact)


def test_spec_loader_rejects_capture_source_with_non_secret_capture_ref() -> None:
    """The CaptureSource pydantic field-validator rejects capture_ref
    values that aren't secret:// URIs per §2.7."""
    from uacp_prototype.spec.loader import load_dict
    from uacp_prototype.spec.schema import SpecValidationError

    artifact = {
        "$schema": "https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.1.0/schemas/uacp.json",
        "authentication": {"method": "session_cookie", "tos_acknowledged": True},
        "dispatch": {"base_url": "https://api.example.com"},
        "operations": [
            {
                "id": "list_items",
                "summary": "Lists items.",
                "request": {"method": "GET", "path": "/v1/items"},
                "response": {"200": {"description": "ok", "body": "none"}},
                "source": {
                    "type": "capture",
                    "captured_at": "2026-05-06T12:00:00Z",
                    "user_intent": "List items.",
                    "capture_ref": "https://example.com/not-a-secret-uri",
                    "reviewed_at": "2026-05-06T13:00:00Z",
                },
            }
        ],
    }
    with pytest.raises((SpecValidationError, ValueError)):
        load_dict(artifact)


def test_synthesis_audit_does_not_log_user_intent_verbatim(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The audit emit logs intent_len rather than the full
    user-supplied intent. If the operator's intent contained a
    secret, the audit log doesn't propagate it."""
    art = _make_artifact([_entry("GET", "https://api.example.com/v1/x")])
    ref = _persist(art, tmp_path)
    response = (
        '{"operations": [{"id": "get_x", "summary": "x.", '
        '"request": {"method": "GET", "path": "/v1/x"}, '
        '"response": {"200": {"description": "ok", "body": "none"}}}]}'
    )
    sentinel = "ABSOLUTELY-NEVER-LOG-THIS-INTENT-STRING-12345"
    with caplog.at_level(logging.INFO, logger="uacp.connections.ingest_capture"):
        synthesize_from_capture(
            capture_ref=ref,
            user_intent=f"x — {sentinel}",
            llm=MockLLM(response=response),
            base_dir=tmp_path / "secrets",
        )
    full_log = "\n".join(r.message for r in caplog.records)
    assert sentinel not in full_log
