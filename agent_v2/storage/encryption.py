"""
storage/encryption.py - Symmetric Fernet encryption helper using cryptography.
Safely encrypts and decrypts sensitive profile parameters, credentials, and tokens.
"""

from __future__ import annotations

import base64
import os
from typing import Optional, Union
from cryptography.fernet import Fernet, InvalidToken


class EncryptionManager:
    """Enterprise AES-128-CBC/Fernet encryption manager for local secrets."""

    def __init__(self, key: Optional[Union[str, bytes]] = None):
        if not key:
            # Check environment or generate a persistent local default
            key = os.getenv("APP_ENCRYPTION_KEY")
        if not key:
            key = Fernet.generate_key()
        elif isinstance(key, str):
            key = key.encode("utf-8")
            # If a raw 32-byte key is given, base64 urlsafe encode it
            if len(key) == 32 and b"=" not in key:
                key = base64.urlsafe_b64encode(key)

        self._fernet = Fernet(key)
        self._key = key

    @property
    def key_string(self) -> str:
        """Return the current base64-encoded key string."""
        return self._key.decode("utf-8") if isinstance(self._key, bytes) else str(self._key)

    @classmethod
    def generate_key(cls) -> str:
        """Generate a new secure Fernet key."""
        return Fernet.generate_key().decode("utf-8")

    def encrypt(self, plain_text: Union[str, bytes]) -> str:
        """Encrypt plain text or bytes and return a urlsafe token string."""
        if isinstance(plain_text, str):
            raw_bytes = plain_text.encode("utf-8")
        else:
            raw_bytes = plain_text

        encrypted_bytes = self._fernet.encrypt(raw_bytes)
        return encrypted_bytes.decode("utf-8")

    def decrypt(self, cipher_token: Union[str, bytes]) -> str:
        """Decrypt a cipher token and return the plaintext string.
        Raises ValueError if token is invalid or corrupted.
        """
        if isinstance(cipher_token, str):
            raw_token = cipher_token.encode("utf-8")
        else:
            raw_token = cipher_token

        try:
            decrypted_bytes = self._fernet.decrypt(raw_token)
            return decrypted_bytes.decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Decryption failed: invalid or tampered ciphertext token") from exc

    def decrypt_optional(self, cipher_token: Optional[Union[str, bytes]]) -> Optional[str]:
        """Decrypt if present, otherwise return None."""
        if not cipher_token:
            return None
        return self.decrypt(cipher_token)

