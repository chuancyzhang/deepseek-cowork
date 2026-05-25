from abc import ABC, abstractmethod
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
    def chat_stream(self, messages, tools=None):
        """
        Yields chunks of response.
        Each chunk should be a dict with:
        - type: 'content' | 'reasoning' | 'tool_call'
        - content: str (for content/reasoning)
        - tool_call: dict (for tool_call, partial or complete)
        """
        pass

class OpenAIProvider(LLMProvider):
    protocol_family = "openai-compatible"

    def __init__(
        self,
        api_key,
        base_url,
        model_name,
        thinking_enabled=DEFAULT_DEEPSEEK_THINKING_ENABLED,
        reasoning_effort=DEFAULT_DEEPSEEK_REASONING_EFFORT,
    ):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.base_url = base_url
        self.model_name = model_name
        self.thinking_enabled = bool(thinking_enabled)
        self.reasoning_effort = normalize_deepseek_reasoning_effort(reasoning_effort)

    def chat_stream(self, messages, tools=None):
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
            params.update(
                build_deepseek_request_options(
                    self.model_name,
                    self.base_url,
                    thinking_enabled=self.thinking_enabled,
                    reasoning_effort=self.reasoning_effort,
                )
            )

            stream = self.client.chat.completions.create(**params)

            for chunk in stream:
                if not chunk.choices:
                    continue
                    
                delta = chunk.choices[0].delta
                
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

    def _prepare_messages(self, messages):
        # Deep copy and clean
        clean = []
        is_deepseek = bool(is_deepseek_request(self.model_name, self.base_url))
        for msg in messages:
            m = msg.copy()
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
        )

    def _prepare_messages(self, messages):
        clean = []
        for msg in messages:
            m = msg.copy()
            # Moonshot strictly does not support 'reasoning_content' or 'reasoning' fields
            m.pop("reasoning", None)
            m.pop("reasoning_content", None)
            
            # Kimi requires strictly valid tool_calls
            if "tool_calls" in m and not m["tool_calls"]:
                del m["tool_calls"]
                
            # Filter out empty content if tool_calls are present (Standard OpenAI allows it, but being explicit is safer)
            if m.get("role") == "assistant" and "tool_calls" in m and not m.get("content"):
                m["content"] = None # OpenAI SDK handles None as null, which is valid when tool_calls exist

            clean.append(m)
        return clean

class AnthropicProvider(LLMProvider):
    def __init__(self, api_key, base_url, model_name):
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

    @staticmethod
    def _uses_bearer_auth(base_url):
        text = str(base_url or "").strip().lower().rstrip("/")
        return text.endswith("/coding/anthropic") or "/coding/anthropic/" in text

    def chat_stream(self, messages, tools=None):
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
            
            if role == "system":
                system_prompt += content + "\n"
                continue
                
            # Handle multi-modal content
            new_content = []
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
