import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid


SCHEMA_VERSION = 1


def _atomic_write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="memory.", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content or "")
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def workspace_key(workspace_dir):
    normalized = os.path.normcase(os.path.abspath(str(workspace_dir or ""))).replace("\\", "/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else ""


class MemoryStore:
    def __init__(self, history_dir):
        self.history_dir = os.path.abspath(history_dir)
        self.root = os.path.join(self.history_dir, "memory")
        self.index_path = os.path.join(self.root, "index.json")
        self._ensure_initialized()

    def _default_index(self):
        return {"schema_version": SCHEMA_VERSION, "modules": [], "workspaces": {}}

    def _ensure_initialized(self):
        os.makedirs(self.root, exist_ok=True)
        if not os.path.exists(self.index_path):
            _atomic_write(self.index_path, json.dumps(self._default_index(), ensure_ascii=False, indent=2))
        # One-way, non-destructive compatibility import. The legacy file remains untouched.
        legacy = os.path.join(self.history_dir, "memories.md")
        summary = self.summary_path()
        if os.path.exists(legacy) and not os.path.exists(summary):
            with open(legacy, "r", encoding="utf-8") as handle:
                content = handle.read()
            if content.strip():
                self.save_summary(content, create_backup=False)

    def _load_index(self):
        with open(self.index_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION or not isinstance(data.get("modules"), list):
            raise RuntimeError("记忆索引格式无效，请修复 memory/index.json。")
        return data

    def _save_index(self, data):
        _atomic_write(self.index_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def _scope_dir(self, scope="global", workspace_dir=""):
        if scope == "global":
            return os.path.join(self.root, "global")
        key = workspace_key(workspace_dir)
        if not key:
            raise ValueError("工作区记忆必须指定工作区路径。")
        return os.path.join(self.root, "workspaces", key)

    def soul_path(self):
        return os.path.join(self.root, "soul.md")

    def summary_path(self, scope="global", workspace_dir=""):
        return os.path.join(self._scope_dir(scope, workspace_dir), "summary.md")

    def _read(self, path):
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def read_soul(self):
        return self._read(self.soul_path())

    def read_summary(self, scope="global", workspace_dir=""):
        return self._read(self.summary_path(scope, workspace_dir))

    def _save_versioned(self, path, content, create_backup=True):
        backup_path = ""
        if create_backup and os.path.exists(path):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup_dir = os.path.join(self.root, "backups")
            os.makedirs(backup_dir, exist_ok=True)
            backup_path = os.path.join(backup_dir, f"{os.path.basename(path)}.{stamp}.{uuid.uuid4().hex[:6]}.bak")
            shutil.copy2(path, backup_path)
        _atomic_write(path, (content or "").rstrip() + ("\n" if content and content.strip() else ""))
        return backup_path

    def save_soul(self, content):
        return self._save_versioned(self.soul_path(), content)

    def save_summary(self, content, scope="global", workspace_dir="", create_backup=True):
        return self._save_versioned(self.summary_path(scope, workspace_dir), content, create_backup)

    def list_modules(self, workspace_dir="", include_archived=False):
        data = self._load_index()
        applicable = {"global"}
        key = workspace_key(workspace_dir)
        result = []
        for item in data["modules"]:
            if item.get("scope") == "workspace" and item.get("workspace_key") != key:
                continue
            if item.get("scope") not in applicable and item.get("scope") != "workspace":
                continue
            if item.get("archived") and not include_archived:
                continue
            result.append(dict(item))
        return sorted(result, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def _module_path(self, module):
        scope_dir = self._scope_dir(module.get("scope") or "global", module.get("workspace_dir") or "")
        if module.get("scope") == "workspace" and module.get("workspace_key"):
            scope_dir = os.path.join(self.root, "workspaces", module["workspace_key"])
        return os.path.join(scope_dir, "modules", f"{module['id']}.md")

    def get_module(self, module_id):
        data = self._load_index()
        module = next((item for item in data["modules"] if item.get("id") == module_id), None)
        if not module:
            raise KeyError("记忆模块不存在。")
        result = dict(module)
        result["content"] = self._read(self._module_path(module))
        return result

    def save_module(self, title, content, module_id="", tags=None, scope="global", workspace_dir="", enabled=True):
        title = str(title or "").strip()
        if not title:
            raise ValueError("记忆模块标题不能为空。")
        data = self._load_index()
        now = int(time.time())
        module = next((item for item in data["modules"] if item.get("id") == module_id), None)
        if module is None:
            module = {"id": module_id or uuid.uuid4().hex, "created_at": now, "archived": False}
            data["modules"].append(module)
        module.update({
            "title": title,
            "tags": [str(item).strip() for item in (tags or []) if str(item).strip()],
            "scope": scope,
            "workspace_key": workspace_key(workspace_dir) if scope == "workspace" else "",
            "enabled": bool(enabled),
            "updated_at": now,
        })
        self._save_versioned(self._module_path(module), content)
        self._save_index(data)
        return dict(module)

    def archive_module(self, module_id, archived=True):
        data = self._load_index()
        module = next((item for item in data["modules"] if item.get("id") == module_id), None)
        if not module:
            raise KeyError("记忆模块不存在。")
        module["archived"] = bool(archived)
        module["updated_at"] = int(time.time())
        self._save_index(data)

    def list_module_versions(self, module_id):
        module = self.get_module(module_id)
        prefix = f"{module_id}.md."
        backup_dir = os.path.join(self.root, "backups")
        if not os.path.isdir(backup_dir):
            return []
        return [os.path.join(backup_dir, name) for name in sorted(os.listdir(backup_dir), reverse=True) if name.startswith(prefix)]

    def restore_latest_module_version(self, module_id):
        versions = self.list_module_versions(module_id)
        if not versions:
            raise RuntimeError("这个模块还没有可恢复的历史版本。")
        module = self.get_module(module_id)
        with open(versions[0], "r", encoding="utf-8") as handle:
            previous = handle.read()
        self._save_versioned(self._module_path(module), previous)
        data = self._load_index()
        target = next(item for item in data["modules"] if item.get("id") == module_id)
        target["updated_at"] = int(time.time())
        self._save_index(data)
        return previous

    def search_modules(self, query, workspace_dir="", limit=8):
        terms = [item.lower() for item in re.findall(r"[\w\u4e00-\u9fff]+", str(query or ""))]
        matches = []
        for module in self.list_modules(workspace_dir):
            if not module.get("enabled", True):
                continue
            content = self.get_module(module["id"])["content"]
            haystack = " ".join([module.get("title", ""), " ".join(module.get("tags") or []), content]).lower()
            score = sum(haystack.count(term) for term in terms)
            if score or not terms:
                matches.append((score, module))
        matches.sort(key=lambda pair: (pair[0], pair[1].get("updated_at", 0)), reverse=True)
        return [item for _, item in matches[: max(1, int(limit))]]
