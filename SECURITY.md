# Security Policy

UACP takes security seriously. This document describes how to report a vulnerability in the UACP specification or the reference implementation, what is and is not in scope, and how disclosure is coordinated.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.** Public issues are appropriate for wording concerns that would lead an implementer to build something insecure if read literally; those are documentation defects, not vulnerabilities.

For an actual vulnerability — a flaw in an `Authentication Method`'s specified shape that compromises the security guarantee the method is intended to provide, a parser- or dispatch-time defect in the reference prototype that yields code execution, secret exfiltration, or credential leakage, or a comparable issue — email **TBD — operator-action item: replace with a real contact address (e.g., `security@<your-domain>`) before public release**.

You should expect an acknowledgement within 48 hours. Severe vulnerabilities will be triaged on a shorter cadence; lower-severity issues may take longer but will not be ignored.

When reporting, please include:

- A description of the issue and its impact.
- A minimal reproduction (an artifact, a code snippet, or a sequence of steps).
- The affected component (specification stage and section, or prototype module path).
- Whether you have already disclosed the issue elsewhere, and to whom.

## Scope

In scope:

- The UACP `v1.x` specification — the eight stage documents under [`docs/`](./docs/), the JSON Schema artifact at [`schemas/uacp.json`](./schemas/uacp.json), and any normative content in [`SPEC.md`](./SPEC.md).
- The Python reference implementation at [`prototype/python/`](./prototype/python/).
- The repository's own configuration (workflows, hooks, distribution artifacts) where it touches credential handling or supply-chain integrity.

Out of scope:

- **Third-party implementations of UACP.** Other implementations have their own security policies; report there.
- **The underlying providers** (Google, AWS, GitHub, Slack, NotebookLM, etc.). Vulnerabilities in those services should be reported to the providers directly through their own programs.
- **Operator misconfiguration.** Choosing a weak secret store, leaking credentials through environment variables, or accepting `tos_acknowledged: true` without understanding the §2.10 implications is the operator's responsibility, not a protocol vulnerability — though documentation defects that make such misconfigurations *likely* are in scope as wording concerns and can be filed as ordinary issues.

## Supported versions

| Version | Supported |
|---|---|
| `v1.1.x` | Yes — security updates land as patch releases. |
| `v1.0.x` | Yes — security updates land as patch releases for the rest of `v1.x`. |
| pre-`v1.0` | No — never released publicly. |

`v1.x` will continue to receive security updates for the lifetime of `v1`. When `v2` lands, support for `v1.x` will be reassessed and a deprecation window announced through a pinned issue at least six months before any `v1.x` series is dropped.

## Disclosure policy

UACP follows coordinated disclosure with a reasonable time window scaled to severity:

- **Critical** (active exploitation, secret exfiltration, RCE in the reference prototype, auth-method specification flaw that voids a security guarantee): aim for 30 days from acknowledgement to public disclosure, with a fix or compensating guidance landed before disclosure.
- **High** (privilege escalation, integrity compromise without active exploitation, credential leakage paths that require unusual configurations): 60 days.
- **Medium / low** (defense-in-depth issues, documentation-induced misconfigurations, hardening opportunities): 90 days.

If a fix is not feasible within the window — for example because it requires a `v2` major bump per [§7.6](./docs/07-versioning.md) — the maintainer and reporter coordinate on an appropriate alternative: compensating documentation guidance, a deprecation plan for the affected identifier, or a longer embargo with reporter consent.

## Acknowledgement

Reporters who follow this policy will be credited in the release notes for the fix unless they prefer to remain anonymous. UACP does not currently offer monetary bounties.
