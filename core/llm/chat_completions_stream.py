"""Translate Chat Completions chunks without changing requests or owning retries."""

import json


def _value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


class _ToolCalls:
    """Per-attempt identity tracking; argument strings are never accumulated here."""

    def __init__(self):
        self.by_index = {}
        self.by_id = {}

    def translate(self, calls, fail):
        if not isinstance(calls, (list, tuple)):
            fail("delta.tool_calls must be an array")
        for position, call in enumerate(calls):
            location = f"delta.tool_calls[{position}]"
            index = _value(call, "index")
            call_id = _value(call, "id")
            if call_id is not None and not isinstance(call_id, str):
                fail(f"{location}.id must be a string")
            if index is None:
                if call_id and call_id in self.by_id:
                    index = self.by_id[call_id]
                elif len(calls) == 1 and len(self.by_index) <= 1:
                    index = next(iter(self.by_index), 0)
                else:
                    fail(f"{location} has no unambiguous index or known id")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                fail(f"{location}.index must be a non-negative integer")
            state = self.by_index.setdefault(index, {"id": "", "name": ""})
            if call_id:
                if state["id"] and state["id"] != call_id:
                    fail(f"{location}.id conflicts with its index")
                if call_id in self.by_id and self.by_id[call_id] != index:
                    fail(f"{location}.id belongs to another index")
                state["id"] = call_id
                self.by_id[call_id] = index
            function = _value(call, "function")
            name = _value(function, "name")
            payload = {}
            if name:
                if not isinstance(name, str):
                    fail(f"{location}.function.name must be a string")
                if state["name"] and state["name"] != name:
                    fail(f"{location}.function.name changed within a call")
                state["name"] = name
                payload["name"] = name
            arguments = _value(function, "arguments")
            if arguments is not None:
                if not isinstance(arguments, str):
                    try:
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    except (TypeError, ValueError, OverflowError):
                        fail(f"{location}.function.arguments is not JSON serializable")
                payload["arguments"] = arguments
            event = {"type": "tool_call", "index": index, "function": payload}
            if call_id:
                event["id"] = call_id
            yield event


def _provider_error(raw, chunk, fail):
    # Do not echo upstream message bodies: gateways may embed prompts or headers.
    status = _value(raw, "status_code") or _value(raw, "status") or _value(chunk, "status_code")
    code = _value(raw, "code") or _value(raw, "type")
    if status is None and isinstance(code, (str, int)):
        try:
            status = int(code)
        except ValueError:
            status = {
                "rate_limit_exceeded": 429,
                "server_error": 500,
                "overloaded_error": 503,
                "invalid_api_key": 401,
                "invalid_request_error": 400,
            }.get(code)
    if isinstance(status, str) and status.isascii() and status.isdecimal():
        status = int(status)
    if isinstance(status, bool) or not isinstance(status, int) or not 400 <= status <= 599:
        status = None
    detail = f"provider returned an error (HTTP {status})" if status else "provider returned an error"
    fail(detail, status_code=status)


def iter_chat_completions_events(stream, *, usage_payload, error_factory):
    """Yield the existing provider events immediately, including trailing usage.

    The caller owns stream registration, closure and retries. ``error_factory``
    keeps the provider's existing exception type for both malformed data and EOF.
    Only the first choice is consumed, as in the existing single-answer API.
    """
    tool_calls = _ToolCalls()
    terminal_seen = False
    for chunk_number, chunk in enumerate(stream, 1):
        def fail(detail, status_code=None):
            error = error_factory(f"Chat Completions chunk {chunk_number}: {detail}.")
            if status_code is not None:
                error.status_code = status_code
            raise error

        raw_error = _value(chunk, "error")
        if raw_error is not None:
            _provider_error(raw_error, chunk, fail)
        usage = usage_payload(chunk)
        if usage:
            yield {"type": "usage", "usage": usage}
        choices = _value(chunk, "choices")
        if not choices:
            continue
        if not isinstance(choices, (list, tuple)):
            fail("choices must be an array")
        choice = choices[0]
        delta = _value(choice, "delta")
        reasoning = _value(delta, "reasoning_content")
        if reasoning is None:
            alias = _value(delta, "reasoning")
            reasoning = alias if isinstance(alias, str) else None
        content = _value(delta, "content")
        calls = _value(delta, "tool_calls")
        finish_reason = _value(choice, "finish_reason")
        # A finish_reason is not transport EOF. Compatible endpoints may repeat
        # it on usage chunks or send more deltas; preserve the original event
        # sequence until the SDK iterator ends, including later incomplete states.
        for kind, text in (("reasoning", reasoning), ("content", content)):
            if text is not None and not isinstance(text, str):
                fail(f"delta.{kind} must be a string")
            if text:
                yield {"type": kind, "content": text}
        if calls:
            yield from tool_calls.translate(calls, fail)
        if finish_reason is not None:
            terminal_seen = True
            yield {
                "type": "provider_terminal",
                "status": "completed" if str(finish_reason) in {"stop", "tool_calls"} else "incomplete",
                "finish_reason": str(finish_reason),
            }
    if not terminal_seen:
        raise error_factory("Chat Completions stream ended without a finish_reason.")
