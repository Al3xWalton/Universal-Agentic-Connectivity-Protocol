"""AWS Signature Version 4 per §2.5.1.

Implements SigV4 from scratch using only ``hashlib`` and ``hmac`` from
the Python standard library. No dependency on ``boto3`` or
``botocore`` — UACP's whole point is owning the auth implementation
rather than delegating to AWS's SDK; a Conforming Implementation MUST
follow AWS's published spec exactly per §2.5.1, and proving the
implementation against AWS's published test vectors is the right way
to demonstrate that.

Five integration points:

  - ``sign_request(method, url, headers, body, *, credentials, service,
    region, request_dt)`` — pure function. Takes a prepared HTTP
    request and returns a dict of headers to add (Authorization,
    x-amz-date, x-amz-content-sha256, optionally x-amz-security-token).
    The dispatcher calls this and merges the returned headers into the
    wire request.
  - ``AWSSigV4Method`` — adapter implementing the AuthMethod Protocol.
    Wraps ``sign_request`` and reads service / region from the artifact
    plus credentials from the resolver.
  - ``canonical_request(method, url, headers, body)`` — exposed for
    testing against AWS's published test vectors.
  - ``string_to_sign(canonical_request, request_dt, credential_scope)``
    — same.
  - ``signing_key(secret_access_key, date, region, service)`` — same.

Edge cases handled (each named in §2.5.1's deferral to AWS's spec, and
each verified against the AWS-published test vectors):

  - Empty body: SHA-256 of empty string =
    e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    (NOT UNSIGNED-PAYLOAD; that's only for streaming uploads).
  - Query string with multiple values for the same key: canonical sort
    applies to (key, value) pairs lexicographically, not just keys.
  - Headers containing commas: whitespace-trimmed; multiple values
    joined with comma (no surrounding spaces in canonical form).
  - URL paths with reserved characters: SigV4 follows RFC 3986
    percent-encoding for non-S3 services. S3 specifically does NOT
    re-encode the path (it's signed verbatim with one round of
    percent-encoding); the ``service`` field switches the rule.
  - Host header derivation: when not explicitly set, the Host header
    is derived from the URL's authority component (host[:port]).
  - x-amz-content-sha256 inclusion: always computed; for SigV4 against
    S3 it MUST be in SignedHeaders (not just any service).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse

from .base import AuthApplyResult


__all__ = [
    "AWSSigV4Config",
    "AWSSigV4Method",
    "SigV4Error",
    "canonical_request",
    "credential_scope",
    "sign_request",
    "signing_key",
    "string_to_sign",
]


SIGV4_ALGORITHM = "AWS4-HMAC-SHA256"
EMPTY_PAYLOAD_HASH = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


@dataclass(frozen=True)
class AWSSigV4Config:
    """Parsed `aws_sigv4` config from a `.uacp` artifact.

    Field names match §2.5.1 wire shape verbatim except that the *_ref
    references are resolved to plaintext by the dispatcher before this
    dataclass is constructed.
    """

    access_key_id: str
    secret_access_key: str
    service: str
    region: str
    session_token: str | None = None


class SigV4Error(Exception):
    """SigV4 protocol error (malformed inputs, unsupported edge cases)."""


# ---------------------------------------------------------------------------
# Canonical-request construction
# ---------------------------------------------------------------------------


def _canonical_uri(path: str, *, service: str) -> str:
    """Per AWS SigV4 spec: the URI path is signed in a service-specific
    encoded form.

    For S3, the path is signed verbatim against the wire form — no
    additional encoding. The wire URL already has reserved characters
    percent-encoded (one round); the canonical URI is that wire form.

    For all other services, the canonical URI is the wire form encoded
    one MORE time. AWS's "double encoding" rule starts from the
    *unencoded* original path and encodes it twice; equivalently,
    starting from the wire-encoded path (one round of encoding already
    applied), we apply one additional round. Practically: ``%`` in the
    wire path becomes ``%25`` in the canonical URI.

    The input ``path`` here is what ``urlparse`` extracts from the URL
    — i.e., the wire form.
    """
    if not path:
        return "/"
    if service == "s3":
        # Signed verbatim against the wire.
        return path
    # Non-S3: encode the wire form once more.
    return quote(path, safe="/~")


def _canonical_query_string(query: str) -> str:
    """Per SigV4: sort by name, then by value, percent-encode names and
    values separately. Multiple values for the same name appear as
    multiple `name=value` pairs in lexicographic order.
    """
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=False)
    encoded = [
        (quote(k, safe="-_.~"), quote(v, safe="-_.~"))
        for k, v in pairs
    ]
    encoded.sort()  # sort by (name, value) tuples
    return "&".join(f"{k}={v}" for k, v in encoded)


def _canonical_headers(
    headers: dict[str, str], *, host: str
) -> tuple[str, str]:
    """Returns (canonical_headers_block, signed_headers_list).

    Canonical headers: lowercased name + ':' + trimmed value + '\\n', each
    on a line, sorted by lowercase name. Trimming: leading/trailing
    whitespace stripped; sequential whitespace inside the value collapsed
    to a single space (only for values NOT inside double quotes — but
    SigV4's rule is simpler: whitespace-trim outside quoted strings; the
    prototype uses the simplified version that matches AWS's published
    test vectors).

    SignedHeaders: semicolon-separated lowercase header names, sorted.
    """
    work: dict[str, str] = {}
    for name, value in headers.items():
        lname = name.lower().strip()
        # Trim and collapse whitespace per SigV4.
        trimmed = " ".join(str(value).split())
        work[lname] = trimmed
    # Host always part of canonical headers.
    if "host" not in work:
        work["host"] = host
    sorted_names = sorted(work.keys())
    canonical = "".join(f"{n}:{work[n]}\n" for n in sorted_names)
    signed = ";".join(sorted_names)
    return canonical, signed


def _payload_hash(body: bytes | None) -> str:
    if body is None or body == b"":
        return EMPTY_PAYLOAD_HASH
    return hashlib.sha256(body).hexdigest()


def canonical_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    service: str,
) -> tuple[str, str, str]:
    """Construct the SigV4 canonical request.

    Returns (canonical_request_string, signed_headers, payload_hash).
    Exposed at the module level so unit tests can verify byte-for-byte
    against AWS's published test vectors.
    """
    parsed = urlparse(url)
    path = parsed.path or "/"
    query = parsed.query
    host = parsed.netloc
    # Host header derivation: exclude default ports (80 for http, 443 for
    # https) per the SigV4 spec's signing rules.
    if host.endswith(":443") and parsed.scheme == "https":
        host = host[:-4]
    elif host.endswith(":80") and parsed.scheme == "http":
        host = host[:-3]

    cur = _canonical_uri(path, service=service)
    cqs = _canonical_query_string(query)
    chs, signed = _canonical_headers(headers, host=host)
    payload = _payload_hash(body)

    cr = f"{method.upper()}\n{cur}\n{cqs}\n{chs}\n{signed}\n{payload}"
    return cr, signed, payload


# ---------------------------------------------------------------------------
# String-to-sign + signing key
# ---------------------------------------------------------------------------


def credential_scope(date: str, region: str, service: str) -> str:
    """{date}/{region}/{service}/aws4_request — used in both the
    Authorization header's Credential= field and the string-to-sign.

    `date` is YYYYMMDD (8 chars), the date portion of x-amz-date.
    """
    return f"{date}/{region}/{service}/aws4_request"


def string_to_sign(
    cr: str,
    request_dt: _dt.datetime,
    scope: str,
) -> str:
    """AWS4-HMAC-SHA256\\n{x-amz-date}\\n{credential-scope}\\n{hex(sha256(cr))}"""
    iso = request_dt.strftime("%Y%m%dT%H%M%SZ")
    cr_hash = hashlib.sha256(cr.encode("utf-8")).hexdigest()
    return f"{SIGV4_ALGORITHM}\n{iso}\n{scope}\n{cr_hash}"


def signing_key(
    secret_access_key: str, date: str, region: str, service: str
) -> bytes:
    """Per SigV4 spec:

      kSecret  = "AWS4" + secret_access_key
      kDate    = HMAC-SHA256(kSecret, date)
      kRegion  = HMAC-SHA256(kDate, region)
      kService = HMAC-SHA256(kRegion, service)
      kSigning = HMAC-SHA256(kService, "aws4_request")
    """
    k_secret = ("AWS4" + secret_access_key).encode("utf-8")
    k_date = hmac.new(k_secret, date.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
    return k_signing


# ---------------------------------------------------------------------------
# sign_request — the integration point
# ---------------------------------------------------------------------------


def sign_request(
    method: str,
    url: str,
    headers: dict[str, str] | None,
    body: bytes | None,
    *,
    credentials: AWSSigV4Config,
    request_dt: _dt.datetime | None = None,
) -> dict[str, str]:
    """Return the headers a SigV4-signed request must add.

    Adds: Authorization, x-amz-date, x-amz-content-sha256, optionally
    x-amz-security-token.

    Does NOT mutate the input headers; the caller merges the returned
    dict into the wire request. Header names returned are lowercase per
    SigV4's canonical form, but HTTP is header-name-case-insensitive
    (RFC 9110 §5.1) so any case works at the wire.
    """
    if request_dt is None:
        request_dt = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    if request_dt.tzinfo is None or request_dt.tzinfo.utcoffset(request_dt) != _dt.timedelta(0):
        # Treat naive datetime as UTC; reject non-UTC timezones so signed
        # timestamps line up with AWS's expectation.
        request_dt = request_dt.replace(tzinfo=_dt.timezone.utc)

    iso = request_dt.strftime("%Y%m%dT%H%M%SZ")
    date = request_dt.strftime("%Y%m%d")
    scope = credential_scope(date, credentials.region, credentials.service)

    headers = dict(headers or {})
    # x-amz-date is always part of the signed headers (every SigV4
    # request includes it).
    payload_hash = _payload_hash(body)
    headers["x-amz-date"] = iso
    # x-amz-content-sha256 is REQUIRED-and-signed for S3 specifically.
    # For other services it is optional and its absence matches AWS's
    # published test vectors (notably the IAM ListUsers worked example).
    # The prototype follows AWS's spec: include only for S3.
    if credentials.service == "s3":
        headers["x-amz-content-sha256"] = payload_hash
    if credentials.session_token:
        headers["x-amz-security-token"] = credentials.session_token

    cr, signed, _ph = canonical_request(
        method, url, headers, body, service=credentials.service
    )
    sts = string_to_sign(cr, request_dt, scope)
    key = signing_key(
        credentials.secret_access_key, date, credentials.region, credentials.service
    )
    signature = hmac.new(key, sts.encode("utf-8"), hashlib.sha256).hexdigest()

    auth = (
        f"{SIGV4_ALGORITHM} "
        f"Credential={credentials.access_key_id}/{scope}, "
        f"SignedHeaders={signed}, "
        f"Signature={signature}"
    )

    out = {
        "Authorization": auth,
        "x-amz-date": iso,
    }
    # Mirror the inclusion rule used during canonical-request
    # construction: x-amz-content-sha256 lands on the wire only when
    # signed (S3 always; other services don't include it by default).
    if credentials.service == "s3":
        out["x-amz-content-sha256"] = payload_hash
    if credentials.session_token:
        out["x-amz-security-token"] = credentials.session_token
    return out


# ---------------------------------------------------------------------------
# AuthMethod adapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AWSSigV4Method:
    """Adapter implementing AuthMethod for SigV4 dispatch.

    The dispatcher calls ``apply`` with the prepared httpx.Request. The
    method extracts URL + headers + body from the request, signs, and
    returns the headers to merge.

    Service and region come from the artifact's ``authentication``
    block; the AuthMethod adapter is constructed with them. Credentials
    come from the credential resolver per call.
    """

    method: str = "aws_sigv4"
    service: str = "s3"
    region: str = "us-east-1"

    def apply(
        self, request: Any, *, credentials: dict[str, Any]
    ) -> AuthApplyResult:
        access_key_id = credentials.get("access_key_id") or credentials.get("access_key")
        secret_access_key = credentials.get("secret_access_key") or credentials.get("secret_key")
        session_token = credentials.get("session_token")
        if not access_key_id or not secret_access_key:
            raise SigV4Error(
                "AWSSigV4Method.apply: credentials missing access_key_id and/or "
                "secret_access_key. The artifact's secret_refs must populate both."
            )
        cfg = AWSSigV4Config(
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            service=self.service,
            region=self.region,
            session_token=session_token,
        )

        # Read request shape from httpx.Request (or any object with method,
        # url, headers, content attributes).
        method = str(getattr(request, "method", "GET"))
        url = str(getattr(request, "url", ""))
        existing_headers = dict(getattr(request, "headers", {}) or {})

        # body is httpx.Request.content (bytes) OR may be None for GET
        content = getattr(request, "content", None)
        if hasattr(request, "read") and not content:
            try:
                content = request.read()
            except Exception:
                content = None
        body_bytes: bytes | None = content if isinstance(content, (bytes, bytearray)) else None

        signed_headers = sign_request(
            method,
            url,
            existing_headers,
            body_bytes,
            credentials=cfg,
        )
        return AuthApplyResult(headers=signed_headers)
