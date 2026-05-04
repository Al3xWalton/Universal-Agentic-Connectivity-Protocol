"""Response body format dispatching per the §3.3 amendment.

Stage 8c validation against AWS S3 surfaced that UACP's response shape
modelled bodies as JSON Schema, but S3 returns:

  - **Binary** content from GetObject (the file bytes plus a
    Content-Type header that names the actual MIME type).
  - **XML** content from ListObjectsV2 (and most S3 metadata APIs).

This module provides format-aware decoding:

  - JSON (default for media_type ``application/json`` or unspecified):
    parse via httpx's JSON decoder.
  - XML: parse via stdlib ``xml.etree.ElementTree`` into a dict. The
    conversion is documented below; the produced dict can be queried
    by §3.4's JSONPath subset (which is how cursor pagination over
    XML response bodies works).
  - Binary: return raw bytes verbatim.
  - Text: return a decoded string.

The XML→dict conversion is intentionally simple and stable: an element
becomes a dict; child elements become keys (multiple children with the
same tag become a list); attributes become ``@attr``-prefixed keys;
text content becomes ``#text``; the XML namespace prefix is stripped
from tag names. This is one round-trip-stable shape sufficient to
express S3's response envelopes and most XML-shaped APIs without
introducing an external dependency.
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree as ET


__all__ = [
    "decode_response_body",
    "xml_to_dict",
]


_NS_TAG_RE = re.compile(r"^\{[^}]+\}")


def _strip_namespace(tag: str) -> str:
    return _NS_TAG_RE.sub("", tag)


def xml_to_dict(element: ET.Element) -> dict[str, Any] | str:
    """Convert an ElementTree element to a dict (or a bare string for
    leaf elements with no attributes and only text content).

    Conversion rules:

    - Element name (with XML namespace stripped) is the dict key.
    - Attributes become ``@<attr>`` keys at the element's level.
    - Child elements become nested dict entries. When multiple
      children share the same tag, the value is a list.
    - Text content of a leaf element (no children, no attributes)
      becomes a bare string. Text content of a non-leaf element is
      stored under ``#text`` if non-empty.
    - Whitespace-only text is dropped.
    """
    children = list(element)
    text = (element.text or "").strip()
    attribs = {f"@{_strip_namespace(k)}": v for k, v in element.attrib.items()}

    if not children and not attribs:
        return text

    out: dict[str, Any] = dict(attribs)
    if text:
        out["#text"] = text

    for child in children:
        ckey = _strip_namespace(child.tag)
        cval = xml_to_dict(child)
        if ckey in out:
            existing = out[ckey]
            if isinstance(existing, list):
                existing.append(cval)
            else:
                out[ckey] = [existing, cval]
        else:
            out[ckey] = cval
    return out


def _parse_xml(payload: bytes) -> dict[str, Any]:
    """Parse XML bytes into a single-key dict where the key is the root
    element's tag (namespace-stripped) and the value is the
    xml_to_dict conversion.
    """
    text = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else str(payload)
    root = ET.fromstring(text)
    return {_strip_namespace(root.tag): xml_to_dict(root)}


def decode_response_body(
    payload: bytes,
    *,
    format: str | None,
    media_type: str | None,
) -> Any:
    """Decode a response body per the explicit ``format`` field if given,
    otherwise per ``media_type``, with safe fallbacks.

    Returns:

    - ``bytes`` when ``format == 'binary'``.
    - ``str`` when ``format == 'text'``.
    - ``dict`` (XML→dict per ``xml_to_dict``) when ``format == 'xml'``.
    - parsed JSON (any) when ``format == 'json'`` or ``format`` is None
      and the media type indicates JSON.
    - The raw bytes when no format / media type indicates a parser and
      the body isn't recognizable.
    """
    if format == "binary":
        return payload
    if format == "text":
        return payload.decode("utf-8", errors="replace")
    if format == "xml":
        return _parse_xml(payload)
    if format == "json" or (format is None and (media_type or "").startswith("application/json")):
        try:
            import json

            return json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return payload
    if format is None and (media_type or "").endswith("xml"):
        try:
            return _parse_xml(payload)
        except ET.ParseError:
            return payload
    if format is None and (media_type or "").startswith("text/"):
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            return payload
    # Unknown / unspecified — return bytes verbatim.
    return payload
