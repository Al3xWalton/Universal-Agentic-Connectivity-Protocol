"""LLM synthesis from session captures per §3.12.

The consumption side of Stage 11.1's recording infrastructure. Takes
a `secret://` reference to an encrypted-at-rest capture artifact, the
user's stated intent, and a pluggable LLM callable; runs the §3.12
clustering + LLM synthesis pipeline; returns a draft `.uacp`
operations block ready for the user-review surface.

Architectural symmetry with :mod:`uacp_prototype.connections.ingest_nl`
is intentional — both modules implement the §3.8-style mandatory-user-
review pattern, both expose ``synthesize_from_capture`` /
``confirm_and_persist`` / ``refine_synthesis``, both delegate the LLM
call to a Protocol-typed callable, both refuse to persist without an
explicit approval flag. The differences are §3.12-specific:

  - Input is a captured session (loaded + decrypted via
    :func:`capture.storage.load_capture`), not a free-text description.
  - The deterministic analyzer (:func:`capture.analyzer.analyze_capture`)
    runs first; the LLM operates on its structured output.
  - Provenance is ``source.type: "capture"`` carrying ``captured_at``,
    ``user_intent``, ``capture_ref``, ``reviewed_at`` per the §3.12
    field set.
  - Hallucinated operations not in the candidate list are dropped
    at validation time — the spec's §3.12 prose ("Do not hallucinate
    operations not present in the captures") is enforced
    mechanically here, not just suggested in the prompt.

The LLM is pluggable per the ``LLMCallable`` Protocol shared with
:mod:`uacp_prototype.connections.ingest_nl`. The default callable
(``build_default_openrouter_callable``) wraps OpenRouter; operators
can supply their own. Tests use a deterministic mock LLM.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..capture.analyzer import (
    AnalysisResult,
    CandidateOperation,
    analyze_capture,
)
from ..capture.recorder import CaptureArtifact
from ..capture.storage import load_capture
from .ingest_nl import LLMCallable, _parse_llm_response


log = logging.getLogger("uacp.connections.ingest_capture")


DEFAULT_MAX_REFINEMENT_ROUNDS = 3


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """You are an API schema generator producing UACP v1.x \
operation entries from observed browser-captured HTTP traffic.

UACP is the Universal Agentic Connectivity Protocol. A `.uacp` artifact \
declares one or more `Operation` entries; each operation has:
  - id: snake_case unique identifier (matches [a-z][a-z0-9_-]{0,127})
  - summary: one-sentence user-intent description (NOT HTTP-shape vocabulary)
  - description: longer prose
  - request: { method, path, path_parameters?, query_parameters?, body? }
      method ∈ {GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS}
      path: RFC 6570 URI Template; path parameters in {braces}
      path_parameters / query_parameters: JSON Schema 2020-12 objects
      body: { media_type, schema } OR "none" OR { $ref: "#/definitions/..." }
  - response: { "<status>": { description, body, ... } }
  - idempotency: idempotent / not_idempotent / unknown (optional)
  - tags: optional list of snake_case tags

You receive a deterministic clustering of HTTP traffic that the \
prototype's analyzer already produced. Your job: name + summarize + \
classify the candidate operations into UACP entries. The clustering, \
path templates, and parameter frequencies are pre-computed; you should \
NOT recluster, NOT invent path parameters that aren't in the analysis, \
and NOT hallucinate operations beyond the candidate list.

For each candidate operation:
  - Pick a stable, descriptive snake_case `id` reflecting what the \
    operation does (e.g. `list_messages`, `send_chat_message`, \
    `get_repo_metadata`). Operation ids appear in agent code; pick \
    names that read well.
  - Write a one-sentence `summary` describing what the operation does \
    from the AGENT's perspective, not the API's perspective. \
    "Sends a message to a Slack channel" is good; \
    "POSTs JSON to /chat.postMessage" is bad.
  - Render request.path verbatim from the analyzer's `path_template`. \
    Use the analyzer's path-parameter names unless they're clearly \
    wrong (low confidence + obviously misnamed); if you rename, the \
    refinement context will note your change.
  - Construct path_parameters / query_parameters / body schemas using \
    the analyzer's frequency tables: parameters with `is_required: true` \
    go in the schema's `required` array; parameters with \
    `is_required: false` are optional. Permissive types are preferred \
    over strict types when the captures don't give clear evidence \
    (string > anything-specific when in doubt).
  - For body schemas, use the analyzer's `body_keys` table. When \
    `body_shape_ambiguous: true`, prefer permissive shapes (the \
    analyzer flagged this cluster as potentially-two-operations).
  - Pick `idempotency`: GET/HEAD/OPTIONS/PUT/DELETE → `idempotent` by \
    default; POST/PATCH → `not_idempotent` unless the operation is \
    obviously upsert-shaped.

Return a JSON object with a single field `operations` that is an array \
of operation entries. Do NOT include authentication or dispatch blocks; \
the caller adds those separately.

Constraints (HARD):
  - Do not invent operations not in the candidate list.
  - Do not include cookie / Authorization / API-key / token values in \
    any field — the analyzer already stripped these from the input \
    you see; do not re-introduce them.
  - Operation summaries describe what the agent does, not how the \
    API works internally.
  - Output VALID JSON only. No prose, no markdown, no commentary.
"""


def build_user_message(
    *,
    user_intent: str,
    analysis: AnalysisResult,
    captured_at: _dt.datetime,
) -> str:
    """Assemble the structured user message from the AnalysisResult.

    The candidate operations are formatted as a JSON-shaped block so
    the LLM sees clean structure; the user's natural-language intent
    is at the top, the analyzer summary follows. Tests verify the
    shape of this string.
    """
    summary = analysis.to_summary()
    payload = {
        "user_intent": user_intent,
        "captured_at": captured_at.astimezone(_dt.timezone.utc).isoformat(),
        "primary_host": summary.get("primary_host", ""),
        "domain_summary": summary.get("domain_summary", {}),
        "auth_artifacts": summary.get("auth_artifacts", {}),
        "candidate_operations": summary.get("candidate_operations", []),
        "noise_request_count": summary.get("noise_request_count", 0),
    }
    return (
        "User intent (natural-language description of what the user "
        "demonstrated):\n\n"
        f"{user_intent.strip()}\n\n"
        "Deterministic analysis of the captured session (already "
        "clustered, parameters with frequencies, noise filtered). "
        "Generate UACP operation entries strictly from this list:\n\n"
        f"{json.dumps(payload, indent=2)}"
    )


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptureProvenance:
    """The §3.12 source block, populated at draft time + completed at
    confirmation time. ``reviewed_at`` is empty until
    :func:`confirm_and_persist` populates it.
    """

    type: str = "capture"
    captured_at: str = ""
    user_intent: str = ""
    capture_ref: str = ""
    confidence: str = "medium"  # low / medium / high
    reviewed_at: str = ""  # populated at confirm time

    def to_dict(self) -> dict[str, str]:
        out = {
            "type": self.type,
            "captured_at": self.captured_at,
            "user_intent": self.user_intent,
            "capture_ref": self.capture_ref,
            "confidence": self.confidence,
        }
        if self.reviewed_at:
            out["reviewed_at"] = self.reviewed_at
        return out


@dataclass(frozen=True)
class SynthesizedOperation:
    """A single operation entry produced by the LLM, carrying §3.12
    provenance. Kept as a dict (not a typed Operation) because the
    LLM may produce drafts with varying shape that need user review
    before pydantic validation.
    """

    operation: dict[str, Any]
    provenance: CaptureProvenance


@dataclass
class CaptureSynthesisDraft:
    """A draft `.uacp` operations block awaiting user review.

    Mirrors :class:`InferenceDraft` from :mod:`ingest_nl` but for the
    §3.12 capture path. ``raw_llm_response`` is preserved verbatim so
    the user can see what the LLM said in case the structured parse
    missed something. ``dropped_operations`` records LLM-output
    operations that didn't match a candidate (hallucinations) — the
    user-review surface surfaces these so the operator can see what
    the LLM tried to invent.
    """

    operations: list[SynthesizedOperation]
    raw_llm_response: str
    user_intent: str
    capture_ref: str
    captured_at: str
    model: str
    analysis: AnalysisResult
    dropped_operations: list[dict[str, Any]] = field(default_factory=list)
    refinement_round: int = 0

    def operations_with_source(self) -> list[dict[str, Any]]:
        """Return the operations dicts with the ``source`` field
        populated from each operation's provenance — for serialization
        into a `.uacp` file's operations array. ``reviewed_at`` is
        included only when the provenance has been confirmed."""
        out = []
        for synth in self.operations:
            op = dict(synth.operation)
            op["source"] = synth.provenance.to_dict()
            out.append(op)
        return out


class SynthesisNotApprovedError(Exception):
    """Raised when ``confirm_and_persist`` is called without
    ``approved=True``. Per §3.12 mandatory user review (parallel to
    §3.8), persistence requires explicit affirmative confirmation."""


class RefinementLimitExceeded(Exception):
    """Raised when ``refine_synthesis`` is called past the configured
    iteration cap (default 3). The operator is asked to manually
    edit at that point — the brief's "prevents runaway LLM-back-and-
    forth" affordance."""


# ---------------------------------------------------------------------------
# Synthesis flow
# ---------------------------------------------------------------------------


def _candidate_signature(candidate: CandidateOperation) -> tuple[str, str]:
    """The (method, path_template) tuple identifies a candidate
    uniquely within an AnalysisResult."""
    return (candidate.method.upper(), candidate.path_template)


def _operation_signature(op: dict[str, Any]) -> tuple[str, str]:
    """The (method, path) signature of an LLM-produced operation
    matches a candidate iff both fields agree exactly. Used to drop
    hallucinated operations that don't correspond to any candidate."""
    method = (op.get("request", {}) or {}).get("method", "").upper()
    path = (op.get("request", {}) or {}).get("path", "")
    return (method, path)


def _filter_to_candidates(
    operations: list[dict[str, Any]],
    analysis: AnalysisResult,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split LLM-produced operations into (kept, dropped) where kept
    operations match a candidate from the analyzer. Implements the
    brief's hard rule: "an LLM that returns operations beyond the
    candidate list should have those operations dropped at
    validation."
    """
    candidate_signatures = {
        _candidate_signature(c) for c in analysis.candidate_operations
    }
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for op in operations:
        if _operation_signature(op) in candidate_signatures:
            kept.append(op)
        else:
            dropped.append(op)
    return kept, dropped


def _emit_audit_synthesis_started(
    *, capture_ref: str, user_intent: str, candidate_count: int
) -> None:
    """Per §6.6: synthesis-started event. Payload carries the capture
    reference + user-intent + candidate count — never raw cookies /
    response bodies. The user_intent is operator-supplied prose; if
    they include credentials in their intent, that's their choice
    surfaced through the audit log; the prototype doesn't redact
    operator-supplied text."""
    log.info(
        "synthesis started: capture_ref=%s candidate_operations=%d intent_len=%d",
        capture_ref,
        candidate_count,
        len(user_intent),
    )


def _emit_audit_llm_call_completed(
    *, capture_ref: str, kept: int, dropped: int, raw_chars: int
) -> None:
    log.info(
        "synthesis llm-call completed: capture_ref=%s kept_ops=%d dropped_ops=%d raw_chars=%d",
        capture_ref,
        kept,
        dropped,
        raw_chars,
    )


def synthesize_from_capture(
    capture_ref: str,
    user_intent: str,
    *,
    llm: LLMCallable,
    confidence: str = "medium",
    base_dir: Any = None,
    artifact: CaptureArtifact | None = None,
    analysis: AnalysisResult | None = None,
) -> CaptureSynthesisDraft:
    """Run the §3.12 LLM synthesis pipeline.

    Per §3.12 mandatory user review: this function returns a draft;
    it does NOT persist. The caller MUST invoke
    :func:`confirm_and_persist` with an explicit approval flag to
    write a `.uacp` file. Calling persist without approval raises
    :class:`SynthesisNotApprovedError`.

    Parameters:
      - ``capture_ref``: the ``secret://`` URI returned by Stage 11.1's
        capture-session CLI.
      - ``user_intent``: a natural-language description of what the
        user demonstrated (e.g. "I logged into Slack and sent a
        message in #general"). Required and non-empty.
      - ``llm``: any :class:`LLMCallable` (OpenRouter wrapper, mock
        for tests, etc.).
      - ``confidence``: low / medium / high — propagated into each
        synthesized operation's provenance.
      - ``base_dir``: optional override for ``load_capture``'s
        keyring base directory (tests inject ``tmp_path``).
      - ``artifact`` / ``analysis``: optional pre-loaded values that
        skip the load + analyze steps. Used by ``refine_synthesis``
        and tests; production callers leave them unset and let the
        function load + analyze via ``load_capture(capture_ref)``.

    Returns :class:`CaptureSynthesisDraft` carrying the kept
    operations (each with §3.12 provenance and ``reviewed_at`` unset),
    the LLM's raw response verbatim, and any dropped operations the
    LLM hallucinated.
    """
    if not user_intent.strip():
        raise ValueError("user_intent must be non-empty")

    if artifact is None:
        artifact = load_capture(capture_ref, base_dir=base_dir)
    if analysis is None:
        analysis = analyze_capture(artifact)

    _emit_audit_synthesis_started(
        capture_ref=capture_ref,
        user_intent=user_intent,
        candidate_count=len(analysis.candidate_operations),
    )

    user_message = build_user_message(
        user_intent=user_intent,
        analysis=analysis,
        captured_at=artifact.captured_at,
    )

    raw = llm(system=SYSTEM_PROMPT, user=user_message)
    operations_dicts, raw_text = _parse_llm_response(raw)

    kept, dropped = _filter_to_candidates(operations_dicts, analysis)

    _emit_audit_llm_call_completed(
        capture_ref=capture_ref,
        kept=len(kept),
        dropped=len(dropped),
        raw_chars=len(raw_text),
    )

    captured_at_iso = artifact.captured_at.astimezone(
        _dt.timezone.utc
    ).isoformat()

    synthesized: list[SynthesizedOperation] = [
        SynthesizedOperation(
            operation=op,
            provenance=CaptureProvenance(
                type="capture",
                captured_at=captured_at_iso,
                user_intent=user_intent,
                capture_ref=capture_ref,
                confidence=confidence,
                reviewed_at="",
            ),
        )
        for op in kept
    ]

    return CaptureSynthesisDraft(
        operations=synthesized,
        raw_llm_response=raw_text,
        user_intent=user_intent,
        capture_ref=capture_ref,
        captured_at=captured_at_iso,
        model=getattr(llm, "model", ""),
        analysis=analysis,
        dropped_operations=dropped,
        refinement_round=0,
    )


# ---------------------------------------------------------------------------
# Refinement
# ---------------------------------------------------------------------------


REFINEMENT_PROMPT_SUFFIX = """

REFINEMENT CONTEXT — the user reviewed the prior draft and asked for \
changes. Use the additional evidence below to refine your output. \
HARD: preserve every operation's `id` field exactly as it appeared in \
the prior draft (per §3.12 + §3.8 refinement preserves id and source \
attribution; agents reference operations by id, breaking those \
references would break the agents). Continue to operate strictly on \
the candidate-operations list — do not invent new operations.

Prior draft operations (by id + method + path):
{prior_operations_summary}

User feedback:
{refinement_text}
"""


def refine_synthesis(
    draft: CaptureSynthesisDraft,
    refinement_text: str,
    *,
    llm: LLMCallable,
    max_rounds: int = DEFAULT_MAX_REFINEMENT_ROUNDS,
) -> CaptureSynthesisDraft:
    """Refine an existing synthesis draft per §3.12 + §3.8.

    Brief affordance: hard cap of 3 refinement rounds. After that,
    the caller surfaces the manual-edit affordance to the user
    (see the :func:`uacp_prototype.cli` synthesize-from-capture
    command's flow). The cap prevents runaway LLM-back-and-forth.

    Returns a new draft with ``refinement_round`` incremented. The
    refined draft must still go through :func:`confirm_and_persist`
    before storage.
    """
    if not refinement_text.strip():
        raise ValueError("refinement_text must be non-empty")
    if draft.refinement_round + 1 > max_rounds:
        raise RefinementLimitExceeded(
            f"refinement cap reached: {draft.refinement_round}/{max_rounds} rounds. "
            f"Switch to manual editing of the draft .uacp file."
        )

    prior_summary_lines = []
    for synth in draft.operations:
        op = synth.operation
        op_id = op.get("id", "<no-id>")
        method = (op.get("request", {}) or {}).get("method", "")
        path = (op.get("request", {}) or {}).get("path", "")
        prior_summary_lines.append(f"- {op_id}: {method} {path}")
    prior_summary = (
        "\n".join(prior_summary_lines) if prior_summary_lines else "(no prior operations)"
    )

    refinement_user_message = build_user_message(
        user_intent=draft.user_intent,
        analysis=draft.analysis,
        captured_at=_dt.datetime.fromisoformat(draft.captured_at),
    ) + REFINEMENT_PROMPT_SUFFIX.format(
        prior_operations_summary=prior_summary,
        refinement_text=refinement_text.strip(),
    )

    raw = llm(system=SYSTEM_PROMPT, user=refinement_user_message)
    operations_dicts, raw_text = _parse_llm_response(raw)
    kept, dropped = _filter_to_candidates(operations_dicts, draft.analysis)

    _emit_audit_llm_call_completed(
        capture_ref=draft.capture_ref,
        kept=len(kept),
        dropped=len(dropped),
        raw_chars=len(raw_text),
    )

    synthesized: list[SynthesizedOperation] = [
        SynthesizedOperation(
            operation=op,
            provenance=CaptureProvenance(
                type="capture",
                captured_at=draft.captured_at,
                user_intent=draft.user_intent,
                capture_ref=draft.capture_ref,
                confidence=draft.operations[0].provenance.confidence
                if draft.operations
                else "medium",
                reviewed_at="",
            ),
        )
        for op in kept
    ]

    return CaptureSynthesisDraft(
        operations=synthesized,
        raw_llm_response=raw_text,
        user_intent=draft.user_intent,
        capture_ref=draft.capture_ref,
        captured_at=draft.captured_at,
        model=draft.model,
        analysis=draft.analysis,
        dropped_operations=dropped,
        refinement_round=draft.refinement_round + 1,
    )


# ---------------------------------------------------------------------------
# Confirm + persist
# ---------------------------------------------------------------------------


DEFAULT_SCHEMA_URL = (
    "https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/"
    "v1.1.0/schemas/uacp.json"
)


def confirm_and_persist(
    draft: CaptureSynthesisDraft,
    *,
    output_path: str | None = None,
    approved: bool,
    authentication: dict[str, Any] | None = None,
    dispatch: dict[str, Any] | None = None,
    definitions: dict[str, Any] | None = None,
    schema_url: str = DEFAULT_SCHEMA_URL,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Stamp the draft with ``reviewed_at`` and (optionally) write to disk.

    Per §3.12 mandatory user review: ``approved`` MUST be True;
    anything else raises :class:`SynthesisNotApprovedError`. The
    caller (typically the CLI's interactive review surface) is
    responsible for showing the draft to the user, accepting any
    edits, and only setting ``approved=True`` after the user
    explicitly confirms.

    Returns the assembled `.uacp` artifact dict. When ``output_path``
    is supplied, it's also written to disk; when omitted, the caller
    writes it themselves.
    """
    if not approved:
        raise SynthesisNotApprovedError(
            "confirm_and_persist requires approved=True. Per §3.12 mandatory "
            "user review (parallel to §3.8), persistence of capture-synthesized "
            "schemas requires explicit affirmative confirmation. The CLI's "
            "interactive review surface MUST present the draft to the user "
            "and obtain their consent before passing approved=True."
        )

    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    reviewed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    operations: list[dict[str, Any]] = []
    for synth in draft.operations:
        op = dict(synth.operation)
        prov = CaptureProvenance(
            type=synth.provenance.type,
            captured_at=synth.provenance.captured_at,
            user_intent=synth.provenance.user_intent,
            capture_ref=synth.provenance.capture_ref,
            confidence=synth.provenance.confidence,
            reviewed_at=reviewed_at,
        )
        op["source"] = prov.to_dict()
        operations.append(op)

    artifact: dict[str, Any] = {
        "$schema": schema_url,
        "authentication": authentication or {},
        "dispatch": dispatch or {},
        "operations": operations,
    }
    if definitions:
        artifact["definitions"] = definitions

    if output_path is not None:
        from pathlib import Path

        Path(output_path).write_text(json.dumps(artifact, indent=2) + "\n")
        log.info(
            "capture-synthesized draft persisted to %s after user approval",
            output_path,
        )

    return artifact


__all__ = [
    "CaptureProvenance",
    "CaptureSynthesisDraft",
    "DEFAULT_MAX_REFINEMENT_ROUNDS",
    "RefinementLimitExceeded",
    "SynthesisNotApprovedError",
    "SynthesizedOperation",
    "SYSTEM_PROMPT",
    "build_user_message",
    "confirm_and_persist",
    "refine_synthesis",
    "synthesize_from_capture",
]
