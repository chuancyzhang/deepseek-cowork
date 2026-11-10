"""Native settings pages backed by the existing configuration and services."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import threading

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
            from core.long_context import normalize_policy
            from core.browser_backend import normalize_policy as browser_policy
            policy = normalize_policy(self.config.get("experimental_long_context"))
            values = {"context_target": policy["target_tokens"] if policy["enabled"] else 0,
                      "external_browser": browser_policy(self.config.get("experimental_browser_automation"))["enabled"]}
            title, description = "实验功能", "按需启用，设置从下一轮任务生效。"
            sections = [{"title": "长上下文", "fields": [field("context_target", "目标上限", "select", options=[
                         {"value": 0, "label": "未启用"}, {"value": 300000, "label": "300K"}, {"value": 1000000, "label": "1M"}])]},
                        {"title": "浏览器", "fields": [field("external_browser", "使用外部 Chrome / Edge", "toggle",
                         help="关闭后使用工作区内置网页。正在运行的浏览任务结束后才能切换。")]}]
        elif name == "components":
            from core import runtime_components as runtime
            title = "组件与依赖"
            sources = runtime.normalize_download_sources(self.config.get("download_sources"))
            values = {kind + "_source": sources[kind]["selected"] for kind in ("python", "node")}
            values["dependency_timeout"] = self.config.get("skill_dependency_install_timeout_seconds", 600)
            sections = [{"title": "下载来源", "fields": [field(kind + "_source", label, "select", options=[
                         {"value": item["id"], "label": item["name"]} for item in runtime.source_options(kind, sources)])
                         for kind, label in (("python", "Python 依赖"), ("node", "Node.js"))] + [
                         field("dependency_timeout", "技能依赖安装超时（秒）", "number", min=30, max=7200)],
                         "actions": [action("source.python", "测试 Python 来源"), action("source.node", "测试 Node.js 来源")]}]
            items = []
            for identity, spec in runtime.TOOLKITS.items():
                status = runtime.toolkit_status(identity)
                items.append({"id": identity, "name": spec["name"], "description": spec["description"],
                              "status": "已就绪" if status["healthy"] else "需要修复" if status["installed"] else "未安装",
                              "error": status.get("health_error", ""), "actions": [] if spec.get("bundled") else [
                              action("component.install:" + identity, "修复" if status["installed"] else "安装"),
                              *([action("component.remove:" + identity, "卸载", title="卸载组件？", confirm="仅移除此工具包，不删除对话或工作文件。") ] if status["installed"] else [])]})
            node = runtime.node_runtime_status()
            items.append({"id": "node", "name": "Node.js", "status": node.get("version") or "未安装", "actions": [action("component.install:node", "重新安装" if node["installed"] else "安装")]})
            speech = runtime.speech_to_text_component_status()
            items.append({"id": "speech", "name": "本地语音转文字", "status": "已安装" if speech.get("installed") else "未安装", "error": speech.get("health_error", ""),
                          "actions": [action("component.install:speech", "修复" if speech.get("installed") else "安装"),
                                      *([action("component.remove:speech", "卸载", title="卸载语音组件？", confirm="本地识别模型将移除，已有音频和转写结果保留。") ] if speech.get("installed") else [])]})
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
        values = p.get("value") or {}
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
            target = int(v["context_target"])
            if target not in (0, 300000, 1000000):
                raise ValueError("请选择有效的上下文目标。")
            # Keep the original backend coordinator and task-snapshot semantics.
            self.config.set_experimental_browser_automation({"enabled": bool(v["external_browser"])})
            self.config.set_experimental_long_context({"enabled": target != 0, "target_tokens": target or 300000})
        elif name == "components":
            from core import runtime_components as runtime
            sources = runtime.normalize_download_sources(self.config.get("download_sources"))
            for kind in ("python", "node"):
                if v[kind + "_source"] not in {item["id"] for item in runtime.source_options(kind, sources)}:
                    raise ValueError("下载来源不存在。")
                sources[kind]["selected"] = v[kind + "_source"]
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
                if component not in {*runtime.TOOLKITS, "node", "speech"}:
                    raise ValueError("组件不存在。")
                if operation == "component.remove":
                    result = runtime.uninstall_speech_to_text_component() if component == "speech" else runtime.uninstall_toolkit(component)
                elif component == "node":
                    result = runtime.install_node_runtime(runtime.selected_source("node", sources), progress_callback=report)
                elif component == "speech":
                    result = runtime.install_speech_to_text_component(progress_callback=report, force=True)
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
