from . import resolve


class BootstrapSession:
    """One request at most; all state belongs to this worker, never to a global plugin."""

    def __init__(self):
        self.plugin = None
        self.active = False
        self.prompt = ""
        self.definitions = []
        self.tool_names = set()
        self.reason = "not_applicable"
        self.error_type = ""
        self.error_message = ""
        self.handoff_requested = False

    @classmethod
    def create(cls, config_manager, run_context, messages, workspace_dir, is_subagent=False):
        session = cls()
        try:
            profile = run_context.get("selected_model_profile")
            if not isinstance(profile, dict) or not profile:
                getter = getattr(config_manager, "get_model_profile", None)
                profile = getter(run_context.get("selected_model_id")) if callable(getter) else None
            if not isinstance(profile, dict) or not profile:
                profile = {
                    "provider_type": config_manager.get("llm_provider", "openai"),
                    "base_url": config_manager.get("base_url"),
                    "model_name": config_manager.get("model_name"),
                }
            session.plugin = resolve(profile, profile.get("model_name"))
            if session.plugin is None:
                return session
            # Normal desktop text messages also have text content_parts.
            # Selected skills and a connected (unscoped) knowledge library are
            # restored after bootstrap; they are not evidence of a dedicated workflow.
            knowledge = run_context.get("knowledge_context") or {}
            if not messages or any(message.get("role") != "user" for message in messages):
                session.reason = "existing_conversation"
            elif any(
                not isinstance(message.get("content"), str)
                or any(part.get("type") != "text" for part in message.get("content_parts") or [])
                for message in messages
            ):
                session.reason = "attachments"
            elif (
                is_subagent
                or run_context.get("mode") != "execution"
                or any(run_context.get(key) for key in (
                    "allowed_skill_names", "agent_profile_id", "agent_system_prompt",
                    "workflow_mode", "ppt_agent_mode", "im_provider",
                    "office_source_files", "office_template_file",
                ))
                or run_context.get("office_output_profile") not in (None, "", "free")
                or knowledge.get("refs")
                or profile.get("supports_image_generation")
            ):
                session.reason = "dedicated_workflow"
            elif not workspace_dir:
                session.reason = "workspace_unavailable"
            else:
                session.reason = "ready"
            if session.reason != "ready":
                return session
            session.prompt = session.plugin.system_prompt()
            session.definitions = session.plugin.tool_definitions()
            session.tool_names = set(session.plugin.tools())
            if not session.prompt or {
                definition["function"]["name"] for definition in session.definitions
            } != session.tool_names:
                raise ValueError("Bootstrap prompt or tool definitions are invalid.")
            session.plugin.prepare()
            session.active = True
            session.reason = "started"
        except Exception as exc:
            session.reason = "initialization_failed"
            session.error_type = type(exc).__name__
            session.error_message = str(exc)
        return session

    @property
    def plugin_id(self):
        return self.plugin.plugin_id if self.plugin else ""

    @property
    def status_text(self):
        return {
            "started": "正在进行极简启动。",
            "existing_conversation": "当前对话已有上下文，沿用 Cowork 完整能力；极简启动仅用于新对话。",
            "attachments": "当前任务包含附件，直接使用 Cowork 完整能力。",
            "dedicated_workflow": "当前任务已有专用工作流程，直接使用 Cowork 完整能力。",
            "workspace_unavailable": "当前没有任务工作区，使用 Cowork 默认流程。",
            "initialization_failed": "极简启动不可用，正在使用 Cowork 完整能力。",
        }.get(self.reason, "")

    def finish(self, reason):
        if not self.active:
            return False
        self.active = False
        self.reason = reason
        return True

    def call_tool(self, name, args, context):
        if not self.active:
            raise RuntimeError("Bootstrap has already finished.")
        if self.handoff_requested:
            return {
                "status": "denied",
                "error": "This tool batch needs normal Cowork capabilities; no bootstrap command was executed.",
            }
        return self.plugin.call_tool(name, args, context)
