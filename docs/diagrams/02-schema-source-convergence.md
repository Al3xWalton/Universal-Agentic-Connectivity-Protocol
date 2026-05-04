# UACP — Schema source convergence

UACP accepts four schema sources, all of which converge on the same canonical `.uacp` operation form before persistence. OpenAPI ingestion (§3.6) lifts operations from a published spec; curl-paste (§3.7) captures a single request from a `curl` invocation and infers the operation around it; LLM inference (§3.8) produces a draft from a natural-language description and gates it on explicit user review; session capture (§3.12, added in v1.1) records a browser-demonstrated session, clusters the captured requests deterministically, and uses an LLM to draft operations under the same mandatory user-review gate.

This is the visual for "universal-by-design": the spec doesn't care which source produced the artifact, only that the canonical form validates.

```mermaid
flowchart LR
    S1[OpenAPI document\n§3.6] --> N
    S2[curl invocation\n§3.7] --> N
    S3[Natural-language description\n§3.8 LLM inference] --> R{User review gate\n§3.8 / §3.12}
    S4[Browser-demonstrated session\n§3.12 capture] --> A[Deterministic analyzer\nclustering + parameter inference]
    A --> L[LLM synthesis\nhallucination filtering]
    L --> R
    R -->|approved| N
    R -->|rejected / refined| L

    N["Canonical .uacp operation\n(JSON Schema-validated\nagainst schemas/uacp.json)"]
    N --> P[(Persistent storage\nsecret-refs only,\nno plaintext credentials)]
    N --> D[Dispatch runtime\n§4]
    N --> M[MCP server adapter\n→ MCP tool surface]
```

Source provenance (§3.5) is preserved on every operation: `source.type` records which path produced it, and the `inferred` and `capture` paths additionally carry `model`, `description` / `user_intent`, and `reviewed_at` fields enforced at validation time.
