"""Sidebar organization independent of project workspaces and chat history."""

import logging
import time
from uuid import UUID

log = logging.getLogger("desktop.sidebar")

PROJECT_LIST_INITIAL_LIMIT = 5
SIDEBAR_SEARCH_PAGE_SIZE = 50
SIDEBAR_STATE = "sidebar_layout"


class DesktopSidebar:
    methods = frozenset({"sidebar.create_group", "sidebar.move", "sidebar.pin_project"})

    def __init__(self, service):
        self.service = service
        self.config, self.store, self.storage = service.config, service.store, service.storage

    def read(self):
        return self.store.versioned_state(SIDEBAR_STATE, {"groups": [], "chats": {}, "projects": {}})

    def conversation_page(self, *, limit=500, offset=0, include_sidebar=False):
        page = self.storage.list_conversation_summaries(limit=limit, offset=offset)
        conversations = self.store.summarize_conversations(page)
        if not include_sidebar:
            return conversations
        sidebar = self.read()
        visible = {row["id"] for row in page}
        ids = set(sidebar["value"]["chats"]) | set(self.storage.pinned_conversation_ids())
        extras = self.storage.list_conversation_summaries(conversation_ids=sorted(ids - visible))
        return {"conversations": conversations, "sidebar": sidebar,
                "sidebar_conversations": self.store.summarize_conversations(extras)}

    def _project(self, path, placements):
        path = self.config._normalize_project_path(path)
        key = self.config._project_key(path)
        if not key:
            raise ValueError("请选择有效的项目。")
        for project in self.config.get_projects():
            if self.config._project_key(project["path"]) == key:
                if project["archived"]:
                    raise ValueError("请先从归档恢复这个项目。")
                return key, project["path"]
        if key in placements:
            return key, placements[key]["path"]
        for record in self.storage.list_conversation_summaries():
            meta = record["meta"]
            if meta.get("archived") or meta.get("team_space_required") or meta.get("workspace_source") == "chat":
                continue
            if self.config._project_key(meta.get("workspace_dir") or meta.get("workspace_path")) == key:
                return key, path
        raise ValueError("项目已不在侧栏，请刷新后重试。")

    def _move(self, layout, entry, group_id):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ValueError("请选择要移动的项目或聊天。")
        if entry.get("kind") == "chat":
            record = self.storage.get_conversation_record(entry["id"])
            if not record or record["meta"].get("archived"):
                raise ValueError("聊天已不可用或已归档。")
            if group_id:
                layout["chats"][entry["id"]] = group_id
            else:
                layout["chats"].pop(entry["id"], None)
        elif entry.get("kind") == "project":
            key, path = self._project(entry["id"], layout["projects"])
            project = layout["projects"].setdefault(key, {"path": path})
            if group_id:
                project["group_id"] = group_id
            else:
                project.pop("group_id", None)
                if "pinned_at" not in project:
                    del layout["projects"][key]
        else:
            raise ValueError("请选择要移动的项目或聊天。")

    def dispatch(self, method, params):
        with self.service.lock:
            snapshot = self.read()
            layout = snapshot["value"]
            group_id = params.get("group_id")
            if method == "sidebar.create_group":
                try:
                    group_id = UUID(group_id).hex
                except (TypeError, ValueError, AttributeError) as exc:
                    raise ValueError("分组标识无效，请重新新建。") from exc
                if any(group["id"] == group_id for group in layout["groups"]):
                    return snapshot
                name = params.get("name")
                if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
                    raise ValueError("请输入 1 至 60 个字符的分组名称。")
                if params.get("entry") is not None:
                    self._move(layout, params["entry"], group_id)
                layout["groups"].append({"id": group_id, "name": name.strip()})
            elif method == "sidebar.move":
                if group_id is not None and not any(group["id"] == group_id for group in layout["groups"]):
                    raise ValueError("分组已不可用，请重新选择。")
                self._move(layout, params.get("entry"), group_id)
            elif method == "sidebar.pin_project":
                if not isinstance(params.get("path"), str) or not isinstance(params.get("pinned"), bool):
                    raise ValueError("请选择有效的项目和置顶状态。")
                key, path = self._project(params["path"], layout["projects"])
                project = layout["projects"].setdefault(key, {"path": path})
                if params["pinned"]:
                    project["pinned_at"] = project.get("pinned_at") or time.time()
                else:
                    project["pinned_at"] = 0
            else:
                raise ValueError("不支持的侧栏操作。")
            saved = self.store.put_state(SIDEBAR_STATE, layout, expected_revision=snapshot["revision"])
            result = {"value": layout, "revision": saved["revision"]}
            log.info("sidebar_layout_saved action=%s group=%s revision=%s", method, group_id or "", saved["revision"])
            self.service.publish({"event": "sidebar_changed", "data": result})
            return result


def page_sidebar_search(projects, unassigned, limit):
    """Bound widget creation in render order; a project's header accompanies hits."""
    ordered = []
    pinned = [entry for project in projects for entry in project["sessions"] if entry.get("pinned")]
    pinned += [entry for entry in unassigned if entry.get("pinned")]
    for project in projects:
        if project.get("pinned"):
            ordered.extend(("session", entry["id"]) for entry in project["sessions"] if not entry.get("pinned"))
            if not project["sessions"]:
                ordered.append(("project", project["path"]))
    ordered.extend(("session", entry["id"]) for entry in sorted(pinned, key=lambda e: -int(e.get("updated_at") or 0)))
    for project in projects:
        if not project.get("pinned"):
            ordered.extend(("session", entry["id"]) for entry in project["sessions"] if not entry.get("pinned"))
            if not project["sessions"]:
                ordered.append(("project", project["path"]))
    ordered.extend(("session", entry["id"]) for entry in sorted(unassigned, key=lambda e: -int(e.get("updated_at") or 0)))
    ordered = list(dict.fromkeys(ordered))
    selected = set(ordered[:max(1, int(limit))])
    visible_projects = []
    for project in projects:
        sessions = [entry for entry in project["sessions"] if ("session", entry["id"]) in selected]
        if sessions or ("project", project["path"]) in selected:
            visible_projects.append({**project, "sessions": sessions})
    return (visible_projects, [entry for entry in unassigned if ("session", entry["id"]) in selected],
            max(0, len(ordered) - len(selected)))


def visible_sidebar_projects(projects, required_paths=(), *, expanded=False, query=False):
    projects = list(projects)
    if expanded or query:
        return projects
    required = set(required_paths)
    ordinary = [item for item in projects if not item.get("pinned") and item["path"] not in required]
    recent = sorted(ordinary, key=lambda item: (
        -int(item.get("updated_at") or 0),
        item.get("name", "").casefold(),
        item["path"],
    ))
    visible = required | {item["path"] for item in recent[:PROJECT_LIST_INITIAL_LIMIT]}
    return [item for item in projects if item.get("pinned") or item["path"] in visible]
