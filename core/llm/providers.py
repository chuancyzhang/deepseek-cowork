from abc import ABC, abstractmethod
import os
import json
import time

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
    def __init__(self, api_key, base_url, model_name):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model_name = model_name

    def chat_stream(self, messages, tools=None):
        try:
            # Clean messages for OpenAI (remove internal keys if any)
            clean_messages = self._prepare_messages(messages)
            
            # Prepare tools
            api_tools = tools if tools else None
            
            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=clean_messages,
                tools=api_tools,
                stream=True
            )

            for chunk in stream:
                delta = chunk.choices[0].delta
                
                # 1. Reasoning (DeepSeek style)
                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    yield {"type": "reasoning", "content": delta.reasoning_content}
                
                # 2. Content
                if delta.content:
                    yield {"type": "content", "content": delta.content}
                
                # 3. Tool Calls
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        yield {
                            "type": "tool_call",
                            "index": tc.index,
                            "id": tc.id,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        
        except Exception as e:
            yield {"type": "error", "content": str(e)}

    def _prepare_messages(self, messages):
        # Deep copy and clean
        clean = []
        for msg in messages:
            m = msg.copy()
            # Remove internal keys
            m.pop("reasoning", None)
            m.pop("reasoning_content", None)
            
            # Ensure tool_calls are correctly formatted if present
            if "tool_calls" in m and not m["tool_calls"]:
                del m["tool_calls"]
                
            clean.append(m)
        return clean

class AnthropicProvider(LLMProvider):
    def __init__(self, api_key, base_url, model_name):
        from anthropic import Anthropic
        # Anthropic SDK handles base_url differently usually, but we can pass it
        self.client = Anthropic(api_key=api_key, base_url=base_url)
        self.model_name = model_name

    def chat_stream(self, messages, tools=None):
        try:
            system_prompt, api_messages = self._prepare_messages(messages)
            
            # Convert tools to Anthropic format
            api_tools = self._convert_tools(tools) if tools else None
            
            # Anthropic parameters
            kwargs = {
                "model": self.model_name,
                "messages": api_messages,
                "stream": True,
                "max_tokens": 8192 # Required by Anthropic
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            if api_tools:
                kwargs["tools"] = api_tools

            with self.client.messages.stream(**kwargs) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield {"type": "content", "content": event.delta.text}
                        elif event.delta.type == "input_json_delta":
                            # Streaming tool args
                            # We need to track index manually or assume single tool call per block?
                            # Anthropic SDK stream helper might be easier, but let's use raw events for fine control
                            # actually 'index' is in content_block_start
                            pass 
                            
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
                            
                    elif event.type == "content_block_delta" and event.delta.type == "input_json_delta":
                         yield {
                            "type": "tool_call",
                            "index": event.index, 
                            "function": {
                                "arguments": event.delta.partial_json
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
            role = msg["role"]
            content = msg["content"]
            
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
                # OpenAI: role="tool", tool_call_id="..."
                # Anthropic: role="user", content=[{"type": "tool_result", "tool_use_id": ..., "content": ...}]
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id"),
                        "content": content
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
                    anthropic_content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": json.loads(tc["function"]["arguments"])
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
