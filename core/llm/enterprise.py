"""Gateway routing around existing SDK transports; provider prompts stay unchanged."""
from contextvars import ContextVar
import json
import types
import uuid

import httpx

from core.enterprise_client import EnterpriseError, EnterpriseStore, questions_for_binding


class EnterpriseTransport(httpx.BaseTransport):
    def __init__(self, profile, *, store=None, transport=None):
        self.profile = profile
        self.binding = profile["enterprise"]
        self.store = store or EnterpriseStore(self.binding.get("data_dir"))
        self.session = self.store.bound_session(self.binding)
        self.transport = transport or httpx.HTTPTransport(retries=0)
        self.context = ContextVar("enterprise_request", default={})

    def handle_request(self, request):
        state = self.store.bound_session(self.binding)
        protocol = self.profile.get("api_protocol", "chat_completions")
        path = {"chat_completions": "chat/completions", "responses": "responses", "anthropic": "v1/messages"}[protocol]
        source_path = path
        if protocol == "anthropic" and self.profile["base_url"].rstrip("/").endswith("/v1"):
            source_path = "messages"
        expected = httpx.URL(self.profile["base_url"].rstrip("/") + "/" + source_path)
        if request.method != "POST" or request.url != expected:
            raise EnterpriseError("企业模型不支持此请求，请检查模型协议配置。")
        context = self.context.get()
        body = json.loads(request.read())
        questions = questions_for_binding(context.get("enterprise_questions", []), self.binding)
        body["_cowork"] = {"client_id": state["client_id"], "run_id": context.get("run_id") or uuid.uuid4().hex,
                           "model_revision": self.binding.get("model_revision", 1),
                           "questions": questions}
        if "team_space" in context:
            body["_cowork"]["team_space"] = context["team_space"]
        # The SDK still sees the original vendor endpoint for protocol/cache behavior.
        # Only this transport can access the session token; it never leaves for the vendor.
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in {"host", "content-length", "authorization", "x-api-key", "cookie"}}
        headers["Authorization"] = "Bearer " + state["access_token"]
        routed = httpx.Request("POST", state["connection"]["platform_url"] + "/api/v1/gateway/models/" + self.binding["model_id"] + "/" + path,
                               headers=headers, json=body, extensions=request.extensions)
        response = self.transport.handle_request(routed)
        if response.is_success and callable(context.get("enterprise_accepted")):
            context["enterprise_accepted"]({q["id"] for q in questions})
        return response

    def close(self):
        self.transport.close()


def bind_provider(provider, profile):
    transport = EnterpriseTransport(profile)
    binding = profile["enterprise"]
    provider.enterprise_replay_scope = json.dumps([binding["platform_id"], binding["member_id"], binding["model_id"], binding.get("model_revision", 1)], separators=(",", ":"))
    original_client = provider.client
    provider.client = original_client.with_options(http_client=httpx.Client(
        transport=transport, timeout=original_client.timeout, follow_redirects=False), max_retries=0)
    original_client.close()
    original_stream = provider.chat_stream

    def chat_stream(self, messages, tools=None, prompt_cache_key=None, request_context=None):
        token = transport.context.set(request_context or {})
        try:
            yield from original_stream(messages, tools=tools, prompt_cache_key=prompt_cache_key, request_context=request_context)
        finally:
            transport.context.reset(token)

    provider.chat_stream = types.MethodType(chat_stream, provider)
    return provider
