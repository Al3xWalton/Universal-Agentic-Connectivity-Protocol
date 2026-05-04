# UACP — Capture pipeline (§3.12)

§3.12 establishes a fourth schema source: a browser-demonstrated session that becomes a draft `.uacp` file. The pipeline is end-to-end demonstrable through the reference prototype: a user opens the target service in an instrumented browser, performs the actions they want their agent to learn, and the recorder captures every HTTP request as a HAR-1.2-format entry plus the post-login `storage_state` cookies. The deterministic analyzer clusters the captured requests into candidate operations; an LLM synthesizes those candidates into UACP operation drafts; the user reviews and approves each operation before it persists; the validator enforces the §3.12 + §3.8 mandatory-user-review rule below the CLI so the gate cannot be bypassed.

```mermaid
flowchart LR
    User([User]) -->|opens browser\n+ demonstrates session| Recorder
    Recorder["Browser recorder\nPlaywright / Scrapling\nHAR + storage_state"] -->|encrypts at rest\n§6.3 envelope encryption| Storage[("Encrypted capture\n~/.uacp/secrets/<id>.enc\nAES-256-GCM, 0600")]
    Storage --> Analyzer
    Analyzer["Deterministic analyzer\nclustering by (method, path-signature)\nparameter inference\nauth-artifact extraction"] --> Synth
    Synth["LLM synthesis\n(LLMCallable Protocol)\nhallucination filtering\nrefinement loop (max 3)"] --> Draft[Draft .uacp operations\nsource.type=capture\nreviewed_at=null]
    Draft --> Review{User review\n§3.12 + §3.8 gate}
    Review -->|approve| Persist
    Review -->|edit| Editor["$EDITOR tempfile\nreviewed_at cleared"]
    Editor --> Review
    Review -->|refine| Synth
    Review -->|abort| Discard([discarded])
    Persist["confirm_and_persist()\nstamps reviewed_at\nemits §6.6 audit event"] --> File[(.uacp file\nwith provenance\nfully validated)]
    File --> Validator["uacp validate\n_validate_capture_provenance\nrejects missing reviewed_at"]
```

The pipeline preserves UACP's load-bearing properties throughout: captured artifacts are encrypted at rest with no plaintext fallback (§6.3); audit events scrub auth values (§6.6); the LLM never sees raw HAR — only the analyzer's structured summary; operations the LLM hallucinates beyond the candidate-cluster set are dropped mechanically rather than relying on prompt obedience; `source.type=capture` artifacts cannot be persisted without `reviewed_at`, enforced at the spec-loader level so the gate holds even if a future CLI bypasses the prompt.
