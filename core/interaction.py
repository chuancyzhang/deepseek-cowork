from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from PySide6.QtCore import QObject, Signal
import threading
import time
import uuid


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_kind(kind: str) -> str:
    text = (kind or "").strip().lower()
    if text in {"approval", "text", "choice", "multi_choice", "questionnaire"}:
        return text
    return "text"


def _coerce_timeout(timeout_seconds: Any, default: float = 120.0) -> float:
    try:
        value = float(timeout_seconds)
    except Exception:
        value = default
    if value <= 0:
        value = default
    return value


def _normalize_options(options: Any) -> list[dict[str, Any]]:
    normalized = []
    for item in options or []:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("value") or "").strip()
            value = str(item.get("value") or label).strip()
            if not label or not value:
                continue
            normalized.append(
                {
                    "label": label,
                    "value": value,
                    "description": str(item.get("description") or "").strip(),
                }
            )
        elif isinstance(item, str) and item.strip():
            text = item.strip()
            normalized.append({"label": text, "value": text, "description": ""})
    return normalized


def _normalize_questions(questions: Any) -> list[dict[str, Any]]:
    normalized = []
    seen_ids = set()
    for item in questions or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip()
        question = str(item.get("question") or "").strip()
        if not qid or qid in seen_ids or not question:
            continue
        seen_ids.add(qid)
        normalized.append(
            {
                "header": str(item.get("header") or "").strip(),
                "id": qid,
                "question": question,
                "options": _normalize_options(item.get("options")),
            }
        )
    return normalized


def _match_option(raw_value: Any, options: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not options:
        return None
    text = str(raw_value or "").strip()
    if not text:
        return None
    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(options):
            return options[index]
    lowered = text.lower()
    for option in options:
        if lowered in {
            str(option.get("label") or "").strip().lower(),
            str(option.get("value") or "").strip().lower(),
        }:
            return option
    return None


def _build_response_payload(
    request: dict[str, Any],
    *,
    status: str,
    approved: bool,
    text: str = "",
    selected_options: list[str] | None = None,
    answers: dict[str, Any] | None = None,
    raw_value: Any = None,
) -> dict[str, Any]:
    return {
        "request_id": request.get("request_id"),
        "status": status,
        "approved": approved,
        "text": text or "",
        "selected_options": list(selected_options or []),
        "answers": dict(answers or {}),
        "raw_value": raw_value,
        "resolved_at": _utc_now_iso(),
    }


def parse_interaction_reply(
    request: dict[str, Any] | None,
    raw_value: Any,
) -> tuple[dict[str, Any], bool, str]:
    req = dict(request or {})
    kind = _normalize_kind(req.get("kind") or "text")
    allow_free_text = bool(req.get("allow_free_text"))
    options = _normalize_options(req.get("options"))
    questions = _normalize_questions(req.get("questions"))

    if isinstance(raw_value, dict) and raw_value.get("request_id"):
        payload = _build_response_payload(
            req,
            status=str(raw_value.get("status") or "completed"),
            approved=bool(raw_value.get("approved")),
            text=str(raw_value.get("text") or ""),
            selected_options=[
                str(item)
                for item in (raw_value.get("selected_options") or [])
                if str(item).strip()
            ],
            answers=raw_value.get("answers") if isinstance(raw_value.get("answers"), dict) else {},
            raw_value=raw_value.get("raw_value", raw_value),
        )
        return payload, True, ""

    if kind == "approval":
        if isinstance(raw_value, bool):
            return _build_response_payload(
                req,
                status="completed",
                approved=raw_value,
                raw_value=raw_value,
            ), True, ""
        raw_text = str(raw_value or "").strip().lower()
        yes_set = {"y", "yes", "ok", "true", "1", "是", "确认", "同意", "继续"}
        no_set = {"n", "no", "false", "0", "否", "取消", "不同意", "拒绝"}
        if raw_text in yes_set:
            return _build_response_payload(
                req,
                status="completed",
                approved=True,
                raw_value=raw_value,
            ), True, ""
        if raw_text in no_set:
            return _build_response_payload(
                req,
                status="completed",
                approved=False,
                raw_value=raw_value,
            ), True, ""
        return _build_response_payload(
            req,
            status="invalid",
            approved=False,
            raw_value=raw_value,
        ), False, "approval reply must be yes/no"

    if kind == "text":
        text = str(raw_value or "").strip()
        if not text:
            return _build_response_payload(
                req,
                status="invalid",
                approved=False,
                raw_value=raw_value,
            ), False, "text reply is empty"
        return _build_response_payload(
            req,
            status="completed",
            approved=True,
            text=text,
            raw_value=raw_value,
        ), True, ""

    if kind == "choice":
        matched = _match_option(raw_value, options)
        if matched:
            value = str(matched.get("value") or "").strip()
            return _build_response_payload(
                req,
                status="completed",
                approved=True,
                text=value,
                selected_options=[value],
                raw_value=raw_value,
            ), True, ""
        if allow_free_text:
            text = str(raw_value or "").strip()
            if text:
                return _build_response_payload(
                    req,
                    status="completed",
                    approved=True,
                    text=text,
                    raw_value=raw_value,
                ), True, ""
        return _build_response_payload(
            req,
            status="invalid",
            approved=False,
            raw_value=raw_value,
        ), False, "choice reply does not match options"

    if kind == "multi_choice":
        if isinstance(raw_value, (list, tuple, set)):
            raw_items = [str(item).strip() for item in raw_value if str(item).strip()]
        else:
            raw_items = [
                item.strip()
                for item in str(raw_value or "").replace("，", ",").split(",")
                if item.strip()
            ]
        selected = []
        free_text = []
        for item in raw_items:
            matched = _match_option(item, options)
            if matched:
                value = str(matched.get("value") or "").strip()
                if value and value not in selected:
                    selected.append(value)
            elif allow_free_text and item not in free_text:
                free_text.append(item)
        if selected or free_text:
            text = ", ".join(free_text)
            return _build_response_payload(
                req,
                status="completed",
                approved=True,
                text=text,
                selected_options=selected,
                raw_value=raw_value,
            ), True, ""
        return _build_response_payload(
            req,
            status="invalid",
            approved=False,
            raw_value=raw_value,
        ), False, "multi_choice reply does not match options"

    if kind == "questionnaire":
        if not isinstance(raw_value, dict):
            return _build_response_payload(
                req,
                status="invalid",
                approved=False,
                raw_value=raw_value,
            ), False, "questionnaire reply must be an object"
        answers = {}
        for item in questions:
            qid = item.get("id") or ""
            if not qid:
                continue
            raw_answer = raw_value.get(qid)
            if isinstance(raw_answer, dict):
                selected = [
                    str(choice).strip()
                    for choice in (raw_answer.get("selected_options") or [])
                    if str(choice).strip()
                ]
                text = str(raw_answer.get("text") or "").strip()
            else:
                selected = []
                text = str(raw_answer or "").strip()
            if not selected and not text:
                continue
            answers[qid] = {
                "selected_options": selected,
                "text": text,
                "raw_value": raw_answer,
            }
        if not answers:
            return _build_response_payload(
                req,
                status="invalid",
                approved=False,
                raw_value=raw_value,
            ), False, "questionnaire reply is empty"
        return _build_response_payload(
            req,
            status="completed",
            approved=True,
            answers=answers,
            raw_value=raw_value,
        ), True, ""

    return _build_response_payload(
        req,
        status="invalid",
        approved=False,
        raw_value=raw_value,
    ), False, "unsupported interaction kind"


@dataclass
class InteractionRequest:
    request_id: str
    session_id: str
    kind: str
    title: str
    message: str
    options: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    allow_free_text: bool = False
    timeout_seconds: float = 120.0
    source_tool: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InteractionResponse:
    request_id: str
    status: str
    approved: bool
    text: str = ""
    selected_options: list[str] = field(default_factory=list)
    answers: dict[str, Any] = field(default_factory=dict)
    raw_value: Any = None
    resolved_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InteractionService(QObject):
    interaction_requested = Signal(dict)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}

    def create_request(
        self,
        session_id: str,
        kind: str,
        message: str,
        *,
        title: str = "",
        options: list[dict[str, Any]] | None = None,
        questions: list[dict[str, Any]] | None = None,
        allow_free_text: bool = False,
        timeout_seconds: float = 120.0,
        source_tool: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = InteractionRequest(
            request_id=uuid.uuid4().hex,
            session_id=(session_id or "").strip(),
            kind=_normalize_kind(kind),
            title=(title or "").strip(),
            message=(message or "").strip(),
            options=_normalize_options(options),
            questions=_normalize_questions(questions),
            allow_free_text=bool(allow_free_text),
            timeout_seconds=_coerce_timeout(timeout_seconds),
            source_tool=(source_tool or "").strip(),
            metadata=dict(metadata or {}),
        )
        event = threading.Event()
        payload = request.to_dict()
        with self._lock:
            self._pending[request.request_id] = {
                "request": payload,
                "event": event,
                "response": None,
                "created_at": time.time(),
            }
        self.interaction_requested.emit(payload)
        resolved = event.wait(request.timeout_seconds)
        with self._lock:
            entry = self._pending.pop(request.request_id, None)
        if not resolved or not entry or not isinstance(entry.get("response"), dict):
            return InteractionResponse(
                request_id=request.request_id,
                status="timeout",
                approved=False,
                raw_value=None,
            ).to_dict()
        return dict(entry["response"])

    def resolve_request(self, request_id: str, raw_value: Any) -> bool:
        with self._lock:
            entry = self._pending.get(request_id)
        if not isinstance(entry, dict):
            return False
        request = entry.get("request") or {}
        payload, valid, _ = parse_interaction_reply(request, raw_value)
        if not valid:
            return False
        entry["response"] = payload
        event = entry.get("event")
        if isinstance(event, threading.Event):
            event.set()
        return True

    def get_pending_request(self, session_id: str) -> dict[str, Any] | None:
        target_session_id = (session_id or "").strip()
        with self._lock:
            matches = [
                (
                    float(entry.get("created_at") or 0.0),
                    dict(entry.get("request") or {}),
                )
                for entry in self._pending.values()
                if isinstance(entry, dict)
                and isinstance(entry.get("request"), dict)
                and (entry["request"].get("session_id") or "").strip() == target_session_id
            ]
        if not matches:
            return None
        matches.sort(key=lambda item: item[0])
        return matches[0][1]

    def cancel_session_requests(self, session_id: str, reason: str = "cancelled") -> int:
        target_session_id = (session_id or "").strip()
        cancelled = 0
        with self._lock:
            entries = [
                entry
                for entry in self._pending.values()
                if isinstance(entry, dict)
                and isinstance(entry.get("request"), dict)
                and (entry["request"].get("session_id") or "").strip() == target_session_id
            ]
        for entry in entries:
            request = dict(entry.get("request") or {})
            entry["response"] = InteractionResponse(
                request_id=request.get("request_id") or "",
                status=reason,
                approved=False,
                raw_value=None,
            ).to_dict()
            event = entry.get("event")
            if isinstance(event, threading.Event):
                event.set()
            cancelled += 1
        return cancelled


def ask_user(message: str, _context: dict[str, Any] | None = None, *, title: str = "请确认", timeout_seconds: float = 120.0) -> bool:
    """
    Backward-compatible approval helper used by legacy skills (for example file-system delete confirmation).
    """
    response = interaction_service.create_request(
        session_id=((_context or {}).get("session_id") or "").strip(),
        kind="approval",
        message=str(message or "").strip(),
        title=str(title or "请确认").strip(),
        allow_free_text=False,
        timeout_seconds=timeout_seconds,
        source_tool="ask_user",
        metadata={"compat": "legacy_ask_user"},
    )
    return bool(response.get("approved"))


interaction_service = InteractionService()
bridge = interaction_service
InteractionBridge = InteractionService
