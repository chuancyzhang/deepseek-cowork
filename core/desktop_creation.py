"""Creation sessions use the normal conversation ledger and submission queue."""
import base64
import logging
from pathlib import Path
import threading
import time
import uuid

from core.mode_plugins import CREATION, MODE_PLUGINS, mode_inventory, require_mode

log = logging.getLogger("desktop.creation")


class DesktopCreation:
    methods = frozenset({"modes.list", "modes.enable", "creation.open", "creation.candidate", "creation.preview_reply"})

    def __init__(self, service):
        self.s = service
        self.pending = {}
        self.lock = threading.Lock()

    def candidate_path(self, cid):
        record = self.s.conversations.record(cid)
        if not record["meta"].get("creation_session") or record["meta"].get("archived"):
            raise ValueError("请从创造模式打开设计任务。")
        root = self.s.workspace(cid).resolve()
        candidate = root / "interface"
        if candidate.resolve() != candidate:
            raise ValueError("候选界面必须位于设计任务的 interface 目录。")
        return candidate if (candidate / "manifest.json").is_file() else None

    def agent_preview(self, identity, claim):
        cid, run_id = identity["session_id"], identity["run_id"]
        candidate = self.candidate_path(cid)
        if candidate is None:
            raise ValueError("候选缺少 interface/manifest.json，请完成清单后再提交。")
        token = {**identity, "token": claim["token"], "action": "workbench_active"}
        request_id, received = identity["request_id"], threading.Event()
        request = {"conversation_id": cid, "run_id": run_id, "received": received, "result": None}
        with self.lock:
            self.pending[request_id] = request
        log.info("creation_preview_requested session=%s run=%s request=%s", cid, run_id, request_id)
        try:
            self.s.publish({"event": "creation_preview_request", "conversation_id": cid, "run_id": run_id,
                            "data": {"id": request_id, "path": str(candidate)}})
            deadline = time.monotonic() + min(25, claim["remaining"])
            while not received.wait(.25):
                if time.monotonic() >= deadline:
                    raise ValueError("候选试跑未能收到桌面回执，请打开桌面后重新试跑。")
                if self.s.request_agent(token).get("status") != "ok":
                    raise ValueError("设计任务已停止，本次试跑已结束。")
            result = request["result"]
            screenshot = result.pop("screenshot", None)
            if screenshot and claim.get("model_supports_vision") and claim.get("model_api_protocol") in {"responses", "chat_completions"}:
                from core.tool_images import persist_tool_image_bytes
                try:
                    result["content_parts"] = [persist_tool_image_bytes(
                        base64.b64decode(screenshot, validate=True), self.s.config.get_chat_history_dir(),
                        cid, name="工作台候选.png")]
                except (OSError, ValueError) as exc:
                    log.warning("creation_preview_image_failed session=%s run=%s error=%s", cid, run_id, exc)
                    result["image_note"] = "预览截图保存失败，可根据文字检查结果继续。"
            elif screenshot:
                result["image_note"] = "当前模型只接收文字诊断。"
            log.info("creation_preview_completed session=%s run=%s ok=%s hash=%s", cid, run_id,
                     result.get("ok"), result.get("hash"))
            return result
        finally:
            with self.lock:
                self.pending.pop(request_id, None)
            if not received.is_set():
                self.s.publish({"event": "creation_preview_cancelled", "data": {"id": request_id}})

    def dispatch(self, method, p):
        s = self.s
        if method == "creation.preview_reply":
            with self.lock:
                request = self.pending.get(p.get("id"))
                if (request is None or request["received"].is_set()
                        or request["conversation_id"] != p.get("conversation_id") or request["run_id"] != p.get("run_id")):
                    return {"accepted": False}
                request["result"] = p["result"]
                request["received"].set()
                return {"accepted": True}
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
        if method == "creation.candidate":
            candidate = self.candidate_path(cid)
            return {"path": str(candidate) if candidate else None}
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
        "布局和插件任务先用 text_file_read 阅读 reference/sdk/live-preview.md、reference/sdk/README.md 与 reference/sdk/client.d.ts，参考 reference/workbench。"
        "在工作目录 interface/ 中生成 SDK 2 插件。内置工具栏按钮只需清单，其他界面提供 HTML 入口和所需本地资源。"
        "可以替换完整工作台、页面、面板和编辑器，必须复用 SDK 的会话、草稿、发送、停止与保存接口。"
        "保持其他区域及用户已完成成果。不要直接修改应用安装目录、数据库、插件索引或启用配置。"
        "顶部工具栏的小改动优先使用 SDK 的 toolbar 贡献点，复用现有用量面板和生成速度。"
        "用 apply_patch 提交每份可试跑的候选，初版最后提交 manifest.json；已存在清单时，"
        "修改 interface/ 中的文件也会提交该版本。若用其他工具写文件，最后用 apply_patch 更新清单。"
        "查看工具结果中的画面、操作检查和报错，在本轮修正后再交付。用户可在创造模式应用已试跑的版本。"
    )


def preview_creation_patch(workspace, result, context):
    """A complete design patch submits a preview through the existing tool result."""
    if not (result.get("ok") and (context or {}).get("run_context", {}).get("creation_session")
            and not context.get("is_subagent")):
        return result
    touched = [str(item.get("path", "")).replace("\\", "/") for item in result.get("changes", [])]
    if not any(path.startswith("interface/") for path in touched):
        return result
    if not (Path(workspace) / "interface" / "manifest.json").is_file() and "interface/manifest.json" not in touched:
        return result
    from core.workbench import workbench_bridge
    preview = workbench_bridge().call("creation_preview", {}, context, timeout=30)
    parts = preview.pop("content_parts", None)
    result["creation_preview"] = preview
    if parts:
        result["content_parts"] = parts
    return result
