"""UACP CLI subcommands.

  - `uacp validate <file>` — load and validate a `.uacp` file; print a
    summary of operations and exit 0 on success / 1 on validation
    failure.
  - `uacp ingest-openapi <url> [--output FILE] [--discovery]` — ingest a
    canonical OpenAPI 3.x document or a Google discovery document into
    a `.uacp` artifact. Writes to FILE or stdout. With --discovery,
    treats the URL as a Google discovery document.
  - `uacp dispatch <file> <operation_id> [--params JSON]` — dispatch the
    named operation against the configured connection. Requires a
    Connection in `active` state with credentials in the local
    keyring; prints the canonical error / success result.
  - `uacp capture-storage-state --provider <name> --output FILE` —
    Playwright-based session-cookie capture stub kept around from
    Stage 8e for the legacy session_cookie auth flow; prints manual-
    recipe instructions.
  - `uacp capture-session --initial-url <url> --output <secret-ref>
    [--browser playwright|scrapling] [--provider <name>]` — Stage 11.1
    browser-instrumented session capture per §3.12. Opens the target
    URL in a real browser, records every HTTP request the user makes
    during the session, and persists the result as an encrypted-at-
    rest HAR artifact under the supplied `secret://` reference.
  - `uacp synthesize-from-capture --capture-ref <secret://> --intent
    "<description>" --output <path/to/file.uacp>` — Stage 11.2 LLM-
    driven synthesis pass that turns a captured session into a
    draft `.uacp` file. Renders the inferred operations interactively
    and gates persistence on explicit user approval per §3.12 +
    §3.8 mandatory-user-review.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Callable

from .capture import (
    BrowserRecorder,
    CaptureArtifact,
    CaptureError,
    StoredCapture,
    load_capture,
    store_capture,
)
from .connections.ingest_capture import (
    CaptureSynthesisDraft,
    DEFAULT_MAX_REFINEMENT_ROUNDS,
    RefinementLimitExceeded,
    SynthesisNotApprovedError,
    confirm_and_persist as _confirm_capture_synthesis,
    refine_synthesis,
    synthesize_from_capture,
)
from .connections.ingest_nl import LLMCallable, build_default_openrouter_callable
from .connections.ingest_openapi import (
    IngestionResult,
    from_discovery_doc,
    from_openapi,
)
from .security.secrets import SecretURI
from .spec.loader import load
from .spec.models import OpenAPISource


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        artifact = load(args.file)
    except Exception as e:
        print(f"validation failed: {e}", file=sys.stderr)
        return 1
    print(f"OK — {Path(args.file).name}")
    print(f"  authentication: {artifact.authentication.method}")
    print(f"  base_url: {artifact.dispatch.base_url}")
    print(f"  operations ({len(artifact.operations)}):")
    for op in artifact.operations:
        idemp = op.idempotency
        pag = op.pagination.pattern if op.pagination else "none"
        print(f"    - {op.id} [{op.request.method} {op.request.path}] idempotency={idemp} pagination={pag}")
    return 0


def _cmd_ingest_openapi(args: argparse.Namespace) -> int:
    try:
        if args.discovery:
            result: IngestionResult = from_discovery_doc(args.url)
        else:
            result = from_openapi(args.url)
    except Exception as e:
        print(f"ingestion failed: {e}", file=sys.stderr)
        return 1

    artifact_dict = {
        "$schema": "https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.0.0/schemas/uacp.json",
        "authentication": {
            "method": "oauth2_authorization_code",
            "authorization_endpoint": "REPLACED — set after ingestion",
            "token_endpoint": "REPLACED — set after ingestion",
            "client_id": "REPLACED",
            "client_secret_ref": "secret://local-keyring/REPLACED#client_secret",
            "scopes": [],
            "redirect_uri": "http://localhost:8765/oauth/callback",
        },
        "dispatch": {
            "base_url": result.base_url or "https://REPLACE.example.com",
        },
        "definitions": result.definitions,
        "operations": [
            json.loads(op.model_dump_json(by_alias=True)) for op in result.operations
        ],
    }
    output = json.dumps(artifact_dict, indent=2)
    if args.output:
        Path(args.output).write_text(output + "\n")
        print(f"wrote {len(result.operations)} operations to {args.output}")
    else:
        print(output)
    return 0


def _cmd_dispatch(args: argparse.Namespace) -> int:
    """Dispatch is wired in subsequent provider sessions; in Stage 8a
    the integration tests exercise the dispatch path with operator-
    supplied credentials, and the unit-test surface exercises it via
    mocks. The CLI emits an explanatory message rather than running a
    half-implemented path.
    """
    print(
        "uacp dispatch: requires an active Connection with credentials in "
        "the local-keyring store. Run the integration test suite "
        "(tests/providers/test_google.py with @pytest.mark.integration) for "
        "the end-to-end path; that's the v1.0 prototype's primary "
        "real-provider validation surface.",
        file=sys.stderr,
    )
    return 1


def _cmd_capture_storage_state(args: argparse.Namespace) -> int:
    """Stubbed for v1.0 — operators capture storage state with a
    one-shot Playwright session per the README. The CLI emits the
    instructions verbatim so they're discoverable from `uacp --help`.

    Stage 9+ replaces this stub with an interactive Playwright launch
    that opens a Chromium window, waits for the operator to complete
    sign-in, then writes ``page.context.storage_state(path=...)``.
    """
    output = args.output or "~/.uacp/storage/<provider>.json"
    msg = (
        "uacp capture-storage-state: STUB in v1.0.\n\n"
        f"Capture storage state for provider '{args.provider}' manually:\n\n"
        "  1. uv run playwright install chromium\n"
        "  2. uv run python -c \"from playwright.sync_api import sync_playwright;\\\n"
        "       p = sync_playwright().start();\\\n"
        "       browser = p.chromium.launch(headless=False);\\\n"
        "       context = browser.new_context();\\\n"
        "       page = context.new_page();\\\n"
        f"       page.goto('https://notebooklm.google.com/');\\\n"
        "       input('Sign in, then press Enter to capture...');\\\n"
        f"       context.storage_state(path='{output}');\\\n"
        "       browser.close()\"\n"
        f"  3. chmod 600 {output}\n\n"
        "Per §2.10, the captured state is sensitive — store it with\n"
        "filesystem 0600 permissions, never commit it to git, and\n"
        "rotate it (recapture) every 30 days at minimum.\n"
    )
    print(msg, file=sys.stderr)
    return 1


log = logging.getLogger("uacp.cli")


# ---------------------------------------------------------------------------
# capture-session — Stage 11.1 implementation per §3.12
# ---------------------------------------------------------------------------


PROGRESS_INTERVAL_SECONDS = 5.0


def _validate_secret_ref(ref: str, *, allowed_stores: tuple[str, ...] = ("local-keyring",)) -> SecretURI:
    """Parse + validate a `secret://` reference for the capture-session
    --output argument. Raises ValueError on malformed input."""
    if not ref.startswith("secret://"):
        raise ValueError(
            f"--output must be a secret:// URI per §2.7; got {ref!r}. "
            f"Example: secret://local-keyring/example-capture"
        )
    uri = SecretURI.parse(ref)
    if uri.store not in allowed_stores:
        raise ValueError(
            f"--output store {uri.store!r} not supported by the prototype's "
            f"capture path; recognized stores: {sorted(allowed_stores)}. "
            f"§6.2's full registry includes vault + aws-secrets-manager but "
            f"those resolvers don't ship with capture persistence yet."
        )
    if uri.field is not None:
        raise ValueError(
            "--output must not include a #field selector; capture artifacts "
            "are stored as a single blob per id."
        )
    return uri


def _await_user_signal_or_browser_close(
    *,
    recorder: BrowserRecorder,
    stdin: IO[str],
    stdout: IO[str],
    progress_interval: float = PROGRESS_INTERVAL_SECONDS,
    poll_interval: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Block until the user presses Enter (returns "user_stop") OR the
    browser disconnects (returns "browser_closed"). Prints live request
    counts every progress_interval seconds. Tests inject mocked stdin
    / stdout / sleep so the loop's behavior is deterministic."""
    enter_event = threading.Event()

    def stdin_reader() -> None:
        try:
            stdin.readline()
        except Exception:
            pass
        finally:
            enter_event.set()

    reader_thread = threading.Thread(target=stdin_reader, daemon=True, name="uacp-cli-stdin")
    reader_thread.start()

    last_progress = time.time()
    while True:
        # Check disconnect FIRST: if the browser is gone, the user
        # can't have pressed Enter on that window. An empty stdin
        # (test fixture) raises an EOF that sets enter_event quickly,
        # but we want browser_closed to take precedence in that race.
        if recorder.disconnect_event().is_set():
            return "browser_closed"
        if enter_event.is_set():
            return "user_stop"
        now = time.time()
        if now - last_progress >= progress_interval:
            count = recorder.requests_captured()
            print(f"  Captured {count} request(s) so far...", file=stdout, flush=True)
            last_progress = now
        sleep(poll_interval)


def _cmd_capture_session(args: argparse.Namespace) -> int:
    """Run a browser-instrumented capture session per §3.12.

    See module docstring for the full invocation form. The
    orchestration is testable: the BrowserRecorder is the only
    real dependency, and it accepts a driver_factory so tests can
    swap in a fake driver. The await-loop is similarly testable
    by injecting stdin / stdout streams.
    """
    try:
        uri = _validate_secret_ref(args.output)
    except ValueError as e:
        print(f"capture-session: {e}", file=sys.stderr)
        return 2

    recorder = _build_capture_recorder(args)
    return _run_capture_session(
        recorder=recorder,
        initial_url=args.initial_url,
        output_uri=uri,
        stdin=sys.stdin,
        stdout=sys.stdout,
    )


def _build_capture_recorder(args: argparse.Namespace) -> BrowserRecorder:
    """Construct the BrowserRecorder from CLI args. Pulled out so
    tests can monkeypatch the constructor with a recorder backed by
    a FakeDriver."""
    return BrowserRecorder(
        browser_backend=args.browser,
        headless=False,
        provider=args.provider,
    )


def _run_capture_session(
    *,
    recorder: BrowserRecorder,
    initial_url: str,
    output_uri: SecretURI,
    stdin: IO[str],
    stdout: IO[str],
) -> int:
    """Execute the capture-session orchestration. Returns the CLI
    exit code. Pulled out so tests can drive the flow with
    mocked recorder + streams."""
    started_at = datetime.now(timezone.utc)

    print(
        f"Opening {initial_url} in a browser. Log in if needed, then "
        f"demonstrate the actions you want UACP to learn. When you're "
        f"done, return to this terminal and press Enter to stop "
        f"recording. (You can also close the browser window to stop.)",
        file=stdout,
        flush=True,
    )
    print(file=stdout, flush=True)

    try:
        recorder.start(initial_url)
    except CaptureError as e:
        print(f"capture-session: failed to start: {e}", file=sys.stderr)
        return 3

    # Install a SIGINT/SIGTERM handler so the recorder gets a clean
    # stop on ctrl-c / kill — partial artifact still persisted.
    interrupted: dict[str, bool] = {"hit": False}

    def _handle_signal(signum: int, _frame: object) -> None:
        interrupted["hit"] = True
        # Mark disconnect_event so the await loop wakes up.
        recorder.disconnect_event().set()

    prior_handlers: list[tuple[int, signal.Handlers | Callable | None]] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            prev = signal.signal(sig, _handle_signal)
            prior_handlers.append((sig, prev))
        except (ValueError, OSError):
            # Not the main thread / unsupported signal — best-effort.
            pass

    stop_reason = "unknown"
    try:
        stop_reason = _await_user_signal_or_browser_close(
            recorder=recorder, stdin=stdin, stdout=stdout
        )
        if interrupted["hit"]:
            stop_reason = "interrupted"
        if stop_reason == "browser_closed":
            print(
                "  Browser closed; finalizing capture. Press Enter to "
                "continue.",
                file=stdout,
                flush=True,
            )
    finally:
        # Restore signal handlers before stopping the recorder so
        # any errors during stop() don't loop back through the
        # cleanup handler.
        for sig, prev in prior_handlers:
            try:
                signal.signal(sig, prev)  # type: ignore[arg-type]
            except (ValueError, OSError):
                pass

    artifact: CaptureArtifact | None = None
    stop_error: CaptureError | None = None
    try:
        artifact = recorder.stop()
    except CaptureError as e:
        stop_error = e
    if stop_error is not None or artifact is None:
        print(
            f"capture-session: stop failed: {stop_error}",
            file=sys.stderr,
        )
        return 4

    duration = (datetime.now(timezone.utc) - started_at).total_seconds()
    print(
        f"\nCaptured {len(artifact.entries)} request(s) over "
        f"{duration:.1f}s. Persisting...",
        file=stdout,
        flush=True,
    )

    try:
        stored = _persist_capture(artifact, output_uri)
    except CaptureError as e:
        print(f"capture-session: persistence failed: {e}", file=sys.stderr)
        return 5

    print(
        f"Capture stored at {stored.ref}. Use it as the source.capture_ref "
        f"of a session_capture-sourced operation in a .uacp file (per "
        f"§3.12). Stage 11.2's operation-synthesis pass will consume it.",
        file=stdout,
        flush=True,
    )
    return 0 if stop_reason in ("user_stop", "browser_closed", "interrupted") else 0


def _persist_capture(
    artifact: CaptureArtifact, output_uri: SecretURI
) -> StoredCapture:
    """Store the artifact under the user-supplied URI's store + id.

    The user-supplied URI is honored verbatim — the resulting ref
    matches what the user passed as --output. The artifact's
    deterministic ``capture_id`` (hash of initial_url + captured_at
    + provider per §3.12) stays on the artifact for cross-run
    diagnostics; the storage path uses the user's chosen name so
    operators can find their captures by recognizable identifiers.
    """
    return store_capture(
        artifact,
        secret_store=output_uri.store,
        storage_id=output_uri.id,
    )


# ---------------------------------------------------------------------------
# synthesize-from-capture — Stage 11.2 implementation per §3.12 + §3.8
# ---------------------------------------------------------------------------


_REVIEW_PROMPT_HELP = (
    "Approve all (a), edit individual operations in $EDITOR (e), "
    "refine via natural language (r), or abort (x)?"
)


def _render_synthesis_draft(draft: CaptureSynthesisDraft, *, stdout: IO[str]) -> None:
    """Pretty-print the draft to ``stdout`` for user review.

    Each operation: id, summary, method+path, parameters with
    required/optional flags, provenance, source confidence.
    Hallucinated operations the LLM tried to invent (and the
    analyzer dropped) are surfaced separately so the operator can
    see what was rejected.
    """
    print(file=stdout, flush=True)
    print(
        f"Synthesized {len(draft.operations)} operation(s) from capture "
        f"{draft.capture_ref} (round {draft.refinement_round}, model {draft.model}):",
        file=stdout,
        flush=True,
    )
    print(file=stdout, flush=True)

    if not draft.operations:
        print("  (no operations matched the candidate list — try refining the intent or recapture)", file=stdout, flush=True)
    for i, synth in enumerate(draft.operations, start=1):
        op = synth.operation
        prov = synth.provenance
        print(f"  {i}. id: {op.get('id', '<no-id>')}", file=stdout, flush=True)
        print(f"     summary: {op.get('summary', '<no-summary>')}", file=stdout, flush=True)
        method = (op.get("request", {}) or {}).get("method", "?")
        path = (op.get("request", {}) or {}).get("path", "?")
        print(f"     {method} {path}", file=stdout, flush=True)
        idemp = op.get("idempotency", "unknown")
        print(f"     idempotency: {idemp}", file=stdout, flush=True)

        req = op.get("request", {}) or {}
        for kind in ("path_parameters", "query_parameters"):
            schema = req.get(kind)
            if isinstance(schema, dict) and schema.get("properties"):
                required = set(schema.get("required", []) or [])
                names = []
                for name in schema["properties"]:
                    flag = "required" if name in required else "optional"
                    names.append(f"{name} ({flag})")
                if names:
                    print(f"     {kind}: {', '.join(names)}", file=stdout, flush=True)
        body = req.get("body")
        if isinstance(body, dict) and body.get("schema"):
            body_schema = body["schema"]
            if isinstance(body_schema, dict) and body_schema.get("properties"):
                required = set(body_schema.get("required", []) or [])
                names = []
                for name in body_schema["properties"]:
                    flag = "required" if name in required else "optional"
                    names.append(f"{name} ({flag})")
                if names:
                    print(f"     body fields: {', '.join(names)}", file=stdout, flush=True)
        print(
            f"     provenance: source.type=capture confidence={prov.confidence} reviewed_at=(pending)",
            file=stdout,
            flush=True,
        )
        print(file=stdout, flush=True)

    if draft.dropped_operations:
        print(
            f"  Dropped {len(draft.dropped_operations)} hallucinated operation(s) "
            f"the LLM proposed but that didn't match any candidate cluster:",
            file=stdout,
            flush=True,
        )
        for d in draft.dropped_operations:
            method = (d.get("request", {}) or {}).get("method", "?")
            path = (d.get("request", {}) or {}).get("path", "?")
            print(
                f"    - id={d.get('id', '?')} {method} {path}", file=stdout, flush=True
            )
        print(file=stdout, flush=True)


_DECISION_ALIASES = {
    "a": "a",
    "approve": "a",
    "e": "e",
    "edit": "e",
    "r": "r",
    "refine": "r",
    "x": "x",
    "abort": "x",
}


def _read_user_decision(prompt: str, stdin: IO[str], stdout: IO[str]) -> str:
    """Read the user's review decision. Loops until a recognized
    single-letter answer ('a' / 'e' / 'r' / 'x') is supplied (or the
    word-form alias is typed in full). EOF treats as abort to avoid
    silent persistence. Tests inject a StringIO stdin so the loop is
    deterministic."""
    while True:
        print(prompt + " ", end="", file=stdout, flush=True)
        line = stdin.readline()
        if not line:  # EOF — treat as abort to avoid silent persistence
            return "x"
        choice = line.strip().lower()
        if choice in _DECISION_ALIASES:
            return _DECISION_ALIASES[choice]
        print(
            "  unrecognized choice; please answer 'a' / 'e' / 'r' / 'x'",
            file=stdout,
            flush=True,
        )


def _open_editor_for_draft(
    draft: CaptureSynthesisDraft,
    *,
    authentication: dict[str, Any],
    dispatch: dict[str, Any],
    editor_command: str | None = None,
) -> CaptureSynthesisDraft | None:
    """Open the user's $EDITOR with the assembled .uacp draft as a
    JSON file. On save, parse + validate the resulting artifact and
    return a new CaptureSynthesisDraft reflecting the operator's
    edits. Returns None on parse/validation failure (the CLI prompts
    again).

    The function is split from the CLI so tests can monkeypatch
    ``editor_command`` to a no-op.
    """
    import os
    import subprocess
    import tempfile

    artifact_dict = {
        "$schema": "https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.1.0/schemas/uacp.json",
        "authentication": authentication,
        "dispatch": dispatch,
        "operations": [dict(op.operation, source=op.provenance.to_dict()) for op in draft.operations],
    }

    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".uacp", delete=False
    ) as fh:
        fh.write(json.dumps(artifact_dict, indent=2))
        fh.flush()
        path = fh.name

    cmd = editor_command or os.environ.get("EDITOR", "vi")
    try:
        subprocess.call([cmd, path])  # nosec — operator-supplied editor
    except FileNotFoundError:
        log.warning("editor %r not found — falling back to no-op edit", cmd)

    try:
        edited = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning("edited file unreadable: %s", e)
        return None
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    edited_ops = edited.get("operations", [])
    if not isinstance(edited_ops, list):
        return None

    from .connections.ingest_capture import CaptureProvenance, SynthesizedOperation

    rebuilt: list[SynthesizedOperation] = []
    for op in edited_ops:
        if not isinstance(op, dict):
            continue
        source = op.pop("source", {})
        if not isinstance(source, dict):
            source = {}
        prov = CaptureProvenance(
            type="capture",
            captured_at=source.get("captured_at", draft.captured_at),
            user_intent=source.get("user_intent", draft.user_intent),
            capture_ref=source.get("capture_ref", draft.capture_ref),
            confidence=source.get("confidence", "medium"),
            reviewed_at="",  # cleared so confirm_and_persist re-stamps
        )
        rebuilt.append(SynthesizedOperation(operation=op, provenance=prov))

    from dataclasses import replace

    return replace(draft, operations=rebuilt)


def _emit_audit_user_reviewed(*, capture_ref: str, decision: str) -> None:
    """Per §6.6: user-reviewed event with the operator's decision
    (approve/edit/refine/abort). The decision letter is the
    operator's signal; never carries any captured content."""
    log.info(
        "synthesis user-reviewed: capture_ref=%s decision=%s", capture_ref, decision
    )


def _emit_audit_file_persisted(
    *, capture_ref: str, output_path: str, operation_count: int
) -> None:
    log.info(
        "synthesis file-persisted: capture_ref=%s output=%s operations=%d",
        capture_ref,
        output_path,
        operation_count,
    )


def _build_capture_llm(args: argparse.Namespace) -> LLMCallable:
    """Construct the LLMCallable for capture synthesis. Pulled out
    so tests can monkeypatch the constructor with a deterministic
    mock."""
    return build_default_openrouter_callable()


def _cmd_synthesize_from_capture(args: argparse.Namespace) -> int:
    """Run §3.12 + §3.8 LLM synthesis from a captured session.

    The orchestration:

      1. Validate the --capture-ref + --output args.
      2. Build the LLMCallable (default OpenRouter; tests inject).
      3. Call synthesize_from_capture() to get the initial draft.
      4. Render the draft + prompt the user (approve / edit / refine
         / abort).
      5. Loop on refine until approved, edited, aborted, or the
         3-round cap is reached.
      6. On approve: stamp reviewed_at via confirm_and_persist
         and write to --output. Exit 0.
      7. On abort or cap-with-no-approval: do NOT persist. Exit 0
         (clean exit; the capture remains; the user can re-run later).
    """
    output_path = Path(args.output).expanduser().resolve()
    if output_path.exists() and not args.force:
        print(
            f"synthesize-from-capture: refusing to overwrite existing file "
            f"{output_path} (use --force to overwrite).",
            file=sys.stderr,
        )
        return 2

    try:
        SecretURI.parse(args.capture_ref)
    except ValueError as e:
        print(f"synthesize-from-capture: bad --capture-ref: {e}", file=sys.stderr)
        return 2

    if not args.intent.strip():
        print(
            "synthesize-from-capture: --intent must be non-empty.",
            file=sys.stderr,
        )
        return 2

    try:
        llm = _build_capture_llm(args)
    except Exception as e:
        print(f"synthesize-from-capture: LLM init failed: {e}", file=sys.stderr)
        return 3

    try:
        draft = synthesize_from_capture(
            capture_ref=args.capture_ref,
            user_intent=args.intent,
            llm=llm,
        )
    except Exception as e:
        print(f"synthesize-from-capture: synthesis failed: {e}", file=sys.stderr)
        return 4

    auth_block: dict[str, Any] = {}
    dispatch_block: dict[str, Any] = {"base_url": f"https://{draft.analysis.primary_host}"} if draft.analysis.primary_host else {}

    return _run_review_loop(
        draft=draft,
        llm=llm,
        output_path=output_path,
        authentication=auth_block,
        dispatch=dispatch_block,
        stdin=sys.stdin,
        stdout=sys.stdout,
    )


def _run_review_loop(
    *,
    draft: CaptureSynthesisDraft,
    llm: LLMCallable,
    output_path: Path,
    authentication: dict[str, Any],
    dispatch: dict[str, Any],
    stdin: IO[str],
    stdout: IO[str],
    editor_command: str | None = None,
) -> int:
    """The interactive review loop. Pulled out so tests can drive it
    with mocked stdin / stdout / LLM."""

    while True:
        _render_synthesis_draft(draft, stdout=stdout)
        choice = _read_user_decision(_REVIEW_PROMPT_HELP, stdin, stdout)
        _emit_audit_user_reviewed(
            capture_ref=draft.capture_ref, decision=choice
        )

        if choice == "a":
            try:
                _confirm_capture_synthesis(
                    draft,
                    approved=True,
                    output_path=str(output_path),
                    authentication=authentication or None,
                    dispatch=dispatch or None,
                )
            except SynthesisNotApprovedError as e:
                # Defensive: confirm_and_persist enforces the gate
                # explicitly. Should never trigger here since we
                # passed approved=True, but a future refactor could
                # break this and we want a clear failure.
                print(f"synthesize-from-capture: {e}", file=sys.stderr)
                return 5
            _emit_audit_file_persisted(
                capture_ref=draft.capture_ref,
                output_path=str(output_path),
                operation_count=len(draft.operations),
            )
            print(
                f"\nApproved. Wrote {len(draft.operations)} operation(s) to "
                f"{output_path}.",
                file=stdout,
                flush=True,
            )
            return 0

        if choice == "e":
            edited = _open_editor_for_draft(
                draft,
                authentication=authentication,
                dispatch=dispatch,
                editor_command=editor_command,
            )
            if edited is None:
                print(
                    "  edit failed to parse; keeping prior draft", file=stdout, flush=True
                )
                continue
            draft = edited
            # After an edit we drop back into the review loop so the
            # user can approve / refine / abort the edited draft.
            continue

        if choice == "r":
            print("  Describe what's wrong (one line):", file=stdout, flush=True)
            line = stdin.readline()
            if not line.strip():
                print("  empty feedback; canceling refinement", file=stdout, flush=True)
                continue
            try:
                draft = refine_synthesis(draft, line.strip(), llm=llm)
            except RefinementLimitExceeded as e:
                print(
                    f"\n  {e}\n  Switch to manual editing of the draft .uacp "
                    f"file or abort.",
                    file=stdout,
                    flush=True,
                )
                continue
            except Exception as e:
                print(
                    f"  refinement failed: {e}", file=stdout, flush=True
                )
                continue
            continue

        # 'x' or unrecognized → abort
        print(
            "\nAborted. Capture artifact is still available at "
            f"{draft.capture_ref}; re-run synthesize-from-capture later "
            "to try again.",
            file=stdout,
            flush=True,
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uacp", description="UACP reference CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate", help="Validate a .uacp file")
    p_validate.add_argument("file", help="path to the .uacp file")
    p_validate.set_defaults(func=_cmd_validate)

    p_ingest = sub.add_parser(
        "ingest-openapi",
        help="Ingest an OpenAPI 3.x or Google discovery document into a .uacp file",
    )
    p_ingest.add_argument("url", help="URL or path to the source document")
    p_ingest.add_argument("--output", help="output .uacp file (defaults to stdout)")
    p_ingest.add_argument(
        "--discovery",
        action="store_true",
        help="treat URL as a Google discovery document instead of canonical OpenAPI",
    )
    p_ingest.set_defaults(func=_cmd_ingest_openapi)

    p_dispatch = sub.add_parser(
        "dispatch",
        help="Dispatch an operation against the configured connection",
    )
    p_dispatch.add_argument("file", help="path to the .uacp file")
    p_dispatch.add_argument("operation_id", help="operation id to dispatch")
    p_dispatch.add_argument("--params", help="JSON string of params")
    p_dispatch.set_defaults(func=_cmd_dispatch)

    p_capture = sub.add_parser(
        "capture-storage-state",
        help="(STUB) Capture browser session cookies for session_cookie connections",
    )
    p_capture.add_argument(
        "--provider",
        required=True,
        help="provider id (e.g. notebooklm)",
    )
    p_capture.add_argument(
        "--output",
        help="path to write storage_state.json",
    )
    p_capture.set_defaults(func=_cmd_capture_storage_state)

    p_capture_session = sub.add_parser(
        "capture-session",
        help=(
            "Run a browser-instrumented session capture per §3.12 "
            "(added in v1.1)"
        ),
    )
    p_capture_session.add_argument(
        "--initial-url",
        required=True,
        help="URL to open in the browser at the start of the capture session",
    )
    p_capture_session.add_argument(
        "--output",
        required=True,
        help=(
            "secret:// URI where the captured artifact is persisted "
            "(e.g. secret://local-keyring/example-capture)"
        ),
    )
    p_capture_session.add_argument(
        "--browser",
        choices=("playwright", "scrapling"),
        default="playwright",
        help="capture backend (default: playwright)",
    )
    p_capture_session.add_argument(
        "--provider",
        default=None,
        help="optional provider name for audit-log + capture-id seeding",
    )
    p_capture_session.set_defaults(func=_cmd_capture_session)

    p_synth = sub.add_parser(
        "synthesize-from-capture",
        help=(
            "Run §3.12 + §3.8 LLM synthesis against a captured session. "
            "Produces a draft .uacp file gated on explicit user approval."
        ),
    )
    p_synth.add_argument(
        "--capture-ref",
        required=True,
        help=(
            "secret:// URI returned by `uacp capture-session --output ...` "
            "(e.g. secret://local-keyring/example-capture)"
        ),
    )
    p_synth.add_argument(
        "--intent",
        required=True,
        help=(
            "Natural-language description of what the user demonstrated "
            "during the capture (e.g. 'I logged into Slack and sent a "
            "message in #general')."
        ),
    )
    p_synth.add_argument(
        "--output",
        required=True,
        help="Path to write the synthesized .uacp file after approval.",
    )
    p_synth.add_argument(
        "--force",
        action="store_true",
        help="Overwrite --output if it already exists.",
    )
    p_synth.set_defaults(func=_cmd_synthesize_from_capture)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
