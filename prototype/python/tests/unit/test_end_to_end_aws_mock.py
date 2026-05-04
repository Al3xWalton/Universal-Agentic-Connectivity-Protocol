"""Mock-based end-to-end test for the AWS S3 pipeline.

Parallel to test_end_to_end_mock.py (Google) and
test_end_to_end_slack_mock.py (Slack). Loads each S3 .uacp artifact,
mocks the S3 endpoints, dispatches through the full stack
(spec loader → SigV4 auth → dispatch client → format-aware response
decoding), and asserts request shape including the SigV4 Authorization
header and response decoding (binary for GetObject, XML→dict for
ListObjectsV2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from uacp_prototype.auth.aws_sigv4 import AWSSigV4Method
from uacp_prototype.dispatch.client import DispatchClient, DispatchSuccess
from uacp_prototype.dispatch.pagination import dispatch_paginated
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "aws"
GET_FILE = EXAMPLES_DIR / "s3-getobject.uacp"
LIST_FILE = EXAMPLES_DIR / "s3-listobjectsv2.uacp"


def _client(artifact: Any, *, sleep: Any = lambda _s: None) -> DispatchClient:
    return DispatchClient(
        artifact,
        auth_method=AWSSigV4Method(service="s3", region="us-east-1"),
        credential_resolver=lambda: {
            "access_key_id": "AKIDMOCK",
            "secret_access_key": "SKIDMOCK",
        },
        sleep=sleep,
    )


# ---------------------------------------------------------------------------
# s3.GetObject — binary response
# ---------------------------------------------------------------------------


@respx.mock
def test_s3_getobject_returns_binary_bytes_end_to_end() -> None:
    artifact = load(GET_FILE)
    assert artifact.authentication.method == "aws_sigv4"

    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # PNG header + padding
    route = respx.get("https://s3.us-east-1.amazonaws.com/my-bucket/icon.png").mock(
        return_value=httpx.Response(
            200,
            content=payload,
            headers={"Content-Type": "image/png"},
        )
    )
    client = _client(artifact)
    try:
        result = client.dispatch(
            "s3_getobject",
            path_params={"bucket": "my-bucket", "key": "icon.png"},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess)
    # The body comes through as raw bytes per format=binary.
    assert isinstance(result.body, bytes)
    assert result.body == payload
    assert result.headers["content-type"] == "image/png"

    request = route.calls[0].request
    assert request.method == "GET"
    assert "Authorization" in request.headers
    auth = request.headers["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 ")
    assert "Credential=AKIDMOCK/" in auth
    assert "/us-east-1/s3/aws4_request" in auth
    assert "x-amz-date" in request.headers
    assert "x-amz-content-sha256" in request.headers


@respx.mock
def test_s3_getobject_404_returns_xml_error() -> None:
    artifact = load(GET_FILE)
    error_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Error>
  <Code>NoSuchKey</Code>
  <Message>The specified key does not exist.</Message>
  <Key>missing.txt</Key>
  <RequestId>ABCD1234</RequestId>
  <HostId>HOSTHASH</HostId>
</Error>"""
    respx.get("https://s3.us-east-1.amazonaws.com/my-bucket/missing.txt").mock(
        return_value=httpx.Response(
            404, content=error_xml, headers={"Content-Type": "application/xml"}
        )
    )
    client = _client(artifact)
    try:
        result = client.dispatch(
            "s3_getobject",
            path_params={"bucket": "my-bucket", "key": "missing.txt"},
        )
    finally:
        client.close()

    # 404 surfaces as DispatchError (not DispatchSuccess), with the body
    # carried in the canonical error shape's `raw` field.
    from uacp_prototype.dispatch.client import DispatchError

    assert isinstance(result, DispatchError)
    assert result.status == 404
    assert result.code == "not_found"


# ---------------------------------------------------------------------------
# s3.ListObjectsV2 — XML response with cursor pagination
# ---------------------------------------------------------------------------


_LIST_PAGE_1 = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>my-bucket</Name>
  <Prefix>logs/</Prefix>
  <MaxKeys>2</MaxKeys>
  <KeyCount>2</KeyCount>
  <IsTruncated>true</IsTruncated>
  <NextContinuationToken>1ueGcxLPRx1Tr/XYExHnhbYLgveDs2J/wm36Hy4vbOwM=</NextContinuationToken>
  <Contents>
    <Key>logs/2026-05-04-00.json</Key>
    <Size>1024</Size>
  </Contents>
  <Contents>
    <Key>logs/2026-05-04-01.json</Key>
    <Size>2048</Size>
  </Contents>
</ListBucketResult>"""

_LIST_PAGE_2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>my-bucket</Name>
  <Prefix>logs/</Prefix>
  <MaxKeys>2</MaxKeys>
  <KeyCount>1</KeyCount>
  <IsTruncated>false</IsTruncated>
  <Contents>
    <Key>logs/2026-05-04-02.json</Key>
    <Size>4096</Size>
  </Contents>
</ListBucketResult>"""


@respx.mock
def test_s3_listobjectsv2_xml_response_decodes_to_dict_end_to_end() -> None:
    artifact = load(LIST_FILE)
    respx.get("https://s3.us-east-1.amazonaws.com/my-bucket").mock(
        return_value=httpx.Response(
            200, content=_LIST_PAGE_1, headers={"Content-Type": "application/xml"}
        )
    )
    client = _client(artifact)
    try:
        result = client.dispatch(
            "s3_listobjectsv2",
            path_params={"bucket": "my-bucket"},
            query={"list-type": "2", "prefix": "logs/", "max-keys": 2},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess)
    # XML→dict per the §3.3 amendment
    assert "ListBucketResult" in result.body
    inner = result.body["ListBucketResult"]
    assert inner["Name"] == "my-bucket"
    assert inner["IsTruncated"] == "true"
    assert inner["NextContinuationToken"] == "1ueGcxLPRx1Tr/XYExHnhbYLgveDs2J/wm36Hy4vbOwM="
    # Multiple <Contents> elements → list
    assert isinstance(inner["Contents"], list)
    assert len(inner["Contents"]) == 2


@respx.mock
def test_s3_listobjectsv2_cursor_pagination_end_to_end() -> None:
    artifact = load(LIST_FILE)
    op = artifact.operations[0]
    assert op.pagination.pattern == "cursor"
    assert op.pagination.response_cursor_path == "$.ListBucketResult.NextContinuationToken"

    # Page 2's request includes the continuation-token query parameter.
    respx.get(
        "https://s3.us-east-1.amazonaws.com/my-bucket",
        params={
            "list-type": "2",
            "prefix": "logs/",
            "max-keys": "2",
            "continuation-token": "1ueGcxLPRx1Tr/XYExHnhbYLgveDs2J/wm36Hy4vbOwM=",
        },
    ).mock(
        return_value=httpx.Response(
            200, content=_LIST_PAGE_2, headers={"Content-Type": "application/xml"}
        )
    )
    # Page 1: no continuation-token in the query.
    respx.get(
        "https://s3.us-east-1.amazonaws.com/my-bucket",
        params={"list-type": "2", "prefix": "logs/", "max-keys": "2"},
    ).mock(
        return_value=httpx.Response(
            200, content=_LIST_PAGE_1, headers={"Content-Type": "application/xml"}
        )
    )

    client = _client(artifact)
    try:
        pages = list(
            dispatch_paginated(
                client,
                "s3_listobjectsv2",
                path_params={"bucket": "my-bucket"},
                query={"list-type": "2", "prefix": "logs/", "max-keys": 2},
            )
        )
    finally:
        client.close()

    assert len(pages) == 2
    assert all(isinstance(p, DispatchSuccess) for p in pages)
    # First page has the next cursor; second doesn't.
    assert pages[0].body["ListBucketResult"]["IsTruncated"] == "true"
    assert pages[1].body["ListBucketResult"]["IsTruncated"] == "false"


# ---------------------------------------------------------------------------
# Both .uacp files load
# ---------------------------------------------------------------------------


def test_aws_examples_load_clean() -> None:
    for name in ("s3-getobject.uacp", "s3-listobjectsv2.uacp"):
        path = EXAMPLES_DIR / name
        artifact = load(path)
        assert artifact.authentication.method == "aws_sigv4"
        assert len(artifact.operations) == 1
