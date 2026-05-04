"""UACP CLI — `uacp validate`, `uacp ingest-openapi`, `uacp dispatch`.

Filled in Commit 8 once the underlying modules are in place.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in {"-h", "--help"}:
        print(
            "uacp — UACP reference CLI\n\n"
            "Usage:\n"
            "  uacp validate <file>\n"
            "  uacp ingest-openapi <url> --output <file>\n"
            "  uacp dispatch <file> <operation_id> [--params <json>]\n"
        )
        return 0
    print(f"uacp: subcommand '{args[0]}' not yet wired (Commit 8)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
