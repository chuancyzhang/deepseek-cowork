"""Read-only project material browser; adding a file uses normal chat attachments."""
import hashlib
import logging
import os
from pathlib import Path

from core.desktop_jobs import JobCancelled
from core.file_capabilities import file_capability

log = logging.getLogger("desktop.library")
DOCUMENTS = frozenset(".md .markdown .txt .pdf .doc .docx .ppt .pptx .xls .xlsx .csv .tsv .html .htm .png .jpg .jpeg .gif .webp .svg".split())
EXCLUDED = frozenset({"node_modules", "__pycache__", "venv", "node_env", "git_bash_env", "ripgrep_env", "dist", "build", "out"})


class LocalLibrary:
    def __init__(self, service):
        self.s = service

    def projects(self):
        projects = {}
        for item in self.s.config.get_projects():
            root = Path(item["path"]).expanduser().resolve()
            projects[str(root)] = {"name": item.get("name") or root.name, "path": str(root)}
        for entries in self.s.storage.list_conversations_by_workspace().values():
            root = Path(entries[0]["workspace_dir"]).resolve()
            projects.setdefault(str(root), {"name": root.name, "path": str(root)})
        return [{**item, "id": hashlib.sha256(os.path.normcase(path).encode()).hexdigest()[:24],
                 "available": Path(path).is_dir()} for path, item in projects.items()]

    def checked(self, collection, item=""):
        project = next((p for p in self.projects() if p["id"] == collection), None)
        if not project:
            raise ValueError("此项目已不在资料库中，请刷新列表。")
        root = Path(project["path"])
        target = (root / item).resolve() if item else root
        if not target.is_relative_to(root):
            raise ValueError("此文件不属于所选项目。")
        if not target.exists():
            raise ValueError("文件或目录暂时不可用，请恢复原位置后重试。")
        return project, target

    def row(self, project, path):
        stat = path.stat()
        folder = path.is_dir()
        capability = file_capability(str(path))
        return {"id": str(path), "title": path.name, "path": str(path), "is_folder": folder,
                "collection_id": project["id"], "project": project["name"],
                "relative": str(path.relative_to(project["path"])), "size": stat.st_size,
                "updated_at": stat.st_mtime, "type": "文件夹" if folder else capability.label if capability else path.suffix[1:].upper()}

    def files(self, params, cancelled, progress):
        collection, parent, query = params.get("collection", ""), params.get("parent", ""), str(params.get("query") or "").casefold()
        projects = self.projects()
        if collection:
            project, target = self.checked(collection, parent)
            if not target.is_dir():
                raise ValueError("请选择项目中的文件夹。")
            projects = [{**project, "scan_root": str(target)}]
        recursive = bool(query or not collection)
        found, seen, unavailable = [], set(), []
        def failed(_error):
            unavailable.append(project["name"])
        for project in projects:
            if cancelled.is_set():
                raise JobCancelled("已取消读取，本地文件未改变。")
            root = Path(project["path"])
            if not root.is_dir():
                unavailable.append(project["name"]); continue
            progress("正在读取项目资料：" + project["name"])
            for folder, dirs, files in os.walk(project.get("scan_root", root), onerror=failed, followlinks=False):
                dirs[:] = sorted(name for name in dirs if not name.startswith(".") and name not in EXCLUDED
                                 and not Path(folder, name).is_symlink() and not Path(folder, name).is_junction())
                names = files if recursive else [*dirs, *files]
                for name in names:
                    if cancelled.is_set():
                        raise JobCancelled("已取消读取，本地文件未改变。")
                    path = Path(folder, name)
                    if name.startswith(".") or (not path.is_dir() and path.suffix.lower() not in DOCUMENTS):
                        continue
                    if query and query not in str(path.relative_to(root)).casefold():
                        continue
                    try:
                        resolved = path.resolve()
                        key = os.path.normcase(str(resolved))
                        if not resolved.is_relative_to(root) or key in seen:
                            continue
                        found.append(self.row(project, resolved)); seen.add(key)
                    except OSError:
                        unavailable.append(project["name"])
                if not recursive:
                    break
        found.sort(key=lambda row: (not row["is_folder"], -row["updated_at"], row["title"].casefold(), row["path"]))
        page = max(1, int(params.get("page", 1))); start = (page - 1) * 100
        log.info("local_library_list projects=%s count=%s page=%s unavailable=%s", len(projects), len(found), page, len(set(unavailable)))
        return {"items": found[start:start + 100], "has_more": start + 100 < len(found), "total": len(found),
                "unavailable": sorted(set(unavailable))}

    def describe(self, params):
        project, path = self.checked(params["collection"], params["item"])
        if not path.is_file() or path.suffix.lower() not in DOCUMENTS:
            raise ValueError("请选择一份本地资料文件。")
        return self.row(project, path)

    def read(self, params):
        item = self.describe(params); path = Path(item["path"])
        kind = file_capability(str(path)).preview_kind
        result = {"path": str(path), "payload": {"kind": "preview", "preview_kind": kind}}
        if kind in {"markdown", "text"}:
            if item["size"] > 10 * 1024 * 1024:
                raise ValueError("文件超过 10 MiB，请在本机应用中打开。")
            from core.deliverable_editing import decode_text_bytes
            text, *_ = decode_text_bytes(path.read_bytes())
            result.update(text=text, format="markdown" if kind == "markdown" else "text")
        elif kind not in {"pdf", "image", "html"}:
            result["payload"]["preview_kind"] = "external"
        log.info("local_library_read project=%s kind=%s", item["collection_id"], kind)
        return result
