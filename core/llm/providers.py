from abc import ABC, abstractmethod
import base64
import mimetypes
import os
import json
import time
from .deepseek import (
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
    DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY,
    build_deepseek_request_options,
    is_deepseek_request,
    is_official_deepseek_api,
    normalize_reasoning_effort,
)
from .responses_replay import RESPONSES_REPLAY_INPUT_KEY
from core.conversation_integrity import ensure_tool_call_sequence

API_PROTOCOL_CHAT_COMPLETIONS = "chat_completions"
API_PROTOCOL_RESPONSES = "responses"
SUPPORTED_OPENAI_API_PROTOCOLS = (
    API_PROTOCOL_CHAT_COMPLETIONS,
    API_PROTOCOL_RESPONSES,
)
GPT_5_6_CONTEXT_WINDOW_TOKENS = 1_050_000
DEEPSEEK_RESPONSES_REPLAY_ITEM_TYPES = {
    "reasoning",
    "message",
    "function_call",
    "web_search_call",
}
RESPONSES_WEB_SEARCH_TOOL_TYPES = {
    "web_search",
    "web_search_2025_08_26",
}


def normalize_openai_api_protocol(value):
    protocol = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "chat": API_PROTOCOL_CHAT_COMPLETIONS,
        "chat_completion": API_PROTOCOL_CHAT_COMPLETIONS,
        "chat_completions": API_PROTOCOL_CHAT_COMPLETIONS,
        "response": API_PROTOCOL_RESPONSES,
        "responses": API_PROTOCOL_RESPONSES,
    }
    return aliases.get(protocol, API_PROTOCOL_CHAT_COMPLETIONS)


def is_gpt_5_6_model(model_name):
    name = str(model_name or "").strip().lower()
    return name == "gpt-5.6" or name.startswith("gpt-5.6-")

class LLMProvider(ABC):
    @abstractmethod
    def chat_stream(self, messages, tools=None, prompt_cache_key=None):
        """
        Yields chunks of response.
        Each chunk should be a dict with:
        - type: 'content' | 'reasoning' | 'tool_call' | 'usage' | 'error'
          | 'response_items' | 'server_tool_status'
        - content: str (for content/reasoning)
        - tool_call: dict (for tool_call, partial or complete)
        `response_items` carries provider output items needed for stateless replay;
        `server_tool_status` reports provider-executed tools without local execution.
        """
        pass

    def test_connection(self, timeout=20):
        raise NotImplementedError("当前模型服务未实现连接测试。")


SUPPORTED_VISION_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}

MAX_INLINE_FILE_BYTES = 128 * 1024
TEXT_ATTACHMENT_MIME_PREFIXES = ("text/",)
TEXT_ATTACHMENT_MIME_TYPES = {
    "application/json",
    "application/javascript",
    "application/xml",
    "application/x-yaml",
}
TEXT_ATTACHMENT_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".conf",
    ".config",
    ".csv",
    ".ini",
    ".js",
    ".json",
    ".log",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def _guess_image_mime_type(path):
    guessed, _encoding = mimetypes.guess_type(path)
    guessed = str(guessed or "").strip().lower()
    return guessed if guessed in SUPPORTED_VISION_MIME_TYPES else ""


def _build_data_url_from_path(path):
    mime_type = _guess_image_mime_type(path)
    if not mime_type or not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _is_text_attachment(path):
    guessed, _encoding = mimetypes.guess_type(path)
    guessed = str(guessed or "").strip().lower()
    if guessed.startswith(TEXT_ATTACHMENT_MIME_PREFIXES) or guessed in TEXT_ATTACHMENT_MIME_TYPES:
        return True
    return os.path.splitext(str(path or ""))[1].lower() in TEXT_ATTACHMENT_EXTENSIONS


def _attachment_display_name(part, path):
    name = str(part.get("name") or "").strip() if isinstance(part, dict) else ""
    return name or os.path.basename(path) or path


def _build_file_attachment_text(part):
    path = str(part.get("path") or "").strip()
    name = _attachment_display_name(part, path)
    if not path:
        return f"[Attached file: {name}]\nError: missing local file path."
    if not os.path.isfile(path):
        return f"[Attached file: {name}]\nPath: {path}\nError: file does not exist."
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return f"[Attached file: {name}]\nPath: {path}\nError: could not stat file: {exc}"
    if size > MAX_INLINE_FILE_BYTES:
        return (
            f"[Attached file: {name}]\n"
            f"Path: {path}\n"
            f"Size: {size} bytes\n"
            "Content was not inlined because the file is larger than 131072 bytes. "
            "Use an appropriate file-reading tool if the exact content is needed."
        )
    if not _is_text_attachment(path):
        return (
            f"[Attached file: {name}]\n"
            f"Path: {path}\n"
            f"Size: {size} bytes\n"
            "Content was not inlined because this file type is not treated as plain text. "
            "Use an appropriate document or file-reading tool if the exact content is needed."
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                content = handle.read()
        except UnicodeDecodeError as exc:
            return f"[Attached file: {name}]\nPath: {path}\nError: could not decode as UTF-8 text: {exc}"
    except OSError as exc:
        return f"[Attached file: {name}]\nPath: {path}\nError: could not read file: {exc}"
    return f"[Attached file: {name}]\nPath: {path}\nContent:\n{content}"


def _extract_image_paths(content_parts):
    image_paths = []
    for part in content_parts or []:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "").strip().lower() != "input_image":
            continue
        path = str(part.get("path") or "").strip()
        if path:
            image_paths.append(path)
    return image_paths


def _build_openai_user_content(text_content, content_parts, supports_vision=False):
    content = []
    if text_content:
        content.append({"type": "text", "text": text_content})
    file_attachment_count = 0
    for part in content_parts or []:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "").strip().lower() != "input_file":
            continue
        file_attachment_count += 1
        content.append({"type": "text", "text": _build_file_attachment_text(part)})
    if not supports_vision:
        if file_attachment_count == 0:
            return []
        return content
    for path in _extract_image_paths(content_parts):
        data_url = _build_data_url_from_path(path)
        if not data_url:
            content.append({
                "type": "text",
                "text": f"[Attached image]\nPath: {path}\nError: image could not be read or is not a supported image type.",
            })
            continue
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": data_url,
                    "detail": "auto",
                },
            }
        )
    return content


def _build_openai_vision_content(text_content, content_parts):
    return _build_openai_user_content(text_content, content_parts, supports_vision=True)

class OpenAIProvider(LLMProvider):
    protocol_family = "openai-compatible"

    def __init__(
        self,
        api_key,
        base_url,
        model_name,
        thinking_enabled=DEFAULT_DEEPSEEK_THINKING_ENABLED,
        reasoning_effort=DEFAULT_DEEPSEEK_REASONING_EFFORT,
        supports_vision=False,
        stream_usage_enabled=True,
        prompt_cache_key_param="",
        api_protocol=API_PROTOCOL_CHAT_COMPLETIONS,
    ):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.base_url = base_url
        self.model_name = model_name
        self.thinking_enabled = bool(thinking_enabled)
        self.reasoning_effort = normalize_reasoning_effort(reasoning_effort)
        self.supports_vision = bool(supports_vision)
        self.stream_usage_enabled = bool(stream_usage_enabled)
        self.prompt_cache_key_param = str(prompt_cache_key_param or "").strip()
        self.api_protocol = normalize_openai_api_protocol(api_protocol)
        self.requires_responses_replay = self.api_protocol == API_PROTOCOL_RESPONSES
        self.requires_deepseek_responses_replay = bool(
            self.requires_responses_replay
            and is_official_deepseek_api(self.base_url)
        )
        self.provider_name = (
            "OpenAI Responses"
            if self.api_protocol == API_PROTOCOL_RESPONSES
            else "OpenAI Chat Completions"
        )

    def chat_stream(self, messages, tools=None, prompt_cache_key=None):
        try:
            ensure_tool_call_sequence(messages, context=f"{self.provider_name} request")
            if self.api_protocol == API_PROTOCOL_RESPONSES:
                yield from self._responses_stream(messages, tools=tools, prompt_cache_key=prompt_cache_key)
                return
            # Clean messages for OpenAI (remove internal keys if any)
            clean_messages = self._prepare_messages(messages)
            
            # Prepare tools
            api_tools = tools if tools else None
            
            # Common params
            params = {
                "model": self.model_name,
                "messages": clean_messages,
                "stream": True
            }
            if api_tools:
                params["tools"] = api_tools
            if prompt_cache_key:
                self._apply_prompt_cache_key(params, prompt_cache_key)
            self._apply_stream_usage_options(params)
            if is_deepseek_request(self.model_name, self.base_url):
                params.update(build_deepseek_request_options(
                    self.model_name,
                    self.base_url,
                    thinking_enabled=self.thinking_enabled,
                    reasoning_effort=self.reasoning_effort,
                ))
            elif self.reasoning_effort:
                params["reasoning_effort"] = self.reasoning_effort

            stream = self._create_chat_completion_stream(params)

            for chunk in stream:
                usage_payload = self._usage_payload(chunk)
                if usage_payload:
                    yield {"type": "usage", "usage": usage_payload}
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                    
                delta = choices[0].delta
                
                # 1. Reasoning (DeepSeek style)
                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    yield {"type": "reasoning", "content": delta.reasoning_content}
                
                # 2. Content
                delta_content = getattr(delta, "content", None)
                if delta_content:
                    yield {"type": "content", "content": delta_content}
                
                # 3. Tool Calls
                delta_tool_calls = getattr(delta, "tool_calls", None)
                if delta_tool_calls:
                    for tc in delta_tool_calls:
                        function = getattr(tc, "function", None)
                        raw_arguments = getattr(function, "arguments", None) if function else None
                        if raw_arguments is None:
                            arguments = None
                        elif isinstance(raw_arguments, str):
                            arguments = raw_arguments
                        else:
                            try:
                                arguments = json.dumps(raw_arguments, ensure_ascii=False)
                            except Exception:
                                arguments = str(raw_arguments)

                        function_payload = {}
                        name = getattr(function, "name", None) if function else None
                        if name:
                            function_payload["name"] = str(name)
                        if arguments is not None:
                            function_payload["arguments"] = arguments

                        tool_call_payload = {
                            "type": "tool_call",
                            "index": getattr(tc, "index", 0),
                            "function": function_payload,
                        }
                        tool_call_id = getattr(tc, "id", None)
                        if tool_call_id:
                            tool_call_payload["id"] = str(tool_call_id)
                        yield tool_call_payload
                        
        except Exception as e:
            yield {"type": "error", "content": str(e)}

    def test_connection(self, timeout=20):
        if self.api_protocol == API_PROTOCOL_RESPONSES:
            params = {
                "model": self.model_name,
                "input": "Reply with OK.",
                "stream": False,
                "max_output_tokens": 8,
                "timeout": timeout,
            }
            if self.reasoning_effort:
                params["reasoning"] = {"effort": self.reasoning_effort}
            response = self.client.responses.create(**params)
            return str(getattr(response, "output_text", "") or "").strip()
        params = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "stream": False,
            "max_tokens": 8,
            "timeout": timeout,
        }
        if is_deepseek_request(self.model_name, self.base_url):
            params.update(build_deepseek_request_options(
                self.model_name,
                self.base_url,
                thinking_enabled=self.thinking_enabled,
                reasoning_effort=self.reasoning_effort,
            ))
        elif self.reasoning_effort:
            params["reasoning_effort"] = self.reasoning_effort
        response = self.client.chat.completions.create(**params)
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise RuntimeError("服务未返回任何响应内容。")
        message = getattr(choices[0], "message", None)
        return str(getattr(message, "content", "") or "").strip()

    @staticmethod
    def _object_value(obj, name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    @classmethod
    def _response_error_message(cls, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        for name in ("message", "reason", "detail", "code"):
            text = str(cls._object_value(value, name, "") or "").strip()
            if text:
                return text
        return ""

    @classmethod
    def _json_compatible(cls, value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {
                str(key): cls._json_compatible(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._json_compatible(item) for item in value]
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return cls._json_compatible(model_dump(mode="json", exclude_unset=True))
            except TypeError:
                return cls._json_compatible(model_dump())
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return cls._json_compatible(to_dict())
        attributes = getattr(value, "__dict__", None)
        if isinstance(attributes, dict):
            return cls._json_compatible({
                key: item for key, item in attributes.items()
                if not str(key).startswith("_")
            })
        raise TypeError(f"Responses API item contains a non-serializable {type(value).__name__} value.")

    def _normalize_deepseek_replay_items(self, raw_items, source="response"):
        if not isinstance(raw_items, (list, tuple)) or not raw_items:
            raise RuntimeError(
                "DeepSeek Responses completed without replayable output items; "
                "the stateless conversation cannot continue safely."
            )

        normalized = []
        has_reasoning_text = False
        has_tool_item = False
        for raw_item in raw_items:
            item = self._json_compatible(raw_item)
            if not isinstance(item, dict):
                raise RuntimeError(f"DeepSeek Responses {source} contains an invalid output item.")
            item_type = str(item.get("type") or "").strip()
            if item_type not in DEEPSEEK_RESPONSES_REPLAY_ITEM_TYPES:
                raise RuntimeError(
                    f"DeepSeek Responses returned unsupported replay item type: {item_type or 'empty'}."
                )

            if item_type == "reasoning":
                if not str(item.get("id") or "").strip():
                    raise RuntimeError("DeepSeek Responses reasoning item is missing id.")
                if not isinstance(item.get("summary"), list):
                    raise RuntimeError("DeepSeek Responses reasoning item is missing summary.")
                content = item.get("content")
                if not isinstance(content, list):
                    raise RuntimeError("DeepSeek Responses reasoning item is missing content.")
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if (
                        str(part.get("type") or "") == "reasoning_text"
                        and str(part.get("text") or "")
                    ):
                        has_reasoning_text = True
            elif item_type == "message":
                if (
                    not str(item.get("id") or "").strip()
                    or str(item.get("role") or "") != "assistant"
                    or not isinstance(item.get("content"), list)
                ):
                    raise RuntimeError("DeepSeek Responses returned an invalid assistant message item.")
            elif item_type == "function_call":
                has_tool_item = True
                if (
                    not str(item.get("call_id") or "").strip()
                    or not str(item.get("name") or "").strip()
                    or not isinstance(item.get("arguments"), str)
                ):
                    raise RuntimeError("DeepSeek Responses returned an invalid function_call item.")
            elif item_type == "web_search_call":
                has_tool_item = True
                if (
                    not str(item.get("id") or "").strip()
                    or not str(item.get("status") or "").strip()
                    or not isinstance(item.get("action"), dict)
                ):
                    raise RuntimeError("DeepSeek Responses returned an invalid web_search_call item.")
            normalized.append(item)

        if (
            has_tool_item
            and (self.thinking_enabled or self.reasoning_effort)
            and not has_reasoning_text
        ):
            raise RuntimeError(
                "DeepSeek Responses used a tool but did not return replayable reasoning_text; "
                "tool execution was stopped to prevent an invalid follow-up request."
            )
        return normalized

    def _normalize_responses_replay_items(self, raw_items, source="response"):
        if self.requires_deepseek_responses_replay:
            return self._normalize_deepseek_replay_items(raw_items, source=source)
        if not isinstance(raw_items, (list, tuple)) or not raw_items:
            raise RuntimeError(
                "Responses completed without replayable output items; "
                "the append-only conversation cannot continue safely."
            )
        normalized = []
        for raw_item in raw_items:
            item = self._json_compatible(raw_item)
            if not isinstance(item, dict):
                raise RuntimeError(f"Responses {source} contains an invalid output item.")
            item_type = str(item.get("type") or "").strip()
            if not item_type:
                raise RuntimeError(f"Responses {source} contains an output item without type.")
            if item_type == "function_call" and (
                not str(item.get("call_id") or "").strip()
                or not str(item.get("name") or "").strip()
                or not isinstance(item.get("arguments"), str)
            ):
                raise RuntimeError("Responses returned an invalid function_call replay item.")
            normalized.append(item)
        return normalized

    def _responses_stream(self, messages, tools=None, prompt_cache_key=None):
        params = {
            "model": self.model_name,
            "input": self._prepare_responses_input(messages),
            "stream": True,
        }
        api_tools = self._prepare_responses_tools(tools)
        if api_tools:
            params["tools"] = api_tools
        if self.reasoning_effort:
            params["reasoning"] = {"effort": self.reasoning_effort}
        if prompt_cache_key and not self.requires_deepseek_responses_replay:
            params["prompt_cache_key"] = str(prompt_cache_key)

        stream = self.client.responses.create(**params)
        tool_indexes = {}
        next_tool_index = 0
        for event in stream:
            event_type = str(self._object_value(event, "type", "") or "")
            if event_type in {"response.output_text.delta", "response.refusal.delta"}:
                delta = self._object_value(event, "delta", "")
                if delta:
                    yield {"type": "content", "content": str(delta)}
                continue
            if event_type in {
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            }:
                delta = self._object_value(event, "delta", "")
                if delta:
                    yield {"type": "reasoning", "content": str(delta)}
                continue
            if event_type in {
                "response.web_search_call.in_progress",
                "response.web_search_call.searching",
                "response.web_search_call.completed",
            }:
                status = event_type.rsplit(".", 1)[-1]
                payload = {
                    "type": "server_tool_status",
                    "name": "web_search",
                    "status": status,
                }
                item_id = str(self._object_value(event, "item_id", "") or "")
                if item_id:
                    payload["id"] = item_id
                output_index = self._object_value(event, "output_index")
                if output_index is not None:
                    payload["output_index"] = output_index
                yield payload
                continue
            if event_type == "response.output_item.added":
                item = self._object_value(event, "item")
                if str(self._object_value(item, "type", "") or "") != "function_call":
                    continue
                item_id = str(self._object_value(item, "id", "") or "")
                output_index = self._object_value(event, "output_index", next_tool_index)
                try:
                    index = int(output_index)
                except (TypeError, ValueError):
                    index = next_tool_index
                next_tool_index = max(next_tool_index, index + 1)
                if item_id:
                    tool_indexes[item_id] = index
                call_id = str(self._object_value(item, "call_id", "") or item_id)
                name = str(self._object_value(item, "name", "") or "")
                payload = {"type": "tool_call", "index": index, "function": {}}
                if call_id:
                    payload["id"] = call_id
                if name:
                    payload["function"]["name"] = name
                arguments = self._object_value(item, "arguments")
                if arguments:
                    payload["function"]["arguments"] = str(arguments)
                yield payload
                continue
            if event_type == "response.function_call_arguments.delta":
                item_id = str(self._object_value(event, "item_id", "") or "")
                output_index = self._object_value(event, "output_index", 0)
                try:
                    fallback_index = int(output_index)
                except (TypeError, ValueError):
                    fallback_index = 0
                index = tool_indexes.get(item_id, fallback_index)
                delta = self._object_value(event, "delta", "")
                if delta:
                    yield {
                        "type": "tool_call",
                        "index": index,
                        "function": {"arguments": str(delta)},
                    }
                continue
            if event_type == "response.completed":
                response = self._object_value(event, "response")
                replay_items = self._normalize_responses_replay_items(
                    self._object_value(response, "output"),
                )
                yield {"type": "response_items", "items": replay_items}
                if self.requires_deepseek_responses_replay:
                    for item in replay_items:
                        if item.get("type") == "web_search_call" and item.get("status") == "failed":
                            reason = (
                                self._response_error_message(item.get("error"))
                                or self._response_error_message(item.get("action"))
                                or "DeepSeek did not return error details for the failed web search."
                            )
                            yield {
                                "type": "server_tool_status",
                                "name": "web_search",
                                "id": item.get("id") or "",
                                "status": "failed",
                                "reason": reason,
                            }
                usage_payload = self._usage_payload(response)
                if usage_payload:
                    yield {"type": "usage", "usage": usage_payload}
                continue
            if event_type in {"response.failed", "response.incomplete", "error"}:
                response = self._object_value(event, "response")
                error = self._object_value(event, "error") or self._object_value(response, "error")
                message = self._object_value(error, "message", "") or self._object_value(response, "incomplete_details", "")
                raise RuntimeError(str(message or f"Responses API stream ended with {event_type}."))

    def _prepare_responses_tools(self, tools):
        prepared = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") != "function":
                prepared.append(dict(tool))
                continue
            function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
            name = str(function.get("name") or "").strip()
            if not name:
                raise ValueError("Responses API function tool is missing name.")
            entry = {
                "type": "function",
                "name": name,
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            }
            description = str(function.get("description") or "").strip()
            if description:
                entry["description"] = description
            if "strict" in function:
                entry["strict"] = bool(function.get("strict"))
            prepared.append(entry)
        if self.requires_deepseek_responses_replay:
            deduped = []
            has_web_search = False
            for tool in prepared:
                tool_type = str(tool.get("type") or "") if isinstance(tool, dict) else ""
                if tool_type in RESPONSES_WEB_SEARCH_TOOL_TYPES:
                    if has_web_search:
                        continue
                    has_web_search = True
                deduped.append(tool)
            if not has_web_search:
                deduped.append({"type": "web_search"})
            prepared = deduped
        return prepared

    def _prepare_responses_input(self, messages):
        items = []
        for message in self._prepare_messages(messages):
            role = str(message.get("role") or "").strip().lower()
            content = message.get("content")
            if role in {"system", "developer"}:
                items.append({"role": "developer", "content": self._responses_content(content, "input_text")})
                continue
            if role == "user":
                items.append({"role": "user", "content": self._responses_content(content, "input_text")})
                continue
            if role == "assistant":
                replay_items = message.get(RESPONSES_REPLAY_INPUT_KEY)
                if replay_items is None and self.requires_deepseek_responses_replay:
                    replay_items = message.get(DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY)
                if self.requires_responses_replay and replay_items:
                    items.extend(self._normalize_responses_replay_items(replay_items, source="history"))
                    continue

                reasoning_content = str(message.get("reasoning_content") or "")
                if self.requires_deepseek_responses_replay and reasoning_content:
                    message_id = str(message.get("id") or "").strip()
                    if not message_id:
                        raise ValueError(
                            "DeepSeek Responses history contains reasoning_text without a stable assistant message id."
                        )
                    reasoning_id = message_id if message_id.startswith("rs_") else f"rs_{message_id}"
                    items.append({
                        "type": "reasoning",
                        "id": reasoning_id,
                        "summary": [],
                        "content": [{"type": "reasoning_text", "text": reasoning_content}],
                    })
                elif (
                    self.requires_deepseek_responses_replay
                    and message.get("tool_calls")
                    and (self.thinking_enabled or self.reasoning_effort)
                ):
                    raise ValueError(
                        "DeepSeek Responses 工具调用历史缺少 reasoning_text，无法安全续接。"
                        "请新建任务后重试；原历史不会被静默裁剪。"
                    )
                if content:
                    items.append({"role": "assistant", "content": self._responses_content(content, "output_text")})
                for tool_call in message.get("tool_calls") or []:
                    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                    call_id = str(tool_call.get("id") or "").strip()
                    name = str(function.get("name") or "").strip()
                    if not call_id or not name:
                        raise ValueError("Responses API history contains an invalid function call.")
                    arguments = function.get("arguments")
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments or {}, ensure_ascii=False)
                    items.append({
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    })
                continue
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "").strip()
                if not call_id:
                    raise ValueError("Responses API tool result is missing tool_call_id.")
                output = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                items.append({"type": "function_call_output", "call_id": call_id, "output": output or ""})
                continue
            raise ValueError(f"Responses API does not support message role: {role or 'empty'}")
        return items

    def _responses_content(self, content, text_type):
        if isinstance(content, str):
            return [{"type": text_type, "text": content}]
        prepared = []
        for part in content or []:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").strip().lower()
            if part_type == "text":
                prepared.append({"type": text_type, "text": str(part.get("text") or "")})
            elif part_type == "image_url":
                image_url = part.get("image_url") if isinstance(part.get("image_url"), dict) else {}
                url = str(image_url.get("url") or "").strip()
                if url:
                    prepared.append({
                        "type": "input_image",
                        "image_url": url,
                        "detail": str(image_url.get("detail") or "auto"),
                    })
        return prepared

    def _apply_prompt_cache_key(self, params, prompt_cache_key):
        key = str(prompt_cache_key or "").strip()
        if not key or not self.prompt_cache_key_param:
            return
        if self.prompt_cache_key_param == "prompt_cache_key":
            params["prompt_cache_key"] = key
        elif self.prompt_cache_key_param == "extra_body.prompt_cache_key":
            extra_body = dict(params.get("extra_body") or {})
            extra_body["prompt_cache_key"] = key
            params["extra_body"] = extra_body

    def _apply_stream_usage_options(self, params):
        if not self.stream_usage_enabled:
            return
        stream_options = dict(params.get("stream_options") or {})
        stream_options["include_usage"] = True
        params["stream_options"] = stream_options

    def _create_chat_completion_stream(self, params):
        try:
            return self.client.chat.completions.create(**params)
        except Exception as exc:
            if "stream_options" not in params or not self._should_retry_without_stream_options(exc):
                raise
            fallback = dict(params)
            fallback.pop("stream_options", None)
            return self.client.chat.completions.create(**fallback)

    def _should_retry_without_stream_options(self, exc):
        text = str(exc or "").lower()
        if "stream_options" in text:
            return True
        retry_markers = (
            "unknown parameter",
            "unsupported parameter",
            "invalid parameter",
            "unrecognized",
            "not supported",
            "bad request",
            "400",
        )
        return any(marker in text for marker in retry_markers)

    def _usage_payload(self, chunk):
        usage = getattr(chunk, "usage", None)
        if usage is None and isinstance(chunk, dict):
            usage = chunk.get("usage")
        if usage is None:
            return None

        def _value(obj, name):
            if isinstance(obj, dict):
                return obj.get(name)
            return getattr(obj, name, None)

        payload = {}
        for source, target in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
            ("total_tokens", "total_tokens"),
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
        ):
            value = _value(usage, source)
            if value is not None:
                payload[target] = value

        cached_tokens = None
        uncached_tokens = None
        cache_metrics_status = "unavailable"

        # DeepSeek Chat Completions exposes cache usage as top-level hit/miss
        # fields, while OpenAI-compatible responses commonly nest cached_tokens
        # under prompt/input token details.  Keep the schemas explicit so a
        # missing field cannot reuse a previous request's value.
        deepseek_hit = _value(usage, "prompt_cache_hit_tokens")
        deepseek_miss = _value(usage, "prompt_cache_miss_tokens")
        if deepseek_hit is not None or deepseek_miss is not None:
            cache_metrics_status = "deepseek_prompt_cache"
            if deepseek_hit is not None:
                cached_tokens = deepseek_hit
            if deepseek_miss is not None:
                uncached_tokens = deepseek_miss

        for details_name in ("prompt_tokens_details", "input_tokens_details"):
            if cached_tokens is not None:
                break
            details = _value(usage, details_name)
            if details is None:
                continue
            cached_tokens = _value(details, "cached_tokens")
            if cached_tokens is not None:
                cache_metrics_status = f"{details_name}.cached_tokens"
                break
        if cached_tokens is not None:
            payload["cached_input_tokens"] = cached_tokens
        if uncached_tokens is not None:
            payload["uncached_input_tokens"] = uncached_tokens

        input_tokens = payload.get("input_tokens")
        if input_tokens is None and cached_tokens is not None and uncached_tokens is not None:
            try:
                payload["input_tokens"] = int(cached_tokens) + int(uncached_tokens)
                input_tokens = payload["input_tokens"]
            except Exception:
                pass
        if input_tokens is not None and cached_tokens is not None and uncached_tokens is None:
            try:
                input_count = int(input_tokens)
                cached_count = int(cached_tokens)
                uncached_tokens = max(0, input_count - cached_count)
                payload["uncached_input_tokens"] = uncached_tokens
                payload["cache_hit_rate"] = cached_count / input_count if input_count > 0 else 0
            except Exception:
                pass
        elif input_tokens is not None and cached_tokens is not None and uncached_tokens is not None:
            try:
                input_count = int(input_tokens)
                cached_count = int(cached_tokens)
                payload["uncached_input_tokens"] = max(0, int(uncached_tokens))
                payload["cache_hit_rate"] = cached_count / input_count if input_count > 0 else 0
            except Exception:
                pass
        if cache_metrics_status == "unavailable":
            payload["cache_metrics_status"] = "unavailable"
        else:
            payload["cache_metrics_status"] = cache_metrics_status
        return payload or None

    def _prepare_messages(self, messages):
        # Deep copy and clean
        clean = []
        is_deepseek = bool(is_deepseek_request(self.model_name, self.base_url))
        for msg in messages:
            m = msg.copy()
            content_parts = m.pop("content_parts", None)
            # Remove internal keys
            m.pop("reasoning", None)
            
            # Most OpenAI-compatible providers reject reasoning_content entirely.
            # DeepSeek thinking mode requires replaying non-empty assistant
            # reasoning_content for prior tool-using turns.
            if "reasoning_content" in m:
                if (
                    not is_deepseek
                    or not m.get("reasoning_content")
                    or m.get("role") != "assistant"
                ):
                    m.pop("reasoning_content", None)
            
            # Ensure tool_calls are correctly formatted if present
            if "tool_calls" in m and not m["tool_calls"]:
                del m["tool_calls"]
            
            # When tool_calls exist, OpenAI-compatible APIs expect content to be null
            if m.get("role") == "assistant" and "tool_calls" in m and not m.get("content"):
                m["content"] = None
            elif (
                m.get("role") == "user"
                and isinstance(content_parts, list)
            ):
                user_content = _build_openai_user_content(
                    m.get("content") or "",
                    content_parts,
                    supports_vision=self.supports_vision,
                )
                if user_content:
                    m["content"] = user_content
                
            clean.append(m)
        return clean

class MoonshotProvider(OpenAIProvider):
    """
    Optimized Provider for Moonshot AI (Kimi 2.5)
    Reference: https://platform.moonshot.cn/docs/guide/use-kimi-api-to-complete-tool-calls
    """
    def __init__(
        self,
        api_key,
        base_url,
        model_name,
        thinking_enabled=DEFAULT_DEEPSEEK_THINKING_ENABLED,
        reasoning_effort=DEFAULT_DEEPSEEK_REASONING_EFFORT,
        supports_vision=False,
        stream_usage_enabled=True,
        prompt_cache_key_param="",
        api_protocol=API_PROTOCOL_CHAT_COMPLETIONS,
    ):
        # Ensure correct Base URL if user selects 'moonshot' but leaves default URL
        if not base_url or "api.openai.com" in base_url:
            base_url = "https://api.moonshot.cn/v1"
        super().__init__(
            api_key,
            base_url,
            model_name,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
            supports_vision=supports_vision,
            stream_usage_enabled=stream_usage_enabled,
            prompt_cache_key_param=prompt_cache_key_param,
            api_protocol=api_protocol,
        )

    def _prepare_messages(self, messages):
        clean = []
        for msg in messages:
            m = msg.copy()
            content_parts = m.pop("content_parts", None)
            # Moonshot strictly does not support 'reasoning_content' or 'reasoning' fields
            m.pop("reasoning", None)
            m.pop("reasoning_content", None)
            
            # Kimi requires strictly valid tool_calls
            if "tool_calls" in m and not m["tool_calls"]:
                del m["tool_calls"]
                
            # Filter out empty content if tool_calls are present (Standard OpenAI allows it, but being explicit is safer)
            if m.get("role") == "assistant" and "tool_calls" in m and not m.get("content"):
                m["content"] = None # OpenAI SDK handles None as null, which is valid when tool_calls exist
            elif (
                m.get("role") == "user"
                and isinstance(content_parts, list)
            ):
                user_content = _build_openai_user_content(
                    m.get("content") or "",
                    content_parts,
                    supports_vision=self.supports_vision,
                )
                if user_content:
                    m["content"] = user_content

            clean.append(m)
        return clean

class AnthropicProvider(LLMProvider):
    def __init__(self, api_key, base_url, model_name, supports_vision=False):
        import anthropic
        from anthropic import Anthropic
        # Anthropic SDK handles base_url differently usually, but we can pass it
        client_kwargs = {"base_url": base_url}
        cleaned_key = str(api_key or "").strip()
        if self._uses_bearer_auth(base_url):
            if cleaned_key.lower().startswith("bearer "):
                cleaned_key = cleaned_key[7:].strip()
            client_kwargs["auth_token"] = cleaned_key
            omit_header = getattr(anthropic, "omit", None)
            if omit_header is not None:
                client_kwargs["default_headers"] = {"X-Api-Key": omit_header}
        else:
            client_kwargs["api_key"] = cleaned_key
        self.client = Anthropic(**client_kwargs)
        self.base_url = base_url
        self.model_name = model_name
        self.supports_vision = bool(supports_vision)

    @staticmethod
    def _uses_bearer_auth(base_url):
        text = str(base_url or "").strip().lower().rstrip("/")
        return text.endswith("/coding/anthropic") or "/coding/anthropic/" in text

    def chat_stream(self, messages, tools=None, prompt_cache_key=None):
        try:
            system_prompt, api_messages = self._prepare_messages(messages)
            
            # Convert tools to Anthropic format
            api_tools = self._convert_tools(tools) if tools else None
            
            # Anthropic parameters
            kwargs = {
                "model": self.model_name,
                "messages": api_messages,
                "max_tokens": 8192 # Required by Anthropic
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            if api_tools:
                kwargs["tools"] = api_tools

            with self.client.messages.stream(**kwargs) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        delta_type = getattr(event.delta, "type", "")
                        if delta_type == "text_delta":
                            yield {"type": "content", "content": event.delta.text}
                        elif delta_type == "input_json_delta":
                            yield {
                                "type": "tool_call",
                                "index": event.index,
                                "function": {
                                    "arguments": getattr(event.delta, "partial_json", "") or ""
                                }
                            }
                            
                    elif event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            yield {
                                "type": "tool_call",
                                "index": event.index,
                                "id": event.content_block.id,
                                "function": {
                                    "name": event.content_block.name,
                                    "arguments": "" # Start
                                }
                            }

        except Exception as e:
            yield {"type": "error", "content": str(e)}

    def test_connection(self, timeout=20):
        response = self.client.messages.create(
            model=self.model_name,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=8,
            timeout=timeout,
        )
        blocks = getattr(response, "content", None) or []
        text = "".join(str(getattr(block, "text", "") or "") for block in blocks).strip()
        if not text:
            raise RuntimeError("服务未返回任何响应内容。")
        return text

    def _prepare_messages(self, messages):
        """
        Convert OpenAI-style messages to Anthropic format.
        - Extract system message.
        - Convert 'image_url' content to Anthropic image block.
        - Merge consecutive tool results into the single user message required
          immediately after an assistant tool-use turn.
        """
        system_prompt = ""
        api_messages = []
        pending_tool_results = []

        def flush_tool_results():
            if not pending_tool_results:
                return
            api_messages.append({
                "role": "user",
                "content": list(pending_tool_results),
            })
            pending_tool_results.clear()
        
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            content_parts = msg.get("content_parts")

            if role == "tool":
                tool_call_id = str(msg.get("tool_call_id") or "").strip()
                if not tool_call_id:
                    raise ValueError("Anthropic tool result is missing tool_call_id.")
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content if content is not None else "",
                })
                continue

            flush_tool_results()
            
            if role == "system":
                system_prompt += content + "\n"
                continue
                
            # Handle multi-modal content
            new_content = []
            if (
                role == "user"
                and isinstance(content_parts, list)
            ):
                content = _build_openai_user_content(
                    content or "",
                    content_parts,
                    supports_vision=self.supports_vision,
                )
            if isinstance(content, str):
                new_content = content
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        new_content.append({"type": "text", "text": part["text"]})
                    elif part.get("type") == "image_url":
                        # Convert OpenAI image_url to Anthropic image
                        # OpenAI: {"url": "data:image/jpeg;base64,..."} or "https://..."
                        url = part["image_url"]["url"]
                        if url.startswith("data:"):
                            # Extract media type and base64
                            header, data = url.split(",", 1)
                            media_type = header.split(":")[1].split(";")[0]
                            new_content.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": data
                                }
                            })
                        else:
                            # Anthropic usually requires base64 for images unless using specific integrations
                            # For now, we assume base64 data URIs are used for local images
                            # If it's a remote URL, we might need to fetch it (not implemented yet)
                            new_content.append({"type": "text", "text": f"[Image: {url}] (Remote images not fully supported in Anthropic adapter yet)"})
            
            # Assistant messages with tool calls
            if role == "assistant" and "tool_calls" in msg:
                # Anthropic expects tool_use blocks in content
                anthropic_content = []
                if msg.get("content"):
                     anthropic_content.append({"type": "text", "text": msg["content"]})
                
                for tc in msg["tool_calls"]:
                    args = tc["function"].get("arguments")
                    if isinstance(args, str):
                        args = args.strip()
                        if args:
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        else:
                            args = {}
                    elif not isinstance(args, dict):
                        args = {}
                    anthropic_content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": args
                    })
                
                api_messages.append({
                    "role": "assistant",
                    "content": anthropic_content
                })
                continue

            api_messages.append({
                "role": role,
                "content": new_content
            })

        flush_tool_results()
        return system_prompt.strip(), api_messages

    def _convert_tools(self, tools):
        """Convert OpenAI tool definitions to Anthropic format"""
        # OpenAI: {"type": "function", "function": {"name":..., "description":..., "parameters":...}}
        # Anthropic: {"name":..., "description":..., "input_schema":...}
        anthropic_tools = []
        for t in tools:
            if t["type"] == "function":
                f = t["function"]
                anthropic_tools.append({
                    "name": f["name"],
                    "description": f.get("description", ""),
                    "input_schema": f.get("parameters", {})
                })
        return anthropic_tools
