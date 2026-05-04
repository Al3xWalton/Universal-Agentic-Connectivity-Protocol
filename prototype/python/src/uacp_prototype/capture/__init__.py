"""Browser-instrumented session capture per §3.12 (added in v1.1).

Stage 11.0 specified the schema source; Stage 11.1 builds the recording
side: a CLI-driven flow that opens the user's target service in a real
browser, records every HTTP request the browser makes during the
session, persists the captures as HAR-format artifacts encrypted at
rest per §6.3, and produces a stable storage reference (a `secret://`
URI per §2.7) the next session (Stage 11.2) consumes for operation
synthesis.

Public surface:

  - ``BrowserRecorder`` — the recording engine. PlaywrightBackend is
    the default (gated on the optional ``capture`` extras);
    ScraplingBackend is the optional anti-bot-fingerprint-matching
    backend (gated on the ``stealth`` extras). Both lazy-import their
    underlying libraries so the prototype runs without either
    installed.
  - ``CaptureArtifact`` — the typed in-memory representation of a
    completed capture, with HAR 1.2-compliant + UACP-internal
    serialization.
  - ``store_capture`` — encrypted-at-rest persistence to the registered
    secret-store registry per §6.2; returns the canonical
    ``secret://<store>/<id>`` reference.
  - ``load_capture`` — round-trip companion that decrypts a stored
    artifact back to a ``CaptureArtifact``; consumed by Stage 11.2's
    operation-synthesis pass.

Operation synthesis from captures stays out of scope for Stage 11.1
(it lives in Stage 11.2). This module stops at "the captured artifact
is stored cleanly with a stable reference."
"""

from .recorder import (
    BrowserRecorder,
    CaptureArtifact,
    CaptureError,
    HarEntry,
    PlaywrightBackend,
    PlaywrightNotInstalledError,
    ScraplingBackend,
    ScraplingCaptureNotInstalledError,
    capture_id_for,
    recover_in_progress,
)
from .storage import (
    SUPPORTED_STORES,
    StoredCapture,
    load_capture,
    store_capture,
)

__all__ = [
    "BrowserRecorder",
    "CaptureArtifact",
    "CaptureError",
    "HarEntry",
    "PlaywrightBackend",
    "PlaywrightNotInstalledError",
    "ScraplingBackend",
    "ScraplingCaptureNotInstalledError",
    "SUPPORTED_STORES",
    "StoredCapture",
    "capture_id_for",
    "load_capture",
    "recover_in_progress",
    "store_capture",
]
