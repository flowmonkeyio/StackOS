"""Versioned, authenticated whole-archive encryption with an independent recipient."""

import base64
import hashlib
import json
import os
import struct
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import limits
from .filesystem import CHUNK, identity, open_regular
from .models import BackupError

MAGIC = b"STACKOSB2\n"
MAX_HEADER = 4096
ALGORITHM = "X25519-HKDF-SHA256-AES-256-GCM"
DOMAIN = b"stackos.local-backup.v2"


def fingerprint(key: X25519PublicKey) -> str:
    return hashlib.sha256(
        key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).hexdigest()


def _derive_key(secret: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=DOMAIN).derive(secret)


def encrypt_payload(source: Path, output: BinaryIO, recipient: X25519PublicKey) -> dict[str, str]:
    ephemeral = X25519PrivateKey.generate()
    salt, nonce = os.urandom(32), os.urandom(12)
    header = {
        "version": 2,
        "algorithm": ALGORITHM,
        "ephemeral_public_key": base64.b64encode(
            ephemeral.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ).decode("ascii"),
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "recipient_fingerprint": fingerprint(recipient),
    }
    serialized = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("ascii")
    aad = MAGIC + struct.pack(">I", len(serialized)) + serialized
    cipher_key = _derive_key(ephemeral.exchange(recipient), salt)
    encryptor = Cipher(algorithms.AES(cipher_key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)
    output.write(aad)
    with source.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            output.write(encryptor.update(chunk))
    output.write(encryptor.finalize())
    output.write(encryptor.tag)
    return {
        "format": "stackos.local-backup.v2",
        "algorithm": ALGORITHM,
        "recipient_fingerprint": fingerprint(recipient),
    }


def _read_header(stream: BinaryIO) -> tuple[dict[str, str], bytes]:
    prefix = stream.read(len(MAGIC) + 4)
    if len(prefix) != len(MAGIC) + 4 or not prefix.startswith(MAGIC):
        raise BackupError("unsupported_archive", "Use an encrypted StackOS v2 backup.")
    length = struct.unpack(">I", prefix[-4:])[0]
    if not 1 <= length <= MAX_HEADER:
        raise BackupError("invalid_archive", "The encrypted backup header is invalid.")
    raw = stream.read(length)
    try:
        header = json.loads(raw)
        required = {
            "version",
            "algorithm",
            "ephemeral_public_key",
            "salt",
            "nonce",
            "recipient_fingerprint",
        }
        if set(header) != required or header["version"] != 2 or header["algorithm"] != ALGORITHM:
            raise ValueError("unsupported header")
        if any(not isinstance(header[key], str) for key in required - {"version"}):
            raise ValueError("invalid header values")
        if len(header["recipient_fingerprint"]) != 64:
            raise ValueError("invalid fingerprint")
    except (ValueError, TypeError, KeyError) as exc:
        raise BackupError("invalid_archive", "The encrypted backup header is invalid.") from exc
    if len(raw) != length:
        raise BackupError("invalid_archive", "The encrypted backup is truncated.")
    return header, prefix + raw


def archive_header(path: Path) -> dict[str, str]:
    fd = open_regular(path)
    with os.fdopen(fd, "rb") as stream:
        header, _ = _read_header(stream)
        return header


def decrypt_payload(
    source: Path, destination: Path, private_key: X25519PrivateKey
) -> dict[str, str]:
    """Quarantine plaintext; caller must wait for successful authentication before using it."""
    fd = open_regular(source)
    try:
        before = identity(os.fstat(fd))
        with os.fdopen(fd, "rb", closefd=False) as stream:
            header, aad = _read_header(stream)
            if header["recipient_fingerprint"] != fingerprint(private_key.public_key()):
                raise BackupError(
                    "authentication_failed", "The recovery key does not match this backup."
                )
            try:
                salt = base64.b64decode(header["salt"], validate=True)
                nonce = base64.b64decode(header["nonce"], validate=True)
                ephemeral_bytes = base64.b64decode(header["ephemeral_public_key"], validate=True)
                if len(salt) != 32 or len(nonce) != 12 or len(ephemeral_bytes) != 32:
                    raise ValueError("invalid cryptographic field")
                ephemeral = X25519PublicKey.from_public_bytes(ephemeral_bytes)
                key = _derive_key(private_key.exchange(ephemeral), salt)
                size = os.fstat(fd).st_size
                cipher_size = size - len(aad) - 16
                if not 1 <= cipher_size <= limits.MAX_ARCHIVE_BYTES:
                    raise ValueError("invalid ciphertext size")
                stream.seek(size - 16)
                tag = stream.read(16)
                stream.seek(len(aad))
                decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
                decryptor.authenticate_additional_data(aad)
                archive_digest = hashlib.sha256(aad)
                output_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(output_fd, "wb") as output:
                    remaining = cipher_size
                    while remaining:
                        chunk = stream.read(min(CHUNK, remaining))
                        if not chunk:
                            raise ValueError("truncated ciphertext")
                        remaining -= len(chunk)
                        archive_digest.update(chunk)
                        output.write(decryptor.update(chunk))
                    output.write(decryptor.finalize())
                    archive_digest.update(tag)
                    if identity(os.fstat(fd)) != before or identity(source.lstat()) != before:
                        raise ValueError("archive changed during recovery")
                    output.flush()
                    os.fsync(output.fileno())
            except (InvalidTag, ValueError) as exc:
                destination.unlink(missing_ok=True)
                raise BackupError(
                    "authentication_failed",
                    "The backup failed authentication or is damaged.",
                    "Use the matching recovery key and an intact backup.",
                ) from exc
            return {**header, "archive_sha256": archive_digest.hexdigest()}
    finally:
        os.close(fd)
