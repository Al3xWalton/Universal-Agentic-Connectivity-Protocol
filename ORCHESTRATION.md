# ORCHESTRATION.md — Routing policy for UACP work between Claude Code and Codex

**Purpose**: This document defines how Claude (acting as the orchestrator) routes work between Claude Code and Codex on the UACP repository. UACP's scope is bounded — a spec under `docs/` plus a Python reference implementation under `prototype/python/` — so the routing policy is correspondingly tight.

The substance of the framework is identical to AVA's `ORCHESTRATION.md`: Claude Code handles ambiguity / architecture / safety-sensitive design; Codex handles bounded delivery / parallel execution / repeatable workflows. The UACP-specific framing below documents the deviations and the per-stage routing defaults.

This document is the authoritative policy for UACP work. `CLAUDE.md` and `AGENTS.md` reference it. When the policy is updated, this file is the one to update first.

---

## Core decision principle

Route work based on **task uncertainty first**, then by **execution requirements**, then by **cost and throughput**.

If the task is ambiguous, cross-cutting, or likely to evolve while being worked, default to Claude Code because Plan Mode exists specifically for analysis before modification, and permissions can restrict what happens until a plan is approved.

If the task is well specified, can be evaluated by tests or acceptance criteria, and does not require tight human steering during every step, default to Codex because its skills and autonomous execution model are optimized for repeatable workflows and background completion.

---

## UACP-specific routing defaults

### Spec design sessions (Stages 0-7)

**Default to Claude Code.** Spec design is ambiguous, cross-cutting, and architecturally consequential. Each stage references prior stages; getting a section wrong propagates errors forward. Plan Mode is the right pattern: read the prior stages, propose the section structure, get confirmation, then write.

Stage 8 (per-provider prototype validation), Stage 9 (prototype freeze + conformance test suite), and Stage 10 (production reference impl in AVA) are different shapes — see below.

### Per-provider prototype sessions (Stage 8a-8e)

**Stage 8a (Google) ran in Claude Code** because it bootstrapped the prototype scaffold and exercised the full Stage 2-6 surface end-to-end against a real provider — many decisions were ambiguous and benefited from interactive iteration.

**Stages 8b-8e MAY route to Codex** when the spec and the scaffold are stable. The pattern: a session brief identifies one provider, names the auth method (e.g., `aws_sigv4` for 8c S3) and the source path (e.g., curl-paste for 8b Slack), points at the existing scaffold patterns, and lists the operations to add. The work is bounded; the acceptance criteria are unit-test pass count + the new `.uacp` file validating + the new operations dispatching against the mock surface.

In practice, Alexander may choose Claude Code for the per-provider sessions too — the work involves spec interpretation, and design questions surface when a provider's API doesn't fit cleanly. The default is "Claude Code unless the brief is very tight."

### Stage 9 — prototype freeze

**Default to Claude Code.** This is the moment the canonical `$schema` URL is pinned, the conformance test suite is extracted, and the `v1.0` backward-compatibility rules begin to bind. Every decision is consequential and many cross several stages.

### Stage 10 — production reference implementation in AVA

**Cross-repo session that operates on the AVA monorepo, not this repo.** The prototype migrates into AVA's `backend/services/connections-broker/` (a Ring 3 service per ADR-036). Routing is governed by AVA's own `ORCHESTRATION.md`; this repo's policy doesn't apply once the work moves out.

### Memory + meta sessions

**Default to Claude Code.** Memory sessions update `docs/memory/CURRENT.md` and append to `docs/memory/CHANGELOG.md`. They're short, intent-driven, and require reading the prior session's framing to maintain continuity. Codex is over-tooled for this.

---

## System roles

### Claude as orchestrator

"Claude as orchestrator" means any Claude window operating one level above the per-agent execution layer. In Alexander's setup that includes:

- **Cowork mode** (the Claude desktop app's agentic surface) — the highest-level orchestrator. When the user issues a task here, Cowork's job is to classify it and decide whether to (a) handle it inline with its own tools, (b) hand it to a Claude Code window with a Plan-Mode brief, (c) hand it to a Codex window with a bounded spec, or (d) coordinate a hybrid flow across multiple windows in parallel.
- **Top-level Claude Code windows** acting as supervisors over sub-agents. Same routing logic applies.

The orchestrator's job: classify the task, determine risk, decide whether exploration is needed, choose the execution engine, attach the right workflow instructions, and decide whether the work should happen interactively or asynchronously.

### Claude Code role

Claude Code is the primary agent for repository understanding, planning, interactive debugging, controlled edits, and high-context reasoning across a working directory. UACP's spec-design surface is a Claude Code surface — every stage is read-the-prior-stages-then-write.

### Codex role

Codex is the primary agent for autonomous execution, repeatable implementation flows, and skill-triggered jobs where the task can be framed as a bounded unit of work. For UACP that's per-provider prototype sessions once the scaffold and the spec are stable.

---

## Classification layer

Before selecting an agent, classify the task across six axes.

### 1. Ambiguity

Use Claude Code when the request is under-specified, such as "design Stage 9 freeze" or "the prototype's discovery-doc handling diverges from the spec — figure out which is right" — these require investigation before execution.

Use Codex when the request is specific, such as "add Slack `chat.postMessage` and `conversations.history` to the prototype following the Stage 8a Gmail patterns" — the work can be judged against explicit deliverables.

### 2. Scope breadth

Use Claude Code when the change spans multiple spec stages or the boundaries are unknown. Plan Mode is a strong default because it lets Claude analyze without modifying files.

Use Codex when the change is contained to a provider, an operation, or a module-sized unit of work with clear edges.

### 3. Risk

Use Claude Code when mistakes are expensive: spec changes that affect downstream stages; prototype changes that affect security (encryption, auth flows); changes to the `.uacp` validator; changes to the canonical error shape.

Use Codex only when those operations are already standardized behind a tested workflow with strong guardrails.

### 4. Need for human steering

Use Claude Code when Alexander wants to inspect steps, redirect the approach, or approve commits in real time.

Use Codex when Alexander wants to hand off a well-defined result and review the output later rather than supervise the process.

### 5. Repetition level

Use Codex when the same class of task repeats enough that the workflow can be encoded as a skill. The per-provider prototype sessions (8a-8e) are the obvious candidates — each follows the same shape (read spec, identify auth method, write `.uacp` example, write tests, validate).

Use Claude Code when the workflow is still being discovered.

### 6. Context density

Use Claude Code when the task depends on understanding the spec's architecture, the principles, and the current memory state.

Use Codex when the task can be fully described through a compact spec plus repo contents and acceptance criteria.

---

## Routing rules

### Default to Claude Code when any of the following is true

- The task starts with investigation rather than implementation.
- The request is vague, high-level, or architectural.
- The work crosses multiple spec stages or shared abstractions in the prototype.
- Alexander wants step-by-step collaboration, visibility, or approvals.
- The repo has local context that matters (uncommitted changes, recent memory entries, mid-session state).
- Safety controls are required before command execution.
- The task is a spec correction, refactor, performance investigation, or subtle bug hunt.

### Default to Codex when any of the following is true

- The task can be written as a bounded job with clear acceptance criteria.
- The workflow is repetitive and should become a reusable skill.
- The job can run independently and does not need continuous oversight.
- Parallel execution would help (multiple bounded provider sessions in flight).
- The main objective is throughput, consistency, or automation rather than collaborative exploration.

---

## Operational playbooks

### Playbook A: Spec design session

1. Start in Claude Code Plan Mode.
2. Read the prior stages in numerical order, then `docs/memory/CURRENT.md`, then any open questions in `docs/open-questions.md` that the new stage might resolve.
3. Propose the section structure (e.g., for Stage 4: §4.0 Overview, §4.1 Connection-level dispatch, ..., §4.9 Conformance summary). Get confirmation from Alexander.
4. Write the stage in one pass, one commit. Index update for `SPEC.md` and `docs/00-primer.md` in the same commit.
5. Memory commit at session end.

### Playbook B: Per-provider prototype session

1. Read the spec stages relevant to the provider's auth method and dispatch shape.
2. Read `prototype/python/README.md` and the existing module under `src/uacp_prototype/`.
3. Read the Stage 8a entry in `docs/memory/CHANGELOG.md` for the established pattern.
4. Author the `.uacp` file under `examples/<provider>/`.
5. Implement the missing auth method module (or extend existing dispatch shape).
6. Write unit tests that pass against mocks; write integration tests decorated `@pytest.mark.integration`.
7. Memory commit at session end.

This playbook is a candidate for Codex once Stage 8a's pattern is well documented; it's currently a Claude Code playbook because the pattern is still settling.

### Playbook C: Spec-fix session

1. Identify the bug (typo, broken cross-reference, internal inconsistency).
2. Confirm it's not a substantive design issue (substantive issues go to `docs/open-questions.md`).
3. Single commit: `fix(spec): <stage> — <description>`.

### Playbook D: Memory continuity

1. Update `docs/memory/CURRENT.md` — demote the previous "Last updated" line to a "Prior session" or new section, and write a new top-of-file summary.
2. Append a `## YYYY-MM-DD — <slug> — <area>` entry to `docs/memory/CHANGELOG.md`.
3. Single commit: `chore: log session — <slug>` or equivalent.

---

## Anti-patterns

- Do not use Codex as the first responder to a vague spec question.
- Do not use Claude Code for Codex-shaped repeatable jobs once the pattern is stable.
- Do not skip Plan Mode on cross-stage spec changes.
- Do not commit a spec change as a `feat(prototype):` commit or vice versa — the commit type is part of the audit log.
- Do not let multiple autonomous outputs merge without a memory commit summarizing what landed.

---

## Recommended hybrid strategy

The strongest general pattern for UACP is **Claude Code for design + memory, Codex for bounded prototype work, Claude Code for integration / spec-fix / freeze**.

For small and well-specified prototype tasks (one new operation against an already-supported auth method), skip Claude Code and send the work directly to Codex.

For spec design, freeze, and any work where the right answer isn't obvious from reading the brief, keep the whole workflow in Claude Code with Plan Mode.

---

## A compact decision tree

1. Is the task ambiguous, design-bearing, or cross-stage? **Claude Code first**.
2. Is the task a spec change? **Claude Code in Plan Mode**.
3. Is the task a bounded prototype task with the auth method, source path, and operations all named? **Codex eligible**.
4. Is the task a memory commit / spec-fix / open-question logging? **Claude Code; quick.**
5. Is the task crossing into AVA (Stage 10)? **Hand off to AVA's `ORCHESTRATION.md`.**

---

## Final policy

- Prefer **Claude Code** for spec design (Stages 0-7), prototype scaffolding (Stage 8a), spec corrections, memory commits, freeze decisions, and any cross-stage work.
- Prefer **Codex** for bounded per-provider prototype sessions once the scaffold is stable, parallel test-writing tasks, and any well-specified module that's a port of an established pattern.
- When both are useful, use **Claude Code to shape the work, Codex to execute the well-bounded parts, and Claude Code again to land the memory commit and confirm the result**.
