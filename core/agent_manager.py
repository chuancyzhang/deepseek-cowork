import json
import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, Signal
from core.chat_storage import AGENT_TERMINAL_STATUSES, ChatStorage
from core.env_utils import get_app_data_dir
from core.process_utils import runtime_debug_logging_enabled

AGENT_MANAGEMENT_TOOLS = {
    "spawn_agent",
    "send_input",
    "wait_agent",
    "close_agent",
    "list_agents",
}

AGENT_LIVE_STATUSES = {"queued", "running", "waiting_input"}


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
    if not runtime_debug_logging_enabled():
        return
    try:
        payload = {
            "event": event,
            "thread_id": threading.get_ident(),
        }
        payload.update(fields or {})
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
        self._loaded = False
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
        self._loaded = True
        _log_agent_runtime("load_agents_from_storage_start", conversation_id=self.conversation_id)
        for row in self.chat_storage.list_agents(self.conversation_id):
            status = row.get("status") or "queued"
            if status not in AGENT_TERMINAL_STATUSES:
                recovered = "failed_recovered" if status == "running" else "closed"
                _log_agent_runtime(
                    "recover_nonterminal_agent",
                    conversation_id=self.conversation_id,
                    agent_id=row.get("id") or "",
                    old_status=status,
                    new_status=recovered,
                )
                updated = self.chat_storage.set_agent_status(
                    row["id"],
                    recovered,
                    last_error="Agent runtime was not alive after process restart.",
                )
                row = updated or row
            meta = row.get("meta") or {}
            record = AgentRuntimeRecord(
                agent_id=row.get("id") or "",
                conversation_id=self.conversation_id,
                parent_message_id=row.get("parent_message_id") or "",
                name=row.get("name") or "",
                status=row.get("status") or "queued",
                fork_context=bool(row.get("fork_context")),
                created_at=int(row.get("created_at") or 0),
                updated_at=int(row.get("updated_at") or 0),
                started_at=int(row.get("started_at") or 0),
                finished_at=int(row.get("finished_at") or 0),
                last_error=row.get("last_error") or "",
                last_result=row.get("last_result") or "",
                source_tool_call_id=str(meta.get("source_tool_call_id") or ""),
                run_context=_json_copy(meta.get("run_context"), {}),
                meta=_json_copy(meta, {}),
                messages=self.chat_storage.get_agent_messages(row.get("id") or ""),
            )
            if record.agent_id:
                self._agents[record.agent_id] = record
        _log_agent_runtime(
            "load_agents_from_storage_done",
            conversation_id=self.conversation_id,
            known_count=len(self._agents),
        )

    def _build_summary(self, record):
        return {
            "id": record.agent_id,
            "conversation_id": record.conversation_id,
            "name": record.name or "",
            "status": record.status,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "last_error": record.last_error or "",
            "last_result": record.last_result or "",
            "meta": _json_copy(record.meta, {}),
            "has_pending_input": bool(record.pending_inputs),
        }

    def _live_count_unlocked(self):
        return len([item for item in self._agents.values() if item.is_live()])

    def _worker_has_thread_finished_signal(self, worker):
        finished = getattr(worker, "finished", None)
        return bool(finished is not None and hasattr(finished, "connect"))

    def _connect_worker_signals(self, record):
        worker = record.worker
        if worker is None:
            return
        agent_id = record.agent_id
        _log_agent_runtime(
            "connect_worker_signals_start",
            conversation_id=self.conversation_id,
            agent_id=agent_id,
            **_debug_worker_summary(worker),
        )

        def _on_step(message):
            with self._lock:
                latest = self._agents.get(agent_id)
                if not latest:
                    return
                text = str(message)
                lower = text.lower()
                if lower.startswith("provider start:") or lower.startswith("provider end:"):
                    self._emit_agent_state(latest, "provider_log", provider_message=text)
                elif lower.startswith("provider error:"):
                    self._emit_agent_state(latest, "provider_error", provider_message=text)
                else:
                    self._emit_agent_state(latest, "log", log_content=text)

        def _on_thinking(delta):
            with self._lock:
                latest = self._agents.get(agent_id)
                if not latest:
                    return
                self._emit_agent_state(latest, "thinking", reasoning_delta=str(delta or ""))

        def _on_content(delta):
            with self._lock:
                latest = self._agents.get(agent_id)
                if not latest:
                    return
                self._emit_agent_state(latest, "content", content_delta=str(delta or ""))

        def _on_output(message):
            with self._lock:
                latest = self._agents.get(agent_id)
                if not latest:
                    return
                text = str(message or "")
                lower = text.lower()
                if "provider" in lower and ("error" in lower or "exception" in lower):
                    self._emit_agent_state(latest, "provider_error", provider_message=text)
                else:
                    self._emit_agent_state(latest, "log", log_content=text)

        def _on_tool_call(payload):
            tool_name = ""
            tool_args = {}
            tool_call_id = ""
            if isinstance(payload, dict):
                tool_name = str(payload.get("name") or "")
                tool_args = _json_copy(payload.get("args"), {})
                tool_call_id = str(payload.get("id") or "")
            with self._lock:
                latest = self._agents.get(agent_id)
                if not latest:
                    return
                self._emit_agent_state(
                    latest,
                    "tool_use",
                    event_type="tool_call",
                    task=f"Tool: {tool_name or 'unknown'}",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name or "unknown",
                    tool_args=tool_args,
                )

        def _on_tool_result(payload):
            tool_name = ""
            tool_args = {}
            tool_call_id = ""
            tool_result = ""
            tool_result_obj = None
            duration = None
            if isinstance(payload, dict):
                tool_name = str(payload.get("name") or "")
                tool_args = _json_copy(payload.get("args"), {})
                tool_call_id = str(payload.get("id") or "")
                tool_result = str(payload.get("result") or "")
                tool_result_obj = _json_copy(payload.get("result_obj"), None)
                meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
                try:
                    duration = float(meta.get("duration")) if meta.get("duration") is not None else None
                except Exception:
                    duration = None
            with self._lock:
                latest = self._agents.get(agent_id)
                if not latest:
                    return
                self._emit_agent_state(
                    latest,
                    "tool_result",
                    event_type="tool_result",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name or "unknown",
                    tool_args=tool_args,
                    tool_result=tool_result,
                    tool_result_obj=tool_result_obj,
                    duration=duration,
                )

        def _on_finished(result):
            self._on_worker_finished(agent_id, result if isinstance(result, dict) else {})

        def _on_thread_finished():
            self._on_worker_thread_finished(agent_id, worker)

        try:
            if getattr(worker, "step_signal", None):
                worker.step_signal.connect(_on_step, Qt.DirectConnection)
            if getattr(worker, "thinking_signal", None):
                worker.thinking_signal.connect(_on_thinking, Qt.DirectConnection)
            if getattr(worker, "content_signal", None):
                worker.content_signal.connect(_on_content, Qt.DirectConnection)
            if getattr(worker, "output_signal", None):
                worker.output_signal.connect(_on_output, Qt.DirectConnection)
            if getattr(worker, "tool_call_signal", None):
                worker.tool_call_signal.connect(_on_tool_call, Qt.DirectConnection)
            if getattr(worker, "tool_result_signal", None):
                worker.tool_result_signal.connect(_on_tool_result, Qt.DirectConnection)
            if getattr(worker, "finished_signal", None):
                worker.finished_signal.connect(_on_finished, Qt.DirectConnection)
            if self._worker_has_thread_finished_signal(worker):
                worker.finished.connect(_on_thread_finished, Qt.DirectConnection)
            _log_agent_runtime(
                "connect_worker_signals_done",
                conversation_id=self.conversation_id,
                agent_id=agent_id,
                has_thread_finished=self._worker_has_thread_finished_signal(worker),
            )
        except Exception as exc:
            _log_agent_runtime(
                "connect_worker_signals_failed",
                conversation_id=self.conversation_id,
                agent_id=agent_id,
                error=_short_debug_value(exc),
                traceback=traceback.format_exc(),
            )
            raise

    def _persist_record_unlocked(self, record):
        now = int(time.time())
        record.updated_at = now
        meta = {}
        if isinstance(record.meta, dict):
            meta_copy = _json_copy(record.meta, {})
            if isinstance(meta_copy, dict):
                meta.update(meta_copy)
        if record.source_tool_call_id:
            meta["source_tool_call_id"] = record.source_tool_call_id
        if record.run_context:
            run_context_copy = _json_copy(record.run_context, {})
            meta["run_context"] = run_context_copy if isinstance(run_context_copy, dict) else {}
        self.chat_storage.upsert_agent(
            record.agent_id,
            conversation_id=record.conversation_id,
            parent_message_id=record.parent_message_id or None,
            name=record.name or None,
            status=record.status,
            is_subagent=True,
            fork_context=record.fork_context,
            created_at=record.created_at or now,
            updated_at=record.updated_at,
            started_at=record.started_at or None,
            finished_at=record.finished_at or None,
            last_error=record.last_error or None,
            last_result=record.last_result or None,
            meta=meta,
        )
        self.chat_storage.replace_agent_messages(record.agent_id, record.messages)

    def _start_worker_unlocked(self, record):
        if record.worker is not None:
            try:
                if record.worker.isRunning():
                    _log_agent_runtime(
                        "start_worker_skip_already_running",
                        conversation_id=self.conversation_id,
                        agent_id=record.agent_id,
                        status=record.status,
                        **_debug_worker_summary(record.worker),
                    )
                    return
            except Exception as exc:
                _log_agent_runtime(
                    "start_worker_is_running_check_failed",
                    conversation_id=self.conversation_id,
                    agent_id=record.agent_id,
                    error=_short_debug_value(exc),
                )
        record.status = "running"
        if not record.started_at:
            record.started_at = int(time.time())
        record.finished_at = 0
        self._persist_record_unlocked(record)
        messages_copy = _json_copy(record.messages, [])
        if not isinstance(messages_copy, list):
            messages_copy = []
        run_context_copy = _json_copy(record.run_context, {})
        if not isinstance(run_context_copy, dict):
            run_context_copy = {}
        _log_agent_runtime(
            "start_worker_factory_begin",
            conversation_id=self.conversation_id,
            agent_id=record.agent_id,
            message_count=len(messages_copy),
            workspace_dir=self.workspace_dir,
            run_context_keys=sorted(list(run_context_copy.keys())),
        )
        try:
            worker = self.worker_factory(
                messages_copy,
                self.config_manager,
                self.workspace_dir,
                record.agent_id,
                self.conversation_id,
                run_context_copy,
            )
        except Exception as exc:
            record.status = "failed"
            record.last_error = str(exc)
            record.finished_at = int(time.time())
            self._persist_record_unlocked(record)
            _log_agent_runtime(
                "start_worker_factory_failed",
                conversation_id=self.conversation_id,
                agent_id=record.agent_id,
                error=_short_debug_value(exc),
                traceback=traceback.format_exc(),
            )
            self._emit_agent_state(record, "failed", error=record.last_error)
            self._condition.notify_all()
            raise
        record.worker = worker
        record.pending_restart = False
        record.worker_result_received = False
        record.worker_cleanup_pending = False
        self._connect_worker_signals(record)
        self._emit_agent_state(record, "pending", task="Starting task")
        self._emit_agent_state(record, "running")
        _log_agent_runtime(
            "start_worker_start_begin",
            conversation_id=self.conversation_id,
            agent_id=record.agent_id,
            **_debug_worker_summary(worker),
        )
        try:
            worker.start()
            _log_agent_runtime(
                "start_worker_start_done",
                conversation_id=self.conversation_id,
                agent_id=record.agent_id,
                **_debug_worker_summary(worker),
            )
        except Exception as exc:
            record.status = "failed"
            record.last_error = str(exc)
            record.finished_at = int(time.time())
            record.worker = None
            self._persist_record_unlocked(record)
            _log_agent_runtime(
                "start_worker_start_failed",
                conversation_id=self.conversation_id,
                agent_id=record.agent_id,
                error=_short_debug_value(exc),
                traceback=traceback.format_exc(),
            )
            self._emit_agent_state(record, "failed", error=record.last_error)
            self._condition.notify_all()
            raise

    def _append_user_input_unlocked(self, record, message):
        created_at = _event_timestamp()
        msg = {
            "id": uuid.uuid4().hex,
            "role": "user",
            "content": message,
            "created_at": created_at,
        }
        record.messages.append(msg)
        record.messages = self.chat_storage.normalize_messages(
            record.messages,
            conversation_id=f"agent:{record.agent_id}",
        )
        self.chat_storage.append_agent_messages(record.agent_id, [msg])
        self._emit_agent_state(
            record,
            "input",
            event_type="input",
            input_text=str(message or ""),
            content=str(message or ""),
            ts=created_at,
        )

    def _on_worker_finished(self, agent_id, result):
        with self._lock:
            record = self._agents.get(agent_id)
            if not record:
                _log_agent_runtime(
                    "worker_finished_missing_record",
                    conversation_id=self.conversation_id,
                    agent_id=agent_id,
                    result_keys=sorted(list(result.keys())) if isinstance(result, dict) else [],
                )
                self._condition.notify_all()
                return

            worker = record.worker
            record.worker_result_received = True
            _log_agent_runtime(
                "worker_finished_signal",
                conversation_id=self.conversation_id,
                agent_id=agent_id,
                status_before=record.status,
                result_keys=sorted(list(result.keys())) if isinstance(result, dict) else [],
                generated_count=len(result.get("generated_messages", [])) if isinstance(result.get("generated_messages"), list) else 0,
                content_preview=_short_debug_value(result.get("content") if isinstance(result, dict) else ""),
                error_preview=_short_debug_value(result.get("error") if isinstance(result, dict) else ""),
                pending_inputs=len(record.pending_inputs),
                **_debug_worker_summary(worker),
            )
            generated_messages = result.get("generated_messages", [])
            if isinstance(generated_messages, list) and generated_messages:
                for item in generated_messages:
                    if isinstance(item, dict):
                        record.messages.append(item)
            else:
                assistant_content = str(result.get("content") or "")
                if assistant_content:
                    record.messages.append(
                        {
                            "id": uuid.uuid4().hex,
                            "role": "assistant",
                            "content": assistant_content,
                            "reasoning": result.get("reasoning") or "",
                            "created_at": int(time.time()),
                        }
                    )
            record.messages = self.chat_storage.normalize_messages(
                record.messages,
                conversation_id=f"agent:{record.agent_id}",
            )
            self.chat_storage.replace_agent_messages(record.agent_id, record.messages)

            if record.closing_requested:
                record.status = "killed" if record.force_close else "closed"
                record.last_result = str(result.get("content") or record.last_result or "")
                record.last_error = str(result.get("error") or record.last_error or "")
                record.closing_requested = False
                record.force_close = False
            elif result.get("error"):
                record.status = "failed"
                record.last_error = str(result.get("error") or "")
                record.last_result = str(result.get("content") or "")
            else:
                record.status = "completed"
                record.last_error = ""
                record.last_result = str(result.get("content") or "")

            if record.pending_inputs and record.status not in {"closed", "killed"}:
                if self._worker_has_thread_finished_signal(worker):
                    record.pending_restart = True
                    record.status = "waiting_input"
                    record.worker_cleanup_pending = True
                    self._persist_record_unlocked(record)
                    self._emit_agent_state(record, "waiting_input")
                    _log_agent_runtime(
                        "worker_finished_defer_pending_restart",
                        conversation_id=self.conversation_id,
                        agent_id=agent_id,
                        pending_inputs=len(record.pending_inputs),
                    )
                else:
                    record.worker = None
                    next_input = record.pending_inputs.pop(0)
                    self._append_user_input_unlocked(record, next_input)
                    self._start_worker_unlocked(record)
                self._condition.notify_all()
                return

            record.finished_at = int(time.time())
            self._persist_record_unlocked(record)
            payload = {}
            if record.last_result:
                payload["content"] = record.last_result
                payload["output_text"] = record.last_result
            if record.last_error:
                payload["error"] = record.last_error
            self._emit_agent_state(record, record.status, **payload)
            if self._worker_has_thread_finished_signal(worker):
                record.worker_cleanup_pending = True
            else:
                record.worker = None
            _log_agent_runtime(
                "worker_finished_processed",
                conversation_id=self.conversation_id,
                agent_id=agent_id,
                status=record.status,
                pending_restart=record.pending_restart,
                cleanup_pending=record.worker_cleanup_pending,
                pending_inputs=len(record.pending_inputs),
                has_worker=record.worker is not None,
            )
            self._condition.notify_all()

    def _on_worker_thread_finished(self, agent_id, worker):
        with self._lock:
            record = self._agents.get(agent_id)
            if not record:
                _log_agent_runtime(
                    "worker_thread_finished_missing_record",
                    conversation_id=self.conversation_id,
                    agent_id=agent_id,
                    **_debug_worker_summary(worker),
                )
                self._condition.notify_all()
                return
            if record.worker is not worker:
                _log_agent_runtime(
                    "worker_thread_finished_stale_worker",
                    conversation_id=self.conversation_id,
                    agent_id=agent_id,
                    current_worker_id=hex(id(record.worker)) if record.worker is not None else "",
                    finished_worker_id=hex(id(worker)),
                    status=record.status,
                )
                self._condition.notify_all()
                return
            _log_agent_runtime(
                "worker_thread_finished",
                conversation_id=self.conversation_id,
                agent_id=agent_id,
                status_before=record.status,
                result_received=record.worker_result_received,
                pending_restart=record.pending_restart,
                pending_inputs=len(record.pending_inputs),
                **_debug_worker_summary(worker),
            )

            if not record.worker_result_received and record.status not in AGENT_TERMINAL_STATUSES:
                record.status = "failed"
                record.last_error = "Agent thread exited before reporting a result."
                record.finished_at = int(time.time())
                self._persist_record_unlocked(record)
                self._emit_agent_state(record, "failed", error=record.last_error)

            record.worker = None
            record.worker_cleanup_pending = False

            try:
                delete_later = getattr(worker, "deleteLater", None)
                if callable(delete_later):
                    delete_later()
            except Exception as exc:
                _log_agent_runtime(
                    "worker_delete_later_failed",
                    conversation_id=self.conversation_id,
                    agent_id=agent_id,
                    error=_short_debug_value(exc),
                )

            if record.pending_restart and record.pending_inputs and record.status not in {"closed", "killed"}:
                record.pending_restart = False
                next_input = record.pending_inputs.pop(0)
                _log_agent_runtime(
                    "worker_thread_finished_restart_pending",
                    conversation_id=self.conversation_id,
                    agent_id=agent_id,
                    remaining_pending_inputs=len(record.pending_inputs),
                )
                self._append_user_input_unlocked(record, next_input)
                self._start_worker_unlocked(record)
                self._condition.notify_all()
                return

            record.pending_restart = False
            _log_agent_runtime(
                "worker_thread_finished_cleanup_done",
                conversation_id=self.conversation_id,
                agent_id=agent_id,
                status=record.status,
                pending_inputs=len(record.pending_inputs),
            )
            self._condition.notify_all()

    def spawn_agent(
        self,
        message,
        name=None,
        fork_context=False,
        current_messages_snapshot=None,
        parent_message_id=None,
        source_tool_call_id=None,
        run_context=None,
        meta=None,
    ):
        text = str(message or "").strip()
        if not text:
            raise ValueError("message is required")
        with self._lock:
            _log_agent_runtime(
                "spawn_agent_begin",
                conversation_id=self.conversation_id,
                requested_name=str(name or ""),
                fork_context=bool(fork_context),
                message_len=len(text),
                source_tool_call_id=str(source_tool_call_id or ""),
            )
            self._load_from_storage_unlocked()
            self._ensure_conversation_row()
            if self._live_count_unlocked() >= self.max_live_agents:
                raise ValueError(f"live agent limit reached ({self.max_live_agents})")
            if name:
                for item in self._agents.values():
                    if item.name == name and item.status not in {"closed", "killed"}:
                        raise ValueError(f"agent name '{name}' already exists")
            agent_id = uuid.uuid4().hex
            base_messages = []
            if fork_context and isinstance(current_messages_snapshot, list):
                for msg in current_messages_snapshot:
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("role") == "system":
                        continue
                    base_messages.append(_json_copy(msg, {}))
            base_messages.append(
                {
                    "id": uuid.uuid4().hex,
                    "role": "user",
                    "content": text,
                    "created_at": int(time.time()),
                }
            )
            now = int(time.time())
            run_context_copy = _json_copy(run_context, {})
            if not isinstance(run_context_copy, dict):
                run_context_copy = {}
            meta_copy = _json_copy(meta, {})
            if not isinstance(meta_copy, dict):
                meta_copy = {}
            record = AgentRuntimeRecord(
                agent_id=agent_id,
                conversation_id=self.conversation_id,
                parent_message_id=str(parent_message_id or ""),
                name=str(name or ""),
                status="queued",
                fork_context=bool(fork_context),
                created_at=now,
                updated_at=now,
                source_tool_call_id=str(source_tool_call_id or ""),
                run_context=run_context_copy,
                meta=meta_copy,
                messages=self.chat_storage.normalize_messages(
                    base_messages,
                    conversation_id=f"agent:{agent_id}",
                ),
            )
            self._agents[agent_id] = record
            self._persist_record_unlocked(record)
            self._emit_agent_state(
                record,
                "input",
                event_type="input",
                input_text=text,
                content=text,
                ts=record.created_at,
            )
            self._start_worker_unlocked(record)
            _log_agent_runtime(
                "spawn_agent_done",
                conversation_id=self.conversation_id,
                agent_id=record.agent_id,
                name=record.name,
                status=record.status,
            )
            return {
                "status": "spawned",
                "agent_id": record.agent_id,
                "name": record.name,
                "conversation_id": self.conversation_id,
            }

    def send_input(self, target, message):
        text = str(message or "").strip()
        if not text:
            raise ValueError("message is required")
        with self._lock:
            self._load_from_storage_unlocked()
            agent = self.chat_storage.resolve_agent_target(self.conversation_id, target)
            record = self._agents.get(agent["id"])
            _log_agent_runtime(
                "send_input_begin",
                conversation_id=self.conversation_id,
                agent_id=agent.get("id") or "",
                target=str(target or ""),
                message_len=len(text),
                known_record=bool(record),
            )
            if not record:
                record = AgentRuntimeRecord(
                    agent_id=agent["id"],
                    conversation_id=self.conversation_id,
                    parent_message_id=agent.get("parent_message_id") or "",
                    name=agent.get("name") or "",
                    status=agent.get("status") or "queued",
                    fork_context=bool(agent.get("fork_context")),
                    created_at=int(agent.get("created_at") or 0),
                    updated_at=int(agent.get("updated_at") or 0),
                    started_at=int(agent.get("started_at") or 0),
                    finished_at=int(agent.get("finished_at") or 0),
                    last_error=agent.get("last_error") or "",
                    last_result=agent.get("last_result") or "",
                    source_tool_call_id=str((agent.get("meta") or {}).get("source_tool_call_id") or ""),
                    run_context=_json_copy((agent.get("meta") or {}).get("run_context"), {}),
                    meta=_json_copy(agent.get("meta"), {}),
                    messages=self.chat_storage.get_agent_messages(agent["id"]),
                )
                self._agents[record.agent_id] = record

            if record.status in {"closed", "killed"}:
                raise ValueError("cannot send input to closed agent")

            record.pending_inputs.append(text)
            if record.worker is None:
                _log_agent_runtime(
                    "send_input_start_idle_agent",
                    conversation_id=self.conversation_id,
                    agent_id=record.agent_id,
                    pending_inputs=len(record.pending_inputs),
                )
                if record.pending_inputs:
                    next_input = record.pending_inputs.pop(0)
                    self._append_user_input_unlocked(record, next_input)
                self._start_worker_unlocked(record)
            else:
                record.status = "waiting_input"
                if record.worker_result_received or record.worker_cleanup_pending:
                    record.pending_restart = True
                self._persist_record_unlocked(record)
                self._emit_agent_state(record, "waiting_input")
                _log_agent_runtime(
                    "send_input_queued_running_agent",
                    conversation_id=self.conversation_id,
                    agent_id=record.agent_id,
                    pending_inputs=len(record.pending_inputs),
                    pending_restart=record.pending_restart,
                    cleanup_pending=record.worker_cleanup_pending,
                    result_received=record.worker_result_received,
                    **_debug_worker_summary(record.worker),
                )

            self._condition.notify_all()
            return {
                "status": "queued",
                "agent_id": record.agent_id,
                "pending_inputs": len(record.pending_inputs),
            }

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
            if status_filter:
                rows = self.chat_storage.list_agents(self.conversation_id, status_filter=status_filter)
                known_ids = {row["id"] for row in rows}
                for row in rows:
                    if row["id"] not in self._agents:
                        self._agents[row["id"]] = AgentRuntimeRecord(
                            agent_id=row["id"],
                            conversation_id=self.conversation_id,
                            parent_message_id=row.get("parent_message_id") or "",
                            name=row.get("name") or "",
                            status=row.get("status") or "queued",
                            fork_context=bool(row.get("fork_context")),
                            created_at=int(row.get("created_at") or 0),
                            updated_at=int(row.get("updated_at") or 0),
                            started_at=int(row.get("started_at") or 0),
                            finished_at=int(row.get("finished_at") or 0),
                            last_error=row.get("last_error") or "",
                            last_result=row.get("last_result") or "",
                        )
                summaries = [
                    self._build_summary(self._agents[agent_id])
                    for agent_id in known_ids
                    if agent_id in self._agents
                ]
                summaries.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
                return summaries
            summaries = [self._build_summary(item) for item in self._agents.values()]
            summaries.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
            return summaries


class AgentManagerRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._managers = {}

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
        with self._lock:
            manager = self._managers.get(key)
            if manager is None:
                _log_agent_runtime("registry_create_manager", conversation_id=key)
                manager = SessionAgentManager(
                    key,
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
