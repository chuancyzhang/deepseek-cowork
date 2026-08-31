"""本地宏变量保险库。

把用户在各平台（百度地图、腾讯文档等）的 AK / token / key 以加密形式保存在
本地应用数据目录，支持按名称读取明文，供 Agent 在会话中按需获取，避免跨会话
重复提供密钥。

安全设计：
- 值使用 AES-256-GCM 加密（pycryptodome），密钥为随机生成并保存在同目录的
  .key 文件；仅本机可解密（不进行跨机同步、不上传）。
- 若运行环境缺少 pycryptodome，则退化为 base64 编码（非明文，但不提供加密保护）。
- 名称、标签、分组、掩码等元数据以明文保存，便于列表展示而无需解密值。
- 本模块绝不向日志或异常信息输出明文值。
"""
import base64
import copy
import json
import os
import secrets
import time
import uuid

try:
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes

    _CRYPTO_OK = True
except Exception:  # pragma: no cover - 依赖缺失时的降级路径
    _CRYPTO_OK = False


KEY_FILENAME = "macro_vault.key"
DATA_FILENAME = "macro_vault.json"
_KEY_BYTES = 32
_GCM_NONCE_BYTES = 12
_GCM_TAG_BYTES = 16


def mask_secret(value, visible_head=4, visible_tail=4):
    """把密钥转为掩码形式，用于列表展示，绝不回显完整明文。"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= visible_head + visible_tail:
        return "\u2022" * len(text)
    return f"{text[:visible_head]}{'\u2022' * 4}{text[-visible_tail:]}"


def _normalize_identifier(value):
    return str(value or "").strip()


class MacroVault:
    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(str(data_dir or ""))
        self.key_path = os.path.join(self.data_dir, KEY_FILENAME)
        self.data_path = os.path.join(self.data_dir, DATA_FILENAME)
        self._key = self._load_or_create_key()
        self._data = self._load_data()
        self._data_mtime = self._file_mtime()

    # ------------------------------------------------------------------ #
    # 密钥与数据文件的读写
    # ------------------------------------------------------------------ #
    def _file_mtime(self):
        try:
            stat = os.stat(self.data_path)
            return (stat.st_mtime_ns, stat.st_size)
        except Exception:
            return None

    def _ensure_fresh(self):
        """磁盘文件被其他进程（如 daemon）改写后，懒加载最新数据。"""
        current = self._file_mtime()
        if current is not None and current != self._data_mtime:
            self._data = self._load_data()
            self._data_mtime = current
    def _load_or_create_key(self):
        if os.path.exists(self.key_path):
            try:
                with open(self.key_path, "rb") as handle:
                    key = handle.read()
                if len(key) == _KEY_BYTES:
                    return key
            except Exception:
                pass
        key = get_random_bytes(_KEY_BYTES) if _CRYPTO_OK else secrets.token_bytes(_KEY_BYTES)
        os.makedirs(self.data_dir, exist_ok=True)
        try:
            with open(self.key_path, "wb") as handle:
                handle.write(key)
        except Exception:
            pass
        return key

    def _load_data(self):
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    entries = payload.get("entries")
                    if isinstance(entries, list):
                        return {"version": int(payload.get("version") or 1), "entries": entries}
            except Exception:
                pass
        return {"version": 1, "entries": []}

    def _save_data(self):
        os.makedirs(self.data_dir, exist_ok=True)
        temp_path = f"{self.data_path}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.data_path)
            self._data_mtime = self._file_mtime()
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    # 加解密
    # ------------------------------------------------------------------ #
    def _encrypt(self, plaintext):
        data = str(plaintext or "").encode("utf-8")
        if not _CRYPTO_OK:
            return "b64:" + base64.b64encode(data).decode("ascii")
        nonce = get_random_bytes(_GCM_NONCE_BYTES)
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(data)
        return "gcm:" + base64.b64encode(nonce + ciphertext + tag).decode("ascii")

    def _decrypt(self, payload):
        if not payload:
            return None
        payload = str(payload)
        try:
            if payload.startswith("b64:"):
                return base64.b64decode(payload[4:]).decode("utf-8")
            if payload.startswith("gcm:"):
                raw = base64.b64decode(payload[4:])
                nonce = raw[:_GCM_NONCE_BYTES]
                ciphertext = raw[_GCM_NONCE_BYTES:-_GCM_TAG_BYTES]
                tag = raw[-_GCM_TAG_BYTES:]
                cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
                return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def _find_entry(self, identifier):
        self._ensure_fresh()
        target = _normalize_identifier(identifier)
        if not target:
            return None
        for entry in self._data.get("entries", []):
            if entry.get("id") == target:
                return entry
        lowered = target.casefold()
        for entry in self._data.get("entries", []):
            if str(entry.get("name") or "").strip().casefold() == lowered:
                return entry
        return None

    def list_entries(self):
        self._ensure_fresh()
        return copy.deepcopy(self._data.get("entries", []))

    def public_entries(self):
        """返回不含密文的条目，供 UI 与 list_macro_variables 工具使用。"""
        self._ensure_fresh()
        public = []
        for entry in self._data.get("entries", []):
            public.append(
                {
                    "id": entry.get("id"),
                    "name": entry.get("name"),
                    "label": entry.get("label"),
                    "scope": entry.get("scope"),
                    "description": entry.get("description"),
                    "value_masked": entry.get("value_masked") or mask_secret(entry.get("value_cipher") or ""),
                    "created_at": entry.get("created_at"),
                    "updated_at": entry.get("updated_at"),
                }
            )
        return public

    def names(self):
        self._ensure_fresh()
        return [str(entry.get("name") or "").strip() for entry in self._data.get("entries", []) if str(entry.get("name") or "").strip()]

    def get_value(self, identifier):
        entry = self._find_entry(identifier)
        if not entry:
            return None
        return self._decrypt(entry.get("value_cipher"))

    def get_masked(self, identifier):
        entry = self._find_entry(identifier)
        if not entry:
            return None
        return entry.get("value_masked") or mask_secret(entry.get("value_cipher") or "")

    def upsert(self, name, value, label="", scope="", description="", entry_id=None):
        self._ensure_fresh()
        name = _normalize_identifier(name)
        if not name:
            raise ValueError("宏变量名称不能为空。")
        now = int(time.time())

        if entry_id:
            # 编辑既有条目。
            existing = self._find_entry(entry_id)
            if existing is None:
                raise ValueError("未找到要编辑的宏变量。")
            lowered = name.casefold()
            for entry in self._data.get("entries", []):
                if entry.get("id") == existing.get("id"):
                    continue
                if str(entry.get("name") or "").strip().casefold() == lowered:
                    raise ValueError(f"已存在同名宏变量：{name}")
            # value 为空表示“保持不变”。
            if str(value or "") == "":
                next_cipher = existing.get("value_cipher")
                next_masked = existing.get("value_masked")
            else:
                next_cipher = self._encrypt(value)
                next_masked = mask_secret(value)
            existing["name"] = name
            existing["label"] = str(label or "").strip() or name
            existing["scope"] = str(scope or "").strip()
            existing["description"] = str(description or "").strip()
            existing["value_cipher"] = next_cipher
            existing["value_masked"] = next_masked
            existing["updated_at"] = now
            self._save_data()
            return copy.deepcopy(existing)

        # 新增条目。
        if str(value or "") == "":
            raise ValueError("新宏变量的值不能为空。")
        # 校验名称不重复（大小写不敏感）。
        lowered = name.casefold()
        for entry in self._data.get("entries", []):
            if str(entry.get("name") or "").strip().casefold() == lowered:
                raise ValueError(f"已存在同名宏变量：{name}")
        entry = {
            "id": uuid.uuid4().hex,
            "name": name,
            "label": str(label or "").strip() or name,
            "scope": str(scope or "").strip(),
            "description": str(description or "").strip(),
            "value_cipher": self._encrypt(value),
            "value_masked": mask_secret(value),
            "created_at": now,
            "updated_at": now,
        }
        self._data.setdefault("entries", []).append(entry)
        self._save_data()
        return copy.deepcopy(entry)

    def delete(self, identifier):
        entry = self._find_entry(identifier)
        if not entry:
            return False
        entries = self._data.setdefault("entries", [])
        self._data["entries"] = [item for item in entries if item.get("id") != entry.get("id")]
        self._save_data()
        return True

    def replace_all(self, entries):
        """用给定条目列表整体替换（用于设置保存失败时回滚）。"""
        self._ensure_fresh()
        normalized = []
        for item in entries or []:
            if isinstance(item, dict):
                normalized.append(dict(item))
        self._data["entries"] = normalized
        self._save_data()
