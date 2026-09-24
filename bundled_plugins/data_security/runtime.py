"""Model-bound text projection. Output and protocol envelopes remain untouched."""
from __future__ import annotations

import hashlib
import hmac
from collections import OrderedDict

MAX_TEXT_BYTES = 2 * 1024 * 1024


def map_text_blocks(value, transform):
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, list):
        return [map_text_blocks(item, transform) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get("type", "")
    if kind in {"text", "input_text", "output_text"}:
        return {**value, "text": transform(value.get("text", ""))}
    if kind == "tool_result":
        return {**value, "content": map_text_blocks(value.get("content", ""), transform)}
    return value  # images, file IDs, signatures, opaque replay: never walk blindly


def project_payload(params, protocol, transform):
    key = "input" if protocol == "responses" else "messages"
    items = params.get(key)
    if not isinstance(items, list):
        return params
    projected = []
    for item in items:
        if not isinstance(item, dict):
            projected.append(item)
        elif item.get("type") == "function_call_output":
            projected.append({**item, "output": map_text_blocks(item.get("output", ""), transform)})
        elif item.get("role") in {"user", "tool"}:
            projected.append({**item, "content": map_text_blocks(item.get("content", ""), transform)})
        else:
            projected.append(item)
    return {**params, key: projected}


class RequestPlugin:
    def __init__(self, run):
        self.run = run
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.vault = None
        if run.policy["tokenize"]:
            if not any(run.policy["categories"].values()):
                raise ValueError("no_categories")
            from .vault import TokenVault
            self.vault = TokenVault(run.scope, run.data_dir)
            from .rules import matches
            self.matches = matches

    def project_request(self, params, protocol):
        counts = {}
        warning_texts = []
        pending = {}
        scanned_bytes = 0

        def transform(text, state=None):
            nonlocal scanned_bytes
            if not isinstance(text, str) or not text:
                return text
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            if digest in self.cache:
                self.cache.move_to_end(digest)
                return self.cache[digest]
            scanned_bytes += len(text.encode("utf-8"))
            if scanned_bytes > MAX_TEXT_BYTES:
                raise ValueError("request_text_limit")
            if callable(self.run.abort_check) and self.run.abort_check():
                raise InterruptedError("cancelled")
            if self.run.policy["credential_warning"]:
                warning_texts.append(text)
            result = text
            if state is not None:
                pieces, last = [], 0
                for start, end, kind, value in self.matches(text, self.run.policy["categories"]):
                    fingerprint = hmac.new(bytes.fromhex(state["key"]), (kind + "\x00" + value).encode("utf-8"), hashlib.sha256).hexdigest()[:16]
                    token = f'[[CW:{state["namespace"]}:{kind}:{fingerprint}]]'
                    if token in text or (token in state["values"] and state["values"][token] != value):
                        raise ValueError("token_collision")
                    state["values"][token] = value
                    if len(state["values"]) > 10000:
                        raise ValueError("conversation_mapping_limit")
                    counts[kind] = counts.get(kind, 0) + 1
                    pieces.extend((text[last:start], token))
                    last = end
                if pieces:
                    pieces.append(text[last:])
                    result = "".join(pieces)
            pending[digest] = result
            return result

        if self.vault:
            with self.vault.transaction() as state:
                result = project_payload(params, protocol, lambda text: transform(text, state))
        else:
            result = project_payload(params, protocol, transform)
        # Publish/cache only after mapping persistence commits successfully.
        for digest, value in pending.items():
            self.cache[digest] = value
            self.cache_bytes += len(value) * 2
        while self.cache and (self.cache_bytes > 8 * 1024 * 1024 or len(self.cache) > 512):
            _, value = self.cache.popitem(last=False)
            self.cache_bytes -= len(value) * 2
        if warning_texts:
            from .credentials import submit
            submit(self.run, warning_texts)
        if counts:
            self.run.notice("tokenize", "complete", "已对本次模型请求中的所选敏感信息令牌化；原始消息和文件未修改。", counts=counts)
        return result

    def close(self):
        self.cache.clear()
        self.cache_bytes = 0
