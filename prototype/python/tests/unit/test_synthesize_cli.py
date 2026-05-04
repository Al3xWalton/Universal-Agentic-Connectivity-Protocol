"""Tests for the ``uacp synthesize-from-capture`` CLI command per §3.12.

Mocks the LLM via the ingest_capture LLMCallable Protocol; mocks the
$EDITOR subprocess invocation. The CLI tests don't run real LLM calls
and don't launch real editors.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import uacp_prototype.cli as cli_module
from uacp_prototype.capture import (
    BrowserRecorder,
    CaptureArtifact,
    HarEntry,
    analyze_capture,
)
from uacp_prototype.capture.recorder import capture_id_for
from uacp_prototype.capture.storage import store_capture
from uacp_prototype.connections.ingest_capture import (
    CaptureProvenance,
    CaptureSynthesisDraft,
    SynthesizedOperation,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _entry(method: str, url: str, *, body: str | None = None) -> HarEntry:
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
            "status": 200,
            "status_text": "OK",
            "headers": {"Content-Type": "application/json"},
            "body": '{"ok": true}',
        },
    )


def _make_artifact_at_host(host: str = "api.example.com") -> CaptureArtifact:
    when = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    entries = [_entry("GET", f"https://{host}/v1/items")]
    return CaptureArtifact(
        capture_id=capture_id_for(f"https://{host}/", when, provider="test"),
        captured_at=when,
        browser_backend="test",
        initial_url=f"https://{host}/",
        final_url=f"https://{host}/",
        entries=entries,
        storage_state={"cookies": []},
        metadata={},
    )


def _persist(art: CaptureArtifact, tmp_path: Path) -> str:
    return store_capture(art, base_dir=tmp_path / "secrets").ref


def _trivial_draft(*, capture_ref: str, host: str = "api.example.com") -> CaptureSynthesisDraft:
    art = _make_artifact_at_host(host=host)
    analysis = analyze_capture(art)
    return CaptureSynthesisDraft(
        operations=[
            SynthesizedOperation(
                operation={
                    "id": "list_items",
                    "summary": "Lists items in the catalog.",
                    "request": {
                        "method": "GET",
                        "path": "/v1/items",
                    },
                    "response": {"200": {"description": "ok", "body": "none"}},
                    "idempotency": "idempotent",
                },
                provenance=CaptureProvenance(
                    captured_at=art.captured_at.astimezone(timezone.utc).isoformat(),
                    user_intent="List items.",
                    capture_ref=capture_ref,
                    confidence="medium",
                ),
            )
        ],
        raw_llm_response="{}",
        user_intent="List items.",
        capture_ref=capture_ref,
        captured_at=art.captured_at.astimezone(timezone.utc).isoformat(),
        model="mock/test",
        analysis=analysis,
    )


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_main_rejects_existing_output_without_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "exists.uacp"
    out.write_text("{}")
    rc = cli_module.main(
        [
            "synthesize-from-capture",
            "--capture-ref",
            "secret://local-keyring/x",
            "--intent",
            "x",
            "--output",
            str(out),
        ]
    )
    assert rc == 2
    assert "refusing to overwrite" in capsys.readouterr().err


def test_main_rejects_invalid_capture_ref(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli_module.main(
        [
            "synthesize-from-capture",
            "--capture-ref",
            "not-a-secret-uri",
            "--intent",
            "x",
            "--output",
            str(tmp_path / "out.uacp"),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "bad --capture-ref" in err


def test_main_rejects_empty_intent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli_module.main(
        [
            "synthesize-from-capture",
            "--capture-ref",
            "secret://local-keyring/x",
            "--intent",
            "   ",
            "--output",
            str(tmp_path / "out.uacp"),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "non-empty" in err


# ---------------------------------------------------------------------------
# _read_user_decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a\n", "a"),
        ("approve\n", "a"),
        ("e\n", "e"),
        ("edit\n", "e"),
        ("r\n", "r"),
        ("refine\n", "r"),
        ("x\n", "x"),
        ("abort\n", "x"),
    ],
)
def test_read_user_decision_recognized(raw: str, expected: str) -> None:
    decision = cli_module._read_user_decision(
        "?", io.StringIO(raw), io.StringIO()
    )
    assert decision == expected


def test_read_user_decision_loops_on_unrecognized() -> None:
    stdin = io.StringIO("garbage\n??\n\nabort\n")
    stdout = io.StringIO()
    decision = cli_module._read_user_decision("?", stdin, stdout)
    assert decision == "x"
    # The unrecognized-choice message printed at least twice.
    assert stdout.getvalue().count("unrecognized") >= 2


def test_read_user_decision_eof_treats_as_abort() -> None:
    decision = cli_module._read_user_decision(
        "?", io.StringIO(""), io.StringIO()
    )
    assert decision == "x"


# ---------------------------------------------------------------------------
# _render_synthesis_draft
# ---------------------------------------------------------------------------


def test_render_synthesis_draft_includes_operation_summary() -> None:
    draft = _trivial_draft(capture_ref="secret://local-keyring/test-cap")
    out = io.StringIO()
    cli_module._render_synthesis_draft(draft, stdout=out)
    text = out.getvalue()
    assert "list_items" in text
    assert "Lists items in the catalog." in text
    assert "GET /v1/items" in text
    assert "idempotency: idempotent" in text


def test_render_draft_surfaces_dropped_operations() -> None:
    draft = _trivial_draft(capture_ref="secret://local-keyring/test-cap")
    draft.dropped_operations = [
        {
            "id": "admin_secrets",
            "request": {"method": "GET", "path": "/v1/admin/secrets"},
        }
    ]
    out = io.StringIO()
    cli_module._render_synthesis_draft(draft, stdout=out)
    text = out.getvalue()
    assert "Dropped 1 hallucinated operation" in text
    assert "admin_secrets" in text


def test_render_draft_with_no_operations_surfaces_message() -> None:
    draft = CaptureSynthesisDraft(
        operations=[],
        raw_llm_response="{}",
        user_intent="x",
        capture_ref="secret://local-keyring/empty",
        captured_at="2026-05-06T12:00:00+00:00",
        model="mock",
        analysis=analyze_capture(_make_artifact_at_host()),
    )
    out = io.StringIO()
    cli_module._render_synthesis_draft(draft, stdout=out)
    assert "no operations matched" in out.getvalue()


# ---------------------------------------------------------------------------
# _run_review_loop — approve path
# ---------------------------------------------------------------------------


def test_review_loop_approve_writes_file_with_reviewed_at(tmp_path: Path) -> None:
    output = tmp_path / "approved.uacp"
    draft = _trivial_draft(capture_ref="secret://local-keyring/approve-test")
    rc = cli_module._run_review_loop(
        draft=draft,
        llm=lambda system, user: "{}",  # never called on approve
        output_path=output,
        authentication={"method": "session_cookie", "tos_acknowledged": True},
        dispatch={"base_url": "https://api.example.com"},
        stdin=io.StringIO("a\n"),
        stdout=io.StringIO(),
    )
    assert rc == 0
    artifact = json.loads(output.read_text())
    assert len(artifact["operations"]) == 1
    src = artifact["operations"][0]["source"]
    assert src["type"] == "capture"
    assert src["reviewed_at"]  # populated by confirm_and_persist
    assert src["capture_ref"] == "secret://local-keyring/approve-test"


def test_review_loop_approved_artifact_loads_through_spec_loader(
    tmp_path: Path,
) -> None:
    """End-to-end: a draft → CLI approve → spec.loader.load cycle
    produces an artifact the prototype's loader accepts. Verifies
    the §3.12 capture provenance + reviewed_at requirement go
    through full validation."""
    from uacp_prototype.spec.loader import load

    output = tmp_path / "approved-loadable.uacp"
    draft = _trivial_draft(capture_ref="secret://local-keyring/loadable-test")
    cli_module._run_review_loop(
        draft=draft,
        llm=lambda system, user: "{}",
        output_path=output,
        authentication={"method": "session_cookie", "tos_acknowledged": True},
        dispatch={"base_url": "https://api.example.com"},
        stdin=io.StringIO("a\n"),
        stdout=io.StringIO(),
    )
    parsed = load(output)
    assert len(parsed.operations) == 1
    op = parsed.operations[0]
    assert op.id == "list_items"
    assert op.source is not None
    assert op.source.type == "capture"


# ---------------------------------------------------------------------------
# _run_review_loop — abort path
# ---------------------------------------------------------------------------


def test_review_loop_abort_does_not_write_file(tmp_path: Path) -> None:
    output = tmp_path / "should-not-exist.uacp"
    draft = _trivial_draft(capture_ref="secret://local-keyring/abort-test")
    rc = cli_module._run_review_loop(
        draft=draft,
        llm=lambda system, user: "{}",
        output_path=output,
        authentication={},
        dispatch={},
        stdin=io.StringIO("x\n"),
        stdout=io.StringIO(),
    )
    assert rc == 0
    assert not output.exists()


def test_review_loop_eof_aborts_does_not_write_file(tmp_path: Path) -> None:
    output = tmp_path / "should-not-exist.uacp"
    draft = _trivial_draft(capture_ref="secret://local-keyring/eof-test")
    rc = cli_module._run_review_loop(
        draft=draft,
        llm=lambda system, user: "{}",
        output_path=output,
        authentication={},
        dispatch={},
        stdin=io.StringIO(""),  # EOF immediately
        stdout=io.StringIO(),
    )
    assert rc == 0
    assert not output.exists()


# ---------------------------------------------------------------------------
# _run_review_loop — refine path
# ---------------------------------------------------------------------------


def test_review_loop_refine_then_approve(tmp_path: Path) -> None:
    output = tmp_path / "refined.uacp"
    draft = _trivial_draft(capture_ref="secret://local-keyring/refine-test")
    refined_response = json.dumps(
        {
            "operations": [
                {
                    "id": "list_items",
                    "summary": "REFINED: Lists items in the catalog.",
                    "request": {"method": "GET", "path": "/v1/items"},
                    "response": {"200": {"description": "ok", "body": "none"}},
                    "idempotency": "idempotent",
                }
            ]
        }
    )

    def llm(*, system: str, user: str) -> str:
        return refined_response

    llm.model = "mock/test"  # type: ignore[attr-defined]

    rc = cli_module._run_review_loop(
        draft=draft,
        llm=llm,
        output_path=output,
        authentication={},
        dispatch={"base_url": "https://api.example.com"},
        # 'r' triggers refine; the next line is the feedback; then 'a' approves.
        stdin=io.StringIO("r\nthe summary is too generic\na\n"),
        stdout=io.StringIO(),
    )
    assert rc == 0
    artifact = json.loads(output.read_text())
    assert artifact["operations"][0]["summary"] == "REFINED: Lists items in the catalog."


def test_review_loop_refine_with_empty_feedback_loops(tmp_path: Path) -> None:
    """Empty refinement feedback skips the LLM call (per the
    ingest_capture.refine_synthesis empty-feedback rule) and loops
    back to the review prompt."""
    output = tmp_path / "noop.uacp"
    draft = _trivial_draft(capture_ref="secret://local-keyring/empty-feedback")
    rc = cli_module._run_review_loop(
        draft=draft,
        llm=lambda system, user: "{}",
        output_path=output,
        authentication={},
        dispatch={},
        # 'r', empty feedback, then 'x' to abort.
        stdin=io.StringIO("r\n\nx\n"),
        stdout=io.StringIO(),
    )
    assert rc == 0
    assert not output.exists()


def test_review_loop_refine_cap_surfaces_then_user_aborts(tmp_path: Path) -> None:
    output = tmp_path / "cap-test.uacp"
    draft = _trivial_draft(capture_ref="secret://local-keyring/cap-test")
    response = json.dumps(
        {
            "operations": [
                {
                    "id": "list_items",
                    "summary": "x",
                    "request": {"method": "GET", "path": "/v1/items"},
                    "response": {"200": {"description": "ok", "body": "none"}},
                }
            ]
        }
    )

    def llm(*, system: str, user: str) -> str:
        return response

    llm.model = "mock/test"  # type: ignore[attr-defined]

    # Three refines burn the cap; a fourth surfaces the limit message
    # and the user aborts.
    stdout = io.StringIO()
    rc = cli_module._run_review_loop(
        draft=draft,
        llm=llm,
        output_path=output,
        authentication={},
        dispatch={},
        stdin=io.StringIO(
            "r\nfb1\nr\nfb2\nr\nfb3\nr\nfb4\nx\n"
        ),
        stdout=stdout,
    )
    assert rc == 0
    assert not output.exists()
    text = stdout.getvalue()
    assert "refinement cap reached" in text or "Switch to manual editing" in text


# ---------------------------------------------------------------------------
# _open_editor_for_draft
# ---------------------------------------------------------------------------


def test_edit_path_lets_user_modify_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 'e' branch invokes the editor, which we mock with a
    subprocess.call replacement that mutates the temp file in place."""
    output = tmp_path / "edited.uacp"
    draft = _trivial_draft(capture_ref="secret://local-keyring/edit-test")

    def fake_subprocess_call(args, *a, **kw):  # type: ignore[no-untyped-def]
        # Mutate the temp file the editor would have edited.
        path = args[1]
        text = json.loads(Path(path).read_text())
        text["operations"][0]["summary"] = "USER-EDITED summary"
        Path(path).write_text(json.dumps(text, indent=2))
        return 0

    import subprocess

    monkeypatch.setattr(subprocess, "call", fake_subprocess_call)

    rc = cli_module._run_review_loop(
        draft=draft,
        llm=lambda system, user: "{}",
        output_path=output,
        authentication={},
        dispatch={"base_url": "https://api.example.com"},
        stdin=io.StringIO("e\na\n"),  # edit, then approve
        stdout=io.StringIO(),
        editor_command="fake-editor",
    )
    assert rc == 0
    artifact = json.loads(output.read_text())
    assert artifact["operations"][0]["summary"] == "USER-EDITED summary"


# ---------------------------------------------------------------------------
# Spec-level guarantee: persistence-requires-approval is enforced below the CLI
# ---------------------------------------------------------------------------


def test_capture_uacp_without_reviewed_at_fails_to_load(tmp_path: Path) -> None:
    """The brief's hard rule: 'verify a .uacp written without going
    through the approval step fails to load via the spec loader.'
    We construct such a file by hand and assert the loader rejects
    it."""
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
                    "captured_at": "2026-05-06T12:00:00+00:00",
                    "user_intent": "x",
                    "capture_ref": "secret://local-keyring/never-reviewed",
                    # NO reviewed_at — this is the rejection target.
                },
            }
        ],
    }
    with pytest.raises((SpecValidationError, ValueError)):
        load_dict(artifact)


# ---------------------------------------------------------------------------
# Audit emission per §6.6
# ---------------------------------------------------------------------------


def test_user_reviewed_audit_event_emitted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output = tmp_path / "audit.uacp"
    draft = _trivial_draft(capture_ref="secret://local-keyring/audit-test")
    with caplog.at_level(logging.INFO, logger="uacp.cli"):
        cli_module._run_review_loop(
            draft=draft,
            llm=lambda system, user: "{}",
            output_path=output,
            authentication={},
            dispatch={"base_url": "https://api.example.com"},
            stdin=io.StringIO("a\n"),
            stdout=io.StringIO(),
        )
    msgs = "\n".join(r.message for r in caplog.records)
    assert "user-reviewed" in msgs
    assert "decision=a" in msgs
    assert "file-persisted" in msgs
    assert "operations=1" in msgs
