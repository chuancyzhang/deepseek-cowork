"""Creation sessions use the normal conversation ledger and submission queue."""
from pathlib import Path
import logging
import uuid

from core.mode_plugins import CREATION, MODE_PLUGINS, mode_inventory, require_mode

log = logging.getLogger("desktop.creation")


class DesktopCreation:
    methods = frozenset({"modes.list", "modes.enable", "creation.open", "creation.candidate"})

    def __init__(self, service):
        self.s = service

    def dispatch(self, method, p):
        s = self.s
        if method == "modes.list":
            return mode_inventory(s.config)
        if method == "modes.enable":
            plugin = next((item for item in MODE_PLUGINS if item.id == p.get("id")), None)
            if not plugin or type(p.get("enabled")) is not bool:
                raise ValueError("模式插件或启停参数无效。")
            with s.lock:
                s.config.set_mode_plugin_enabled(plugin.id, p["enabled"])
                s.publish({"event": "modes_changed"})
                log.info("mode_plugin_changed plugin=%s enabled=%s", plugin.id, p["enabled"])
                return mode_inventory(s.config)
        cid = str(p.get("conversation_id") or "")
        source = s.conversations.record(cid)
        if method == "creation.candidate":
            if not source["meta"].get("creation_session"):
                raise ValueError("请从创造模式打开设计任务。")
            root = s.workspace(cid).resolve()
            candidate = (root / "interface").resolve()
            if not candidate.is_relative_to(root) or candidate == root:
                raise ValueError("候选界面目录超出设计任务范围。")
            if not (candidate / "manifest.json").is_file():
                return {"path": None}
            return {"path": str(candidate)}
        require_mode(s.config, "creation")
        with s.lock:
            source = s.conversations.record(cid)
            if source["meta"].get("creation_session"):
                design_id = cid
            else:
                design_id = source["meta"].get("creation_session_id")
                existing = s.storage.get_conversation_record(design_id) if design_id else None
                if (not existing or existing["meta"].get("archived")
                        or not existing["meta"].get("creation_session")
                        or existing["meta"].get("creation_source_id") != cid):
                    design_id = uuid.uuid4().hex
                    root = (Path(s.config.get_chat_workspace_root()) / design_id).resolve()
                    root.mkdir(parents=True, exist_ok=True)
                    s.storage.upsert_conversation(design_id, title="设计我的工作台", meta={
                        "workspace_dir": str(root), "workspace_path": str(root), "workspace_source": "chat",
                        "desktop": True, "creation_session": True, "creation_source_id": cid,
                        "mode": "execution", "manual_title": True,
                        **{key: source["meta"][key] for key in ("selected_model_id", "reasoning_effort") if key in source["meta"]}})
                    s.storage.update_conversation_meta(cid, {"creation_session_id": design_id})
                    log.info("creation_session_created source=%s session=%s", cid, design_id)
            return {"conversation_id": design_id, "workspace": str(s.workspace(design_id)),
                    "plugin": CREATION.id}


def creation_request(text, scope):
    """A user-task suffix, never a system-prefix or tool-definition change."""
    allowed = {"整个界面", "首页布局", "侧栏导航", "顶部工具栏", "对话输入区", "工作区", "管理页面", "主题"}
    if scope not in allowed:
        raise ValueError("请选择有效的设计范围。")
    return text + "\n\n" + (
        f"[创造模式 · 修改范围：{scope}]\n"
        "这是独立的界面设计任务。原对话与运行状态由宿主保留。\n"
        "主题任务：通过 tool_search 查找 inspect_ui_theme / preview_ui_theme，先读取当前主题与 schema，再生成候选。"
        "本任务只生成预览，不调用保存或激活工具，用户在创造面板中应用。\n"
        "布局和插件任务：先阅读 reference/sdk/README.md 与 reference/sdk/client.d.ts，参考 reference/workbench。"
        "在工作目录 interface/ 中生成完整的 SDK 2 插件（manifest.json、index.html、client.js 及所需本地资源）。"
        "可以替换完整工作台、页面、面板和编辑器，必须复用 SDK 的会话、草稿、发送、停止与保存接口。"
        "保持其他区域及用户已完成成果。不要直接修改应用安装目录、数据库、插件索引或启用配置。"
        "生成后检查资源引用与 manifest，报告修改范围；用户通过候选预览应用，未确认前不安装。"
    )
