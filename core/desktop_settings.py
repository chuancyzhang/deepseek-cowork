"""Native settings pages backed by the existing configuration and services."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import threading
import uuid

log = logging.getLogger("desktop.settings")


def field(key, label, kind="text", **extra):
    return {"key": key, "label": label, "type": kind, **extra}


def action(identity, label, **extra):
    return {"id": identity, "label": label, **extra}


class DesktopSettings:
    methods = frozenset({"settings.page", "settings.page_save", "settings.action"})

    def __init__(self, service):
        self.s = service
        self.config = service.config
        self.lock = threading.RLock()

    def memory(self):
        from core.memory_store import MemoryStore
        return MemoryStore(self.config.get_chat_history_dir())

    @staticmethod
    def revision(values):
        return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def page(self, name, context=""):
        values, sections = {}, []
        title, description = "", ""
        if name == "preferences":
            title, description = "回答偏好", "设置通用交流偏好，从下一轮任务生效。"
            values = {"soul": self.memory().read_soul()}
            sections = [{"fields": [field("soul", "回答个性与偏好", "textarea", rows=12,
                         placeholder="例如：温和、好奇、坦率；先理解问题，再给出清晰而有主见的建议。")]}]
        elif name == "memory":
            title, description = "长期记忆", "管理稳定事实和偏好。修改从下一轮任务生效，不改写历史。"
            projects = self.config.get_projects()
            choices = [{"value": "", "label": "全局记忆"}] + [{"value": item["path"], "label": item.get("name") or item["path"]} for item in projects]
            if context and context not in {item["value"] for item in choices}:
                raise ValueError("请选择已有项目。")
            values = {"summary": self.memory().read_summary("workspace" if context else "global", context)}
            sections = [{"fields": [field("summary", "项目摘要" if context else "全局摘要", "textarea", rows=16)],
                         "actions": [action("memory.generate", "从历史更新", requires_saved=True)]}]
        elif name == "security":
            from core.data_security import FEATURES, CATEGORIES
            policy = self.config.get_data_security()
            values = {**{key: policy[key] for key in ("enabled", *FEATURES)},
                      **{"category:" + key: value for key, value in policy["categories"].items()}}
            title = "数据安全"
            sections = [{"fields": [field("enabled", "启用数据安全", "toggle")]},
                        {"title": "功能", "fields": [field(key, label, "toggle") for key, label in FEATURES.items()]},
                        {"title": "令牌化类别", "fields": [field("category:" + key, label, "toggle") for key, label in CATEGORIES.items()],
                         "actions": [action("security.events", "查看处理记录")]}]
        elif name == "storage":
            title = "文件与存储"
            values = {"default_workspace": self.config.get("default_workspace", ""),
                      "chat_workspace_root": self.config.get_chat_workspace_root(),
                      "history_dir": self.config.get_chat_history_dir(), "data_dir": self.config.data_dir}
            sections = [{"fields": [field("default_workspace", "默认工作区", "path"),
                         field("chat_workspace_root", "独立聊天目录", "path", help="只影响新建或尚未绑定目录的对话。"),
                         field("history_dir", "聊天记录与记忆目录", "path", read_only=True),
                         field("data_dir", "应用数据目录", "path", read_only=True)],
                         "actions": [action("storage.open", "打开数据目录")]}]
        elif name == "experiments":
            from core.browser_backend import normalize_policy as browser_policy
            values = {"external_browser": browser_policy(self.config.get("experimental_browser_automation"))["enabled"]}
            title, description = "实验功能", "按需启用，设置从下一轮任务生效。"
            sections = [{"title": "浏览器", "fields": [field("external_browser", "使用外部 Chrome / Edge", "toggle",
                         help="关闭后使用工作区内置网页。正在运行的浏览任务结束后才能切换。")]}]
        elif name == "components":
            from core import runtime_components as runtime
            title = "组件与依赖"
            sources = runtime.normalize_download_sources(self.config.get("download_sources"))
            values = {"python_source": sources["python"]["selected"]}
            values["dependency_timeout"] = self.config.get("skill_dependency_install_timeout_seconds", 600)
            sections = [{"title": "下载来源", "fields": [field("python_source", "Python 依赖", "select", options=[
                         {"value": item["id"], "label": item["name"]} for item in runtime.source_options("python", sources)]),
                         field("dependency_timeout", "技能依赖安装超时（秒）", "number", min=30, max=7200)],
                         "actions": [action("source.python", "测试 Python 来源")]}]
            items = []
            for identity in ("documents", "data-analysis"):
                spec = runtime.TOOLKITS[identity]
                status = runtime.toolkit_status(identity)
                items.append({"id": identity, "name": spec["name"], "description": spec["description"],
                              "status": "已就绪" if status["healthy"] else "需要修复" if status["installed"] else "未安装",
                              "error": status.get("health_error", ""), "actions": [] if spec.get("bundled") else [
                              action("component.install:" + identity, "修复" if status["installed"] else "安装"),
                              *([action("component.remove:" + identity, "卸载", title="卸载组件？", confirm="仅移除此工具包，不删除对话或工作文件。") ] if status["installed"] else [])]})
            sections.append({"title": "运行组件", "items": items})
        elif name == "logs":
            title = "运行日志"
            values = {"days": 1, "keyword": ""}
            sections = [{"fields": [field("days", "时间范围", "select", transient=True, options=[{"value": 1, "label": "今天"}, {"value": 7, "label": "最近 7 天"}]),
                         field("keyword", "关键词", transient=True)], "actions": [action("logs.query", "查看日志"),
                         action("logs.export", "导出日志", save_name="Cowork-运行日志.log"), action("storage.logs", "打开日志目录")]}]
        elif name == "update":
            return self.s.updates.page()
        elif name == "im":
            return self.s.messaging.page()
        else:
            raise ValueError("设置页面不存在。")
        result = {"title": title, "description": description, "values": values, "sections": sections,
                  "revision": self.revision(values), "context": context}
        if name == "memory":
            result["contexts"] = choices
        return result

    def save(self, name, p):
        context = str(p.get("context") or "")
        current = self.page(name, context)
        if p.get("revision") != current["revision"]:
            raise ValueError("设置已被其他操作修改。编辑已保留，请刷新后合并。")
        # Older local form drafts can still contain controls retired from these pages.
        # Do not let them overwrite runtime settings now managed elsewhere.
        retired = {"experiments": {"context_target"}, "components": {"node_source"}}
        values = {key: value for key, value in (p.get("value") or {}).items() if key not in retired.get(name, set())}
        if set(values) - set(current["values"]):
            raise ValueError("不支持的设置项。")
        v = {**current["values"], **values}
        if name == "preferences":
            self.memory().save_soul(str(v["soul"]))
        elif name == "memory":
            self.memory().save_summary(str(v["summary"]), "workspace" if context else "global", context)
        elif name == "security":
            from core.data_security import CATEGORIES, FEATURES
            self.config.set_data_security({**{key: v[key] for key in ("enabled", *FEATURES)}, "categories": {key: v["category:" + key] for key in CATEGORIES}})
        elif name == "storage":
            with self.config.batch_save():
                for key in ("default_workspace", "chat_workspace_root"):
                    value = str(v[key]).strip()
                    if value and not Path(value).expanduser().is_dir():
                        raise ValueError("目录不存在，请选择已有目录。")
                    self.config.set(key, value)
        elif name == "experiments":
            # Keep the original backend coordinator and task-snapshot semantics.
            self.config.set_experimental_browser_automation({"enabled": bool(v["external_browser"])})
        elif name == "components":
            from core import runtime_components as runtime
            sources = runtime.normalize_download_sources(self.config.get("download_sources"))
            if v["python_source"] not in {item["id"] for item in runtime.source_options("python", sources)}:
                raise ValueError("下载来源不存在。")
            sources["python"]["selected"] = v["python_source"]
            timeout = int(v["dependency_timeout"])
            if not 30 <= timeout <= 7200:
                raise ValueError("安装超时应为 30 到 7200 秒。")
            with self.config.batch_save():
                self.config.set("download_sources", sources)
                self.config.set("skill_dependency_install_timeout_seconds", timeout)
        elif name == "im":
            return self.s.messaging.save(v, p)
        else:
            raise ValueError("此页面没有需要保存的设置。")
        log.info("settings_saved page=%s", name)
        result = self.page(name, context)
        return {"saved": True, "values": result["values"], "revision": result["revision"]}

    def dispatch(self, method, p):
        name = str(p.get("page") or "")
        with self.lock:
            if method == "settings.page":
                return self.page(name, str(p.get("context") or ""))
            if method == "settings.page_save":
                return self.save(name, p)
            identity = str(p.get("action") or "")
            allowed = {item["id"] for section in self.page(name, str(p.get("context") or ""))["sections"] for item in [
                       *section.get("actions", []), *(action for record in section.get("items", []) for action in record.get("actions", []))]}
            if identity not in allowed:
                raise ValueError("此操作不可用，请刷新页面。")
            return self.perform(identity, p)

    def perform(self, identity, p):
        if identity in {"storage.open", "storage.logs"}:
            directory = Path(self.config.data_dir) / ("logs" if identity.endswith("logs") else "")
            directory.mkdir(parents=True, exist_ok=True)
            return {"open_path": str(directory)}
        if identity == "security.events":
            from core.data_security import recent_events
            return {"detail": {"title": "处理记录", "text": json.dumps(recent_events(), ensure_ascii=False, indent=2)}}
        if identity.startswith("logs."):
            def query(cancelled, progress):
                from core.runtime_logs import query_logs
                values = p.get("values") or {}
                result = query_logs(self.config.data_dir, days=int(values.get("days") or 1), keyword=str(values.get("keyword") or ""),
                                    limit=100000 if identity == "logs.export" else 500, cancelled=cancelled.is_set)
                content = "\n".join(record.display_text() for record in result.records)
                if identity == "logs.export":
                    target = Path(p["path"]).resolve()
                    from core.runtime_logs import collect_files, validate_export_target
                    files, _ = collect_files(self.config.data_dir)
                    validate_export_target(target, self.config.data_dir, files)
                    target.write_text(content, encoding="utf-8")
                return {"detail": {"title": f"运行日志 · {len(result.records)}/{result.total} 条", "text": content or "此范围内没有日志。"},
                        "message": "；".join(result.issues) if result.issues else ""}
            return self.s.jobs.submit("读取运行日志", query)
        if identity.startswith(("source.", "component.")):
            from core import runtime_components as runtime
            from core.desktop_jobs import JobCancelled
            sources = runtime.normalize_download_sources(self.config.get("download_sources"))
            def install(cancelled, progress):
                def report(*args):
                    if cancelled.is_set():
                        raise JobCancelled("已取消组件操作，已有文件保留。")
                    message = next((arg for arg in reversed(args) if isinstance(arg, str)), "正在处理组件…")
                    progress(message)
                report()
                if identity.startswith("source."):
                    kind = identity.split(".")[1]
                    result = runtime.test_source(kind, runtime.selected_source(kind, sources))
                    return {"detail": {"title": "下载来源测试", "text": str(result)}}
                operation, component = identity.split(":")
                if component not in {"documents", "data-analysis"}:
                    raise ValueError("组件不存在。")
                if operation == "component.remove":
                    result = runtime.uninstall_toolkit(component)
                else:
                    result = runtime.install_toolkit(component, runtime.selected_source("python", sources), progress_callback=report, force=True)
                log.info("component_completed action=%s", identity)
                return {"refresh": True, "message": "组件操作已完成。"}
            return self.s.jobs.submit("组件管理", install)
        if identity == "memory.generate":
            return self.generate_memory(p)
        if identity.startswith("im."):
            return self.s.messaging.action(identity, p)
        if identity.startswith("update."):
            return self.s.updates.action(identity, p)
        raise ValueError("不支持的设置操作。")

    def generate_memory(self, p):
        context = str(p.get("context") or "")
        original = self.page("memory", context)["values"]["summary"]
        profile = self.config.get_model_profile(self.config.get_selected_model_id())
        if not profile or not profile.get("api_key"):
            raise ValueError("请先在模型与服务中配置一个模型。")
        def generate(cancelled, progress):
            from core import memory_update as update
            from core.desktop_jobs import JobCancelled
            from core.llm.factory import LLMFactory
            def report(message):
                if cancelled.is_set():
                    raise JobCancelled("已取消记忆整理，原有记忆保留。")
                progress(message)
            report("正在读取历史…")
            transcripts = self.s.storage.iter_conversation_transcripts()
            if context:
                transcripts = update.filter_transcripts_for_workspace(transcripts, context)
            state = update.memory_update_scope_state(self.config.get_chat_history_dir(), "workspace" if context else "global", context)
            transcripts = update.filter_transcripts_for_memory_update(transcripts, state.get("last_processed_at", 0))
            if not transcripts:
                return {"message": "没有新的历史记录，现有记忆保持不变。"}
            provider = LLMFactory.create_provider_from_profile(profile)
            result = update.generate_memory_update(provider, original, transcripts, progress_callback=report)
            report("整理完成，请核对后保存。")
            content = result["content"] if isinstance(result, dict) else result
            return {"draft_values": {"summary": content}, "message": "记忆草稿已生成，核对后点击保存。"}
        return self.s.jobs.submit("从历史整理记忆", generate)


class DesktopSettingsAPI:
    methods = frozenset(['settings.get', 'settings.save_model', 'settings.select_model', 'settings.permission', 'settings.theme', 'settings.context', 'settings.import'])

    def __init__(self, config, themes, publish, public_settings, store):
        self.config, self.themes, self.publish, self.public_settings, self.store = config, themes, publish, public_settings, store
        self.methods = self.methods | {"host.recovery.get", "host.recovery.save"}

    def dispatch(self, method, p):
        cid = str(p.get("conversation_id") or "")
        if method in {"host.recovery.get", "host.recovery.save"}:
            import base64
            from core.credential_protection import create_credential_protector
            protector = create_credential_protector(self.config.data_dir)
            if method == "host.recovery.get":
                saved = self.store.versioned_state("host_forms", None)
                value = json.loads(protector.unprotect(base64.b64decode(saved["value"]))) if saved["value"] else {}
                return {"value": value, "revision": saved["revision"]}
            value = p.get("value")
            if not isinstance(value, dict):
                raise ValueError("表单恢复副本格式无效。")
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("表单恢复副本超过保存上限，当前输入仍保留。")
            encrypted = base64.b64encode(protector.protect(raw)).decode("ascii")
            result = self.store.put_state("host_forms", encrypted, p.get("revision"))
            log.info("host_forms_preserved forms=%s", len(value))
            return result
        if method == "settings.get":
            return self.public_settings()
        if method == "settings.save_model":
            channel_id = str(p.get("channel_id") or uuid.uuid4().hex)
            channels = self.config.get_model_channels()
            existing = next((c for c in channels if c.get("channel_id") == channel_id), {})
            api_key = str(p.get("api_key") or "").strip() or existing.get("api_key", "")
            model_id = str(p.get("model_id") or uuid.uuid4().hex)
            previous_model = next((model for model in existing.get("models", []) if model.get("id") == model_id), {})
            model = {**previous_model, "id": model_id, "model_name": p["model_name"], "display_name": p.get("display_name") or p["model_name"],
                     "api_protocol": p.get("api_protocol") or previous_model.get("api_protocol") or "chat_completions"}
            for key in ("supports_vision", "supports_tools", "supports_reasoning", "reasoning_effort", "context_length", "max_tokens", "temperature"):
                if key in p:
                    model[key] = p[key]
            models = [model if item.get("id") == model_id else item for item in existing.get("models", [])]
            if not previous_model:
                models.append(model)
            channel = {**existing, "channel_id": channel_id, "display_name": p.get("name") or existing.get("display_name") or "模型服务",
                       "provider_type": p.get("provider_type") or existing.get("provider_type") or "openai", "base_url": p["base_url"], "api_key": api_key,
                       "models": models}
            channels = [channel if item.get("channel_id") == channel_id else item for item in channels]
            if not existing:
                channels.append(channel)
            self.config.set_model_channels(channels, model_id)
            return self.public_settings()
        if method == "settings.select_model":
            self.config.set_selected_model_id(p["model_id"])
            return self.public_settings()
        if method == "settings.permission":
            self.config.set_god_mode(bool(p["enabled"]))
            return self.public_settings()
        if method == "settings.theme":
            appearance = self.themes.load_appearance()
            self.themes.save_appearance({**appearance, "mode": p["theme"]})
            self.publish({"event": "appearance_changed"})
            return {"saved": True}
        if method == "settings.context":
            from core.long_context import TARGETS
            target = p.get("target_tokens")
            if target not in (0, *TARGETS) or type(target) is not int:
                raise ValueError("请选择未启用、300K 或 1M。")
            self.config.set_experimental_long_context({"enabled": bool(target), "target_tokens": target or TARGETS[0]})
            log.info("context_policy_saved target=%s next_run=true", target)
            return self.public_settings()
        if method == "settings.import":
            source = Path(p["path"]).resolve()
            if source.name != "config.json" or source == Path(self.config.config_path).resolve():
                raise ValueError("请选择旧版数据目录中的 config.json。")
            config = json.loads(source.read_text(encoding="utf-8-sig"))
            if not isinstance(config.get("model_channels"), list):
                raise ValueError("文件中没有可导入的模型服务。")
            self.config.set_model_channels(config["model_channels"], config.get("selected_model_id"))
            return self.public_settings()
        raise ValueError("不支持的操作：" + method)
