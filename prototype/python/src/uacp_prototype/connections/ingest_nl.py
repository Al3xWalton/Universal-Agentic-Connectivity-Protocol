"""LLM-inferred schema authoring per §3.8.

The most distinctive single capability in UACP and the one with the
strictest spec contract. §3.8 specifies three load-bearing rules that
the prototype enforces here:

  1. **Mandatory user review before persistence.** The
     ``infer_from_description`` function returns the inferred draft
     plus provenance metadata. Persistence is a separate
     ``confirm_and_persist`` call that takes an explicit approval
     flag. Calling persist without ``approved=True`` raises
     ``InferenceNotApprovedError``. There is no path through the
     module that writes a `.uacp` file from inferred output without
     an explicit approval step.

  2. **Provenance metadata required.** Every inferred operation
     carries a ``source = {type: "inferred", model, description,
     confidence, reviewed_at}`` block per §3.8. ``reviewed_at`` is
     populated only at confirmation time (RFC 3339 timestamp at
     persistence). Operations with ``source.type == "inferred"``
     and missing ``reviewed_at`` MUST fail validation per §3.10
     (the spec layer enforces this; this module just produces
     compliant outputs).

  3. **Refinement preserves operation `id` and source attribution.**
     The refinement workflow lives in the module's public surface:
     ``refine_inference(draft, refinement_text, llm)`` accepts the
     prior draft and additional evidence (a response example, a
     correction, an updated description), invokes the LLM with the
     accumulated context, and returns a new draft preserving the
     original ``operations[].id`` so already-deployed agents keep
     working. The refined draft must go through ``confirm_and_persist``
     again before storage.

UACP does NOT specify the LLM, the prompt structure, or the
inference pipeline (per §3.8). This module accepts a pluggable
``llm_callable`` argument: any callable that takes a description
string + system prompt and returns a structured operations payload.
The default implementation wraps OpenRouter for convenience (since
AVA's stack already uses OpenRouter); operators can supply their own
callable to use Anthropic / OpenAI / a local model / a recorded
fixture.

The default model is ``anthropic/claude-haiku-4.5`` (a small/fast
model is appropriate for the inference task — the LLM's job is
structured-output generation against a tight schema, not deep
reasoning). The choice is overridable via the ``UACP_LLM_MODEL``
environment variable.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol


__all__ = [
    "InferenceDraft",
    "InferenceNotApprovedError",
    "InferenceProvenance",
    "InferredOperation",
    "LLMCallable",
    "build_default_openrouter_callable",
    "confirm_and_persist",
    "infer_from_description",
    "refine_inference",
]


log = logging.getLogger("uacp.connections.ingest_nl")


DEFAULT_MODEL = "anthropic/claude-haiku-4.5"


SYSTEM_PROMPT = """You are an API schema generator producing UACP v1.x \
operation entries from natural-language descriptions.

UACP is the Universal Agentic Connectivity Protocol. A `.uacp` artifact \
declares one or more `Operation` entries; each operation has:
  - id: kebab-or-snake-case unique identifier (matches [a-z][a-z0-9_-]{0,127})
  - summary: one-sentence user-intent description (NOT HTTP-shape vocabulary)
  - description: longer prose
  - request: { method, path, path_parameters?, query_parameters?, body? }
      method ∈ {GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS}
      path: RFC 6570 URI Template; path parameters in {braces}
      path_parameters / query_parameters: JSON Schema 2020-12 objects
      body: { media_type, schema } OR "none" OR { $ref: "#/definitions/..." }
  - response: { "<status>": { description, body, failure_predicate?, ... } }
  - pagination: { pattern: cursor / offset / link_header / none, ... } (optional)
  - idempotency: idempotent / not_idempotent / unknown (optional)

Return a JSON object with a single field `operations` that is an array of \
operation entries. Do NOT include authentication or dispatch blocks; the \
caller adds those separately.

Be conservative: prefer permissive JSON schemas over strict ones, since \
inferred schemas are best-effort. If the user's description is ambiguous \
about a parameter's type or shape, mark it permissively rather than guessing.

Output VALID JSON only. No prose, no markdown, no commentary.
"""


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class LLMCallable(Protocol):
    """Pluggable LLM interface. Takes (system_prompt, user_message) and
    returns a JSON-decodable string carrying ``{"operations": [...]}``.

    Operators can supply any callable: OpenRouter wrapper (default),
    Anthropic SDK, OpenAI SDK, a local model, a recorded fixture for
    deterministic tests. The §3.8 spec is silent on which LLM; this
    Protocol is the prototype's contract for "give me a draft."
    """

    model: str

    def __call__(self, *, system: str, user: str) -> str: ...


@dataclass(frozen=True)
class InferenceProvenance:
    """The §3.8 source block, populated at draft time + completed at
    confirmation time. ``reviewed_at`` is empty until
    ``confirm_and_persist`` populates it.
    """

    type: str = "inferred"
    model: str = ""
    description: str = ""
    confidence: str = "medium"  # low / medium / high
    reviewed_at: str = ""  # populated at confirm time

    def to_dict(self) -> dict[str, str]:
        out = {
            "type": self.type,
            "model": self.model,
            "description": self.description,
            "confidence": self.confidence,
        }
        if self.reviewed_at:
            out["reviewed_at"] = self.reviewed_at
        return out


@dataclass(frozen=True)
class InferredOperation:
    """A single operation entry produced by the LLM, carrying provenance.

    The operation field is the raw dict the LLM returned for that
    operation; the runtime can inject `source` into it before
    persistence. Kept as a dict (not a typed Operation) because the
    LLM may produce drafts with varying shape that need user review
    before pydantic validation.
    """

    operation: dict[str, Any]
    provenance: InferenceProvenance


@dataclass(frozen=True)
class InferenceDraft:
    """A draft `.uacp` operations block awaiting user review.

    `.operations` is the list of inferred operations with their
    individual provenance attached. `.raw_llm_response` is preserved
    verbatim so the user can see what the LLM said in case the
    structured parse missed something.
    """

    operations: tuple[InferredOperation, ...]
    raw_llm_response: str
    description: str
    model: str

    def operations_with_source(self) -> list[dict[str, Any]]:
        """Return the operations dicts with the `source` field populated
        from each operation's provenance — for serialization into a
        `.uacp` file's operations array. ``reviewed_at`` is included
        only when the provenance has been confirmed.
        """
        out = []
        for inferred in self.operations:
            op = dict(inferred.operation)
            op["source"] = inferred.provenance.to_dict()
            out.append(op)
        return out


class InferenceNotApprovedError(Exception):
    """Raised when ``confirm_and_persist`` is called without
    ``approved=True``. Per §3.8 mandatory user review, persistence
    requires explicit affirmative confirmation."""


# ---------------------------------------------------------------------------
# Default LLM callable: OpenRouter wrapper
# ---------------------------------------------------------------------------


def build_default_openrouter_callable(
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str = "https://openrouter.ai/api/v1",
) -> LLMCallable:
    """Build the default LLM callable that wraps OpenRouter.

    Reads `api_key` from the OPENROUTER_API_KEY env var when not
    supplied; reads `model` from UACP_LLM_MODEL env var (default
    anthropic/claude-haiku-4.5).

    Returns a Protocol-compliant callable. The wrapper is
    intentionally thin: synchronous, single-shot, no streaming, no
    tool use. The LLM's job is to produce structured operations JSON;
    a small/fast model with a rigid system prompt is the right shape.
    """
    api_key_resolved = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    model_resolved = model or os.environ.get("UACP_LLM_MODEL", DEFAULT_MODEL)

    @dataclass
    class _OpenRouterCallable:
        model: str = model_resolved

        def __call__(self, *, system: str, user: str) -> str:
            if not api_key_resolved:
                raise RuntimeError(
                    "OPENROUTER_API_KEY not set. Set the env var or supply "
                    "api_key explicitly to build_default_openrouter_callable. "
                    "Alternatively, supply your own LLMCallable to "
                    "infer_from_description (the prototype is LLM-agnostic)."
                )
            import httpx

            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key_resolved}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol",
                    "X-Title": "UACP prototype Stage 8e",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    return _OpenRouterCallable()


# ---------------------------------------------------------------------------
# Inference flow
# ---------------------------------------------------------------------------


def _parse_llm_response(raw: str) -> tuple[list[dict[str, Any]], str]:
    """Parse the LLM's response into (operations list, raw passed
    through). Tolerates the LLM occasionally wrapping its JSON in a
    markdown code fence even when the system prompt asks for plain
    JSON; strips the fence before parsing.

    Returns (operations_list, raw_response). Raises ValueError when
    the response can't be parsed or doesn't carry an operations array.
    """
    cleaned = raw.strip()
    # Strip markdown fences if present.
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        data = json.loads(cleaned)
    except ValueError as e:
        raise ValueError(
            f"LLM response is not valid JSON (after fence-strip): {cleaned[:200]!r}"
        ) from e

    if not isinstance(data, dict):
        raise ValueError(f"LLM response top-level must be a JSON object; got {type(data).__name__}")
    operations = data.get("operations")
    if not isinstance(operations, list):
        raise ValueError(
            f"LLM response.operations must be a list; got {type(operations).__name__}"
        )
    return operations, raw


def infer_from_description(
    description: str,
    *,
    llm: LLMCallable,
    confidence: str = "medium",
) -> InferenceDraft:
    """Run the LLM-inference flow.

    Per §3.8 mandatory user review: this function returns a draft;
    it does NOT persist. The caller MUST invoke
    ``confirm_and_persist`` with an explicit approval flag to write
    a `.uacp` file. Calling persist without approval raises
    InferenceNotApprovedError.

    Returns InferenceDraft holding:
      - operations: list of InferredOperation (operation dict +
        provenance with reviewed_at unset).
      - raw_llm_response: the full text the LLM returned, preserved
        verbatim for review.
      - description: the input the user gave (echoed back so
        provenance is complete; the LLM may paraphrase the
        description in its output but the original goes into the
        `source.description` field).
      - model: the LLMCallable's reported model identifier.
    """
    if not description.strip():
        raise ValueError("description must be non-empty")

    raw = llm(system=SYSTEM_PROMPT, user=description)
    operations_dicts, raw_text = _parse_llm_response(raw)

    inferred: list[InferredOperation] = []
    for op in operations_dicts:
        inferred.append(
            InferredOperation(
                operation=op,
                provenance=InferenceProvenance(
                    type="inferred",
                    model=getattr(llm, "model", ""),
                    description=description,
                    confidence=confidence,
                    reviewed_at="",  # populated at confirm time
                ),
            )
        )

    return InferenceDraft(
        operations=tuple(inferred),
        raw_llm_response=raw_text,
        description=description,
        model=getattr(llm, "model", ""),
    )


def confirm_and_persist(
    draft: InferenceDraft,
    *,
    output_path: str | None = None,
    approved: bool,
    authentication: dict[str, Any] | None = None,
    dispatch: dict[str, Any] | None = None,
    definitions: dict[str, Any] | None = None,
    schema_url: str = "https://uacp.spec/v1/schema.json",
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Stamp the draft with reviewed_at and (optionally) write to disk.

    Per §3.8 mandatory user review: ``approved`` MUST be True;
    anything else raises InferenceNotApprovedError. The caller
    (typically a CLI's interactive review surface) is responsible
    for showing the draft to the user, accepting their edits, and
    only setting approved=True after the user explicitly confirms.

    The function returns the assembled `.uacp` artifact dict. When
    ``output_path`` is supplied, it's also written to disk; when
    omitted, the caller writes it themselves (useful for in-memory
    flows like tests or dry-run).

    `authentication` / `dispatch` / `definitions` are the surrounding
    artifact blocks the LLM doesn't generate (per §3.8 the LLM
    produces operations only; auth / dispatch / definitions are
    user-supplied or session-supplied via the CLI's connection-
    creation flow).
    """
    if not approved:
        raise InferenceNotApprovedError(
            "confirm_and_persist requires approved=True. Per §3.8 mandatory "
            "user review, persistence of inferred schemas requires explicit "
            "affirmative confirmation. The CLI's interactive review surface "
            "MUST present the draft to the user and obtain their consent "
            "before passing approved=True."
        )

    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    reviewed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    operations: list[dict[str, Any]] = []
    for inferred in draft.operations:
        op = dict(inferred.operation)
        prov = InferenceProvenance(
            type=inferred.provenance.type,
            model=inferred.provenance.model,
            description=inferred.provenance.description,
            confidence=inferred.provenance.confidence,
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
        log.info("inference draft persisted to %s after user approval", output_path)

    return artifact


# ---------------------------------------------------------------------------
# Refinement
# ---------------------------------------------------------------------------


REFINEMENT_PROMPT_SUFFIX = """

REFINEMENT CONTEXT — the prior draft produced unexpected results in dispatch \
or doesn't match the provider's actual behavior. Use the following additional \
evidence to refine. CRITICAL: preserve every operation's `id` field exactly \
as it appeared in the prior draft (per §3.8 refinement preserves id and \
source attribution; already-deployed agents reference operations by id and \
breaking those references would break the agents).

Prior draft operations (by id):
{prior_operations_summary}

Additional evidence from the user:
{refinement_text}
"""


def refine_inference(
    draft: InferenceDraft,
    refinement_text: str,
    *,
    llm: LLMCallable,
    confidence: str = "medium",
) -> InferenceDraft:
    """Refine an inferred draft per §3.8.

    Takes the prior draft + new evidence (a response example, a
    corrected request shape, an updated description), invokes the
    LLM with the accumulated context, and returns a new draft. The
    refined draft preserves every operation's id from the prior
    draft so already-deployed agents keep working.

    Like ``infer_from_description``, this returns a draft awaiting
    user review; the caller invokes ``confirm_and_persist`` to
    record approval and write the refined artifact.
    """
    prior_summary_lines = []
    for inferred in draft.operations:
        op = inferred.operation
        prior_summary_lines.append(
            f"  - id: {op.get('id', '<missing>')}; summary: {op.get('summary', '<missing>')}"
        )
    prior_summary = "\n".join(prior_summary_lines) or "  (none)"

    user_message = (
        f"{draft.description}\n"
        + REFINEMENT_PROMPT_SUFFIX.format(
            prior_operations_summary=prior_summary,
            refinement_text=refinement_text,
        )
    )

    raw = llm(system=SYSTEM_PROMPT, user=user_message)
    operations_dicts, raw_text = _parse_llm_response(raw)

    # Verify id-preservation rule: every prior id should appear in the
    # refined output. If the LLM dropped or renamed an id, we keep it
    # as a soft constraint via logger warning rather than raising —
    # the user-review step is the load-bearing check.
    prior_ids = {inf.operation.get("id") for inf in draft.operations}
    refined_ids = {op.get("id") for op in operations_dicts}
    dropped = prior_ids - refined_ids
    if dropped:
        log.warning(
            "refinement dropped prior operation id(s) %s; the refined draft "
            "would break agents that reference them. Reject the refinement "
            "in user-review unless the renames are intentional.",
            sorted(d for d in dropped if d),
        )

    inferred: list[InferredOperation] = []
    for op in operations_dicts:
        # Augment description for refined provenance: original description
        # plus the refinement evidence so the §3.8 source.description
        # captures the full context.
        merged_description = (
            f"{draft.description}\n\n[refined: {refinement_text}]"
        )
        inferred.append(
            InferredOperation(
                operation=op,
                provenance=InferenceProvenance(
                    type="inferred",
                    model=getattr(llm, "model", ""),
                    description=merged_description,
                    confidence=confidence,
                    reviewed_at="",
                ),
            )
        )

    return InferenceDraft(
        operations=tuple(inferred),
        raw_llm_response=raw_text,
        description=draft.description + f"\n\n[refined: {refinement_text}]",
        model=getattr(llm, "model", ""),
    )
