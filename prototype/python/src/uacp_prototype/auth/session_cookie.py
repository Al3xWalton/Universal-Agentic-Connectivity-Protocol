"""Session-cookie replay authentication per §2.10.

Stage 8e adds the fifth registered authentication method to UACP's
v1.x registry — and the most operationally distinctive one. Where the
prior four (OAuth 2.0 authcode, OAuth 2.0 workspace, AWS SigV4, API
key) all assume the provider has a public API surface that authorizes
machine clients, ``session_cookie`` covers the complement: providers
*without* public APIs that the user already has browser-equivalent
access to. The user logs in via Chromium → captures the storage state
→ UACP replays the cookies on every request, with optional CSRF token
refresh on 401/403/419.

The canonical reference implementation pattern is the open-source
``notebooklm-py`` library at https://github.com/teng-lin/notebooklm-py;
its ``src/notebooklm/`` modules document the cookie + CSRF dance for
Google's NotebookLM, which has no public API and is reachable only
via the same RPC endpoint the web UI uses.

Conformance posture: ``session_cookie`` is **MAY** in v1.x's §2.9
table. Implementations supporting it MUST surface a ToS-violation
risk warning at connection-creation time (per §2.10), and the
``.uacp`` artifact MUST set ``tos_acknowledged: true`` to load
(enforced at §3.10 spec validation). Without that ack, the spec
loader rejects the artifact.

Five integration points exposed by this module:

  - ``parse_storage_state(raw)`` — accepts Playwright's
    ``storage_state.json`` shape and returns a normalized
    ``StorageState`` object.
  - ``inject_cookies(request, storage_state, csrf_state)`` — returns
    AuthApplyResult with the Cookie header (filtered by URL + cookie
    domain match per RFC 6265) plus the CSRF token header when
    csrf_state is populated.
  - ``refresh_csrf(refresh_url, storage_state, extraction_path,
    client)`` — fetches the refresh URL with current cookies, parses
    the response, extracts a fresh CSRF token via the configured
    extraction path. Returns the new token string.
  - ``SessionCookieMethod`` — adapter implementing AuthMethod.
  - ``CSRFConfig`` — declarative shape declaring how the CSRF token
    is read from the artifact (header_name + cookie_name OR
    extraction_path) and how it's refreshed (refresh_url +
    extraction_path).

Per §6.1's threat-model addition for Stage 8e, session_cookie auth
bypasses provider authentication once captured; revocation requires
the user to log out of the browser session that originally captured
the state. The credential blast radius equals a stolen browser
cookie. Implementations MUST encrypt storage_state at rest per §6.3
with the same rigor as OAuth tokens. The prototype's local-keyring
store satisfies this since AES-256-GCM at rest is the §6.3 floor.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .base import AuthApplyResult


__all__ = [
    "Cookie",
    "CSRFConfig",
    "CSRFState",
    "SessionCookieAuthError",
    "SessionCookieConfig",
    "SessionCookieMethod",
    "StorageState",
    "extract_csrf_from_payload",
    "filter_cookies_for_url",
    "format_cookie_header",
    "inject_cookies",
    "parse_storage_state",
    "refresh_csrf",
    "tos_violation_warning",
]


log = logging.getLogger("uacp.auth.session_cookie")


TOS_WARNING_TEXT = (
    "WARNING — session_cookie authentication may violate the provider's "
    "Terms of Service. Replaying browser-captured cookies against an "
    "undocumented or restricted API is a grey-zone practice. The user is "
    "responsible for confirming compatibility with the provider's ToS "
    "before enabling this connection. Per §2.10 conformance, every "
    "dispatch through a session_cookie connection is logged at "
    "audit-log INFO with risk: tos_violation_potential."
)


def tos_violation_warning() -> str:
    """Return the canonical ToS-violation warning text. Surfaced by the
    AuthMethod constructor and (per §2.10) at connection-creation
    time. The CLI's connection-creation flow MUST print this; the
    string is exposed here so other surfaces (UI, telemetry) can
    reuse the exact wording.
    """
    return TOS_WARNING_TEXT


class SessionCookieAuthError(Exception):
    """session_cookie auth failed (missing storage state, malformed
    cookie jar, CSRF refresh failed, etc.)."""


# ---------------------------------------------------------------------------
# Storage-state parsing (Playwright shape)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cookie:
    """A single cookie in Playwright's storage_state.json shape.

    Field names mirror Playwright's serialization; UACP doesn't
    redefine the format. ``expires`` is Unix seconds (-1 means
    session cookie; we accept it but treat as non-expiring at
    replay time — the provider decides when the session lapses).
    """

    name: str
    value: str
    domain: str
    path: str = "/"
    expires: float = -1
    httpOnly: bool = False  # noqa: N815 — Playwright's casing
    secure: bool = False
    sameSite: str | None = None  # noqa: N815


@dataclass(frozen=True)
class StorageState:
    """Parsed Playwright storage_state.json."""

    cookies: tuple[Cookie, ...]
    origins: tuple[dict, ...] = ()


def parse_storage_state(raw: str | bytes | dict) -> StorageState:
    """Parse Playwright's storage_state.json into a StorageState.

    Tolerates dict / JSON string / JSON bytes input. Cookies missing
    optional fields get safe defaults; cookies missing required
    fields (name, value, domain) are dropped with a logger warning
    rather than raising — a half-corrupted state is more useful than
    no state, and the missing-required check is a soft signal that
    the operator's capture flow may have been incomplete.
    """
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise SessionCookieAuthError(f"storage_state is not valid JSON: {e}") from e
    elif isinstance(raw, dict):
        data = raw
    else:
        raise SessionCookieAuthError(
            f"storage_state must be JSON / bytes / dict; got {type(raw).__name__}"
        )

    raw_cookies = data.get("cookies") or []
    if not isinstance(raw_cookies, list):
        raise SessionCookieAuthError("storage_state.cookies must be a list")

    cookies: list[Cookie] = []
    for entry in raw_cookies:
        if not isinstance(entry, dict):
            log.warning("dropping non-dict cookie entry from storage_state")
            continue
        name = entry.get("name")
        value = entry.get("value")
        domain = entry.get("domain")
        if not (isinstance(name, str) and isinstance(value, str) and isinstance(domain, str)):
            log.warning("dropping cookie with missing name / value / domain: %r", entry)
            continue
        cookies.append(
            Cookie(
                name=name,
                value=value,
                domain=domain,
                path=entry.get("path", "/"),
                expires=float(entry.get("expires", -1)),
                httpOnly=bool(entry.get("httpOnly", False)),
                secure=bool(entry.get("secure", False)),
                sameSite=entry.get("sameSite"),
            )
        )

    origins = data.get("origins") or []
    return StorageState(
        cookies=tuple(cookies),
        origins=tuple(o for o in origins if isinstance(o, dict)),
    )


# ---------------------------------------------------------------------------
# Cookie filtering + header construction
# ---------------------------------------------------------------------------


def _domain_matches(request_host: str, cookie_domain: str) -> bool:
    """RFC 6265 §5.1.3 domain matching.

    A cookie's domain matches a host when:
      - The two are identical, OR
      - The cookie's domain is a parent of the host (e.g.,
        ``.example.com`` matches ``api.example.com``).

    Playwright stores domains with the leading dot for parent-domain
    cookies (``.example.com``) and without for host-only
    (``example.com``); both forms are handled.
    """
    if not request_host or not cookie_domain:
        return False
    request_host = request_host.lower()
    cookie_domain = cookie_domain.lower()
    if cookie_domain.startswith("."):
        cookie_domain_bare = cookie_domain[1:]
        return request_host == cookie_domain_bare or request_host.endswith(
            "." + cookie_domain_bare
        )
    # Host-only cookie: exact match.
    return request_host == cookie_domain


def _path_matches(request_path: str, cookie_path: str) -> bool:
    """RFC 6265 §5.1.4 path matching."""
    if not cookie_path or cookie_path == "/":
        return True
    if request_path == cookie_path:
        return True
    if request_path.startswith(cookie_path):
        # Either cookie path ends in / or next char in request path is /
        if cookie_path.endswith("/"):
            return True
        if len(request_path) > len(cookie_path) and request_path[len(cookie_path)] == "/":
            return True
    return False


def filter_cookies_for_url(storage: StorageState, url: str) -> list[Cookie]:
    """Return cookies whose domain + path match the given URL per RFC
    6265 §5.4. Secure cookies are gated by URL scheme.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    is_https = parsed.scheme == "https"

    out: list[Cookie] = []
    for c in storage.cookies:
        if c.secure and not is_https:
            continue
        if not _domain_matches(host, c.domain):
            continue
        if not _path_matches(path, c.path):
            continue
        out.append(c)
    return out


def format_cookie_header(cookies: list[Cookie]) -> str:
    """Format a list of cookies as the value of an HTTP Cookie header
    per RFC 6265 §5.4. Names + values are joined by =; pairs are
    separated by ``"; "`` (semicolon + space).
    """
    return "; ".join(f"{c.name}={c.value}" for c in cookies)


# ---------------------------------------------------------------------------
# CSRF configuration + state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CSRFConfig:
    """How to read and refresh a CSRF token for session_cookie auth.

    Two read paths supported:

    - ``cookie_name`` — the token IS a cookie (common pattern). When
      injecting, the prototype reads this cookie's value and adds a
      separate header named ``header_name``.
    - ``extraction_path`` — the token is in the body of the
      ``refresh_url`` response. After fetching that URL with the
      current cookies, parse JSON or extract via regex per the
      ``extraction_format`` and read the token from ``extraction_path``.
      Format is ``json`` (JSONPath subset matching §3.4) or
      ``regex`` (a regex with a single capture group).

    A ``CSRFConfig`` MUST set ``header_name`` (where to put the
    token in outgoing requests) and at least one of ``cookie_name``
    or ``extraction_path``.
    """

    header_name: str
    cookie_name: str | None = None
    refresh_url: str | None = None
    extraction_path: str | None = None
    extraction_format: str = "json"  # "json" or "regex"


@dataclass(frozen=True)
class CSRFState:
    """The runtime state of a CSRF token: the most-recent value plus
    bookkeeping. Held outside the AuthMethod adapter (which is
    immutable) so the dispatcher can refresh it after a 401/403/419
    without rebuilding the AuthMethod.
    """

    token: str | None = None
    # Optional: when the token was extracted, for staleness telemetry
    # — implementation-defined; not part of the wire protocol.


# ---------------------------------------------------------------------------
# SessionCookie config + AuthMethod adapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionCookieConfig:
    """Parsed `session_cookie` config from a `.uacp` artifact.

    `cookie_names` MAY be empty, in which case all cookies in the
    storage state matching the request URL are sent. When non-empty,
    only listed cookie names are sent (whitelist filtering — useful
    when the provider sets analytics or non-auth cookies that aren't
    needed for replay).
    """

    cookie_names: tuple[str, ...] = ()
    csrf: CSRFConfig | None = None


def inject_cookies(
    request: Any,
    *,
    storage_state: StorageState,
    config: SessionCookieConfig,
    csrf_state: CSRFState | None = None,
) -> AuthApplyResult:
    """Build the Cookie + optional CSRF-token headers for the request.

    `request` is any object with a ``url`` attribute (or a string URL).
    The function does not mutate the request; it returns the headers
    delta the dispatcher merges per §4.1 composition order.
    """
    url = str(getattr(request, "url", request))

    cookies = filter_cookies_for_url(storage_state, url)
    if config.cookie_names:
        whitelist = set(config.cookie_names)
        cookies = [c for c in cookies if c.name in whitelist]

    if not cookies:
        raise SessionCookieAuthError(
            f"no cookies in storage_state match URL {url!r} (host or path filter "
            f"excluded all entries; possible mismatch between captured state and "
            f"target API endpoint)"
        )

    headers: dict[str, str] = {"Cookie": format_cookie_header(cookies)}

    # CSRF token injection.
    if config.csrf is not None:
        token: str | None = None
        if csrf_state is not None and csrf_state.token:
            token = csrf_state.token
        elif config.csrf.cookie_name:
            # Read from cookies in the storage state directly. Pick the
            # FIRST matching cookie (the one most recently captured, by
            # Playwright convention).
            for c in storage_state.cookies:
                if c.name == config.csrf.cookie_name:
                    token = c.value
                    break
        if token is not None:
            headers[config.csrf.header_name] = token

    return AuthApplyResult(headers=headers)


def extract_csrf_from_payload(
    payload: bytes | str,
    *,
    extraction_path: str,
    extraction_format: str,
) -> str | None:
    """Extract a CSRF token from a refresh-URL response body.

    `extraction_format` is "json" (JSONPath subset $.field /
    $.field.subfield) or "regex" (single capture group).
    """
    if isinstance(payload, (bytes, bytearray)):
        text = payload.decode("utf-8", errors="replace")
    else:
        text = payload

    if extraction_format == "regex":
        m = re.search(extraction_path, text)
        if m and m.groups():
            return m.group(1)
        return None

    if extraction_format == "json":
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not extraction_path.startswith("$."):
            return None
        segments = extraction_path[2:].split(".")
        cur: Any = data
        for seg in segments:
            if not isinstance(cur, dict) or seg not in cur:
                return None
            cur = cur[seg]
        return cur if isinstance(cur, str) else None

    return None


def refresh_csrf(
    refresh_url: str,
    *,
    storage_state: StorageState,
    extraction_path: str,
    extraction_format: str,
    http_client: Any,
) -> str:
    """Fetch the refresh URL with current cookies, extract a fresh
    CSRF token. Raises SessionCookieAuthError on failure.

    `http_client` is an httpx.Client (or compatible) so the dispatcher
    can reuse its connection pool. The function reads cookies from
    storage_state filtered for the refresh_url's host+path.
    """
    cookies = filter_cookies_for_url(storage_state, refresh_url)
    if not cookies:
        raise SessionCookieAuthError(
            f"refresh_csrf: no cookies match refresh_url {refresh_url!r}"
        )

    response = http_client.get(
        refresh_url,
        headers={"Cookie": format_cookie_header(cookies)},
    )
    if response.status_code >= 400:
        raise SessionCookieAuthError(
            f"refresh_csrf: refresh_url returned {response.status_code}: "
            f"{response.text[:200]!r}"
        )

    body = response.content
    new_token = extract_csrf_from_payload(
        body, extraction_path=extraction_path, extraction_format=extraction_format
    )
    if not new_token:
        raise SessionCookieAuthError(
            f"refresh_csrf: extraction at {extraction_path!r} ({extraction_format}) "
            f"yielded no token; response body sample: {body[:200]!r}"
        )
    return new_token


@dataclass
class SessionCookieMethod:
    """Adapter implementing AuthMethod for session_cookie auth.

    The dispatcher constructs this from the artifact's authentication
    block, then calls ``apply`` with the prepared httpx.Request and
    the credentials dict carrying the resolved storage_state JSON
    plus the current CSRF state.

    Per §2.10 the constructor surfaces the ToS-violation warning via
    the module logger at WARNING level. The CLI's connection-creation
    flow is responsible for surfacing the human-readable warning at
    operator-time; the logger emit ensures the audit trail captures
    it regardless of UI surface.
    """

    method: str = "session_cookie"
    config: SessionCookieConfig | None = None
    _warned: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        # Emit the §2.10 ToS warning once per AuthMethod construction.
        if not self._warned:
            log.warning("session_cookie auth: %s", TOS_WARNING_TEXT)
            object.__setattr__(self, "_warned", True)

    def apply(
        self, request: Any, *, credentials: dict[str, Any]
    ) -> AuthApplyResult:
        if self.config is None:
            raise SessionCookieAuthError(
                "SessionCookieMethod.apply: config not set; the dispatcher "
                "MUST construct this adapter from the artifact's authentication "
                "block (cookie_names + optional csrf)."
            )
        raw_state = credentials.get("storage_state")
        if raw_state is None:
            raise SessionCookieAuthError(
                "SessionCookieMethod.apply: credentials missing storage_state. "
                "The artifact's storage_state_ref MUST resolve to Playwright "
                "storage_state.json content."
            )
        if isinstance(raw_state, StorageState):
            storage = raw_state
        else:
            storage = parse_storage_state(raw_state)

        csrf_state = credentials.get("csrf_state")
        if csrf_state is not None and not isinstance(csrf_state, CSRFState):
            csrf_state = CSRFState(token=str(csrf_state))

        return inject_cookies(
            request,
            storage_state=storage,
            config=self.config,
            csrf_state=csrf_state,
        )
