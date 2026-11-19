"""Reversible encryption for stored upstream credentials.

Machine/portal passwords have to be replayed to the vendor API, so they
cannot be hashed - they are encrypted with Fernet (AES-128-CBC + HMAC)
under a key supplied in ATT_SECRET_KEY and never written to the database
in plaintext.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretError(RuntimeError):
    """Raised when credentials cannot be sealed or opened."""


class SecretBox:
    def __init__(self, key: str) -> None:
        if not key:
            raise SecretError(
                "ATT_SECRET_KEY is not set - run 'attendance keygen' and put "
                "the value in the service environment file"
            )
        try:
            self._f = Fernet(key.encode("utf-8"))
        except Exception as exc:  # malformed key
            raise SecretError(f"ATT_SECRET_KEY is not a valid Fernet key: {exc}") from exc

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("utf-8")

    def seal(self, plaintext: str) -> str:
        if plaintext == "":
            return ""
        return self._f.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def open(self, token: str) -> str:
        if token == "":
            return ""
        try:
            return self._f.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretError(
                "stored credential could not be decrypted - ATT_SECRET_KEY "
                "has changed since it was saved; re-enter the password"
            ) from exc
