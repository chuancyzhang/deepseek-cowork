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


def notify_existing_ui(server_name, command=DEFAULT_UI_COMMAND, timeout_ms=500):
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
    payload = json.dumps({"command": command}, ensure_ascii=False) + "\n"
    socket.write(payload.encode("utf-8"))
    ok = socket.waitForBytesWritten(timeout_ms)
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
):
    """Retry activation while the first UI process is still booting its local server."""
    deadline = time.monotonic() + max(total_timeout_ms, 0) / 1000
    while True:
        if notify_existing_ui(server_name, command=command, timeout_ms=per_attempt_timeout_ms):
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
        self.server.close()
        QLocalServer.removeServer(self.server_name)

    def _handle_new_connection(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self._sockets.append(socket)
            socket.readyRead.connect(lambda sock=socket: self._read_socket(sock))
            socket.disconnected.connect(lambda sock=socket: self._discard_socket(sock))
            if socket.bytesAvailable():
                self._read_socket(socket)

    def _read_socket(self, socket):
        raw = bytes(socket.readAll()).decode("utf-8", errors="replace").strip()
        command = ""
        try:
            payload = json.loads(raw) if raw else {}
            command = payload.get("command") or ""
        except Exception:
            command = raw
        if command == DEFAULT_UI_COMMAND and self.on_activate:
            self.on_activate()
        socket.disconnectFromServer()

    def _discard_socket(self, socket):
        if socket in self._sockets:
            self._sockets.remove(socket)
        socket.deleteLater()
