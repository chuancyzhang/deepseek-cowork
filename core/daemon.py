import json
import os
import socket
import socketserver
import threading
import time
import uuid
from PySide6.QtCore import QEventLoop, QTimer, Qt, QThread
from PySide6.QtWidgets import QApplication
from core.agent import LLMWorker
from core.chat_storage import ChatStorage
from core.config_manager import ConfigManager
from core.env_utils import get_app_data_dir
from core.im_session_key import parse_im_session_key
from core.interaction import bridge


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 23333

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
        self.sessions = {}
        self.active_workers = {}
        self.pending_confirmations = {}
        self.lock = threading.Lock()
        self.suspended = False
        self.last_activity = time.time()
        idle_minutes = config_manager.get("daemon_idle_minutes", 10)
        self.idle_timeout = max(int(idle_minutes), 1) * 60

    def touch(self):
        self.last_activity = time.time()
        if self.suspended:
            self.suspended = False

    def maybe_suspend(self):
        if self.suspended:
            return
        if time.time() - self.last_activity < self.idle_timeout:
            return
        with self.lock:
            for session_id, messages in list(self.sessions.items()):
                title = _compute_session_title(messages)
                self.chat_storage.save_conversation(session_id, messages, title=title)
            self.sessions = {}
            self.suspended = True

    def get_session_messages(self, session_id):
        with self.lock:
            if session_id in self.sessions:
                return self.sessions[session_id]
        if self.chat_storage.has_conversation(session_id):
            messages = self.chat_storage.get_messages(session_id)
        else:
            messages = []
        messages = self._dedupe_consecutive_user_messages(messages)
        with self.lock:
            self.sessions[session_id] = messages
        return messages

    def _dedupe_consecutive_user_messages(self, messages):
        if not isinstance(messages, list):
            return []
        deduped = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if (
                deduped
                and msg.get("role") == "user"
                and deduped[-1].get("role") == "user"
                and (msg.get("content") or "") == (deduped[-1].get("content") or "")
            ):
                continue
            deduped.append(msg)
        return deduped

    def append_user_message_if_needed(self, messages, content):
        if not isinstance(messages, list):
            return
        text = (content or "").strip()
        if not text:
            return
        if messages:
            last = messages[-1]
            if (
                isinstance(last, dict)
                and last.get("role") == "user"
                and (last.get("content") or "").strip() == text
            ):
                return
        messages.append({"role": "user", "content": content})

    def save_session(self, session_id):
        with self.lock:
            messages = self.sessions.get(session_id, [])
        title = _compute_session_title(messages)
        self.chat_storage.save_conversation(session_id, messages, title=title)
    
    def set_active_worker(self, session_id, worker):
        with self.lock:
            self.active_workers[session_id] = worker
    
    def clear_active_worker(self, session_id):
        with self.lock:
            if session_id in self.active_workers:
                del self.active_workers[session_id]
    
    def stop_session(self, session_id):
        with self.lock:
            worker = self.active_workers.get(session_id)
            pending_ids = [cid for cid, entry in self.pending_confirmations.items() if entry.get("session_id") == session_id]
        for cid in pending_ids:
            self.resolve_confirmation(cid, False)
        if worker:
            try:
                worker.stop()
            except Exception as e:
                _log_daemon(f"stop_session worker.stop failed session_id={session_id} error={e}")
            return True
        return False
    
    def create_confirmation(self, confirm_id, session_id):
        event = threading.Event()
        with self.lock:
            self.pending_confirmations[confirm_id] = {
                "event": event,
                "result": False,
                "session_id": session_id
            }
        return event
    
    def resolve_confirmation(self, confirm_id, result):
        with self.lock:
            entry = self.pending_confirmations.get(confirm_id)
        if not entry:
            return False
        entry["result"] = result
        entry["event"].set()
        return True
    
    def wait_for_confirmation(self, confirm_id, timeout=None):
        with self.lock:
            entry = self.pending_confirmations.get(confirm_id)
        if not entry:
            return None
        resolved = entry["event"].wait(timeout)
        with self.lock:
            entry = self.pending_confirmations.pop(confirm_id, entry)
        if not resolved:
            return None
        return entry.get("result", False)

    def _run_worker_once(self, session_id, worker_messages, workspace_dir):
        result_holder = {}
        loop = QEventLoop()

        def on_finished(result):
            result_holder["result"] = result
            self.clear_active_worker(session_id)
            loop.quit()

        worker = LLMWorker(worker_messages, self.config_manager, workspace_dir)
        worker.finished_signal.connect(on_finished)
        self.set_active_worker(session_id, worker)
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
            elif role == "tool":
                if len(actions) < 10:
                    actions.append(f"工具结果: {content}")

        def _section(title, items):
            if not items:
                return f"{title}:\n- 暂无"
            return title + ":\n" + "\n".join([f"- {item}" for item in items[:10]])

        blocks = [
            _section("今日目标", goals),
            _section("已完成动作", actions),
            _section("关键决策与约束", decisions),
            _section("未决问题与待确认项", pending),
            _section("用户偏好与约定", preferences),
        ]
        return "\n\n".join(blocks).strip()

    def _compress_summary_text(self, summary_text, max_chars):
        text = (summary_text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    def _build_overflow_retry_messages(self, session_id, messages):
        enabled = self.config_manager.get("im_context_compression_enabled", True)
        if enabled is False:
            return None
        binding = self._get_im_binding_for_session(session_id)
        if not binding:
            return None
        keep_turns = self.config_manager.get("im_summary_recent_keep_turns", 12)
        try:
            keep_turns = int(keep_turns)
        except Exception:
            keep_turns = 12
        keep_turns = max(2, keep_turns)
        if len(messages) <= keep_turns:
            return None
        compress_end = len(messages) - keep_turns - 1
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
            "content": f"Daily Summary ({binding['summary_date']}):\n{merged_summary}",
        }
        tail_messages = messages[compress_end + 1 :]
        retry_messages = [summary_message] + tail_messages
        _log_daemon(
            "context_overflow_retry_prepare "
            + json.dumps(
                {
                    "session_id": session_id,
                    "hit_context_overflow": True,
                    "compressed_message_count": compress_end + 1,
                    "summary_date": binding["summary_date"],
                    "source_message_upto_pos": compress_end,
                },
                ensure_ascii=False,
            )
        )
        return retry_messages

    def run_llm_sync(self, session_id, user_text, workspace_dir=None):
        self.touch()
        try:
            self.config_manager.load_config()
        except Exception as e:
            _log_daemon(f"run_llm_sync load_config failed session_id={session_id} error={e}")
        idle_minutes = self.config_manager.get("daemon_idle_minutes", 10)
        self.idle_timeout = max(int(idle_minutes), 1) * 60
        messages = self.get_session_messages(session_id)
        self.append_user_message_if_needed(messages, user_text)
        result = self._run_worker_once(session_id, messages, workspace_dir)
        retry_once = self.config_manager.get("im_context_overflow_retry_once", True)
        if retry_once is not False and self._is_context_overflow_error(result):
            retry_messages = self._build_overflow_retry_messages(session_id, messages)
            if retry_messages:
                retry_result = self._run_worker_once(session_id, retry_messages, workspace_dir)
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
        if "error" not in result:
            generated_messages = result.get("generated_messages", [])
            if generated_messages:
                messages.extend(generated_messages)
            else:
                messages.append(
                    {
                        "role": result.get("role", "assistant"),
                        "content": result.get("content", ""),
                        "reasoning": result.get("reasoning", "")
                    }
                )
        self.save_session(session_id)
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
            self._send({"status": "ok", "pid": os.getpid()})
            return
        if action == "status":
            state = self.server.state
            self._send(
                {
                    "status": "ok",
                    "suspended": state.suspended,
                    "last_activity": state.last_activity,
                    "sessions": len(state.sessions)
                }
            )
            return
        if action == "send_message":
            session_id = data.get("session_id") or uuid.uuid4().hex
            content = data.get("content") or ""
            workspace_dir = data.get("workspace_dir")
            if not content:
                self._send({"status": "error", "error": "Empty content"})
                return
            result = self.server.state.run_llm_sync(session_id, content, workspace_dir)
            self._send({"status": "ok", "session_id": session_id, "result": result})
            return
        if action == "send_message_stream":
            session_id = data.get("session_id") or uuid.uuid4().hex
            content = data.get("content") or ""
            workspace_dir = data.get("workspace_dir")
            if not content:
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
            messages = state.get_session_messages(session_id)
            state.append_user_message_if_needed(messages, content)
            stream_lock = threading.Lock()

            def send_stream(payload):
                try:
                    with stream_lock:
                        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                        self.wfile.write(raw)
                        self.wfile.flush()
                except Exception as e:
                    _log_daemon(f"send_stream write failed session_id={session_id} payload_type={payload.get('type')} error={e}")

            result_holder = {}
            done = threading.Event()
            def on_finished(result):
                result_holder["result"] = result
                send_stream({"type": "final", "result": result})
                state.clear_active_worker(session_id)
                done.set()

            worker = LLMWorker(messages, state.config_manager, workspace_dir)
            worker.thinking_signal.connect(lambda text: send_stream({"type": "thinking", "delta": text}), Qt.DirectConnection)
            worker.content_signal.connect(lambda text: send_stream({"type": "content", "delta": text}), Qt.DirectConnection)
            worker.tool_call_signal.connect(lambda data: send_stream({"type": "tool_call", "data": data}), Qt.DirectConnection)
            worker.tool_result_signal.connect(lambda data: send_stream({"type": "tool_result", "data": data}), Qt.DirectConnection)
            worker.output_signal.connect(lambda text: send_stream({"type": "log", "data": text}), Qt.DirectConnection)
            worker.finished_signal.connect(on_finished, Qt.DirectConnection)

            def handle_confirmation_request(message):
                if QThread.currentThread() != worker:
                    return
                confirm_id = uuid.uuid4().hex
                state.create_confirmation(confirm_id, session_id)
                send_stream({"type": "confirm_request", "data": {"id": confirm_id, "message": message}})
                timeout_seconds = state.config_manager.get("confirmation_timeout_seconds", 120)
                try:
                    timeout_seconds = float(timeout_seconds)
                except Exception:
                    timeout_seconds = 120.0
                if timeout_seconds <= 0:
                    timeout_seconds = 120.0
                result = state.wait_for_confirmation(confirm_id, timeout=timeout_seconds)
                if result is None:
                    send_stream({"type": "error", "error": "Confirmation timed out. Conversation interrupted."})
                    state.stop_session(session_id)
                    bridge.respond(False)
                    return
                bridge.respond(result)

            bridge.request_confirmation_signal.connect(handle_confirmation_request, Qt.DirectConnection)
            state.set_active_worker(session_id, worker)
            try:
                worker.start()
                done.wait()
                worker.wait(2000)
            finally:
                try:
                    bridge.request_confirmation_signal.disconnect(handle_confirmation_request)
                except Exception as e:
                    _log_daemon(f"disconnect confirmation bridge failed session_id={session_id} error={e}")
            result = result_holder.get("result") or {"error": "No response"}
            if "error" not in result:
                generated_messages = result.get("generated_messages", [])
                if generated_messages:
                    messages.extend(generated_messages)
                else:
                    messages.append(
                        {
                            "role": result.get("role", "assistant"),
                            "content": result.get("content", ""),
                            "reasoning": result.get("reasoning", "")
                        }
                    )
            state.save_session(session_id)
            state.touch()
            return
        if action == "stop_session":
            session_id = data.get("session_id")
            if not session_id:
                self._send({"status": "error", "error": "Missing session_id"})
                return
            stopped = self.server.state.stop_session(session_id)
            self._send({"status": "ok", "stopped": stopped})
            return
        if action == "confirm_response":
            confirm_id = data.get("confirm_id")
            result = data.get("result")
            if not confirm_id:
                self._send({"status": "error", "error": "Missing confirm_id"})
                return
            resolved = self.server.state.resolve_confirmation(confirm_id, result)
            self._send({"status": "ok", "resolved": resolved})
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

    def send_message(self, session_id, content, workspace_dir=None):
        return self._request(
            {
                "action": "send_message",
                "session_id": session_id,
                "content": content,
                "workspace_dir": workspace_dir
            },
            timeout=self.send_timeout
        )

    def send_message_stream(self, session_id, content, workspace_dir=None):
        sock = socket.create_connection((self.host, self.port), timeout=self.send_timeout)
        try:
            payload = {
                "action": "send_message_stream",
                "session_id": session_id,
                "content": content,
                "workspace_dir": workspace_dir
            }
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
    
    def stop_session(self, session_id):
        try:
            return self._request({"action": "stop_session", "session_id": session_id})
        except Exception:
            return None
    
    def confirm_response(self, confirm_id, result):
        try:
            return self._request({"action": "confirm_response", "confirm_id": confirm_id, "result": result})
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
        QTimer.singleShot(0, app.quit)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    timer = QTimer()
    timer.setInterval(5000)
    timer.timeout.connect(state.maybe_suspend)
    timer.start()

    app.exec()
