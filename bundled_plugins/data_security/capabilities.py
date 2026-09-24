"""Bounded read-only capability checks. Candidate matches are never verdicts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from .jobs import enqueue
from core.data_security import publish

RULES = {
    "remote_execution": re.compile(r"(?:curl|wget)[^\n]{0,300}\|\s{0,8}(?:ba)?sh", re.I),
    "credential_access": re.compile(r"(?:HOME|USERPROFILE|~/)[^\n]{0,100}(?:\.ssh|\.aws|credentials|mcp\.json)", re.I),
    "instruction_override": re.compile(r"ignore\s{1,8}(?:previous|all)\s{1,8}instructions|SYSTEM\s{1,8}OVERRIDE", re.I),
    "encoded_execution": re.compile(r"(?:b64decode|atob)[^\n]{0,200}\b(?:exec|eval|system)\b", re.I),
}
MAX_FILE = 512 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_FINDINGS = 200
RULES_VERSION = "capability-rules-v1"
_lock = threading.Lock()
_pending = {}
_cache = OrderedDict()
_requests = OrderedDict()


def cancel_all():
    with _lock:
        for cancel in _pending.values():
            cancel.set()
    publish({"event": "scan", "status": "partial", "message": "已取消后台检查；能力正常使用。"})


def retry_recent(policy):
    with _lock:
        requests = list(_requests.items())
    count = 0
    for (kind, object_id), payload in requests:
        if policy.get("enabled") and policy.get(kind + "_check"):
            submit(kind, object_id, payload)
            count += 1
    if not count:
        publish({"event": "scan", "status": "inactive", "message": "没有可重试的检查。启用并保存相应功能后，安装或更新 Skill、保存或连接 MCP 可触发检查。"})


def scan(kind, payload, cancelled=lambda: False):
    started = time.monotonic()
    findings, skipped, total = [], 0, 0
    contents = []
    digest = hashlib.sha256(RULES_VERSION.encode("ascii"))

    def inspect(label, content):
        digest.update(label.encode("utf-8", errors="replace"))
        digest.update(content.encode("utf-8"))
        contents.append((label, content))

    if kind == "mcp":
        # Never include headers, env, credentials, URL query or userinfo.
        parts = [("MCP 配置", json.dumps({"transport": payload.get("transport"), "command": payload.get("command")}, ensure_ascii=False))]
        for index, tool in enumerate(payload.get("tools", [])):
            if isinstance(tool, dict):
                parts.append((f"MCP 工具 #{index + 1}", json.dumps({key: tool.get(key) for key in ("name", "description")}, ensure_ascii=False)))
        for label, content in parts:
            if cancelled() or time.monotonic() - started > 2 or total >= MAX_FILE:
                skipped += 1
                break
            raw = content.encode("utf-8")
            available = MAX_FILE - total
            if len(raw) > available:
                skipped += 1
                raw = raw[:available]
            total += len(raw)
            inspect(label, raw.decode("utf-8", errors="ignore"))
    else:
        root_path = os.path.realpath(payload)
        if not os.path.isdir(root_path):
            raise FileNotFoundError("capability_source_missing")
        def walk_error(error):
            nonlocal skipped
            skipped += 1

        for root, dirs, files in os.walk(root_path, followlinks=False, onerror=walk_error):
            safe_dirs = [name for name in dirs if name != ".git" and not os.path.islink(os.path.join(root, name))
                         and not getattr(os.path, "isjunction", lambda p: False)(os.path.join(root, name))]
            skipped += len([name for name in dirs if name != ".git"]) - len(safe_dirs)
            dirs[:] = safe_dirs
            for name in files:
                if cancelled() or time.monotonic() - started > 2 or total >= MAX_TOTAL:
                    skipped += 1
                    break
                path = os.path.join(root, name)
                try:
                    if os.path.islink(path) or not os.path.isfile(path) or os.path.getsize(path) > MAX_FILE:
                        skipped += 1
                        continue
                    with open(path, "rb") as handle:
                        raw = handle.read(min(MAX_FILE + 1, MAX_TOTAL - total))
                    if len(raw) < os.path.getsize(path):
                        skipped += 1
                    total += len(raw)
                    if b"\x00" in raw or len(raw) > MAX_FILE:
                        skipped += 1
                        continue
                    inspect(os.path.relpath(path, root_path).replace("\\", "/"), raw.decode("utf-8-sig"))
                except (OSError, UnicodeError):
                    skipped += 1
            if cancelled() or time.monotonic() - started > 2 or total >= MAX_TOTAL:
                break
    cache_key = (kind, digest.hexdigest())
    if not skipped and not cancelled() and cache_key in _cache:
        return {**_cache[cache_key], "cached": True, "elapsed_ms": round((time.monotonic()-started)*1000, 2)}
    for label, content in contents:
        if cancelled() or time.monotonic() - started > 2 or len(findings) >= MAX_FINDINGS:
            skipped += 1
            break
        for name, pattern in RULES.items():
            for index, match in enumerate(pattern.finditer(content)):
                if len(findings) >= MAX_FINDINGS:
                    skipped += 1
                    break
                findings.append({"rule": name, "file": label, "line": content.count("\n", 0, match.start()) + 1})
                if index >= 2:
                    break
    return {"status": "partial" if skipped or cancelled() else "complete", "findings": findings,
            "rules_version": RULES_VERSION,
            "skipped": skipped, "bytes": total, "digest": digest.hexdigest(),
            "coverage": "仅配置与工具描述；未检查远程服务源码" if kind == "mcp" else "有界本地文本检查；未执行代码",
            "elapsed_ms": round((time.monotonic()-started)*1000, 2)}


def submit(kind, object_id, payload):
    key = (kind, object_id)
    with _lock:
        _requests[key] = payload
        while len(_requests) > 16:
            _requests.popitem(last=False)
        if key in _pending:
            _pending[key].set()  # old completion must never overwrite a newer object
        if len(_pending) >= 16 and key not in _pending:
            publish({"event": kind, "object_id": object_id, "status": "partial", "message": "检查队列已满，尚未检查；能力正常使用。"})
            return None
        cancel = threading.Event()
        _pending[key] = cancel
        future = enqueue(_check, key, payload, cancel)
        if future is None:
            _pending.pop(key, None)
            publish({"event": kind, "object_id": object_id, "status": "partial", "message": "检查队列已满，尚未检查；能力正常使用。"})
            return None
    return cancel


def _check(key, payload, cancel):
    kind, object_id = key
    publish({"event": kind, "object_id": object_id, "status": "running", "message": "正在检查风险线索…"})
    try:
        result = scan(kind, payload, cancel.is_set)
        if cancel.is_set():
            return
        cache_key = (kind, result["digest"])
        if result["status"] == "complete":
            _cache[cache_key] = result
            while len(_cache) > 64:
                _cache.popitem(last=False)
        message = "发现风险线索，请结合用途核对" if result["findings"] else "未发现明显风险"
        if result["status"] == "partial":
            message += "；部分内容未检查"
        event = {"event": kind, "object_id": object_id, "message": message, **{k: v for k, v in result.items() if k != "digest"}}
        publish(event)
        from .events import append
        append(event)
    except Exception as exc:
        publish({"event": kind, "object_id": object_id, "status": "failed", "error_type": type(exc).__name__,
                 "message": "风险检查未完成，能力仍按原有设置使用。"})
    finally:
        with _lock:
            if _pending.get(key) is cancel:
                _pending.pop(key, None)
                if cancel.is_set():
                    publish({"event": kind, "object_id": object_id, "status": "partial", "message": "检查已取消，尚未检查的内容不视为安全。"})
