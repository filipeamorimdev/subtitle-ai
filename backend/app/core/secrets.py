"""Secret encryption helpers (Fernet)."""

from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def load_or_create_fernet(key_path: Path) -> Fernet:
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        key = key_path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        key_path.chmod(0o600)
    return Fernet(key)


def encrypt_secret(fernet: Fernet, value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(fernet: Fernet, value: str | None) -> str | None:
    if value is None or value == "":
        return None
    try:
        return fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt secret") from exc


def mask_secret(value: str | None, visible_suffix: int = 4) -> str | None:
    if value is None or value == "":
        return None
    if len(value) <= visible_suffix:
        return "•" * len(value)
    prefix = value[:7] if value.startswith("sk-") else value[:4]
    return f"{prefix}{'•' * 12}{value[-visible_suffix:]}"
