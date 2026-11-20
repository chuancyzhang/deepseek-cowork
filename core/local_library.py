"""Public local files and project browsing; references use normal chat attachments."""
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
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
        self.mutations = threading.RLock()

    def public(self):
        root = Path(self.s.config.data_dir).resolve() / "library" / "public"
        if root.resolve() != root:
            raise ValueError("公共资料位置已移动，请先恢复原位置。")
        root.mkdir(parents=True, exist_ok=True)
        return {"id": "public", "name": "公共资料", "path": str(root), "available": True}

    def folder(self, params):
        project, target = self.checked(params.get("collection", "public"), params.get("parent", ""))
        if not target.is_dir():
            raise ValueError("请选择一个资料文件夹。")
        return {"path": str(target), "collection_id": project["id"]}

    @staticmethod
    def entry_name(value):
        name = str(value or "").strip()
        stem = name.split(".", 1)[0].rstrip()
        if (not name or name in {".", ".."} or name.endswith(".")
                or any(ord(char) < 32 or char in '/\\:*?"<>|' for char in name)
                or re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", stem, re.IGNORECASE)):
            raise ValueError("请输入有效的名称。")
        return name

    def mkdir(self, params):
        if params.get("collection") != "public":
            raise ValueError("请在公共资料中新建文件夹。")
        name = self.entry_name(params.get("name"))
        with self.mutations:
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
        with self.mutations:
            return self._import_files(params, cancelled, progress)

    def _import_files(self, params, cancelled, progress):
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

    @staticmethod
    def revision(stat):
        return f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}"

    def _public_entry(self, item):
        raw = str(item.get("item") or "")
        project, path = self.checked("public", raw)
        root = Path(project["path"])
        if not raw or path == root or Path(os.path.abspath(root / raw)) != path:
            raise ValueError("请选择公共资料中的实际文件或文件夹。")
        if not item.get("revision") or item["revision"] != self.revision(path.stat()):
            raise ValueError("资料已变化，请刷新列表后重新选择。")
        return project, path

    @staticmethod
    def _move(source, destination, *, case_only=False):
        if os.path.lexists(destination) and not case_only:
            raise FileExistsError(destination.name)
        source.rename(destination)

    def rename(self, params):
        name = self.entry_name(params.get("name"))
        with self.mutations:
            project, original = self._public_entry(params)
            destination = original.with_name(name)
            if name != original.name:
                try:
                    self._move(original, destination, case_only=original == destination)
                except FileExistsError:
                    raise ValueError("同名文件或文件夹已存在，请使用其他名称。") from None
                except OSError as exc:
                    log.warning("local_library_rename_failed cause=%s", type(exc).__name__)
                    raise ValueError("重命名未完成，请确认资料未被占用后重试。") from None
                log.info("local_library_renamed directory=%s", destination.is_dir())
            return self.row(project, destination)

    def _recycle_root(self):
        root = Path(self.s.config.data_dir).resolve() / "library" / "recycle"
        if root.resolve() != root:
            raise ValueError("回收站位置已移动，请先恢复原位置。")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _recycled(self, batch_id):
        if not re.fullmatch(r"[0-9a-f]{32}", str(batch_id)):
            raise ValueError("回收记录不可用，请刷新后重试。")
        folder = self._recycle_root() / batch_id
        if folder.resolve() != folder:
            raise ValueError("回收记录的位置已变化，请刷新后重试。")
        try:
            record = json.loads((folder / "items.json").read_text(encoding="utf-8"))
            pending = [(folder / str(index), item) for index, item in enumerate(record["items"])
                       if (folder / str(index)).exists()]
            return record["time"], pending
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("local_library_recycle_record_failed batch=%s cause=%s", batch_id, type(exc).__name__)
            raise ValueError("回收记录不可用，请刷新后重试。") from None

    def trash(self, params, cancelled, progress):
        items = params.get("items")
        if not isinstance(items, list) or not items or any(not isinstance(item, dict) for item in items):
            raise ValueError("请选择要移到回收站的资料。")
        with self.mutations:
            targets = dict.fromkeys(self._public_entry(item)[1] for item in items)
            if any(parent in targets for target in targets for parent in target.parents):
                raise ValueError("请按文件夹或其中文件分别操作。")
            batch_id = uuid.uuid4().hex
            folder = self._recycle_root() / batch_id
            folder.mkdir()
            root = Path(self.public()["path"])
            entries = [{"name": target.name, "relative": str(target.relative_to(root)), "is_folder": target.is_dir()}
                       for target in targets]
            with (folder / "items.json").open("x", encoding="utf-8") as handle:
                json.dump({"time": time.time(), "items": entries}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            log.info("local_library_trash_start batch=%s count=%s", batch_id, len(entries))
            moved, failed = [], []
            for index, (target, item) in enumerate(zip(targets, entries)):
                if cancelled.is_set():
                    failed.extend({"name": pending["name"], "reason": "操作已停止"} for pending in entries[index:])
                    break
                progress(f"正在移到回收站（{index + 1}/{len(entries)}）")
                try:
                    self._move(target, folder / str(index))
                    moved.append(str(target))
                except OSError as exc:
                    log.warning("local_library_trash_failed batch=%s index=%s cause=%s", batch_id, index, type(exc).__name__)
                    failed.append({"name": item["name"], "reason": "文件暂不可用或被占用"})
            log.info("local_library_trash_complete batch=%s moved=%s failed=%s", batch_id, len(moved), len(failed))
            return {"id": batch_id, "moved": moved, "failed": failed}

    def trash_list(self):
        result = []
        with self.mutations:
            for folder in self._recycle_root().iterdir():
                if not folder.is_dir() or folder.is_symlink() or folder.is_junction() or not re.fullmatch(r"[0-9a-f]{32}", folder.name):
                    continue
                if not any(child.name.isdecimal() for child in folder.iterdir()):
                    continue
                when, pending = self._recycled(folder.name)
                if pending:
                    result.append({"id": folder.name, "time": when, "items": [item for _, item in pending]})
        return sorted(result, key=lambda batch: batch["time"], reverse=True)

    def restore(self, params, cancelled, progress):
        batch_id = str(params.get("id") or "")
        with self.mutations:
            _, pending = self._recycled(batch_id)
            root = Path(self.public()["path"])
            destinations = []
            for stored, item in pending:
                original = Path(item["relative"])
                if original.anchor or ".." in original.parts or not original.name:
                    raise ValueError("回收记录中的原位置无效。")
                target = root / original
                if target.parent.resolve() != target.parent or not target.parent.is_dir():
                    raise ValueError("原文件夹已移动或删除，请先恢复该文件夹的位置。")
                if os.path.lexists(target):
                    raise ValueError(f"原位置已有「{target.name}」，请先重命名现有资料。")
                destinations.append((stored, target))
            log.info("local_library_restore_start batch=%s count=%s", batch_id, len(destinations))
            restored, failed = [], []
            for index, (stored, target) in enumerate(destinations):
                if cancelled.is_set():
                    failed.extend({"name": entry.name, "reason": "还原已停止"} for _, entry in destinations[index:])
                    break
                progress(f"正在还原资料（{index + 1}/{len(destinations)}）")
                try:
                    self._move(stored, target)
                    restored.append(str(target))
                except OSError as exc:
                    log.warning("local_library_restore_failed batch=%s index=%s cause=%s", batch_id, index, type(exc).__name__)
                    failed.append({"name": target.name, "reason": "原位置暂不可写，资料仍在回收站"})
            if not failed:
                folder = self._recycle_root() / batch_id
                try:
                    (folder / "items.json").unlink(missing_ok=True)
                    folder.rmdir()
                except OSError:
                    log.warning("local_library_recycle_cleanup_failed batch=%s", batch_id)
            log.info("local_library_restore_complete batch=%s restored=%s failed=%s", batch_id, len(restored), len(failed))
            return {"restored": restored, "failed": failed}

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
                "revision": self.revision(stat),
                "editable": not folder and bool(capability and capability.editable
                    and capability.office_family not in {"word", "presentation"} and stat.st_size <= capability.max_bytes),
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
            public = project["id"] == "public"
            if not root.is_dir():
                unavailable.append(project["name"]); continue
            progress("正在读取项目资料：" + project["name"])
            for folder, dirs, files in os.walk(project.get("scan_root", root), onerror=failed, followlinks=False):
                dirs[:] = sorted(name for name in dirs if (public or not name.startswith(".") and name not in EXCLUDED)
                                 and not Path(folder, name).is_symlink() and not Path(folder, name).is_junction())
                names = files if recursive else [*dirs, *files]
                for name in names:
                    if cancelled.is_set():
                        raise JobCancelled("已取消读取，本地文件未改变。")
                    path = Path(folder, name)
                    if not public and (name.startswith(".") or not path.is_dir() and path.suffix.lower() not in DOCUMENTS):
                        continue
                    if query and query not in str(path.relative_to(root)).casefold():
                        continue
                    try:
                        if public and path.is_symlink():
                            continue
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
