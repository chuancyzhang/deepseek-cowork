import hashlib
import json
import os

import time

from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from core.env_utils import get_app_data_dir


DEFAULT_UI_COMMAND = "activate"


def build_ui_server_name(identity_path=None):
    """Return a stable, install-scoped local server name for the UI instance."""
    if identity_path is None:
        identity_path = get_app_data_dir()
    normalized = os.path.normcase(os.path.abspath(identity_path or ""))
    digest = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"deepseek-cowork-ui-{digest}"


def notify_existing_ui(server_name, command=DEFAULT_UI_COMMAND, timeout_ms=500, *, update_request=None):
    """Send a command to an already-running UI instance if one is listening."""
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    app = QCoreApplication.instance()
    if app:
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        connected = False
        while time.monotonic() <= deadline:
            app.processEvents()
            if socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
                connected = True
                break
            time.sleep(0.01)
    else:
        connected = socket.waitForConnected(timeout_ms)
    if not connected:
        socket.abort()
        return False
    message = {"command": command}
    if update_request is not None:
        message["update_startup"] = update_request
    payload = json.dumps(message, ensure_ascii=False) + "\n"
    socket.write(payload.encode("utf-8"))
    ok = socket.waitForBytesWritten(timeout_ms)
    if ok and update_request is not None:
        # Bytes written is not proof that the loading UI accepted the handoff.
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        response = b""
        ok = False
        while time.monotonic() <= deadline:
            if app:
                app.processEvents()
            response += bytes(socket.readAll())
            if b"\n" in response:
                try:
                    ok = json.loads(response.split(b"\n", 1)[0]).get("accepted") is True
                except (ValueError, AttributeError):
                    pass
                break
            if socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                break
            socket.waitForReadyRead(20)
    socket.disconnectFromServer()
    if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        socket.waitForDisconnected(timeout_ms)
    return bool(ok)


def notify_existing_ui_with_retries(
    server_name,
    command=DEFAULT_UI_COMMAND,
    total_timeout_ms=5000,
    interval_ms=100,
    per_attempt_timeout_ms=200,
    *, update_request=None,
):
    """Retry activation while the first UI process is still booting its local server."""
    deadline = time.monotonic() + max(total_timeout_ms, 0) / 1000
    while True:
        if notify_existing_ui(server_name, command=command, timeout_ms=per_attempt_timeout_ms, update_request=update_request):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(interval_ms, 0) / 1000)


class UiSingleInstanceServer(QObject):
    """Local IPC server used by later exe launches to activate the first UI."""

    def __init__(self, server_name, on_activate, parent=None):
        super().__init__(parent)
        self.server_name = server_name
        self.on_activate = on_activate
        self.server = QLocalServer(self)
        self._sockets = []
        self._buffers = {}
        self._update_requests = {}
        self._on_update_startup = None
        self.server.newConnection.connect(self._handle_new_connection)

    def start(self):
        if self.server.listen(self.server_name):
            return True
        QLocalServer.removeServer(self.server_name)
        return self.server.listen(self.server_name)

    def stop(self):
        for socket in list(self._sockets):
            try:
                socket.disconnectFromServer()
            except Exception:
                pass
        self._sockets = []
        self._buffers.clear()
        self._update_requests.clear()
        self.server.close()
        QLocalServer.removeServer(self.server_name)

    def set_update_startup_handler(self, handler):
        """Called only after the owning UI has completed startup hydration."""
        self._on_update_startup = handler
        pending, self._update_requests = self._update_requests, {}
        for request in pending.values():
            handler(request)

    def _handle_new_connection(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self._sockets.append(socket)
            socket.readyRead.connect(lambda sock=socket: self._read_socket(sock))
            socket.disconnected.connect(lambda sock=socket: self._discard_socket(sock))
            if socket.bytesAvailable():
                self._read_socket(socket)

    def _read_socket(self, socket):
        raw = self._buffers.get(socket, b"") + bytes(socket.readAll())
        if len(raw) > 32768:
            socket.abort()
            return
        if b"\n" not in raw and raw.strip() != b"activate":
            self._buffers[socket] = raw
            return
        self._buffers.pop(socket, None)
        raw = raw.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
        command = ""
        payload = {}
        try:
            payload = json.loads(raw) if raw else {}
            command = payload.get("command") or ""
        except Exception:
            command = raw
        if command == DEFAULT_UI_COMMAND:
            request = payload.get("update_startup")
            if request is not None:
                accepted = isinstance(request, dict) and isinstance(request.get("id"), str)
                if accepted:
                    if self._on_update_startup:
                        accepted = bool(self._on_update_startup(request))
                    elif len(self._update_requests) < 16 or request["id"] in self._update_requests:
                        self._update_requests[request["id"]] = request
                    else:
                        accepted = False
                socket.write((json.dumps({"accepted": accepted}) + "\n").encode("utf-8"))
                socket.flush()
            if self.on_activate:
                self.on_activate()
        socket.disconnectFromServer()

    def _discard_socket(self, socket):
        self._buffers.pop(socket, None)
        if socket in self._sockets:
            self._sockets.remove(socket)
        socket.deleteLater()
