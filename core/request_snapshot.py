"""Optional diagnostic copies of final SDK arguments, never conversation state."""

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime


class RequestSnapshotStore:
    def __init__(self, history_dir):
        self.root = os.path.join(history_dir, "request_snapshots")

    def path(self, session_id):
        if not session_id:
            raise ValueError("缺少会话标识")
        key = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()
        return os.path.join(self.root, key + ".json")

    def available(self, session_id):
        path = self.path(session_id)
        return os.path.isfile(path) and not os.path.exists(path + ".pending")

    def capture(self, session_id, run_id, request_id, protocol, params):
        path = self.path(session_id)
        os.makedirs(self.root, exist_ok=True)
        # An unfinished/failed capture must not expose the preceding request as current.
        with open(path + ".pending", "w", encoding="utf-8") as handle:
            handle.write(str(request_id))
        payload = {
            "format_version": 1,
            "scope": "Provider 完成协议转换及数据安全处理后提交给 SDK 的参数；不包含认证头。",
            "session_id": str(session_id), "run_id": str(run_id),
            "request_id": str(request_id), "protocol": protocol,
            "model": params.get("model", ""),
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "parameters": {key: value for key, value in params.items()
                           if key not in {"extra_headers", "api_key", "http_client"}},
        }
        temporary = ""
        try:
            fd, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.remove(path + ".pending")
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)
        return payload["captured_at"]

    def read(self, session_id):
        if not self.available(session_id):
            raise ValueError("尚无可下载的请求，或最近一次请求快照采集失败。")
        with open(self.path(session_id), encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("session_id") != str(session_id):
            raise ValueError("请求快照的会话标识不匹配。")
        return payload

    def delete(self, session_id):
        path = self.path(session_id)
        for target in (path, path + ".pending"):
            if os.path.exists(target):
                os.remove(target)


def capture_provider_request(params, protocol, request_context):
    callback = (request_context or {}).get("capture_request")
    if callable(callback):
        try:
            callback(protocol, params)
        except Exception as exc:
            # This diagnostic hook must never change request/retry semantics.
            logging.getLogger(__name__).warning("request_snapshot callback failed: %s", type(exc).__name__)
