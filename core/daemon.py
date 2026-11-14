"""Socket adapters for the shared runtime; no execution state lives here."""
import json
import os
import socket
import socketserver
import time
from core.config_manager import ConfigManager
from core.clarify_mode import normalize_run_context
from core.runtime_coordinator import RuntimeCoordinator, get_runtime_signature, _log_daemon

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 23333
RUNTIME_PROTOCOL = 2
# The legacy entry point and desktop gateway share the same implementation.
DaemonState = RuntimeCoordinator


class DaemonRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline()
        if not line:
            return
        try:
            payload = json.loads(line)
        except (ValueError, UnicodeError):
            self._send({"status": "error", "error": "Invalid JSON"})
            return
        self.connection.settimeout(5)
        self.server.state.request(payload, self._send, self._disconnect)
        if self.server.state.shutdown_requested:
            self.server.shutdown_requested = True

    def _send(self, payload):
        self.wfile.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _disconnect(self, reason):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


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
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=3, send_timeout=600, desktop_token=""):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.send_timeout = send_timeout
        self.desktop_token = desktop_token

    def _request(self, payload, timeout=None):
        if self.desktop_token:
            payload = {**payload, "desktop_token": self.desktop_token, "runtime_protocol": RUNTIME_PROTOCOL}
        effective_timeout = self.timeout if timeout is None else timeout
        with socket.create_connection((self.host, self.port), timeout=effective_timeout) as sock:
            sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            data = bytearray()
            while not data.endswith(b"\n"):
                chunk = sock.recv(64 * 1024)
                if not chunk:
                    break
                data.extend(chunk)
        if not data:
            return None
        return json.loads(data.decode("utf-8"))

    def subagent_control(self, session_id, operation, **data):
        response = self._request(dict(data, action="subagent_control", session_id=session_id, operation=operation))
        if not response or response.get("status") != "ok":
            raise RuntimeError((response or {}).get("error") or "守护进程未返回结果")
        return response["result"]

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
            if self.desktop_token:
                payload["desktop_token"] = self.desktop_token
                payload["runtime_protocol"] = RUNTIME_PROTOCOL
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
        return self._request(payload, timeout=max(float(self.timeout or 0), 6.0))

    def attach_run(self, session_id, run_id, starting_after=0):
        return self._request(
            {
                "action": "attach_run",
                "session_id": str(session_id or ""),
                "run_id": str(run_id or ""),
                "starting_after": int(starting_after or 0),
            },
            # Recovery reads and verifies durable events, unlike a small ping.
            timeout=max(float(self.timeout or 0), 30.0),
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
            response = self._request({"action": "respond_interaction", "request_id": request_id, "result": result})
            if response is None:
                _log_daemon(f"interaction submit unconfirmed request_id={request_id} reason=empty_response")
            return response
        except Exception as exc:
            _log_daemon(f"interaction submit unconfirmed request_id={request_id} error_type={type(exc).__name__}")
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
    state = DaemonState(ConfigManager())
    server = DaemonServer((host, port), DaemonRequestHandler, state)
    server.timeout = 0.1
    next_maintenance = time.monotonic() + 5
    try:
        while not server.shutdown_requested:
            server.handle_request()
            if time.monotonic() >= next_maintenance:
                state.maybe_suspend()
                next_maintenance = time.monotonic() + 5
    finally:
        state.skill_catalog.stop_watching()
        server.server_close()
