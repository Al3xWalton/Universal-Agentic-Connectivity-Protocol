"""Tests for AWS SigV4 implementation per §2.5.1.

The most stringent tests use AWS's own published test vectors. AWS's
General Reference includes a worked example for the IAM ListUsers
request on 2015-08-30 that walks through the canonical request, the
string-to-sign, and the final signature. Several other test vectors
come from AWS's published "Examples of the Complete Version 4 Signing
Process" and the test-suite tarball at
https://docs.aws.amazon.com/general/latest/gr/signature-v4-test-suite.html.

Reproducing the canonical IAM ListUsers test vector here both validates
the implementation and serves as a regression anchor for any future
changes.

Beyond the AWS-published vectors, this file covers the edge cases
named in §2.5.1's deferral to AWS's spec: empty body hash; query-string
multi-value sort; header whitespace trimming; SigV4 + STS session
token; S3-specific URL encoding.
"""

from __future__ import annotations

import datetime as _dt
import hashlib

import httpx
import pytest

from uacp_prototype.auth.aws_sigv4 import (
    AWSSigV4Config,
    AWSSigV4Method,
    EMPTY_PAYLOAD_HASH,
    SIGV4_ALGORITHM,
    SigV4Error,
    canonical_request,
    credential_scope,
    sign_request,
    signing_key,
    string_to_sign,
)


# ---------------------------------------------------------------------------
# AWS-published test vector: IAM GET ListUsers (2015-08-30)
#
# This is the worked example in AWS's General Reference. The expected
# values below are reproduced from AWS's documentation:
#   - Canonical request, string-to-sign, signing key, and final
#     signature are all published in the docs.
# ---------------------------------------------------------------------------


# AWS's published example credentials (intentionally test-only)
EXAMPLE_ACCESS_KEY = "AKIDEXAMPLE"
EXAMPLE_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"

# AWS's published example datetime
EXAMPLE_DT = _dt.datetime(2015, 8, 30, 12, 36, 0, tzinfo=_dt.timezone.utc)
EXAMPLE_DATE = "20150830"
EXAMPLE_ISO = "20150830T123600Z"


def test_signing_key_derivation_matches_aws_published() -> None:
    """AWS's docs publish the intermediate kSigning value for the
    example credentials on 20150830 / us-east-1 / iam.

    Expected kSigning = c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e
    86da6ed3c154a4b9 (hex).
    """
    key = signing_key(EXAMPLE_SECRET_KEY, EXAMPLE_DATE, "us-east-1", "iam")
    assert key.hex() == "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"


def test_canonical_request_iam_get_listusers() -> None:
    """AWS's published canonical request for:
        GET https://iam.amazonaws.com/?Action=ListUsers&Version=2010-05-08
        Host: iam.amazonaws.com
        x-amz-date: 20150830T123600Z

    Expected:
        GET
        /

        Action=ListUsers&Version=2010-05-08
        content-type:application/x-www-form-urlencoded; charset=utf-8
        host:iam.amazonaws.com
        x-amz-date:20150830T123600Z

        content-type;host;x-amz-date
        e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    """
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "Host": "iam.amazonaws.com",
        "x-amz-date": EXAMPLE_ISO,
    }
    cr, signed, payload = canonical_request(
        "GET",
        "https://iam.amazonaws.com/?Action=ListUsers&Version=2010-05-08",
        headers,
        body=None,
        service="iam",
    )
    expected = (
        "GET\n"
        "/\n"
        "Action=ListUsers&Version=2010-05-08\n"
        "content-type:application/x-www-form-urlencoded; charset=utf-8\n"
        "host:iam.amazonaws.com\n"
        "x-amz-date:20150830T123600Z\n"
        "\n"
        "content-type;host;x-amz-date\n"
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert cr == expected
    assert signed == "content-type;host;x-amz-date"
    assert payload == EMPTY_PAYLOAD_HASH


def test_string_to_sign_iam_get_listusers() -> None:
    """The expected string-to-sign for the IAM ListUsers example is:

        AWS4-HMAC-SHA256
        20150830T123600Z
        20150830/us-east-1/iam/aws4_request
        f536975d06c0309214f805bb90ccff089219ecd68b2577efef23edd43b7e1a59

    where the final hex is sha256(canonical_request).
    """
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "Host": "iam.amazonaws.com",
        "x-amz-date": EXAMPLE_ISO,
    }
    cr, _signed, _payload = canonical_request(
        "GET",
        "https://iam.amazonaws.com/?Action=ListUsers&Version=2010-05-08",
        headers,
        body=None,
        service="iam",
    )
    sts = string_to_sign(
        cr, EXAMPLE_DT, credential_scope(EXAMPLE_DATE, "us-east-1", "iam")
    )
    expected = (
        "AWS4-HMAC-SHA256\n"
        "20150830T123600Z\n"
        "20150830/us-east-1/iam/aws4_request\n"
        "f536975d06c0309214f805bb90ccff089219ecd68b2577efef23edd43b7e1a59"
    )
    assert sts == expected


def test_full_signature_iam_get_listusers() -> None:
    """The expected final signature for IAM ListUsers (per AWS docs):
        5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7
    """
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "Host": "iam.amazonaws.com",
    }
    cfg = AWSSigV4Config(
        access_key_id=EXAMPLE_ACCESS_KEY,
        secret_access_key=EXAMPLE_SECRET_KEY,
        service="iam",
        region="us-east-1",
    )
    out = sign_request(
        "GET",
        "https://iam.amazonaws.com/?Action=ListUsers&Version=2010-05-08",
        headers,
        body=None,
        credentials=cfg,
        request_dt=EXAMPLE_DT,
    )
    auth = out["Authorization"]
    assert SIGV4_ALGORITHM in auth
    assert (
        f"Credential={EXAMPLE_ACCESS_KEY}/20150830/us-east-1/iam/aws4_request"
        in auth
    )
    assert "Signature=5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7" in auth


# ---------------------------------------------------------------------------
# Edge case: empty body
# ---------------------------------------------------------------------------


def test_empty_body_uses_sha256_of_empty_string() -> None:
    """The SHA-256 of empty string is the expected payload hash for
    requests with no body. NOT UNSIGNED-PAYLOAD — that's only for
    streaming uploads.
    """
    cfg = AWSSigV4Config(
        access_key_id="AKID",
        secret_access_key="SKID",
        service="s3",
        region="us-east-1",
    )
    out = sign_request(
        "GET",
        "https://example-bucket.s3.us-east-1.amazonaws.com/",
        headers={},
        body=None,
        credentials=cfg,
        request_dt=EXAMPLE_DT,
    )
    assert out["x-amz-content-sha256"] == EMPTY_PAYLOAD_HASH


def test_empty_body_bytes_uses_sha256_of_empty_string() -> None:
    """b"" should produce the same payload hash as None."""
    cfg = AWSSigV4Config(
        access_key_id="AKID",
        secret_access_key="SKID",
        service="s3",
        region="us-east-1",
    )
    out_none = sign_request(
        "GET", "https://b.s3.us-east-1.amazonaws.com/",
        headers={}, body=None,
        credentials=cfg, request_dt=EXAMPLE_DT,
    )
    out_empty = sign_request(
        "GET", "https://b.s3.us-east-1.amazonaws.com/",
        headers={}, body=b"",
        credentials=cfg, request_dt=EXAMPLE_DT,
    )
    assert out_none["x-amz-content-sha256"] == out_empty["x-amz-content-sha256"]


# ---------------------------------------------------------------------------
# Edge case: query-string multi-value sort
# ---------------------------------------------------------------------------


def test_query_string_multi_value_sorts_by_key_then_value() -> None:
    """Per SigV4: sort canonical query by (name, value), URL-encoded."""
    cr, signed, _ = canonical_request(
        "GET",
        "https://example.com/?b=2&a=2&a=1",
        headers={"Host": "example.com"},
        body=None,
        service="generic",
    )
    # Sorted: a=1, a=2, b=2
    assert "a=1&a=2&b=2" in cr


def test_query_string_blank_value_preserved() -> None:
    cr, _signed, _ = canonical_request(
        "GET",
        "https://example.com/?empty=&nonempty=v",
        headers={"Host": "example.com"},
        body=None,
        service="generic",
    )
    assert "empty=" in cr
    assert "nonempty=v" in cr


# ---------------------------------------------------------------------------
# Edge case: header whitespace trimming
# ---------------------------------------------------------------------------


def test_header_whitespace_collapsed() -> None:
    """Per SigV4: leading/trailing whitespace stripped, internal sequential
    whitespace collapsed."""
    cr, _signed, _ = canonical_request(
        "GET",
        "https://example.com/",
        headers={
            "Host": "example.com",
            "X-My-Header": "  multiple   spaces   inside  ",
        },
        body=None,
        service="generic",
    )
    assert "x-my-header:multiple spaces inside\n" in cr


def test_header_names_lowercased_and_sorted() -> None:
    cr, signed, _ = canonical_request(
        "GET",
        "https://example.com/",
        headers={"Host": "example.com", "Z-Custom": "z", "A-Custom": "a"},
        body=None,
        service="generic",
    )
    assert signed == "a-custom;host;z-custom"
    # Lines appear in sorted order in the canonical request
    a_pos = cr.find("a-custom:a\n")
    h_pos = cr.find("host:example.com\n")
    z_pos = cr.find("z-custom:z\n")
    assert 0 < a_pos < h_pos < z_pos


# ---------------------------------------------------------------------------
# Edge case: STS session token
# ---------------------------------------------------------------------------


def test_session_token_added_to_signed_headers() -> None:
    cfg = AWSSigV4Config(
        access_key_id="ASIA-TEMPORARY",
        secret_access_key="SK",
        service="s3",
        region="us-east-1",
        session_token="FwoGZXIvYXdzELw" + "x" * 100,  # mock STS-shape token
    )
    out = sign_request(
        "GET",
        "https://b.s3.us-east-1.amazonaws.com/",
        headers={},
        body=None,
        credentials=cfg,
        request_dt=EXAMPLE_DT,
    )
    assert "x-amz-security-token" in out
    assert out["x-amz-security-token"].startswith("FwoG")
    # Session token must participate in the signed-headers list
    auth = out["Authorization"]
    assert "x-amz-security-token" in auth


def test_no_session_token_omits_header() -> None:
    cfg = AWSSigV4Config(
        access_key_id="AKID",
        secret_access_key="SK",
        service="s3",
        region="us-east-1",
    )
    out = sign_request(
        "GET",
        "https://b.s3.us-east-1.amazonaws.com/",
        headers={},
        body=None,
        credentials=cfg,
        request_dt=EXAMPLE_DT,
    )
    assert "x-amz-security-token" not in out
    assert "x-amz-security-token" not in out["Authorization"]


# ---------------------------------------------------------------------------
# Service-specific URL encoding (S3 vs non-S3)
# ---------------------------------------------------------------------------


def test_s3_path_single_encoded() -> None:
    """S3's canonical URI is single-encoded; the path with a slash inside
    a key like /folder/file%20.txt stays single-encoded."""
    cr, _, _ = canonical_request(
        "GET",
        "https://bucket.s3.us-east-1.amazonaws.com/folder/file.txt",
        headers={"Host": "bucket.s3.us-east-1.amazonaws.com"},
        body=None,
        service="s3",
    )
    # Path appears single-encoded
    assert "GET\n/folder/file.txt\n" in cr


def test_non_s3_path_double_encoded() -> None:
    """For non-S3 services, the path is double-encoded. A path like
    /foo bar becomes /foo%2520bar in the canonical request."""
    cr, _, _ = canonical_request(
        "GET",
        "https://example.amazonaws.com/foo%20bar",
        headers={"Host": "example.amazonaws.com"},
        body=None,
        service="lambda",
    )
    # %20 → %2520 after second encoding
    assert "%2520" in cr.split("\n")[1]


# ---------------------------------------------------------------------------
# Body hash with content
# ---------------------------------------------------------------------------


def test_body_hash_is_sha256_of_body() -> None:
    body = b"hello world"
    expected = hashlib.sha256(body).hexdigest()
    cfg = AWSSigV4Config(
        access_key_id="AKID",
        secret_access_key="SK",
        service="s3",
        region="us-east-1",
    )
    out = sign_request(
        "POST",
        "https://b.s3.us-east-1.amazonaws.com/key",
        headers={"Host": "b.s3.us-east-1.amazonaws.com"},
        body=body,
        credentials=cfg,
        request_dt=EXAMPLE_DT,
    )
    assert out["x-amz-content-sha256"] == expected


# ---------------------------------------------------------------------------
# Authorization header shape
# ---------------------------------------------------------------------------


def test_authorization_header_structure() -> None:
    cfg = AWSSigV4Config(
        access_key_id="AKID",
        secret_access_key="SK",
        service="s3",
        region="us-east-1",
    )
    out = sign_request(
        "GET",
        "https://b.s3.us-east-1.amazonaws.com/",
        headers={},
        body=None,
        credentials=cfg,
        request_dt=EXAMPLE_DT,
    )
    auth = out["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 ")
    assert "Credential=AKID/20150830/us-east-1/s3/aws4_request" in auth
    assert "SignedHeaders=" in auth
    assert "Signature=" in auth
    sig_part = auth.split("Signature=")[1]
    assert len(sig_part) == 64
    assert all(c in "0123456789abcdef" for c in sig_part)


def test_x_amz_date_iso_format() -> None:
    cfg = AWSSigV4Config(
        access_key_id="AKID",
        secret_access_key="SK",
        service="s3",
        region="us-east-1",
    )
    out = sign_request(
        "GET",
        "https://b.s3.us-east-1.amazonaws.com/",
        headers={},
        body=None,
        credentials=cfg,
        request_dt=EXAMPLE_DT,
    )
    assert out["x-amz-date"] == "20150830T123600Z"


# ---------------------------------------------------------------------------
# Default port stripping in Host
# ---------------------------------------------------------------------------


def test_https_port_443_stripped_from_host() -> None:
    cr, _, _ = canonical_request(
        "GET",
        "https://example.com:443/",
        headers={},
        body=None,
        service="generic",
    )
    assert "host:example.com\n" in cr
    assert "host:example.com:443\n" not in cr


# ---------------------------------------------------------------------------
# Method adapter
# ---------------------------------------------------------------------------


def test_method_adapter_signs_request() -> None:
    method = AWSSigV4Method(service="s3", region="us-east-1")
    req = httpx.Request(
        "GET",
        "https://b.s3.us-east-1.amazonaws.com/",
        headers={"Host": "b.s3.us-east-1.amazonaws.com"},
    )
    result = method.apply(
        req,
        credentials={
            "access_key_id": "AKID",
            "secret_access_key": "SK",
        },
    )
    assert "Authorization" in result.headers
    assert result.headers["Authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert "x-amz-date" in result.headers
    assert "x-amz-content-sha256" in result.headers


def test_method_adapter_missing_credentials_raises() -> None:
    method = AWSSigV4Method()
    req = httpx.Request("GET", "https://b.s3.us-east-1.amazonaws.com/")
    with pytest.raises(SigV4Error, match="missing"):
        method.apply(req, credentials={})


def test_method_adapter_with_session_token() -> None:
    method = AWSSigV4Method(service="s3", region="us-west-2")
    req = httpx.Request("GET", "https://b.s3.us-west-2.amazonaws.com/key")
    result = method.apply(
        req,
        credentials={
            "access_key_id": "ASIA-TEMP",
            "secret_access_key": "SK",
            "session_token": "STS-TOKEN-VALUE",
        },
    )
    assert result.headers["x-amz-security-token"] == "STS-TOKEN-VALUE"


# ---------------------------------------------------------------------------
# Determinism: same inputs → same signature
# ---------------------------------------------------------------------------


def test_signature_is_deterministic() -> None:
    cfg = AWSSigV4Config(
        access_key_id="AKID",
        secret_access_key="SK",
        service="s3",
        region="us-east-1",
    )
    a = sign_request(
        "GET",
        "https://b.s3.us-east-1.amazonaws.com/",
        headers={},
        body=None,
        credentials=cfg,
        request_dt=EXAMPLE_DT,
    )
    b = sign_request(
        "GET",
        "https://b.s3.us-east-1.amazonaws.com/",
        headers={},
        body=None,
        credentials=cfg,
        request_dt=EXAMPLE_DT,
    )
    assert a == b


def test_different_region_different_signature() -> None:
    cfg_a = AWSSigV4Config(
        access_key_id="AKID", secret_access_key="SK",
        service="s3", region="us-east-1",
    )
    cfg_b = AWSSigV4Config(
        access_key_id="AKID", secret_access_key="SK",
        service="s3", region="us-west-2",
    )
    a = sign_request(
        "GET", "https://b.s3.amazonaws.com/",
        headers={}, body=None, credentials=cfg_a, request_dt=EXAMPLE_DT,
    )
    b = sign_request(
        "GET", "https://b.s3.amazonaws.com/",
        headers={}, body=None, credentials=cfg_b, request_dt=EXAMPLE_DT,
    )
    assert a["Authorization"] != b["Authorization"]
