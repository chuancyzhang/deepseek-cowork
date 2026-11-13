import copy
import json
import os
import threading
import time
import uuid
import hashlib
import logging
from functools import wraps

runtime_log = logging.getLogger("runtime.coordinator")
from core.agent import LLMWorker
from core.agent_manager import AGENT_LIVE_STATUSES, get_agent_manager_registry
from core.chat_storage import ChatStorage, ConversationWriteConflict
from core.chat_save_queue import classify_chat_save_error
from core.config_manager import ConfigManager
from core.env_utils import get_app_data_dir, get_base_dir
from core.runtime_logs import append_log
from core.im_session_key import parse_im_session_key
from core.interaction import interaction_service
from core.clarify_mode import RUN_MODE_EXECUTION, normalize_run_context
from core.llm.deepseek import DEEPSEEK_V4_CONTEXT_WINDOW_TOKENS, is_deepseek_v4_model
from core.llm.providers import GPT_5_6_CONTEXT_WINDOW_TOKENS, is_gpt_5_6_model
from core.message_persistence import filter_persistable_messages
from core.runtime_journal import RUN_TERMINAL_STATUSES, RuntimeJournal, RuntimeJournalError
from core.runtime_stream import RuntimeStream
from core.conversation_integrity import merge_messages_by_id
from core.skill_catalog import DependencyCoordinator, SkillCatalogService, SkillChangeEvent


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 23333
def get_runtime_signature():
    try:
        if getattr(__import__("sys"), "frozen", False):
            exe_path = os.path.abspath(__import__("sys").executable)
            stat = os.stat(exe_path)
            payload = f"frozen|{exe_path}|{int(stat.st_mtime)}|{stat.st_size}"
            return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
        base_dir = get_base_dir()
        candidates = [
            os.path.join(base_dir, "main.py"),
            os.path.join(base_dir, "core", "agent.py"),
            os.path.join(base_dir, "core", "daemon.py"),
            os.path.join(base_dir, "core", "runtime_coordinator.py"),
            os.path.join(base_dir, "core", "runtime_ledger.py"),
            os.path.join(base_dir, "core", "agent_manager.py"),
            os.path.join(base_dir, "core", "agent_inbox.py"),
            os.path.join(base_dir, "core", "subagent_control.py"),
            os.path.join(base_dir, "core", "clarify_mode.py"),
            os.path.join(base_dir, "skills", "file-system", "impl.py"),
        ]
        parts = []
        for path in candidates:
            if not os.path.isfile(path):
                continue
            stat = os.stat(path)
            parts.append(f"{os.path.relpath(path, base_dir)}|{int(stat.st_mtime)}|{stat.st_size}")
        payload = "\n".join(parts) if parts else str(time.time())
        return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
    except Exception:
        return "unknown"

def _log_daemon(message):
    try:
        append_log("daemon", message, get_app_data_dir())
    except Exception:
        try:
            print(f"[daemon] {message}")
        except Exception:
            return


def _runtime_journal_error_details(journal, operation, exc):
    return (
        f"operation={operation} path={getattr(journal, 'root', '')} "
        f"errno={getattr(exc, 'errno', None)} "
        f"winerror={getattr(exc, 'winerror', None)} error={exc}"
    )


def _normalize_worker_error(result):
    if not isinstance(result, dict) or "error" not in result:
        return result
    error_type = str(result.get("error_type") or "UnknownError").strip() or "UnknownError"
    result["error_type"] = error_type
    result["error"] = str(result.get("error") or "").strip() or f"{error_type}: no message"
    return result


def _compute_session_title(messages):
    title = "新对话"
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content") or ""
            if content:
                title = content[:15] + "..." if len(content) > 15 else content
            break
    return title


def _session_command(method):
    """Serialize lifecycle commands, never model/network work or cancellation."""
    @wraps(method)
    def serialized(self, session_id, *args, **kwargs):
        with self.lock:
            if not hasattr(self, "command_locks"):
                self.command_locks = {}
            lock = self.command_locks.setdefault(session_id, threading.RLock())
        with lock:
            return method(self, session_id, *args, **kwargs)
    return serialized


class RuntimeCoordinator:
    def __init__(self, config_manager, *, storage=None, journal=None):
        self.config_manager = config_manager
        history_dir = config_manager.get_chat_history_dir()
        db_path = os.path.join(history_dir, "chat_history.sqlite")
        self.chat_storage = storage if storage is not None else ChatStorage(db_path)
        self.runtime_journal = journal if journal is not None else RuntimeJournal(history_dir)
        config_manager.runtime_journal = self.runtime_journal
        config_manager.runtime_storage = self.chat_storage
        self.chat_storage.runtime_journal = self.runtime_journal
        self.shutdown_requested = False
        self.sessions = {}
        self.streams = {}
        self.active_workers = {}
        self.cancelled_runs = set()
        self.accepted_runs = {}
        self.detached_workers = {}
        self.update_preparing = False
        self.update_requests = {}
        self.update_errors = {}
        self.lock = threading.RLock()
        self.daemon_instance_id = uuid.uuid4().hex
        self.suspended = False
        self.last_activity = time.time()
        idle_minutes = config_manager.get("daemon_idle_minutes", 10)
        self.idle_timeout = max(int(idle_minutes), 1) * 60
        self.dependency_coordinator = DependencyCoordinator(config_manager, logger=_log_daemon)
        self.skill_catalog = SkillCatalogService(
            config_manager,
            logger=_log_daemon,
            dependency_coordinator=self.dependency_coordinator,
        )
        self.skill_catalog_error = ""
        try:
            self.skill_catalog.preload()
            self.skill_catalog.start_watching()
        except Exception as exc:
            self.skill_catalog_error = str(exc)
            _log_daemon(f"skill_catalog preload_failed error={exc}")

    def touch(self):
        self.last_activity = time.time()
        if self.suspended:
            self.suspended = False

    def accept(self, payload):
        cid, rid = payload["conversation_id"], payload["request_id"]
        return self._accept(cid, rid, payload)

    @_session_command
    def _accept(self, cid, rid, payload):
        with self.runtime_journal.session_lock(cid):
            if self.update_preparing or self.shutdown_requested:
                raise RuntimeError("后台正在结束，输入内容已保留。")
            run, created = self.desktop_store.accept(payload)
            if created:
                self.ensure_runtime_run(cid, rid, rid, f"desktop:{os.getpid()}", self.chat_storage.get_messages(cid))
                self.runtime_journal.update_run(cid, rid, {"status": "queued"})
        with self.lock:
            self.accepted_runs[rid] = cid
        return run, created

    def owns_run(self, session_id, run_id):
        with self.lock:
            return self.accepted_runs.get(run_id) == session_id

    def cancel_tokens(self, session_id, run_id):
        """Priority control path: no database, queue or disk wait."""
        with self.lock:
            if self.accepted_runs.get(run_id) != session_id:
                return False
            self.cancelled_runs.add((session_id, run_id))
            active = self.active_workers.get(session_id)
            worker = active.get("worker") if active and active.get("run_id") == run_id else None
        if worker:
            worker.execution_authorization.cancel()
            pipeline = getattr(worker, "_runtime_stream_pipeline", None)
            if pipeline:
                pipeline.stop_accepting()
        return True

    def run_status(self, session_id, run_id, status, error=""):
        with self.runtime_journal.session_lock(session_id):
            run = self.runtime_journal.update_run(session_id, run_id, {"status": status, "terminal_error": str(error)})
            if hasattr(self, "desktop_store"):
                self.desktop_store.set_run(run_id, run["status"], run.get("terminal_error", ""))
            return run

    def recover_runs(self):
        pending = {(row["conversation_id"], row["id"]) for row in self.desktop_store.active_runs()}
        manifests, errors = self.runtime_journal.scan_manifests()
        if errors:
            raise RuntimeJournalError("运行索引恢复失败：" + str(errors))
        pending.update((item["session_id"], item["active_run_id"]) for item in manifests if item.get("active_run_id"))
        for cid, rid in pending:
            with self.runtime_journal.session_lock(cid):
                run = self.runtime_journal.get_run(cid, rid)
                if run is None:
                    self.ensure_runtime_run(cid, rid, rid, f"desktop:{os.getpid()}", self.chat_storage.get_messages(cid))
                    run = self.runtime_journal.get_run(cid, rid)
                terminal = run["status"] if run["status"] in RUN_TERMINAL_STATUSES else "interrupted"
                self.run_status(cid, rid, terminal, run.get("terminal_error") or "应用上次退出时任务尚未结束，已保存的消息和输出仍可查看。")

    @_session_command
    def finalize(self, session_id, run_id, turn_id, messages, result, *, ui_owned=False, execution_policy=None):
        """One logical commit for final history, lifecycle, receipt and output."""
        result = _normalize_worker_error(dict(result))
        with self.runtime_journal.session_lock(session_id):
            latest = self.runtime_journal.get_run(session_id, run_id) or {}
            with self.lock:
                cancelled = (session_id, run_id) in self.cancelled_runs
            if latest.get("stop_requested") or cancelled:
                result["error"] = "Run interrupted by user."
            terminal = "interrupted" if latest.get("stop_requested") or cancelled else "failed" if result.get("error") else "completed"
            # save_session normalizes into a new list. Use the canonical session
            # view, including guidance persisted while this worker was running.
            with self.lock:
                messages[:] = merge_messages_by_id(self.sessions.get(session_id, []), messages)
            self.append_worker_result_messages(session_id, messages, result, source="runtime_finalize")
            messages[:] = self._normalize_persistable_messages(session_id, messages, source="runtime_finalize")
            with self.lock:
                self.sessions[session_id] = messages
            if self.update_preparing and terminal == "interrupted" and not ui_owned:
                self.preserve_update_fragments(session_id, run_id, turn_id, messages)
            if not ui_owned and not self.save_session(session_id, run_id, acknowledge=False):
                raise RuntimeJournalError("对话保存冲突，已持久化的输出保留，请重新打开对话核对。")
            result["_runtime_terminal"] = terminal
            self.run_status(session_id, run_id, terminal, result.get("error", ""))
            self.runtime_journal.update_run(session_id, run_id, {
                "final_result": result, "postprocess_status": "completed", "commit_status": "pending" if ui_owned else "completed"})
            self.runtime_journal.append_event(session_id, run_id, "terminal", {"status": terminal, "error": result.get("error", ""), "source": "runtime"})
            if not ui_owned:
                self.runtime_journal.acknowledge_commit(session_id, run_id, messages)
            payload = {"type": "final", "result": result, "session_id": session_id, "run_id": run_id,
                       "created_at": time.time()}
            if execution_policy is not None:
                payload["execution_policy"] = execution_policy
            event = self.runtime_journal.append_event(session_id, run_id, "final", payload)
        self.clear_active_worker(session_id, expected_run_id=run_id)
        return payload, event["sequence"]

    @_session_command
    def prepare_stream(self, session_id, run_id, turn_id, writer_owner, content, message_id, snapshot):
        """Durably accept input and claim execution once for every entry point."""
        with self.runtime_journal.session_lock(session_id):
            existing = self.runtime_journal.get_run(session_id, run_id)
            if existing and (existing.get("execution_claimed") or existing["status"] in RUN_TERMINAL_STATUSES):
                return None
            baseline = self.chat_storage.get_messages(session_id) if self.chat_storage.has_conversation(session_id) else []
            # Validate the active run before replacing the session's memory view.
            self.ensure_runtime_run(session_id, run_id, turn_id, writer_owner, baseline)
            messages = self.request_messages(session_id, snapshot)
            self.append_user_message_if_needed(messages, content, message_id=message_id,
                                               turn_id=turn_id, request_id=run_id)
            if not writer_owner.startswith("ui:") and not self.save_session(session_id, acknowledge=False):
                raise RuntimeJournalError("输入保存失败，运行未启动。")
            self.runtime_journal.update_run(session_id, run_id, {"execution_claimed": True})
        with self.lock:
            self.accepted_runs[run_id] = session_id
        return messages

    def replay_stream(self, session_id, run_id, send):
        """A repeated request subscribes to durable output; it never runs tools."""
        cursor = 0
        while True:
            run = self.runtime_journal.get_run(session_id, run_id)
            for event in self.runtime_journal.read_events(session_id, run_id, starting_after=cursor):
                cursor = event["sequence"]
                payload = event.get("payload") or {}
                if payload.get("type"):
                    send({**payload, "session_id": session_id, "run_id": run_id, "sequence": cursor})
                    if payload["type"] == "final":
                        return
            if run["status"] in RUN_TERMINAL_STATUSES:
                send({"type": "final", "session_id": session_id, "run_id": run_id,
                      "result": run.get("final_result") or {"error": run.get("terminal_error") or "运行已结束。"}})
                return
            time.sleep(.016)

    def prepare_update_shutdown(self):
        """Quiesce accepted requests, including sessions with no attached UI."""
        deadline = time.monotonic() + 30
        with self.lock:
            self.update_preparing = True
            self.update_errors.clear()
            sessions = set(self.active_workers)
            sessions.update(item.get("session_id") for item in self.detached_workers.values())
            sessions.update(item[0] for item in self.update_requests.values())
        try:
            for session_id in sessions - {None, ""}:
                result = self.stop_session(session_id, wait_for_release=True, release_timeout_ms=max(0, int((deadline-time.monotonic())*1000)))
                if not result["released"]:
                    raise RuntimeError("后台任务尚未完成停止和保存。")
            while True:
                with self.lock:
                    pending = bool(self.update_requests)
                if not pending:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("后台会话仍在保存，请稍后重试。")
                time.sleep(0.05)
            get_agent_manager_registry().stop_for_update(self.chat_storage.db_path, timeout=max(0, deadline-time.monotonic()))
            if self.update_errors:
                raise RuntimeError("后台内容保存失败：" + "; ".join(self.update_errors.values()))
            _log_daemon("update_prepare_saved")
            return {"status": "ok", "saved": True, "pid": os.getpid()}
        except Exception as exc:
            with self.lock:
                self.update_preparing = False
            _log_daemon(f"update_prepare_failed error={exc}")
            return {"status": "error", "saved": False, "error": str(exc)}

    def ensure_runtime_run(self, session_id, run_id, turn_id, writer_owner, base_messages):
        existing = self.runtime_journal.get_run(session_id, run_id)
        if existing is not None:
            existing_owner = str(existing.get("writer_owner") or "")
            if existing_owner and existing_owner != str(writer_owner or ""):
                raise RuntimeError(
                    f"runtime writer owner mismatch for {session_id}: "
                    f"{existing_owner} != {writer_owner}"
                )
            return self.runtime_journal.update_run(
                session_id,
                run_id,
                {
                    "execution_backend": "daemon",
                    "daemon_instance_id": self.daemon_instance_id,
                    "daemon_pid": os.getpid(),
                },
            )
        run = self.runtime_journal.begin_run(
            session_id,
            run_id,
            turn_id=turn_id,
            writer_owner=writer_owner,
            base_messages=base_messages,
            extra={
                "execution_backend": "daemon",
                "daemon_instance_id": self.daemon_instance_id,
                "daemon_pid": os.getpid(),
            },
        )
        self.runtime_journal.append_event(
            session_id,
            run_id,
            "run_started",
            {
                "turn_id": str(turn_id or ""),
                "writer_owner": str(writer_owner or ""),
                "snapshot_hash": RuntimeJournal.messages_hash(base_messages),
            },
        )
        return run

    def maybe_suspend(self):
        if self.suspended:
            return
        if time.time() - self.last_activity < self.idle_timeout:
            return
        with self.lock:
            if self.active_workers or self.detached_workers or get_agent_manager_registry().active_count(self.chat_storage.db_path):
                return
            self.sessions = {}
            self.suspended = True

    def get_session_messages(self, session_id):
        with self.lock:
            if session_id in self.sessions:
                messages = self.chat_storage.normalize_messages(
                    self.sessions[session_id],
                    conversation_id=session_id,
                )
                self.sessions[session_id] = messages
                self._log_context_source(session_id, "daemon_memory", messages)
                return messages
        if self.chat_storage.has_conversation(session_id):
            messages = self.chat_storage.get_messages(session_id)
            source = "sqlite"
        else:
            messages = []
            source = "empty"
        messages = self.chat_storage.normalize_messages(messages, conversation_id=session_id)
        with self.lock:
            self.sessions[session_id] = messages
        self._log_context_source(session_id, source, messages)
        return messages

    def use_session_messages_snapshot(self, session_id, messages):
        if not isinstance(messages, list):
            return None
        normalized = self.chat_storage.normalize_messages(messages, conversation_id=session_id)
        with self.lock:
            self.sessions[session_id] = normalized
        self._log_context_source(session_id, "ui_snapshot", normalized)
        return normalized

    def request_messages(self, session_id, snapshot=None):
        messages = self.use_session_messages_snapshot(session_id, snapshot)
        if messages is not None:
            return messages
        return self.get_session_messages(session_id)

    def _log_context_source(self, session_id, source, messages):
        try:
            last = messages[-1] if messages else {}
            _log_daemon(
                "context_source "
                + json.dumps(
                    {
                        "session_id": session_id,
                        "source": source,
                        "message_count": len(messages or []),
                        "last_role": last.get("role") if isinstance(last, dict) else "",
                    },
                    ensure_ascii=False,
                )
            )
        except Exception:
            pass

    def append_user_message_if_needed(
        self,
        messages,
        content,
        message_id="",
        turn_id="",
        request_id="",
    ):
        if not isinstance(messages, list):
            return
        text = (content or "").strip()
        if not text:
            return
        stable_id = str(message_id or "").strip()
        if stable_id:
            for existing in messages:
                if not isinstance(existing, dict) or str(existing.get("id") or "") != stable_id:
                    continue
                if (
                    existing.get("role") != "user"
                    or (existing.get("content") or "").strip() != text
                ):
                    raise ValueError(f"用户消息 ID 冲突：{stable_id}")
                return
        else:
            # A caller without an ID is an older boundary and must receive a
            # fresh identity here; text equality is never a ledger idempotency
            # rule.  Current UI/daemon submissions always provide the ID from
            # the submit entry point.
            stable_id = uuid.uuid4().hex
        meta = {}
        if turn_id not in (None, ""):
            meta["turn_id"] = str(turn_id)
        if request_id:
            meta["request_id"] = str(request_id)
        sequences = []
        for existing in messages:
            existing_meta = existing.get("meta") if isinstance(existing, dict) else None
            if not isinstance(existing_meta, dict):
                continue
            try:
                existing_sequence = int(existing_meta.get("sequence"))
            except (TypeError, ValueError):
                continue
            if existing_sequence >= 0:
                sequences.append(existing_sequence)
        meta["sequence"] = max(sequences, default=len(messages) - 1) + 1
        message = {
            "id": stable_id,
            "role": "user",
            "content": content,
            "created_at": int(time.time()),
        }
        message["meta"] = meta
        messages.append(message)

    def _normalize_persistable_messages(self, session_id, messages, source):
        source_messages = messages if isinstance(messages, list) else []
        persistable_messages = filter_persistable_messages(source_messages)
        normalized_messages = self.chat_storage.normalize_messages(
            persistable_messages,
            conversation_id=session_id,
        )
        filtered_count = len(source_messages) - len(persistable_messages)
        if filtered_count:
            _log_daemon(
                "message_persistence_filter "
                + json.dumps(
                    {
                        "session_id": session_id,
                        "source": source,
                        "input_message_count": len(source_messages),
                        "persisted_message_count": len(normalized_messages),
                        "filtered_message_count": filtered_count,
                    },
                    ensure_ascii=False,
                )
            )
        return normalized_messages

    def append_worker_result_messages(self, session_id, messages, result, source):
        generated_messages = result.get("generated_messages", [])
        if not isinstance(generated_messages, list):
            generated_messages = []
        persisted_count = 0
        if generated_messages:
            persistable_messages = filter_persistable_messages(generated_messages)
            persisted_count = len(persistable_messages)
            filtered_count = len(generated_messages) - len(persistable_messages)
            if filtered_count:
                _log_daemon(
                    "message_persistence_filter "
                    + json.dumps(
                        {
                            "session_id": session_id,
                            "source": source,
                            "input_message_count": len(generated_messages),
                            "persisted_message_count": len(persistable_messages),
                            "filtered_message_count": filtered_count,
                        },
                        ensure_ascii=False,
                    )
                )
            messages[:] = merge_messages_by_id(messages, persistable_messages)
        if "error" in result:
            _log_daemon(
                "provider_error_with_ledger_append "
                + json.dumps(
                    {
                        "session_id": session_id,
                        "source": source,
                        "error": str(result.get("error") or ""),
                        "error_type": str(result.get("error_type") or ""),
                        "generated_message_count": len(generated_messages),
                        "persisted_message_count": persisted_count,
                    },
                    ensure_ascii=False,
                )
            )
            return
        if generated_messages:
            return
        fallback = {
            "id": str(result.get("message_id") or uuid.uuid4().hex),
            "role": result.get("role", "assistant"),
            "content": result.get("content", ""),
            "reasoning": result.get("reasoning", ""),
        }
        fallback_meta = {}
        if result.get("turn_id") not in (None, ""):
            fallback_meta["turn_id"] = str(result.get("turn_id"))
        if result.get("request_id") not in (None, ""):
            fallback_meta["request_id"] = str(result.get("request_id"))
        if fallback_meta:
            fallback["meta"] = fallback_meta
        messages[:] = merge_messages_by_id(messages, [fallback])

    def save_session(self, session_id, run_id="", *, acknowledge=True):
        with self.lock:
            if session_id not in self.sessions:
                _log_daemon(
                    f"daemon_save_session skipped_missing_session session_id={session_id}"
                )
                return False
            messages = self._normalize_persistable_messages(
                session_id,
                self.sessions[session_id],
                source="daemon_save_session",
            )
            self.sessions[session_id] = messages
        title = _compute_session_title(messages)
        try:
            result = self.chat_storage.save_conversation_safely(
                session_id,
                messages,
                title=title,
            )
        except ConversationWriteConflict as exc:
            _log_daemon(
                "daemon_save_session conflict "
                f"session_id={session_id} run_id={run_id} error={exc}"
            )
            if run_id:
                conflict_payload = {
                    "error": str(exc),
                    "snapshot_hash": RuntimeJournal.messages_hash(messages),
                    "recovery": "重新打开会话并从当前历史创建新分支。",
                }
                self.runtime_journal.append_event(
                    session_id,
                    run_id,
                    "sqlite_conflict",
                    conflict_payload,
                )
                self.runtime_journal.update_run(
                    session_id,
                    run_id,
                    {
                        "commit_status": "conflict",
                        "commit_error": str(exc),
                        "conflicting_snapshot_hash": conflict_payload["snapshot_hash"],
                    },
                )
            return False
        _log_daemon(
            "daemon_save_session committed "
            f"session_id={session_id} outcome={result.get('outcome')} "
            f"message_count={result.get('message_count')}"
        )
        if run_id and acknowledge:
            self.runtime_journal.acknowledge_commit(session_id, run_id, messages)
        return True

    def preserve_update_fragments(self, session_id, run_id, turn_id, messages):
        """Preserve stopped background output before stream acknowledgement prunes it.

        Display anchors are excluded by the existing provider projection. They must
        not fabricate a completed assistant message or change the request prefix.
        """
        fragments = {}
        for event in self.runtime_journal.read_events(session_id, run_id):
            payload = event.get("payload") or {}
            kind = payload.get("type")
            if kind not in {"content", "content_snapshot", "thinking"}:
                continue
            source = str(payload.get("source_message_id") or "")
            fragment = fragments.setdefault(source, {"content": "", "reasoning_content": ""})
            if kind == "content_snapshot":
                fragment["content"] = str(payload.get("content") or "")
            else:
                key = "reasoning_content" if kind == "thinking" else "content"
                fragment[key] += str(payload.get("delta") or "")
        canonical_ids = {str(message.get("id") or "") for message in messages}
        additions = []
        for source, fragment in fragments.items():
            if (source and source in canonical_ids) or not any(fragment.values()):
                continue
            identity = "update-partial-" + hashlib.sha256((run_id + ":" + source).encode()).hexdigest()[:24]
            additions.append({"id": identity, "role": "assistant", **fragment, "created_at": int(time.time()),
                              "meta": {"turn_id": str(turn_id), "request_id": run_id, "ui_turn_id": str(turn_id),
                                       "ui_reply_kind": "interrupted", "context_visible_interruption": True,
                                       "visible_interruption_outcome": "interrupted", "ui_visible_fragment": True,
                                       "ui_display_anchor_only": True, "ui_source_message_id": source}})
        messages[:] = merge_messages_by_id(messages, additions)
        _log_daemon(f"update_partial_preserved session_id={session_id} run_id={run_id} fragments={len(additions)}")
    
    def set_active_worker(
        self,
        session_id,
        worker,
        turn_id=None,
        run_id=None,
        writer_owner=None,
    ):
        resolved_run_id = str(run_id or getattr(worker, "request_id", "") or "")
        resolved_writer_owner = str(writer_owner or "").strip()
        if not resolved_writer_owner and resolved_run_id:
            run_record = self.runtime_journal.get_run(session_id, resolved_run_id)
            resolved_writer_owner = str((run_record or {}).get("writer_owner") or "").strip()
        if not resolved_writer_owner:
            resolved_writer_owner = f"daemon:{os.getpid()}"
        run_record = self.runtime_journal.get_run(session_id, resolved_run_id) if resolved_run_id else None
        with self.lock:
            if self.update_preparing or (session_id, resolved_run_id) in self.cancelled_runs:
                return False
            if run_record and (run_record.get("stop_requested") or run_record["status"] in RUN_TERMINAL_STATUSES):
                return False
            active = self.active_workers.get(session_id)
            if active and active.get("worker") is not worker:
                raise RuntimeError("当前对话已有活动运行。")
            self.active_workers[session_id] = {
                "worker": worker,
                "turn_id": str(turn_id or getattr(worker, "turn_id", "") or ""),
                "run_id": resolved_run_id,
                "writer_owner": resolved_writer_owner,
            }
            return True
    
    def clear_active_worker(self, session_id, *, expected_worker=None, expected_run_id=""):
        with self.lock:
            active = self.active_workers.get(session_id)
            if not active:
                return False
            active_worker = active.get("worker") if isinstance(active, dict) else active
            active_run_id = str(
                (active.get("run_id") if isinstance(active, dict) else "")
                or getattr(active_worker, "request_id", "")
                or ""
            )
            if expected_worker is not None and active_worker is not expected_worker:
                return False
            if expected_run_id and active_run_id != str(expected_run_id):
                return False
            del self.active_workers[session_id]
            return True

    def detach_worker_until_finished(self, session_id, worker, reason=""):
        if not worker:
            return False
        key = id(worker)

        def cleanup():
            with self.lock:
                self.detached_workers.pop(key, None)
            _log_daemon(
                f"detached worker finished session_id={session_id} "
                f"worker_id={hex(key)} reason={reason}"
            )

        with self.lock:
            self.detached_workers[key] = {
                "session_id": session_id,
                "worker": worker,
                "run_id": str(getattr(worker, "request_id", "") or ""),
                "reason": reason,
                "created_at": time.time(),
            }
        try:
            worker.finished.subscribe(cleanup)
        except Exception as e:
            _log_daemon(f"detach worker connect finished failed session_id={session_id} error={e}")
        try:
            is_running = bool(worker.is_running())
        except Exception:
            is_running = True
        if not is_running:
            cleanup()
            return False
        _log_daemon(
            f"detached running worker session_id={session_id} "
            f"worker_id={hex(key)} reason={reason}"
        )
        return True
    
    def stop_session(
        self,
        session_id,
        expected_run_id="",
        *,
        wait_for_release=False,
        release_timeout_ms=5000,
    ):
        expected_run_id = str(expected_run_id or "").strip()
        interrupted_run_ids = set()
        with self.lock:
            active = self.active_workers.get(session_id)
            worker = active.get("worker") if isinstance(active, dict) else active
            active_run_id = str(
                (active.get("run_id") if isinstance(active, dict) else "")
                or getattr(worker, "request_id", "")
                or ""
            )
            detached_entries = [
                item
                for item in self.detached_workers.values()
                if item.get("session_id") == session_id
                and (
                    not expected_run_id
                    or str(
                        item.get("run_id")
                        or getattr(item.get("worker"), "request_id", "")
                        or ""
                    ) == expected_run_id
                )
            ]
            if expected_run_id and active_run_id != expected_run_id:
                worker = None
                active_run_id = ""
            detached = [item.get("worker") for item in detached_entries]
            run_ids = {
                run_id
                for run_id in [
                    active_run_id,
                    *[
                        str(
                            item.get("run_id")
                            or getattr(item.get("worker"), "request_id", "")
                            or ""
                        )
                        for item in detached_entries
                    ],
                ]
                if run_id
            }
            if expected_run_id:
                run_ids.add(expected_run_id)
            self.cancelled_runs.update((session_id, run_id) for run_id in run_ids)
        save_errors = []
        # Release stream backpressure and stop execution before waiting on
        # disk. The journal transition below still fences registration.
        for target_worker in [worker, *detached]:
            if target_worker is None:
                continue
            pipeline = getattr(target_worker, "_runtime_stream_pipeline", None)
            if pipeline is not None:
                pipeline.stop_accepting()
            try:
                target_worker.stop()
            except Exception as exc:
                _log_daemon(f"stop_session worker.stop failed session_id={session_id} error={exc}")
        matched_execution = bool(worker or detached)
        if matched_execution or not expected_run_id:
            interaction_service.cancel_session_requests(session_id, reason="cancelled")
            self._close_live_subagents(session_id, force=True)
        # Keep the journal transition and worker registration mutually exclusive.
        # Otherwise a stop can observe no worker, then a worker can register and
        # start after the journal has already been marked interrupted.
        for run_id in run_ids:
            try:
                with self.runtime_journal.session_lock(session_id):
                    record = self.runtime_journal.get_run(session_id, run_id)
                    if record and record["status"] not in RUN_TERMINAL_STATUSES:
                        record = self.runtime_journal.update_run(session_id, run_id,
                            {"status": "stopping", "stop_requested": True, "terminal_error": "interrupted by user"})
                        if hasattr(self, "desktop_store"):
                            self.desktop_store.set_run(run_id, "stopping", "")
                if record and record.get("stop_requested"):
                    interrupted_run_ids.add(run_id)
            except Exception as exc:
                save_errors.append(str(exc))
                _log_daemon(
                    f"stop_session runtime interrupt failed session_id={session_id} "
                    f"run_id={run_id} error={exc}"
                )
        stopped = bool(matched_execution or interrupted_run_ids)
        release_targets = []
        seen_worker_ids = set()
        for candidate in [worker, *detached]:
            if candidate is None or id(candidate) in seen_worker_ids:
                continue
            seen_worker_ids.add(id(candidate))
            release_targets.append(candidate)
        released = True
        if wait_for_release and release_targets:
            deadline = time.monotonic() + max(0.0, int(release_timeout_ms or 0) / 1000.0)
            for candidate in release_targets:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                if remaining_ms <= 0 or not candidate.join(remaining_ms / 1000.0):
                    released = False
                    break
                # The model worker can exit before a background tool finishes
                # its process-tree cleanup. Resource release includes both.
                manager = getattr(candidate, "tool_tasks", None)
                for task in list(manager.tasks.values()) if manager is not None else []:
                    remaining = max(0.0, deadline - time.monotonic())
                    if not task.done.wait(remaining):
                        released = False
                        break
        _log_daemon(
            "stop_session handled "
            f"session_id={session_id} expected_run_id={expected_run_id or '-'} "
            f"matched_worker={bool(worker)} detached_workers={len(detached)} "
            f"interrupted_runs={sorted(interrupted_run_ids)} stopped={stopped} "
            f"wait_for_release={bool(wait_for_release)} released={released}"
        )
        if wait_for_release:
            return {
                "stopped": stopped,
                "released": released,
                "release_timeout_ms": int(release_timeout_ms or 0),
                "save_error": "; ".join(save_errors),
            }
        return stopped

    @_session_command
    def steer_session(self, session_id, expected_turn_id, message):
        expected = str(expected_turn_id or "")
        if not isinstance(message, dict):
            return {"accepted": False, "error": "invalid_message", "turn_id": expected}
        message = copy.deepcopy(message)
        message["id"] = str(message.get("id") or uuid.uuid4().hex)
        run = self.runtime_journal.get_run(session_id, expected) or {}
        receipts = run.get("guidance_receipts") or {}
        signature = hashlib.sha256(json.dumps(message, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        previous = receipts.get(message["id"])
        if previous:
            if previous["signature"] != signature:
                raise ValueError("补充消息标识已用于其他内容。")
            return previous["result"]
        with self.lock:
            active = self.active_workers.get(session_id)
            if isinstance(active, dict):
                worker = active.get("worker")
                active_turn_id = str(active.get("turn_id") or "")
                writer_owner = str(active.get("writer_owner") or "")
            else:
                worker = active
                active_turn_id = str(getattr(worker, "turn_id", "") or "") if worker else ""
                writer_owner = ""
        if not worker:
            return {"accepted": False, "error": "turn_not_active", "turn_id": active_turn_id}
        if not expected or expected != active_turn_id:
            return {
                "accepted": False,
                "error": "turn_mismatch",
                "expected_turn_id": expected,
                "turn_id": active_turn_id,
            }
        ui_owned_history = writer_owner.startswith("ui:")
        if not ui_owned_history:
            messages = self.get_session_messages(session_id)
            messages[:] = merge_messages_by_id(messages, [message])
            messages[:] = self._normalize_persistable_messages(
                session_id,
                messages,
                source="daemon_guidance_submit",
            )
            # Save before handing input to the worker. A crash after this receipt
            # leaves a visible message, never an instruction to replay operations.
            retained = {"accepted": False, "message_committed": True, "error": "delivery_unconfirmed",
                        "turn_id": active_turn_id, "history_writer_owner": writer_owner}
            receipts[message["id"]] = {"signature": signature, "result": retained}
            with self.runtime_journal.session_lock(session_id):
                if not self.save_session(session_id, acknowledge=False):
                    return {"accepted": False, "error": "message_commit_failed", "turn_id": active_turn_id}
                self.runtime_journal.update_run(session_id, expected, {"guidance_receipts": receipts})
        else:
            _log_daemon(
                "daemon_guidance_history_write_delegated "
                f"session_id={session_id} turn_id={active_turn_id} "
                f"message_id={message['id']} writer_owner={writer_owner}"
            )
        result = worker.steer(message, expected_turn_id=expected)
        result["message_committed"] = not ui_owned_history
        result["history_writer_owner"] = writer_owner
        receipts[message["id"]] = {"signature": signature, "result": result}
        self.runtime_journal.update_run(session_id, expected, {"guidance_receipts": receipts})
        runtime_log.info("guidance_received session=%s run=%s message=%s accepted=%s committed=%s",
                         session_id, expected, message["id"], result.get("accepted"), result["message_committed"])
        return result

    def update_guidance_session(self, session_id, expected_turn_id, message_id, message):
        with self.lock:
            active = self.active_workers.get(session_id)
            if isinstance(active, dict):
                worker = active.get("worker")
                active_turn_id = str(active.get("turn_id") or "")
            else:
                worker = active
                active_turn_id = str(getattr(worker, "turn_id", "") or "") if worker else ""
        expected = str(expected_turn_id or "")
        if not worker:
            return {"updated": False, "error": "turn_not_active", "turn_id": active_turn_id}
        if not expected or expected != active_turn_id:
            return {
                "updated": False,
                "error": "turn_mismatch",
                "expected_turn_id": expected,
                "turn_id": active_turn_id,
            }
        return worker.update_guidance(message_id, message, expected_turn_id=expected)

    def delete_guidance_session(self, session_id, expected_turn_id, message_id):
        with self.lock:
            active = self.active_workers.get(session_id)
            if isinstance(active, dict):
                worker = active.get("worker")
                active_turn_id = str(active.get("turn_id") or "")
            else:
                worker = active
                active_turn_id = str(getattr(worker, "turn_id", "") or "") if worker else ""
        expected = str(expected_turn_id or "")
        if not worker:
            return {"deleted": False, "error": "turn_not_active", "turn_id": active_turn_id}
        if not expected or expected != active_turn_id:
            return {
                "deleted": False,
                "error": "turn_mismatch",
                "expected_turn_id": expected,
                "turn_id": active_turn_id,
            }
        return worker.delete_guidance(message_id, expected_turn_id=expected)

    def _close_live_subagents(self, session_id, force=False):
        try:
            close_reason = "父会话已停止，子任务本轮已停止，消息与队列已保留。"
            manager = get_agent_manager_registry().get_session_manager(
                session_id,
                chat_storage=self.chat_storage,
                config_manager=self.config_manager,
            )
            manager.execution_owner = "daemon"
            manager.step_signal = None
            manager.agent_state_signal = None
            live_items = manager.list_agent_summaries(status_filter=list(AGENT_LIVE_STATUSES))
            for item in live_items:
                agent_id = item.get("id")
                if not agent_id:
                    continue
                try:
                    summary = manager.interrupt_agent(agent_id, item.get("run_id", ""))
                    _log_daemon(
                        "close sub-agent "
                        + json.dumps(
                            {
                                "session_id": session_id,
                                "agent_id": agent_id,
                                "force": bool(force),
                                "status": summary.get("status"),
                                "reason": close_reason,
                            },
                            ensure_ascii=False,
                        )
                    )
                except Exception as close_err:
                    _log_daemon(
                        f"close sub-agent failed session_id={session_id} agent_id={agent_id} error={close_err}"
                    )
        except Exception as e:
            _log_daemon(f"_close_live_subagents failed session_id={session_id} error={e}")

    def _run_worker_once(
        self,
        session_id,
        worker_messages,
        workspace_dir,
        run_context=None,
        turn_id=None,
        request_id=None,
    ):
        result_holder = {}
        done = threading.Event()

        def on_finished(result):
            result_holder["result"] = result
            self.clear_active_worker(
                session_id,
                expected_worker=worker,
                expected_run_id=request_id,
            )
            done.set()

        worker = LLMWorker(
            worker_messages,
            self.config_manager,
            workspace_dir,
            session_id=session_id,
            run_context=run_context,
            turn_id=turn_id,
            request_id=request_id,
            skill_catalog_service=self.skill_catalog,
            dependency_coordinator=self.dependency_coordinator,
        )
        if worker.agent_manager is not None:
            worker.agent_manager.execution_owner = "daemon"

        worker.finished_signal.subscribe(on_finished)
        def on_thread_exit():
            if not done.is_set():
                on_finished({"error": str(worker.failure or "运行线程未返回结果"),
                             "error_type": "WorkerExited", "generated_messages": [],
                             "turn_id": turn_id, "request_id": request_id})
        worker.finished.subscribe(on_thread_exit)
        activated = self.set_active_worker(
            session_id,
            worker,
            turn_id=turn_id,
            run_id=request_id,
            writer_owner=(self.runtime_journal.get_run(session_id, request_id) or {}).get("writer_owner"),
        )
        if not activated:
            return {
                "error": "Run interrupted by user.",
                "generated_messages": [],
                "turn_id": turn_id,
                "request_id": request_id,
                "_runtime_terminal": "interrupted",
            }
        worker.start()
        done.wait()
        worker.join()
        return result_holder.get("result") or {"error": "No response"}

    def _is_context_overflow_error(self, result):
        if not isinstance(result, dict):
            return False
        text = (result.get("error") or "").lower()
        if not text:
            return False
        markers = [
            "context length",
            "maximum context",
            "too many tokens",
            "context_window_exceeded",
            "maximum context length",
        ]
        return any(marker in text for marker in markers)

    def _get_im_binding_for_session(self, session_id):
        binding = self.chat_storage.get_im_session_binding_by_conversation(session_id)
        if not binding:
            return None
        parsed = parse_im_session_key(binding.get("im_user_id"))
        if not parsed:
            return None
        return {
            "provider": binding.get("provider"),
            "im_user_id": parsed.get("im_user_id"),
            "chat_id": parsed.get("chat_id"),
            "summary_date": parsed.get("summary_date"),
        }

    def _estimate_token_count(self, text):
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _estimate_message_token_count(self, message):
        if not isinstance(message, dict):
            return 0
        parts = []
        for key in ("role", "name", "content", "reasoning_content"):
            value = message.get(key)
            if isinstance(value, str):
                parts.append(value)
            elif value:
                try:
                    parts.append(json.dumps(value, ensure_ascii=False))
                except Exception:
                    parts.append(str(value))
        for key in ("tool_calls", "function_call"):
            value = message.get(key)
            if value:
                try:
                    parts.append(json.dumps(value, ensure_ascii=False))
                except Exception:
                    parts.append(str(value))
        return self._estimate_token_count("\n".join(parts))

    def _estimate_messages_token_count(self, messages):
        return sum(self._estimate_message_token_count(msg) for msg in messages or [])

    def _selected_model_profile(self, run_context=None):
        snapshot = (run_context or {}).get("selected_model_profile")
        if isinstance(snapshot, dict) and snapshot:
            return snapshot
        model_id = (run_context or {}).get("selected_model_id")
        if hasattr(self.config_manager, "get_model_profile"):
            try:
                profile = self.config_manager.get_model_profile(model_id)
                if isinstance(profile, dict):
                    return profile
            except Exception:
                pass
        return {
            "model_name": self.config_manager.get("model_name", ""),
            "base_url": self.config_manager.get("base_url", ""),
        }

    def _context_window_tokens(self, run_context=None):
        profile = self._selected_model_profile(run_context)
        model_name = profile.get("model_name") or ""
        if is_deepseek_v4_model(model_name):
            configured = self.config_manager.get(
                "deepseek_v4_context_window_tokens",
                DEEPSEEK_V4_CONTEXT_WINDOW_TOKENS,
            )
            try:
                return max(1, int(configured))
            except Exception:
                return DEEPSEEK_V4_CONTEXT_WINDOW_TOKENS
        if is_gpt_5_6_model(model_name):
            return GPT_5_6_CONTEXT_WINDOW_TOKENS
        configured = self.config_manager.get("context_window_tokens", 128000)
        try:
            return max(1, int(configured))
        except Exception:
            return 128000

    def _context_budget_threshold(self, run_context=None):
        ratio = self.config_manager.get("context_budget_ratio", 0.8)
        try:
            ratio = float(ratio)
        except Exception:
            ratio = 0.8
        ratio = min(max(ratio, 0.1), 0.98)
        return int(self._context_window_tokens(run_context) * ratio)

    def _snippet(self, value, limit=180):
        text = (value or "").strip().replace("\r", " ").replace("\n", " ")
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    def _build_increment_summary(self, messages_slice):
        goals = []
        actions = []
        decisions = []
        pending = []
        preferences = []
        file_changes = []
        tool_results = []
        for msg in messages_slice:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = self._snippet(msg.get("content") or "")
            if not content:
                continue
            lower = content.lower()
            if role == "user":
                if len(goals) < 8:
                    goals.append(content)
                if any(k in lower for k in ["不要", "请用", "必须", "记住", "偏好", "风格", "格式"]) and len(preferences) < 6:
                    preferences.append(content)
                if any(k in lower for k in ["待确认", "确认", "是否", "吗", "?", "？"]) and len(pending) < 6:
                    pending.append(content)
            elif role == "assistant":
                if len(actions) < 10:
                    actions.append(content)
                if any(k in lower for k in ["决定", "采用", "方案", "策略", "改为"]) and len(decisions) < 8:
                    decisions.append(content)
                if any(k in lower for k in ["文件", "写入", "修改", "创建", "删除", "移动", "rename", "write", "delete", "move"]) and len(file_changes) < 8:
                    file_changes.append(content)
            elif role == "tool":
                if len(tool_results) < 10:
                    tool_results.append(content)
                if any(k in lower for k in ["file", "path", "文件", "目录", "写入", "修改", "created", "updated", "deleted"]) and len(file_changes) < 8:
                    file_changes.append(content)

        def _section(title, items):
            if not items:
                return f"{title}:\n- 暂无"
            return title + ":\n" + "\n".join([f"- {item}" for item in items[:10]])

        blocks = [
            _section("今日目标", goals),
            _section("已完成动作", actions),
            _section("关键决策与约束", decisions),
            _section("文件与产物变更", file_changes),
            _section("工具结果要点", tool_results),
            _section("未决问题与待确认项", pending),
            _section("用户偏好与约定", preferences),
        ]
        return "\n\n".join(blocks).strip()

    def _compress_summary_text(self, summary_text, max_chars):
        text = (summary_text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    def _adjust_compress_end_for_tool_round(self, messages, compress_end):
        while compress_end >= 0 and compress_end + 1 < len(messages):
            next_msg = messages[compress_end + 1]
            if not isinstance(next_msg, dict) or next_msg.get("role") != "tool":
                break
            compress_end -= 1
        return compress_end

    def _build_overflow_retry_messages(self, session_id, messages, run_context=None, force=False, reason="overflow"):
        enabled = self.config_manager.get("im_context_compression_enabled", True)
        if enabled is False:
            return None
        binding = self._get_im_binding_for_session(session_id)
        if not binding:
            return None
        total_tokens = self._estimate_messages_token_count(messages)
        threshold = self._context_budget_threshold(run_context)
        window_tokens = self._context_window_tokens(run_context)
        if not force and total_tokens < threshold:
            return None
        keep_turns = self.config_manager.get(
            "context_compression_recent_keep_turns",
            self.config_manager.get("im_summary_recent_keep_turns", 12),
        )
        try:
            keep_turns = int(keep_turns)
        except Exception:
            keep_turns = 40
        keep_turns = max(2, keep_turns)
        if len(messages) <= keep_turns:
            return None
        compress_end = len(messages) - keep_turns - 1
        compress_end = self._adjust_compress_end_for_tool_round(messages, compress_end)
        if compress_end < 0:
            return None
        summary_row = self.chat_storage.get_im_daily_summary(
            binding["provider"],
            binding["im_user_id"],
            binding["chat_id"],
            binding["summary_date"],
        )
        old_summary = ""
        covered_pos = -1
        if summary_row:
            old_summary = summary_row.get("summary_text") or ""
            covered_pos = summary_row.get("source_message_upto_pos")
            if covered_pos is None:
                covered_pos = -1
        if compress_end > covered_pos:
            increment_slice = messages[covered_pos + 1 : compress_end + 1]
            increment_summary = self._build_increment_summary(increment_slice)
            if old_summary and increment_summary:
                merged_summary = old_summary + "\n\n增量补充:\n" + increment_summary
            else:
                merged_summary = old_summary or increment_summary
        else:
            merged_summary = old_summary
        if not merged_summary.strip():
            return None
        rewrite_threshold = self.config_manager.get("im_summary_rewrite_threshold_chars", 6000)
        summary_max_chars = self.config_manager.get("im_summary_max_chars", 4000)
        try:
            rewrite_threshold = int(rewrite_threshold)
        except Exception:
            rewrite_threshold = 6000
        try:
            summary_max_chars = int(summary_max_chars)
        except Exception:
            summary_max_chars = 4000
        if len(merged_summary) > max(rewrite_threshold, summary_max_chars):
            merged_summary = self._compress_summary_text(merged_summary, summary_max_chars)
        token_estimate = self._estimate_token_count(merged_summary)
        self.chat_storage.upsert_im_daily_summary(
            binding["provider"],
            binding["im_user_id"],
            binding["chat_id"],
            binding["summary_date"],
            session_id,
            merged_summary,
            compress_end,
            token_estimate=token_estimate,
        )
        summary_message = {
            "role": "system",
            "content": f"Context Summary ({binding['summary_date']}):\n{merged_summary}",
        }
        tail_messages = messages[compress_end + 1 :]
        retry_messages = [summary_message] + tail_messages
        _log_daemon(
            "context_overflow_retry_prepare "
            + json.dumps(
                {
                    "session_id": session_id,
                    "reason": reason,
                    "hit_context_overflow": force,
                    "compressed_message_count": compress_end + 1,
                    "summary_date": binding["summary_date"],
                    "source_message_upto_pos": compress_end,
                    "estimated_tokens": total_tokens,
                    "context_window_tokens": window_tokens,
                    "budget_threshold_tokens": threshold,
                    "kept_recent_turns": keep_turns,
                },
                ensure_ascii=False,
            )
        )
        return retry_messages

    def run_llm_sync(
        self,
        session_id,
        user_text,
        workspace_dir=None,
        run_context=None,
        messages_snapshot=None,
        turn_id=None,
        request_id=None,
        user_message_id=None,
        writer_owner=None,
    ):
        final = {}
        def receive(event):
            nonlocal final
            if event.get("type") == "final":
                final = event["result"]
            elif event.get("type") == "error":
                final = {"error": event.get("error", "运行未能启动。")}
        self.request({"action": "send_message_stream", "session_id": session_id,
            "content": user_text, "workspace_dir": workspace_dir, "run_context": run_context,
            "messages": messages_snapshot, "turn_id": turn_id, "request_id": request_id,
            "user_message_id": user_message_id, "writer_owner": writer_owner}, receive)
        return final or {"error": "运行未返回结束结果，已持久化内容保留。"}


    def start_stream(self, payload, profile, publish, workbench):
        run_id, conversation_id = payload["request_id"], payload["conversation_id"]
        with self.lock:
            if run_id in self.streams:
                return
            thread = threading.Thread(target=self.consume_stream, args=(payload, profile, publish, workbench), name=f"desktop-run-{run_id[:8]}", daemon=True)
            self.streams[run_id] = thread
            thread.start()

    def consume_stream(self, payload, profile, publish, workbench):
        run_id, conversation_id = payload["request_id"], payload["conversation_id"]
        try:
            context = {**payload.get("runtime_context", {}), "selected_model_profile": profile}
            request = {"action": "send_message_stream", 
                       "session_id": conversation_id, "request_id": run_id, "turn_id": run_id,
                       "user_message_id": payload["message_id"], "content": payload["content"],
                       "workspace_dir": payload["workspace_dir"], "run_context": context,
                       "writer_owner": f"desktop:{os.getpid()}", "messages": self.chat_storage.get_messages(conversation_id)}
            runtime_log.info("run_start session=%s run=%s", conversation_id, run_id)
            final_seen = False
            def receive(event):
                nonlocal final_seen
                if event.get("type") == "final":
                    final_seen = True
                envelope = {"event": "runtime", "data": event, "conversation_id": conversation_id, "run_id": run_id}
                if event.get("type") in {"turn_started", "final"}:
                    row = self.desktop_store.get_run(run_id)
                    envelope["run"] = {key: row[key] for key in ("id", "conversation_id", "message_id", "status", "error", "created_at", "updated_at")}
                publish(envelope)
                if event.get("type") == "workbench_request":
                    threading.Thread(target=workbench, args=(event,), daemon=True).start()
            self.request(request, receive)
            if not final_seen:
                raise RuntimeError("执行连接已断开；已保存的消息和输出仍可查看，请重新打开对话核对状态。")
            terminal = self.desktop_store.get_run(run_id)
            runtime_log.info("run_complete session=%s run=%s status=%s", conversation_id, run_id, terminal["status"])
        except Exception as exc:
            runtime_log.exception("run_error session=%s run=%s", conversation_id, run_id)
            try:
                self.run_status(conversation_id, run_id, "failed", str(exc))
            except Exception:
                runtime_log.exception("run_error_save_failed session=%s run=%s", conversation_id, run_id)
            publish({"event": "run_error", "conversation_id": conversation_id, "run_id": run_id, "error": str(exc)})
        finally:
            with self.lock:
                self.streams.pop(run_id, None)
            publish({"event": "conversation_changed", "conversation_id": conversation_id})

    @_session_command
    def context_storage(self, session_id, event):
        """A worker requests a save; the runtime owns source validation and its receipt."""
        run_id = str(event["run_id"])
        operation = event["operation"]
        try:
            if (session_id, run_id) in self.cancelled_runs:
                raise RuntimeJournalError("任务已停止，未切换上下文。")
            run = self.runtime_journal.get_run(session_id, run_id) or {}
            if run.get("status") in RUN_TERMINAL_STATUSES:
                raise RuntimeJournalError("运行已结束，未切换上下文。")
            if operation == "prepare":
                messages = self.get_session_messages(session_id)
                generated = filter_persistable_messages(event.get("generated_messages") or [])
                with self.lock:
                    self.sessions[session_id] = merge_messages_by_id(messages, generated)
                if not self.save_session(session_id, acknowledge=False):
                    raise RuntimeJournalError("上下文来源保存冲突，历史已保留。")
            def action():
                if (session_id, run_id) in self.cancelled_runs:
                    raise RuntimeJournalError("任务已停止，未切换上下文。")
                payload = event["payload"]
                if operation == "prepare":
                    return self.chat_storage.check_context_source(session_id, payload["source_fingerprints"], payload["history_revision"])
                if operation == "commit":
                    return self.chat_storage.commit_context_window(session_id, payload)
                raise ValueError("未知上下文保存操作。")
            receipt = self.runtime_journal.execute_context_operation(session_id, run_id,
                event["operation_id"], event["expires_at"], action)
            runtime_log.info("context_saved session=%s run=%s operation=%s ok=%s", session_id, run_id, operation, receipt["ok"])
            return receipt
        except Exception as exc:
            runtime_log.exception("context_save_failed session=%s run=%s operation=%s", session_id, run_id, operation)
            receipt = {"operation_id": event["operation_id"], "ok": False, "error": str(exc), "error_type": type(exc).__name__}
            self.runtime_journal.update_run(session_id, run_id, {"context_receipt": receipt})
            return receipt

    def request(self, payload, send=None, disconnect=None):
        responses = []
        request = RuntimeRequest(self, send if send is not None else responses.append,
                                 disconnect if disconnect is not None else lambda reason: None)
        request.execute(payload)
        return responses[-1] if responses else None


class RuntimeRequest:
    """One execution with a transport-independent event sink."""

    def __init__(self, state, send, disconnect):
        self.state, self.send, self.disconnect = state, send, disconnect

    def execute(self, data):
        try:
            return self.dispatch(data)
        except Exception as exc:
            claimed = getattr(self, "_claimed_run", None)
            if claimed and not getattr(self, "_stream_finalized", False):
                session_id, run_id = claimed
                try:
                    status = "interrupted" if (session_id, run_id) in self.state.cancelled_runs else "failed"
                    self.state.run_status(session_id, run_id, status, str(exc))
                except Exception:
                    runtime_log.exception("run_abort_save_failed session=%s run=%s", session_id, run_id)
            raise
        finally:
            self.close()

    def dispatch(self, data):
        action = data.get("action")
        if action in {"send_message", "send_message_stream", "subagent_control"}:
            state = self.state
            with state.lock:
                if getattr(state, "update_preparing", False):
                    self.send({"status": "error", "type": "error", "error": "应用正在准备更新，请更新完成后再试。"})
                    return
                if not hasattr(state, "update_requests"):
                    state.update_requests = {}
                self._update_request_token = uuid.uuid4().hex
                state.update_requests[self._update_request_token] = (str(data.get("session_id") or ""), str(data.get("request_id") or ""))
        if action == "prepare_update":
            self.send(self.state.prepare_update_shutdown())
            return
        if action == "cancel_update":
            self.state.update_preparing = False
            self.send({"status": "ok"})
            return
        if action == "subagent_control":
            try:
                from core.subagent_control import dispatch_subagent
                state = self.state
                manager = get_agent_manager_registry().get_session_manager(
                    str(data.get("session_id") or ""), state.chat_storage, state.config_manager)
                manager.execution_owner = "daemon"
                result = dispatch_subagent(manager, data.get("operation"), data)
                self.send({"status": "ok", "result": result})
            except Exception as exc:
                self.send({"status": "error", "error": str(exc)})
            return
        if action == "ping":
            self.send({"status": "ok", "pid": os.getpid(), "signature": get_runtime_signature()})
            return
        if action == "status":
            state = self.state
            self.send(
                {
                    "status": "ok",
                    "suspended": state.suspended,
                    "last_activity": state.last_activity,
                    "sessions": len(state.sessions),
                    "active_runs": len(state.active_workers) + len(state.detached_workers) + get_agent_manager_registry().active_count(state.chat_storage.db_path),
                    "pid": os.getpid(),
                    "signature": get_runtime_signature(),
                }
            )
            return
        if action == "attach_run":
            session_id = str(data.get("session_id") or "")
            run_id = str(data.get("run_id") or "")
            if not session_id or not run_id:
                self.send({"status": "error", "error": "Missing session_id or run_id"})
                return
            run = self.state.runtime_journal.get_run(session_id, run_id)
            if run is None:
                self.send({"status": "error", "error": "Run not found"})
                return
            with self.state.lock:
                active = self.state.active_workers.get(session_id)
                active_worker = active.get("worker") if isinstance(active, dict) else active
                active_run_id = str(
                    (active.get("run_id") if isinstance(active, dict) else "")
                    or getattr(active_worker, "request_id", "")
                    or ""
                )
            try:
                active_worker_running = bool(
                    active_worker
                    and (
                        active_worker.is_running()
                        or not active_worker.is_finished()
                    )
                )
            except Exception:
                active_worker_running = False
            active_pipeline = getattr(active_worker, "_runtime_stream_pipeline", None)
            if active_pipeline is not None and active_pipeline.active:
                active_worker_running = True
            if active_worker and active_run_id == run_id and not active_worker_running:
                self.state.clear_active_worker(
                    session_id,
                    expected_worker=active_worker,
                    expected_run_id=run_id,
                )
            worker_active = bool(
                active_worker
                and active_run_id == run_id
                and active_worker_running
            )
            old_daemon_instance = bool(
                str(run.get("daemon_instance_id") or "")
                and str(run.get("daemon_instance_id") or "")
                != str(self.state.daemon_instance_id)
            )
            if (
                str(run.get("status") or "") not in RUN_TERMINAL_STATUSES
                and not worker_active
                and old_daemon_instance
            ):
                self.state.runtime_journal.append_event(
                    session_id,
                    run_id,
                    "run_interrupted",
                    {
                        "reason": "worker_not_alive",
                        "previous_daemon_instance_id": str(
                            run.get("daemon_instance_id") or ""
                        ),
                        "current_daemon_instance_id": str(
                            self.state.daemon_instance_id
                        ),
                    },
                )
                run = self.state.run_status(session_id, run_id, "interrupted", "上次运行已中断，已保存内容保留。")
            replay_pending = bool(
                active_run_id == run_id and active_pipeline is not None and not active_pipeline.drained
            )
            events = self.state.runtime_journal.read_events(
                session_id,
                run_id,
                starting_after=data.get("starting_after") or 0,
            )
            if replay_pending and run.get("status") in RUN_TERMINAL_STATUSES:
                # Replay must drain accepted events before reporting terminal.
                run = {**run, "status": "finalizing"}
            self.send(
                {
                    "status": "ok",
                    "run": run,
                    "events": events,
                    "worker_active": worker_active,
                }
            )
            return
        if action == "send_message":
            session_id = data.get("session_id") or uuid.uuid4().hex
            content = data.get("content") or ""
            turn_id = data.get("turn_id")
            request_id = data.get("request_id")
            user_message_id = data.get("user_message_id")
            workspace_dir = data.get("workspace_dir")
            run_context = normalize_run_context(data.get("run_context"))
            if not content and run_context.get("mode") != RUN_MODE_EXECUTION:
                self.send({"status": "error", "error": "Empty content"})
                return
            result = self.state.run_llm_sync(
                session_id,
                content,
                workspace_dir,
                run_context=run_context,
                messages_snapshot=data.get("messages"),
                turn_id=turn_id,
                request_id=request_id,
                user_message_id=user_message_id,
                writer_owner=str(data.get("writer_owner") or f"daemon:{os.getpid()}"),
            )
            self.send({"status": "ok", "session_id": session_id, "result": result})
            self._update_save_error = str((result or {}).get("_history_commit_error") or (result or {}).get("_runtime_journal_warning") or "")
            return
        if action == "send_message_stream":
            session_id = data.get("session_id") or uuid.uuid4().hex
            turn_id = str(data.get("turn_id") or uuid.uuid4().hex)
            request_id = str(data.get("request_id") or uuid.uuid4().hex)
            writer_owner = str(data.get("writer_owner") or f"daemon:{os.getpid()}")
            ui_owned_history = writer_owner.startswith("ui:")
            user_message_id = str(data.get("user_message_id") or "")
            content = data.get("content") or ""
            workspace_dir = data.get("workspace_dir")
            run_context = normalize_run_context(data.get("run_context"))
            _log_daemon(
                "send_message_stream received "
                f"session_id={session_id} turn_id={turn_id} content_len={len(content)} "
                f"workspace={workspace_dir} run_context_keys={sorted(list(run_context.keys()))}"
            )
            if not content and run_context.get("mode") != RUN_MODE_EXECUTION:
                self.send({"type": "error", "error": "Empty content"})
                return
            state = self.state
            state.touch()
            try:
                state.config_manager.load_config()
            except Exception as e:
                _log_daemon(f"send_message_stream load_config failed session_id={session_id} error={e}")
            idle_minutes = state.config_manager.get("daemon_idle_minutes", 10)
            state.idle_timeout = max(int(idle_minutes), 1) * 60
            messages = state.prepare_stream(session_id, request_id, turn_id, writer_owner,
                                            content, user_message_id, data.get("messages"))
            if messages is None:
                state.replay_stream(session_id, request_id, self.send)
                return
            self._claimed_run = (session_id, request_id)
            _log_daemon(
                f"send_message_stream prepared_messages session_id={session_id} "
                f"turn_id={turn_id} message_count={len(messages)}"
            )
            stream_lock = threading.Lock()
            stream_closed = threading.Event()
            worker_holder = {}

            def detach_stream_due_to_disconnect(reason):
                if stream_closed.is_set():
                    return
                stream_closed.set()
                try:
                    self.disconnect(reason)
                except OSError:
                    pass
                _log_daemon(
                    f"send_message_stream client disconnected session_id={session_id} "
                    f"run_id={request_id} last_sequence={last_stream_sequence['value']} reason={reason}"
                )
                threading.Thread(target=record_subscriber_detached, args=(reason,), daemon=True).start()

            def record_subscriber_detached(reason):
                try:
                    state.runtime_journal.append_event(
                        session_id,
                        request_id,
                        "subscriber_detached",
                        {"reason": str(reason or "socket_closed")},
                    )
                except Exception as exc:
                    _log_daemon(
                        "subscriber_detached journal failed "
                        f"session_id={session_id} run_id={request_id} "
                        + _runtime_journal_error_details(
                            state.runtime_journal,
                            "append_subscriber_detached",
                            exc,
                        )
                    )

            last_stream_sequence = {"value": 0}

            def write_stream(payload, sequence):
                if stream_closed.is_set():
                    return False
                try:
                    with stream_lock:
                        if stream_closed.is_set():
                            return False
                        wire_payload = dict(payload)
                        wire_payload["sequence"] = int(sequence or 0)
                        self.send(wire_payload)
                    return True
                except Exception as e:
                    _log_daemon(f"send_stream write failed session_id={session_id} payload_type={payload.get('type')} error={e}")
                    detach_stream_due_to_disconnect(str(e))
                    return False

            def send_stream(payload):
                payload = dict(payload)
                payload["session_id"] = session_id
                payload["run_id"] = request_id
                if payload.get("type") in {"turn_started", "final"}:
                    authorization = getattr(worker_holder.get("worker"), "execution_authorization", None)
                    if authorization is not None:
                        payload["execution_policy"] = authorization.runtime_snapshot()
                payload["source_message_id"] = str(
                    getattr(worker_holder.get("worker"), "current_assistant_message_id", "") or ""
                )
                try:
                    return pipeline.enqueue(
                        payload, terminal=payload.get("type") in {"final", "interaction_finished"},
                    )
                except Exception as exc:
                    _log_daemon(
                        "runtime stream event write failed "
                        f"session_id={session_id} run_id={request_id} "
                        + _runtime_journal_error_details(
                            state.runtime_journal,
                            f"append_{payload.get('type') or 'stream_event'}",
                            exc,
                        )
                    )
                    on_stream_failure(exc)
                    if payload.get("type") in {"turn_started", "final"}:
                        raise
                    return False

            result_holder = {}
            done = threading.Event()

            def on_stream_failure(exc):
                if worker_holder.get("stream_failed"):
                    return
                worker_holder["stream_failed"] = str(exc)
                current_pipeline = getattr(self, "_stream_pipeline", None)
                if current_pipeline is not None:
                    current_pipeline.stop_accepting()
                _log_daemon(
                    f"runtime_stream failed session_id={session_id} run_id={request_id} "
                    f"error={exc}"
                )
                active_worker = worker_holder.get("worker")
                try:
                    if active_worker is not None:
                        active_worker.stop()
                finally:
                    done.set()

            def report_stream(**metrics):
                last_stream_sequence["value"] = int(metrics.get("last_sequence") or 0)
                _log_daemon(
                    f"runtime_stream summary session_id={session_id} run_id={request_id} "
                    + json.dumps(metrics, separators=(",", ":"))
                )

            def on_finished(result):
                try:
                    result_holder["result"] = result
                except Exception as exc:
                    _log_daemon(
                        f"send_message_stream final capture failed session_id={session_id} "
                        f"run_id={request_id} error={exc}"
                    )
                finally:
                    done.set()

            worker = LLMWorker(
                messages,
                state.config_manager,
                workspace_dir,
                session_id=session_id,
                run_context=run_context,
                turn_id=turn_id,
                request_id=request_id,
                skill_catalog_service=state.skill_catalog,
                dependency_coordinator=state.dependency_coordinator,
            )
            if worker.agent_manager is not None:
                worker.agent_manager.execution_owner = "daemon"
            worker_holder["worker"] = worker
            pipeline = RuntimeStream(
                state.runtime_journal, session_id, request_id,
                write=write_stream, detach=detach_stream_due_to_disconnect,
                failed=on_stream_failure, report=report_stream,
            )
            self._stream_pipeline = pipeline
            self._stream_worker = worker
            worker._runtime_stream_pipeline = pipeline
            worker.thinking_signal.subscribe(lambda text: send_stream({"type": "thinking", "delta": text}))
            worker.content_signal.subscribe(lambda text: send_stream({"type": "content", "delta": text}))
            content_snapshot_signal = getattr(worker, "content_snapshot_signal", None)
            if content_snapshot_signal is not None:
                content_snapshot_signal.subscribe(
                    lambda text: send_stream({"type": "content_snapshot", "content": text}),
                )
            worker.tool_call_signal.subscribe(lambda data: send_stream({"type": "tool_call", "data": data}))
            worker.tool_result_signal.subscribe(lambda data: send_stream({"type": "tool_result", "data": data}))
            worker.agent_state_signal.subscribe(lambda data: send_stream({"type": "agent_state", "data": data}))
            def observe(data):
                if data.get("type") == "context_storage_request":
                    state.context_storage(session_id, data)
                else:
                    send_stream({"type": "observability", "data": data})
            worker.observability_signal.subscribe(observe)
            worker.output_signal.subscribe(lambda text: send_stream({"type": "log", "data": text}))
            worker.finished_signal.subscribe(on_finished)
            def on_thread_exit():
                if not done.is_set():
                    on_finished({"error": str(worker.failure or "运行线程未返回结果"),
                                 "error_type": "WorkerExited", "generated_messages": [],
                                 "turn_id": turn_id, "request_id": request_id})
            worker.finished.subscribe(on_thread_exit)

            def route_interaction(payload, event_type):
                if (payload or {}).get("session_id") != session_id:
                    return
                request_run_id = (payload or {}).get("run_id") or (payload or {}).get("metadata", {}).get("run_id")
                if request_run_id:
                    if request_run_id != worker.execution_authorization.run_id:
                        return
                elif not worker.is_current_thread():
                    return
                request_payload = dict(payload or {})
                send_stream({"type": event_type, "data": request_payload})
                _log_daemon(f"interaction dispatched type={event_type} session_id={session_id} run_id={request_id} request_id={request_payload.get('request_id')} status={request_payload.get('status', '')}")

            def handle_interaction_request(payload):
                route_interaction(payload, "interaction_request")

            def handle_interaction_finished(payload):
                route_interaction(payload, "interaction_finished")

            from core.workbench import workbench_bridge
            from core.workbench_transport import dispatch as dispatch_workbench
            workbench_host = workbench_bridge()
            route_key = (worker.execution_authorization.session_id, worker.execution_authorization.run_id)
            with workbench_host._lock:
                workbench_host.remote_routes[route_key] = lambda act, args, ctx, timeout: dispatch_workbench(send_stream, act, args, ctx, timeout)
            interaction_service.interaction_requested.subscribe(handle_interaction_request)
            interaction_service.interaction_finished.subscribe(handle_interaction_finished)
            activated = state.set_active_worker(
                session_id,
                worker,
                turn_id=turn_id,
                run_id=request_id,
                writer_owner=writer_owner,
            )
            if activated:
                state.run_status(session_id, request_id, "running")
                try:
                    try:
                        _log_daemon(f"send_message_stream worker_starting session_id={session_id} turn_id={turn_id}")
                        send_stream({"type": "turn_started", "turn_id": turn_id})
                        worker.start()
                        _log_daemon(
                            f"send_message_stream worker_started session_id={session_id} "
                            f"turn_id={turn_id} is_running={worker.is_running()}"
                        )
                        done.wait()
                        if not worker.join(2.0):
                            state.detach_worker_until_finished(
                                session_id,
                                worker,
                                reason="stream_closed" if stream_closed.is_set() else "finished_wait_timeout",
                            )
                    except Exception as exc:
                        result_holder["result"] = {
                            "error": f"tool execution journal write failed: {exc}",
                            "error_type": type(exc).__name__,
                            "generated_messages": [],
                            "turn_id": turn_id,
                            "request_id": request_id,
                        }
                        _log_daemon(
                            "send_message_stream startup failed "
                            f"session_id={session_id} run_id={request_id} "
                            + _runtime_journal_error_details(
                                state.runtime_journal,
                                "start_run",
                                exc,
                            )
                        )
                finally:
                    try:
                        interaction_service.interaction_requested.unsubscribe(handle_interaction_request)
                        interaction_service.interaction_finished.unsubscribe(handle_interaction_finished)
                    except Exception as e:
                        _log_daemon(f"disconnect interaction bridge failed session_id={session_id} error={e}")
            else:
                try:
                    interaction_service.interaction_requested.unsubscribe(handle_interaction_request)
                    interaction_service.interaction_finished.unsubscribe(handle_interaction_finished)
                except Exception as e:
                    _log_daemon(f"disconnect interaction bridge failed session_id={session_id} error={e}")
                result_holder["result"] = {
                    "error": "Run interrupted by user.",
                    "generated_messages": [],
                    "turn_id": turn_id,
                    "request_id": request_id,
                }
            with workbench_host._lock:
                workbench_host.remote_routes.pop(route_key, None)
            result = result_holder.get("result") or {"error": "No response"}
            try:
                pipeline.flush()
            except Exception as exc:
                worker_holder["stream_failed"] = str(exc)
            if worker_holder.get("stream_failed"):
                result = dict(result)
                result["error"] = "运行记录保存失败：" + worker_holder["stream_failed"]
                result["_runtime_journal_warning"] = worker_holder["stream_failed"]
            result = _normalize_worker_error(result)
            # Drain previously accepted output before committing the final receipt.
            pipeline.close()
            try:
                authorization = getattr(worker, "execution_authorization", None)
                final, sequence = state.finalize(session_id, request_id, turn_id, messages, result,
                    ui_owned=ui_owned_history,
                    execution_policy=authorization.runtime_snapshot() if authorization else None)
                write_stream(final, sequence)
                self._stream_finalized = True
            except Exception as exc:
                self._update_save_error = str(exc)
                _log_daemon(f"runtime_finalize_failed session_id={session_id} run_id={request_id} error={exc}")
                failure = {"type": "final", "session_id": session_id, "run_id": request_id, "created_at": time.time(),
                    "result": {**result, "error": "运行结果保存失败，已持久化内容保留。",
                               "_runtime_journal_warning": str(exc)}}
                sequence = 0
                try:
                    with state.runtime_journal.session_lock(session_id):
                        status = "interrupted" if (session_id, request_id) in state.cancelled_runs else "failed"
                        state.run_status(session_id, request_id, status, str(exc))
                        saved = state.runtime_journal.append_event(session_id, request_id, "final", failure)
                    sequence = saved["sequence"]
                    self._stream_finalized = True
                except Exception as save_error:
                    _log_daemon(f"runtime_failure_save_failed session_id={session_id} run_id={request_id} error={save_error}")
                write_stream(failure, sequence)
            finally:
                state.clear_active_worker(session_id, expected_worker=worker, expected_run_id=request_id)
                if getattr(self, "_stream_finalized", False):
                    with state.lock:
                        state.accepted_runs.pop(request_id, None)
                        state.cancelled_runs.discard((session_id, request_id))
            state.touch()
            return
        if action == "stop_session":
            session_id = data.get("session_id")
            if not session_id:
                self.send({"status": "error", "error": "Missing session_id"})
                return
            expected_run_id = str(data.get("run_id") or "")
            stop_result = self.state.stop_session(
                session_id,
                expected_run_id=expected_run_id,
                wait_for_release=True,
            )
            self.send({
                "status": "ok",
                "stopped": bool(stop_result.get("stopped")),
                "released": bool(stop_result.get("released")),
                "release_timeout_ms": int(stop_result.get("release_timeout_ms") or 0),
                "run_id": expected_run_id,
                "save_error": stop_result.get("save_error", ""),
            })
            return
        if action == "steer_message":
            session_id = data.get("session_id")
            expected_turn_id = data.get("expected_turn_id")
            message = data.get("message")
            if not session_id or expected_turn_id in (None, ""):
                self.send({"status": "error", "error": "Missing session_id or expected_turn_id"})
                return
            result = self.state.steer_session(session_id, expected_turn_id, message)
            self.send({"status": "ok", **result})
            return
        if action == "update_guidance":
            session_id = data.get("session_id")
            expected_turn_id = data.get("expected_turn_id")
            message_id = data.get("message_id")
            message = data.get("message")
            if not session_id or expected_turn_id in (None, "") or not message_id:
                self.send({"status": "error", "error": "Missing guidance update fields"})
                return
            result = self.state.update_guidance_session(
                session_id,
                expected_turn_id,
                message_id,
                message,
            )
            self.send({"status": "ok", **result})
            return
        if action == "delete_guidance":
            session_id = data.get("session_id")
            expected_turn_id = data.get("expected_turn_id")
            message_id = data.get("message_id")
            if not session_id or expected_turn_id in (None, "") or not message_id:
                self.send({"status": "error", "error": "Missing guidance delete fields"})
                return
            result = self.state.delete_guidance_session(
                session_id,
                expected_turn_id,
                message_id,
            )
            self.send({"status": "ok", **result})
            return
        if action == "refresh_skills":
            try:
                event = SkillChangeEvent.create(
                    data.get("change_action") or "updated",
                    data.get("skill_names") or [],
                    source=data.get("source") or "ui",
                    session_id=data.get("session_id") or "",
                )
                applied = self.state.skill_catalog.publish_change(event)
                self.send({"status": "ok", "event": applied.to_dict()})
            except Exception as exc:
                self.send({"status": "error", "error": str(exc)})
            return
        if action in {"workbench_claim", "workbench_active", "workbench_result"}:
            from core.workbench_transport import handle_host_request
            self.send(handle_host_request(data))
            return
        if action == "respond_interaction":
            request_id = data.get("request_id")
            result = data.get("result")
            if not request_id:
                self.send({"status": "error", "error": "Missing request_id"})
                return
            self.send(interaction_service.respond_request(request_id, result))
            return
        if action == "get_pending_interaction":
            session_id = data.get("session_id")
            if not session_id:
                self.send({"status": "error", "error": "Missing session_id"})
                return
            pending = interaction_service.get_pending_request(session_id)
            self.send({"status": "ok", "pending": pending})
            return
        if action == "shutdown":
            self.send({"status": "ok"})
            self.state.shutdown_requested = True
            return
        self.send({"status": "error", "error": "Unknown action"})


    def close(self):
        pipeline = getattr(self, "_stream_pipeline", None)
        try:
            if pipeline is not None:
                pipeline.stop_accepting()
                if not getattr(self, "_stream_finalized", False):
                    worker = getattr(self, "_stream_worker", None)
                    if worker is not None:
                        try:
                            worker.stop()
                        except Exception as exc:
                            _log_daemon(f"runtime_stream abort stop failed run_id={pipeline.run_id} error={exc}")
                        self.state.detach_worker_until_finished(
                            pipeline.session_id, worker, reason="stream_handler_failed",
                        )
                pipeline.close()
                if not getattr(self, "_stream_finalized", False):
                    _log_daemon(
                        f"runtime_stream handler_aborted session_id={pipeline.session_id} "
                        f"run_id={pipeline.run_id} last_sequence={pipeline.last_sequence}"
                    )
                    try:
                        self.state.runtime_journal.interrupt_run(
                            pipeline.session_id, pipeline.run_id, reason="stream_handler_failed",
                        )
                    except Exception as exc:
                        _log_daemon(f"runtime_stream abort journal failed run_id={pipeline.run_id} error={exc}")
                    finally:
                        self.state.clear_active_worker(
                            pipeline.session_id, expected_worker=worker, expected_run_id=pipeline.run_id,
                        )
        finally:
            if getattr(self, "_update_request_token", None):
                state = self.state
                with state.lock:
                    error = getattr(self, "_update_save_error", "")
                    if pipeline is not None and (pipeline.error or not getattr(self, "_stream_finalized", False)):
                        error = str(pipeline.error or "后台回复尚未完成保存")
                    if state.update_preparing and error:
                        state.update_errors[self._update_request_token] = error
                    state.update_requests.pop(self._update_request_token, None)

