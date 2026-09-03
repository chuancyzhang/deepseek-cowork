import base64
import copy
import ctypes
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from ctypes import wintypes


logger = logging.getLogger(__name__)

VARIABLE_KIND_TEXT = "text"
VARIABLE_KIND_SECRET = "secret"
VARIABLE_KINDS = {VARIABLE_KIND_TEXT, VARIABLE_KIND_SECRET}
VARIABLE_STORE_VERSION = 1
VARIABLES_FILENAME = "app_variables.json"
VARIABLES_BACKUP_FILENAME = "app_variables.previous.json"
DPAPI_PREFIX = "dpapi:v1:"
MAX_NAME_LENGTH = 64
MAX_VALUE_LENGTH = 8192


class VariableStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class WindowsDpapiProtector:
    _UI_FORBIDDEN = 0x1

    def __init__(self):
        if os.name != "nt":
            raise VariableStoreError("敏感凭据仅支持在 Windows 上使用 DPAPI 保存。")
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_pointer = ctypes.POINTER(_DataBlob)
        self._crypt32.CryptProtectData.argtypes = [
            blob_pointer, ctypes.c_wchar_p, blob_pointer, ctypes.c_void_p,
            ctypes.c_void_p, wintypes.DWORD, blob_pointer,
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            blob_pointer, ctypes.c_void_p, blob_pointer, ctypes.c_void_p,
            ctypes.c_void_p, wintypes.DWORD, blob_pointer,
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    @staticmethod
    def _blob(data):
        raw = bytes(data or b"")
        buffer = ctypes.create_string_buffer(raw, len(raw))
        blob = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        return blob, buffer

    def _transform(self, function_name, payload):
        source, source_buffer = self._blob(payload)
        target = _DataBlob()
        function = getattr(self._crypt32, function_name)
        if function_name == "CryptProtectData":
            ok = function(
                ctypes.byref(source),
                "DeepSeek Cowork variable",
                None,
                None,
                None,
                self._UI_FORBIDDEN,
                ctypes.byref(target),
            )
        else:
            ok = function(
                ctypes.byref(source),
                None,
                None,
                None,
                None,
                self._UI_FORBIDDEN,
                ctypes.byref(target),
            )
        del source_buffer
        if not ok:
            code = ctypes.get_last_error()
            raise VariableStoreError(f"Windows DPAPI 操作失败（错误码 {code}）。")
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            if target.pbData:
                self._kernel32.LocalFree(target.pbData)

    def protect(self, value):
        return self._transform("CryptProtectData", bytes(value or b""))

    def unprotect(self, value):
        return self._transform("CryptUnprotectData", bytes(value or b""))


def _normalize_name(value):
    name = str(value or "").strip()
    if not name:
        raise ValueError("变量名称不能为空。")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"变量名称不能超过 {MAX_NAME_LENGTH} 个字符。")
    if any(ord(character) < 32 for character in name):
        raise ValueError("变量名称不能包含控制字符。")
    return name


def _normalize_kind(value):
    kind = str(value or "").strip().lower()
    if kind not in VARIABLE_KINDS:
        raise ValueError("变量类型必须是 text 或 secret。")
    return kind


def _normalize_value(value):
    text = str(value if value is not None else "")
    if not text:
        raise ValueError("变量值不能为空。")
    if len(text) > MAX_VALUE_LENGTH:
        raise ValueError(f"变量值不能超过 {MAX_VALUE_LENGTH} 个字符。")
    return text


class VariableStore:
    """Single-writer global variable store. Secret values are DPAPI protected."""

    def __init__(self, data_dir, protector=None):
        self.data_dir = os.path.abspath(os.fspath(data_dir))
        self.path = os.path.join(self.data_dir, VARIABLES_FILENAME)
        self.backup_path = os.path.join(self.data_dir, VARIABLES_BACKUP_FILENAME)
        self._protector = protector
        self._lock = threading.RLock()
        self._snapshot = None
        self._signature = None

    def _get_protector(self):
        if self._protector is None:
            self._protector = WindowsDpapiProtector()
        return self._protector

    def _file_signature(self):
        try:
            stat = os.stat(self.path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise VariableStoreError(f"无法读取变量存储状态：{exc}") from exc
        return stat.st_mtime_ns, stat.st_size

    @staticmethod
    def _empty_snapshot():
        return {"version": VARIABLE_STORE_VERSION, "revision": 0, "entries": []}

    def _validate_snapshot(self, payload):
        if not isinstance(payload, dict) or payload.get("version") != VARIABLE_STORE_VERSION:
            raise VariableStoreError("变量存储格式无效或版本不受支持。")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise VariableStoreError("变量存储 entries 格式无效。")
        normalized = []
        ids = set()
        names = set()
        for item in entries:
            if not isinstance(item, dict):
                raise VariableStoreError("变量存储包含无效条目。")
            identifier = str(item.get("id") or "").strip()
            try:
                name = _normalize_name(item.get("name"))
                kind = _normalize_kind(item.get("kind"))
            except ValueError as exc:
                raise VariableStoreError(str(exc)) from exc
            if not identifier or identifier in ids or name.casefold() in names:
                raise VariableStoreError("变量存储包含重复或无效的 ID/名称。")
            if kind == VARIABLE_KIND_TEXT:
                if not isinstance(item.get("value"), str):
                    raise VariableStoreError(f"普通变量“{name}”缺少有效值。")
            else:
                if not str(item.get("protected_value") or "").startswith(DPAPI_PREFIX):
                    raise VariableStoreError(f"敏感凭据“{name}”不是有效的 DPAPI 数据。")
                if "allow_ai_read" in item and not isinstance(item.get("allow_ai_read"), bool):
                    raise VariableStoreError(f"敏感凭据“{name}”的 AI 读取权限格式无效。")
            ids.add(identifier)
            names.add(name.casefold())
            normalized_item = copy.deepcopy(item)
            normalized_item["allow_ai_read"] = (
                True if kind == VARIABLE_KIND_TEXT else bool(item.get("allow_ai_read", False))
            )
            normalized.append(normalized_item)
        return {
            "version": VARIABLE_STORE_VERSION,
            "revision": max(0, int(payload.get("revision") or 0)),
            "entries": normalized,
        }

    def _load(self):
        signature = self._file_signature()
        if self._snapshot is not None and signature == self._signature:
            return self._snapshot
        if signature is None:
            self._snapshot = self._empty_snapshot()
            self._signature = None
            return self._snapshot
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except Exception as exc:
            logger.error("variable_store.load.error path=%s error=%s", self.path, exc)
            raise VariableStoreError(f"变量存储读取失败，原文件已保留：{exc}") from exc
        self._snapshot = self._validate_snapshot(payload)
        self._signature = signature
        return self._snapshot

    def _write(self, snapshot):
        validated = self._validate_snapshot(snapshot)
        os.makedirs(self.data_dir, exist_ok=True)
        data_fd, temp_path = tempfile.mkstemp(prefix=".app_variables-", suffix=".tmp", dir=self.data_dir)
        backup_temp = ""
        try:
            with os.fdopen(data_fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(validated, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if os.path.exists(self.path):
                backup_fd, backup_temp = tempfile.mkstemp(
                    prefix=".app_variables-backup-", suffix=".tmp", dir=self.data_dir
                )
                os.close(backup_fd)
                shutil.copy2(self.path, backup_temp)
                os.replace(backup_temp, self.backup_path)
                backup_temp = ""
            os.replace(temp_path, self.path)
            temp_path = ""
        except Exception as exc:
            raise VariableStoreError(f"变量存储保存失败，原数据已保留：{exc}") from exc
        finally:
            for pending in (temp_path, backup_temp):
                if pending and os.path.exists(pending):
                    try:
                        os.remove(pending)
                    except OSError:
                        logger.warning("variable_store.temp_cleanup_failed path=%s", pending)
        self._snapshot = validated
        self._signature = self._file_signature()

    def restore_previous(self):
        with self._lock:
            if not os.path.exists(self.backup_path):
                raise VariableStoreError("没有可恢复的上一版变量存储。")
            try:
                with open(self.backup_path, "r", encoding="utf-8") as stream:
                    snapshot = self._validate_snapshot(json.load(stream))
                os.makedirs(self.data_dir, exist_ok=True)
                handle, temp_path = tempfile.mkstemp(prefix=".app_variables-restore-", suffix=".tmp", dir=self.data_dir)
                try:
                    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
                        json.dump(snapshot, output, ensure_ascii=False, indent=2)
                        output.write("\n")
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temp_path, self.path)
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            logger.warning("variable_store.restore_temp_cleanup_failed path=%s", temp_path)
            except VariableStoreError:
                raise
            except Exception as exc:
                logger.error("variable_store.restore.error path=%s error=%s", self.backup_path, exc)
                raise VariableStoreError(f"上一版变量存储恢复失败：{exc}") from exc
            self._snapshot = snapshot
            self._signature = self._file_signature()
            logger.info("variable_store.restore.finish revision=%s", snapshot["revision"])
            return len(snapshot["entries"])

    def _protect(self, value):
        raw = self._get_protector().protect(value.encode("utf-8"))
        return DPAPI_PREFIX + base64.b64encode(raw).decode("ascii")

    def _unprotect(self, payload):
        encoded = str(payload or "")
        if not encoded.startswith(DPAPI_PREFIX):
            raise VariableStoreError("敏感凭据不是有效的 DPAPI 数据。")
        try:
            protected = base64.b64decode(encoded[len(DPAPI_PREFIX):], validate=True)
            return self._get_protector().unprotect(protected).decode("utf-8")
        except VariableStoreError:
            raise
        except Exception as exc:
            raise VariableStoreError(f"敏感凭据解密失败：{exc}") from exc

    @staticmethod
    def _public_entry(entry):
        return {
            "id": entry["id"],
            "name": entry["name"],
            "kind": entry["kind"],
            "allow_ai_read": (
                True if entry["kind"] == VARIABLE_KIND_TEXT else bool(entry.get("allow_ai_read", False))
            ),
            "description": str(entry.get("description") or ""),
            "created_at": int(entry.get("created_at") or 0),
            "updated_at": int(entry.get("updated_at") or 0),
            "has_value": True,
        }

    def list_public(self, kind=None, query=""):
        with self._lock:
            snapshot = self._load()
            normalized_kind = _normalize_kind(kind) if kind else ""
            needle = str(query or "").strip().casefold()
            results = []
            for entry in snapshot["entries"]:
                if normalized_kind and entry["kind"] != normalized_kind:
                    continue
                haystack = f"{entry['name']} {entry.get('description') or ''}".casefold()
                if needle and needle not in haystack:
                    continue
                results.append(self._public_entry(entry))
            return results

    def get_public_by_id(self, variable_id):
        with self._lock:
            entry = self._find_by_id(variable_id)
            return self._public_entry(entry) if entry else None

    def _find_by_id(self, variable_id):
        target = str(variable_id or "").strip()
        for entry in self._load()["entries"]:
            if entry["id"] == target:
                return entry
        return None

    def _find_by_name(self, name):
        target = str(name or "").strip().casefold()
        for entry in self._load()["entries"]:
            if entry["name"].casefold() == target:
                return entry
        return None

    def get_text_exact(self, name):
        with self._lock:
            entry = self._find_by_name(name)
            if entry is None:
                raise KeyError(str(name or "").strip())
            if entry["kind"] != VARIABLE_KIND_TEXT:
                return None
            return str(entry.get("value") or "")

    def resolve_for_ai(self, name):
        with self._lock:
            entry = self._find_by_name(name)
            if entry is None:
                return {"status": "not_found", "name": str(name or "").strip()}
            variable_id = entry["id"]
            kind = entry["kind"]
            if kind == VARIABLE_KIND_SECRET and not bool(entry.get("allow_ai_read", False)):
                logger.info(
                    "variable_store.ai_read.denied variable_id=%s kind=%s reason=restricted",
                    variable_id,
                    kind,
                )
                return {
                    "status": "restricted",
                    "id": variable_id,
                    "name": entry["name"],
                    "kind": kind,
                }
            logger.info("variable_store.ai_read.start variable_id=%s kind=%s", variable_id, kind)
            try:
                value = (
                    self._unprotect(entry.get("protected_value"))
                    if kind == VARIABLE_KIND_SECRET
                    else str(entry.get("value") or "")
                )
            except Exception:
                logger.exception("variable_store.ai_read.error variable_id=%s kind=%s", variable_id, kind)
                raise
            logger.info("variable_store.ai_read.finish variable_id=%s kind=%s", variable_id, kind)
            return {
                "status": "ok",
                "id": variable_id,
                "name": entry["name"],
                "kind": kind,
                "value": value,
            }

    def get_text_by_id(self, variable_id):
        with self._lock:
            entry = self._find_by_id(variable_id)
            if entry is None:
                raise VariableStoreError("变量不存在或已被删除。")
            if entry["kind"] != VARIABLE_KIND_TEXT:
                raise VariableStoreError("敏感凭据不能插入输入框。")
            return str(entry.get("value") or "")

    def upsert(self, name, kind, value=None, description="", variable_id="", allow_ai_read=False):
        name = _normalize_name(name)
        kind = _normalize_kind(kind)
        if kind == VARIABLE_KIND_SECRET and not isinstance(allow_ai_read, bool):
            raise ValueError("敏感凭据的 AI 读取权限必须是布尔值。")
        description = str(description or "").strip()
        now = int(time.time())
        with self._lock:
            snapshot = copy.deepcopy(self._load())
            current = None
            if variable_id:
                current = next((item for item in snapshot["entries"] if item["id"] == variable_id), None)
                if current is None:
                    raise ValueError("要编辑的变量不存在。")
                if current["kind"] != kind:
                    raise ValueError("变量类型创建后不能修改。")
            duplicate = next(
                (
                    item for item in snapshot["entries"]
                    if item["name"].casefold() == name.casefold()
                    and (current is None or item["id"] != current["id"])
                ),
                None,
            )
            if duplicate:
                raise ValueError(f"已存在同名变量：{name}")
            if current is None:
                normalized_value = _normalize_value(value)
                current = {
                    "id": uuid.uuid4().hex,
                    "name": name,
                    "kind": kind,
                    "description": description,
                    "created_at": now,
                    "updated_at": now,
                }
                snapshot["entries"].append(current)
            else:
                current["name"] = name
                current["description"] = description
                current["updated_at"] = now
                normalized_value = _normalize_value(value) if value not in (None, "") else None
            if kind == VARIABLE_KIND_TEXT:
                if normalized_value is None:
                    raise ValueError("普通变量的新值不能为空。")
                current["value"] = normalized_value
                current["allow_ai_read"] = True
                current.pop("protected_value", None)
            else:
                current["allow_ai_read"] = bool(allow_ai_read)
                if normalized_value is not None:
                    try:
                        current["protected_value"] = self._protect(normalized_value)
                        current.pop("value", None)
                    except Exception:
                        logger.exception("variable_store.save.error variable_id=%s stage=encrypt", current["id"])
                        raise
            snapshot["revision"] = int(snapshot.get("revision") or 0) + 1
            logger.info("variable_store.save.start variable_id=%s operation=%s", current["id"], "update" if variable_id else "create")
            try:
                self._write(snapshot)
            except Exception:
                logger.exception("variable_store.save.error variable_id=%s stage=write", current["id"])
                raise
            logger.info("variable_store.save.finish variable_id=%s revision=%s", current["id"], snapshot["revision"])
            return self._public_entry(current)

    def delete(self, variable_id):
        with self._lock:
            snapshot = copy.deepcopy(self._load())
            before = len(snapshot["entries"])
            snapshot["entries"] = [item for item in snapshot["entries"] if item["id"] != variable_id]
            if len(snapshot["entries"]) == before:
                return False
            snapshot["revision"] = int(snapshot.get("revision") or 0) + 1
            logger.info("variable_store.delete.start variable_id=%s", variable_id)
            try:
                self._write(snapshot)
            except Exception:
                logger.exception("variable_store.delete.error variable_id=%s", variable_id)
                raise
            logger.info("variable_store.delete.finish variable_id=%s revision=%s", variable_id, snapshot["revision"])
            return True
