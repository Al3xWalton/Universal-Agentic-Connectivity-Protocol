# UACP Architectural Diagrams

This directory holds visual references that complement the prose in [`SPEC.md`](../../SPEC.md) and the eight stage documents under [`docs/`](../). Each diagram is a short markdown file with a one-paragraph context plus a Mermaid block; GitHub renders Mermaid natively, so the files are visual without any build step.

The diagrams are explanatory, not normative. Where any diagram conflicts with the spec, the spec wins.

| Diagram | Purpose |
|---|---|
| [01 — Layered architecture](./01-layered-architecture.md) | The five-layer stack: Authentication / Schema / Dispatch / Lifecycle / Security. |
| [02 — Schema source convergence](./02-schema-source-convergence.md) | Four schema sources (OpenAPI, curl-paste, LLM inference, session capture) converging on the canonical operation form. |
| [03 — MCP composition](./03-mcp-composition.md) | The agent → MCP → UACP server → external service flow. |
| [04 — Capture pipeline](./04-capture-pipeline.md) | The §3.12 browser-instrumented capture-to-`.uacp` pipeline end-to-end. |
