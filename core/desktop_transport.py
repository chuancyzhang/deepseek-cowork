"""Authenticated local desktop transport; a window never owns task lifetime."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hmac
import json
import logging
import os
from pathlib import Path
import queue
import socket
import socketserver
import threading
import time
import uuid

log = logging.getLogger("desktop.transport")
# A supported 25 MiB Office document expands to ~34 MiB in base64.
# Bound the envelope while leaving room for draft metadata and JSON escaping.
MAX_FRAME = 64 * 1024 * 1024


class StartupCancelled(RuntimeError):
    pass


class DesktopTransport:
    def __init__(self, directory, protocol):
        self.directory = Path(directory).resolve()
        self.descriptor = self.directory / "desktop-service.json"
        self.protocol = protocol
        self.token = uuid.uuid4().hex + uuid.uuid4().hex
        self.instance = uuid.uuid4().hex
        self.service = None
        self.thread = None
        self.stop_requested = threading.Event()
        self.startup_message = "正在启动本地服务…"
        self.last_progress = 0
        self.peers = set()
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="desktop-rpc")
        self.control_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="desktop-control")
        transport = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(10)
                try:
                    raw = self.rfile.readline(MAX_FRAME + 1)
                    if len(raw) > MAX_FRAME:
                        return
                    hello = json.loads(raw)
                    if hello.get("protocol") != protocol or not hmac.compare_digest(str(hello.get("token", "")), transport.token):
                        self.wfile.write(b'{"error":{"message":"Invalid desktop connection"}}\n')
                        return
                except (ValueError, OSError):
                    return
                self.request.settimeout(None)
                peer = _Peer(self.request)
                with transport.lock:
                    # Register and queue ready atomically relative to runtime events.
                    peer.send(transport.status())
                    transport.peers.add(peer)
                log.info("host_attached peers=%s", len(transport.peers))
                capacity = threading.BoundedSemaphore(64)
                control_capacity = threading.BoundedSemaphore(16)
                try:
                    while not peer.closed.is_set():
                        raw = self.rfile.readline(MAX_FRAME + 1)
                        if not raw or len(raw) > MAX_FRAME:
                            break
                        request = json.loads(raw)
                        if not isinstance(request, dict) or not request.get("id"):
                            break
                        if transport.service is not None and request.get("method") == "conversation.stop":
                            params = request.get("params") or {}
                            transport.service.state.cancel_tokens(str(params.get("conversation_id") or ""), str(params.get("run_id") or ""))
                            if not control_capacity.acquire(blocking=False):
                                peer.send({"id": request["id"], "error": {"code": "Busy", "message": "停止请求已提交，正在等待已有控制操作结束。"}})
                                continue
                            future = transport.control_executor.submit(transport.handle, peer, request)
                            future.add_done_callback(lambda _future: control_capacity.release())
                            continue
                        if not capacity.acquire(blocking=False):
                            peer.send({"id": request["id"], "error": {"code": "Busy", "message": "操作较多，请稍后重试。"}})
                            continue
                        future = transport.executor.submit(transport.handle, peer, request)
                        future.add_done_callback(lambda _future: capacity.release())
                except (ValueError, OSError):
                    pass
                finally:
                    with transport.lock:
                        transport.peers.discard(peer)
                    peer.close()
                    log.info("host_detached peers=%s tasks_continue=true", len(transport.peers))

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = False

        self.server = Server(("127.0.0.1", 0), Handler)

    def status(self):
        return {"event": "ready" if self.service is not None else "starting", "protocol": self.protocol,
                "instance": self.instance, "message": self.startup_message}

    def progress(self, message):
        if self.stop_requested.is_set():
            raise StartupCancelled("启动已停止，已有数据仍保留。")
        if message == self.startup_message and self.last_progress:
            return
        now = time.monotonic()
        self.startup_message = message
        if now - self.last_progress < 1:
            return
        self.last_progress = now
        self.publish(self.status())

    def start(self, service=None):
        if service is not None:
            with self.lock:
                self.service = service
                for peer in tuple(self.peers):
                    peer.send(self.status())
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self.server.serve_forever, name="desktop-transport", daemon=True)
        self.thread.start()
        value = {"protocol": self.protocol, "pid": os.getpid(), "port": self.server.server_address[1],
                 "token": self.token, "instance": self.instance, "data_dir": str(self.directory)}
        temp = self.descriptor.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as output:
            os.chmod(temp, 0o600)
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, self.descriptor)

    def publish(self, value):
        with self.lock:
            for peer in tuple(self.peers):
                peer.send(value)

    def handle(self, peer, request):
        method = str(request.get("method") or "")
        try:
            if self.service is None:
                if method != "app.shutdown":
                    raise RuntimeError("本地服务正在启动，请等待工作台恢复。")
                self.stop_requested.set()
                log.info("startup_stop_requested")
                result = {"stopping": True}
            else:
                result = self.service.dispatch(method, request.get("params"))
            peer.send({"id": request["id"], "result": result})
        except Exception as exc:
            log.exception("rpc_error method=%s request=%s", method, request["id"])
            peer.send({"id": request["id"], "error": {"code": type(exc).__name__, "message": str(exc)}})

    def close(self):
        if self.thread is not None:
            self.server.shutdown()
        self.server.server_close()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.control_executor.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            for peer in tuple(self.peers):
                peer.close()
        try:
            descriptor = json.loads(self.descriptor.read_text(encoding="utf-8"))
            if descriptor.get("instance") == self.instance:
                self.descriptor.unlink()
        except FileNotFoundError:
            pass


class _Peer:
    def __init__(self, connection):
        self.connection = connection
        self.queue = queue.Queue(maxsize=1024)
        self.closed = threading.Event()
        threading.Thread(target=self.write, name="desktop-events", daemon=True).start()

    def send(self, value):
        if self.closed.is_set():
            return
        try:
            self.queue.put_nowait(value)
        except queue.Full:
            # The durable journal is authoritative. A slow view reconnects and
            # reloads it; never block an executing worker behind a window.
            log.warning("host_event_backpressure reconnect_required=true")
            self.close()

    def write(self):
        while not self.closed.is_set():
            value = self.queue.get()
            if value is None:
                return
            try:
                self.connection.sendall((json.dumps(value, ensure_ascii=False, default=str) + "\n").encode())
            except OSError:
                self.close()
                return

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
