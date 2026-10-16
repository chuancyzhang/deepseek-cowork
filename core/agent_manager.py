import json
import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, Signal
from core.chat_storage import AGENT_TERMINAL_STATUSES
from core.env_utils import get_app_data_dir
from core.process_utils import runtime_debug_logging_enabled

AGENT_MANAGEMENT_TOOLS = {
    "spawn_agent",
    "send_input",
    "wait_agent",
    "close_agent",
    "list_agents",
}

AGENT_LIVE_STATUSES = {"queued", "running", "waiting_input", "stopping"}


def _short_debug_value(value, limit=240):
    text = str(value or "")
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit] + "..." if len(text) > limit else text


def _debug_worker_summary(worker):
    if worker is None:
        return {"worker": None}
    summary = {
        "worker_class": worker.__class__.__name__,
        "worker_id": hex(id(worker)),
        "has_qthread_finished": bool(getattr(worker, "finished", None) is not None),
        "has_finished_signal": bool(getattr(worker, "finished_signal", None) is not None),
    }
    try:
        summary["is_running"] = bool(worker.isRunning())
    except Exception as exc:
        summary["is_running_error"] = _short_debug_value(exc)
    return summary


def _log_agent_runtime(event, **fields):
    fields = {k: v for k, v in fields.items() if not any(x in k for x in ("preview", "content", "traceback"))}
    try:
        payload = {
            "event": event,
            "thread_id": threading.get_ident(),
        }
        payload.update({k: v for k, v in fields.items() if not any(x in k for x in ("preview", "content", "traceback"))})
        log_dir = get_app_data_dir()
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "sub_agent_runtime.log")
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"[{ts}] {json.dumps(payload, ensure_ascii=False, default=str)}\n")
    except Exception:
        try:
            print(f"[sub-agent-runtime] {event} {fields}")
        except Exception:
            return


class _ManagerEventBridge(QObject):
    step_message = Signal(str)
    agent_event = Signal(dict)

    def __init__(self, owner_worker=None):
        super().__init__()
        self.owner_worker = owner_worker
        if owner_worker is not None:
            try:
                self.step_message.connect(owner_worker.relay_agent_step, Qt.DirectConnection)
            except Exception as exc:
                _log_agent_runtime("event_bridge_step_connect_failed", error=_short_debug_value(exc))
            try:
                self.agent_event.connect(owner_worker.relay_agent_state, Qt.DirectConnection)
            except Exception as exc:
                _log_agent_runtime("event_bridge_agent_connect_failed", error=_short_debug_value(exc))


def _json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        copied = json.loads(json.dumps(value, ensure_ascii=False))
        return fallback if copied is None else copied
    except Exception:
        return fallback


def _event_timestamp():
    return int(time.time())


def _default_worker_factory(
    messages,
    config_manager,
    workspace_dir,
    agent_id,
    conversation_id,
    run_context=None,
):
    from core.agent import LLMWorker

    return LLMWorker(
        messages,
        config_manager,
        workspace_dir,
        parent_agent_id=agent_id,
        session_id=conversation_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        is_subagent=True,
        run_context=run_context,
    )


@dataclass
class AgentRuntimeRecord:
    agent_id: str
    conversation_id: str
    parent_message_id: str = ""
    name: str = ""
    status: str = "queued"
    fork_context: bool = False
    created_at: int = 0
    updated_at: int = 0
    started_at: int = 0
    finished_at: int = 0
    last_error: str = ""
    last_result: str = ""
    source_tool_call_id: str = ""
    run_context: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    worker: object = None
    messages: list = field(default_factory=list)
    pending_inputs: list = field(default_factory=list)
    pending_restart: bool = False
    worker_result_received: bool = False
    worker_cleanup_pending: bool = False
    closing_requested: bool = False
    force_close: bool = False
    run_id: str = ""
    paused: bool = False
    interrupted: bool = False
    draft_content: str = ""
    draft_reasoning: str = ""
    assistant_id: str = ""
    storage_error: str = ""
    final_result: object = None
    execution_authorization: object = field(default=None, repr=False)

    def is_live(self):
        return self.status in AGENT_LIVE_STATUSES

    def is_terminal(self):
        return self.status in AGENT_TERMINAL_STATUSES


class SessionAgentManager:
    def __init__(
        self,
        conversation_id,
        chat_storage,
        config_manager,
        workspace_dir=None,
        step_signal=None,
        agent_state_signal=None,
        owner_worker=None,
        worker_factory=None,
        max_live_agents=8,
    ):
        self.conversation_id = conversation_id
        self.chat_storage = chat_storage
        self.config_manager = config_manager
        self.workspace_dir = workspace_dir
        self.step_signal = step_signal
        self.agent_state_signal = agent_state_signal
        self.owner_worker = owner_worker
        self.worker_factory = worker_factory or _default_worker_factory
        self.max_live_agents = max_live_agents
        self._event_bridge = _ManagerEventBridge(owner_worker=owner_worker) if owner_worker is not None else None

        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._agents = {}
        self.execution_owner = "local"
        self._loaded = False
        from core.runtime_journal import RuntimeJournal
        self.journal = RuntimeJournal(os.path.dirname(chat_storage.db_path))
        _log_agent_runtime(
            "manager_init",
            conversation_id=self.conversation_id,
            workspace_dir=self.workspace_dir,
            has_step_signal=bool(self.step_signal),
            has_agent_state_signal=bool(self.agent_state_signal),
            owner_worker=owner_worker.__class__.__name__ if owner_worker is not None else "",
        )

    def update_runtime_context(
        self,
        chat_storage=None,
        config_manager=None,
        workspace_dir=None,
        step_signal=None,
        agent_state_signal=None,
        owner_worker=None,
    ):
        with self._lock:
            if chat_storage is not None:
                self.chat_storage = chat_storage
            if config_manager is not None:
                self.config_manager = config_manager
            if workspace_dir is not None:
                self.workspace_dir = workspace_dir
            if step_signal is not None:
                self.step_signal = step_signal
            if agent_state_signal is not None:
                self.agent_state_signal = agent_state_signal
            if owner_worker is not None and owner_worker is not self.owner_worker:
                self.owner_worker = owner_worker
                self._event_bridge = _ManagerEventBridge(owner_worker=owner_worker)
            _log_agent_runtime(
                "manager_update_runtime_context",
                conversation_id=self.conversation_id,
                workspace_dir=self.workspace_dir,
                has_step_signal=bool(self.step_signal),
                has_agent_state_signal=bool(self.agent_state_signal),
                owner_worker=self.owner_worker.__class__.__name__ if self.owner_worker is not None else "",
            )

    def _emit_step(self, text):
        if not self.step_signal:
            if self._event_bridge is None:
                return
        try:
            if self.step_signal:
                self.step_signal.emit(str(text))
                return
        except Exception as exc:
            _log_agent_runtime("emit_step_signal_failed", conversation_id=self.conversation_id, error=_short_debug_value(exc))
        if self._event_bridge is not None:
            try:
                self._event_bridge.step_message.emit(str(text))
            except Exception as exc:
                _log_agent_runtime("emit_step_bridge_failed", conversation_id=self.conversation_id, error=_short_debug_value(exc))
                return

    def _emit_agent_state(self, record, status, **extra):
        payload = {
            "agent_id": record.agent_id,
            "agent_name": record.name or "",
            "status": status,
            "event_type": str((extra or {}).get("event_type") or status or ""),
            "ts": int((extra or {}).get("ts") or _event_timestamp()),
        }
        if record.source_tool_call_id:
            payload["tool_call_id"] = record.source_tool_call_id
        if isinstance(record.meta, dict):
            for key in ("agent_profile_id", "agent_profile_name", "agent_description", "summon_source"):
                value = record.meta.get(key)
                if value not in (None, ""):
                    payload[key] = value
        payload.update(extra or {})
        payload.update(conversation_id=self.conversation_id, run_id=record.run_id,
                       runtime_status=record.status, pending_count=len(record.pending_inputs))
        if record.run_id:
            event = self.journal.append_event(self._journal_id(record), record.run_id, "agent_state", payload)
            payload["sequence"] = event["sequence"]
        _log_agent_runtime("child_" + status, conversation_id=self.conversation_id,
                           agent_id=record.agent_id, run_id=record.run_id,
                           message_id=extra.get("message_id", record.assistant_id))
        if not self.agent_state_signal:
            if self._event_bridge is None:
                return
        try:
            if self.agent_state_signal:
                self.agent_state_signal.emit(payload)
                return
        except Exception as exc:
            _log_agent_runtime(
                "emit_agent_state_signal_failed",
                conversation_id=self.conversation_id,
                agent_id=record.agent_id,
                status=status,
                error=_short_debug_value(exc),
            )
        if self._event_bridge is not None:
            try:
                self._event_bridge.agent_event.emit(payload)
            except Exception as exc:
                _log_agent_runtime(
                    "emit_agent_state_bridge_failed",
                    conversation_id=self.conversation_id,
                    agent_id=record.agent_id,
                    status=status,
                    error=_short_debug_value(exc),
                )
                return

    def _ensure_conversation_row(self):
        self.chat_storage.upsert_conversation(
            self.conversation_id,
            title=None,
            status="active",
            meta=None,
        )

    def _load_from_storage_unlocked(self):
        if self._loaded:
            return
        for row in self.chat_storage.list_agents(self.conversation_id):
            meta = row.get("meta") or {}
            if meta.get("execution_owner", "local") != self.execution_owner:
                continue
            record = AgentRuntimeRecord(
                agent_id=row["id"], conversation_id=self.conversation_id,
                parent_message_id=row.get("parent_message_id") or "", name=row.get("name") or "",
                status=row["status"], fork_context=bool(row.get("fork_context")),
                created_at=row.get("created_at") or 0, updated_at=row.get("updated_at") or 0,
                started_at=row.get("started_at") or 0, finished_at=row.get("finished_at") or 0,
                last_error=row.get("last_error") or "", last_result=row.get("last_result") or "",
                source_tool_call_id=meta.get("source_tool_call_id", ""),
                run_context=meta.get("run_context", {}), meta=meta,
                run_id=meta.get("run_id", ""), paused=bool(meta.get("paused")),
            )
            self._agents[record.agent_id] = record
            if not meta.get("execution_owner"):
                record.paused = True
                continue
            if record.status not in AGENT_TERMINAL_STATUSES:
                record.status = "interrupted"
                record.paused = True
                record.last_error = "上次运行已中断，已保存内容和待处理消息仍可查看。"
                if record.run_id:
                    self._restore_draft(record)
                    self.chat_storage.settle_agent_input(record.agent_id, record.run_id, "interrupted")
                    self.journal.interrupt_run(self._journal_id(record), record.run_id, reason="process restarted")
                self._persist_record_unlocked(record)
            self._refresh_queue(record)
        self._loaded = True

    def _journal_id(self, record):
        return "subagent:" + self.conversation_id + ":" + record.agent_id

    def _refresh_queue(self, record):
        record.pending_inputs = [row["message_id"] for row in self.chat_storage.agent_inbox(record.agent_id) if row["state"] == "queued"]

    def _resolve(self, target, exact=False):
        self._load_from_storage_unlocked()
        row = self.chat_storage.get_agent(target) if exact else self.chat_storage.resolve_agent_target(self.conversation_id, target)
        if not row or row["conversation_id"] != self.conversation_id or row["id"] not in self._agents:
            raise ValueError("子任务不属于当前会话")
        return self._agents[row["id"]]

    def _restore_draft(self, record):
        run = self.journal.get_run(self._journal_id(record), record.run_id)
        if not run:
            return
        record.assistant_id = run.get("assistant_id", record.run_id + ":assistant")
        last_activity = record.meta.get("run_started_at", 0)
        for event in self.journal.read_events(self._journal_id(record), record.run_id):
            last_activity = max(last_activity, event.get("created_at", 0))
            payload = event.get("payload") or {}
            if payload.get("status") == "content":
                record.draft_content += payload.get("content_delta", "")
            elif payload.get("status") == "content_snapshot":
                record.draft_content = payload.get("content", "")
            elif payload.get("status") == "thinking":
                record.draft_reasoning += payload.get("reasoning_delta", "")
        record.finished_at = last_activity
        if record.meta.get("run_started_at"):
            elapsed = max(0, last_activity - record.meta["run_started_at"])
            record.meta["last_duration"] = elapsed
            record.meta["total_duration"] = record.meta.get("run_total_base", 0) + elapsed
        if record.draft_content or record.draft_reasoning:
            self._save_draft(record)

    def _save_draft(self, record):
        self.chat_storage.merge_agent_messages(record.agent_id, [{
            "id": record.assistant_id, "role": "assistant", "content": record.draft_content,
            "reasoning_content": record.draft_reasoning,
        }], run_id=record.run_id)

    def _build_summary(self, record):
        self._refresh_queue(record)
        elapsed = record.meta.get("last_duration")
        if record.worker is not None and not record.worker_result_received:
            elapsed = max(0, time.time() - record.meta.get("run_started_at", time.time()))
        return {
            "id": record.agent_id, "conversation_id": self.conversation_id,
            "name": record.name, "status": record.status, "run_id": record.run_id,
            "created_at": record.created_at, "updated_at": record.updated_at,
            "started_at": record.started_at, "finished_at": record.finished_at,
            "last_error": record.last_error, "last_result": record.last_result,
            "latest_progress": record.draft_content[-160:] or record.last_result,
            "meta": _json_copy(record.meta, {}), "paused": record.paused,
            "has_pending_input": bool(record.pending_inputs), "pending_count": len(record.pending_inputs),
            "duration": elapsed, "total_duration": record.meta.get("total_duration"),
            "usage": record.meta.get("usage"),
        }

    def _live_count_unlocked(self):
        return len([item for item in self._agents.values() if item.is_live()])

    def _worker_has_thread_finished_signal(self, worker):
        finished = getattr(worker, "finished", None)
        return bool(finished is not None and hasattr(finished, "connect"))

    def _connect_worker_signals(self, record):
        worker, run_id = record.worker, record.run_id
        def connect(name, handler):
            signal = getattr(worker, name, None)
            if signal is None:
                return
            def guarded(*args):
                with self._lock:
                    if record.worker is not worker or record.run_id != run_id:
                        return
                    try:
                        handler(*args)
                    except Exception as exc:
                        record.paused = True
                        record.storage_error = str(exc)
                        record.last_error = "运行记录保存失败：" + str(exc)
                        record.status = "failed"
                        _log_agent_runtime("child_persistence_failed", conversation_id=self.conversation_id,
                                           agent_id=record.agent_id, run_id=run_id, error=str(exc))
                        worker.stop()
                        self._condition.notify_all()
            signal.connect(guarded, Qt.DirectConnection)
        def content(delta):
            if record.storage_error:
                return
            self._emit_agent_state(record, "content", content_delta=str(delta or ""))
            record.draft_content += str(delta or "")
        def snapshot(value):
            self._emit_agent_state(record, "content_snapshot", content=str(value or ""))
            record.draft_content = str(value or "")
        def thinking(delta):
            self._emit_agent_state(record, "thinking", reasoning_delta=str(delta or ""))
            record.draft_reasoning += str(delta or "")
        def tool(payload, result=False):
            self._emit_agent_state(record, "tool_result" if result else "tool_use",
                event_type="tool_result" if result else "tool_call", tool_call_id=payload.get("id", ""),
                tool_name=payload.get("name", ""), tool_args=payload.get("args", {}),
                tool_result=payload.get("result", ""), tool_result_obj=payload.get("result_obj"),
                duration=(payload.get("meta") or {}).get("duration"))
        def usage(payload):
            if payload.get("type") != "llm_usage":
                return
            value = payload.get("usage") or {}
            key = str(value.get("request_id") or "")
            if not key:
                return
            record.meta.setdefault("usage_requests", {})[key] = value
            values = record.meta["usage_requests"].values()
            record.meta["usage"] = {
                "input_tokens": sum(int(v.get("prompt_tokens", v.get("input_tokens", 0)) or 0) for v in values),
                "output_tokens": sum(int(v.get("completion_tokens", v.get("output_tokens", 0)) or 0) for v in values),
            }
            self._persist_record_unlocked(record)
        connect("content_signal", content)
        connect("content_snapshot_signal", snapshot)
        connect("thinking_signal", thinking)
        connect("step_signal", lambda text: self._emit_agent_state(record, "log", log_content=str(text)))
        connect("output_signal", lambda text: self._emit_agent_state(record, "log", log_content=str(text)))
        connect("tool_call_signal", lambda payload: tool(payload))
        connect("tool_result_signal", lambda payload: tool(payload, True))
        connect("observability_signal", usage)
        connect("finished_signal", lambda result: self._on_worker_finished(record.agent_id, result))
        connect("finished", lambda: self._on_worker_thread_finished(record.agent_id, worker))

    def _persist_record_unlocked(self, record):
        record.updated_at = time.time()
        record.meta.update(run_id=record.run_id, paused=record.paused,
                           execution_owner=self.execution_owner,
                           source_tool_call_id=record.source_tool_call_id,
                           run_context=record.run_context, workspace_dir=self.workspace_dir or record.meta.get("workspace_dir"))
        self.chat_storage.upsert_agent(
            record.agent_id, conversation_id=self.conversation_id,
            parent_message_id=record.parent_message_id or None, name=record.name,
            status=record.status, fork_context=record.fork_context,
            created_at=record.created_at, updated_at=record.updated_at,
            started_at=record.started_at, finished_at=record.finished_at,
            last_error=record.last_error, last_result=record.last_result, meta=record.meta,
        )

    def _start_worker_unlocked(self, record):
        self._refresh_queue(record)
        if record.worker is not None or record.paused or not record.pending_inputs:
            return
        if sum(item.worker is not None for item in self._agents.values()) >= self.max_live_agents:
            record.status = "queued"
            self._persist_record_unlocked(record)
            return
        record.run_id = uuid.uuid4().hex
        record.assistant_id = record.run_id + ":assistant"
        record.draft_content = record.draft_reasoning = record.storage_error = ""
        record.interrupted = record.worker_result_received = record.worker_cleanup_pending = False
        record.last_error = ""
        record.final_result = None
        record.meta["run_total_base"] = record.meta.get("total_duration", 0)
        record.meta["run_started_at"] = time.time()
        record.meta["run_usage"] = {}
        self.journal.begin_run(self._journal_id(record), record.run_id, extra={"assistant_id": record.assistant_id})
        record.status = "running"
        self._persist_record_unlocked(record)
        claimed = self.chat_storage.claim_agent_input(record.agent_id, record.run_id)
        if not claimed:
            return
        _log_agent_runtime("child_input_claimed", conversation_id=self.conversation_id,
                           agent_id=record.agent_id, run_id=record.run_id,
                           message_id=claimed["message_id"])
        record.status = "running"
        record.started_at = record.started_at or time.time()
        record.finished_at = 0
        self._persist_record_unlocked(record)
        record.messages = self.chat_storage.agent_execution_messages(record.agent_id)
        # Reserve the visible answer before subsequent user inputs are admitted.
        self._save_draft(record)
        try:
            from core.execution_authorization import bind_authorization
            with bind_authorization(record.execution_authorization):
                worker = self.worker_factory(_json_copy(record.messages, []), self.config_manager,
                    self.workspace_dir or record.meta.get("workspace_dir"), record.agent_id,
                    self.conversation_id, _json_copy(record.run_context, {}))
            worker.turn_id = record.run_id
            worker.request_id = record.run_id
            record.worker = worker
            self._connect_worker_signals(record)
            self._emit_agent_state(record, "running")
            worker.start()
        except Exception as exc:
            record.paused = True
            record.status = "failed"
            record.last_error = str(exc)
            self.chat_storage.settle_agent_input(record.agent_id, record.run_id, "failed")
            self.journal.update_run(self._journal_id(record), record.run_id, {"status": "failed"})
            record.worker = None
            self._persist_record_unlocked(record)
            self._condition.notify_all()
            raise
        self._refresh_queue(record)


    def _on_worker_finished(self, agent_id, result):
        with self._lock:
            record = self._agents[agent_id]
            if record.worker_result_received:
                return
            record.final_result = _json_copy(result, {})
            generated = [dict(m) for m in result.get("generated_messages", []) if isinstance(m, dict)]
            # Only the child's new output is merged; queued user messages remain untouched.
            for index, message in enumerate(generated):
                message["id"] = record.run_id + ":message:" + str(index)
            assistants = [m for m in generated if m.get("role") == "assistant" and not m.get("tool_calls")]
            if assistants:
                assistants[-1]["id"] = record.assistant_id
                if not assistants[-1].get("content") and record.draft_content:
                    assistants[-1]["content"] = record.draft_content
                if not assistants[-1].get("reasoning_content") and record.draft_reasoning:
                    assistants[-1]["reasoning_content"] = record.draft_reasoning
            elif result.get("content") or record.draft_content or record.draft_reasoning:
                generated.append({"id": record.assistant_id, "role": "assistant",
                    "content": result.get("content") or record.draft_content,
                    "reasoning_content": result.get("reasoning") or record.draft_reasoning})
            if generated:
                self.chat_storage.merge_agent_messages(agent_id, generated, run_id=record.run_id)
            if record.closing_requested:
                record.status = "killed" if record.force_close else "closed"
            elif record.interrupted:
                record.status = "interrupted"
            elif result.get("error") or record.storage_error:
                record.status = "failed"
                record.paused = True
            else:
                record.status = "completed"
            record.last_result = str(result.get("content") or record.draft_content or "")
            record.last_error = record.last_error if record.closing_requested else str(record.storage_error or result.get("error") or "")
            record.finished_at = time.time()
            elapsed = max(0, record.finished_at - record.meta["run_started_at"])
            record.meta["last_duration"] = elapsed
            record.meta["total_duration"] = record.meta.get("run_total_base", 0) + elapsed
            self.chat_storage.settle_agent_input(agent_id, record.run_id, record.status)
            self.journal.update_run(self._journal_id(record), record.run_id, {
                "status": "interrupted" if record.status in {"closed", "killed", "interrupted"} else record.status,
                "finished_at": record.finished_at})
            self._persist_record_unlocked(record)
            self._emit_agent_state(record, record.status, content=record.last_result, error=record.last_error)
            record.worker_result_received = True
            record.final_result = None
            worker = record.worker
            if self._worker_has_thread_finished_signal(worker):
                record.worker_cleanup_pending = True
            else:
                record.worker = None
                self._schedule_ready()
            self._condition.notify_all()

    def _schedule_ready(self):
        for item in sorted(self._agents.values(), key=lambda r: r.created_at):
            if item.status not in {"closed", "killed"}:
                try:
                    self._start_worker_unlocked(item)
                except Exception as exc:
                    item.paused = True
                    item.status = "failed"
                    item.last_error = str(exc)
                    _log_agent_runtime("child_start_failed", conversation_id=self.conversation_id,
                                       agent_id=item.agent_id, run_id=item.run_id, error=str(exc))

    def _on_worker_thread_finished(self, agent_id, worker):
        with self._lock:
            record = self._agents.get(agent_id)
            if not record or record.worker is not worker:
                return
            try:
                if not record.worker_result_received:
                    self._on_worker_finished(agent_id, record.final_result or {
                        "error": record.storage_error or "运行线程意外退出", "content": record.draft_content})
            except Exception as exc:
                record.paused = True
                record.storage_error = str(exc)
                record.last_error = "运行记录保存失败：" + str(exc)
                record.status = "failed"
                _log_agent_runtime("child_final_save_failed", conversation_id=self.conversation_id,
                                   agent_id=agent_id, run_id=record.run_id, error=str(exc))
            finally:
                record.worker = None
                record.worker_cleanup_pending = False
                record.pending_restart = False
                worker.deleteLater()
                self._condition.notify_all()
            self._schedule_ready()

    def spawn_agent(self, message, name=None, fork_context=False, current_messages_snapshot=None,
                    parent_message_id=None, source_tool_call_id=None, run_context=None, meta=None):
        text = str(message or "").strip()
        if not text:
            raise ValueError("message is required")
        with self._lock:
            self._load_from_storage_unlocked()
            self._ensure_conversation_row()
            if name and any(r.name == name and r.status not in {"closed", "killed"} for r in self._agents.values()):
                raise ValueError("agent name already exists")
            agent_id = uuid.uuid4().hex
            from core.execution_authorization import current_authorization
            record = AgentRuntimeRecord(agent_id=agent_id, conversation_id=self.conversation_id,
                parent_message_id=parent_message_id or "", name=name or "", fork_context=bool(fork_context),
                source_tool_call_id=source_tool_call_id or "", created_at=time.time(),
                run_context=_json_copy(run_context, {}), meta=_json_copy(meta, {}),
                execution_authorization=current_authorization())
            self._persist_record_unlocked(record)
            if fork_context and isinstance(current_messages_snapshot, list):
                seed = [_json_copy(m,{}) for m in current_messages_snapshot if isinstance(m,dict) and m.get("role") != "system"]
                # Message ids are local to the child; provider-visible content is unchanged.
                for i, m in enumerate(seed):
                    m["id"] = agent_id + ":seed:" + str(i)
                self.chat_storage.merge_agent_messages(agent_id, seed)
            message_id = uuid.uuid4().hex
            self.chat_storage.enqueue_agent_input(agent_id, text, message_id, "parent")
            self._agents[agent_id] = record
            self._emit_agent_state(record, "input", content=text, input_text=text, message_id=message_id)
            self._start_worker_unlocked(record)
            return {"status": "spawned", "agent_id": agent_id, "name": record.name, "conversation_id": self.conversation_id}

    def send_input(self, target, message, message_id=None, source="parent"):
        text = str(message or "").strip()
        with self._lock:
            record = self._resolve(target)
            if not record.meta.get("execution_owner"):
                raise ValueError("旧版子任务缺少运行归属，只能查看历史")
            row, inserted = self.chat_storage.enqueue_agent_input(record.agent_id, text, message_id or uuid.uuid4().hex, source)
            self._refresh_queue(record)
            if inserted:
                self._emit_agent_state(record, "input", content=text, input_text=text, message_id=row["message_id"])
            if not record.paused:
                from core.execution_authorization import current_authorization
                if record.worker is None:
                    record.execution_authorization = current_authorization()
                self._start_worker_unlocked(record)
                if record.worker_result_received and record.pending_inputs:
                    record.status = "queued"
                    self._persist_record_unlocked(record)
            self._condition.notify_all()
            return {"status": "queued", "agent_id": record.agent_id,
                    "message_id": row["message_id"], "pending_inputs": len(record.pending_inputs)}

    def interrupt_agent(self, target, expected_run_id):
        with self._lock:
            record = self._resolve(target, exact=True)
            if record.status in {"closed", "killed"} or not record.meta.get("execution_owner"):
                raise ValueError("此子任务历史只读，不能停止或恢复")
            if record.run_id != expected_run_id:
                raise ValueError("运行已更新，请刷新后再停止")
            record.paused = True
            record.interrupted = True
            record.status = "stopping" if record.worker is not None and not record.worker_result_received else "interrupted"
            self._persist_record_unlocked(record)
            self._emit_agent_state(record, record.status)
            if record.worker is not None:
                record.worker.stop()
            self._condition.notify_all()
            return self._build_summary(record)

    def resume_agent(self, target):
        with self._lock:
            record = self._resolve(target, exact=True)
            if not record.meta.get("execution_owner"):
                raise ValueError("旧版子任务缺少运行归属，只能查看历史")
            if record.status in {"closed", "killed"}:
                raise ValueError("已关闭的子任务不能继续")
            if record.worker is not None:
                raise ValueError("上一轮尚未停止，请稍后继续")
            if record.final_result is not None:
                self._on_worker_finished(record.agent_id, record.final_result)
            from core.execution_authorization import ExecutionAuthorization
            record.execution_authorization = ExecutionAuthorization.start(self.config_manager,
                self.conversation_id, uuid.uuid4().hex, self.workspace_dir or record.meta.get("workspace_dir"))
            record.paused = False
            self._persist_record_unlocked(record)
            self._start_worker_unlocked(record)
            return self._build_summary(record)

    def history(self, target, before=None, limit=50):
        with self._lock:
            record = self._resolve(target, exact=True)
            page = self.chat_storage.agent_history_page(record.agent_id, before, limit)
            if record.worker is not None and not record.worker_result_received:
                for message in page["messages"]:
                    if message["id"] == record.assistant_id:
                        message["content"] = record.draft_content
                        message["reasoning_content"] = record.draft_reasoning
            activity = []
            run_ids = {m["id"].split(":")[0] for m in page["messages"] if ":" in m["id"] and ":seed:" not in m["id"]}
            for run_id in run_ids:
                activity.extend(event for event in self.journal.read_events(self._journal_id(record), run_id)
                                if (event.get("payload") or {}).get("event_type") in {"tool_call", "tool_result"})
            page.update(summary=self._build_summary(record), inbox=self.chat_storage.agent_inbox(record.agent_id), activity=activity)
            return page

    def events(self, target, run_id, after=0):
        with self._lock:
            record = self._resolve(target, exact=True)
            events = self.journal.read_events(self._journal_id(record), run_id, starting_after=int(after)) if run_id else []
            return {"events": events, "summary": self._build_summary(record)}

    def wait_agent(self, targets, timeout_ms=30000, return_when="any"):
        mode = "all" if str(return_when or "").lower() == "all" else "any"
        timeout_seconds = max(float(timeout_ms or 0), 0.0) / 1000.0
        deadline = time.time() + timeout_seconds if timeout_seconds > 0 else None
        with self._lock:
            _log_agent_runtime(
                "wait_agent_begin",
                conversation_id=self.conversation_id,
                targets=targets,
                timeout_ms=timeout_ms,
                return_when=return_when,
            )
            self._load_from_storage_unlocked()
            target_values = []
            if isinstance(targets, (list, tuple, set)):
                target_values = [str(item).strip() for item in targets if str(item).strip()]
            elif isinstance(targets, str) and targets.strip():
                target_values = [targets.strip()]

            resolved_ids = []
            if target_values:
                for item in target_values:
                    resolved = self.chat_storage.resolve_agent_target(self.conversation_id, item)
                    resolved_ids.append(resolved["id"])
            else:
                resolved_ids = list(self._agents.keys())

            resolved_ids = list(dict.fromkeys(resolved_ids))

            def _snapshot():
                completed = []
                pending = []
                for agent_id in resolved_ids:
                    record = self._agents.get(agent_id)
                    if not record:
                        row = self.chat_storage.get_agent(agent_id)
                        if not row:
                            continue
                        record = AgentRuntimeRecord(
                            agent_id=row["id"],
                            conversation_id=self.conversation_id,
                            name=row.get("name") or "",
                            status=row.get("status") or "queued",
                            created_at=int(row.get("created_at") or 0),
                            updated_at=int(row.get("updated_at") or 0),
                            started_at=int(row.get("started_at") or 0),
                            finished_at=int(row.get("finished_at") or 0),
                            last_error=row.get("last_error") or "",
                            last_result=row.get("last_result") or "",
                        )
                    info = self._build_summary(record)
                    if record.status in AGENT_TERMINAL_STATUSES:
                        completed.append(info)
                    else:
                        pending.append(info)
                return completed, pending

            timed_out = False
            while True:
                completed, pending = _snapshot()
                done = bool(completed) if mode == "any" else (len(pending) == 0)
                if done:
                    _log_agent_runtime(
                        "wait_agent_done",
                        conversation_id=self.conversation_id,
                        completed_count=len(completed),
                        pending_count=len(pending),
                        timed_out=False,
                    )
                    return {
                        "completed": completed,
                        "pending": pending,
                        "timed_out": False,
                    }
                if deadline is None:
                    timed_out = True
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    timed_out = True
                    break
                self._condition.wait(timeout=remaining)

            completed, pending = _snapshot()
            _log_agent_runtime(
                "wait_agent_done",
                conversation_id=self.conversation_id,
                completed_count=len(completed),
                pending_count=len(pending),
                timed_out=timed_out,
            )
            return {
                "completed": completed,
                "pending": pending,
                "timed_out": timed_out,
            }

    def close_agent(self, target, force=False, reason=None):
        with self._lock:
            self._load_from_storage_unlocked()
            resolved = self.chat_storage.resolve_agent_target(self.conversation_id, target)
            record = self._agents.get(resolved["id"])
            _log_agent_runtime(
                "close_agent_begin",
                conversation_id=self.conversation_id,
                agent_id=resolved.get("id") or "",
                force=bool(force),
                reason=_short_debug_value(reason),
                known_record=bool(record),
            )
            if not record:
                record = AgentRuntimeRecord(
                    agent_id=resolved["id"],
                    conversation_id=self.conversation_id,
                    name=resolved.get("name") or "",
                    status=resolved.get("status") or "queued",
                    created_at=int(resolved.get("created_at") or 0),
                    updated_at=int(resolved.get("updated_at") or 0),
                    started_at=int(resolved.get("started_at") or 0),
                    finished_at=int(resolved.get("finished_at") or 0),
                    last_error=resolved.get("last_error") or "",
                    last_result=resolved.get("last_result") or "",
                    messages=self.chat_storage.get_agent_messages(resolved["id"]),
                )
                self._agents[record.agent_id] = record

            close_reason = str(reason or "").strip()
            worker = record.worker
            if worker is not None:
                record.closing_requested = True
                record.force_close = bool(force)
                if close_reason:
                    record.last_error = close_reason
                    self._emit_agent_state(record, "log", log_content=close_reason)
                try:
                    worker.stop()
                except Exception as exc:
                    _log_agent_runtime(
                        "close_agent_worker_stop_failed",
                        conversation_id=self.conversation_id,
                        agent_id=record.agent_id,
                        error=_short_debug_value(exc),
                    )
                try:
                    worker.quit()
                    worker.wait(300)
                except Exception as exc:
                    _log_agent_runtime(
                        "close_agent_worker_quit_failed",
                        conversation_id=self.conversation_id,
                        agent_id=record.agent_id,
                        error=_short_debug_value(exc),
                    )
                if force:
                    try:
                        if worker.isRunning():
                            worker.terminate()
                            worker.wait(300)
                    except Exception as exc:
                        _log_agent_runtime(
                            "close_agent_worker_terminate_failed",
                            conversation_id=self.conversation_id,
                            agent_id=record.agent_id,
                            error=_short_debug_value(exc),
                        )
                if not worker.isRunning():
                    record.worker = None
                    record.status = "killed" if force else "closed"
                    record.finished_at = int(time.time())
                    if close_reason:
                        record.last_error = close_reason
                    self._persist_record_unlocked(record)
            else:
                record.status = "killed" if force else "closed"
                record.finished_at = int(time.time())
                if close_reason:
                    record.last_error = close_reason
                self._persist_record_unlocked(record)
            payload = {}
            if close_reason:
                payload["error"] = close_reason
            self._emit_agent_state(record, record.status, **payload)
            self._condition.notify_all()
            _log_agent_runtime(
                "close_agent_done",
                conversation_id=self.conversation_id,
                agent_id=record.agent_id,
                status=record.status,
                force=bool(force),
            )
            return self._build_summary(record)

    def list_agent_summaries(self, status_filter=None):
        with self._lock:
            self._load_from_storage_unlocked()
            return [self._build_summary(r) for r in sorted(self._agents.values(), key=lambda r: r.created_at)
                    if not status_filter or r.status in ([status_filter] if isinstance(status_filter, str) else status_filter)]

class AgentManagerRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._managers = {}

    def active_count(self, db_path):
        with self._lock:
            managers = [m for (path, _), m in self._managers.items() if path == os.path.abspath(db_path)]
        return sum(record.worker is not None for manager in managers for record in list(manager._agents.values()))

    def request_stop(self, db_path):
        """Pause local child queues and request cancellation without joining."""
        with self._lock:
            managers = [m for (path, _), m in self._managers.items() if path == os.path.abspath(db_path)]
        targets = []
        for manager in managers:
            with manager._condition:
                records = [record for record in manager._agents.values()
                           if record.worker is not None or (record.status not in {"closed", "killed"}
                               and (record.status in AGENT_LIVE_STATUSES or record.pending_inputs))]
                for record in records:
                    manager.interrupt_agent(record.agent_id, record.run_id)
                targets.append((manager, records))
        return targets

    def stop_for_update(self, db_path, timeout=30):
        """Pause child queues, drain accepted output, and require durable terminal state."""
        deadline = time.monotonic() + timeout
        targets = self.request_stop(db_path)
        for manager, records in targets:
            with manager._condition:
                while any(record.worker is not None for record in records):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("子任务仍在保存或停止，请稍后重试更新。")
                    manager._condition.wait(remaining)
                if any(record.storage_error for record in records):
                    raise RuntimeError("子任务内容保存失败，尚未开始安装。")

    def get_session_manager(
        self,
        conversation_id,
        chat_storage,
        config_manager,
        workspace_dir=None,
        step_signal=None,
        agent_state_signal=None,
        owner_worker=None,
        worker_factory=None,
    ):
        key = str(conversation_id or "").strip()
        if not key:
            raise ValueError("conversation_id is required")
        key = (os.path.abspath(chat_storage.db_path), key)
        with self._lock:
            manager = self._managers.get(key)
            if manager is None:
                _log_agent_runtime("registry_create_manager", conversation_id=key)
                manager = SessionAgentManager(
                    conversation_id,
                    chat_storage=chat_storage,
                    config_manager=config_manager,
                    workspace_dir=workspace_dir,
                    step_signal=step_signal,
                    agent_state_signal=agent_state_signal,
                    owner_worker=owner_worker,
                    worker_factory=worker_factory,
                )
                self._managers[key] = manager
            else:
                _log_agent_runtime("registry_reuse_manager", conversation_id=key)
                manager.update_runtime_context(
                    chat_storage=chat_storage,
                    config_manager=config_manager,
                    workspace_dir=workspace_dir,
                    step_signal=step_signal,
                    agent_state_signal=agent_state_signal,
                    owner_worker=owner_worker,
                )
            return manager


_GLOBAL_REGISTRY = AgentManagerRegistry()


def get_agent_manager_registry():
    return _GLOBAL_REGISTRY
