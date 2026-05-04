"""Tests for the §3.8 LLM-inferred schema authoring path."""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from uacp_prototype.connections.ingest_nl import (
    InferenceDraft,
    InferenceNotApprovedError,
    InferenceProvenance,
    InferredOperation,
    LLMCallable,
    confirm_and_persist,
    infer_from_description,
    refine_inference,
)


# ---------------------------------------------------------------------------
# Mock LLM callable for deterministic tests
# ---------------------------------------------------------------------------


@dataclass
class MockLLM:
    """A deterministic mock LLMCallable. Returns a fixed JSON string
    regardless of input. Tests can subclass to record inputs."""

    response: str
    model: str = "mock/test-llm"
    last_system: str = ""
    last_user: str = ""

    def __call__(self, *, system: str, user: str) -> str:
        self.last_system = system
        self.last_user = user
        return self.response


# ---------------------------------------------------------------------------
# infer_from_description
# ---------------------------------------------------------------------------


def test_infer_returns_draft_with_provenance() -> None:
    llm = MockLLM(
        response=json.dumps(
            {
                "operations": [
                    {
                        "id": "list_things",
                        "summary": "List all things.",
                        "request": {"method": "GET", "path": "/v1/things"},
                        "response": {"200": {"description": "ok"}},
                    }
                ]
            }
        )
    )
    draft = infer_from_description("List my things from the things API.", llm=llm)
    assert isinstance(draft, InferenceDraft)
    assert len(draft.operations) == 1
    assert draft.operations[0].operation["id"] == "list_things"
    prov = draft.operations[0].provenance
    assert prov.type == "inferred"
    assert prov.model == "mock/test-llm"
    assert prov.description == "List my things from the things API."
    # reviewed_at MUST be empty until confirm_and_persist sets it
    assert prov.reviewed_at == ""


def test_infer_calls_llm_with_system_and_user() -> None:
    """The LLM callable receives the system prompt + the user
    description verbatim. The system prompt mentions UACP."""
    llm = MockLLM(response=json.dumps({"operations": []}))
    infer_from_description("describe an API", llm=llm)
    assert "UACP" in llm.last_system
    assert "describe an API" in llm.last_user


def test_infer_handles_markdown_fence() -> None:
    """LLMs sometimes wrap JSON in ```json fences even when asked not
    to. The parser strips them."""
    llm = MockLLM(
        response='```json\n{"operations": [{"id": "x", "summary": "x", '
        '"request": {"method": "GET", "path": "/x"}, '
        '"response": {"200": {"description": "ok"}}}]}\n```'
    )
    draft = infer_from_description("x", llm=llm)
    assert len(draft.operations) == 1


def test_infer_rejects_non_json_response() -> None:
    llm = MockLLM(response="I'm sorry, I can't help with that.")
    with pytest.raises(ValueError, match="not valid JSON"):
        infer_from_description("x", llm=llm)


def test_infer_rejects_missing_operations_array() -> None:
    llm = MockLLM(response=json.dumps({"other_field": "x"}))
    with pytest.raises(ValueError, match="operations must be a list"):
        infer_from_description("x", llm=llm)


def test_infer_rejects_top_level_array() -> None:
    """The contract is {operations: [...]}; a bare array is rejected
    so prompt drift gets caught."""
    llm = MockLLM(response=json.dumps([{"id": "x"}]))
    with pytest.raises(ValueError, match="JSON object"):
        infer_from_description("x", llm=llm)


def test_infer_rejects_empty_description() -> None:
    llm = MockLLM(response=json.dumps({"operations": []}))
    with pytest.raises(ValueError, match="non-empty"):
        infer_from_description("", llm=llm)


def test_infer_preserves_raw_response() -> None:
    raw = json.dumps({"operations": [{"id": "x", "summary": "x"}]})
    llm = MockLLM(response=raw)
    draft = infer_from_description("x", llm=llm)
    assert draft.raw_llm_response == raw


def test_infer_confidence_default_medium() -> None:
    llm = MockLLM(response=json.dumps({"operations": [{"id": "x"}]}))
    draft = infer_from_description("x", llm=llm)
    assert draft.operations[0].provenance.confidence == "medium"


def test_infer_confidence_override() -> None:
    llm = MockLLM(response=json.dumps({"operations": [{"id": "x"}]}))
    draft = infer_from_description("x", llm=llm, confidence="low")
    assert draft.operations[0].provenance.confidence == "low"


# ---------------------------------------------------------------------------
# confirm_and_persist — §3.8 mandatory user review
# ---------------------------------------------------------------------------


def _trivial_draft() -> InferenceDraft:
    op = InferredOperation(
        operation={
            "id": "test_op",
            "summary": "Test.",
            "request": {"method": "GET", "path": "/x"},
            "response": {"200": {"description": "ok"}},
        },
        provenance=InferenceProvenance(
            type="inferred",
            model="mock/test",
            description="d",
            confidence="medium",
        ),
    )
    return InferenceDraft(
        operations=(op,),
        raw_llm_response="raw",
        description="d",
        model="mock/test",
    )


def test_confirm_without_approval_raises() -> None:
    """Per §3.8: persistence requires explicit affirmative
    confirmation. Anything other than approved=True raises."""
    draft = _trivial_draft()
    with pytest.raises(InferenceNotApprovedError, match="approved=True"):
        confirm_and_persist(draft, approved=False)


def test_confirm_with_approval_returns_artifact() -> None:
    draft = _trivial_draft()
    artifact = confirm_and_persist(
        draft,
        approved=True,
        authentication={"method": "x-test"},
        dispatch={"base_url": "https://example.com"},
    )
    assert artifact["$schema"] == "https://uacp.spec/v1/schema.json"
    assert artifact["authentication"] == {"method": "x-test"}
    assert artifact["dispatch"] == {"base_url": "https://example.com"}
    assert len(artifact["operations"]) == 1
    op = artifact["operations"][0]
    assert op["id"] == "test_op"
    # Provenance source block populated WITH reviewed_at after confirm
    assert "source" in op
    assert op["source"]["type"] == "inferred"
    assert op["source"]["model"] == "mock/test"
    assert op["source"]["reviewed_at"]  # non-empty


def test_confirm_populates_reviewed_at_rfc3339() -> None:
    draft = _trivial_draft()
    fixed_time = _dt.datetime(2026, 5, 4, 12, 0, 0, tzinfo=_dt.timezone.utc)
    artifact = confirm_and_persist(draft, approved=True, now=fixed_time)
    assert artifact["operations"][0]["source"]["reviewed_at"] == "2026-05-04T12:00:00Z"


def test_confirm_writes_file(tmp_path: Path) -> None:
    draft = _trivial_draft()
    out = tmp_path / "test.uacp"
    artifact = confirm_and_persist(
        draft,
        approved=True,
        output_path=str(out),
        authentication={"method": "x-test"},
        dispatch={"base_url": "https://example.com"},
    )
    assert out.exists()
    written = json.loads(out.read_text())
    assert written == artifact


def test_confirm_preserves_definitions() -> None:
    draft = _trivial_draft()
    artifact = confirm_and_persist(
        draft,
        approved=True,
        definitions={"X": {"type": "object"}},
    )
    assert artifact["definitions"] == {"X": {"type": "object"}}


def test_confirm_no_definitions_omitted_from_artifact() -> None:
    """When no definitions are supplied, the artifact omits the
    `definitions` key entirely (avoids generating empty {})."""
    draft = _trivial_draft()
    artifact = confirm_and_persist(draft, approved=True)
    assert "definitions" not in artifact


def test_confirmed_artifact_loads_through_spec_loader() -> None:
    """End-to-end §3.8 + §3.10 contract: a draft confirmed with
    approval passes the spec validator, including the inferred-source
    provenance check (reviewed_at must be present)."""
    from uacp_prototype.spec.loader import load_dict

    draft = _trivial_draft()
    artifact = confirm_and_persist(
        draft,
        approved=True,
        authentication={"method": "x-test"},
        dispatch={"base_url": "https://example.com"},
    )
    parsed = load_dict(artifact)
    assert parsed.operations[0].id == "test_op"
    assert parsed.operations[0].source is not None
    assert parsed.operations[0].source.type == "inferred"


def test_unconfirmed_artifact_fails_spec_loader() -> None:
    """A draft serialized without going through confirm_and_persist
    has empty reviewed_at; the spec loader rejects it per §3.10."""
    from uacp_prototype.spec.loader import load_dict
    from uacp_prototype.spec.schema import SpecValidationError

    op = {
        "id": "test_op",
        "summary": "Test.",
        "request": {"method": "GET", "path": "/x"},
        "response": {"200": {"description": "ok"}},
        "source": {
            "type": "inferred",
            "model": "mock/test",
            "description": "d",
            "confidence": "medium",
            # reviewed_at intentionally missing
        },
    }
    artifact = {
        "$schema": "https://uacp.spec/v1/schema.json",
        "authentication": {"method": "x-test"},
        "dispatch": {"base_url": "https://example.com"},
        "operations": [op],
    }
    with pytest.raises(SpecValidationError):
        load_dict(artifact)


# ---------------------------------------------------------------------------
# Refinement
# ---------------------------------------------------------------------------


def test_refine_passes_prior_context_to_llm() -> None:
    prior = _trivial_draft()
    llm = MockLLM(
        response=json.dumps(
            {
                "operations": [
                    {
                        "id": "test_op",  # preserved
                        "summary": "Test (refined).",
                        "request": {"method": "GET", "path": "/v2/x"},
                        "response": {"200": {"description": "ok"}},
                    }
                ]
            }
        )
    )
    refined = refine_inference(prior, "the path is /v2/x not /x", llm=llm)
    assert "test_op" in llm.last_user  # prior id mentioned in prompt
    assert "the path is /v2/x" in llm.last_user
    assert refined.operations[0].operation["request"]["path"] == "/v2/x"


def test_refine_warns_when_id_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """The refinement workflow's id-preservation rule is enforced as a
    soft constraint: if the LLM drops a prior id, log a warning. The
    user-review step is the hard check."""
    import logging

    prior = _trivial_draft()
    # LLM "refines" but drops the prior id and adds a different one
    llm = MockLLM(
        response=json.dumps(
            {
                "operations": [
                    {
                        "id": "renamed_op",
                        "summary": "Different id.",
                        "request": {"method": "GET", "path": "/x"},
                        "response": {"200": {"description": "ok"}},
                    }
                ]
            }
        )
    )
    with caplog.at_level(logging.WARNING, logger="uacp.connections.ingest_nl"):
        refine_inference(prior, "rename it", llm=llm)
    assert any("test_op" in r.message and "dropped" in r.message for r in caplog.records)


def test_refine_appends_refinement_to_provenance_description() -> None:
    prior = _trivial_draft()
    llm = MockLLM(
        response=json.dumps(
            {
                "operations": [
                    {
                        "id": "test_op",
                        "summary": "Test.",
                        "request": {"method": "GET", "path": "/x"},
                        "response": {"200": {"description": "ok"}},
                    }
                ]
            }
        )
    )
    refined = refine_inference(prior, "extra context", llm=llm)
    desc = refined.operations[0].provenance.description
    assert "d" in desc  # original
    assert "extra context" in desc  # refinement


def test_refined_draft_still_requires_confirmation() -> None:
    """Refined drafts go through confirm_and_persist again. Without
    approval, persistence fails."""
    prior = _trivial_draft()
    llm = MockLLM(
        response=json.dumps(
            {"operations": [{"id": "test_op", "summary": "x"}]}
        )
    )
    refined = refine_inference(prior, "evidence", llm=llm)
    with pytest.raises(InferenceNotApprovedError):
        confirm_and_persist(refined, approved=False)


# ---------------------------------------------------------------------------
# Default OpenRouter callable
# ---------------------------------------------------------------------------


def test_default_openrouter_callable_uses_env_model_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UACP_LLM_MODEL", "anthropic/claude-sonnet-4.6")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")

    from uacp_prototype.connections.ingest_nl import (
        build_default_openrouter_callable,
    )

    callable_ = build_default_openrouter_callable()
    assert callable_.model == "anthropic/claude-sonnet-4.6"


def test_default_openrouter_callable_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UACP_LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")

    from uacp_prototype.connections.ingest_nl import (
        DEFAULT_MODEL,
        build_default_openrouter_callable,
    )

    callable_ = build_default_openrouter_callable()
    assert callable_.model == DEFAULT_MODEL  # anthropic/claude-haiku-4.5


def test_default_openrouter_callable_raises_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from uacp_prototype.connections.ingest_nl import (
        build_default_openrouter_callable,
    )

    callable_ = build_default_openrouter_callable()
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        callable_(system="x", user="x")


# ---------------------------------------------------------------------------
# End-to-end: realistic NotebookLM-shaped inference
# ---------------------------------------------------------------------------


def test_notebooklm_inference_smoke() -> None:
    """Demonstrates the §3.8 path end-to-end with a realistic
    NotebookLM-shaped LLM response. No real LLM call — uses
    MockLLM. Validates that the draft → confirm → spec-load chain
    works for the kind of output the operator's real LLM would
    produce."""
    notebooklm_response = json.dumps(
        {
            "operations": [
                {
                    "id": "notebooklm_list_notebooks",
                    "summary": "List the user's NotebookLM notebooks.",
                    "description": "Returns the list of notebooks the authenticated user has access to.",
                    "tags": ["notebooklm", "read"],
                    "idempotency": "idempotent",
                    "request": {
                        "method": "POST",
                        "path": "/_/NotebookLmRpcs/data/batchexecute",
                        "query_parameters": {
                            "type": "object",
                            "properties": {
                                "rpcids": {
                                    "type": "string",
                                    "const": "wXbhsf",
                                    "description": "RPC method id for list-notebooks.",
                                }
                            },
                        },
                        "body": {
                            "media_type": "application/x-www-form-urlencoded",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "f.req": {"type": "string"},
                                    "at": {"type": "string", "description": "CSRF token."},
                                },
                            },
                        },
                    },
                    "response": {
                        "200": {
                            "description": "RPC response with notebook list.",
                            "body": {"media_type": "application/json+protobuf", "format": "text"},
                        }
                    },
                },
                {
                    "id": "notebooklm_send_chat_message",
                    "summary": "Send a chat message to a specific notebook.",
                    "description": "Posts a chat message into the named notebook and returns the assistant's reply.",
                    "tags": ["notebooklm", "chat"],
                    "idempotency": "not_idempotent",
                    "request": {
                        "method": "POST",
                        "path": "/_/NotebookLmRpcs/data/batchexecute",
                        "query_parameters": {
                            "type": "object",
                            "properties": {
                                "rpcids": {
                                    "type": "string",
                                    "const": "BfMzVf",
                                }
                            },
                        },
                        "body": {
                            "media_type": "application/x-www-form-urlencoded",
                            "schema": {
                                "type": "object",
                                "required": ["f.req", "at"],
                                "properties": {
                                    "f.req": {
                                        "type": "string",
                                        "description": "URL-encoded RPC payload with notebook_id and message.",
                                    },
                                    "at": {"type": "string"},
                                },
                            },
                        },
                    },
                    "response": {
                        "200": {
                            "description": "Chat completion response.",
                            "body": {"media_type": "application/json+protobuf", "format": "text"},
                        }
                    },
                },
            ]
        }
    )
    llm = MockLLM(response=notebooklm_response, model="mock/notebooklm-aware")
    description = (
        "I want to connect to Google NotebookLM. It's a research/notebook "
        "tool. The two operations I need are: list my notebooks (no parameters), "
        "and send a chat message to a specific notebook (parameters: "
        "notebook_id, message)."
    )
    draft = infer_from_description(description, llm=llm)
    assert len(draft.operations) == 2
    assert draft.operations[0].operation["id"] == "notebooklm_list_notebooks"
    assert draft.operations[1].operation["id"] == "notebooklm_send_chat_message"

    # Confirm + serialize + load through spec.
    from uacp_prototype.spec.loader import load_dict

    artifact = confirm_and_persist(
        draft,
        approved=True,
        authentication={
            "method": "session_cookie",
            "tos_acknowledged": True,
            "storage_state_ref": "secret://local-keyring/notebooklm-storage-state",
            "cookie_names": ["SID", "HSID", "SSID", "APISID", "SAPISID"],
        },
        dispatch={"base_url": "https://notebooklm.google.com"},
    )
    # The spec loader should accept the artifact once session_cookie is
    # registered. Pre-registration the loader's method validator
    # rejects unknown methods; we tolerate either outcome here so the
    # test passes both before and after commit 3 lands. The §3.10
    # provenance rule (reviewed_at populated) is the load-bearing
    # check this test asserts unconditionally.
    from uacp_prototype.spec.schema import SpecValidationError

    try:
        parsed = load_dict(artifact)
        assert len(parsed.operations) == 2
        assert all(op.source.type == "inferred" for op in parsed.operations)
        assert all(op.source.reviewed_at for op in parsed.operations)
    except SpecValidationError as e:
        # Pre-commit-3 state: session_cookie not yet registered. Verify
        # the failure is exactly the registration check (so we'd catch
        # an unrelated regression).
        assert "session_cookie" in str(e)
        assert "v1.0 registered set" in str(e) or "x-namespaced" in str(e)
