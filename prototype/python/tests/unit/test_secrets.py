"""Tests for the secret resolver + encryption-at-rest per §2.7 + §6.2 + §6.3."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from uacp_prototype.security.encryption import (
    EncryptedBlob,
    EncryptionError,
    decrypt,
    encrypt,
    get_or_create_master_key,
    rotate_master_key,
)
from uacp_prototype.security.secrets import (
    LocalKeyringStore,
    SecretResolutionError,
    SecretResolver,
    SecretURI,
    make_credential_resolver,
)


# ---------------------------------------------------------------------------
# SecretURI parsing
# ---------------------------------------------------------------------------


def test_uri_parse_basic() -> None:
    parsed = SecretURI.parse("secret://vault/path/to/secret")
    assert parsed.store == "vault"
    assert parsed.id == "path/to/secret"
    assert parsed.field is None


def test_uri_parse_with_fragment() -> None:
    parsed = SecretURI.parse("secret://aws-secrets-manager/my-secret#refresh_token")
    assert parsed.store == "aws-secrets-manager"
    assert parsed.id == "my-secret"
    assert parsed.field == "refresh_token"


def test_uri_parse_local_keyring() -> None:
    parsed = SecretURI.parse("secret://local-keyring/conn-1#access_token")
    assert parsed.store == "local-keyring"
    assert parsed.id == "conn-1"
    assert parsed.field == "access_token"


def test_uri_parse_rejects_non_secret_scheme() -> None:
    with pytest.raises(ValueError):
        SecretURI.parse("https://example.com/x")


def test_uri_parse_rejects_missing_id() -> None:
    with pytest.raises(ValueError):
        SecretURI.parse("secret://store-only/")


def test_uri_parse_rejects_empty_store() -> None:
    with pytest.raises(ValueError):
        SecretURI.parse("secret:///id")


# ---------------------------------------------------------------------------
# Master key generation + persistence
# ---------------------------------------------------------------------------


def test_master_key_generated_on_first_call(tmp_path: Path) -> None:
    key_path = tmp_path / "master.key"
    key1 = get_or_create_master_key(path=key_path)
    assert len(key1) == 32  # AES-256
    assert key_path.exists()
    # Second call returns the same key
    key2 = get_or_create_master_key(path=key_path)
    assert key1 == key2


def test_master_key_wrong_length_rejected(tmp_path: Path) -> None:
    key_path = tmp_path / "master.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(b"short")
    with pytest.raises(EncryptionError, match="wrong length"):
        get_or_create_master_key(path=key_path)


# ---------------------------------------------------------------------------
# Encrypt / decrypt round-trip
# ---------------------------------------------------------------------------


def test_round_trip_encrypts_and_decrypts() -> None:
    key = b"\x00" * 32
    plaintext = b"top-secret-token-value"
    blob = encrypt(plaintext, master_key=key)
    assert blob.algorithm == "AES-256-GCM"
    assert blob.version == 1
    out = decrypt(blob, master_key=key)
    assert out == plaintext


def test_decrypt_with_wrong_master_key_fails() -> None:
    key1 = b"\x00" * 32
    key2 = b"\x01" * 32
    blob = encrypt(b"x", master_key=key1)
    with pytest.raises(EncryptionError):
        decrypt(blob, master_key=key2)


def test_blob_serialization_round_trip() -> None:
    key = b"\x00" * 32
    blob = encrypt(b"data", master_key=key)
    as_dict = blob.to_dict()
    rebuilt = EncryptedBlob.from_dict(as_dict)
    assert decrypt(rebuilt, master_key=key) == b"data"


def test_decrypt_unsupported_algorithm_rejected() -> None:
    key = b"\x00" * 32
    blob = encrypt(b"x", master_key=key)
    bad = EncryptedBlob(
        version=blob.version,
        algorithm="DES",  # unsupported
        wrapped_dek_iv=blob.wrapped_dek_iv,
        wrapped_dek_tag=blob.wrapped_dek_tag,
        wrapped_dek=blob.wrapped_dek,
        iv=blob.iv,
        tag=blob.tag,
        ciphertext=blob.ciphertext,
    )
    with pytest.raises(EncryptionError, match="unsupported algorithm"):
        decrypt(bad, master_key=key)


# ---------------------------------------------------------------------------
# Tampering detection
# ---------------------------------------------------------------------------


def test_ciphertext_tampering_detected() -> None:
    key = b"\x00" * 32
    blob = encrypt(b"x", master_key=key)
    raw = base64.b64decode(blob.ciphertext)
    tampered = bytes([raw[0] ^ 0xFF]) + raw[1:] if raw else b"\x00" * 16
    bad = EncryptedBlob(
        version=blob.version,
        algorithm=blob.algorithm,
        wrapped_dek_iv=blob.wrapped_dek_iv,
        wrapped_dek_tag=blob.wrapped_dek_tag,
        wrapped_dek=blob.wrapped_dek,
        iv=blob.iv,
        tag=blob.tag,
        ciphertext=base64.b64encode(tampered).decode("ascii"),
    )
    with pytest.raises(EncryptionError):
        decrypt(bad, master_key=key)


# ---------------------------------------------------------------------------
# Master-key rotation (§6.3)
# ---------------------------------------------------------------------------


def test_rotate_master_key_rewraps_blobs(tmp_path: Path) -> None:
    key_path = tmp_path / "master.key"
    old_key = get_or_create_master_key(path=key_path)
    blob_path = tmp_path / "blob.json"
    plaintext = b"abc"
    blob = encrypt(plaintext, master_key=old_key)
    import json

    blob_path.write_text(json.dumps(blob.to_dict()))

    new_key = rotate_master_key(old_key, path=key_path, rewrap_paths=[blob_path])

    assert new_key != old_key
    rewrapped = EncryptedBlob.from_dict(json.loads(blob_path.read_text()))
    # Old key no longer works
    with pytest.raises(EncryptionError):
        decrypt(rewrapped, master_key=old_key)
    # New key works; ciphertext is unchanged (envelope encryption)
    assert decrypt(rewrapped, master_key=new_key) == plaintext
    assert rewrapped.ciphertext == blob.ciphertext  # ciphertext untouched
    assert rewrapped.iv == blob.iv  # IV untouched


# ---------------------------------------------------------------------------
# LocalKeyringStore (filesystem-simulated)
# ---------------------------------------------------------------------------


def test_local_keyring_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Redirect master key + secrets dir into tmp
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "uacp_prototype.security.encryption.DEFAULT_MASTER_KEY_FILE",
        tmp_path / ".uacp" / "master.key",
    )
    store = LocalKeyringStore(base_dir=tmp_path / ".uacp" / "secrets")

    uri = SecretURI(store="local-keyring", id="conn-1", field="access_token")
    store.put(uri, b"token-bytes-1")
    out = store.get(uri)
    assert out == b"token-bytes-1"

    # File exists with the expected name
    assert (tmp_path / ".uacp" / "secrets" / "conn-1.access_token.enc").exists()


def test_local_keyring_get_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "uacp_prototype.security.encryption.DEFAULT_MASTER_KEY_FILE",
        tmp_path / ".uacp" / "master.key",
    )
    store = LocalKeyringStore(base_dir=tmp_path / "secrets")
    with pytest.raises(SecretResolutionError, match="no entry"):
        store.get(SecretURI(store="local-keyring", id="conn-1", field="x"))


def test_local_keyring_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "uacp_prototype.security.encryption.DEFAULT_MASTER_KEY_FILE",
        tmp_path / ".uacp" / "master.key",
    )
    store = LocalKeyringStore(base_dir=tmp_path / "secrets")
    uri = SecretURI(store="local-keyring", id="conn-x", field="t")
    store.put(uri, b"v")
    store.delete(uri)
    with pytest.raises(SecretResolutionError):
        store.get(uri)


# ---------------------------------------------------------------------------
# SecretResolver registry — stub stores raise NotImplementedError
# ---------------------------------------------------------------------------


def test_resolver_routes_to_local_keyring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "uacp_prototype.security.encryption.DEFAULT_MASTER_KEY_FILE",
        tmp_path / ".uacp" / "master.key",
    )
    resolver = SecretResolver(
        local_keyring=LocalKeyringStore(base_dir=tmp_path / "secrets")
    )
    resolver.store("secret://local-keyring/conn-1#tok", b"abc")
    assert resolver.resolve("secret://local-keyring/conn-1#tok") == b"abc"


def test_resolver_vault_stub_raises() -> None:
    resolver = SecretResolver()
    with pytest.raises(NotImplementedError):
        resolver.resolve("secret://vault/path/to/x")


def test_resolver_aws_stub_raises() -> None:
    resolver = SecretResolver()
    with pytest.raises(NotImplementedError):
        resolver.resolve("secret://aws-secrets-manager/my-secret")


def test_resolver_unknown_store_raises() -> None:
    resolver = SecretResolver()
    with pytest.raises(SecretResolutionError, match="unknown store"):
        resolver.resolve("secret://made-up-store/x")


# ---------------------------------------------------------------------------
# make_credential_resolver
# ---------------------------------------------------------------------------


def test_make_credential_resolver_resolves_and_returns_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "uacp_prototype.security.encryption.DEFAULT_MASTER_KEY_FILE",
        tmp_path / ".uacp" / "master.key",
    )
    secret_resolver = SecretResolver(
        local_keyring=LocalKeyringStore(base_dir=tmp_path / "secrets")
    )
    secret_resolver.store("secret://local-keyring/conn-1#access_token", b"at-1")
    secret_resolver.store("secret://local-keyring/conn-1#refresh_token", b"rt-1")

    resolver = make_credential_resolver(
        secret_resolver,
        refs={
            "access_token": "secret://local-keyring/conn-1#access_token",
            "refresh_token": "secret://local-keyring/conn-1#refresh_token",
        },
    )
    creds = resolver()
    assert creds == {"access_token": "at-1", "refresh_token": "rt-1"}


# ---------------------------------------------------------------------------
# inline-encrypted resolution
# ---------------------------------------------------------------------------


def test_inline_encrypted_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An inline-encrypted blob's key_ref points at local-keyring; the
    resolver decrypts the inline ciphertext using the resolved key.
    """
    monkeypatch.setattr(
        "uacp_prototype.security.encryption.DEFAULT_MASTER_KEY_FILE",
        tmp_path / ".uacp" / "master.key",
    )
    # Stage the master key in local-keyring
    secret_resolver = SecretResolver(
        local_keyring=LocalKeyringStore(base_dir=tmp_path / "secrets")
    )
    inline_key = b"\x00" * 32
    secret_resolver.store("secret://local-keyring/inline-key#k", inline_key)

    # Build an inline-encrypted entry whose ciphertext was encrypted with
    # the inline key.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import secrets as _secrets

    iv = _secrets.token_bytes(12)
    aesgcm = AESGCM(inline_key)
    payload = b"inline-secret-value"
    cipher_with_tag = aesgcm.encrypt(iv, payload, associated_data=b"uacp/inline")
    ciphertext = cipher_with_tag[:-16]
    tag = cipher_with_tag[-16:]

    encrypted_secrets = {
        "blob1": {
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "algorithm": "AES-256-GCM",
            "key_ref": "secret://local-keyring/inline-key#k",
            "iv": base64.b64encode(iv).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii"),
        }
    }
    resolver_with_inline = SecretResolver(
        local_keyring=LocalKeyringStore(base_dir=tmp_path / "secrets"),
        encrypted_secrets=encrypted_secrets,
    )
    # Re-stage the inline-key into the new resolver's store
    resolver_with_inline.store("secret://local-keyring/inline-key#k", inline_key)

    out = resolver_with_inline.resolve("secret://inline-encrypted/blob1")
    assert out == payload
