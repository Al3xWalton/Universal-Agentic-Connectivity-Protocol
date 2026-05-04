"""AES-256-GCM envelope encryption per §6.3.

The prototype implements the §6.3 SHOULD floor — envelope encryption with
a master key (KEK) wrapping per-Connection data-encryption-keys (DEKs).

Layout:

  - Master key lives in `~/.uacp/master.key` (one file per machine).
    Generated on first run; permission 0600 (owner read/write only).
  - Per-Connection DEKs are encrypted with the master key and stored
    alongside the encrypted credential blob.
  - Credential blobs are encrypted with the DEK using AES-256-GCM.

The wire shape of an encrypted blob (used both for `inline-encrypted`
artifact entries per §6.2 and the local-keyring filesystem store
implementation in `secrets.py`):

  {
    "version": 1,
    "algorithm": "AES-256-GCM",
    "wrapped_dek_iv": "<base64>",
    "wrapped_dek_tag": "<base64>",
    "wrapped_dek": "<base64>",  # the DEK encrypted with the KEK
    "iv": "<base64>",
    "tag": "<base64>",
    "ciphertext": "<base64>"
  }

Key rotation (§6.3 SHOULD): rotating the master key re-wraps every
existing DEK without touching the underlying credential ciphertext.
The rotation function is exposed but not auto-scheduled; the
implementation's policy decides cadence.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Path layout. The default uses the user's home; tests inject custom paths.
DEFAULT_UACP_DIR = Path.home() / ".uacp"
DEFAULT_MASTER_KEY_FILE = DEFAULT_UACP_DIR / "master.key"

KEY_BYTES = 32  # AES-256
IV_BYTES = 12  # GCM nonce


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _ub64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


# ---------------------------------------------------------------------------
# Master key (KEK)
# ---------------------------------------------------------------------------


def get_or_create_master_key(*, path: Path = DEFAULT_MASTER_KEY_FILE) -> bytes:
    """Read the master key from disk, generating one on first call.

    The file is 0600 on first write and the parent directory is 0700.
    """
    if path.exists():
        data = path.read_bytes()
        if len(data) != KEY_BYTES:
            raise EncryptionError(
                f"master key at {path} has wrong length ({len(data)} bytes, expected {KEY_BYTES})"
            )
        return data
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        # Some filesystems (CI VMs) reject chmod; the file permission below
        # is the load-bearing one.
        pass
    key = secrets.token_bytes(KEY_BYTES)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def rotate_master_key(
    old_key: bytes,
    *,
    path: Path = DEFAULT_MASTER_KEY_FILE,
    rewrap_paths: list[Path] | None = None,
) -> bytes:
    """Generate a fresh master key, persist it, and re-wrap every encrypted
    blob in `rewrap_paths` so the credentials remain decryptable under the
    new key.

    Per §6.3, this rotates the KEK only; the underlying credential
    ciphertext is untouched. Each blob's wrapped DEK is unwrapped with
    the old key, re-wrapped with the new key, and written back.
    """
    new_key = secrets.token_bytes(KEY_BYTES)
    if rewrap_paths:
        for blob_path in rewrap_paths:
            if not blob_path.exists():
                continue
            blob = json.loads(blob_path.read_text())
            dek = _unwrap_dek(blob, old_key)
            wrapped = _wrap_dek(dek, new_key)
            blob.update(wrapped)
            blob_path.write_text(json.dumps(blob))
    path.write_bytes(new_key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return new_key


# ---------------------------------------------------------------------------
# DEK wrapping / unwrapping
# ---------------------------------------------------------------------------


def _wrap_dek(dek: bytes, master_key: bytes) -> dict[str, str]:
    iv = secrets.token_bytes(IV_BYTES)
    aesgcm = AESGCM(master_key)
    wrapped_with_tag = aesgcm.encrypt(iv, dek, associated_data=b"uacp/dek-wrap")
    # AESGCM.encrypt returns ciphertext || 16-byte tag. Split for serialization.
    wrapped_dek = wrapped_with_tag[:-16]
    tag = wrapped_with_tag[-16:]
    return {
        "wrapped_dek_iv": _b64(iv),
        "wrapped_dek_tag": _b64(tag),
        "wrapped_dek": _b64(wrapped_dek),
    }


def _unwrap_dek(blob: dict, master_key: bytes) -> bytes:
    aesgcm = AESGCM(master_key)
    iv = _ub64(blob["wrapped_dek_iv"])
    tag = _ub64(blob["wrapped_dek_tag"])
    wrapped = _ub64(blob["wrapped_dek"])
    return aesgcm.decrypt(iv, wrapped + tag, associated_data=b"uacp/dek-wrap")


# ---------------------------------------------------------------------------
# Encrypt / decrypt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EncryptedBlob:
    version: int
    algorithm: str
    wrapped_dek_iv: str
    wrapped_dek_tag: str
    wrapped_dek: str
    iv: str
    tag: str
    ciphertext: str

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "algorithm": self.algorithm,
            "wrapped_dek_iv": self.wrapped_dek_iv,
            "wrapped_dek_tag": self.wrapped_dek_tag,
            "wrapped_dek": self.wrapped_dek,
            "iv": self.iv,
            "tag": self.tag,
            "ciphertext": self.ciphertext,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "EncryptedBlob":
        return cls(
            version=int(data["version"]),  # type: ignore[arg-type]
            algorithm=str(data["algorithm"]),
            wrapped_dek_iv=str(data["wrapped_dek_iv"]),
            wrapped_dek_tag=str(data["wrapped_dek_tag"]),
            wrapped_dek=str(data["wrapped_dek"]),
            iv=str(data["iv"]),
            tag=str(data["tag"]),
            ciphertext=str(data["ciphertext"]),
        )


class EncryptionError(Exception):
    """Raised on encryption / decryption failures."""


def encrypt(plaintext: bytes, *, master_key: bytes) -> EncryptedBlob:
    """Encrypt `plaintext` under a fresh DEK that's wrapped with master_key."""
    dek = secrets.token_bytes(KEY_BYTES)
    iv = secrets.token_bytes(IV_BYTES)
    aesgcm = AESGCM(dek)
    cipher_with_tag = aesgcm.encrypt(iv, plaintext, associated_data=b"uacp/credential")
    ciphertext = cipher_with_tag[:-16]
    tag = cipher_with_tag[-16:]
    wrapped = _wrap_dek(dek, master_key)
    return EncryptedBlob(
        version=1,
        algorithm="AES-256-GCM",
        wrapped_dek_iv=wrapped["wrapped_dek_iv"],
        wrapped_dek_tag=wrapped["wrapped_dek_tag"],
        wrapped_dek=wrapped["wrapped_dek"],
        iv=_b64(iv),
        tag=_b64(tag),
        ciphertext=_b64(ciphertext),
    )


def decrypt(blob: EncryptedBlob, *, master_key: bytes) -> bytes:
    """Decrypt the blob using master_key to unwrap the DEK."""
    if blob.algorithm != "AES-256-GCM":
        raise EncryptionError(
            f"unsupported algorithm {blob.algorithm!r}; v1.0 prototype only supports AES-256-GCM"
        )
    if blob.version != 1:
        raise EncryptionError(f"unsupported blob version {blob.version}")
    try:
        dek = _unwrap_dek(blob.to_dict(), master_key)
    except Exception as e:
        raise EncryptionError(f"DEK unwrap failed: {e}") from e

    aesgcm = AESGCM(dek)
    iv = _ub64(blob.iv)
    tag = _ub64(blob.tag)
    ciphertext = _ub64(blob.ciphertext)
    try:
        return aesgcm.decrypt(iv, ciphertext + tag, associated_data=b"uacp/credential")
    except Exception as e:
        raise EncryptionError(f"ciphertext decryption failed: {e}") from e


__all__ = [
    "DEFAULT_MASTER_KEY_FILE",
    "DEFAULT_UACP_DIR",
    "EncryptedBlob",
    "EncryptionError",
    "decrypt",
    "encrypt",
    "get_or_create_master_key",
    "rotate_master_key",
]
