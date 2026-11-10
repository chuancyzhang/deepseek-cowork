"""Project and history actions on the existing SQLite conversation ledger."""
from __future__ import annotations

import copy
import json
from datetime import datetime
import logging
from pathlib import Path
import time
import uuid

from core.conversation_integrity import canonical_ledger_messages_hash

log = logging.getLogger("desktop.conversations")
PREFERENCE_KEYS = frozenset({"selected_model_id", "selected_skill_names", "conversation_agent", "knowledge_refs",
    "conversation_refs", "mode", "reasoning_effort", "keep_audio_local", "audio_transcription_backend",
    "clarify_mode_state", "pending_clarify_questions", "grill_round_count", "grill_cycle_count", "grill_execution_confirmed",
    "workflow_mode", "office_output_profile", "office_conversion_target", "office_source_files", "office_template_file",
    "ppt_agent_mode", "ppt_agent_strategy", "ppt_agent_selected_strategy", "ppt_agent_preference", "ppt_agent_template_file", "ppt_agent_output_format"})


class ConversationActions:
    methods = frozenset({"project.list", "project.save", "project.archive", "project.restore", "conversation.search",
        "conversation.pin", "conversation.restore", "conversation.delete", "conversation.preferences", "conversation.export",
        "conversation.reference", "conversation.branch", "history.prepare", "history.edit", "history.delete", "history.undo",
        "agent.list", "agent.get", "agent.stop", "agent.resume", "task.inspect", "visualization.get", "visualization.state"})

    def __init__(self, service):
        self.service = service
        self.storage, self.store, self.config = service.storage, service.store, service.config

    def idle(self, cid):
        if any(run["conversation_id"] == cid for run in self.store.active_runs()):
            raise ValueError("请先停止当前任务并等待保存完成，再修改历史。")
        if any(agent.get("status") in {"running", "queued", "stopping"} for agent in self.storage.list_agents(cid)):
            raise ValueError("子任务仍在运行，请先停止子任务并等待保存完成。")

    def record(self, cid):
        record = self.storage.get_conversation_record(cid)
        if not record:
            raise ValueError("对话不存在。")
        return record

    def snapshot(self, cid):
        record = self.record(cid)
        return {"session_id": cid, "title": record["title"], "captured_at": datetime.now().astimezone(),
            "messages": self.storage.get_messages(cid), "timeline": record["meta"].get("ui_timeline_v1", []),
            "running": any(run["conversation_id"] == cid for run in self.store.active_runs())}

    def guard(self, cid):
        self.idle(cid)
        record = self.record(cid)
        messages = self.storage.get_messages(cid)
        return record, messages, {"hash": canonical_ledger_messages_hash(messages),
            "revision": int(record["meta"].get("history_save_revision") or 0)}

    def dispatch(self, method, p):
        cid = str(p.get("conversation_id") or "")
        if method in {"visualization.get", "visualization.state"}:
            from core.inline_visualization import build_visualization_document, is_visualize_enabled_context, sha256_file, validate_visualization_fragment
            if not cid:
                raise ValueError("可视化缺少会话上下文，无法读取已保存内容。")
            self.record(cid)
            artifact = self.storage.get_inline_visualization(cid, str(p.get("file") or ""))
            if not artifact:
                raise ValueError("这份可视化不属于当前对话，或尚未发布完成。")
            if sha256_file(artifact["path"]) != artifact["sha256"]:
                raise ValueError("可视化文件已变化，请重新生成或发布，原交互状态仍保留。")
            read_only = not is_visualize_enabled_context({"skill_manager": self.service.state.skill_catalog.snapshot().manager})
            if method == "visualization.state":
                if read_only:
                    raise ValueError("Visualize 已停用，历史可视化保持只读，原交互状态仍保留。")
                if p.get("sha256") != artifact["sha256"] or not isinstance(p.get("state"), dict):
                    raise ValueError("可视化版本或交互状态无效，尚未覆盖已保存内容。")
                self.storage.save_inline_visualization_state(cid, artifact["file"], artifact["sha256"], p["state"])
                log.info("visualization_state_saved conversation=%s file=%s", cid, artifact["file"])
                return {"saved": True}
            content = validate_visualization_fragment(artifact["path"], artifact["origins"])
            state = self.storage.get_inline_visualization_state(cid, artifact["file"], artifact["sha256"])
            log.info("visualization_loaded conversation=%s file=%s read_only=%s", cid, artifact["file"], read_only)
            return {"title": artifact["title"], "sha256": artifact["sha256"], "read_only": read_only,
                    **build_visualization_document(content["fragment"], state, read_only=read_only, origins=content["origins"], frame_only=True)}
        if method == "project.list":
            return self.config.get_projects()
        if method == "project.save":
            root = Path(p["path"]).expanduser().resolve()
            if not root.is_dir():
                raise ValueError("项目目录不存在，请选择现有目录。")
            with self.service.lock:
                return self.config.upsert_project(str(root), name=p.get("name"), pinned=p.get("pinned"))
        if method in {"project.archive", "project.restore"}:
            with self.service.lock:
                action = self.config.archive_project if method.endswith("archive") else self.config.restore_project
                return {"saved": action(p["path"])}
        if method == "conversation.search":
            query = str(p.get("query") or "")
            identities = self.storage.search_conversations(query, limit=min(200, int(p.get("limit", 50))))
            project_paths = {Path(project["path"]).resolve() for project in self.config.get_projects() if query.casefold() in project["name"].casefold()}
            if project_paths:
                for record in self.storage.list_conversation_summaries():
                    path = record["meta"].get("workspace_dir")
                    if path and Path(path).resolve() in project_paths:
                        identities.append(record["id"])
            return self.storage.list_conversation_summaries(conversation_ids=identities)
        if method in {"conversation.pin", "conversation.restore", "conversation.delete", "conversation.preferences"}:
            with self.service.lock:
                self.record(cid)
                if method == "conversation.pin":
                    patch = {"pinned": bool(p.get("pinned"))}
                elif method == "conversation.restore":
                    patch = {"archived": False, "deleted_at": None}
                elif method == "conversation.delete":
                    self.idle(cid)
                    patch = {"archived": True, "deleted_at": int(time.time())}
                else:
                    patch = {key: copy.deepcopy(value) for key, value in p.get("value", {}).items() if key in PREFERENCE_KEYS}
                self.storage.update_conversation_meta(cid, patch)
                self.service.publish({"event": "conversation_changed", "conversation_id": cid})
                return {"saved": True, "meta": self.storage.get_conversation_meta(cid)}
        if method == "conversation.export":
            from core.chat_log_export import iter_conversation_export, chat_log_filename
            snapshot = self.snapshot(cid)
            return {"name": chat_log_filename(snapshot["title"], snapshot["captured_at"]), "text": "".join(iter_conversation_export(snapshot))}
        if method == "conversation.reference":
            from core.conversation_actions import write_reference_snapshot
            if cid == p["source_id"]:
                raise ValueError("请选择另一段对话作为参考。")
            self.record(cid)
            return write_reference_snapshot(self.snapshot(p["source_id"]), self.service.workspace(cid))
        if method == "conversation.branch":
            from core.conversation_actions import clone_branch, retain_branch_text_history
            with self.service.lock:
                source = self.record(cid)
                target = uuid.uuid4().hex
                messages, meta = clone_branch(self.storage.get_messages(cid), source["meta"], target, cid, source["title"], p["message_id"])
                # A personal fork gets an independent workspace. Files remain
                # provenance until explicitly copied by the user.
                if source["meta"].get("team_space_required"):
                    raise ValueError("团队空间对话需在原空间内创建分支，不能复制到个人会话。")
                retain_branch_text_history(messages)
                root = Path(self.config.get_chat_workspace_root()) / target
                root.mkdir(parents=True, exist_ok=True)
                meta.update(workspace_dir=str(root), workspace_path=str(root), workspace_source="chat")
                self.storage.create_branch_conversation(target, messages, title=str(p.get("title") or (source["title"] or "新对话") + " · 分支"), meta=meta)
                return self.service.conversation(target)
        if method == "history.prepare":
            with self.service.lock:
                _record, messages, guard = self.guard(cid)
                index = next((index for index, message in enumerate(messages) if message["id"] == p["message_id"]), -1)
                if index < 0:
                    raise ValueError("找不到这条消息，请重新打开对话。")
                from core.conversation_actions import regeneration_source
                source = regeneration_source(messages, p["message_id"]) if messages[index]["role"] == "assistant" else messages[index]
                return {**guard, "message": messages[index], "downstream": len(messages) - index - 1,
                        "source_user": source, "rewrite_message_id": source["id"],
                        "downstream_users": sum(message["role"] == "user" for message in messages[index + 1:])}
        if method in {"history.edit", "history.delete"}:
            with self.service.lock:
                record, messages, guard = self.guard(cid)
                if guard != p.get("guard"):
                    raise ValueError("对话已发生变化，请重新核对修改范围。当前编辑内容仍保留。")
                index = next((index for index, message in enumerate(messages) if message["id"] == p["message_id"]), -1)
                if index < 0 or messages[index]["role"] != "user" or (messages[index].get("meta") or {}).get("hidden") or (messages[index].get("meta") or {}).get("same_turn_guidance"):
                    raise ValueError("请选择一条用户消息。")
                revised = copy.deepcopy(messages[:index])
                if method == "history.edit":
                    from core.desktop_submission import user_payload
                    old = messages[index]
                    files = (old.get("meta") or {}).get("user_added_files", [])
                    if not str(p.get("text") or "").strip() and not files:
                        raise ValueError("消息内容不能为空。")
                    old_meta = old.get("meta") or {}
                    replacement = user_payload(p.get("text"), files, workspace=self.service.workspace(cid), supports_vision=True,
                        references=old_meta.get("conversation_refs", []), branch_file_map=record["meta"].get("branch_file_map"),
                        keep_audio_local=bool(old_meta.get("local_only_audio_files") or old_meta.get("transcription_only_audio_files")),
                        audio_transcription_backend=old_meta.get("audio_transcription_backend", "local"))
                    from core.enterprise_client import submission
                    if old_meta.get("enterprise_submission"):
                        previous = old_meta["enterprise_submission"]
                        replacement.setdefault("meta", {})["enterprise_submission"] = submission({"enterprise": previous["identity"]}, str(p.get("text") or ""), previous=previous)
                    revised.append({**old, "id": uuid.uuid4().hex, "content": replacement["content"],
                        "content_parts": replacement["content_parts"], "meta": {**(old.get("meta") or {}), **(replacement["meta"] or {}),
                            "turn_id": uuid.uuid4().hex, "request_id": "", "edited_at": int(time.time())}})
                undo = uuid.uuid4().hex
                self.store.put_state("history_undo:" + undo, {"conversation_id": cid, "messages": messages, "meta": record["meta"], "title": record["title"], "status": record["status"],
                    "expected_hash": canonical_ledger_messages_hash(revised), "expected_revision": guard["revision"] + 1})
                self.storage.rewrite_conversation_safely(cid, revised, expected_messages_hash=guard["hash"], expected_revision=guard["revision"], title=record["title"], status=record["status"])
                log.info("history_rewritten session=%s removed=%s undo=%s", cid, len(messages) - len(revised), undo)
                self.service.publish({"event": "conversation_changed", "conversation_id": cid})
                return {"conversation": self.service.conversation(cid), "undo": undo}
        if method == "history.undo":
            with self.service.lock:
                self.idle(cid)
                snapshot = self.store.get_state("history_undo:" + str(p["undo"]))
                if not snapshot or snapshot["conversation_id"] != cid:
                    raise ValueError("撤销记录已失效。")
                self.storage.rewrite_conversation_safely(cid, snapshot["messages"], expected_messages_hash=snapshot["expected_hash"],
                    expected_revision=snapshot["expected_revision"], meta=snapshot["meta"], title=snapshot["title"], status=snapshot["status"])
                self.store.put_state("history_undo:" + str(p["undo"]), None)
                self.service.publish({"event": "conversation_changed", "conversation_id": cid})
                return self.service.conversation(cid)
        if method == "task.inspect":
            record = self.record(cid)
            run = self.store.get_run(p['run_id']) if p.get('run_id') else self.store.latest_run(cid)
            if run and run['conversation_id'] != cid:
                raise ValueError('任务不属于当前对话。')
            payload = json.loads(run['payload']) if run else {}
            preferences = payload.get('preferences') or record['meta']
            journal = self.service.state.runtime_journal
            events = journal.read_events(cid, run['id']) if run and journal.get_run(cid, run['id']) else []
            calls = [event for event in events if (event.get('payload') or event).get('type') in {'tool_call', 'tool_result'}]
            return {'run': {key: run[key] for key in ('id', 'status', 'error', 'created_at', 'updated_at')} if run else None,
                    'events': calls[-400:], 'omitted_events': max(0, len(calls) - 400),
                    'agents': self.storage.list_agents(cid),
                    'context': {'model': payload.get('model_id') or preferences.get('selected_model_id') or self.config.get_selected_model_id(),
                                'workspace': str(self.service.workspace(cid)), 'skills': payload.get('skills') or preferences.get('selected_skill_names', []),
                                **{key: preferences.get(key) for key in ('mode', 'conversation_agent', 'knowledge_refs', 'conversation_refs', 'workflow_mode', 'office_output_profile', 'reasoning_effort')}}}
        if method == "agent.list":
            self.record(cid)
            return self.storage.list_agents(cid)
        if method in {"agent.get", "agent.stop", "agent.resume"}:
            agent = self.storage.get_agent(p["agent_id"])
            if not agent or agent.get("conversation_id") != cid:
                raise ValueError("子任务不属于当前对话。")
            if method == "agent.get":
                return {**agent, "messages": self.storage.get_agent_messages(p["agent_id"])}
            from core.agent_manager import get_agent_manager_registry
            manager = get_agent_manager_registry().get_session_manager(cid, self.storage, self.config, str(self.service.workspace(cid)))
            return manager.interrupt_agent(p["agent_id"], p["run_id"]) if method == "agent.stop" else manager.resume_agent(p["agent_id"])
        raise ValueError("不支持的会话操作。")
