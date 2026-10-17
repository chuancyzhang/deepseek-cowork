"""Desktop boundary tests: identity binding, protected credentials, unchanged prompts."""
import json
import os
import threading
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
    store = EnterpriseStore(tmp_path)
    with patch("httpx.Client", side_effect=AssertionError("guest made a network call")):
        assert store.read() == {}
        assert store.profiles() == []
        assert read_connection(tmp_path) is None
    assert list(tmp_path.iterdir()) == []


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
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from core.update_coordinator import UpdateCoordinator
    app = QApplication.instance() or QApplication([])
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


def test_enterprise_first_start_offers_login_without_contacting_center(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ui.enterprise_connection import offer_enterprise_onboarding
    values = {}
    config = SimpleNamespace(data_dir=str(tmp_path), get=values.get, set=values.__setitem__)
    parent = Mock()
    with patch("core.enterprise_client.read_connection", return_value=CONNECTION), patch("ui.enterprise_connection.ProductMessageDialog") as dialog, patch("httpx.Client", side_effect=AssertionError("unexpected network")):
        dialog.return_value.exec_result.return_value = "login"
        assert offer_enterprise_onboarding(config, parent)
        parent.open_settings.assert_called_once_with("账号与连接")
        assert values["enterprise_intro_seen"] == "company"
        assert not offer_enterprise_onboarding(config, parent)
    assert not (tmp_path / "enterprise.sqlite3").exists()


def test_employee_page_has_one_browser_login_and_no_configuration_forms(tmp_path):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton
    from ui.account_connections import AccountConnectionsPage
    app = QApplication.instance() or QApplication([])
    store = EnterpriseStore(tmp_path)
    store.write({"connection": CONNECTION, "generation": "one", "client_id": "one-installation"})
    with patch("httpx.Client", side_effect=AssertionError("opening the page made a network request")):
        page = AccountConnectionsPage(config_manager=SimpleNamespace(data_dir=str(tmp_path)))
        page.show()
        app.processEvents()
    try:
        assert not page.findChildren(QComboBox)
        assert not page.findChildren(QLineEdit)
        assert [b.text() for b in page.findChildren(QPushButton) if b.isVisible()] == ["登录企业账号"]
        calls = []
        def login(provider, cancel, open_browser):
            calls.append(provider)
            raise EnterpriseError("模拟认证失败，重新登录即可。")
        page.enterprise.client.login = login
        page.enterprise.login_button.click()
        page.enterprise.jobs.pool.shutdown(wait=True)
        app.processEvents()
        assert calls == [None]
        assert page.enterprise.login_button.isVisible()
        assert "模拟认证失败" in page.enterprise.status.text()
        assert not page.has_pending_input()
    finally:
        page.enterprise.jobs.pool.shutdown(wait=True)
        page.close()
        page.deleteLater()
        app.processEvents()


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
