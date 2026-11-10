"""A disconnected desktop must not interrupt, duplicate, or lose a real run."""
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import ThreadingHTTPServer

from test_desktop_service import Provider, ServiceClient, QA_ROOT, ROOT


class SocketClient:
    def __init__(self, descriptor):
        self.connection = socket.create_connection(("127.0.0.1", descriptor["port"]), timeout=10)
        self.connection.settimeout(None)
        self.reader = self.connection.makefile("r", encoding="utf-8")
        self.lines = queue.Queue()
        self.events = []
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()
        self.write({"token": descriptor["token"], "protocol": descriptor["protocol"]})
        self.until(lambda value: value.get("event") == "ready")

    def read(self):
        try:
            for line in self.reader:
                self.lines.put(json.loads(line))
        except OSError:
            pass

    def write(self, value):
        self.connection.sendall((json.dumps(value, ensure_ascii=False) + "\n").encode())

    until = ServiceClient.until
    wait_run = ServiceClient.wait_run

    def call(self, method, **params):
        identity = uuid.uuid4().hex
        self.write({"id": identity, "method": method, "params": params})
        reply = self.until(lambda value: value.get("id") == identity)
        if reply.get("error"):
            raise RuntimeError(reply["error"]["message"])
        return reply["result"]

    def close(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.thread.join(2)
        self.reader.close()
        self.connection.close()


class DesktopReconnectTests(unittest.TestCase):
    def test_detach_reconnect_preserves_running_task_and_one_submission(self):
        with tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            with (Path(directory) / "service.log").open("w", encoding="utf-8") as output:
                process = subprocess.Popen([sys.executable, "-X", "utf8", "-B", "-m", "core.desktop_service", "--data-dir", directory, "--serve"],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                    env={**os.environ, "TEMP": str(QA_ROOT), "TMP": str(QA_ROOT), "NO_PROXY": "127.0.0.1,localhost,::1"},
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                client = None
                try:
                    descriptor_path = Path(directory) / "desktop-service.json"
                    deadline = time.monotonic() + 40
                    while not descriptor_path.exists():
                        if process.poll() is not None or time.monotonic() > deadline:
                            self.fail("Service did not start; inspect isolated service.log")
                        time.sleep(.05)
                    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
                    with socket.create_connection(("127.0.0.1", descriptor["port"]), timeout=5) as untrusted:
                        untrusted.sendall(b'{"protocol":1,"token":"wrong-token"}\n')
                        self.assertIn(b'error', untrusted.recv(4096))
                    client = SocketClient(descriptor)
                    settings = client.call("settings.save_model", model_name="fixture", api_key="test-only", base_url=f"http://127.0.0.1:{server.server_address[1]}/v1")
                    cid = client.call("conversation.create")["id"]
                    params = dict(conversation_id=cid, request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex, text="SLOW reconnect", model_id=settings["selected_model_id"])
                    client.call("conversation.send", **params)
                    client.until(lambda value: value.get("data", {}).get("type") == "content")
                    client.close()
                    client = None
                    time.sleep(.4)
                    self.assertIsNone(process.poll(), "Closing the desktop killed its background task")
                    client = SocketClient(descriptor)
                    restored = client.call("conversation.get", conversation_id=cid)
                    self.assertEqual(restored["run"]["status"], "running")
                    self.assertTrue(any(event.get("payload", {}).get("type") == "content" for event in restored["events"]))
                    client.call("conversation.send", **params)
                    client.call("conversation.stop", conversation_id=cid, run_id=params["request_id"])
                    stopped = client.wait_run(cid)
                    self.assertEqual(stopped["run"]["status"], "interrupted")
                    self.assertEqual(sum(message["id"] == params["message_id"] for message in stopped["messages"]), 1)
                    client.call("app.shutdown")
                    process.wait(timeout=15)
                    self.assertFalse(descriptor_path.exists())
                finally:
                    if client:
                        client.close()
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
                    server.shutdown()
                    server.server_close()
