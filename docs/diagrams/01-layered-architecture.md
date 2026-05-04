# UACP — Layered architecture

UACP organizes its specification into five subsystems that compose top-down at every dispatch. The Authentication layer (Stage 2) resolves credentials through `secret://` references. The Schema layer (Stage 3) describes operations and validates them against the JSON Schema profile. The Dispatch layer (Stage 4) carries the validated request over HTTPS, applies retry / pagination / rate-limit handling, and normalizes errors. The Lifecycle layer (Stage 5) sits alongside, owning Connection state and refresh-token rotation. The Security layer (Stage 6) cross-cuts every other layer with the secret-store registry, encryption-at-rest, scope enforcement, and audit logging.

```mermaid
flowchart TB
    subgraph Caller["Caller: AI agent (often via MCP)"]
        A[Agent invocation]
    end

    subgraph UACP["UACP runtime"]
        direction TB
        L1["Authentication (Stage 2)\n10 registered methods\nsecret:// resolution"]
        L2["Schema (Stage 3)\n.uacp artifact\nJSON Schema profile\n4 schema sources"]
        L3["Dispatch (Stage 4)\nHTTPS transport\nretry / pagination / rate limits\nerror normalization"]
        L4["Lifecycle (Stage 5)\nConnection state machine\nrefresh + rotation + revocation"]
        L5["Security (Stage 6)\nsecret stores\nencryption-at-rest\nscope + audit"]

        L1 --> L2 --> L3
        L4 -.->|state| L1
        L4 -.->|state| L3
        L5 -.->|cross-cuts| L1
        L5 -.->|cross-cuts| L2
        L5 -.->|cross-cuts| L3
        L5 -.->|cross-cuts| L4
    end

    subgraph External["External Provider"]
        P[HTTPS service]
    end

    A --> L1
    L3 --> P
    P --> L3
    L3 --> A
```

Versioning (Stage 7) governs how the four other subsystems evolve over time without breaking conformance: every `v1.x` release is non-breaking per §7.2, and `v2` follows the public RFC process per §7.6.
