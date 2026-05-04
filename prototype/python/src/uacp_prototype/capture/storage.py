"""Encrypted-at-rest persistence for capture artifacts per §6.3.

Composes on the existing :mod:`uacp_prototype.security.secrets`
infrastructure: every captured artifact is serialized to JSON, then
written through ``LocalKeyringStore.put()`` which performs §6.3
envelope encryption (per-blob DEK wrapped by the master KEK; AES-256-
GCM ciphertext on disk). The returned ``secret://<store>/<id>``
reference is the canonical pointer §3.12 mandates for the
``source.capture_ref`` provenance field.

Audit emission for the capture-stored event lives here so the
audit trail aligns with the persistence boundary, not the in-memory
finalization. Per §6.6 the event payload carries the storage URI +
request count + duration — never the captured cookies / auth values.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..security.secrets import (
    LocalKeyringStore,
    SecretResolutionError,
    SecretResolver,
    SecretURI,
)
from .recorder import (
    DEFAULT_UACP_DIR,
    CaptureArtifact,
    CaptureError,
)


log = logging.getLogger("uacp.capture")


SUPPORTED_STORES: tuple[str, ...] = ("local-keyring",)


@dataclass(frozen=True)
class StoredCapture:
    """Result of :func:`store_capture` — the canonical reference plus
    the parsed components for callers that want to log or display
    them. The ``ref`` field is the value §3.12 expects to land in
    each capture-sourced operation's ``source.capture_ref``."""

    ref: str
    store: str
    capture_id: str
    request_count: int
    duration_ms: float

    @property
    def uri(self) -> SecretURI:
        return SecretURI.parse(self.ref)


# ---------------------------------------------------------------------------
# store_capture
# ---------------------------------------------------------------------------


def store_capture(
    artifact: CaptureArtifact,
    *,
    secret_store: str = "local-keyring",
    storage_id: str | None = None,
    base_dir: Path | None = None,
) -> StoredCapture:
    """Persist ``artifact`` to ``secret_store`` under §6.3 envelope
    encryption. Returns the ``secret://<store>/<id>`` reference plus
    diagnostic metadata.

    By default the id is ``capture-<artifact.capture_id>`` —
    deterministic per §3.12, so storing the same capture twice yields
    the same id and the second write overwrites the first
    transparently. Callers (e.g., the ``capture-session`` CLI) MAY
    pass ``storage_id`` to honor an operator-chosen storage name
    verbatim; the resulting ref is ``secret://<store>/<storage_id>``.

    ``base_dir`` overrides the keyring base for tests. Production
    callers pass ``secret_store="local-keyring"`` and let the store
    use its default ``~/.uacp/secrets/`` directory.
    """
    if secret_store not in SUPPORTED_STORES:
        raise CaptureError(
            f"store_capture: secret_store {secret_store!r} not supported; "
            f"v1.1 prototype recognizes {sorted(SUPPORTED_STORES)}. The "
            f"§6.2 registry includes vault + aws-secrets-manager + "
            f"inline-encrypted but the prototype's capture path only "
            f"persists to local-keyring; switch the operator's secret "
            f"store explicitly when those land."
        )

    if not artifact.capture_id:
        raise CaptureError("store_capture: artifact has no capture_id")

    if storage_id is None:
        storage_id = f"capture-{artifact.capture_id}"

    store: LocalKeyringStore
    if base_dir is not None:
        store = LocalKeyringStore(base_dir=base_dir)
    else:
        store = LocalKeyringStore.default()

    payload = json.dumps(artifact.to_internal_json(), separators=(",", ":")).encode("utf-8")
    uri = SecretURI(store=secret_store, id=storage_id)
    store.put(uri, payload)

    duration_ms = 0.0
    if artifact.entries:
        first = min(e.started_at for e in artifact.entries)
        last = max(
            e.started_at.timestamp() + e.time_ms / 1000.0 for e in artifact.entries
        )
        duration_ms = max(0.0, (last - first.timestamp()) * 1000.0)

    ref = f"secret://{secret_store}/{storage_id}"
    _emit_audit_capture_stored(
        ref=ref,
        capture_id=artifact.capture_id,
        request_count=len(artifact.entries),
        duration_ms=duration_ms,
        initial_url=artifact.initial_url,
    )

    return StoredCapture(
        ref=ref,
        store=secret_store,
        capture_id=artifact.capture_id,
        request_count=len(artifact.entries),
        duration_ms=duration_ms,
    )


def load_capture(
    ref: str,
    *,
    base_dir: Path | None = None,
) -> CaptureArtifact:
    """Reload a previously-stored capture artifact from its
    ``secret://`` reference. Decrypts via the same envelope-encryption
    path used at write time. Stage 11.2's operation-synthesis pass
    consumes this path; the function lands here so the read side
    rounds out the §3.12 storage contract.
    """
    uri = SecretURI.parse(ref)
    if uri.store not in SUPPORTED_STORES:
        raise CaptureError(
            f"load_capture: store {uri.store!r} not supported; "
            f"v1.1 prototype recognizes {sorted(SUPPORTED_STORES)}."
        )
    store: LocalKeyringStore
    if base_dir is not None:
        store = LocalKeyringStore(base_dir=base_dir)
    else:
        store = LocalKeyringStore.default()
    try:
        plaintext = store.get(uri)
    except SecretResolutionError as e:
        raise CaptureError(f"load_capture: {e}") from e
    try:
        data = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CaptureError(f"load_capture: corrupt artifact at {ref}: {e}") from e
    return CaptureArtifact.from_internal_json(data)


# ---------------------------------------------------------------------------
# Audit emission
# ---------------------------------------------------------------------------


def _emit_audit_capture_stored(
    *,
    ref: str,
    capture_id: str,
    request_count: int,
    duration_ms: float,
    initial_url: str,
) -> None:
    """Per §6.6: log the persistence of a capture artifact at
    INFO level. Payload carries the storage reference + request
    count + duration + the URL pattern (host without query) but
    NEVER the captured cookies / auth header values. The
    underlying envelope encryption protects the payload at rest;
    the audit log is the operator-visible trail.
    """
    host = ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(initial_url)
        host = parsed.netloc
    except Exception:
        host = ""
    log.info(
        "capture stored: ref=%s id=%s requests=%d duration_ms=%.0f host=%s",
        ref,
        capture_id,
        request_count,
        duration_ms,
        host,
    )


__all__ = [
    "StoredCapture",
    "SUPPORTED_STORES",
    "load_capture",
    "store_capture",
]
