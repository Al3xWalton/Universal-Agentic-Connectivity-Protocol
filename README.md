# UACP — Universal Agentic Connectivity Protocol

UACP is a wire format and runtime contract for describing how an AI agent authenticates to and dispatches operations against an external service.

UACP is a peer to the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). Where MCP standardizes the agent-to-tool surface — how an LLM application calls a tool — UACP standardizes the agent-to-external-service surface — how the tool, once called, reaches the external service it connects to. The two protocols compose: UACP-defined connections can be exposed through MCP servers as tools, and an MCP-aware agent does not need UACP-specific code to consume one.

## Status

UACP `v1.0.0` is the first stable release, frozen on 2026-05-04. Subsequent `v1.x` releases are non-breaking per [Stage 7 — Versioning](./docs/07-versioning.md) §7.2; `v2` follows the public RFC process described in §7.6.

This repository contains the canonical specification under [`docs/`](./docs/), the JSON Schema artifact at [`schemas/uacp.json`](./schemas/uacp.json), and a Python reference implementation under [`prototype/python/`](./prototype/python/). The production reference implementation will live in the AVA monorepo at `backend/services/connections-broker/`.

## Read the specification

Start with the [Stage 0 Primer](./docs/00-primer.md) for terminology, scope, prior-art comparison, and document conventions, then read the [Stage 1 Principles](./docs/01-principles.md) for the foundational design constraints.

The [`SPEC.md`](./SPEC.md) document indexes the full specification and tracks the status of each stage.

## Why UACP?

A user who wants their agent to act against a long-tail service today has two choices: wait for a curated catalog (Composio, Zapier, similar) to admit it, or build a bespoke integration. UACP rejects this trade-off. Its commitment is universal-by-design: any service describable through standard authentication and standard HTTPS dispatch is reachable, and the description itself is producible from natural language by an AI agent and validated against a JSON schema.

The design principles that follow from this commitment — layered architecture, AI-native authoring, pluggable authentication and dispatch, public artifacts with private secrets, deterministic behavior — are documented in [`docs/01-principles.md`](./docs/01-principles.md).

## Governance

UACP is a personal project maintained by Alexander Walton. `v1.x` evolves under the stewardship of its maintainer; `v2` and beyond will follow a public RFC process if and when the protocol's user base justifies one. Outside contributors are welcome — see [`CONTRIBUTING.md`](./CONTRIBUTING.md) — and [`GOVERNANCE.md`](./GOVERNANCE.md) records the current stewardship and trademark posture.

## License

UACP specification documents are licensed under the Apache License 2.0. See [`LICENSE`](./LICENSE).
