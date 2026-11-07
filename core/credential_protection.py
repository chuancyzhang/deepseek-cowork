"""Native credential protection; never fall back to plaintext or a file key."""
import base64
import hashlib
import logging
import os
import sys

log = logging.getLogger(__name__)
KEYRING_PREFIX = "keyring:v1:"
_HEADER = b"Cowork-Keyring-AES256GCM-v1\0"


class CredentialProtectionError(RuntimeError):
    pass


class KeyringProtector:
    prefix = KEYRING_PREFIX

    def __init__(self, data_dir, backend=None):
        self.data_dir = os.path.realpath(os.path.abspath(data_dir))
        self.account = hashlib.sha256(self.data_dir.encode("utf-8")).hexdigest()
        self.service = "DeepSeekCowork.credentials.v1"
        self.backend = backend

    def _backend(self):
        if self.backend is None:
            try:
                if sys.platform == "darwin":
                    from keyring.backends.macOS import Keyring
                elif sys.platform.startswith("linux"):
                    from keyring.backends.SecretService import Keyring
                else:
                    raise CredentialProtectionError("当前系统不支持安全凭据存储。")
                self.backend = Keyring()
            except Exception as exc:
                raise CredentialProtectionError("系统密钥环不可用，请安装并解锁系统密钥环后重试；凭据尚未保存。") from exc
        return self.backend

    def _key(self, create=False):
        from .file_write_locks import file_write_lock
        # All processes sharing this data directory must serialize first creation.
        with file_write_lock(os.path.join(self.data_dir, ".credential-key")):
            try:
                backend = self._backend()
                encoded = backend.get_password(self.service, self.account)
                if encoded is None and create:
                    encoded = base64.b64encode(os.urandom(32)).decode("ascii")
                    backend.set_password(self.service, self.account, encoded)
                    if backend.get_password(self.service, self.account) != encoded:
                        raise CredentialProtectionError("系统密钥环未能确认保存，请解锁后重试。")
                    log.info("credential.key_created backend=%s", type(backend).__name__)
                if encoded is None:
                    raise CredentialProtectionError("系统密钥环中缺少解密密钥；原数据已保留，请恢复原密钥环或重新录入凭据。")
                key = base64.b64decode(encoded, validate=True)
                if len(key) != 32:
                    raise CredentialProtectionError("系统密钥环中的密钥格式无效，原数据已保留。")
                return key
            except CredentialProtectionError:
                raise
            except Exception as exc:
                log.warning("credential.key_access_failed reason=%s", type(exc).__name__)
                raise CredentialProtectionError("无法访问系统密钥环，请解锁密钥环后重试；原数据已保留。") from exc

    def protect(self, value):
        from Crypto.Cipher import AES
        cipher = AES.new(self._key(create=True), AES.MODE_GCM, nonce=os.urandom(12))
        cipher.update(_HEADER + self.account.encode("ascii"))
        encrypted, tag = cipher.encrypt_and_digest(bytes(value))
        return _HEADER + cipher.nonce + tag + encrypted

    def unprotect(self, value):
        from Crypto.Cipher import AES
        raw = bytes(value)
        if not raw.startswith(_HEADER) or len(raw) < len(_HEADER) + 28:
            raise CredentialProtectionError("凭据格式不属于当前平台，不能导入 Windows 加密数据；请重新录入。")
        body = raw[len(_HEADER):]
        cipher = AES.new(self._key(), AES.MODE_GCM, nonce=body[:12])
        cipher.update(_HEADER + self.account.encode("ascii"))
        try:
            return cipher.decrypt_and_verify(body[28:], body[12:28])
        except ValueError as exc:
            raise CredentialProtectionError("凭据校验失败，原数据已保留；请恢复原密钥环或重新录入。") from exc


def create_credential_protector(data_dir):
    if os.name == "nt":
        from .variable_store import WindowsDpapiProtector
        return WindowsDpapiProtector()
    return KeyringProtector(data_dir)
