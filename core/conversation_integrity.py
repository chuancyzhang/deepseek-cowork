"""Consistency primitives shared by conversation storage and provider adapters.

The conversation ledger is append-only from the user's point of view.  This
module deliberately validates malformed tool rounds instead of silently
removing the offending messages, and repairs only message identity conflicts
that would otherwise make a legacy conversation unloadable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field


MESSAGE_ID_NAMESPACE = "deepseek-cowork-message"


def canonical_ledger_message(message, *, include_id=True):
    """Return the storage/provider-stable identity of one ledger message.

    UI projection metadata and database timestamps are deliberately excluded:
    the UI and daemon may carry different presentation projections for the same
    canonical message, but that must never look like a conversation fork.
    """

    if not isinstance(message, dict):
        return None
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    stable_meta = {
        key: value
        for key, value in meta.items()
        if key != "sequence"
        and key != "ui_only"
        and not str(key).startswith("ui_")
    }
    normalized = {
        "role": str(message.get("role") or ""),
        "content": message.get("content") or "",
        "tool_call_id": str(message.get("tool_call_id") or ""),
        "reasoning_content": (
            message.get("reasoning_content")
            if message.get("reasoning_content") is not None
            else message.get("reasoning") or ""
        ),
    }
    if include_id:
        normalized["id"] = str(message.get("id") or "")
    if isinstance(message.get("content_parts"), list):
        normalized["content_parts"] = message.get("content_parts")
    if stable_meta:
        normalized["meta"] = stable_meta
    if message.get("result_obj") is not None:
        normalized["result_obj"] = message.get("result_obj")
    raw_tool_calls = message.get("tool_calls")
    if isinstance(raw_tool_calls, list):
        tool_calls = []
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, dict):
                continue
            function = (
                raw_call.get("function")
                if isinstance(raw_call.get("function"), dict)
                else {}
            )
            arguments = function.get("arguments")
            if isinstance(arguments, (dict, list)):
                arguments = json.dumps(
                    arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            elif arguments is None:
                arguments = ""
            tool_calls.append({
                "id": str(raw_call.get("id") or ""),
                "type": str(raw_call.get("type") or "function"),
                "name": str(function.get("name") or ""),
                "arguments": arguments,
            })
        normalized["tool_calls"] = tool_calls
    return normalized


def canonical_ledger_messages_hash(messages):
    normalized = [
        canonical_ledger_message(message, include_id=True)
        for message in (messages or [])
        if isinstance(message, dict)
    ]
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ToolSequenceValidation:
    """Result of validating a canonical role/tool message sequence."""

    missing_tool_call_ids: tuple[str, ...] = ()
    orphan_tool_call_ids: tuple[str, ...] = ()
    duplicate_tool_call_ids: tuple[str, ...] = ()
    out_of_order_tool_call_ids: tuple[str, ...] = ()
    invalid_tool_call_indexes: tuple[int, ...] = ()
    invalid_tool_message_indexes: tuple[int, ...] = ()
    interrupted_at_indexes: tuple[int, ...] = ()

    @property
    def valid(self) -> bool:
        return not any(
            (
                self.missing_tool_call_ids,
                self.orphan_tool_call_ids,
                self.duplicate_tool_call_ids,
                self.out_of_order_tool_call_ids,
                self.invalid_tool_call_indexes,
                self.invalid_tool_message_indexes,
                self.interrupted_at_indexes,
            )
        )

    def as_dict(self):
        return {
            "missing_tool_call_ids": list(self.missing_tool_call_ids),
            "orphan_tool_call_ids": list(self.orphan_tool_call_ids),
            "duplicate_tool_call_ids": list(self.duplicate_tool_call_ids),
            "out_of_order_tool_call_ids": list(self.out_of_order_tool_call_ids),
            "invalid_tool_call_indexes": list(self.invalid_tool_call_indexes),
            "invalid_tool_message_indexes": list(self.invalid_tool_message_indexes),
            "interrupted_at_indexes": list(self.interrupted_at_indexes),
            "valid": self.valid,
        }


class ToolSequenceValidationError(ValueError):
    """Raised before a provider request when a tool round is not replayable."""

    def __init__(self, validation: ToolSequenceValidation, context=""):
        self.validation = validation
        prefix = f"{context}: " if str(context or "").strip() else ""
        parts = []
        if validation.missing_tool_call_ids:
            parts.append(
                "缺少 tool 消息 "
                + ", ".join(validation.missing_tool_call_ids)
            )
        if validation.orphan_tool_call_ids:
            parts.append(
                "存在孤立 tool_call_id "
                + ", ".join(validation.orphan_tool_call_ids)
            )
        if validation.duplicate_tool_call_ids:
            parts.append(
                "存在重复 tool_call_id "
                + ", ".join(validation.duplicate_tool_call_ids)
            )
        if validation.out_of_order_tool_call_ids:
            parts.append(
                "tool 消息顺序与 assistant.tool_calls 不一致 "
                + ", ".join(validation.out_of_order_tool_call_ids)
            )
        if validation.invalid_tool_call_indexes:
            parts.append(
                "存在无效 assistant tool_calls，消息索引 "
                + ", ".join(str(index) for index in validation.invalid_tool_call_indexes)
            )
        if validation.invalid_tool_message_indexes:
            parts.append(
                "存在无效 tool 消息，消息索引 "
                + ", ".join(str(index) for index in validation.invalid_tool_message_indexes)
            )
        if validation.interrupted_at_indexes:
            parts.append(
                "工具调用闭环被后续消息打断，消息索引 "
                + ", ".join(str(index) for index in validation.interrupted_at_indexes)
            )
        detail = "；".join(parts) or "未知工具调用结构错误"
        super().__init__(
            prefix
            + "工具调用历史不完整，未发送供应商请求："
            + detail
            + "。原历史不会被静默裁剪。"
        )


class LedgerMessageConflictError(ValueError):
    """Raised when one stable message ID is reused for different data."""

    def __init__(self, message_id, existing_message=None, incoming_message=None):
        self.message_id = str(message_id or "")
        self.existing_message = copy.deepcopy(existing_message)
        self.incoming_message = copy.deepcopy(incoming_message)
        super().__init__(
            "会话账本冲突：message_id="
            f"{self.message_id} 已存在，但收到的消息内容不同；"
            "原历史不会被静默覆盖。"
        )


class LedgerMessageIdentityError(ValueError):
    """Raised when an intermediate layer tries to merge an ID-less event."""

    def __init__(self, index):
        self.index = int(index)
        super().__init__(
            "会话账本身份错误：合并事件缺少 message_id，"
            f"incoming_index={self.index}；不能在中间层重新生成消息身份。"
        )


class LedgerStructureError(ValueError):
    """Raised when a history payload is not a list of message objects."""

    def __init__(self, detail):
        super().__init__(f"会话账本结构损坏：{detail}；原始历史未被覆盖。")


def validate_tool_call_sequence(messages) -> ToolSequenceValidation:
    """Validate every assistant tool-call round in ``messages``.

    A tool round is complete only when every tool call emitted by an assistant
    message is followed by exactly one matching ``role=tool`` message before a
    new non-tool message appears.  The function never mutates the input.
    """

    pending = {}
    pending_order = []
    delivered = set()
    seen_call_ids = set()
    missing = set()
    orphan = set()
    duplicate = set()
    out_of_order = set()
    invalid_call_indexes = []
    invalid_tool_indexes = []
    interrupted_indexes = []

    for index, raw_message in enumerate(messages or []):
        if not isinstance(raw_message, dict):
            continue
        role = str(raw_message.get("role") or "").strip().lower()

        if role == "assistant":
            if pending:
                missing.update(pending)
                interrupted_indexes.append(index)
                pending.clear()
                pending_order.clear()

            raw_calls = raw_message.get("tool_calls")
            if not raw_calls:
                replay_items = []
                for replay_key in (
                    "_responses_replay_items",
                    "_deepseek_responses_replay_items",
                ):
                    candidate = raw_message.get(replay_key)
                    if isinstance(candidate, list):
                        replay_items.extend(candidate)
                replay_calls = []
                for item in replay_items:
                    if not isinstance(item, dict) or item.get("type") != "function_call":
                        continue
                    replay_calls.append(
                        {
                            "id": item.get("call_id") or item.get("id"),
                            "function": {
                                "name": item.get("name") or "",
                                "arguments": item.get("arguments") or "",
                            },
                        }
                    )
                raw_calls = replay_calls
            if not raw_calls:
                continue
            if not isinstance(raw_calls, list):
                invalid_call_indexes.append(index)
                continue

            for call in raw_calls:
                if not isinstance(call, dict):
                    invalid_call_indexes.append(index)
                    continue
                call_id = str(call.get("id") or "").strip()
                function = call.get("function")
                name = (
                    str(function.get("name") or "").strip()
                    if isinstance(function, dict)
                    else ""
                )
                if not call_id or not name:
                    invalid_call_indexes.append(index)
                    continue
                if call_id in seen_call_ids or call_id in pending:
                    duplicate.add(call_id)
                    continue
                seen_call_ids.add(call_id)
                pending[call_id] = index
                pending_order.append(call_id)
            continue

        if role == "tool":
            call_id = str(raw_message.get("tool_call_id") or "").strip()
            if not call_id:
                invalid_tool_indexes.append(index)
                continue
            if call_id in delivered:
                duplicate.add(call_id)
                continue
            if call_id not in pending:
                orphan.add(call_id)
                continue
            if pending_order and call_id != pending_order[0]:
                out_of_order.add(call_id)
                pending_order.remove(call_id)
                pending.pop(call_id, None)
                delivered.add(call_id)
                continue
            pending.pop(call_id, None)
            if pending_order:
                pending_order.pop(0)
            delivered.add(call_id)
            continue

        if pending:
            missing.update(pending)
            interrupted_indexes.append(index)
            pending.clear()
            pending_order.clear()

    missing.update(pending)
    return ToolSequenceValidation(
        missing_tool_call_ids=tuple(sorted(missing)),
        orphan_tool_call_ids=tuple(sorted(orphan)),
        duplicate_tool_call_ids=tuple(sorted(duplicate)),
        out_of_order_tool_call_ids=tuple(sorted(out_of_order)),
        invalid_tool_call_indexes=tuple(sorted(set(invalid_call_indexes))),
        invalid_tool_message_indexes=tuple(sorted(set(invalid_tool_indexes))),
        interrupted_at_indexes=tuple(sorted(set(interrupted_indexes))),
    )


def ensure_tool_call_sequence(messages, context="") -> ToolSequenceValidation:
    validation = validate_tool_call_sequence(messages)
    if not validation.valid:
        raise ToolSequenceValidationError(validation, context=context)
    return validation


class ToolSequenceValidator:
    """Named façade used by provider boundaries and regression tests."""

    @staticmethod
    def validate(messages):
        return validate_tool_call_sequence(messages)

    @staticmethod
    def ensure(messages, context=""):
        return ensure_tool_call_sequence(messages, context=context)


def _stable_message_id(conversation_id, original_id, index, occurrence):
    owner = str(conversation_id or "")
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{MESSAGE_ID_NAMESPACE}:{owner}:{original_id}:{index}:{occurrence}",
    ).hex


def normalize_message_ids(messages, conversation_id=""):
    """Return messages with deterministic IDs and duplicate-ID repair metadata.

    The first occurrence keeps its ID.  Later occurrences receive deterministic
    IDs and retain the original ID in metadata so a repair can be audited.  No
    message content or order is removed.
    """

    if not isinstance(messages, list):
        raise LedgerStructureError("messages 必须是数组")

    normalized = []
    repairs = []
    seen = set()
    occurrences = {}
    used_sequences = set()
    next_sequence = 0
    owner = str(conversation_id or "")

    for index, raw_message in enumerate(messages):
        if not isinstance(raw_message, dict):
            raise LedgerStructureError(f"消息索引 {index} 不是对象")
        message = copy.deepcopy(raw_message)
        original_id = str(message.get("id") or "").strip()
        if not original_id:
            message_id = _stable_message_id(owner, "missing", index, 0)
            message["id"] = message_id
            repairs.append({
                "kind": "missing_message_id",
                "index": index,
                "new_message_id": message_id,
            })
        elif original_id in seen:
            occurrence = occurrences.get(original_id, 1)
            message_id = _stable_message_id(owner, original_id, index, occurrence)
            while message_id in seen:
                occurrence += 1
                message_id = _stable_message_id(owner, original_id, index, occurrence)
            occurrences[original_id] = occurrence + 1
            message["id"] = message_id
            meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
            meta = dict(meta)
            meta["original_message_id"] = original_id
            meta["message_id_remapped"] = True
            meta["message_id_remap_index"] = index
            message["meta"] = meta
            repairs.append({
                "kind": "duplicate_message_id",
                "index": index,
                "original_message_id": original_id,
                "new_message_id": message_id,
            })
        else:
            message_id = original_id
            message["id"] = message_id

        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        meta = dict(meta)
        raw_sequence = meta.get("sequence")
        try:
            sequence = int(raw_sequence)
            if sequence < 0 or sequence in used_sequences:
                raise ValueError
        except (TypeError, ValueError):
            while next_sequence in used_sequences:
                next_sequence += 1
            sequence = next_sequence
        meta["sequence"] = sequence
        message["meta"] = meta
        used_sequences.add(sequence)
        next_sequence = max(next_sequence, sequence + 1)

        seen.add(message_id)
        occurrences.setdefault(original_id or "missing", 1)
        normalized.append(message)

    return normalized, repairs


def merge_messages_by_id(existing_messages, incoming_messages):
    """Append incoming messages once while preserving independent same-text turns."""

    existing = [copy.deepcopy(item) for item in (existing_messages or []) if isinstance(item, dict)]
    incoming = [copy.deepcopy(item) for item in (incoming_messages or []) if isinstance(item, dict)]
    existing_by_id = {
        str(item.get("id") or "").strip(): item
        for item in existing
        if str(item.get("id") or "").strip()
    }

    def signature(message):
        comparable = canonical_ledger_message(message, include_id=True)
        try:
            return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            return repr(comparable)

    for index, message in enumerate(incoming):
        message_id = str(message.get("id") or "").strip()
        if not message_id:
            raise LedgerMessageIdentityError(index)
        existing_message = existing_by_id.get(message_id)
        if existing_message is not None:
            if signature(existing_message) != signature(message):
                raise LedgerMessageConflictError(
                    message_id,
                    existing_message=existing_message,
                    incoming_message=message,
                )
            continue
        existing.append(message)
        existing_by_id[message_id] = message
    return existing
