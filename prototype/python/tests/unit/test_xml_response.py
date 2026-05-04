"""Tests for XML and binary response decoding per the §3.3 amendment.

Stage 8c validation against S3 surfaced that UACP's response shape
modelled bodies as JSON-only. The amendment adds a ``format``
discriminator on response body objects: ``json`` (default), ``xml``,
``binary``, ``text``. The dispatcher decodes per the format.

These tests cover both the pure-function decoder and the §3.4 cursor-
pagination interaction with XML-shaped responses (S3's
ListObjectsV2 returns NextContinuationToken inside an XML envelope).
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

import pytest

from uacp_prototype.dispatch.body_format import decode_response_body, xml_to_dict
from uacp_prototype.dispatch.envelope import resolve_jsonpath


# ---------------------------------------------------------------------------
# xml_to_dict — conversion rules
# ---------------------------------------------------------------------------


def test_leaf_element_returns_text() -> None:
    el = ET.fromstring("<Key>hello</Key>")
    assert xml_to_dict(el) == "hello"


def test_leaf_element_with_attribute_returns_dict() -> None:
    el = ET.fromstring('<Key id="42">hello</Key>')
    assert xml_to_dict(el) == {"@id": "42", "#text": "hello"}


def test_element_with_children_returns_dict() -> None:
    el = ET.fromstring("<Object><Key>file.txt</Key><Size>1024</Size></Object>")
    assert xml_to_dict(el) == {"Key": "file.txt", "Size": "1024"}


def test_element_with_repeated_children_returns_list() -> None:
    el = ET.fromstring(
        "<Contents><Object>a</Object><Object>b</Object><Object>c</Object></Contents>"
    )
    assert xml_to_dict(el) == {"Object": ["a", "b", "c"]}


def test_element_with_namespace_strips_namespace() -> None:
    el = ET.fromstring(
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<Name>my-bucket</Name></ListBucketResult>"
    )
    out = xml_to_dict(el)
    assert out == {"Name": "my-bucket"}


def test_whitespace_only_text_dropped() -> None:
    el = ET.fromstring("<Outer>  \n  <Inner>x</Inner>  \n  </Outer>")
    assert xml_to_dict(el) == {"Inner": "x"}


# ---------------------------------------------------------------------------
# decode_response_body — format dispatching
# ---------------------------------------------------------------------------


def test_decode_binary_returns_bytes() -> None:
    payload = b"\x00\x01\x02\xff"
    assert decode_response_body(payload, format="binary", media_type="image/png") == payload


def test_decode_text_returns_string() -> None:
    payload = b"hello world"
    assert decode_response_body(payload, format="text", media_type="text/plain") == "hello world"


def test_decode_xml_returns_dict() -> None:
    payload = b'<Result><Status>OK</Status></Result>'
    out = decode_response_body(payload, format="xml", media_type="application/xml")
    assert out == {"Result": {"Status": "OK"}}


def test_decode_json_returns_parsed() -> None:
    payload = b'{"ok": true, "ts": "1.0"}'
    out = decode_response_body(payload, format="json", media_type="application/json")
    assert out == {"ok": True, "ts": "1.0"}


def test_decode_default_json_when_media_type_application_json() -> None:
    """No format declared → infer from media_type."""
    payload = b'{"ok": true}'
    out = decode_response_body(payload, format=None, media_type="application/json")
    assert out == {"ok": True}


def test_decode_default_xml_when_media_type_ends_xml() -> None:
    payload = b"<Root><A>1</A></Root>"
    out = decode_response_body(payload, format=None, media_type="application/xml")
    assert out == {"Root": {"A": "1"}}


def test_decode_default_text_when_text_prefix() -> None:
    payload = b"hello"
    out = decode_response_body(payload, format=None, media_type="text/plain")
    assert out == "hello"


def test_decode_unknown_falls_back_to_bytes() -> None:
    payload = b"\xff\xff\xff"
    out = decode_response_body(payload, format=None, media_type="application/octet-stream")
    assert out == payload


def test_decode_malformed_json_falls_back_to_bytes() -> None:
    payload = b"not json"
    out = decode_response_body(payload, format="json", media_type="application/json")
    # malformed → returns raw bytes per the fallback rule
    assert out == payload


# ---------------------------------------------------------------------------
# Realistic S3 ListObjectsV2 response — round-trip
# ---------------------------------------------------------------------------


_S3_LIST_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
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
    <ETag>"abc123"</ETag>
  </Contents>
  <Contents>
    <Key>logs/2026-05-04-01.json</Key>
    <Size>2048</Size>
    <ETag>"def456"</ETag>
  </Contents>
</ListBucketResult>"""


def test_s3_list_response_decodes_to_expected_shape() -> None:
    out = decode_response_body(
        _S3_LIST_RESPONSE, format="xml", media_type="application/xml"
    )
    # Top-level wraps the root tag (namespace-stripped)
    assert "ListBucketResult" in out
    inner = out["ListBucketResult"]
    assert inner["Name"] == "my-bucket"
    assert inner["IsTruncated"] == "true"
    assert inner["NextContinuationToken"] == "1ueGcxLPRx1Tr/XYExHnhbYLgveDs2J/wm36Hy4vbOwM="
    # Two <Contents> children → list
    assert isinstance(inner["Contents"], list)
    assert len(inner["Contents"]) == 2
    assert inner["Contents"][0]["Key"] == "logs/2026-05-04-00.json"
    assert inner["Contents"][1]["Size"] == "2048"


def test_s3_list_cursor_extraction_via_jsonpath() -> None:
    """The §3.4 cursor pagination's response_cursor_path is a JSONPath
    in the §3.4 minimal subset. After XML→dict conversion, the same
    JSONPath subset works against the dict form. The cursor at
    $.ListBucketResult.NextContinuationToken extracts the
    continuation token cleanly."""
    out = decode_response_body(
        _S3_LIST_RESPONSE, format="xml", media_type="application/xml"
    )
    cursor = resolve_jsonpath(out, "$.ListBucketResult.NextContinuationToken")
    assert cursor == "1ueGcxLPRx1Tr/XYExHnhbYLgveDs2J/wm36Hy4vbOwM="


def test_s3_list_cursor_absent_resolves_none() -> None:
    """When IsTruncated is false, S3 omits NextContinuationToken; the
    cursor JSONPath resolves to None and the §4.4 cursor loop
    terminates."""
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>my-bucket</Name>
  <IsTruncated>false</IsTruncated>
</ListBucketResult>"""
    out = decode_response_body(payload, format="xml", media_type="application/xml")
    cursor = resolve_jsonpath(out, "$.ListBucketResult.NextContinuationToken")
    assert cursor is None


# ---------------------------------------------------------------------------
# Spec-loader: format field validates
# ---------------------------------------------------------------------------


def test_spec_accepts_format_xml() -> None:
    from uacp_prototype.spec.loader import load_dict
    from uacp_prototype.spec.models import UACPArtifact

    raw = {
        "$schema": "https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.0.0/schemas/uacp.json",
        "authentication": {"method": "x-test"},
        "dispatch": {"base_url": "https://example.com"},
        "operations": [
            {
                "id": "list_x",
                "summary": "List.",
                "request": {"method": "GET", "path": "/list"},
                "response": {
                    "200": {
                        "description": "XML body",
                        "body": {"media_type": "application/xml", "format": "xml"},
                    }
                },
            }
        ],
    }
    art = load_dict(raw)
    assert isinstance(art, UACPArtifact)
    assert art.operations[0].response["200"].body["format"] == "xml"


def test_spec_accepts_format_binary_without_schema() -> None:
    """Binary format MAY omit schema; the body is opaque to validation."""
    from uacp_prototype.spec.loader import load_dict

    raw = {
        "$schema": "https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.0.0/schemas/uacp.json",
        "authentication": {"method": "x-test"},
        "dispatch": {"base_url": "https://example.com"},
        "operations": [
            {
                "id": "get_obj",
                "summary": "Get.",
                "request": {"method": "GET", "path": "/o"},
                "response": {
                    "200": {
                        "description": "binary body",
                        "body": {"media_type": "application/octet-stream", "format": "binary"},
                    }
                },
            }
        ],
    }
    art = load_dict(raw)
    assert art.operations[0].response["200"].body["format"] == "binary"


def test_spec_rejects_unknown_format() -> None:
    from uacp_prototype.spec.loader import load_dict
    from uacp_prototype.spec.schema import SpecValidationError

    raw = {
        "$schema": "https://raw.githubusercontent.com/Al3xWalton/Universal-Agentic-Connectivity-Protocol/v1.0.0/schemas/uacp.json",
        "authentication": {"method": "x-test"},
        "dispatch": {"base_url": "https://example.com"},
        "operations": [
            {
                "id": "x",
                "summary": "x",
                "request": {"method": "GET", "path": "/"},
                "response": {
                    "200": {
                        "description": "x",
                        "body": {"media_type": "x/y", "format": "yaml"},
                    }
                },
            }
        ],
    }
    with pytest.raises(SpecValidationError):
        load_dict(raw)
