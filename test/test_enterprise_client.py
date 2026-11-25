"""Desktop boundary tests: identity binding, protected credentials, unchanged prompts."""
import json
import os
import time
from unittest.mock import patch

import httpx
import pytest

from core.enterprise_client import EnterpriseClient, EnterpriseError, EnterpriseStore, submission
from core.enterprise_connection import EnterpriseConfigurationError, check_update_connection, parse_connection, read_connection
from core.llm.enterprise import EnterpriseTransport, bind_provider
from core.llm.factory import LLMFactory

CONNECTION = {"schema_version": 1, "config_version": 1, "platform_id": "company", "platform_name": "测试企业",
              "platform_url": "https://enterprise.example/sub", "distribution_channel": "enterprise"}


class TestProtector:
    __test__ = False
    def protect(self, data):
        return bytes(x ^ 0x5A for x in data)
    unprotect = protect


def ready_store(tmp_path, *, protocol="chat_completions", provider="openai"):
    store = EnterpriseStore(tmp_path, protector=TestProtector())
    store.write({"connection": CONNECTION, "generation": "generation-one", "client_id": "client-installation-1",
                 "member": {"id": "member-one", "name": "测试成员"}, "expires_at": time.time() + 1000,
                 "models": [{"id": "model-one", "name": "企业模型", "model_name": "test-model", "provider": provider,
                             "protocol": protocol, "base_url": "https://api.vendor.example/v1", "options": {}}]}, "platform-session-token")
    return store


def test_guest_discovery_is_local_and_does_not_create_state(tmp_path):
    from core.enterprise_capabilities import EnterpriseCapabilities
    store = EnterpriseStore(tmp_path)
    with patch("httpx.Client", side_effect=AssertionError("guest made a network call")):
        assert store.read() == {}
        assert store.profiles() == []
        assert read_connection(tmp_path) is None
        assert EnterpriseCapabilities(EnterpriseClient(store)).inventory()["status"] == "signed_out"
    assert list(tmp_path.iterdir()) == []


def test_conflicting_package_does_not_use_or_overwrite_saved_identity_without_confirmation(tmp_path, monkeypatch):
    from threading import Event

    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setenv("COWORK_DESKTOP_INSTALL_DIR", str(install))
    package = {**CONNECTION, "platform_url": "https://second.example/sub"}
    (install / "enterprise_connection.json").write_text(json.dumps(package), encoding="utf-8")
    store = ready_store(tmp_path)
    seen = []
    matching_platform = False

    def endpoint(request):
        assert request.url.host == "second.example"
        assert "Authorization" not in request.headers
        seen.append(request.url.path)
        if request.url.path.endswith("platform-info"):
            return httpx.Response(200, json={"platform_id": package["platform_id"] if matching_platform else "unexpected",
                                             "supported_api_versions": [1]})
        return httpx.Response(200, json={"items": []})

    client = EnterpriseClient(store, transport=httpx.MockTransport(endpoint))
    assert store.connection_status() == {"connection": package, "connection_source": "package", "previous_connection": CONNECTION}
    assert store.read(secret=True) == {} and store.profiles() == []
    with pytest.raises(EnterpriseError, match="确认切换"):
        client.login(None, Event(), lambda _: pytest.fail("unexpected browser"))
    with pytest.raises(EnterpriseError, match="确认切换"):
        client.bind(use_package=True)
    assert not seen
    with pytest.raises(EnterpriseError, match="不匹配"):
        client.bind(use_package=True, confirm_switch=True)
    assert store.connection_status()["previous_connection"] == CONNECTION
    assert store._saved()[0]["member"]["id"] == "member-one"

    matching_platform = True
    client.bind(use_package=True, confirm_switch=True)
    assert store.connection_status()["connection"] == package
    assert "previous_connection" not in store.connection_status()
    assert "access_token" not in store.read(secret=True) and "member" not in store.read()


def test_invalid_package_leaves_saved_identity_inactive(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORK_DESKTOP_INSTALL_DIR", str(tmp_path))
    (tmp_path / "enterprise_connection.json").write_text("{}", encoding="utf-8")
    store = ready_store(tmp_path)
    assert store.connection_status()["connection_error"]
    assert store.read(secret=True) == {} and store.profiles() == []
    with pytest.raises(EnterpriseError, match="连接文件"):
        store.connection()


def test_enterprise_capability_keys_and_member_bearer_stay_scoped(tmp_path):
    from core.enterprise_capabilities import EnterpriseCapabilities, identity, selection_key
    store = ready_store(tmp_path)
    capability_id, expert_id = "1" * 32, "2" * 32
    server = {"id": capability_id, "kind": "mcp", "name": "经营数据", "description": "查询数据", "version": 1,
              "config": {"auth_type": "bearer", "url": "https://data.example/mcp"}}
    expert = {"id": expert_id, "kind": "expert", "name": "经营分析", "description": "分析数据", "version": 2,
              "config": {"default_model_id": "model-one", "mcp_ids": [capability_id]}}

    def endpoint(request):
        assert request.headers["Authorization"] in {"Bearer platform-session-token", "Bearer second-member-token"}
        if request.url.path.endswith("platform-info"):
            return httpx.Response(200, json={"platform_id": "company", "enterprise_capabilities": {"version": 1}})
        assert request.url.path.endswith("capabilities")
        return httpx.Response(200, json={"items": [server, expert]})

    capabilities = EnterpriseCapabilities(EnterpriseClient(store, transport=httpx.MockTransport(endpoint)))
    old_identity = identity(store.read())
    key = selection_key("mcp", capability_id, old_identity)
    expert_key = selection_key("expert", expert_id, old_identity)
    before = capabilities.inventory()["items"]
    assert not before[0]["available"] and not before[1]["available"]
    assert before[1]["default_model_id"] == "enterprise:company:model-one"
    capabilities.set_credential(key, "member-only-credential")
    assert b"member-only-credential" not in store.path.read_bytes()
    after = capabilities.inventory()["items"]
    assert after[0]["available"] and after[1]["available"]
    assert "member-only-credential" not in str(after)
    manifest, selected_expert, dependencies = capabilities.selection([], expert_key)
    assert dependencies == [key] and selected_expert["id"] == expert_id
    assert manifest["items"] == [{"id": capability_id, "kind": "mcp", "version": 1},
                                 {"id": expert_id, "kind": "expert", "version": 2}]
    assert capabilities.credential(old_identity, server) == "member-only-credential"
    server["config"]["url"] = "https://another.example/mcp"
    assert not capabilities.inventory()["items"][0]["credential_configured"]
    state = store.read()
    state.update(member={"id": "other-member"}, generation="other-generation")
    store.write(state, "second-member-token")
    with pytest.raises(EnterpriseError, match="身份"):
        capabilities.credential(old_identity, server)
    with pytest.raises(EnterpriseError, match="不可用"):
        capabilities.selection([key], None)


def test_desktop_submission_uses_pinned_enterprise_expert_and_dependencies(tmp_path):
    from types import SimpleNamespace

    from core.conversation_agents import agent_role_context
    from core.desktop_submission import run_context
    from core.enterprise_capabilities import EnterpriseCapabilities, identity, selection_key

    store = ready_store(tmp_path)
    binding = identity(store.read())
    skill_id, mcp_id, expert_id = "a" * 32, "b" * 32, "c" * 32
    releases = [
        {"id": skill_id, "kind": "skill", "name": "经营口径", "description": "各项经营指标的口径", "version": 3,
         "config": {"instructions": "计算经营利润"}},
        {"id": mcp_id, "kind": "mcp", "name": "经营数据", "description": "连接经营数据库", "version": 5,
         "config": {"auth_type": "none", "url": "https://data.example/mcp"}},
        {"id": expert_id, "kind": "expert", "name": "经营分析", "description": "分析经营数据", "version": 2,
         "config": {"instructions": "核实企业数据后出报告", "skill_ids": [skill_id], "mcp_ids": [mcp_id]}},
    ]
    expected_items = [{k: item[k] for k in ("id", "kind", "version")} for item in releases]
    personal_expert = {"id": "personal-expert", "name": "本地专家", "system_prompt": "普通工作指引"}
    config = SimpleNamespace(data_dir=tmp_path, get={}.get, get_agent_profiles=lambda: [personal_expert])
    storage = SimpleNamespace(get_conversation_meta=lambda _: {}, get_team_space_binding=lambda _: None)
    service = SimpleNamespace(config=config, storage=storage, workspace=lambda _: tmp_path)
    profile = {"id": "personal-model"}
    personal_preferences = {"conversation_agent": {"id": personal_expert["id"], "name": personal_expert["name"]}}
    expert_key = selection_key("expert", expert_id, binding)
    expert_preferences = {"conversation_agent": {"id": expert_key, "name": "经营分析"}}
    skill_key = selection_key("skill", skill_id, binding)
    mcp_key = selection_key("mcp", mcp_id, binding)
    revoked = False
    requests = []

    def center(client, connection, method, path, *, token, payload=None):
        assert connection == CONNECTION and token == "platform-session-token"
        requests.append((method, path, payload))
        if path == "/api/v1/capabilities":
            return {"items": list(reversed(releases))}
        assert path == "/api/v1/capabilities/check"
        assert payload["items"] == expected_items
        if revoked:
            return {"allowed": False}
        return {"allowed": True, "items": list(reversed(releases))} if payload["include_config"] else {"allowed": True}

    with patch("core.enterprise_client.EnterpriseStore", return_value=store), \
            patch.object(EnterpriseClient, "request", center), \
            patch("core.knowledge_sources.MultiSourceKnowledgeService.snapshot", return_value=None):
        personal = run_context(service, "chat", {"message_id": "m-1", "preferences": personal_preferences}, profile)
        role = agent_role_context(personal_preferences["conversation_agent"], personal_expert)
        assert personal["agent_system_prompt"] == role["agent_system_prompt"]
        assert "enterprise_capabilities" not in personal and requests == []

        context = run_context(service, "chat", {"message_id": "m-2", "preferences": expert_preferences,
                                               "skills": ["local-skill", skill_key]}, profile)
        assert context["agent_system_prompt"].endswith(releases[2]["config"]["instructions"])
        assert context["selected_model_id"] == profile["id"]
        assert context["selected_skill_names"] == ["local-skill", skill_key, mcp_key]
        assert context["enterprise_capabilities"] == {"binding": {**binding, "data_dir": str(tmp_path)},
                                                      "items": expected_items}

        pin = context["enterprise_capabilities"]
        capability_client = EnterpriseCapabilities(EnterpriseClient(store))
        resolved = capability_client.check(pin["binding"], pin["items"], include_config=True)
        assert {item["version"] for item in resolved} == {2, 3, 5}
        revoked = True
        with pytest.raises(EnterpriseError, match="撤销授权"):
            capability_client.check(pin["binding"], pin["items"])

        state = store.read()
        store.write({**state, "generation": "generation-two"}, "another-session-token")
        count = len(requests)
        with pytest.raises(EnterpriseError, match="身份已改变"):
            capability_client.check(pin["binding"], pin["items"])
        assert len(requests) == count


def test_enterprise_prompt_scope_reuses_only_same_member_and_version_set(tmp_path):
    from core.enterprise_capabilities import identity, prompt_scope, scope_prompt_messages

    manifest = {"binding": {**identity(ready_store(tmp_path).read()), "data_dir": str(tmp_path)}, "items": [
        {"id": "1" * 32, "kind": "skill", "version": 1}, {"id": "2" * 32, "kind": "mcp", "version": 1},
    ]}
    original = prompt_scope(manifest)
    relogin = {"binding": {**manifest["binding"], "generation": "fresh"}, "items": list(reversed(manifest["items"]))}
    assert prompt_scope(relogin) == original
    assert prompt_scope({**manifest, "binding": {**manifest["binding"], "member_id": "other"}}) != original
    updated = {**manifest, "items": [{**manifest["items"][0], "version": 2}, manifest["items"][1]]}
    assert prompt_scope(updated) != original
    assert prompt_scope(None) == ""
    for invalid in ({}, {**manifest, "items": []}, {**manifest, "items": [*manifest["items"], manifest["items"][0]]}):
        with pytest.raises(EnterpriseError, match="运行信息"):
            prompt_scope(invalid)

    chat = {"role": "user", "content": "业务问题", "meta": {"enterprise_context_identity": manifest["binding"]}}
    instructions = {"role": "system", "content": "企业指引", "meta": {"enterprise_prompt_scope": original, "hidden": True}}
    messages = [chat, instructions]
    assert scope_prompt_messages(messages, original) == messages
    assert scope_prompt_messages(messages, prompt_scope(updated)) == [chat]
    assert scope_prompt_messages(messages, "") == [chat]


def test_chat_audit_queue_preserves_actual_latest_and_never_uses_next_identity(tmp_path):
    from types import SimpleNamespace

    from core import enterprise_chat_audit as audit

    store = ready_store(tmp_path)
    state = store.read(secret=True)
    store.write({**state, "chat_audit_version": 1}, state["access_token"])
    provider = SimpleNamespace(enterprise_audit_binding=store.profiles()[0]["enterprise"])
    worker = SimpleNamespace(is_subagent=False, conversation_id="chat-a", request_id="run-1", run_context={},
                             api_key="FAKE_MODEL_KEY")
    outgoing = {"model": "test-model", "messages": [{"content": "first"}, {"type": "input_file", "file_data": "a-file"}],
                "text": "password=secret-value FAKE_MODEL_KEY and platform-session-token"}
    uploaded = []

    def send(state, method, path, payload):
        assert state["member"]["id"] == "member-one"
        uploaded.append((method, path, payload))
        return httpx.Response(200)

    with patch.object(audit, "EnterpriseStore", return_value=store):
        capture = audit.ChatAuditCapture(worker, provider, None)
    assert capture.enterprise_negotiated
    capture.capture("chat_completions", outgoing, "request-a")
    newer = {**outgoing, "messages": [{"content": "newer"}]}
    capture.capture("chat_completions", newer, "request-b")
    with patch.object(audit, "request", side_effect=send):
        assert audit._drain_once(store) == 0
        assert audit._drain_once(store) is None
    assert len(uploaded) == 1
    method, url, payload = uploaded[0]
    assert (method, url, payload["request_id"]) == ("PUT", "/chat-audit/latest", "request-b")
    assert payload["parameters"]["messages"] == newer["messages"]
    for secret in ("secret-value", "FAKE_MODEL_KEY", "platform-session-token"):
        assert secret not in json.dumps(payload, ensure_ascii=False)
    assert "a-file" in json.dumps(outgoing)
    capture.capture("chat_completions", newer, "request-c")

    store.write({**state, "generation": "generation-two", "member": {"id": "member-two"}}, "new-member-token")
    capture.capture("chat_completions", newer, "request-d")
    with patch.object(audit, "request", side_effect=AssertionError("old data must not use the new user's token")):
        assert audit._drain_once(store) is None
    with store.db() as db:
        assert db.execute("SELECT request_id FROM chat_audit_pending").fetchall() == []


def test_chat_audit_skips_foreign_tagged_history_without_reverting_to_legacy(tmp_path):
    from types import SimpleNamespace

    from core import enterprise_chat_audit as audit

    store = ready_store(tmp_path)
    state = store.read(secret=True)
    store.write({**state, "chat_audit_version": 1}, state["access_token"])
    provider = SimpleNamespace(enterprise_audit_binding=store.profiles()[0]["enterprise"])
    observed = []
    marker = {"platform_id": "company", "member_id": "another-member", "generation": "old-generation"}
    message = {"role": "user", "content": "历史", "meta": {"enterprise_context_identity": marker}}
    worker = SimpleNamespace(is_subagent=False, conversation_id="chat-a", request_id="run-1", run_context={},
                             messages=[message], observability_signal=SimpleNamespace(emit=observed.append),
                             step_signal=SimpleNamespace(emit=lambda _: None))
    with patch.object(audit, "EnterpriseStore", return_value=store):
        capture = audit.ChatAuditCapture(worker, provider, None)
    assert capture.enterprise_negotiated
    assert capture.store is None
    assert observed[0]["reason"] == "foreign_enterprise_history"
    assert not audit.contains_foreign_history([{**message, "meta": {"enterprise_submission": {"identity": {
        **marker, "member_id": "member-one"}}}}], ("company", "member-one", "new-generation"))


def test_connection_schema_and_update_channel(tmp_path):
    data = json.dumps(CONNECTION).encode()
    assert parse_connection(data) == CONNECTION
    with pytest.raises(EnterpriseConfigurationError):
        parse_connection(data[:-1] + b',"platform_id":"duplicate"}')
    for changes in ({"extra": 1}, {"schema_version": 2}, {"platform_url": "https://user:pass@example.com"},
                    {"platform_url": "ftp://enterprise.example"}, {"platform_url": "https://example.com/../bad"}):
        with pytest.raises(EnterpriseConfigurationError):
            parse_connection(json.dumps({**CONNECTION, **changes}).encode())
    for candidate in (None, {**CONNECTION, "platform_id": "other"}, {**CONNECTION, "platform_name": "same-version conflict"}):
        with pytest.raises(EnterpriseConfigurationError):
            check_update_connection(CONNECTION, candidate)
    check_update_connection(CONNECTION, {**CONNECTION, "config_version": 2})


@pytest.mark.skipif(os.name != "nt", reason="DPAPI requires Windows")
def test_credentials_are_dpapi_protected(tmp_path):
    store = EnterpriseStore(tmp_path)
    store.write({"generation": "one"}, "never-store-this-token-as-plaintext")
    assert b"never-store-this-token-as-plaintext" not in store.path.read_bytes()
    assert "access_token" not in store.read()
    assert store.read(secret=True)["access_token"] == "never-store-this-token-as-plaintext"


def test_logout_and_account_switch_invalidate_existing_provider(tmp_path):
    store = ready_store(tmp_path)
    profile = store.profiles()[0]
    requests = []
    transport = EnterpriseTransport(profile, store=store, transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200)))
    state = store.read()
    state["generation"] = "generation-two"
    state["member"]["id"] = "other-member"
    store.write(state, "another-token")
    with pytest.raises(EnterpriseError, match="身份"):
        transport.handle_request(httpx.Request("POST", profile["base_url"] + "/chat/completions", json={"model": "test-model"}))
    assert requests == []
    with pytest.raises(EnterpriseError, match="已改变"):
        store.write(state, "old-token", expected_generation="generation-one")


@pytest.mark.parametrize("protocol,provider,path", [("chat_completions", "openai", "chat/completions"),
                                                  ("responses", "openai", "responses"), ("anthropic", "anthropic", "messages")])
def test_transport_preserves_prompt_tools_and_routes_only_to_platform(tmp_path, protocol, provider, path):
    store = ready_store(tmp_path, protocol=protocol, provider=provider)
    profile = store.profiles()[0]
    question = submission(profile, "真正新提交的用户问题")
    received = []
    transport = EnterpriseTransport(profile, store=store, transport=httpx.MockTransport(lambda request: received.append(request) or httpx.Response(200, json={})))
    transport.context.set({"run_id": "stable-run", "enterprise_questions": [question]})
    body = {"model": "test-model", "messages": [{"role": "system", "content": "stable-prefix"}, {"role": "user", "content": "old personal history"}],
            "tools": [{"type": "function", "function": {"name": "first"}}, {"type": "function", "function": {"name": "second"}}],
            "stream": True, "temperature": 0.5}
    request = httpx.Request("POST", profile["base_url"] + "/" + path, json=body, headers={"x-api-key": "dummy", "Authorization": "Bearer dummy"})
    transport.handle_request(request)
    routed = received[0]
    assert str(routed.url).startswith(CONNECTION["platform_url"] + "/api/v1/gateway/models/model-one/")
    assert routed.headers["Authorization"] == "Bearer platform-session-token"
    assert "x-api-key" not in routed.headers
    sent = json.loads(routed.content)
    metadata = sent.pop("_cowork")
    assert sent == body
    assert metadata["questions"] == [{k: question[k] for k in ("id", "text", "sent_at")}]
    assert "old personal history" not in json.dumps(metadata)
    assert "platform-session-token" not in json.dumps(profile)
    with pytest.raises(EnterpriseError):
        transport.handle_request(httpx.Request("POST", "https://different.example/v1/chat/completions", json=body))


def test_submission_deduplicates_retry_but_new_input_and_identity_do_not(tmp_path):
    profile = ready_store(tmp_path).profiles()[0]
    first = submission(profile, "question")
    assert submission(profile, "question", previous=first) == first
    profile["enterprise"]["generation"] = "reauthenticated-same-member"
    assert submission(profile, "question", previous=first)["id"] == first["id"]
    assert submission(profile, "changed", previous=first)["id"] != first["id"]
    assert submission({}, "personal question") is None
    assert submission(profile, "") is None
    profile["enterprise"]["member_id"] = "new-member"
    assert submission(profile, "question", previous=first)["id"] != first["id"]


def test_config_manager_keeps_personal_models_and_does_not_fallback(tmp_path):
    from core.config_manager import ConfigManager
    with patch("core.config_manager.get_app_data_dir", return_value=str(tmp_path)), patch("core.config_manager.get_base_dir", return_value=str(tmp_path)):
        manager = ConfigManager()
        personal = manager.iter_model_profiles()
        store = ready_store(tmp_path)
        assert len(manager.iter_model_profiles()) == len(personal) + 1
        enterprise_id = store.profiles()[0]["id"]
        manager.set_selected_model_id(enterprise_id)
        assert manager.get_model_profile()["enterprise"]["member_id"] == "member-one"
        reloaded = ConfigManager()
        assert reloaded.get_selected_model_id() == enterprise_id
        store.write({"connection": CONNECTION, "generation": "logged-out"})
        assert reloaded.get_model_profile()["enterprise_unavailable"]
        assert reloaded.get_selected_model_id() == enterprise_id
        with pytest.raises(EnterpriseError):
            LLMFactory.create_provider(reloaded)
        assert manager.get_model_profile(personal[0]["id"])["id"] == personal[0]["id"]


def test_actual_openai_sdk_stream_uses_enterprise_transport(tmp_path):
    store = ready_store(tmp_path)
    profile = store.profiles()[0]
    ordinary = {k: v for k, v in profile.items() if k != "enterprise"}
    received = []
    def endpoint(request):
        received.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=(
            'data: {"id":"chat-1","object":"chat.completion.chunk","created":1,"model":"test-model","choices":[{"index":0,"delta":{"content":"完成"},"finish_reason":null}]}\n\n'
            'data: {"id":"chat-1","object":"chat.completion.chunk","created":1,"model":"test-model","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            'data: [DONE]\n\n').encode())
    transport = EnterpriseTransport(profile, store=store, transport=httpx.MockTransport(endpoint))
    with patch("core.llm.enterprise.EnterpriseTransport", return_value=transport):
        provider = bind_provider(LLMFactory.create_provider_from_profile(ordinary), profile)
    try:
        chunks = list(provider.chat_stream([{"role": "user", "content": "测试"}], request_context={"run_id": "run-one", "enterprise_questions": [submission(profile, "测试")]}))
        assert any(c.get("content") == "完成" for c in chunks), chunks
        assert len(received) == 1
        assert json.loads(received[0].content)["_cowork"]["questions"][0]["text"] == "测试"
    finally:
        provider.client.close()


def test_enterprise_native_replay_is_scoped_without_changing_personal_namespace():
    from core.llm.responses_replay import build_provider_replay_namespace, provider_replay_namespaces_compatible
    params = dict(provider_family="openai", base_url="https://vendor.example/v1", model="model", protocol="responses")
    personal = build_provider_replay_namespace(**params)
    assert "enterprise_scope" not in personal
    first = build_provider_replay_namespace(**params, enterprise_scope="platform/member/model/revision1")
    assert not provider_replay_namespaces_compatible(personal, first)
    assert provider_replay_namespaces_compatible(first, dict(first))
    assert not provider_replay_namespaces_compatible(first, build_provider_replay_namespace(**params, enterprise_scope="platform/other-member/model/revision1"))


def test_model_change_stops_old_transport_before_network(tmp_path):
    store = ready_store(tmp_path)
    profile = store.profiles()[0]
    state = store.read()
    state["models"][0]["revision"] = 2
    store.write(state, "platform-session-token")
    with pytest.raises(EnterpriseError, match="配置已更新"):
        store.bound_session(profile["enterprise"])


def test_invalid_enterprise_package_does_not_start_github_update(tmp_path):

    from core.update_coordinator import UpdateCoordinator

    (tmp_path / "enterprise_connection.json").write_text('{"unknown":1}', encoding="utf-8")
    with patch("core.enterprise_client.EnterpriseStore.read", return_value={}):
        coordinator = UpdateCoordinator(str(tmp_path), False, root=str(tmp_path / "updates"))
        with patch.object(coordinator, "_start") as start:
            coordinator.check()
            start.assert_not_called()
            assert coordinator.snapshot()["phase"] == "error"


def test_broken_optional_store_does_not_hide_personal_models(tmp_path):
    from core.config_manager import ConfigManager
    with patch("core.config_manager.get_app_data_dir", return_value=str(tmp_path)), patch("core.config_manager.get_base_dir", return_value=str(tmp_path)):
        manager = ConfigManager()
        personal = manager.iter_model_profiles()
        (tmp_path / "enterprise.sqlite3").write_bytes(b"broken database")
        assert manager.iter_model_profiles() == personal


@pytest.mark.parametrize("url", ["http://localhost:8788", "http://127.0.0.1:8788", "http://[::1]:8788", "http://192.168.1.10:8788", "http://enterprise.example/cowork", "https://enterprise.example"])
def test_http_and_https_package_connection_is_readable_and_bindable(tmp_path, url):
    from core.enterprise_connection import validate_connection
    connection = {**CONNECTION, "platform_url": url}
    (tmp_path / "enterprise_connection.json").write_text(json.dumps(connection), encoding="utf-8")
    assert read_connection(tmp_path) == connection
    assert validate_connection(connection) == connection
    assert read_connection(tmp_path, development=False) == connection


@pytest.mark.parametrize("url", ["ftp://example.com", "file:///platform", "http://", "http://user:pass@example.com", "http://localhost:8788/?token=x", "http://example.com/#fragment", "http://example.com/../private"])
def test_connection_rejects_invalid_or_credential_urls(url):
    with pytest.raises(EnterpriseConfigurationError):
        parse_connection(json.dumps({**CONNECTION, "platform_url": url}).encode())


@pytest.mark.parametrize("url,trust_env", [("http://localhost:8788", False), ("https://127.0.0.1", False), ("http://[::1]:8788", False), ("http://enterprise.example", True)])
def test_loopback_requests_bypass_system_proxy(url, trust_env):
    from unittest.mock import MagicMock
    response = MagicMock(is_success=True)
    response.json.return_value = {"status": "ok"}
    with patch("core.enterprise_client.httpx.Client") as constructor:
        constructor.return_value.__enter__.return_value.request.return_value = response
        assert EnterpriseClient().request({**CONNECTION, "platform_url": url}, "GET", "/healthz") == {"status": "ok"}
        assert constructor.call_args.kwargs["trust_env"] is trust_env
