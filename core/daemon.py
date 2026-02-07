import json
import os
import socket
import socketserver
import threading
import time
import uuid
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, Qt
from core.agent import LLMWorker
from core.chat_storage import ChatStorage
from core.config_manager import ConfigManager
from core.interaction import bridge


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 23333


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
        with self.lock:
            self.sessions[session_id] = messages
        return messages

    def save_session(self, session_id):
        with self.lock:
            messages = self.sessions.get(session_id, [])
        title = _compute_session_title(messages)
        self.chat_storage.save_conversation(session_id, messages, title=title)

    def run_llm_sync(self, session_id, user_text, workspace_dir=None):
        self.touch()
        try:
            self.config_manager.load_config()
        except Exception:
            pass
        idle_minutes = self.config_manager.get("daemon_idle_minutes", 10)
        self.idle_timeout = max(int(idle_minutes), 1) * 60
        messages = self.get_session_messages(session_id)
        messages.append({"role": "user", "content": user_text})
        result_holder = {}
        loop = QEventLoop()

        def on_finished(result):
            result_holder["result"] = result
            loop.quit()

        worker = LLMWorker(messages, self.config_manager, workspace_dir)
        worker.finished_signal.connect(on_finished)
        worker.start()
        loop.exec()
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
            except Exception:
                pass
            idle_minutes = state.config_manager.get("daemon_idle_minutes", 10)
            state.idle_timeout = max(int(idle_minutes), 1) * 60
            messages = state.get_session_messages(session_id)
            messages.append({"role": "user", "content": content})
            stream_lock = threading.Lock()

            def send_stream(payload):
                try:
                    with stream_lock:
                        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                        self.wfile.write(raw)
                        self.wfile.flush()
                except Exception:
                    pass

            result_holder = {}
            done = threading.Event()

            def on_finished(result):
                result_holder["result"] = result
                send_stream({"type": "final", "result": result})
                done.set()

            worker = LLMWorker(messages, state.config_manager, workspace_dir)
            worker.thinking_signal.connect(lambda text: send_stream({"type": "thinking", "delta": text}), Qt.DirectConnection)
            worker.content_signal.connect(lambda text: send_stream({"type": "content", "delta": text}), Qt.DirectConnection)
            worker.tool_call_signal.connect(lambda data: send_stream({"type": "tool_call", "data": data}), Qt.DirectConnection)
            worker.tool_result_signal.connect(lambda data: send_stream({"type": "tool_result", "data": data}), Qt.DirectConnection)
            worker.output_signal.connect(lambda text: send_stream({"type": "log", "data": text}), Qt.DirectConnection)
            worker.finished_signal.connect(on_finished, Qt.DirectConnection)
            worker.start()
            done.wait()
            worker.wait(2000)
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

    def shutdown(self):
        try:
            return self._request({"action": "shutdown"})
        except Exception:
            return None


def run_daemon(host=DEFAULT_HOST, port=DEFAULT_PORT):
    app = QCoreApplication([])
    config_manager = ConfigManager()
    state = DaemonState(config_manager)
    server = DaemonServer((host, port), DaemonRequestHandler, state)

    def auto_respond(_message):
        bridge.respond(False)

    bridge.request_confirmation_signal.connect(auto_respond)

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
