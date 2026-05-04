"""secret://<store>/<id> resolver per §2.7 + §6.2.

The prototype implements one store end-to-end (`local-keyring`,
filesystem-simulated) and stubs the rest with NotImplementedError so
artifacts that target Vault, AWS Secrets Manager, or inline-encrypted
fail clearly until the relevant store implementation lands.

`local-keyring` storage layout:

  ~/.uacp/secrets/<connection_id>.<field>.enc

Each file is a serialized EncryptedBlob (per encryption.py). The
`<connection_id>.<field>` naming convention is the prototype's; the
spec leaves the per-store identifier shape to the store.

Sub-field selection via `secret://<store>/<id>#<field>` (§6.2) is
supported for the local-keyring store: the `<id>` is the connection
identifier, the `<field>` is the secret-field name (access_token,
refresh_token, etc.).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .encryption import (
    DEFAULT_UACP_DIR,
    EncryptedBlob,
    decrypt,
    encrypt,
    get_or_create_master_key,
)


@dataclass(frozen=True)
class SecretURI:
    """Parsed `secret://<store>/<id>#<field>` reference."""

    store: str
    id: str
    field: str | None = None

    @classmethod
    def parse(cls, uri: str) -> "SecretURI":
        if not uri.startswith("secret://"):
            raise ValueError(f"not a secret URI: {uri!r}")
        rest = uri[len("secret://") :]
        if "/" not in rest:
            raise ValueError(f"secret URI missing identifier: {uri!r}")
        store, _, tail = rest.partition("/")
        if not store:
            raise ValueError(f"secret URI has empty store segment: {uri!r}")
        if "#" in tail:
            id_part, _, field = tail.partition("#")
        else:
            id_part, field = tail, None
        if not id_part:
            raise ValueError(f"secret URI has empty id segment: {uri!r}")
        return cls(store=store, id=id_part, field=field or None)


class SecretResolutionError(Exception):
    """Raised when a secret URI cannot be resolved to plaintext."""


# ---------------------------------------------------------------------------
# Per-store resolvers
# ---------------------------------------------------------------------------


class StoreResolver(Protocol):
    """Each registered store implements this interface."""

    def get(self, uri: SecretURI) -> bytes: ...

    def put(self, uri: SecretURI, value: bytes) -> None: ...

    def delete(self, uri: SecretURI) -> None: ...


@dataclass
class LocalKeyringStore:
    """Filesystem-simulated `local-keyring` store.

    `~/.uacp/secrets/<connection_id>.<field>.enc` per entry; each file is
    an EncryptedBlob serialized as JSON. The master key for the envelope
    encryption lives at `~/.uacp/master.key` (encryption.py).

    Per §6.2 the prototype's local-keyring would normally route through
    macOS Keychain / Windows Credential Manager / Linux Secret Service;
    the filesystem simulation lets the prototype be unit-tested without
    a real keyring and lets integration tests run on CI machines that
    don't have one.
    """

    base_dir: Path

    @classmethod
    def default(cls) -> "LocalKeyringStore":
        return cls(base_dir=DEFAULT_UACP_DIR / "secrets")

    def _path(self, uri: SecretURI) -> Path:
        if uri.field is None:
            return self.base_dir / f"{uri.id}.enc"
        return self.base_dir / f"{uri.id}.{uri.field}.enc"

    def get(self, uri: SecretURI) -> bytes:
        path = self._path(uri)
        if not path.exists():
            raise SecretResolutionError(
                f"local-keyring: no entry at {path} for URI secret://local-keyring/{uri.id}"
                + (f"#{uri.field}" if uri.field else "")
            )
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise SecretResolutionError(f"local-keyring: corrupted blob at {path}: {e}") from e
        master_key = get_or_create_master_key()
        try:
            blob = EncryptedBlob.from_dict(data)
            return decrypt(blob, master_key=master_key)
        except Exception as e:
            raise SecretResolutionError(
                f"local-keyring: decryption failed for {path}: {e}"
            ) from e

    def put(self, uri: SecretURI, value: bytes) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        master_key = get_or_create_master_key()
        blob = encrypt(value, master_key=master_key)
        path = self._path(uri)
        path.write_text(json.dumps(blob.to_dict()))
        # File contains ciphertext only; permissions 0600 anyway as defense
        # in depth in case anyone misreads the directory.
        try:
            import os

            os.chmod(path, 0o600)
        except OSError:
            pass

    def delete(self, uri: SecretURI) -> None:
        path = self._path(uri)
        if path.exists():
            path.unlink()


def _stub_store(name: str, *, stage_name: str) -> StoreResolver:
    """Build a stub StoreResolver that raises NotImplementedError on use."""

    class StubStore:
        def get(self, uri: SecretURI) -> bytes:
            raise NotImplementedError(
                f"{name} store is implemented in {stage_name}; this stub is "
                f"intentional in Stage 8a. To use this prototype, point the "
                f"artifact's secret:// URIs at the local-keyring store."
            )

        def put(self, uri: SecretURI, value: bytes) -> None:
            raise NotImplementedError(
                f"{name} store is implemented in {stage_name}; this stub is "
                f"intentional in Stage 8a."
            )

        def delete(self, uri: SecretURI) -> None:
            raise NotImplementedError(
                f"{name} store is implemented in {stage_name}; this stub is "
                f"intentional in Stage 8a."
            )

    return StubStore()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SecretResolver:
    """Top-level resolver. Routes `secret://<store>/<id>` URIs to the
    appropriate StoreResolver.

    The default registry covers the four registered v1.0 stores from §6.2:
      - `local-keyring`: implemented end-to-end.
      - `vault`: stub for Stage 8c+ (HashiCorp Vault integration).
      - `aws-secrets-manager`: stub for Stage 8c (AWS S3 session).
      - `inline-encrypted`: implemented when the artifact carries the
        encrypted blob (resolution looks up by id in
        encrypted_secrets[id]).
    """

    def __init__(
        self,
        *,
        local_keyring: LocalKeyringStore | None = None,
        encrypted_secrets: dict | None = None,
        custom_stores: dict[str, StoreResolver] | None = None,
    ) -> None:
        self._stores: dict[str, StoreResolver] = {}
        self._stores["local-keyring"] = local_keyring or LocalKeyringStore.default()
        self._stores["vault"] = _stub_store("vault", stage_name="a future provider session")
        self._stores["aws-secrets-manager"] = _stub_store(
            "aws-secrets-manager", stage_name="Stage 8c (AWS S3 session)"
        )
        self._inline_encrypted = encrypted_secrets or {}
        if custom_stores:
            self._stores.update(custom_stores)

    def resolve(self, uri: str | SecretURI) -> bytes:
        parsed = uri if isinstance(uri, SecretURI) else SecretURI.parse(uri)
        if parsed.store == "inline-encrypted":
            return self._resolve_inline_encrypted(parsed)
        if parsed.store not in self._stores:
            raise SecretResolutionError(
                f"unknown store {parsed.store!r}; v1.0 registered stores are "
                f"local-keyring, vault, aws-secrets-manager, inline-encrypted "
                f"(per §6.2). Implementations MAY register additional stores via §2.8."
            )
        return self._stores[parsed.store].get(parsed)

    def store(self, uri: str | SecretURI, value: bytes) -> None:
        parsed = uri if isinstance(uri, SecretURI) else SecretURI.parse(uri)
        if parsed.store == "inline-encrypted":
            raise SecretResolutionError(
                "inline-encrypted is read-only; the encrypted blob lives in the "
                "artifact and is written by the artifact's author"
            )
        if parsed.store not in self._stores:
            raise SecretResolutionError(f"unknown store {parsed.store!r}")
        self._stores[parsed.store].put(parsed, value)

    def delete(self, uri: str | SecretURI) -> None:
        parsed = uri if isinstance(uri, SecretURI) else SecretURI.parse(uri)
        if parsed.store == "inline-encrypted":
            raise SecretResolutionError("inline-encrypted is read-only")
        if parsed.store not in self._stores:
            raise SecretResolutionError(f"unknown store {parsed.store!r}")
        self._stores[parsed.store].delete(parsed)

    def _resolve_inline_encrypted(self, uri: SecretURI) -> bytes:
        if uri.id not in self._inline_encrypted:
            raise SecretResolutionError(
                f"inline-encrypted blob {uri.id!r} not found in artifact"
            )
        entry = self._inline_encrypted[uri.id]
        # entry is the EncryptedSecret pydantic model from spec/models.py;
        # its key_ref points at another store. Recursive inline-encrypted
        # was rejected at validation per §6.2 / spec/models.py.
        key_ref = entry.key_ref if hasattr(entry, "key_ref") else entry["key_ref"]
        master_key = self.resolve(key_ref)
        # Inline-encrypted blobs use a different shape than EncryptedBlob —
        # they're a single ciphertext encrypted directly with the resolved
        # key, no DEK wrapping. Per §6.2 the artifact carries ciphertext +
        # iv + tag and the resolved key_ref decrypts directly.
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import base64

        ciphertext = base64.b64decode(
            entry.ciphertext if hasattr(entry, "ciphertext") else entry["ciphertext"]
        )
        iv = base64.b64decode(entry.iv if hasattr(entry, "iv") else entry["iv"])
        tag = base64.b64decode(entry.tag if hasattr(entry, "tag") else entry["tag"])
        aesgcm = AESGCM(master_key)
        try:
            return aesgcm.decrypt(iv, ciphertext + tag, associated_data=b"uacp/inline")
        except Exception as e:
            raise SecretResolutionError(f"inline-encrypted decryption failed: {e}") from e


def make_credential_resolver(
    secret_resolver: SecretResolver,
    *,
    refs: dict[str, str],
) -> Callable[[], dict[str, str]]:
    """Build a credential_resolver suitable for DispatchClient.

    Resolves each `secret://` URI in `refs` to plaintext at every call,
    returning a dict of {field_name: plaintext}. Per §6.2 resolution
    happens at dispatch time, and the result is consumed by the
    AuthMethod's apply() within a single dispatch lifetime — the dict
    is rebuilt every call rather than cached, satisfying §6.2's
    no-cache-beyond-dispatch rule at the granularity of the resolver.
    """

    def resolve_now() -> dict[str, str]:
        out: dict[str, str] = {}
        for field_name, uri in refs.items():
            value = secret_resolver.resolve(uri)
            out[field_name] = value.decode("utf-8")
        return out

    return resolve_now


__all__ = [
    "LocalKeyringStore",
    "SecretResolutionError",
    "SecretResolver",
    "SecretURI",
    "StoreResolver",
    "make_credential_resolver",
]
