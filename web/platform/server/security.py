"""Small cryptographic primitives; caller-controlled data is never logged."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

from cryptography.fernet import Fernet


def token():
    return secrets.token_urlsafe(32)


def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("ascii"), n=16384, r=8, p=1).hex()


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


def equal(a, b):
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a, b)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Vault:
    def __init__(self, key):
        self.cipher = Fernet(key.encode("ascii"))

    def seal(self, value):
        return self.cipher.encrypt(encode(value).encode("utf-8")).decode("ascii")

    def open(self, value):
        return json.loads(self.cipher.decrypt(value.encode("ascii")))


class PlatformError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)
