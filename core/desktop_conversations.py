"""Project and history actions on the existing SQLite conversation ledger."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
import logging
from pathlib import Path
import time
import uuid

from core.conversation_integrity import canonical_ledger_messages_hash

log = logging.getLogger("desktop.conversations")
PREFERENCE_KEYS = frozenset({"selected_model_id", "selected_skill_names", "conversation_agent", "knowledge_refs",
    "conversation_refs", "mode", "reasoning_effort", "keep_audio_local",
    "clarify_mode_state", "pending_clarify_questions", "grill_round_count", "grill_cycle_count", "grill_execution_confirmed",
    "workflow_mode", "office_output_profile", "office_conversion_target", "office_source_files", "office_template_file",
    "ppt_agent_mode", "ppt_agent_strategy", "ppt_agent_selected_strategy", "ppt_agent_preference", "ppt_agent_template_file", "ppt_agent_output_format"})


class ConversationActions:
    methods = frozenset({"project.list", "project.save", "project.archive", "project.restore", "conversation.search",
        "conversation.pin", "conversation.restore", "conversation.delete", "conversation.preferences", "conversation.export",
        "conversation.reference", "conversation.branch", "history.prepare", "history.edit", "history.delete", "history.undo",
        "agent.list", "agent.get", "agent.send", "agent.stop", "agent.resume", "conversation.workspace", "task.inspect", "task.context", "visualization.get", "visualization.state"})

    def __init__(self, service):
        self.service = service
        self.storage, self.store, self.config = service.storage, service.store, service.config

    def subagent_control(self, session_id, operation, **data):
        return self.service.request_agent({**data, "action": "subagent_control", "session_id": session_id,
                                           "operation": operation}).get("result")

    def child_control(self, cid, operation, **data):
        from core.subagent_control import SubagentControl
        return SubagentControl(self.storage, self.config, self).request(cid, operation, **data)

    def task_snapshot(self, cid, run_id=None):
        from core.conversation_render import build_conversation_render_items
        record = self.record(cid)
        run = self.store.get_run(run_id) if run_id else self.store.latest_run(cid)
        if run and run['conversation_id'] != cid:
            raise ValueError('任务不属于当前对话。')
        payload = json.loads(run['payload']) if run else {}
        journal = self.service.state.runtime_journal
        events = journal.read_events(cid, run['id']) if run and journal.get_run(cid, run['id']) else []
        messages = self.storage.get_messages(cid)
        if run_id or run:
            identity = run_id or run['id']
            messages = [message for message in messages if identity in {
                (message.get('meta') or {}).get('turn_id'), (message.get('meta') or {}).get('ui_turn_id')}]
        calls = {}
        for group in build_conversation_render_items(messages):
            for item in group.get('tool_calls', []):
                meta, result = item.get('meta') or {}, item.get('result_obj')
                has_result = any(message.get('role') == 'tool' and message.get('tool_call_id') == item['id']
                                 for message in group.get('messages', []))
                failure = result.get('error') if isinstance(result, dict) else None
                status = (result.get('status') if isinstance(result, dict) else None) or ('failed' if failure else 'completed' if has_result else 'unknown')
                calls[item['id']] = {'id': item['id'], 'name': item['name'], 'arguments': item['args'],
                    'result': result if result is not None else item['result'] if has_result else None,
                    'status': status, 'error': result.get('error') if isinstance(result, dict) else None,
                    'elapsed_ms': meta['duration'] * 1000 if isinstance(meta.get('duration'), (int, float)) else None,
                    'started_at': meta.get('start_time')}
        for event in events:
            value = event.get('payload') or event
            if value.get('type') not in {'tool_call', 'tool_result'}:
                continue
            data = value.get('data') or {}
            identity = data.get('id') or data.get('tool_call_id')
            if not identity:
                continue
            item = calls.setdefault(identity, {'id': identity, 'name': '', 'arguments': {}, 'result': None, 'status': 'unknown'})
            function = data.get('function') or {}
            item['name'] = function.get('name') or data.get('name') or item['name'] or '工具调用'
            if value['type'] == 'tool_call':
                arguments = function.get('arguments', data.get('arguments', data.get('args', {})))
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        pass
                item['arguments'] = arguments
                item['started_at'] = data.get('start_time') or event.get('created_at')
                if item['result'] is None:
                    item['status'] = 'running' if run and run['status'] in {'queued', 'running', 'stopping'} else 'unknown'
            else:
                result = data.get('result_obj') if data.get('result_obj') is not None else data.get('result', '')
                item['result'] = result
                item['error'] = data.get('error') or (result.get('error') if isinstance(result, dict) else None)
                item['status'] = (result.get('status') if isinstance(result, dict) else None) or ('failed' if item['error'] else 'completed')
                duration = data.get('duration')
                if isinstance(duration, (int, float)):
                    item['elapsed_ms'] = duration * 1000
        def context(preferences, submission=None):
            submission = submission or {}
            return {'workspace': submission.get('workspace_dir') or str(self.service.workspace(cid)),
                'model': submission.get('model_id') or preferences.get('selected_model_id') or self.config.get_selected_model_id(),
                'skills': submission.get('skills', preferences.get('selected_skill_names', [])),
                **{key: preferences.get(key) for key in ('mode', 'conversation_agent', 'knowledge_refs', 'conversation_refs',
                    'workflow_mode', 'office_output_profile', 'reasoning_effort')}}
        result = {'run': self.service.public_run(run), 'calls': list(calls.values()),
            'agents': self.child_control(cid, 'list'), 'current_context': context(record['meta']),
            'submitted_context': context(payload.get('preferences') or {}, payload) if run else None}
        log.debug('task_inspected session=%s run=%s calls=%s agents=%s', cid, run['id'] if run else '', len(calls), len(result['agents']))
        return result

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
        if method == "conversation.workspace":
            with self.service.lock:
                self.idle(cid)
                self.record(cid)
                root = Path(p["path"]).expanduser().resolve()
                if not root.is_dir():
                    raise ValueError("工作目录不存在，请选择现有目录。")
                self.storage.update_conversation_meta(cid, {"workspace_dir": str(root), "workspace_path": str(root), "workspace_source": "project"})
                self.service.publish({"event": "conversation_changed", "conversation_id": cid})
                log.info('workspace_selected session=%s', cid)
                return {"saved": True, "meta": self.storage.get_conversation_meta(cid)}
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
                saved = action(p["path"])
                log.info("project_visibility_changed action=%s path=%s saved=%s", method, p["path"], saved)
                return {"saved": saved}
        if method == "conversation.search":
            query = str(p.get("query") or "")
            identities = self.storage.search_conversations(query, limit=min(200, int(p.get("limit", 50))))
            project_paths = {Path(project["path"]).resolve() for project in self.config.get_projects() if query.casefold() in project["name"].casefold()}
            if project_paths:
                for record in self.storage.list_conversation_summaries():
                    path = record["meta"].get("workspace_dir")
                    if path and Path(path).resolve() in project_paths:
                        identities.append(record["id"])
            return self.store.summarize_conversations(self.storage.list_conversation_summaries(conversation_ids=identities))
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
                if method == "conversation.preferences":
                    log.info("preferences_saved session=%s keys=%s", cid, sorted(patch))
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
                    keep_audio_local=bool(old_meta.get("local_only_audio_files") or old_meta.get("transcription_only_audio_files")))
                from core.enterprise_client import submission
                if old_meta.get("enterprise_submission"):
                    previous = old_meta["enterprise_submission"]
                    replacement.setdefault("meta", {})["enterprise_submission"] = submission({"enterprise": previous["identity"]}, str(p.get("text") or ""), previous=previous)
                revised.append({**old, "id": uuid.uuid4().hex, "content": replacement["content"],
                    "content_parts": replacement["content_parts"], "meta": {**(old.get("meta") or {}), **(replacement["meta"] or {}),
                        "turn_id": uuid.uuid4().hex, "request_id": "", "edited_at": int(time.time())}})
            undo = uuid.uuid4().hex
            with self.service.state.runtime_journal.session_lock(cid):
                self.idle(cid)
                self.store.put_state("history_undo:" + undo, {"conversation_id": cid, "messages": messages, "meta": record["meta"], "title": record["title"], "status": record["status"],
                    "expected_hash": canonical_ledger_messages_hash(revised), "expected_revision": guard["revision"] + 1})
                self.storage.rewrite_conversation_safely(cid, revised, expected_messages_hash=guard["hash"], expected_revision=guard["revision"], title=record["title"], status=record["status"])
            log.info("history_rewritten session=%s removed=%s undo=%s", cid, len(messages) - len(revised), undo)
            self.service.publish({"event": "conversation_changed", "conversation_id": cid})
            return {"conversation": self.service.conversation(cid), "undo": undo}
        if method == "history.undo":
            with self.service.state.runtime_journal.session_lock(cid):
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
            return self.task_snapshot(cid, p.get('run_id'))
        if method == "task.context":
            from core.request_snapshot import RequestSnapshotStore
            self.record(cid)
            snapshot = RequestSnapshotStore(self.config.get_chat_history_dir()).read(cid)
            if p.get('run_id') and snapshot.get('run_id') != p['run_id']:
                log.info('request_context_unavailable session=%s requested_run=%s snapshot_run=%s reason=run_mismatch', cid, p['run_id'], snapshot.get('run_id'))
                raise ValueError('该轮的模型请求快照已被后续请求替换。请点击“返回当前任务”查看最新请求。')
            log.info('request_context_opened session=%s run=%s', cid, snapshot.get('run_id'))
            return snapshot
        if method == "agent.list":
            self.record(cid)
            return self.child_control(cid, 'list')
        if method in {"agent.get", "agent.send", "agent.stop", "agent.resume"}:
            operation = {'agent.get': 'history', 'agent.send': 'send', 'agent.stop': 'interrupt', 'agent.resume': 'resume'}[method]
            result = self.child_control(cid, operation, **{key: value for key, value in p.items() if key != 'conversation_id'})
            if method != 'agent.get':
                log.info('child_control_accepted session=%s agent=%s operation=%s', cid, p.get('agent_id'), operation)
            return result
        raise ValueError("不支持的会话操作。")


class ConversationRuntime:
    methods = frozenset(['conversation.create', 'conversation.list', 'conversation.get', 'conversation.rename', 'conversation.archive', 'conversation.receipt', 'conversation.send', 'conversation.stop', 'conversation.steer', 'draft.save', 'view.save', 'view.get', 'interaction.respond'])

    def __init__(self, *, config, storage, store, state, files, actions, publish, start, view, request):
        self.config, self.storage, self.store, self.state = config, storage, store, state
        self.workspace, self.checked_file = files.workspace, files.checked_file
        self.conversations, self.publish, self.start_stream = actions, publish, start
        self.conversation, self.request_agent = view, request
        self.public_run = lambda run: {key: run[key] for key in ("id", "conversation_id", "message_id", "status", "error")} if run else None
        self.ledger = state.runtime_journal.ledger

    @property
    def closing(self):
        return self.state.update_preparing or self.state.shutdown_requested

    def dispatch(self, method, p):
        cid = str(p.get("conversation_id") or "")
        if method == "conversation.create":
            cid = uuid.uuid4().hex
            root = Path(p.get("workspace_dir") or (Path(self.config.get_chat_workspace_root()) / cid)).resolve()
            root.mkdir(parents=True, exist_ok=True)
            self.storage.upsert_conversation(cid, title="新对话", meta={"workspace_dir": str(root), "workspace_path": str(root),
                "workspace_source": "project" if p.get("workspace_dir") else "chat", "desktop": True})
            self.store.put_state("last_conversation", cid)
            return self.conversation(cid)
        if method == "conversation.list":
            return self.store.summarize_conversations(self.storage.list_conversation_summaries(
                limit=min(500, max(1, int(p.get("limit", 500)))), offset=max(0, int(p.get("offset", 0)))))
        if method == "conversation.get":
            self.store.put_state("last_conversation", cid)
            return self.conversation(cid, before=p.get("before"), limit=p.get("limit", 80))
        if method == "conversation.rename":
            if not self.storage.has_conversation(cid):
                raise ValueError("对话不存在。")
            title = str(p.get("title") or "").strip()[:200]
            if not title:
                raise ValueError("请输入对话标题。")
            self.storage.upsert_conversation(cid, title=title)
            self.storage.update_conversation_meta(cid, {"manual_title": True})
            return {"saved": True}
        if method == "conversation.archive":
            if any(r["conversation_id"] == cid for r in self.store.active_runs()):
                raise ValueError("请先停止当前任务，再归档对话。")
            self.storage.update_conversation_meta(cid, {"archived": True})
            return {"saved": True}
        if method in {"draft.save", "view.save"}:
            if cid:
                self.workspace(cid)
            return self.store.put_state(("draft:" if method == "draft.save" else "view:") + cid, p["value"], p.get("revision"))
        if method == "view.get":
            self.workspace(cid)
            return self.store.get_state("view:" + cid, {})
        if method == "conversation.receipt":
            run = self.store.get_run(str(p["request_id"]))
            if run and run["conversation_id"] != cid:
                raise ValueError("发送记录不属于当前对话。")
            rejected = self.store.get_state("submission_rejected:" + str(p["request_id"]))
            return {"accepted": bool(run), "rejected": bool(rejected and rejected["conversation_id"] == cid), "run": self.public_run(run), "draft": self.store.versioned_state("draft:" + cid, {"text": "", "attachments": []})}
        if method == "conversation.send":
            if self.closing:
                raise ValueError("应用正在退出，输入内容已保留。")
            input_hash = hashlib.sha256(json.dumps(p, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            existing = self.store.get_run(str(p["request_id"]))
            if existing:
                accepted = json.loads(existing["payload"])
                if existing["conversation_id"] != cid or accepted.get("input_hash") != input_hash:
                    raise ValueError("请求标识已用于另一条消息，请重新发送。")
                return {"accepted": True, "run": self.public_run(existing), "draft": self.store.versioned_state("draft:" + cid)}
            profile = self.config.get_model_profile(p.get("model_id"))
            if not profile or not profile.get("api_key"):
                raise ValueError("请先在“模型与服务”中填写 API Key，或导入现有设置。输入内容已保留。")
            text = str(p.get("text") or "").strip()
            attachments = p.get("attachments") or []
            if not text and not attachments:
                raise ValueError("请输入消息或添加附件。")
            from core.desktop_submission import user_payload, run_context
            record = self.storage.get_conversation_record(cid)
            if not record:
                raise ValueError("对话不存在。")
            from core.desktop_conversations import PREFERENCE_KEYS
            preferences = {key: value for key, value in {**record["meta"], **(p.get("preferences") or {})}.items() if key in PREFERENCE_KEYS}
            workspace = self.workspace(cid)
            if not workspace.is_dir():
                if record["meta"].get("workspace_source") == "chat" or not (record["meta"].get("workspace_dir") or record["meta"].get("workspace_path")):
                    workspace.mkdir(parents=True, exist_ok=True)
                else:
                    raise ValueError("原项目目录暂时不可用，请恢复该目录后继续。输入和历史记录已保留。")
            protected_audio = bool(preferences.get("keep_audio_local"))
            prior_attachments = set()
            if p.get("rewrite"):
                source = next((message for message in self.storage.get_messages(cid) if message["id"] == p["rewrite"].get("message_id")), {})
                protected_audio = protected_audio or bool((source.get("meta") or {}).get("local_only_audio_files") or (source.get("meta") or {}).get("transcription_only_audio_files"))
                prior_attachments = {str(Path(path).resolve()) for path in (source.get("meta") or {}).get("user_added_files", [])}
            paths = []
            for item in attachments:
                path = str(Path(item["path"]).resolve())
                paths.append(path if path in prior_attachments else str(self.checked_file(cid, path)))
            message = user_payload(text, paths, workspace=self.workspace(cid), supports_vision=profile.get("supports_vision", False),
                keep_audio_local=protected_audio,
                references=preferences.get("conversation_refs", []), branch_file_map=record["meta"].get("branch_file_map"))
            from core.enterprise_client import submission
            question = submission(profile, text)
            message_meta = {**(message.get("meta") or {})}
            if "conversation_agent" in preferences:
                message_meta["conversation_agent"] = preferences["conversation_agent"]
            if question:
                message_meta["enterprise_submission"] = question
            payload = {"conversation_id": cid, "request_id": str(p["request_id"]),
                       "message_id": str(p["message_id"]), "content": message["content"], "display_text": text,
                       "workspace_dir": str(self.workspace(cid)), "model_id": profile["id"],
                       "skills": p.get("skills", preferences.get("selected_skill_names", [])), "attachments": attachments,
                       "preferences": preferences, "input_hash": input_hash, "draft_revision": p.get("draft_revision"),
                       "content_parts": message.get("content_parts"), "message_meta": message_meta, "rewrite": p.get("rewrite")}
            if payload["rewrite"]:
                self.conversations.idle(cid)
            payload["runtime_context"] = run_context(self, cid, payload, profile)
            # Display-only snapshot; provider projection ignores message metadata.
            message_meta["ui_context"] = {
                "skills": list(payload["runtime_context"].get("selected_skill_names") or []),
                "knowledge": [{"title": ref.get("title") or ref.get("name") or "资料"}
                              for ref in preferences.get("knowledge_refs") or []],
            }
            draft = self.store.versioned_state("draft:" + cid, {"text": "", "attachments": []})
            run, created = self.state.accept(payload)
            if created and payload.get("draft_revision") is not None and draft["revision"] == payload["draft_revision"]:
                draft = {"value": {"text": "", "attachments": []}, "revision": draft["revision"] + 1}
            log.info("message_accepted session=%s run=%s created=%s", cid, run["id"], created)
            if created:
                self.start_stream(payload, copy.deepcopy(profile))
            return {"accepted": True, "run": self.public_run(run), "draft": draft, "durable_cursor": self.ledger.durable,
                    "undo": run["id"] if payload.get("rewrite") else None}
        if method == "conversation.stop":
            run_id = str(p["run_id"])
            if not self.state.owns_run(cid, run_id):
                run = self.store.get_run(run_id)
                if not run or run["conversation_id"] != cid:
                    raise ValueError("任务不属于当前对话。")
                if run["status"] not in {"queued", "running", "stopping"}:
                    return {"stopped": True, "released": True}
            result = self.request_agent({"action": "stop_session", "session_id": cid, "run_id": run_id}, timeout=20)
            if result.get("save_error"):
                raise RuntimeError("已请求停止，但保存停止状态失败：" + result["save_error"])
            if not result.get("released"):
                raise RuntimeError("任务仍在结束处理中，已完成内容会保留，请稍后核对状态。")
            log.info("run_stop session=%s run=%s released=%s", cid, run_id, result.get("released"))
            return result
        if method == "conversation.steer":
            message = copy.deepcopy(p["message"])
            run = self.store.get_run(str(p["run_id"]))
            if not run or run["conversation_id"] != cid:
                raise ValueError("任务不属于当前对话。补充内容已保留。")
            accepted = json.loads(run["payload"])
            profile = self.config.get_model_profile(accepted["model_id"])
            if not profile:
                raise ValueError("本轮模型配置已不可用，请停止后重新选择模型。补充内容已保留。")
            preferences = accepted.get("preferences") or {}
            from core.desktop_submission import user_payload
            from core.enterprise_client import submission
            text = str(message.get("content") or "")
            attachments = p.get("attachments") or []
            built = user_payload(text, [str(self.checked_file(cid, item["path"])) for item in attachments],
                workspace=self.workspace(cid), supports_vision=profile.get("supports_vision", False),
                keep_audio_local=bool(preferences.get("keep_audio_local")))
            message.update(content=built["content"], content_parts=built["content_parts"])
            message["meta"] = {**(message.get("meta") or {}), **(built.get("meta") or {}), "desktop_attachments": attachments}
            question = submission(profile, text)
            if question:
                message["meta"]["enterprise_submission"] = question
            return self.request_agent({"action": "steer_message", "session_id": cid, "expected_turn_id": p["run_id"], "message": message})
        if method == "interaction.respond":
            # The shared interaction service also checks the request identity.
            from core.interaction import interaction_service, parse_interaction_reply
            request = interaction_service.get_pending_request(cid)
            if not request or request["request_id"] != p["request_id"]:
                raise ValueError("这项操作已经结束或不属于当前对话。")
            result, valid, reason = parse_interaction_reply(request, p["value"])
            if not valid:
                raise ValueError(reason or "请完成填写。")
            return self.request_agent({"action": "respond_interaction", "request_id": p["request_id"], "result": result})
        raise ValueError("不支持的操作：" + method)
