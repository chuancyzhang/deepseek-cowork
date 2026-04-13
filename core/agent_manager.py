import json
import threading
import time
import uuid
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, Signal
from core.chat_storage import AGENT_TERMINAL_STATUSES, ChatStorage

AGENT_MANAGEMENT_TOOLS = {
    "spawn_agent",
    "send_input",
    "wait_agent",
    "close_agent",
    "list_agents",
}

AGENT_LIVE_STATUSES = {"queued", "running", "waiting_input"}


class _ManagerEventBridge(QObject):
    step_message = Signal(str)
    agent_event = Signal(dict)

    def __init__(self, owner_worker=None):
        super().__init__()
        self.owner_worker = owner_worker
        if owner_worker is not None:
            try:
                self.step_message.connect(owner_worker.relay_agent_step, Qt.DirectConnection)
            except Exception:
                pass
            try:
                self.agent_event.connect(owner_worker.relay_agent_state, Qt.DirectConnection)
            except Exception:
                pass


def _json_copy(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


def _default_worker_factory(
    messages,
    config_manager,
    workspace_dir,
    agent_id,
    conversation_id,
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
    worker: object = None
    messages: list = field(default_factory=list)
    pending_inputs: list = field(default_factory=list)
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

    def _emit_step(self, text):
        if not self.step_signal:
            if self._event_bridge is None:
                return
        try:
            if self.step_signal:
                self.step_signal.emit(str(text))
                return
        except Exception:
            pass
        if self._event_bridge is not None:
            try:
                self._event_bridge.step_message.emit(str(text))
            except Exception:
                return

    def _emit_agent_state(self, record, status, **extra):
        payload = {
            "agent_id": record.agent_id,
            "agent_name": record.name or "",
            "status": status,
        }
        if record.source_tool_call_id:
            payload["tool_call_id"] = record.source_tool_call_id
        payload.update(extra or {})
        if not self.agent_state_signal:
            if self._event_bridge is None:
                return
        try:
            if self.agent_state_signal:
                self.agent_state_signal.emit(payload)
                return
        except Exception:
            pass
        if self._event_bridge is not None:
            try:
                self._event_bridge.agent_event.emit(payload)
            except Exception:
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
        for row in self.chat_storage.list_agents(self.conversation_id):
            status = row.get("status") or "queued"
            if status not in AGENT_TERMINAL_STATUSES:
                recovered = "failed_recovered" if status == "running" else "closed"
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
                messages=self.chat_storage.get_agent_messages(row.get("id") or ""),
            )
            if record.agent_id:
                self._agents[record.agent_id] = record

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
            "has_pending_input": bool(record.pending_inputs),
        }

    def _live_count_unlocked(self):
        return len([item for item in self._agents.values() if item.is_live()])

    def _connect_worker_signals(self, record):
        worker = record.worker
        if worker is None:
            return
        agent_id = record.agent_id

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
            if isinstance(payload, dict):
                tool_name = str(payload.get("name") or "")
            with self._lock:
                latest = self._agents.get(agent_id)
                if not latest:
                    return
                self._emit_agent_state(latest, "tool_use", task=f"Tool: {tool_name or 'unknown'}")

        def _on_finished(result):
            self._on_worker_finished(agent_id, result if isinstance(result, dict) else {})

        if getattr(worker, "step_signal", None):
            worker.step_signal.connect(_on_step)
        if getattr(worker, "thinking_signal", None):
            worker.thinking_signal.connect(_on_thinking)
        if getattr(worker, "content_signal", None):
            worker.content_signal.connect(_on_content)
        if getattr(worker, "output_signal", None):
            worker.output_signal.connect(_on_output)
        if getattr(worker, "tool_call_signal", None):
            worker.tool_call_signal.connect(_on_tool_call)
        if getattr(worker, "finished_signal", None):
            worker.finished_signal.connect(_on_finished)

    def _persist_record_unlocked(self, record):
        now = int(time.time())
        record.updated_at = now
        meta = {}
        if record.source_tool_call_id:
            meta["source_tool_call_id"] = record.source_tool_call_id
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
                    return
            except Exception:
                pass
        record.status = "running"
        if not record.started_at:
            record.started_at = int(time.time())
        record.finished_at = 0
        self._persist_record_unlocked(record)
        worker = self.worker_factory(
            _json_copy(record.messages, []),
            self.config_manager,
            self.workspace_dir,
            record.agent_id,
            self.conversation_id,
        )
        record.worker = worker
        self._connect_worker_signals(record)
        self._emit_agent_state(record, "pending", task="Starting task")
        self._emit_agent_state(record, "running")
        worker.start()

    def _append_user_input_unlocked(self, record, message):
        msg = {
            "id": uuid.uuid4().hex,
            "role": "user",
            "content": message,
            "created_at": int(time.time()),
        }
        record.messages.append(msg)
        record.messages = self.chat_storage.normalize_messages(record.messages)
        self.chat_storage.append_agent_messages(record.agent_id, [msg])

    def _on_worker_finished(self, agent_id, result):
        with self._lock:
            record = self._agents.get(agent_id)
            if not record:
                self._condition.notify_all()
                return

            record.worker = None
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
            record.messages = self.chat_storage.normalize_messages(record.messages)
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
            if record.last_error:
                payload["error"] = record.last_error
            self._emit_agent_state(record, record.status, **payload)
            self._condition.notify_all()

    def spawn_agent(
        self,
        message,
        name=None,
        fork_context=False,
        current_messages_snapshot=None,
        parent_message_id=None,
        source_tool_call_id=None,
    ):
        text = str(message or "").strip()
        if not text:
            raise ValueError("message is required")
        with self._lock:
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
                messages=self.chat_storage.normalize_messages(base_messages),
            )
            self._agents[agent_id] = record
            self._persist_record_unlocked(record)
            self._start_worker_unlocked(record)
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
                    messages=self.chat_storage.get_agent_messages(agent["id"]),
                )
                self._agents[record.agent_id] = record

            if record.status in {"closed", "killed"}:
                raise ValueError("cannot send input to closed agent")

            record.pending_inputs.append(text)
            if record.worker is None:
                if record.pending_inputs:
                    next_input = record.pending_inputs.pop(0)
                    self._append_user_input_unlocked(record, next_input)
                self._start_worker_unlocked(record)
            else:
                record.status = "waiting_input"
                self._persist_record_unlocked(record)
                self._emit_agent_state(record, "waiting_input")

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
            return {
                "completed": completed,
                "pending": pending,
                "timed_out": timed_out,
            }

    def close_agent(self, target, force=False):
        with self._lock:
            self._load_from_storage_unlocked()
            resolved = self.chat_storage.resolve_agent_target(self.conversation_id, target)
            record = self._agents.get(resolved["id"])
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

            worker = record.worker
            if worker is not None:
                record.closing_requested = True
                record.force_close = bool(force)
                try:
                    worker.stop()
                except Exception:
                    pass
                try:
                    worker.quit()
                    worker.wait(300)
                except Exception:
                    pass
                if force:
                    try:
                        if worker.isRunning():
                            worker.terminate()
                            worker.wait(300)
                    except Exception:
                        pass
                if not worker.isRunning():
                    record.worker = None
                    record.status = "killed" if force else "closed"
                    record.finished_at = int(time.time())
                    self._persist_record_unlocked(record)
            else:
                record.status = "closed"
                record.finished_at = int(time.time())
                self._persist_record_unlocked(record)
            self._emit_agent_state(record, record.status)
            self._condition.notify_all()
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
