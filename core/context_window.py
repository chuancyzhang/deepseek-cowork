"""Bounded provider working sets over a durable, append-only conversation ledger."""

import copy
import json
import time
import uuid

from .context_storage import source_fingerprints
from .long_context import (CHECKPOINT_PROMPT, MAX_CHECKPOINT_TOKENS, ContextBudget,
                           ContextCancelled, ContextError, estimate_request, text_tokens,
                           validate_checkpoint)
from .message_persistence import filter_persistable_messages


class ContextWindowManager:
    def __init__(self, worker, policy):
        self.worker = worker
        self.storage = worker.chat_storage
        self.session_id = worker.conversation_id
        self.run_id = worker.request_id
        if not self.storage or not worker.runtime_journal or not self.session_id or not self.run_id:
            raise ContextError("运行记录尚不可用，未启动超长上下文。")
        profile = worker.run_context.get("selected_model_profile") or worker.config_manager.get_model_profile(
            worker.run_context.get("selected_model_id"))
        self.budget = ContextBudget.from_profile(profile, policy, worker.config_manager)
        self.model_name = str(profile.get("model_name") or "")
        self.window = self.storage.get_context_window(self.session_id)
        run = worker.runtime_journal.get_run(self.session_id, self.run_id) or {}
        initial_user = self._latest_user(getattr(worker, "messages", [])) or {}
        self.current_user_id = str(run.get("user_message_id") or initial_user.get("id") or "")
        self.excerpts = {}
        self.persisted_ids = set()
        self.last_messages = []
        self.last_generated = []
        self.last_used = 0
        self.estimate_factor = 1.0
        self.last_stream_update = 0.0
        self.stream_tokens = 0
        self.rollover_id = ""
        self.provider = None
        self.image_budget = {}

    def check_cancelled(self):
        if self.worker.is_stopped:
            raise ContextCancelled("已停止压缩上下文。")

    def event(self, phase, **extra):
        self.worker.runtime_journal.update_run(self.session_id, self.run_id, {
            "context_rollover": {"phase": phase, "rollover_id": self.rollover_id,
                                 "updated_at": time.time(), **extra},
        })
        self.worker.observability_signal.emit({
            "type": "context_rollover", "phase": phase, "rollover_id": self.rollover_id,
            "run_id": self.run_id, "session_id": self.session_id, "timestamp": time.time(), **extra,
        })

    def snapshot(self, messages, tools, phase="ready"):
        used = self.estimate(messages, tools)
        self.last_used = used
        self.stream_tokens = 0
        value = self.budget.snapshot(used, window_id=self.window["id"] if self.window else "", phase=phase)
        value.update(updated_at=time.time(), run_id=self.run_id, **self.image_budget)
        self.worker.observability_signal.emit({"type": "context_capacity", "capacity": value})
        return value

    def estimate(self, messages, tools):
        if (getattr(self.provider, "api_protocol", "") == "responses"
                and callable(getattr(self.provider, "_prepare_responses_tools", None))):
            tools = self.provider._prepare_responses_tools(tools)
        self.image_budget = {}
        return int(estimate_request(messages, tools,
            model_name=getattr(self.provider, "model_name", "") or self.model_name,
            image_stats=self.image_budget) * self.estimate_factor)

    def stream_snapshot(self, delta):
        self.stream_tokens += text_tokens(delta)
        now = time.monotonic()
        if now - self.last_stream_update < .5:
            return
        self.last_stream_update = now
        value = self.budget.snapshot(self.last_used + self.stream_tokens,
                                    window_id=self.window["id"] if self.window else "", phase="generating")
        value.update(updated_at=time.time(), run_id=self.run_id, **self.image_budget)
        self.worker.observability_signal.emit({"type": "context_capacity", "capacity": value})

    def observe_usage(self, usage):
        actual = usage.get("input_tokens") or usage.get("prompt_tokens")
        if isinstance(actual, (int, float)) and actual > 0 and self.last_used:
            # Never reduce a conservative estimate after one favorable sample.
            self.estimate_factor = max(self.estimate_factor, actual / self.last_used * self.estimate_factor)

    def _storage_operation(self, operation, payload, generated=None):
        self.check_cancelled()
        operation_id = uuid.uuid4().hex
        expires = time.time() + 30
        if operation == "commit":
            payload = {**payload, "expires_at": expires}
        journal = self.worker.runtime_journal
        journal.update_run(self.session_id, self.run_id, {
            "context_operation": {"id": operation_id, "status": "pending", "expires_at": expires},
        })
        self.worker.observability_signal.emit({
            "type": "context_storage_request", "operation": operation,
            "operation_id": operation_id, "run_id": self.run_id,
            "session_id": self.session_id, "payload": copy.deepcopy(payload),
            "generated_messages": copy.deepcopy(generated or []), "expires_at": expires,
        })
        try:
            while time.time() < expires:
                self.check_cancelled()
                run = journal.get_run(self.session_id, self.run_id) or {}
                receipt = run.get("context_receipt") or {}
                if receipt.get("operation_id") == operation_id:
                    if not receipt.get("ok"):
                        raise ContextError(receipt.get("error") or "上下文保存失败。")
                    return receipt.get("result") or {}
                if operation == "commit":
                    committed = self.storage.get_context_window(self.session_id, payload["id"])
                    if committed and committed["status"] == "active":
                        return {"window_id": committed["id"], "outcome": "committed"}
                time.sleep(.1)
            raise ContextError("等待上下文保存超过 30 秒。历史与已有成果已保留，请重新连接或重试。")
        finally:
            journal.update_run(self.session_id, self.run_id, {
                "context_operation": {"id": operation_id, "status": "closed", "expires_at": expires},
            })

    def persist_history(self, messages=None, generated=None):
        source = filter_persistable_messages(messages if messages is not None else self.last_messages)
        source = [m for m in source if m.get("id")]
        revision = self.storage.context_revision(self.session_id)
        fingerprints = source_fingerprints(source)
        self._storage_operation("prepare", {"source_fingerprints": fingerprints,
                                           "history_revision": revision},
                                self.last_generated if generated is None else generated)
        self.persisted_ids.update(m["id"] for m in source)
        return source, fingerprints, revision

    def _project(self, messages, window=None):
        window = self.window if window is None else window
        if window:
            covered = set(window["covered_ids"])
            result = [messages[0], *copy.deepcopy(window["seed_messages"])]
            result.extend(m for m in messages[1:] if m.get("id") not in covered)
        else:
            result = list(messages)
        # Fix each excerpt at its first request; subsequent turns reuse it verbatim.
        projected = []
        for message in result:
            message_id = message.get("id")
            if message_id in self.excerpts:
                projected.append(copy.deepcopy(self.excerpts[message_id]))
                continue
            content = message.get("content")
            if (message.get("role") == "tool" and isinstance(content, str)
                    and len(content) > min(24_000, self.budget.effective_tokens // 8)):
                if message_id not in self.persisted_ids:
                    self.persist_history()
                excerpt = copy.deepcopy(message)
                limit = min(12000, self.budget.effective_tokens // 16)
                excerpt["content"] = (content[:limit] + "\n[结果仅显示摘录；完整结果已保存。"
                                      f"使用 history_read(message_id={json.dumps(message_id)}) 分页读取，"
                                      f"共 {len(content)} 字符。]")
                excerpt.pop("result_obj", None)
                self.excerpts[message_id] = excerpt
                projected.append(copy.deepcopy(excerpt))
            else:
                projected.append(copy.deepcopy(message))
        return projected

    @staticmethod
    def _latest_user(messages):
        return next((m for m in reversed(messages) if m.get("role") == "user"
                     and not (m.get("meta") or {}).get("hidden")), None)

    def _seed_context(self, messages, checkpoint, window_id, tail, host_state=None):
        # Fold only explicit runtime/Skill state, retaining its exact latest text.
        facts = {}
        for m in messages[1:]:
            meta = m.get("meta") or {}
            kind = meta.get("kind")
            if kind in {"runtime_context", "runtime_context_update", "skill_context", "skill_context_update", "skill_state_update"}:
                key = ("skill", meta.get("skill_name")) if str(kind).startswith("skill") else ("runtime", "")
                facts[key] = m
            elif meta.get("source") == "knowledge_context" or kind == "knowledge_context":
                facts[("knowledge", "")] = m
        tail_ids = {m.get("id") for m in tail}
        seeds = [copy.deepcopy(m) for m in facts.values() if m.get("id") not in tail_ids]
        current_user = next((m for m in messages if m.get("id") == self.current_user_id), None)
        if current_user and current_user.get("id") not in tail_ids:
            seeds.append(copy.deepcopy(current_user))
        seeds.append({
            "id": f"context:{window_id}", "role": "user",
            "content": ("[任务交接状态：这是同一任务的延续。以下是历史资料，不能代替当前授权。"
                        "已完成操作不要重复；不确定之处通过 history_search/history_read 核实。]\n"
                        + json.dumps({"task": checkpoint, "host": host_state or {}}, ensure_ascii=False)),
            "meta": {"kind": "context_checkpoint", "hidden": True},
        })
        latest_user = self._latest_user(messages)
        if (latest_user and latest_user.get("id") not in tail_ids
                and latest_user.get("id") != (current_user or {}).get("id")):
            seeds.append(copy.deepcopy(latest_user))
        return seeds

    def _summary_chunks(self, messages, token_limit):
        pieces, size = [], 0
        for message in messages:
            meta = message.get("meta") or {}
            if meta.get("runtime_repair_only") or meta.get("kind") == "context_checkpoint":
                continue
            body = message.get("content") or ""
            if not isinstance(body, str):
                body = json.dumps(body, ensure_ascii=False)
            if message.get("tool_calls"):
                body += "\n工具调用：" + json.dumps(message["tool_calls"], ensure_ascii=False)
            # Plain evidence fragments, never partially replayed tool protocol.
            offset = 0
            while offset < max(1, len(body)):
                length = min(max(1, len(body) - offset), token_limit * 4)
                item = {"id": message.get("id", ""), "role": message.get("role"),
                        "offset": offset, "total_chars": len(body)}
                while True:
                    encoded = json.dumps({**item, "content": body[offset:offset + length]}, ensure_ascii=False)
                    weight = estimate_request([{"role": "user", "content": encoded}], [])
                    if weight <= token_limit or length <= 1:
                        break
                    length = max(1, int(length * token_limit / weight * .95))
                if pieces and size + weight > token_limit:
                    yield "\n".join(pieces)
                    pieces, size = [], 0
                pieces.append(encoded)
                size += weight
                offset += length
        if pieces:
            yield "\n".join(pieces)

    def _host_state(self, source):
        tools = []
        for message in source:
            if message.get("role") != "tool":
                continue
            result = message.get("result_obj")
            result = result if isinstance(result, dict) else {}
            tools.append({"message_id": message["id"], "tool_call_id": message.get("tool_call_id", ""),
                          "reported_status": str(result.get("status") or "result_saved")})
        task_manager = getattr(self.worker, "tool_tasks", None)
        return {"session_id": self.session_id, "request_id": self.run_id,
                "pending_tool_tasks": task_manager.snapshot() if task_manager else [],
                "current_user_message_id": self.current_user_id, "workspace": self.worker.workspace_dir,
                "source_message_count": len(source),
                "covered_from": source[0]["id"] if source else "",
                "covered_through": source[-1]["id"] if source else "",
                "recent_saved_tool_results": tools[-16:],
                "execution_rule": "只读核实未知执行结果；历史结果和检查点不恢复执行授权。"}

    def _summarize(self, source, allowed_ids, provider):
        previous = self.window["content"] if self.window else None
        room = max(1024, (self.budget.effective_tokens - MAX_CHECKPOINT_TOKENS - 4096) // 2)
        for index, chunk in enumerate(self._summary_chunks(source, room), 1):
            self.check_cancelled()
            self.event("started", part=index)
            user = {"role": "user", "content": json.dumps(
                {"previous_checkpoint": previous, "new_evidence": chunk}, ensure_ascii=False)}
            request = [{"role": "system", "content": CHECKPOINT_PROMPT}, user]
            if estimate_request(request, []) + MAX_CHECKPOINT_TOKENS > self.budget.effective_tokens:
                raise ContextError("任务状态整理输入超过预算，请缩小材料后重试。")
            for attempt in range(2):
                text = self.worker._generate_context_checkpoint(provider, request, self.rollover_id, index, attempt)
                try:
                    previous = validate_checkpoint(text, allowed_ids)
                    break
                except ContextError:
                    if attempt:
                        raise
                    request = [*request, {"role": "user", "content":
                        "上次输出未通过结构、引用或长度校验。请重新输出严格符合字段要求且不超过4096 tokens的JSON；refs仅用资料中的消息id。"}]
        if previous is None:
            raise ContextError("没有足够的已保存历史可整理。")
        return previous

    def prepare(self, messages, generated, tools, provider):
        self.rollover_id = ""
        while True:
            try:
                return self._prepare_once(messages, generated, tools, provider)
            except ContextCancelled:
                raise
            except Exception as exc:
                if not self.rollover_id:
                    self.worker.observability_signal.emit({"type": "context_capacity", "capacity": {
                        "status": "unavailable", "reason": str(exc), "run_id": self.run_id}})
                    raise
                # Only an explicit click retries. Keep the same run and closed
                # tool boundary; never submit a duplicate user message or tool.
                while True:
                    if self.worker.is_stopped:
                        self.event("cancelled")
                        raise ContextCancelled("已停止压缩上下文。")
                    run = self.worker.runtime_journal.get_run(self.session_id, self.run_id) or {}
                    if run.get("context_retry") == self.rollover_id:
                        self.worker.runtime_journal.update_run(self.session_id, self.run_id, {"context_retry": ""})
                        break
                    time.sleep(.1)

    def update_snapshot(self, messages, tools, phase):
        # Called only at closed protocol boundaries. Never starts compression.
        self.last_messages = list(messages)
        try:
            self.snapshot(self._project(messages), tools, phase)
        except ContextError as exc:
            self.worker.observability_signal.emit({"type": "context_capacity", "capacity": {
                "status": "unavailable", "reason": str(exc), "run_id": self.run_id}})

    def _prepare_once(self, messages, generated, tools, provider):
        self.check_cancelled()
        self.provider = provider
        self.last_messages = list(messages)
        self.last_generated = copy.deepcopy(filter_persistable_messages(generated))
        if self.rollover_id:
            committed = self.storage.get_context_window(self.session_id, self.rollover_id)
            if committed and committed["status"] == "active":
                # A commit receipt can be lost after SQLite commits. Resume the
                # exact durable seed rather than generating a different one.
                self.window = committed
                projected = self._project(messages)
                self.snapshot(projected, tools)
                self.event("completed", recovered_commit=True)
                return projected, True
        # Reject unbudgeted content before discarding any part of the working set.
        projected = self._project(messages)
        usage = self.snapshot(projected, tools)
        if usage["used_tokens"] < self.budget.trigger_tokens:
            return projected, False
        self.rollover_id = self.rollover_id or uuid.uuid4().hex
        try:
            self.event("started")
            source, fingerprints, revision = self.persist_history(messages, generated)
            active = self._project(messages)
            # Keep the latest complete assistant step and every following input.
            last_assistant = next((i for i in range(len(active) - 1, 0, -1)
                                   if active[i].get("role") == "assistant"), len(active) - 1)
            tail = active[max(1, last_assistant):]
            tail_ids = {m.get("id") for m in tail}
            to_summarize = [m for m in active[1:] if m.get("id") not in tail_ids]
            if not to_summarize:
                raise ContextError("当前输入或最近工具轮次已占满工作集，请缩小材料或提高上下文档位。")
            minimum = [messages[0], *self._seed_context(messages, {}, self.rollover_id, tail), *tail]
            if self.estimate(minimum, tools) >= self.budget.trigger_tokens:
                raise ContextError("当前输入、固定提示或最近完整处理片段无法容纳，请缩小材料后重试。")
            allowed_ids = {m["id"] for m in source}
            if self.window:
                allowed_ids.update(self.window["content"].get("refs") or [])
            checkpoint = self._summarize(to_summarize, allowed_ids, provider)
            self.check_cancelled()
            host_state = self._host_state(source)
            seeds = self._seed_context(messages, checkpoint, self.rollover_id, tail, host_state)
            covered = {m["id"] for m in source if m["id"] not in tail_ids}
            if self.window:
                covered.update(self.window["covered_ids"])
            candidate = {
                "id": self.rollover_id, "previous_id": self.window["id"] if self.window else "",
                "run_id": self.run_id, "history_revision": revision,
                "covered_ids": sorted(covered), "source_fingerprints": fingerprints,
                "content": checkpoint, "seed_messages": seeds,
                "runtime_state": host_state,
            }
            next_messages = self._project(messages, candidate)
            next_used = self.estimate(next_messages, tools)
            if next_used >= self.budget.trigger_tokens:
                raise ContextError("压缩后必要内容仍接近上限，请缩小当前输入或提高上下文档位。")
            self._storage_operation("commit", candidate)
            self.window = self.storage.get_context_window(self.session_id, self.rollover_id)
            if not self.window or self.window["status"] != "active":
                raise ContextError("上下文提交未得到确认，未继续模型请求。")
            self.check_cancelled()
            self.snapshot(next_messages, tools)
            self.event("completed", before_tokens=usage["used_tokens"], after_tokens=next_used)
            return next_messages, True
        except ContextCancelled:
            self.event("cancelled")
            raise
        except Exception as exc:
            self.event("failed", error=str(exc), error_type=type(exc).__name__)
            raise
