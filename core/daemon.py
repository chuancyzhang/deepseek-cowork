import json
import os
import socket
import socketserver
import threading
import time
import uuid
import hashlib
from PySide6.QtCore import QEventLoop, QTimer, Qt, QThread
from PySide6.QtWidgets import QApplication
from core.agent import LLMWorker
from core.agent_manager import AGENT_LIVE_STATUSES, get_agent_manager_registry
from core.chat_storage import ChatStorage, ConversationWriteConflict
from core.config_manager import ConfigManager
from core.env_utils import get_app_data_dir, get_base_dir
from core.im_session_key import parse_im_session_key
from core.interaction import interaction_service
from core.clarify_mode import RUN_MODE_EXECUTION, normalize_run_context
from core.llm.deepseek import DEEPSEEK_V4_CONTEXT_WINDOW_TOKENS, is_deepseek_v4_model
from core.llm.providers import GPT_5_6_CONTEXT_WINDOW_TOKENS, is_gpt_5_6_model
from core.message_persistence import filter_persistable_messages
from core.runtime_journal import RUN_TERMINAL_STATUSES, RuntimeJournal, RuntimeJournalError
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
        log_dir = get_app_data_dir()
        log_path = os.path.join(log_dir, "daemon.log")
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        try:
            print(f"[daemon] {message}")
        except Exception:
            return


def _compute_session_title(messages):
    title = "新对话"
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content") or ""
            if content:
                title = content[:15] + "..." if len(content) > 15 else content
            break
    return title


class DaemonState:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        history_dir = config_manager.get_chat_history_dir()
        db_path = os.path.join(history_dir, "chat_history.sqlite")
        self.chat_storage = ChatStorage(db_path)
        self.runtime_journal = RuntimeJournal(history_dir)
        self.sessions = {}
        self.active_workers = {}
        self.detached_workers = {}
        self.lock = threading.Lock()
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
            if self.active_workers or self.detached_workers:
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
        message = {"id": stable_id, "role": "user", "content": content}
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
        if "error" in result:
            _log_daemon(
                "provider_error_without_ledger_append "
                + json.dumps(
                    {
                        "session_id": session_id,
                        "source": source,
                        "error": str(result.get("error") or ""),
                    },
                    ensure_ascii=False,
                )
            )
            return
        generated_messages = result.get("generated_messages", [])
        if generated_messages:
            persistable_messages = filter_persistable_messages(generated_messages)
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
    
    def set_active_worker(self, session_id, worker, turn_id=None, run_id=None):
        resolved_run_id = str(run_id or getattr(worker, "request_id", "") or "")
        with self.lock:
            if resolved_run_id:
                run_record = self.runtime_journal.get_run(session_id, resolved_run_id)
                if isinstance(run_record, dict) and (
                    bool(run_record.get("stop_requested"))
                    or str(run_record.get("status") or "") in RUN_TERMINAL_STATUSES
                ):
                    _log_daemon(
                        "set_active_worker rejected terminal run "
                        f"session_id={session_id} run_id={resolved_run_id} "
                        f"status={run_record.get('status')}"
                    )
                    return False
            self.active_workers[session_id] = {
                "worker": worker,
                "turn_id": str(turn_id or getattr(worker, "turn_id", "") or ""),
                "run_id": resolved_run_id,
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
            worker.finished.connect(cleanup, Qt.DirectConnection)
        except Exception as e:
            _log_daemon(f"detach worker connect finished failed session_id={session_id} error={e}")
        try:
            is_running = bool(worker.isRunning())
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
    
    def stop_session(self, session_id, expected_run_id=""):
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
            # Keep the journal transition and worker registration mutually exclusive.
            # Otherwise a stop can observe no worker, then a worker can register and
            # start after the journal has already been marked interrupted.
            for run_id in run_ids:
                try:
                    record = self.runtime_journal.interrupt_run(
                        session_id,
                        run_id,
                        reason="interrupted by user",
                    )
                    if bool(record.get("stop_requested")):
                        interrupted_run_ids.add(run_id)
                except RuntimeJournalError as exc:
                    _log_daemon(
                        f"stop_session runtime interrupt failed session_id={session_id} "
                        f"run_id={run_id} error={exc}"
                    )
            matched_execution = bool(worker or detached)
            if matched_execution or not expected_run_id:
                interaction_service.cancel_session_requests(session_id, reason="cancelled")
                self._close_live_subagents(session_id, force=True)
            if worker:
                try:
                    worker.stop()
                except Exception as e:
                    _log_daemon(f"stop_session worker.stop failed session_id={session_id} error={e}")
            for detached_worker in detached:
                try:
                    detached_worker.stop()
                except Exception as e:
                    _log_daemon(f"stop_session detached worker.stop failed session_id={session_id} error={e}")
            stopped = bool(matched_execution or interrupted_run_ids)
        _log_daemon(
            "stop_session handled "
            f"session_id={session_id} expected_run_id={expected_run_id or '-'} "
            f"matched_worker={bool(worker)} detached_workers={len(detached)} "
            f"interrupted_runs={sorted(interrupted_run_ids)} stopped={stopped}"
        )
        return stopped

    def steer_session(self, session_id, expected_turn_id, message):
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
            return {"accepted": False, "error": "turn_not_active", "turn_id": active_turn_id}
        if not expected or expected != active_turn_id:
            return {
                "accepted": False,
                "error": "turn_mismatch",
                "expected_turn_id": expected,
                "turn_id": active_turn_id,
            }
        return worker.steer(message, expected_turn_id=expected)

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
            close_reason = "Daemon 会话已停止，子 Agent 被终止。" if force else "Daemon 会话已结束，子 Agent 已关闭。"
            manager = get_agent_manager_registry().get_session_manager(
                session_id,
                chat_storage=self.chat_storage,
                config_manager=self.config_manager,
            )
            manager.step_signal = None
            manager.agent_state_signal = None
            live_items = manager.list_agent_summaries(status_filter=list(AGENT_LIVE_STATUSES))
            for item in live_items:
                agent_id = item.get("id")
                if not agent_id:
                    continue
                try:
                    summary = manager.close_agent(agent_id, force=bool(force), reason=close_reason)
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
        loop = QEventLoop()

        def on_finished(result):
            result_holder["result"] = result
            self.clear_active_worker(
                session_id,
                expected_worker=worker,
                expected_run_id=request_id,
            )
            loop.quit()

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
        worker.finished_signal.connect(on_finished)
        activated = self.set_active_worker(
            session_id,
            worker,
            turn_id=turn_id,
            run_id=request_id,
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
        loop.exec()
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
        self.touch()
        try:
            self.config_manager.load_config()
        except Exception as e:
            _log_daemon(f"run_llm_sync load_config failed session_id={session_id} error={e}")
        idle_minutes = self.config_manager.get("daemon_idle_minutes", 10)
        self.idle_timeout = max(int(idle_minutes), 1) * 60
        messages = self.request_messages(session_id, messages_snapshot)
        sqlite_baseline = (
            self.chat_storage.get_messages(session_id)
            if self.chat_storage.has_conversation(session_id)
            else []
        )
        normalized_run_context = normalize_run_context(run_context)
        turn_id = str(turn_id or uuid.uuid4().hex)
        request_id = str(request_id or uuid.uuid4().hex)
        self.append_user_message_if_needed(
            messages,
            user_text,
            message_id=user_message_id,
            turn_id=turn_id,
            request_id=request_id,
        )
        effective_writer_owner = str(writer_owner or f"daemon:{os.getpid()}")
        self.ensure_runtime_run(
            session_id,
            request_id,
            turn_id,
            effective_writer_owner,
            sqlite_baseline,
        )
        worker_messages = (
            self._build_overflow_retry_messages(
                session_id,
                messages,
                run_context=normalized_run_context,
                force=False,
                reason="budget",
            )
            or messages
        )
        result = self._run_worker_once(
            session_id,
            worker_messages,
            workspace_dir,
            run_context=normalized_run_context,
            turn_id=turn_id,
            request_id=request_id,
        )
        retry_once = self.config_manager.get("im_context_overflow_retry_once", True)
        if retry_once is not False and self._is_context_overflow_error(result):
            retry_messages = self._build_overflow_retry_messages(
                session_id,
                messages,
                run_context=normalized_run_context,
                force=True,
                reason="overflow",
            )
            if retry_messages:
                retry_result = self._run_worker_once(
                    session_id,
                    retry_messages,
                    workspace_dir,
                    run_context=normalized_run_context,
                    turn_id=turn_id,
                    request_id=request_id,
                )
                _log_daemon(
                    "context_overflow_retry_result "
                    + json.dumps(
                        {
                            "session_id": session_id,
                            "retry_success": "error" not in retry_result,
                            "final_fallback": "error" in retry_result,
                        },
                        ensure_ascii=False,
                    )
                )
                result = retry_result
        latest_run = self.runtime_journal.get_run(session_id, request_id) or {}
        if latest_run.get("stop_requested"):
            result = {
                "error": "Run interrupted by user.",
                "generated_messages": result.get("generated_messages", []),
                "turn_id": turn_id,
                "request_id": request_id,
            }
        provider_succeeded = "error" not in result
        terminal_status = (
            "completed"
            if provider_succeeded
            else ("interrupted" if latest_run.get("stop_requested") else "failed")
        )
        if provider_succeeded:
            finalizing_record = self.runtime_journal.update_run(
                session_id,
                request_id,
                {
                    "status": "finalizing",
                    "terminal_error": "",
                    "final_result": result,
                },
            )
            if str(finalizing_record.get("status") or "") == "interrupted":
                result = {
                    "error": "Run interrupted by user.",
                    "generated_messages": result.get("generated_messages", []),
                    "turn_id": turn_id,
                    "request_id": request_id,
                }
                provider_succeeded = False
                terminal_status = "interrupted"
            else:
                self.runtime_journal.append_event(
                    session_id,
                    request_id,
                    "finalizing",
                    {"source": "daemon_sync"},
                )
        postprocess_error = ""
        try:
            self.append_worker_result_messages(
                session_id,
                messages,
                result,
                source="daemon_sync_result",
            )
            messages[:] = self._normalize_persistable_messages(
                session_id,
                messages,
                source="daemon_sync_finalize",
            )
        except Exception as exc:
            postprocess_error = str(exc)
            _log_daemon(
                f"daemon_sync postprocess failed session_id={session_id} "
                f"run_id={request_id} error={exc}"
            )
        terminal_record = self.runtime_journal.update_run(
            session_id,
            request_id,
            {
                "status": terminal_status,
                "terminal_error": str(result.get("error") or ""),
                "final_result": result,
            },
        )
        terminal_status = str(terminal_record.get("status") or terminal_status)
        if terminal_status == "interrupted" and "error" not in result:
            result = {
                "error": "Run interrupted by user.",
                "generated_messages": result.get("generated_messages", []),
                "turn_id": turn_id,
                "request_id": request_id,
            }
            provider_succeeded = False
        self.runtime_journal.append_event(
            session_id,
            request_id,
            "terminal",
            {
                "status": terminal_status,
                "error": str(result.get("error") or ""),
                "source": "daemon_sync",
            },
        )
        if isinstance(result, dict):
            result["_runtime_terminal"] = terminal_status
        self.runtime_journal.update_run(
            session_id,
            request_id,
            {
                "postprocess_status": "failed" if postprocess_error else "completed",
                "postprocess_error": postprocess_error,
            },
        )
        commit_error = postprocess_error
        daemon_commit_succeeded = False
        if not postprocess_error and effective_writer_owner.startswith("ui:"):
            if provider_succeeded:
                try:
                    self.runtime_journal.mark_pending_commit(
                        session_id,
                        request_id,
                        messages,
                        title=_compute_session_title(messages),
                    )
                except RuntimeJournalError as exc:
                    stopped_run = self.runtime_journal.get_run(session_id, request_id) or {}
                    if not stopped_run.get("stop_requested"):
                        commit_error = str(exc)
                    else:
                        _log_daemon(
                            f"daemon_sync pending_commit rejected_interrupted "
                            f"session_id={session_id} run_id={request_id}"
                        )
        elif not postprocess_error and not effective_writer_owner.startswith("ui:"):
            try:
                daemon_commit_succeeded = self.save_session(
                    session_id,
                    run_id=request_id,
                    acknowledge=False,
                )
                if not daemon_commit_succeeded:
                    commit_error = "SQLite conversation save did not commit."
            except Exception as exc:
                commit_error = str(exc)
        if commit_error:
            self.runtime_journal.update_run(
                session_id,
                request_id,
                {"commit_status": "failed", "commit_error": commit_error},
            )
            self.runtime_journal.append_event(
                session_id,
                request_id,
                "commit_failed",
                {"error": commit_error},
            )
        elif provider_succeeded:
            self.runtime_journal.update_run(
                session_id,
                request_id,
                {
                    "commit_status": (
                        "pending"
                        if effective_writer_owner.startswith("ui:")
                        else "completed"
                    )
                },
            )
        if daemon_commit_succeeded:
            self.runtime_journal.acknowledge_commit(
                session_id,
                request_id,
                messages,
            )
        self.touch()
        return result


class DaemonRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline()
        if not line:
            return
        try:
            data = json.loads(line.decode("utf-8"))
        except Exception:
            self._send({"status": "error", "error": "Invalid JSON"})
            return
        action = data.get("action")
        if action == "ping":
            self._send({"status": "ok", "pid": os.getpid(), "signature": get_runtime_signature()})
            return
        if action == "status":
            state = self.server.state
            self._send(
                {
                    "status": "ok",
                    "suspended": state.suspended,
                    "last_activity": state.last_activity,
                    "sessions": len(state.sessions),
                    "active_runs": len(state.active_workers) + len(state.detached_workers),
                    "pid": os.getpid(),
                    "signature": get_runtime_signature(),
                }
            )
            return
        if action == "attach_run":
            session_id = str(data.get("session_id") or "")
            run_id = str(data.get("run_id") or "")
            if not session_id or not run_id:
                self._send({"status": "error", "error": "Missing session_id or run_id"})
                return
            run = self.server.state.runtime_journal.get_run(session_id, run_id)
            if run is None:
                self._send({"status": "error", "error": "Run not found"})
                return
            with self.server.state.lock:
                active = self.server.state.active_workers.get(session_id)
                active_worker = active.get("worker") if isinstance(active, dict) else active
                active_run_id = str(
                    (active.get("run_id") if isinstance(active, dict) else "")
                    or getattr(active_worker, "request_id", "")
                    or ""
                )
            worker_active = bool(active_worker and active_run_id == run_id)
            run_age = max(0.0, time.time() - float(run.get("updated_at") or 0.0))
            old_daemon_instance = bool(
                str(run.get("daemon_instance_id") or "")
                and str(run.get("daemon_instance_id") or "")
                != str(self.server.state.daemon_instance_id)
            )
            if (
                str(run.get("status") or "") == "finalizing"
                and not worker_active
                and old_daemon_instance
                and isinstance(run.get("final_result"), dict)
                and run_age >= 5.0
            ):
                run = self.server.state.runtime_journal.update_run(
                    session_id,
                    run_id,
                    {
                        "status": "completed",
                        "terminal_error": "",
                        "recovered_by_daemon_instance_id": (
                            self.server.state.daemon_instance_id
                        ),
                    },
                )
                self.server.state.runtime_journal.append_event(
                    session_id,
                    run_id,
                    "terminal",
                    {
                        "status": "completed",
                        "source": "daemon_finalizing_recovery",
                    },
                )
            if (
                str(run.get("status") or "") == "running"
                and not worker_active
                and old_daemon_instance
                and run_age >= 5.0
            ):
                self.server.state.runtime_journal.append_event(
                    session_id,
                    run_id,
                    "run_interrupted",
                    {
                        "reason": "daemon_instance_replaced",
                        "previous_daemon_instance_id": str(
                            run.get("daemon_instance_id") or ""
                        ),
                        "current_daemon_instance_id": str(
                            self.server.state.daemon_instance_id
                        ),
                    },
                )
                run = self.server.state.runtime_journal.update_run(
                    session_id,
                    run_id,
                    {
                        "status": "interrupted",
                        "terminal_error": "The daemon process that owned this run was replaced.",
                    },
                )
            events = self.server.state.runtime_journal.read_events(
                session_id,
                run_id,
                starting_after=data.get("starting_after") or 0,
            )
            self._send(
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
                self._send({"status": "error", "error": "Empty content"})
                return
            result = self.server.state.run_llm_sync(
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
            self._send({"status": "ok", "session_id": session_id, "result": result})
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
                self._send({"type": "error", "error": "Empty content"})
                return
            state = self.server.state
            state.touch()
            try:
                state.config_manager.load_config()
            except Exception as e:
                _log_daemon(f"send_message_stream load_config failed session_id={session_id} error={e}")
            idle_minutes = state.config_manager.get("daemon_idle_minutes", 10)
            state.idle_timeout = max(int(idle_minutes), 1) * 60
            messages = state.request_messages(session_id, data.get("messages"))
            sqlite_baseline = (
                state.chat_storage.get_messages(session_id)
                if state.chat_storage.has_conversation(session_id)
                else []
            )
            state.append_user_message_if_needed(
                messages,
                content,
                message_id=user_message_id,
                turn_id=turn_id,
                request_id=request_id,
            )
            state.ensure_runtime_run(
                session_id,
                request_id,
                turn_id,
                writer_owner,
                sqlite_baseline,
            )
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
                _log_daemon(f"send_message_stream client disconnected session_id={session_id} reason={reason}")
                state.runtime_journal.append_event(
                    session_id,
                    request_id,
                    "subscriber_detached",
                    {"reason": str(reason or "socket_closed")},
                )

            def send_stream(payload):
                event = state.runtime_journal.append_event(
                    session_id,
                    request_id,
                    str(payload.get("type") or "stream_event"),
                    payload,
                )
                if stream_closed.is_set():
                    return False
                try:
                    with stream_lock:
                        if stream_closed.is_set():
                            return False
                        wire_payload = dict(payload)
                        wire_payload["sequence"] = event["sequence"]
                        raw = (json.dumps(wire_payload, ensure_ascii=False) + "\n").encode("utf-8")
                        self.wfile.write(raw)
                        self.wfile.flush()
                    return True
                except Exception as e:
                    _log_daemon(f"send_stream write failed session_id={session_id} payload_type={payload.get('type')} error={e}")
                    detach_stream_due_to_disconnect(str(e))
                    return False

            result_holder = {}
            done = threading.Event()
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
            worker_holder["worker"] = worker
            worker.thinking_signal.connect(lambda text: send_stream({"type": "thinking", "delta": text}), Qt.DirectConnection)
            worker.content_signal.connect(lambda text: send_stream({"type": "content", "delta": text}), Qt.DirectConnection)
            worker.tool_call_signal.connect(lambda data: send_stream({"type": "tool_call", "data": data}), Qt.DirectConnection)
            worker.tool_result_signal.connect(lambda data: send_stream({"type": "tool_result", "data": data}), Qt.DirectConnection)
            worker.agent_state_signal.connect(lambda data: send_stream({"type": "agent_state", "data": data}), Qt.DirectConnection)
            worker.observability_signal.connect(lambda data: send_stream({"type": "observability", "data": data}), Qt.DirectConnection)
            worker.output_signal.connect(lambda text: send_stream({"type": "log", "data": text}), Qt.DirectConnection)
            worker.finished_signal.connect(on_finished, Qt.DirectConnection)

            def handle_interaction_request(payload):
                if QThread.currentThread() != worker:
                    return
                request_payload = dict(payload or {})
                request_payload["session_id"] = session_id
                send_stream({"type": "interaction_request", "data": request_payload})

            interaction_service.interaction_requested.connect(handle_interaction_request, Qt.DirectConnection)
            activated = state.set_active_worker(
                session_id,
                worker,
                turn_id=turn_id,
                run_id=request_id,
            )
            if activated:
                try:
                    _log_daemon(f"send_message_stream worker_starting session_id={session_id} turn_id={turn_id}")
                    send_stream({"type": "turn_started", "turn_id": turn_id})
                    worker.start()
                    _log_daemon(
                        f"send_message_stream worker_started session_id={session_id} "
                        f"turn_id={turn_id} is_running={worker.isRunning()}"
                    )
                    done.wait()
                    if not worker.wait(2000):
                        state.detach_worker_until_finished(
                            session_id,
                            worker,
                            reason="stream_closed" if stream_closed.is_set() else "finished_wait_timeout",
                        )
                finally:
                    try:
                        interaction_service.interaction_requested.disconnect(handle_interaction_request)
                    except Exception as e:
                        _log_daemon(f"disconnect interaction bridge failed session_id={session_id} error={e}")
            else:
                try:
                    interaction_service.interaction_requested.disconnect(handle_interaction_request)
                except Exception as e:
                    _log_daemon(f"disconnect interaction bridge failed session_id={session_id} error={e}")
                result_holder["result"] = {
                    "error": "Run interrupted by user.",
                    "generated_messages": [],
                    "turn_id": turn_id,
                    "request_id": request_id,
                }
            result = result_holder.get("result") or {"error": "No response"}
            latest_run = state.runtime_journal.get_run(session_id, request_id) or {}
            if latest_run.get("stop_requested"):
                result = {
                    "error": "Run interrupted by user.",
                    "generated_messages": result.get("generated_messages", [])
                    if isinstance(result, dict)
                    else [],
                    "turn_id": turn_id,
                    "request_id": request_id,
                }
            provider_succeeded = not (isinstance(result, dict) and "error" in result)
            terminal_status = (
                "completed"
                if provider_succeeded
                else ("interrupted" if latest_run.get("stop_requested") else "failed")
            )
            _log_daemon(
                f"send_message_stream result session_id={session_id} turn_id={turn_id} "
                f"keys={sorted(list(result.keys())) if isinstance(result, dict) else []} "
                f"has_error={isinstance(result, dict) and 'error' in result}"
            )
            if provider_succeeded:
                finalizing_record = state.runtime_journal.update_run(
                    session_id,
                    request_id,
                    {
                        "status": "finalizing",
                        "terminal_error": "",
                        "final_result": result if isinstance(result, dict) else {},
                    },
                )
                if str(finalizing_record.get("status") or "") == "interrupted":
                    result = {
                        "error": "Run interrupted by user.",
                        "generated_messages": result.get("generated_messages", [])
                        if isinstance(result, dict)
                        else [],
                        "turn_id": turn_id,
                        "request_id": request_id,
                    }
                    provider_succeeded = False
                    terminal_status = "interrupted"
                else:
                    state.runtime_journal.append_event(
                        session_id,
                        request_id,
                        "finalizing",
                        {"source": "daemon_stream"},
                    )

            terminal_record = state.runtime_journal.update_run(
                session_id,
                request_id,
                {
                    "status": terminal_status,
                    "terminal_error": (
                        str(result.get("error") or "")
                        if isinstance(result, dict) and not provider_succeeded
                        else ""
                    ),
                    "final_result": result if isinstance(result, dict) else {},
                },
            )
            terminal_status = str(terminal_record.get("status") or terminal_status)
            if terminal_status == "interrupted" and not (
                isinstance(result, dict) and "error" in result
            ):
                result = {
                    "error": "Run interrupted by user.",
                    "generated_messages": result.get("generated_messages", [])
                    if isinstance(result, dict)
                    else [],
                    "turn_id": turn_id,
                    "request_id": request_id,
                }
                provider_succeeded = False
            state.runtime_journal.append_event(
                session_id,
                request_id,
                "terminal",
                {"status": terminal_status, "source": "daemon_stream"},
            )
            if isinstance(result, dict):
                result["_runtime_terminal"] = terminal_status
            state.clear_active_worker(
                session_id,
                expected_worker=worker,
                expected_run_id=request_id,
            )

            postprocess_error = ""
            try:
                state.append_worker_result_messages(
                    session_id,
                    messages,
                    result,
                    source="daemon_stream_result",
                )
                messages[:] = state._normalize_persistable_messages(
                    session_id,
                    messages,
                    source="daemon_stream_finalize",
                )
            except Exception as exc:
                postprocess_error = str(exc)
                _log_daemon(
                    f"send_message_stream postprocess failed session_id={session_id} "
                    f"run_id={request_id} error={exc}"
                )

            state.runtime_journal.update_run(
                session_id,
                request_id,
                {
                    "postprocess_status": "failed" if postprocess_error else "completed",
                    "postprocess_error": postprocess_error,
                },
            )
            daemon_commit_succeeded = False
            commit_error = postprocess_error
            if not postprocess_error and ui_owned_history:
                if provider_succeeded:
                    try:
                        state.runtime_journal.mark_pending_commit(
                            session_id,
                            request_id,
                            messages,
                            title=_compute_session_title(messages),
                        )
                        _log_daemon(
                            f"send_message_stream pending_commit session_id={session_id} "
                            f"run_id={request_id} writer_owner={writer_owner}"
                        )
                    except RuntimeJournalError as exc:
                        stopped_run = state.runtime_journal.get_run(
                            session_id,
                            request_id,
                        ) or {}
                        if not stopped_run.get("stop_requested"):
                            commit_error = str(exc)
                            _log_daemon(
                                f"send_message_stream pending_commit failed "
                                f"session_id={session_id} run_id={request_id} error={exc}"
                            )
                        else:
                            _log_daemon(
                                f"send_message_stream pending_commit rejected_interrupted "
                                f"session_id={session_id} run_id={request_id}"
                            )
            elif not postprocess_error and not ui_owned_history:
                try:
                    daemon_commit_succeeded = state.save_session(
                        session_id,
                        run_id=request_id,
                        acknowledge=False,
                    )
                    if not daemon_commit_succeeded:
                        commit_error = "SQLite conversation save did not commit."
                except Exception as exc:
                    commit_error = str(exc)
                    _log_daemon(
                        f"send_message_stream sqlite commit failed session_id={session_id} "
                        f"run_id={request_id} error={exc}"
                    )
            if commit_error:
                state.runtime_journal.update_run(
                    session_id,
                    request_id,
                    {
                        "commit_status": "failed",
                        "commit_error": commit_error,
                    },
                )
                state.runtime_journal.append_event(
                    session_id,
                    request_id,
                    "commit_failed",
                    {"error": commit_error},
                )
            elif provider_succeeded:
                state.runtime_journal.update_run(
                    session_id,
                    request_id,
                    {"commit_status": "pending" if ui_owned_history else "completed"},
                )
            send_stream({"type": "final", "result": result})
            if daemon_commit_succeeded:
                state.runtime_journal.acknowledge_commit(
                    session_id,
                    request_id,
                    messages,
                )
            state.touch()
            return
        if action == "stop_session":
            session_id = data.get("session_id")
            if not session_id:
                self._send({"status": "error", "error": "Missing session_id"})
                return
            expected_run_id = str(data.get("run_id") or "")
            stopped = self.server.state.stop_session(
                session_id,
                expected_run_id=expected_run_id,
            )
            self._send({
                "status": "ok",
                "stopped": stopped,
                "run_id": expected_run_id,
            })
            return
        if action == "steer_message":
            session_id = data.get("session_id")
            expected_turn_id = data.get("expected_turn_id")
            message = data.get("message")
            if not session_id or expected_turn_id in (None, ""):
                self._send({"status": "error", "error": "Missing session_id or expected_turn_id"})
                return
            result = self.server.state.steer_session(session_id, expected_turn_id, message)
            self._send({"status": "ok", **result})
            return
        if action == "update_guidance":
            session_id = data.get("session_id")
            expected_turn_id = data.get("expected_turn_id")
            message_id = data.get("message_id")
            message = data.get("message")
            if not session_id or expected_turn_id in (None, "") or not message_id:
                self._send({"status": "error", "error": "Missing guidance update fields"})
                return
            result = self.server.state.update_guidance_session(
                session_id,
                expected_turn_id,
                message_id,
                message,
            )
            self._send({"status": "ok", **result})
            return
        if action == "delete_guidance":
            session_id = data.get("session_id")
            expected_turn_id = data.get("expected_turn_id")
            message_id = data.get("message_id")
            if not session_id or expected_turn_id in (None, "") or not message_id:
                self._send({"status": "error", "error": "Missing guidance delete fields"})
                return
            result = self.server.state.delete_guidance_session(
                session_id,
                expected_turn_id,
                message_id,
            )
            self._send({"status": "ok", **result})
            return
        if action == "refresh_skills":
            try:
                event = SkillChangeEvent.create(
                    data.get("change_action") or "updated",
                    data.get("skill_names") or [],
                    source=data.get("source") or "ui",
                    session_id=data.get("session_id") or "",
                )
                applied = self.server.state.skill_catalog.publish_change(event)
                self._send({"status": "ok", "event": applied.to_dict()})
            except Exception as exc:
                self._send({"status": "error", "error": str(exc)})
            return
        if action == "respond_interaction":
            request_id = data.get("request_id")
            result = data.get("result")
            if not request_id:
                self._send({"status": "error", "error": "Missing request_id"})
                return
            resolved = interaction_service.resolve_request(request_id, result)
            self._send({"status": "ok", "resolved": resolved})
            return
        if action == "get_pending_interaction":
            session_id = data.get("session_id")
            if not session_id:
                self._send({"status": "error", "error": "Missing session_id"})
                return
            pending = interaction_service.get_pending_request(session_id)
            self._send({"status": "ok", "pending": pending})
            return
        if action == "shutdown":
            self._send({"status": "ok"})
            self.server.shutdown_requested = True
            return
        self._send({"status": "error", "error": "Unknown action"})

    def _send(self, payload):
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(raw)


class DaemonServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, state):
        super().__init__(server_address, handler_class)
        self.state = state
        self.shutdown_requested = False


def request_idle_daemon_shutdown(client, expected_ping, wait_timeout=2.0):
    """Gracefully stop one proven-idle mismatched daemon instance.

    The PID and signature are checked again through ``status`` immediately
    before shutdown so a port ownership change cannot stop a different process.
    Unknown activity state is treated as busy rather than guessed safe.
    """
    expected_ping = expected_ping if isinstance(expected_ping, dict) else {}
    expected_pid = int(expected_ping.get("pid") or 0)
    expected_signature = str(expected_ping.get("signature") or "").strip()
    result = {
        "stopped": False,
        "reason": "",
        "remote_pid": expected_pid,
        "remote_signature": expected_signature,
        "active_runs": None,
    }
    status = client.status()
    if not isinstance(status, dict) or status.get("status") != "ok":
        result["reason"] = "status_unavailable"
        return result
    status_pid = int(status.get("pid") or 0)
    status_signature = str(status.get("signature") or "").strip()
    result["remote_pid"] = status_pid or expected_pid
    result["remote_signature"] = status_signature or expected_signature
    if not expected_pid or status_pid != expected_pid:
        result["reason"] = "identity_changed"
        return result
    if not expected_signature or status_signature != expected_signature:
        result["reason"] = "identity_changed"
        return result
    if "active_runs" not in status:
        result["reason"] = "activity_unknown"
        return result
    try:
        active_runs = int(status.get("active_runs"))
    except (TypeError, ValueError):
        result["reason"] = "activity_unknown"
        return result
    result["active_runs"] = active_runs
    if active_runs != 0:
        result["reason"] = "active_runs"
        return result
    response = client.shutdown()
    if not isinstance(response, dict) or response.get("status") != "ok":
        result["reason"] = "shutdown_rejected"
        return result
    deadline = time.monotonic() + max(0.1, float(wait_timeout or 0.0))
    while time.monotonic() < deadline:
        time.sleep(0.05)
        current_ping = client.ping()
        current_pid = int(
            (current_ping.get("pid") if isinstance(current_ping, dict) else 0) or 0
        )
        if current_ping is None or current_pid != expected_pid:
            result["stopped"] = True
            result["reason"] = "stopped"
            return result
    result["reason"] = "shutdown_timeout"
    return result


class DaemonClient:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=3, send_timeout=600):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.send_timeout = send_timeout

    def _request(self, payload, timeout=None):
        effective_timeout = self.timeout if timeout is None else timeout
        with socket.create_connection((self.host, self.port), timeout=effective_timeout) as sock:
            sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        if not data:
            return None
        return json.loads(data.decode("utf-8"))

    def ping(self):
        try:
            resp = self._request({"action": "ping"})
        except Exception:
            return None
        return resp if resp and resp.get("status") == "ok" else None

    def status(self):
        try:
            return self._request({"action": "status"})
        except Exception:
            return None

    def shutdown(self):
        try:
            return self._request({"action": "shutdown"})
        except Exception:
            return None

    def send_message(
        self,
        session_id,
        content,
        workspace_dir=None,
        run_context=None,
        messages=None,
        writer_owner=None,
    ):
        payload = {
            "action": "send_message",
            "session_id": session_id,
            "content": content,
            "workspace_dir": workspace_dir,
            "run_context": normalize_run_context(run_context),
        }
        if writer_owner:
            payload["writer_owner"] = str(writer_owner)
        if messages:
            payload["messages"] = messages
        return self._request(
            payload,
            timeout=self.send_timeout
        )

    def send_message_stream(
        self,
        session_id,
        content,
        workspace_dir=None,
        run_context=None,
        messages=None,
        writer_owner=None,
    ):
        sock = socket.create_connection((self.host, self.port), timeout=self.send_timeout)
        try:
            payload = {
                "action": "send_message_stream",
                "session_id": session_id,
                "content": content,
                "workspace_dir": workspace_dir,
                "run_context": normalize_run_context(run_context),
            }
            if writer_owner:
                payload["writer_owner"] = str(writer_owner)
            if messages:
                payload["messages"] = messages
            sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            with sock.makefile("r", encoding="utf-8") as reader:
                for line in reader:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception as e:
                        _log_daemon(f"send_message_stream json decode failed session_id={session_id} line_len={len(line)} error={e}")
                        continue
        finally:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception as e:
                _log_daemon(f"send_message_stream socket shutdown failed session_id={session_id} error={e}")
            try:
                sock.close()
            except Exception as e:
                _log_daemon(f"send_message_stream socket close failed session_id={session_id} error={e}")
    
    def stop_session(self, session_id, run_id=""):
        payload = {"action": "stop_session", "session_id": session_id}
        if run_id:
            payload["run_id"] = str(run_id)
        return self._request(payload)

    def attach_run(self, session_id, run_id, starting_after=0):
        return self._request(
            {
                "action": "attach_run",
                "session_id": str(session_id or ""),
                "run_id": str(run_id or ""),
                "starting_after": int(starting_after or 0),
            }
        )

    def steer_message(self, session_id, expected_turn_id, message):
        try:
            return self._request(
                {
                    "action": "steer_message",
                    "session_id": session_id,
                    "expected_turn_id": str(expected_turn_id or ""),
                    "message": message,
                }
            )
        except Exception:
            return None

    def update_guidance(self, session_id, expected_turn_id, message_id, message):
        try:
            return self._request(
                {
                    "action": "update_guidance",
                    "session_id": session_id,
                    "expected_turn_id": str(expected_turn_id or ""),
                    "message_id": str(message_id or ""),
                    "message": message,
                }
            )
        except Exception:
            return None

    def delete_guidance(self, session_id, expected_turn_id, message_id):
        try:
            return self._request(
                {
                    "action": "delete_guidance",
                    "session_id": session_id,
                    "expected_turn_id": str(expected_turn_id or ""),
                    "message_id": str(message_id or ""),
                }
            )
        except Exception:
            return None

    def refresh_skills(self, skill_names, action="updated", source="ui", session_id=""):
        return self._request(
            {
                "action": "refresh_skills",
                "skill_names": list(skill_names or []),
                "change_action": action,
                "source": source,
                "session_id": session_id,
            },
            timeout=self.send_timeout,
        )
    
    def respond_interaction(self, request_id, result):
        try:
            return self._request({"action": "respond_interaction", "request_id": request_id, "result": result})
        except Exception:
            return None

    def get_pending_interaction(self, session_id):
        try:
            return self._request({"action": "get_pending_interaction", "session_id": session_id})
        except Exception:
            return None

    def shutdown(self):
        try:
            return self._request({"action": "shutdown"})
        except Exception:
            return None


def run_daemon(host=DEFAULT_HOST, port=DEFAULT_PORT):
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    config_manager = ConfigManager()
    state = DaemonState(config_manager)
    server = DaemonServer((host, port), DaemonRequestHandler, state)

    def serve():
        while not server.shutdown_requested:
            server.handle_request()
        server.server_close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    timer = QTimer()
    timer.setInterval(5000)
    timer.timeout.connect(state.maybe_suspend)
    timer.start()

    shutdown_timer = QTimer()
    shutdown_timer.setInterval(100)
    shutdown_timer.timeout.connect(
        lambda: _poll_daemon_shutdown(app, server)
    )
    shutdown_timer.start()

    try:
        app.exec()
    finally:
        shutdown_timer.stop()
        timer.stop()
        server.shutdown_requested = True
        state.skill_catalog.stop_watching()
        server.server_close()
        thread.join(timeout=2)


def _poll_daemon_shutdown(app, server):
    """Quit from the Qt thread once the socket server accepts shutdown."""
    if not bool(getattr(server, "shutdown_requested", False)):
        return False
    app.quit()
    return True
