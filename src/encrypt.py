"""Encrypted storage for grade snapshots.

The grade file (``grades_encrypted.json``) holds the full transcript, so
it must not be readable even if the repo leaks.  We derive the Fernet key
from the same credentials the action already injects — no separate key
to manage, and the file only opens for its owner.

The key derivation is deliberately identical to the legacy
``crawl_grades.py`` so existing ``grades_encrypted.json`` files keep
decrypting after the refactor.
"""

import base64
import hashlib
import json

from cryptography.fernet import Fernet

from src import config


def get_encryption_key() -> bytes:
    """Derive a 32-byte Fernet key from StuId | UISPsw | sender | smtp.

    SHA-256 of the pipe-joined secrets, then urlsafe-base64.  Changing any
    of the four inputs invalidates old snapshots (by design).
    """
    raw = "|".join(
        [config.STUDENT_ID, config.PASSWORD, config.SMTP_EMAIL, config.SMTP_PASSWORD]
    )
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def encrypt_data(data, key: bytes = None) -> bytes:
    """Encrypt an arbitrary JSON-serialisable value -> Fernet token bytes."""
    key = key or get_encryption_key()
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return Fernet(key).encrypt(payload)


def decrypt_data(encrypted_data: bytes, key: bytes = None):
    """Inverse of :func:`encrypt_data`; returns the original Python object."""
    key = key or get_encryption_key()
    return json.loads(Fernet(key).decrypt(encrypted_data).decode("utf-8"))


def load_grades(path: str = None):
    """Load + decrypt the snapshot at ``path``; return ``{}`` if absent."""
    path = path or config.GRADES_FILE
    try:
        with open(path, "rb") as f:
            return decrypt_data(f.read())
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[-] Could not decrypt {path}: {e}; starting fresh.")
        return {}


def save_grades(data, path: str = None) -> None:
    """Encrypt + write the snapshot atomically-ish (overwrite)."""
    path = path or config.GRADES_FILE
    with open(path, "wb") as f:
        f.write(encrypt_data(data))
    print(f"[+] Encrypted snapshot saved to {path}")
