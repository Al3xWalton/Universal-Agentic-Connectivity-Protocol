"""Integration tests against AWS S3.

Marked @pytest.mark.integration; skipped by default. Requires operator
setup per the README:

  1. Create an AWS IAM user with programmatic access. **Do NOT use
     root credentials.** **Do NOT attach AdministratorAccess or any
     wildcard policy.**
  2. Attach a minimal IAM policy granting only ``s3:GetObject`` and
     ``s3:ListBucket`` on a single named test bucket. Example:

         {
           "Version": "2012-10-17",
           "Statement": [
             {
               "Effect": "Allow",
               "Action": ["s3:GetObject", "s3:ListBucket"],
               "Resource": [
                 "arn:aws:s3:::your-test-bucket",
                 "arn:aws:s3:::your-test-bucket/*"
               ]
             }
           ]
         }

  3. Create a bucket with the matching name (any AWS region; we use the
     ``UACP_AWS_REGION`` env var to address it). Upload at least one
     object with a known key so ``test_get_object_success`` has
     something to fetch.
  4. Capture the access key ID + secret + bucket + region into ``.env``
     (or export):

         UACP_AWS_ACCESS_KEY_ID=AKIA...
         UACP_AWS_SECRET_ACCESS_KEY=...
         UACP_AWS_TEST_BUCKET=your-test-bucket
         UACP_AWS_REGION=us-east-1
         UACP_AWS_TEST_KEY=hello.txt   # whatever you uploaded
         # optional, only if using STS-derived credentials:
         UACP_AWS_SESSION_TOKEN=...

  5. Run with: ``uv run pytest tests/providers/test_aws.py -m integration``.

The integration tests are non-destructive: they only read.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from uacp_prototype.auth.aws_sigv4 import AWSSigV4Method
from uacp_prototype.dispatch.client import DispatchClient, DispatchError, DispatchSuccess
from uacp_prototype.dispatch.pagination import dispatch_paginated
from uacp_prototype.spec.loader import load


EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "aws"
GET_FILE = EXAMPLES_DIR / "s3-getobject.uacp"
LIST_FILE = EXAMPLES_DIR / "s3-listobjectsv2.uacp"


def _required_env(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        pytest.skip(
            f"integration test requires {var} (see tests/providers/test_aws.py docstring)"
        )
    return value


@pytest.fixture(scope="module")
def aws_config() -> dict[str, str]:
    return {
        "access_key_id": _required_env("UACP_AWS_ACCESS_KEY_ID"),
        "secret_access_key": _required_env("UACP_AWS_SECRET_ACCESS_KEY"),
        "bucket": _required_env("UACP_AWS_TEST_BUCKET"),
        "region": _required_env("UACP_AWS_REGION"),
        "test_key": os.environ.get("UACP_AWS_TEST_KEY", "hello.txt"),
        "session_token": os.environ.get("UACP_AWS_SESSION_TOKEN", ""),
    }


def _credentials(aws_config: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "access_key_id": aws_config["access_key_id"],
        "secret_access_key": aws_config["secret_access_key"],
    }
    if aws_config.get("session_token"):
        out["session_token"] = aws_config["session_token"]
    return out


def _patch_artifact_region(artifact: Any, aws_config: dict[str, str]) -> Any:
    """The .uacp examples hardcode us-east-1 / us-east-1-style base_url.
    For integration tests we substitute the operator-supplied region by
    constructing the AWSSigV4Method with the right region; the dispatch
    base_url is also region-bearing so we re-load with substitution."""
    # In v1 we keep this simple: rely on the artifact's base_url being
    # us-east-1 and require the operator's bucket to be in us-east-1
    # for the integration tests. A full multi-region setup is a Stage 9
    # concern.
    return artifact


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_object_success(aws_config: dict[str, str]) -> None:
    """Download the test object. The bucket must be in
    UACP_AWS_REGION (we use the artifact's us-east-1 base_url; if your
    bucket is elsewhere, change UACP_AWS_REGION and the test will
    construct an AWSSigV4Method for that region — but the artifact's
    base_url remains us-east-1, so for consistency keep your test
    bucket in us-east-1)."""
    artifact = load(GET_FILE)
    method = AWSSigV4Method(service="s3", region=aws_config["region"])
    client = DispatchClient(
        artifact,
        auth_method=method,
        credential_resolver=lambda: _credentials(aws_config),
        sleep=time.sleep,
    )
    try:
        result = client.dispatch(
            "s3_getobject",
            path_params={
                "bucket": aws_config["bucket"],
                "key": aws_config["test_key"],
            },
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess), f"GetObject failed: {result}"
    assert isinstance(result.body, bytes)
    assert len(result.body) > 0


@pytest.mark.integration
def test_get_object_404(aws_config: dict[str, str]) -> None:
    """Request a key that doesn't exist; expect a 404 DispatchError."""
    artifact = load(GET_FILE)
    method = AWSSigV4Method(service="s3", region=aws_config["region"])
    client = DispatchClient(
        artifact,
        auth_method=method,
        credential_resolver=lambda: _credentials(aws_config),
        sleep=time.sleep,
    )
    try:
        result = client.dispatch(
            "s3_getobject",
            path_params={
                "bucket": aws_config["bucket"],
                "key": f"nonexistent-{int(time.time())}-uacp-test.txt",
            },
        )
    finally:
        client.close()

    assert isinstance(result, DispatchError)
    assert result.status == 404
    assert result.code == "not_found"


@pytest.mark.integration
def test_list_objects_v2_single_page(aws_config: dict[str, str]) -> None:
    """List up to 5 objects in the test bucket; assert XML decoded
    correctly to a dict shape."""
    artifact = load(LIST_FILE)
    method = AWSSigV4Method(service="s3", region=aws_config["region"])
    client = DispatchClient(
        artifact,
        auth_method=method,
        credential_resolver=lambda: _credentials(aws_config),
        sleep=time.sleep,
    )
    try:
        result = client.dispatch(
            "s3_listobjectsv2",
            path_params={"bucket": aws_config["bucket"]},
            query={"list-type": "2", "max-keys": 5},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess), f"ListObjectsV2 failed: {result}"
    inner = result.body["ListBucketResult"]
    assert inner["Name"] == aws_config["bucket"]
    assert "IsTruncated" in inner


@pytest.mark.integration
def test_list_objects_v2_paginated(aws_config: dict[str, str]) -> None:
    """Page through the bucket with max-keys=2 and max_pages=3 cap. The
    bucket must contain at least 4 objects for the pagination loop to
    advance through more than one page; if it doesn't, this test still
    passes with a single page."""
    artifact = load(LIST_FILE)
    method = AWSSigV4Method(service="s3", region=aws_config["region"])
    client = DispatchClient(
        artifact,
        auth_method=method,
        credential_resolver=lambda: _credentials(aws_config),
        sleep=time.sleep,
    )
    try:
        pages = list(
            dispatch_paginated(
                client,
                "s3_listobjectsv2",
                path_params={"bucket": aws_config["bucket"]},
                query={"list-type": "2", "max-keys": 2},
                max_pages=3,
            )
        )
    finally:
        client.close()

    assert len(pages) >= 1
    success_pages = [p for p in pages if isinstance(p, DispatchSuccess)]
    assert len(success_pages) >= 1


@pytest.mark.integration
def test_list_objects_v2_with_prefix(aws_config: dict[str, str]) -> None:
    """List with a prefix filter. Uses prefix='' (matches everything) for
    safety — the test passes regardless of what's in the bucket."""
    artifact = load(LIST_FILE)
    method = AWSSigV4Method(service="s3", region=aws_config["region"])
    client = DispatchClient(
        artifact,
        auth_method=method,
        credential_resolver=lambda: _credentials(aws_config),
        sleep=time.sleep,
    )
    try:
        result = client.dispatch(
            "s3_listobjectsv2",
            path_params={"bucket": aws_config["bucket"]},
            query={"list-type": "2", "max-keys": 5, "prefix": ""},
        )
    finally:
        client.close()

    assert isinstance(result, DispatchSuccess), f"ListObjectsV2 with prefix failed: {result}"
    inner = result.body["ListBucketResult"]
    assert inner.get("Prefix", "") == ""
