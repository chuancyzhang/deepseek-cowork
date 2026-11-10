"""Model discovery and account onboarding without exposing login sessions."""
import logging
import threading
import time
import uuid

from core.desktop_jobs import JobCancelled

log = logging.getLogger("desktop.models")


class DesktopModels:
    methods = frozenset({"models.discover", "models.test", "models.account_open", "models.account_close", "models.account_login",
        "models.account_verify", "models.account_keys", "models.account_create_key", "models.account_reconcile", "models.account_discover", "models.account_save"})

    def __init__(self, service):
        self.s = service
        self.lock = threading.RLock()
        self.accounts = {}

    def close(self):
        with self.lock:
            sessions = list(self.accounts.values())
            self.accounts.clear()
        for session in sessions:
            session["account"].cancel()
            session["account"].close()

    def channel(self, value):
        previous = next((item for item in self.s.config.get_model_channels() if item["channel_id"] == value.get("channel_id")), {})
        return {**value, "api_key": value.get("api_key") or previous.get("api_key", "")}

    def dispatch(self, method, p):
        if method in {"models.discover", "models.test"}:
            channel = self.channel(p.get("channel") or {})
            if not channel.get("api_key") or not channel.get("base_url"):
                raise ValueError("请先填写服务地址与 API Key。")
            def run(cancelled, progress):
                from core.llm.model_catalog import list_available_models, _safe_error_message
                try:
                    if method == "models.discover":
                        from core.llm.gateway_catalog import catalog_record, new_catalog_model
                        records = list_available_models(channel.get("provider_type", "openai"), channel["base_url"], channel["api_key"])
                        identity = channel.get("channel_id") or uuid.uuid4().hex
                        result = {"models": [new_catalog_model(record if "provider_type" in record else catalog_record(record, default_provider=channel.get("provider_type", "openai")), identity, channel["base_url"]) for record in records]}
                    else:
                        from core.llm.factory import LLMFactory
                        profile = {**channel, **p["model"]}
                        provider = LLMFactory.create_provider_from_profile(profile)
                        result = {"message": str(provider.test_connection(timeout=20) or "连接测试通过。")}
                    if cancelled.is_set():
                        raise JobCancelled("已取消，模型配置保持不变。")
                    return result
                except JobCancelled:
                    raise
                except Exception as exc:
                    raise ValueError(_safe_error_message(exc, channel["api_key"])) from None
            return self.s.jobs.submit("获取模型" if method.endswith("discover") else "测试模型", run)
        if method == "models.account_open":
            from core.llm.gateway_accounts import GatewayAccount
            def open_account(cancelled, progress):
                account = GatewayAccount(p["url"], p.get("platform", "auto"))
                account.cancelled = cancelled
                try:
                    info = account.probe()
                except BaseException:
                    account.close()
                    raise
                with self.lock:
                    for key, old in list(self.accounts.items()):
                        if time.monotonic() - old["used"] > 1800:
                            old["account"].close(); del self.accounts[key]
                    identity = uuid.uuid4().hex
                    self.accounts[identity] = {"account": account, "used": time.monotonic(), "results": {}, "lock": threading.Lock()}
                return {"id": identity, **info}
            return self.s.jobs.submit("识别模型站点", open_account)
        with self.lock:
            session = self.accounts.get(str(p.get("id") or ""))
            if not session:
                raise ValueError("登录会话已结束，请重新识别站点。")
            session["used"] = time.monotonic()
        account = session["account"]
        if method == "models.account_close":
            account.cancel()
            with session["lock"]:
                account.close()
                with self.lock:
                    self.accounts.pop(p["id"], None)
            return {"closed": True}
        def perform(cancelled, progress):
            if not session["lock"].acquire(blocking=False):
                raise ValueError("上一个账号操作仍在进行，请稍后再试。")
            account.cancelled = cancelled
            try:
                if method == "models.account_login":
                    return account.login(p["username"], p["password"])
                if method == "models.account_verify":
                    return account.complete_2fa(p["code"])
                if method == "models.account_keys":
                    return {"keys": account.list_keys(), "groups": account.groups()}
                if method == "models.account_create_key":
                    return account.create_key(str(p.get("name") or "Cowork"), str(p.get("group") or ""), int(p.get("days") or 0), float(p.get("quota") or 0))
                if method == "models.account_reconcile":
                    return account.reconcile_creation()
                if method == "models.account_discover":
                    keys = {item["id"]: item for item in account.list_keys()}
                    selected = list(dict.fromkeys(str(key) for key in p.get("keys", [])))
                    if not selected:
                        raise ValueError("请至少选择一个密钥。")
                    records = []
                    for key in selected:
                        if key not in keys or not keys[key]["usable"]:
                            raise ValueError("所选密钥已不可用，请刷新列表。")
                        progress("正在读取 " + keys[key]["name"] + " 的模型…")
                        result = account.discover(keys[key])
                        session["results"][key] = result
                        records.extend({"key_id": key, "key_name": keys[key]["name"], **record} for record in result["records"])
                    return {"records": records}
                if method == "models.account_save":
                    from core.llm.gateway_accounts import channels_from_selection
                    from core.llm.gateway_catalog import reconcile_catalog
                    selected = {tuple(item) for item in p.get("selected", [])}
                    channels = channels_from_selection(session["results"], selected, str(p.get("name") or account.site_url))
                    if not channels:
                        raise ValueError("请至少选择一个可用模型。")
                    with self.s.config.batch_save():
                        current = self.s.config.get_model_channels()
                        for channel in channels:
                            old = next((item for item in current if item["channel_id"] == channel["channel_id"]), None)
                            if old:
                                names = {item["model_name"] for item in old["models"]}
                                channel["models"] = [*old["models"], *(item for item in channel["models"] if item["model_name"] not in names)]
                            current = [item for item in current if item["channel_id"] != channel["channel_id"]] + [channel]
                        self.s.config.set_model_channels(current, self.s.config.get_selected_model_id())
                    return self.s.public_settings()
                raise ValueError("不支持的账号操作。")
            finally:
                session["lock"].release()
        return self.s.jobs.submit("连接模型账号", perform)
