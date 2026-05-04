"""Deterministic analysis layer that runs before the §3.12 LLM synthesis.

Takes a :class:`CaptureArtifact` (loaded via
:func:`uacp_prototype.capture.storage.load_capture`) and produces a
structured intermediate representation the LLM can reason about
cleanly. The LLM never sees raw HAR; it sees pre-clustered candidate
operations with parameter-frequency tables, observed shapes, and noise
already filtered out. Two consequences:

  - Synthesis quality is bounded above by clustering quality. The
    deterministic step does the heavy lifting; the LLM's job is to
    name + describe + classify.
  - The brief's hard rule "the clustering algorithm is deterministic;
    don't sneak LLM calls into it" is mechanical: nothing in this
    module imports from ``connections/ingest_*`` or makes a network
    call.

The clustering rules per §3.12's "operation inference from captures":

1. **Method match.** Different HTTP methods → different operations.
2. **Path-signature match.** Replace variable-shaped path segments
   (UUIDs, integers, hex tokens, slugs-with-digits) with ``:var``;
   two paths with the same method + signature cluster together.
   Constant literal segments stay literal.
3. **Query parameters** become candidate operation parameters with
   frequency counts (REQUIRED if seen in 100% of cluster invocations,
   OPTIONAL otherwise).
4. **Body shape.** Same path + method + bodies sharing ≥80% of
   top-level keys → same operation, divergent keys are optional;
   <80% overlap → ambiguous (reported separately so the LLM /
   reviewer can disambiguate).

Path parameter naming uses three heuristics in priority order: (a)
the preceding literal segment (``/users/{user_id}``), (b) the
parameter value's shape (UUID → ``{uuid}``, int → ``{id}``,
slug → ``{slug}``), (c) a generic ``{paramN}`` fallback. Each
inferred name carries a ``confidence`` so the LLM knows where to
second-guess.

Noise filtering removes third-party-domain requests, image / font /
CSS / JS asset loads, favicons, manifests, and service-worker
registrations from the candidate-operation list. Filtered entries
still appear in :attr:`AnalysisResult.noise_requests` for diagnostic
purposes.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlparse

from .recorder import CaptureArtifact, HarEntry


# ---------------------------------------------------------------------------
# Variable-shape recognizers
# ---------------------------------------------------------------------------


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_INT_RE = re.compile(r"^-?\d+$")
# Long hex tokens (≥12 chars all-hex) — Slack channel ids, S3 keys, etc.
_HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{12,}$")
# Kebab-case-with-digits (looks like a slug, e.g. "post-12345-title").
_SLUG_WITH_DIGITS_RE = re.compile(r"^[a-zA-Z0-9-]+$")
# Email-shaped (e.g. some APIs pass an email in the path).
_EMAIL_RE = re.compile(r"^[^@/\s]+@[^@/\s]+\.[^@/\s]+$")


@dataclass(frozen=True)
class PathSegment:
    """One path segment from a clustered operation, with its inferred
    role: literal (``role="literal"``) or variable (``role="var"``).
    Variable segments carry the name + shape the analyzer inferred and
    a confidence in [0, 1]. The LLM consumes this directly; the named
    parameter goes into the operation's path_parameters schema.
    """

    role: str  # "literal" | "var"
    value: str  # literal text or the inferred parameter name (e.g. "user_id")
    shape: str = ""  # "uuid" | "integer" | "slug" | "hex" | "email" | "unknown"
    confidence: float = 0.0  # 0.0–1.0; higher = more confident in shape + name


def _classify_segment(value: str) -> str:
    """Return the variable shape for ``value`` or empty string when the
    segment is a literal.

    Recognizes the common path-id shapes; everything else is treated
    as a literal and constrains the cluster's identity.
    """
    if not value:
        return ""
    if _UUID_RE.match(value):
        return "uuid"
    if _INT_RE.match(value):
        return "integer"
    if _EMAIL_RE.match(value):
        return "email"
    if _HEX_TOKEN_RE.match(value):
        return "hex"
    # Slug-shaped only when it carries digits (otherwise risk treating
    # `users` as a slug). Pure-alpha tokens always stay literal.
    if (
        _SLUG_WITH_DIGITS_RE.match(value)
        and any(c.isdigit() for c in value)
        and len(value) >= 4
    ):
        return "slug"
    return ""


def _path_signature(segments: list[str]) -> tuple[str, ...]:
    """Reduce a path's segment list to a clustering signature.

    Variable segments collapse to ``:var``; literals stay verbatim.
    Two paths with equal signatures (and equal HTTP method) cluster.
    """
    return tuple(":var" if _classify_segment(s) else s for s in segments)


def _split_path(url: str) -> tuple[str, list[str]]:
    """Return ``(host, segments)`` for an absolute URL."""
    parsed = urlparse(url)
    host = parsed.netloc
    raw = parsed.path
    if not raw or raw == "/":
        return host, []
    return host, [s for s in raw.split("/") if s]


# ---------------------------------------------------------------------------
# Noise filtering
# ---------------------------------------------------------------------------


_NOISE_CONTENT_TYPE_PREFIXES = (
    "image/",
    "font/",
    "text/css",
    "audio/",
    "video/",
    "application/javascript",
    "application/x-javascript",
    "text/javascript",
)
_NOISE_PATH_HINTS = (
    "/favicon.ico",
    "/manifest.json",
    "/manifest.webmanifest",
    "/sw.js",
    "/service-worker.js",
    "/robots.txt",
)


def _is_noise(entry: HarEntry, primary_host: str) -> tuple[bool, str]:
    """Return ``(is_noise, reason)`` for a captured entry against the
    primary host. The primary host is the one that produced the
    plurality of requests in the capture; everything off-host is
    treated as a third-party asset load and dropped from candidate
    operations (it can still surface in noise_requests for diagnosis).
    """
    method = entry.request.get("method", "GET").upper()
    url = entry.request.get("url", "")
    parsed = urlparse(url)

    if primary_host and parsed.netloc and parsed.netloc != primary_host:
        return True, "third_party_host"

    # Static-asset content-type filter (only matches when the response
    # explicitly declares one of the noise prefixes).
    content_type = ""
    headers = entry.response.get("headers") or {}
    for k, v in headers.items():
        if k.lower() == "content-type":
            content_type = v.split(";")[0].strip().lower()
            break
    if content_type:
        for prefix in _NOISE_CONTENT_TYPE_PREFIXES:
            if content_type.startswith(prefix):
                return True, f"asset_content_type:{content_type}"

    path = parsed.path or ""
    for hint in _NOISE_PATH_HINTS:
        if path.endswith(hint):
            return True, f"asset_path:{hint}"

    # OPTIONS preflights are noise from a synthesis perspective —
    # they're CORS preludes, not user-demonstrated operations.
    if method == "OPTIONS":
        return True, "cors_preflight"

    return False, ""


# ---------------------------------------------------------------------------
# Frequency tables for parameters / headers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParameterFrequency:
    """How often a candidate parameter appeared across a cluster's
    invocations. The LLM uses this directly to mark required vs
    optional in the synthesized operation.
    """

    name: str
    seen: int  # how many cluster invocations carried this parameter
    total: int  # cluster size
    sample_values: tuple[str, ...] = ()

    @property
    def is_required(self) -> bool:
        """REQUIRED iff the parameter appears in every invocation."""
        return self.total > 0 and self.seen == self.total

    @property
    def confidence(self) -> float:
        """Confidence the parameter is required, derived from
        frequency. 5/5 → 1.0, 1/5 → 0.2."""
        if self.total == 0:
            return 0.0
        return self.seen / self.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "seen": self.seen,
            "total": self.total,
            "is_required": self.is_required,
            "confidence": round(self.confidence, 3),
            "sample_values": list(self.sample_values),
        }


@dataclass
class CandidateOperation:
    """One pre-clustered operation candidate the LLM will name +
    summarize. Carries everything the LLM needs to reason about a
    single observed endpoint shape.
    """

    method: str
    host: str
    path_segments: list[PathSegment]
    invocations: list[HarEntry]
    query_parameters: list[ParameterFrequency] = field(default_factory=list)
    request_headers: list[ParameterFrequency] = field(default_factory=list)
    body_keys: list[ParameterFrequency] = field(default_factory=list)
    response_status_codes: list[int] = field(default_factory=list)
    response_content_types: list[str] = field(default_factory=list)
    body_shape_ambiguous: bool = False

    @property
    def path_template(self) -> str:
        """Render the cluster as a UACP-style path template:
        ``/users/{user_id}/posts/{post_id}``. Literal segments stay
        verbatim; variable segments use the inferred name."""
        parts = ["/" + (seg.value if seg.role == "literal" else "{" + seg.value + "}") for seg in self.path_segments]
        return "".join(parts) if parts else "/"

    @property
    def path_parameters(self) -> list[PathSegment]:
        return [s for s in self.path_segments if s.role == "var"]

    def to_summary(self) -> dict[str, Any]:
        """A compact summary for the LLM prompt — every field the LLM
        needs, nothing the LLM doesn't."""
        return {
            "method": self.method,
            "host": self.host,
            "path_template": self.path_template,
            "invocation_count": len(self.invocations),
            "path_parameters": [
                {
                    "name": seg.value,
                    "shape": seg.shape or "unknown",
                    "confidence": round(seg.confidence, 3),
                }
                for seg in self.path_parameters
            ],
            "query_parameters": [p.to_dict() for p in self.query_parameters],
            "request_headers": [
                p.to_dict() for p in self.request_headers if p.name.lower() not in _AUTH_HEADERS
            ],
            "body_keys": [p.to_dict() for p in self.body_keys],
            "response_status_codes": sorted(set(self.response_status_codes)),
            "response_content_types": sorted(set(self.response_content_types)),
            "body_shape_ambiguous": self.body_shape_ambiguous,
        }


_AUTH_HEADERS = frozenset(
    h.lower()
    for h in (
        "Authorization",
        "Cookie",
        "Set-Cookie",
        "Proxy-Authorization",
        "X-Auth-Token",
        "X-API-Key",
        "X-Csrf-Token",
        "X-XSRF-TOKEN",
    )
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class AnalysisResult:
    """Output of :func:`analyze_capture`.

    - ``candidate_operations``: clustered request groups, one per
      inferred operation. The LLM operates exclusively on this list.
    - ``noise_requests``: captured entries the analyzer dropped
      (third-party hosts, asset loads, OPTIONS preflights). Surfaced
      for diagnostic purposes only.
    - ``auth_artifacts``: cookies, CSRF tokens, and Authorization
      header patterns extracted from the captures — input to the
      §2.10 session_cookie connection block downstream.
    - ``domain_summary``: request count per host. Identifies the
      primary service host vs third-party calls.
    - ``primary_host``: the host with the most requests; used as the
      cluster filter and as the future ``dispatch.base_url``.
    """

    candidate_operations: list[CandidateOperation]
    noise_requests: list[HarEntry]
    auth_artifacts: dict[str, Any]
    domain_summary: dict[str, int]
    primary_host: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "primary_host": self.primary_host,
            "domain_summary": dict(self.domain_summary),
            "candidate_operations": [c.to_summary() for c in self.candidate_operations],
            "noise_request_count": len(self.noise_requests),
            "auth_artifacts": dict(self.auth_artifacts),
        }


# ---------------------------------------------------------------------------
# Path-parameter name inference
# ---------------------------------------------------------------------------


def _singularize(token: str) -> str:
    """Cheap English singularizer for the preceding-segment heuristic.

    The full plural→singular surface is well outside the scope here;
    this covers the patterns common in REST URLs (``users`` → ``user``,
    ``categories`` → ``category``, ``boxes`` → ``box``). Anything
    irregular falls through unchanged — the LLM may rename in
    refinement.
    """
    if len(token) < 4:
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith("ses") or token.endswith("xes") or token.endswith("zes"):
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _name_from_preceding(preceding_literal: str | None) -> tuple[str, float]:
    """Derive a parameter name from the literal segment preceding the
    variable segment. Returns ``(name, confidence)``."""
    if not preceding_literal:
        return "", 0.0
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", preceding_literal).strip("_")
    if not safe:
        return "", 0.0
    singular = _singularize(safe)
    return f"{singular}_id", 0.85


def _name_from_shape(shape: str, position: int) -> tuple[str, float]:
    """Derive a parameter name from the variable's shape when no
    preceding-segment hint is available. Lower confidence than the
    preceding-segment heuristic — the LLM is more likely to rename."""
    if shape == "uuid":
        return f"uuid_{position}", 0.55
    if shape == "integer":
        return f"id_{position}", 0.55
    if shape == "slug":
        return f"slug_{position}", 0.55
    if shape == "email":
        return f"email_{position}", 0.65
    if shape == "hex":
        return f"hex_{position}", 0.50
    return f"param_{position}", 0.30


def _build_path_segments(
    cluster_paths: list[list[str]],
) -> list[PathSegment]:
    """For a cluster of paths with the same signature, return the
    PathSegment list (one per position) with inferred names + shapes
    + confidences for variable positions.

    Naming priority per the brief: (a) preceding literal segment,
    (b) variable shape, (c) generic positional fallback.
    """
    if not cluster_paths:
        return []
    segment_count = len(cluster_paths[0])
    segments: list[PathSegment] = []
    used_names: set[str] = set()

    for i in range(segment_count):
        column = [p[i] for p in cluster_paths]
        # A column is variable when ANY value in the column has a
        # recognized variable shape — matches the clustering
        # signature in :func:`_path_signature`. The legacy "must
        # vary across invocations" rule under-classified single-
        # invocation clusters whose path segment was clearly an id.
        is_var_column = any(_classify_segment(v) for v in column)
        if not is_var_column:
            segments.append(PathSegment(role="literal", value=column[0]))
            continue

        # Variable column. Pick the most common shape.
        shape_counts = Counter(_classify_segment(v) for v in column)
        # Drop the empty-string bucket (literals mixed in are unusual
        # but possible for permissive clusters).
        shape_counts.pop("", None)
        shape = shape_counts.most_common(1)[0][0] if shape_counts else "unknown"

        preceding = column_value_or_none(segments)
        name, confidence = _name_from_preceding(preceding)
        if not name:
            name, confidence = _name_from_shape(shape, i)

        # Avoid collision with names already used at earlier positions.
        base = name
        suffix = 1
        while name in used_names:
            suffix += 1
            name = f"{base}{suffix}"
        used_names.add(name)
        segments.append(
            PathSegment(role="var", value=name, shape=shape, confidence=confidence)
        )
    return segments


def column_value_or_none(prior_segments: list[PathSegment]) -> str | None:
    """The most recent literal segment to the left, used as the
    preceding-segment hint. Returns None when the leftmost positions
    are all variable."""
    for seg in reversed(prior_segments):
        if seg.role == "literal":
            return seg.value
    return None


# ---------------------------------------------------------------------------
# Body / parameter accumulation
# ---------------------------------------------------------------------------


_BODY_OVERLAP_THRESHOLD = 0.80


def _extract_query_params(url: str) -> dict[str, str]:
    """Extract query params as a flat dict (last-value-wins for repeated keys)."""
    parsed = urlparse(url)
    return dict(parse_qsl(parsed.query, keep_blank_values=True))


def _body_top_level_keys(body: Any) -> list[str]:
    """Return top-level keys of a JSON body, or [] for non-dict bodies.

    URL-encoded form bodies are parsed via parse_qsl. Bodies that
    aren't JSON-or-form return an empty list, which excludes them
    from the body-key frequency analysis (the LLM still sees the
    raw shape in the per-invocation summary)."""
    if body is None:
        return []
    if isinstance(body, dict):
        return list(body.keys())
    if not isinstance(body, str):
        return []
    text = body.strip()
    if not text:
        return []
    if text.startswith("{") or text.startswith("["):
        import json as _json

        try:
            parsed = _json.loads(text)
        except ValueError:
            return []
        if isinstance(parsed, dict):
            return list(parsed.keys())
        return []
    if "=" in text and "&" in text or "=" in text:
        # URL-encoded form body; parse_qsl tolerates malformed input.
        try:
            return [k for k, _ in parse_qsl(text, keep_blank_values=True)]
        except ValueError:
            return []
    return []


def _build_frequency(
    name_to_seen: dict[str, set[int]],
    name_to_samples: dict[str, list[str]],
    total: int,
) -> list[ParameterFrequency]:
    """Convert the accumulated counters into ParameterFrequency
    entries. Sample values are deduplicated and capped at 3 per
    parameter to keep the prompt small."""
    out: list[ParameterFrequency] = []
    for name in sorted(name_to_seen):
        seen = len(name_to_seen[name])
        samples_unique: list[str] = []
        for v in name_to_samples.get(name, []):
            if v not in samples_unique:
                samples_unique.append(v)
            if len(samples_unique) >= 3:
                break
        out.append(
            ParameterFrequency(
                name=name, seen=seen, total=total, sample_values=tuple(samples_unique)
            )
        )
    return out


def _pearson_overlap(a: set[str], b: set[str]) -> float:
    """Symmetric Jaccard-shaped overlap between two key sets. The
    brief calls out an 80% threshold for "same operation with
    optional fields"; the implementation uses ``|a ∩ b| / |a ∪ b|``,
    which is conservative — bodies with one shared key out of five
    score 0.20 (correctly disambiguated as different shapes)."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def analyze_capture(artifact: CaptureArtifact) -> AnalysisResult:
    """Cluster + summarize the captured artifact per §3.12.

    Returns a deterministic intermediate representation suitable for
    LLM synthesis. The function is pure: same artifact in → same
    AnalysisResult out (modulo Counter ordering, which the
    implementation flattens deterministically).
    """
    domain_summary: Counter[str] = Counter()
    for entry in artifact.entries:
        host = urlparse(entry.request.get("url", "")).netloc
        if host:
            domain_summary[host] += 1
    primary_host = domain_summary.most_common(1)[0][0] if domain_summary else ""

    auth_artifacts = _extract_auth_artifacts(artifact)

    # Partition captured entries into "candidate" vs "noise".
    candidates: list[HarEntry] = []
    noise: list[HarEntry] = []
    for entry in artifact.entries:
        is_noise, _reason = _is_noise(entry, primary_host)
        if is_noise:
            noise.append(entry)
        else:
            candidates.append(entry)

    # Group by (method, path-signature) for clustering.
    grouped: dict[tuple[str, tuple[str, ...]], list[HarEntry]] = {}
    for entry in candidates:
        method = entry.request.get("method", "GET").upper()
        _host, segs = _split_path(entry.request.get("url", ""))
        sig = _path_signature(segs)
        grouped.setdefault((method, sig), []).append(entry)

    candidate_operations: list[CandidateOperation] = []

    for (method, sig), invocations in grouped.items():
        # Collect the per-invocation segment list so the path-
        # parameter names can be inferred from variable values.
        per_inv_segments: list[list[str]] = []
        for inv in invocations:
            _host, segs = _split_path(inv.request.get("url", ""))
            # Pad to signature length (defensively — should always match).
            if len(segs) != len(sig):
                continue
            per_inv_segments.append(segs)
        if not per_inv_segments:
            continue

        path_segments = _build_path_segments(per_inv_segments)

        # Frequency tables for query params + selected request
        # headers + JSON/form body keys.
        query_seen: dict[str, set[int]] = {}
        query_samples: dict[str, list[str]] = {}
        header_seen: dict[str, set[int]] = {}
        header_samples: dict[str, list[str]] = {}
        body_seen: dict[str, set[int]] = {}
        body_keys_per_invocation: list[set[str]] = []
        statuses: list[int] = []
        content_types: list[str] = []

        for idx, inv in enumerate(invocations):
            url = inv.request.get("url", "")
            qp = _extract_query_params(url)
            for k, v in qp.items():
                query_seen.setdefault(k, set()).add(idx)
                query_samples.setdefault(k, []).append(v)

            req_headers = inv.request.get("headers") or {}
            for hk in req_headers:
                if hk.lower() in _AUTH_HEADERS:
                    continue
                header_seen.setdefault(hk, set()).add(idx)
                # Headers' sample values are kept truncated to avoid
                # leaking long bearer tokens via *_AUTH_HEADERS-adjacent
                # custom headers; truncated at 64 chars.
                val = req_headers[hk]
                header_samples.setdefault(hk, []).append(
                    val if len(val) <= 64 else val[:61] + "..."
                )

            body_keys = _body_top_level_keys(inv.request.get("body"))
            body_keys_per_invocation.append(set(body_keys))
            for bk in body_keys:
                body_seen.setdefault(bk, set()).add(idx)

            statuses.append(int(inv.response.get("status", 0) or 0))
            ct = ""
            for k, v in (inv.response.get("headers") or {}).items():
                if k.lower() == "content-type":
                    ct = v.split(";")[0].strip()
                    break
            if ct:
                content_types.append(ct)

        ambiguous = _is_body_shape_ambiguous(body_keys_per_invocation)

        candidate_operations.append(
            CandidateOperation(
                method=method,
                host=primary_host,
                path_segments=path_segments,
                invocations=list(invocations),
                query_parameters=_build_frequency(
                    query_seen, query_samples, total=len(invocations)
                ),
                request_headers=_build_frequency(
                    header_seen, header_samples, total=len(invocations)
                ),
                body_keys=_build_frequency(body_seen, {}, total=len(invocations)),
                response_status_codes=statuses,
                response_content_types=content_types,
                body_shape_ambiguous=ambiguous,
            )
        )

    # Stable ordering: sort candidate operations by (method, path_template)
    # so AnalysisResult round-trips deterministically across runs.
    candidate_operations.sort(key=lambda c: (c.method, c.path_template))

    return AnalysisResult(
        candidate_operations=candidate_operations,
        noise_requests=noise,
        auth_artifacts=auth_artifacts,
        domain_summary=dict(domain_summary),
        primary_host=primary_host,
    )


def _is_body_shape_ambiguous(per_invocation: list[set[str]]) -> bool:
    """True when at least one pair of invocations shares <80% of body
    keys (Jaccard). Triggers the §3.12-flagged disambiguation path:
    the LLM (or the user) is told the cluster MAY actually be two
    different operations sharing an endpoint."""
    if len(per_invocation) < 2:
        return False
    populated = [s for s in per_invocation if s]
    if len(populated) < 2:
        return False
    for i in range(len(populated)):
        for j in range(i + 1, len(populated)):
            if _pearson_overlap(populated[i], populated[j]) < _BODY_OVERLAP_THRESHOLD:
                return True
    return False


def _extract_auth_artifacts(artifact: CaptureArtifact) -> dict[str, Any]:
    """Pull cookies + CSRF + Authorization header patterns out of the
    captured traffic for downstream cross-reference with §2.10's
    session_cookie auth block. NEVER includes raw token values; only
    the field names + counts."""
    cookie_names: set[str] = set()
    auth_header_count = 0
    csrf_header_count = 0

    storage = artifact.storage_state or {}
    for c in storage.get("cookies", []) or []:
        if isinstance(c, dict) and c.get("name"):
            cookie_names.add(str(c["name"]))

    for entry in artifact.entries:
        for k, _v in (entry.request.get("headers") or {}).items():
            kl = k.lower()
            if kl == "authorization":
                auth_header_count += 1
            elif kl in {"x-csrf-token", "x-xsrf-token", "x-same-domain"}:
                csrf_header_count += 1

    return {
        "cookie_names": sorted(cookie_names),
        "authorization_header_seen": auth_header_count,
        "csrf_header_seen": csrf_header_count,
    }


__all__ = [
    "AnalysisResult",
    "CandidateOperation",
    "ParameterFrequency",
    "PathSegment",
    "analyze_capture",
]
