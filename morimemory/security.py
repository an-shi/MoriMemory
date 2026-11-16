"""Token authentication helpers and secret-at-rest encryption."""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    """Encrypt small configuration secrets with a key derived from the install secret."""

    def __init__(self, secret: str) -> None:
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""

    @staticmethod
    def mask(value: str, visible: int = 4) -> str:
        if not value:
            return ""
        if len(value) <= visible:
            return "•" * len(value)
        return f"••••{value[-visible:]}"


def secure_equals(left: str, right: str) -> bool:
    """Constant-time comparison for bearer tokens."""
    if not left or not right:
        return False
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
