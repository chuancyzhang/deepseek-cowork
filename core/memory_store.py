import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from core.file_write_locks import file_write_lock
from core.sidebar_projects import project_paths


MEMORY_LAYOUT_VERSION = 2
EXPERIENCE_HEADING = "## 可复用经验"


def resolve_memory_project(config, workspace_dir, *, storage=None, conversation_id="", run_context=None):
    """Resolve only the run's registered project, never a chat artifact directory."""
    if (run_context or {}).get("creation_session"):
        return ""
    workspace = normalize_workspace_dir(workspace_dir)
    if not workspace or config is None:
        return ""
    if storage is not None and conversation_id:
        meta = storage.get_conversation_meta(conversation_id) or {}
        if meta.get("workspace_source") == "chat" or meta.get("creation_session"):
            return ""
        bound = meta.get("workspace_dir") or meta.get("workspace_path")
        if bound and normalize_workspace_dir(bound) != workspace:
            return ""
    projects = config.get_projects() if hasattr(config, "get_projects") else []
    registered = (path for project in projects if isinstance(project, dict) for path in project_paths(project)
                  if path != project.get("inferred_path"))
    return workspace if any(normalize_workspace_dir(path) == workspace for path in registered) else ""


def memory_project_from_context(context):
    if (context.get("run_context") or {}).get("creation_session"):
        return ""
    # This key is supplied by the worker, not by model tool arguments.
    if "memory_project_dir" in context:
        return context["memory_project_dir"] or ""
    return resolve_memory_project(context.get("config_manager"), context.get("workspace_dir"),
                                  storage=context.get("chat_storage"),
                                  conversation_id=context.get("conversation_id") or context.get("session_id", ""),
                                  run_context=context.get("run_context"))


def _unfenced_line_indexes(lines):
    """Do not mistake a Markdown example for a writable memory section."""
    fence = ""
    for index, line in enumerate(lines):
        marker = re.match(r" {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ""
        elif marker:
            fence = marker[1]
        else:
            yield index
    if fence:
        raise ValueError("记忆中有未闭合的代码块，请先在记忆页整理后重试。")


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
        # Lock the stable directory: Windows realpath probes on a file being
        # replaced can themselves deny its rename while another caller waits.
        with file_write_lock(self.root):
            self._initialize_locked()

    def _initialize_locked(self):
        os.makedirs(self.root, exist_ok=True)
        self._remove_obsolete_module_data()
        layout = json.dumps({"schema_version": MEMORY_LAYOUT_VERSION}, ensure_ascii=False, indent=2) + "\n"
        if self._read(self.layout_path) != layout:
            _atomic_write(self.layout_path, layout)
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
        if scope != "workspace":
            raise ValueError("不支持的记忆范围。")
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
        with file_write_lock(self.root):
            return self._read(self.soul_path())

    def read_summary(self, scope="global", workspace_dir=""):
        with file_write_lock(self.root):
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
        with file_write_lock(self.root):
            return self._save_versioned(self.soul_path(), content)

    def save_summary(self, content, scope="global", workspace_dir="", create_backup=True, *, expected_content=None):
        with file_write_lock(self.root):
            path = self.summary_path(scope, workspace_dir)
            if expected_content is not None and self._read(path) != expected_content:
                raise ValueError("记忆已被其他操作修改。编辑已保留，请刷新后合并。")
            return self._save_versioned(path, content, create_backup)

    def append_summary(self, content):
        with file_write_lock(self.root):
            current = self.read_summary()
            return self.save_summary((current.rstrip() + "\n" + (content or "")).strip())

    def update_experience(self, experience, scope="global", workspace_dir="", previous_experience=None):
        """Edit one plain Markdown bullet; all unrelated summary text is preserved."""
        if not isinstance(experience, str) or not experience.strip() or "\n" in experience.strip() or "\r" in experience.strip():
            raise ValueError("经验必须是一条非空的单段文本，不含换行。")
        entry = "- " + experience.strip()
        if previous_experience is not None and (not isinstance(previous_experience, str) or not previous_experience.strip()):
            raise ValueError("待替换经验不能为空，请先读取当前记忆。")
        previous = "- " + previous_experience.strip() if previous_experience is not None else None
        with file_write_lock(self.root):
            content = self.read_summary(scope, workspace_dir)
            summary_lines = content.splitlines(keepends=True)
            visible = list(_unfenced_line_indexes(summary_lines))
            headings = [index for index in visible if summary_lines[index].rstrip() == EXPERIENCE_HEADING]
            if len(headings) > 1:
                raise ValueError("存在多个可复用经验小节，请先在记忆页合并。")
            if headings:
                start = sum(map(len, summary_lines[:headings[0] + 1]))
                following = next((index for index in visible if index > headings[0] and re.match(r"^#{1,2} ", summary_lines[index])), len(summary_lines))
                end = sum(map(len, summary_lines[:following]))
                body = content[start:end]
            else:
                start = end = len(content)
                body = ""
            lines = body.splitlines(keepends=True)
            entries = {index: lines[index].rstrip("\r\n") for index in _unfenced_line_indexes(lines)}
            matches = [index for index, line in entries.items() if previous is not None and line == previous]
            if previous is not None and len(matches) != 1:
                # A repeated successful replacement is harmless; a stale edit is not an append.
                if not matches and entry in entries.values():
                    return "unchanged"
                raise ValueError("待替换经验不存在、已变化或不唯一，请重新读取后合并。")
            if previous == entry or (previous is None and entry in entries.values()):
                return "unchanged"
            if previous is not None:
                index = matches[0]
                if entry in entries.values():
                    lines.pop(index)
                else:
                    ending = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
                    lines[index] = entry + ending
                updated_body = "".join(lines)
                updated = content[:start] + updated_body + content[end:]
                action = "updated"
            elif headings:
                updated = content[:start] + body.rstrip() + "\n" + entry + "\n\n" + content[end:]
                action = "added"
            else:
                updated = content.rstrip() + ("\n\n" if content.strip() else "") + EXPERIENCE_HEADING + "\n\n" + entry + "\n"
                action = "added"
            self.save_summary(updated, scope, workspace_dir)
            return action
