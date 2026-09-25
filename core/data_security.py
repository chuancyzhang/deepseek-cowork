"""Host boundary for the bundled, opt-in data-security extension.

Importing this module performs no IO and imports no plugin/regex engine. Security
is advisory; failures are visible and never replace the normal execution path.
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections import deque

log = logging.getLogger(__name__)
CATEGORIES = {
    "id_card": "身份证", "phone": "手机号", "bank_card": "银行卡",
    "email": "邮箱", "landline": "固定电话", "passport": "护照号",
    "credit_code": "统一社会信用代码", "ip": "IP 地址",
}
FEATURES = {
    "skill_check": "Skill 本地风险检查", "mcp_check": "MCP 配置与工具描述检查",
    "credential_warning": "模型请求文本中的疑似凭证提醒", "tokenize": "模型请求文本自动令牌化",
}
_events = deque(maxlen=200)
_event_lock = threading.Lock()
TOKEN_PREFIX = "[[CW:"


def normalize_config(value=None):
    value = value if isinstance(value, dict) else {}
    categories = value.get("categories") if isinstance(value.get("categories"), dict) else {}
    return {"enabled": value.get("enabled") is True,
            **{name: value.get(name) is True for name in FEATURES},
            "categories": {name: categories.get(name) is True for name in CATEGORIES}}


def read_config(config):
    value = config.get("data_security", {}) if config is not None else {}
    return normalize_config(value)


def validate_config(value):
    value = normalize_config(value)
    if value["enabled"] and value["tokenize"] and not any(value["categories"].values()):
        raise ValueError("请至少选择一种需要令牌化的数据类别。")
    return value


def publish(event, callback=None):
    # Only structured, host-owned messages. Never exception text or scanned text.
    import time
    event = {"type": "data_security", "timestamp": time.time(), **event}
    with _event_lock:
        _events.append(event)
    log.info("data_security event=%s status=%s run=%s error_type=%s",
             event.get("event"), event.get("status"), event.get("run_id", ""), event.get("error_type", ""))
    if callable(callback):
        try:
            callback(dict(event))
        except Exception:
            log.warning("data_security.callback_failed")


def recent_events():
    with _event_lock:
        return list(_events)


class SecurityRun:
    def __init__(self, policy, scope, run_id, callback=None, abort_check=None, data_dir=None):
        self.policy = normalize_config(policy)
        self.scope = str(scope or uuid.uuid4().hex)
        self.run_id = str(run_id or uuid.uuid4().hex)
        self.callback = callback
        self.abort_check = abort_check
        self.data_dir = data_dir
        self.plugin = None
        self.raw_mode = False
        self.closed = False
        self._notices = set()
        self._restorer = None
        self._warning_keys = set()
        self._warning_future = None

    def notice(self, event, status, message, **fields):
        key = (event, status)
        if key in self._notices:
            return
        self._notices.add(key)
        publish({"event": event, "status": status, "message": message,
                 "scope": self.scope, "run_id": self.run_id, **fields}, self.callback)
        try:
            from bundled_plugins.data_security.events import append
            append({"event": event, "status": status, "message": message,
                    "scope": self.scope, "run_id": self.run_id, **fields})
        except Exception:
            log.warning("data_security.event_persistence_unavailable")

    def project_request(self, params, protocol):
        if (self.closed or self.raw_mode or not self.policy["enabled"]
                or not (self.policy["tokenize"] or self.policy["credential_warning"])):
            return params
        if callable(self.abort_check) and self.abort_check():
            return params
        try:
            if self.plugin is None:
                from bundled_plugins.data_security.runtime import RequestPlugin
                self.plugin = RequestPlugin(self)
            projected = self.plugin.project_request(params, protocol)
            self._restorer = None
            return projected
        except Exception as exc:
            self.raw_mode = True
            self.notice("request", "failed", "本次未脱敏，已按设置使用原文。可在设置 → 数据安全查看状态并重试。",
                        error_type=type(exc).__name__)
            return params

    def restore_text(self, text):
        if not isinstance(text, str) or TOKEN_PREFIX not in text:
            return text
        try:
            if self._restorer is None:
                from bundled_plugins.data_security.compat import Restorer
                self._restorer = Restorer(self.scope, self.data_dir)
            # Mapping can have grown since a previous model request.
            return self._restorer.restore(text)
        except Exception as exc:
            self.notice("restore", "failed", "部分占位符未能还原，原始消息和已有成果已保留。",
                        error_type=type(exc).__name__)
            return text

    def resolve_tool_arguments(self, name, arguments, trusted=False):
        if TOKEN_PREFIX not in str(arguments):
            return {"status": "complete", "arguments": arguments}
        try:
            from bundled_plugins.data_security.compat import resolve_arguments
            return resolve_arguments(name, arguments, self.restore_text, trusted=trusted)
        except Exception as exc:
            self.notice("arguments", "failed", "参数还原未完成，将使用原文继续。", error_type=type(exc).__name__)
            return {"status": "original_required", "arguments": arguments}

    def use_original(self):
        self.raw_mode = True
        self.notice("original_context", "partial", "该操作需要真实内容：尚未执行相关工具，已切换原文重新生成；本轮后续不再脱敏。")

    def original_values(self, arguments):
        """Give the model data for regeneration; never rewrite executable text."""
        try:
            from bundled_plugins.data_security.compat import TOKEN
            import json
            tokens = dict.fromkeys(TOKEN.findall(json.dumps(arguments, ensure_ascii=False)))
            values = {}
            for token in list(tokens)[:100]:
                original = self.restore_text(token)
                if original != token:
                    values[token] = original
            return values
        except Exception as exc:
            self.notice("arguments", "failed", "原文参考未能恢复，已保留原始输入。", error_type=type(exc).__name__)
            return {}

    def close(self):
        self.closed = True
        if self.plugin is not None:
            try:
                self.plugin.close()
            except Exception as exc:
                self.notice("close", "failed", "数据安全插件收尾未完成，已保留任务成果。", error_type=type(exc).__name__)


def begin_run(config=None, *, policy=None, scope=None, run_id=None, callback=None, abort_check=None, data_dir=None):
    return SecurityRun(read_config(config) if policy is None else policy,
                       scope, run_id, callback, abort_check, data_dir)


def project_provider_request(provider, params, protocol, request_context=None):
    run = getattr(provider, "data_security_run", None)
    if run is None:
        policy = getattr(provider, "data_security_policy", None)
        if not isinstance(policy, dict) or not policy.get("enabled"):
            return params
        context = request_context or {}
        run = begin_run(policy=policy, scope=context.get("conversation_id"), run_id=context.get("run_id"))
        provider.data_security_run = run
        provider.data_security_owned = not bool(context.get("conversation_id"))
    return run.project_request(params, protocol)


def close_provider_run(provider):
    if getattr(provider, "data_security_owned", False) is not True:
        return
    run = getattr(provider, "data_security_run", None)
    if not isinstance(run, SecurityRun):
        return
    try:
        run.close()
        if run.plugin is not None and run.plugin.vault is not None:
            delete_scope(run.scope, run.data_dir)
    except Exception as exc:
        run.notice("cleanup", "failed", "临时令牌映射清理未完成。", error_type=type(exc).__name__)
    finally:
        provider.data_security_run = None


def inspect_capability(config, kind, object_id, payload):
    policy = read_config(config)
    if not policy["enabled"] or not policy.get(kind + "_check"):
        return None
    try:
        from bundled_plugins.data_security.capabilities import submit
        return submit(kind, str(object_id), payload)
    except Exception as exc:
        publish({"event": kind, "status": "failed", "object_id": str(object_id),
                 "message": "风险检查未完成，能力仍按原有设置使用。", "error_type": type(exc).__name__})
        return None


def delete_scope(scope, data_dir=None):
    # Deletion/compatibility never imports the detection engine or creates a DB.
    from bundled_plugins.data_security.vault import TokenVault
    TokenVault(scope, data_dir).delete()
