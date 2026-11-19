"""Public local files and project browsing; references use normal chat attachments."""
import hashlib
import logging
import os
from pathlib import Path

from core.desktop_jobs import JobCancelled
from core.file_capabilities import file_capability
from core.sidebar_projects import project_paths

log = logging.getLogger("desktop.library")
DOCUMENTS = frozenset(".md .markdown .txt .pdf .doc .docx .ppt .pptx .xls .xlsx .csv .tsv .html .htm .png .jpg .jpeg .gif .webp .svg".split())
EXCLUDED = frozenset({"node_modules", "__pycache__", "venv", "node_env", "git_bash_env", "ripgrep_env", "dist", "build", "out"})


class LocalLibrary:
    def __init__(self, service):
        self.s = service

    def public(self):
        root = (Path(self.s.config.data_dir) / "library" / "public").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return {"id": "public", "name": "公共资料", "path": str(root), "available": True}

    def folder(self, params):
        project, target = self.checked(params.get("collection", "public"), params.get("parent", ""))
        if not target.is_dir():
            raise ValueError("请选择一个资料文件夹。")
        return {"path": str(target), "collection_id": project["id"]}

    def mkdir(self, params):
        if params.get("collection") != "public":
            raise ValueError("请在公共资料中新建文件夹。")
        name = str(params.get("name", "")).strip()
        if not name or name in {".", ".."} or any(c in name for c in '/\\:*?"<>|') or name.endswith((".", " ")):
            raise ValueError("请输入有效的文件夹名称。")
        target = Path(self.folder(params)["path"]) / name
        try:
            target.mkdir()
        except FileExistsError:
            raise ValueError("同名文件或文件夹已存在，请使用其他名称。") from None
        log.info("local_library_folder_created collection=public")
        return self.row(self.public(), target)

    def import_files(self, params, cancelled, progress):
        if params.get("collection") != "public":
            raise ValueError("请将文件添加到公共资料。")
        target = Path(self.folder(params)["path"])
        added, failed = [], []
        paths = params.get("paths") or []
        log.info("local_library_import_start count=%s", len(paths))
        for raw in paths:
            if cancelled.is_set():
                raise JobCancelled(f"已取消添加，已添加的 {len(added)} 个文件保留在公共资料中，请刷新查看。")
            source = Path(raw).resolve()
            if not source.is_file():
                failed.append({"name": source.name, "reason": "文件不存在或不是普通文件"}); continue
            destination = target / source.name
            progress("正在添加：" + source.name)
            created = False
            try:
                with destination.open("xb") as output:
                    created = True
                    with source.open("rb") as incoming:
                        while chunk := incoming.read(1024 * 1024):
                            if cancelled.is_set():
                                raise JobCancelled("已取消添加，之前已添加的文件保留，请刷新查看。")
                            output.write(chunk)
                added.append(self.row(self.public(), destination))
            except (OSError, JobCancelled) as exc:
                if created:
                    destination.unlink()
                if isinstance(exc, JobCancelled):
                    raise
                failed.append({"name": source.name, "reason": "同名文件已存在，未覆盖" if isinstance(exc, FileExistsError) else "文件读取或写入失败"})
                log.warning("local_library_import_failed error=%s", type(exc).__name__)
        log.info("local_library_import_complete added=%s failed=%s", len(added), len(failed))
        return {"added": added, "failed": failed}

    def projects(self):
        projects = {}
        excluded = set()
        for item in self.s.config.get_projects():
            root = Path(item["path"]).expanduser().resolve()
            key = os.path.normcase(str(root))
            if item.get("archived") or item.get("hidden"):
                excluded.update(os.path.normcase(str(Path(folder).expanduser().resolve())) for folder in project_paths(item))
                continue
            projects[key] = {"name": item.get("name") or root.name, "path": str(root)}
        for entries in self.s.storage.list_conversations_by_workspace().values():
            root = Path(entries[0]["workspace_dir"]).resolve()
            key = os.path.normcase(str(root))
            if key not in excluded:
                projects.setdefault(key, {"name": root.name, "path": str(root)})
        return [{**item, "id": hashlib.sha256(os.path.normcase(path).encode()).hexdigest()[:24],
                 "available": Path(item["path"]).is_dir()} for path, item in projects.items()]

    def checked(self, collection, item=""):
        project = self.public() if collection == "public" else next((p for p in self.projects() if p["id"] == collection), None)
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
                dirs[:] = sorted(name for name in dirs if not name.startswith(".") and (project["id"] == "public" or name not in EXCLUDED)
                                 and not Path(folder, name).is_symlink() and not Path(folder, name).is_junction())
                names = files if recursive else [*dirs, *files]
                for name in names:
                    if cancelled.is_set():
                        raise JobCancelled("已取消读取，本地文件未改变。")
                    path = Path(folder, name)
                    if name.startswith(".") or (project["id"] != "public" and not path.is_dir() and path.suffix.lower() not in DOCUMENTS):
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
        if not path.is_file():
            raise ValueError("请选择一份本地资料文件。")
        return self.row(project, path)

    def read(self, params):
        item = self.describe(params); path = Path(item["path"])
        capability = file_capability(str(path))
        kind = capability.preview_kind if capability else "external"
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
