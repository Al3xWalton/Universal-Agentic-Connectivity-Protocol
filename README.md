# UACP — Universal Agentic Connectivity Protocol

UACP is a wire format and runtime contract for describing how an AI agent authenticates to and dispatches operations against an external service.

UACP is a peer to the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). Where MCP standardizes the agent-to-tool surface — how an LLM application calls a tool — UACP standardizes the agent-to-external-service surface — how the tool, once called, reaches the external service it connects to. The two protocols compose: UACP-defined connections can be exposed through MCP servers as tools, and an MCP-aware agent does not need UACP-specific code to consume one.

## Status

This repository contains specification documents only. UACP is under active development at version `v0.1`, on a path toward a `v1.0` freeze. `v0.x` is unstable and subject to revision without backward-compatibility guarantees. The rules governing later changes are specified in [Stage 7 — Versioning](./docs/07-versioning.md) (forthcoming).

The reference implementation lives in a separate repository (the AVA monorepo, at `backend/services/connections-broker/`). This repository contains no implementation code.

## Read the specification

Start with the [Stage 0 Primer](./docs/00-primer.md) for terminology, scope, prior-art comparison, and document conventions, then read the [Stage 1 Principles](./docs/01-principles.md) for the foundational design constraints.

The [`SPEC.md`](./SPEC.md) document indexes the full specification and tracks the status of each stage.

## Why UACP?

A user who wants their agent to act against a long-tail service today has two choices: wait for a curated catalog (Composio, Zapier, similar) to admit it, or build a bespoke integration. UACP rejects this trade-off. Its commitment is universal-by-design: any service describable through standard authentication and standard HTTPS dispatch is reachable, and the description itself is producible from natural language by an AI agent and validated against a JSON schema.

The design principles that follow from this commitment — layered architecture, AI-native authoring, pluggable authentication and dispatch, public artifacts with private secrets, deterministic behavior — are documented in [`docs/01-principles.md`](./docs/01-principles.md).

## Governance

UACP `v1.x` evolves under the stewardship of the protocol's authoring organization. `v2` and beyond evolve through a public RFC process. Outside contributors are welcome — see [`CONTRIBUTING.md`](./CONTRIBUTING.md) — and the [`GOVERNANCE.md`](./GOVERNANCE.md) document records the current stewardship and trademark posture.

## License

UACP specification documents are licensed under the Apache License 2.0. See [`LICENSE`](./LICENSE).
