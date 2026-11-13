"""Desktop management adapters; domain validation remains in the shared core."""
from __future__ import annotations

import copy
from pathlib import Path
import threading
import time
import uuid

from core.theme_service import ThemeRepository, resolve_theme
from core.theme_tokens import default_design_tokens
from core.theme_presets import BUILTIN_THEMES, builtin_theme


class Management:
    methods = frozenset({
        "jobs.list", "jobs.get", "jobs.cancel", "skills.inventory", "skills.get", "skills.enable",
        "skills.configure", "skills.read", "skills.write", "skills.import", "skills.export", "skills.delete",
        "mcp.list", "mcp.save", "mcp.delete", "mcp.test", "experts.list", "experts.save", "experts.delete",
        "favorites.list", "favorites.save", "favorites.delete", "favorites.history", "favorites.run",
        "variables.list", "variables.save", "variables.delete", "variables.undo",
        "themes.list", "themes.resolve", "themes.activate", "themes.appearance", "themes.delete",
        "themes.import", "themes.export", "themes.preview", "themes.commit_preview", "themes.discard_preview", "themes.fonts", "settings.general",
        "settings.delete_model", "settings.channels", "settings.channel_save",
    })

    def __init__(self, service):
        self.s = service
        self.config = service.config
        self.lock = threading.RLock()
        self.themes = ThemeRepository(self.config.data_dir)

    def theme(self, theme_id=None, *, dark=False, reading=None):
        identity = theme_id or self.themes.load().active_theme_id
        profile = builtin_theme(identity, dark=dark) if identity in BUILTIN_THEMES else self.themes.get_theme(identity)
        if not profile:
            raise ValueError("主题不存在。")
        profile = copy.deepcopy(profile)
        profile["overrides"] = {**profile.get("overrides", {}), **(reading or {})}
        return {**resolve_theme(profile, default_design_tokens()),
                **{key: profile.get(key) or {} for key in ("surfaces", "components", "content", "workspace_scene")}}

    def skill_change(self, action, names):
        from core.skill_catalog import SkillChangeEvent
        self.s.state.skill_catalog.publish_change(SkillChangeEvent.create(action, names, source="desktop"))
        self.s.publish({"event": "skills_changed"})

    @staticmethod
    def checked(result):
        if isinstance(result, tuple):
            ok, message = result
            if not ok:
                raise ValueError(message)
            return {"saved": True, "message": message}
        if isinstance(result, dict) and (result.get("ok") is False or result.get("status") == "failed"):
            raise ValueError(result.get("error") or result.get("message") or "操作失败。")
        return result

    def dispatch(self, method, p):
        with self.lock:
            return self._dispatch(method, p)

    def _dispatch(self, method, p):
        action = method.split(".")[1]
        if method.startswith("jobs."):
            return self.s.jobs.list() if action == "list" else getattr(self.s.jobs, action)(str(p["id"]))
        if method.startswith("skills."):
            catalog = self.s.state.skill_catalog
            snapshot = catalog.snapshot()
            manager = snapshot.manager
            name = str(p.get("name") or "")
            from core.skill_manager import RETIRED_SKILLS
            if name in RETIRED_SKILLS:
                raise ValueError(RETIRED_SKILLS[name])
            if action == "inventory":
                items = snapshot.display_skills()
                for item in items:
                    item["editable"] = bool(manager.is_skill_editable(item["name"]))
                return items
            if action == "get":
                record = next((item for item in snapshot.display_skills() if item["name"] == name), None)
                if not record:
                    raise ValueError("技能不存在。")
                fields = manager.get_skill_config_fields(name)
                values = self.config.get_skill_config(name)
                # Passwords can be replaced; listing never exposes their values.
                public = {key: value for key, value in values.items() if not any(field.get("name") == key and field.get("kind") == "secret" for field in fields)}
                return {**record, "fields": fields, "values": public, "configured_fields": list(values),
                        "files": self.checked(manager.list_skill_files(name))}
            if action == "enable":
                self.config.set_skill_enabled(name, bool(p["enabled"]), persist_strict=True)
                self.skill_change("enabled" if p["enabled"] else "disabled", [name])
                return {"saved": True}
            if action == "configure":
                values = {**self.config.get_skill_config(name), **p["values"]}
                status = manager.get_skill_config_status(name, values)
                if not status.get("complete"):
                    raise ValueError("；".join([*("请填写：" + name for name in status.get("missing_required", [])), *status.get("config_errors", [])]))
                self.config.set_skill_config(name, values)
                from core.connection_retirement import complete_skill_reconfiguration
                complete_skill_reconfiguration(self.config, name)
                self.skill_change("updated", [name])
                return {"saved": True}
            if action == "read":
                return self.checked(manager.read_skill_file(name, p["path"]))
            if action == "write":
                current = self.checked(manager.read_skill_file(name, p["path"]))
                if current["content"] != p["original"]:
                    raise ValueError("技能文件已被其他操作修改。当前编辑已保留，请重新载入后合并。")
                result = self.checked(manager.write_skill_file(name, p["path"], p["content"]))
                self.skill_change("updated", [name])
                return result
            if action == "import":
                def install(cancelled, progress):
                    result = self.checked(manager.import_skill(p["path"], enabled=False, prepare_dependencies=False))
                    self.skill_change("created", manager.last_imported_skill_names)
                    return result
                return self.s.jobs.submit("导入技能", install)
            if action == "export":
                return self.checked(manager.export_skill(name, p["path"]))
            if action == "delete":
                result = self.checked(manager.delete_skill(name))
                if result.get("status") != "deleted":
                    raise ValueError(result.get("message") or "此技能不能删除。")
                self.skill_change("deleted", [name])
                return result
        if method.startswith("mcp."):
            servers = self.config.get_mcp_servers()
            if action == "list":
                return servers
            if action == "test":
                from core.mcp_client import test_mcp_server_connection
                target = next((server for server in servers if server["id"] == p.get("id")), None)
                if not target:
                    raise ValueError("连接不存在，请先保存。")
                return self.s.jobs.submit("测试 " + str(target.get("name") or target["id"]),
                    lambda cancelled, progress: self.checked(test_mcp_server_connection(target, config_manager=self.config,
                        skill_manager=self.s.state.skill_catalog.snapshot().manager)))
            if action == "save":
                from core.config_manager import parse_mcp_servers_json
                incoming = parse_mcp_servers_json(p["value"])
                for server in incoming:
                    if any(item["id"] == server.get("id") for item in servers):
                        self.config.prepare_mcp_reconfiguration(server["id"])
                self.config.upsert_mcp_servers(incoming)
            elif action == "delete":
                self.config.prepare_mcp_reconfiguration(p["id"])
                self.config.set_mcp_servers([item for item in servers if item["id"] != p["id"]])
            self.s.state.skill_catalog.reload(reason="desktop_mcp_change")
            return {"saved": True}
        if method.startswith("experts."):
            items = self.config.get_agent_profiles()
            if action == "list":
                return items
            if action == "save":
                value = p["value"]
                if not str(value.get("name") or "").strip() or not str(value.get("system_prompt") or "").strip():
                    raise ValueError("请填写专家名称和工作指引。")
                value = {**value, "id": value.get("id") or uuid.uuid4().hex, "updated_at": int(time.time())}
                items = [item for item in items if item["id"] != value["id"]] + [value]
            else:
                items = [item for item in items if item["id"] != p["id"]]
            self.config.set_agent_profiles(items)
            return {"saved": True}
        if method.startswith("favorites."):
            if action == "list":
                return self.config.get_favorites()
            if action == "history":
                return self.config.get_favorite_run_history()
            if action == "save":
                value = p["value"]
                if not str(value.get("name") or "").strip() or not str(value.get("prompt") or "").strip():
                    raise ValueError("请填写任务名称和内容。")
                return self.config.upsert_favorite(value)
            if action == "delete":
                self.config.set_favorites([item for item in self.config.get_favorites() if item["id"] != p["id"]])
                return {"saved": True}
            return self.s.scheduler.run(p["id"])
        if method.startswith("variables."):
            if action == "list":
                return self.config.list_app_variables(query=str(p.get("query") or ""))
            if action == "save":
                return self.config.upsert_app_variable(**p["value"])
            if action == "undo":
                return self.config.restore_previous_app_variables()
            return self.config.delete_app_variable(p["id"])
        if method.startswith("themes."):
            if action == "fonts":
                from core.system_fonts import installed_font_families
                return installed_font_families()
            if action == "list":
                snapshot = self.themes.load()
                appearance = self.themes.load_appearance()
                return {"active": snapshot.active_theme_id, "appearance": appearance,
                        "themes": [{"id": key, "name": value[0], "description": value[1], "builtin": True} for key, value in BUILTIN_THEMES.items()] + list(snapshot.themes),
                        "preview": self.themes.load_preview()}
            if action == "resolve":
                appearance = self.themes.load_appearance()
                return self.theme(p.get("id"), dark=bool(p.get("dark")), reading=appearance["reading"])
            if action == "activate":
                self.themes.activate_theme(p["id"])
            elif action == "appearance":
                self.themes.save_appearance(p["value"])
            elif action == "delete":
                if p["id"] in BUILTIN_THEMES:
                    raise ValueError("内置主题不能删除。")
                self.themes.delete_theme(p["id"])
            elif action == "import":
                return self.themes.import_theme(p["path"], default_design_tokens())
            elif action == "export":
                return self.themes.export_theme(p["id"], default_design_tokens(), target_path=p["path"])
            elif action == "preview":
                preview = self.themes.load_preview()
                return {"preview": preview, "resolved": resolve_theme(preview, default_design_tokens()) if preview else None}
            elif action == "discard_preview":
                self.themes.clear_preview(p["id"], preview_revision=p["revision"])
            elif action == "commit_preview":
                self.themes.commit_preview(preview_id=p["id"], preview_revision=p["revision"], name=p["name"],
                    activate=True, default_tokens=default_design_tokens())
            self.s.publish({"event": "appearance_changed"})
            return self._dispatch("themes.list", {})
        if method == "settings.general":
            permitted = {"chat_workspace_root", "download_sources", "experimental_browser_automation", "experimental_long_context", "context_budget_ratio", "deepseek_v4_context_window_tokens", "skill_dependency_install_timeout_seconds"}
            if "value" in p:
                unknown = set(p["value"]) - permitted
                if unknown:
                    raise ValueError("不支持的设置项。")
                with self.config.batch_save():
                    for key, value in p["value"].items():
                        setter = getattr(self.config, "set_" + key, None)
                        if setter:
                            setter(value)
                        else:
                            if key == "context_budget_ratio" and not .1 <= float(value) <= 1:
                                raise ValueError("上下文使用比例应在 10% 到 100% 之间。")
                            if key in {"deepseek_v4_context_window_tokens", "skill_dependency_install_timeout_seconds"} and int(value) <= 0:
                                raise ValueError("请输入大于零的数值。")
                            self.config.set(key, value)
            return {key: self.config.get(key) for key in permitted}
        if method == "settings.channels":
            return [{**channel, "api_key": "", "configured": bool(channel.get("api_key"))} for channel in self.config.get_model_channels()]
        if method == "settings.channel_save":
            channels = self.config.get_model_channels()
            value = p["value"]
            identity = value.get("channel_id") or uuid.uuid4().hex
            previous = next((item for item in channels if item["channel_id"] == identity), {})
            if not str(value.get("display_name") or "").strip() or not str(value.get("base_url") or "").startswith(("https://", "http://")):
                raise ValueError("请填写服务名称和有效的 HTTP 或 HTTPS 地址。")
            if not value.get("models") or any(not str(model.get("model_name") or "").strip() for model in value["models"]):
                raise ValueError("请至少添加一个模型，并填写每个模型的标识。")
            value = {**previous, **value, "channel_id": identity, "api_key": value.get("api_key") or previous.get("api_key", "")}
            self.config.set_model_channels([item for item in channels if item["channel_id"] != identity] + [value], self.config.get_selected_model_id())
            return self.s.public_settings()
        if method == "settings.delete_model":
            channels = self.config.get_model_channels()
            for channel in channels:
                channel["models"] = [model for model in channel.get("models", []) if model["id"] != p["id"]]
            self.config.set_model_channels(channels, self.config.get_selected_model_id())
            return self.s.public_settings()
        raise ValueError("不支持的管理操作。")
