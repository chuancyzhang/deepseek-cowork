from abc import ABC, abstractmethod
import base64
import mimetypes
import os
import json
import time
from .deepseek import (
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
    build_deepseek_request_options,
    is_deepseek_request,
    normalize_deepseek_reasoning_effort,
)

class LLMProvider(ABC):
    @abstractmethod
    def chat_stream(self, messages, tools=None, prompt_cache_key=None):
        """
        Yields chunks of response.
        Each chunk should be a dict with:
        - type: 'content' | 'reasoning' | 'tool_call'
        - content: str (for content/reasoning)
        - tool_call: dict (for tool_call, partial or complete)
        """
        pass


SUPPORTED_VISION_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
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


def _build_openai_vision_content(text_content, content_parts):
    content = []
    if text_content:
        content.append({"type": "text", "text": text_content})
    for path in _extract_image_paths(content_parts):
        data_url = _build_data_url_from_path(path)
        if not data_url:
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
    ):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.base_url = base_url
        self.model_name = model_name
        self.thinking_enabled = bool(thinking_enabled)
        self.reasoning_effort = normalize_deepseek_reasoning_effort(reasoning_effort)
        self.supports_vision = bool(supports_vision)
        self.stream_usage_enabled = bool(stream_usage_enabled)
        self.prompt_cache_key_param = str(prompt_cache_key_param or "").strip()

    def chat_stream(self, messages, tools=None, prompt_cache_key=None):
        try:
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
            params.update(
                build_deepseek_request_options(
                    self.model_name,
                    self.base_url,
                    thinking_enabled=self.thinking_enabled,
                    reasoning_effort=self.reasoning_effort,
                )
            )

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
            if "stream_options" not in params or "stream_options" not in str(exc):
                raise
            fallback = dict(params)
            fallback.pop("stream_options", None)
            return self.client.chat.completions.create(**fallback)

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
        ):
            value = _value(usage, source)
            if value is not None:
                payload[target] = value

        cached_tokens = None
        for details_name in ("prompt_tokens_details", "input_tokens_details"):
            details = _value(usage, details_name)
            if details is None:
                continue
            cached_tokens = _value(details, "cached_tokens")
            if cached_tokens is not None:
                break
        if cached_tokens is not None:
            payload["cached_input_tokens"] = cached_tokens
        input_tokens = payload.get("input_tokens")
        if input_tokens is not None and cached_tokens is not None:
            try:
                input_count = int(input_tokens)
                cached_count = int(cached_tokens)
                payload["uncached_input_tokens"] = max(0, input_count - cached_count)
                payload["cache_hit_rate"] = cached_count / input_count if input_count > 0 else 0
            except Exception:
                pass
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
                self.supports_vision
                and m.get("role") == "user"
                and isinstance(content_parts, list)
            ):
                vision_content = _build_openai_vision_content(m.get("content") or "", content_parts)
                if vision_content:
                    m["content"] = vision_content
                
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
                self.supports_vision
                and m.get("role") == "user"
                and isinstance(content_parts, list)
            ):
                vision_content = _build_openai_vision_content(m.get("content") or "", content_parts)
                if vision_content:
                    m["content"] = vision_content

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

    def _prepare_messages(self, messages):
        """
        Convert OpenAI-style messages to Anthropic format.
        - Extract system message.
        - Convert 'image_url' content to Anthropic image block.
        """
        system_prompt = ""
        api_messages = []
        
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            content_parts = msg.get("content_parts")
            
            if role == "system":
                system_prompt += content + "\n"
                continue
                
            # Handle multi-modal content
            new_content = []
            if (
                self.supports_vision
                and role == "user"
                and isinstance(content_parts, list)
            ):
                content = _build_openai_vision_content(content or "", content_parts)
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
            
            # Tool results
            if role == "tool":
                tool_call_id = msg.get("tool_call_id")
                if not tool_call_id:
                    continue
                # OpenAI: role="tool", tool_call_id="..."
                # Anthropic: role="user", content=[{"type": "tool_result", "tool_use_id": ..., "content": ...}]
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": content if content is not None else ""
                    }]
                })
                continue
            
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
