import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid


MEMORY_LAYOUT_VERSION = 2


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


def normalize_workspace_dir(workspace_dir):
    value = str(workspace_dir or "").strip()
    if not value:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(value))))


def workspace_key(workspace_dir):
    normalized = normalize_workspace_dir(workspace_dir).replace("\\", "/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else ""


class MemoryStore:
    def __init__(self, history_dir):
        self.history_dir = os.path.abspath(history_dir)
        self.root = os.path.join(self.history_dir, "memory")
        self.layout_path = os.path.join(self.root, "layout.json")
        self._ensure_initialized()

    def _ensure_initialized(self):
        os.makedirs(self.root, exist_ok=True)
        self._remove_obsolete_module_data()
        _atomic_write(
            self.layout_path,
            json.dumps({"schema_version": MEMORY_LAYOUT_VERSION}, ensure_ascii=False, indent=2) + "\n",
        )
        legacy = os.path.join(self.history_dir, "memories.md")
        summary = self.summary_path()
        if os.path.exists(legacy) and not os.path.exists(summary):
            with open(legacy, "r", encoding="utf-8") as handle:
                content = handle.read()
            if content.strip():
                self.save_summary(content, create_backup=False)

    def _remove_obsolete_module_data(self):
        index_path = os.path.join(self.root, "index.json")
        if os.path.exists(index_path):
            os.remove(index_path)
        for base in (os.path.join(self.root, "global"), os.path.join(self.root, "workspaces")):
            if not os.path.isdir(base):
                continue
            for dirpath, dirnames, _filenames in os.walk(base, topdown=False):
                if os.path.basename(dirpath) == "modules":
                    shutil.rmtree(dirpath)
        backup_dir = os.path.join(self.root, "backups")
        if os.path.isdir(backup_dir):
            for name in os.listdir(backup_dir):
                if name.startswith("summary.md.") or name.startswith("soul.md."):
                    continue
                path = os.path.join(backup_dir, name)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)

    def _scope_dir(self, scope="global", workspace_dir=""):
        if scope == "global":
            return os.path.join(self.root, "global")
        key = workspace_key(workspace_dir)
        if not key:
            raise ValueError("工作区摘要必须指定工作区路径。")
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
            backup_path = os.path.join(
                backup_dir,
                f"{os.path.basename(path)}.{stamp}.{uuid.uuid4().hex[:6]}.bak",
            )
            shutil.copy2(path, backup_path)
        _atomic_write(path, (content or "").rstrip() + ("\n" if content and content.strip() else ""))
        return backup_path

    def save_soul(self, content):
        return self._save_versioned(self.soul_path(), content)

    def save_summary(self, content, scope="global", workspace_dir="", create_backup=True):
        return self._save_versioned(self.summary_path(scope, workspace_dir), content, create_backup)
