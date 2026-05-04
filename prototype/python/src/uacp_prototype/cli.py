"""UACP CLI — three subcommands.

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
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .connections.ingest_openapi import (
    IngestionResult,
    from_discovery_doc,
    from_openapi,
)
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
        "$schema": "https://uacp.spec/v1/schema.json",
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

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
