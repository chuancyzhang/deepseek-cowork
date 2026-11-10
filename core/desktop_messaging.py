"""Message-channel setup and a hidden gateway owned by the desktop service."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from core.desktop_jobs import JobCancelled
from core.desktop_settings import action, field, DesktopSettings
from core.im_gateway_config import normalize_im_gateway_config, update_selected_provider, disable_im_gateway
from core.im_gateway_registry import IM_PROVIDER_SPECS, get_provider_spec

log = logging.getLogger("desktop.messaging")


class DesktopMessaging:
    methods = frozenset({"im.verify"})

    def __init__(self, service):
        self.s = service
        self.lock = threading.RLock()
        self.process = None
        self.verify_code = ""
        self.verifying = False
        self.onboarding = False

    def configuration(self):
        return normalize_im_gateway_config(self.s.config.get("im_gateway"))

    def page(self):
        from core.im_gateway_status import read_im_gateway_status
        config = self.configuration()
        values = {"enabled_provider": next(iter(config["enabled_providers"]), "")}
        sections = [{"fields": [field("enabled_provider", "接收消息的渠道", "select", options=[{"value": "", "label": "暂不接收"}] + [
                    {"value": item.provider_id, "label": item.title} for item in IM_PROVIDER_SPECS])]}]
        status = read_im_gateway_status()
        states = {"stopped": "已停止", "connecting": "正在连接", "connected": "已连接", "reconnecting": "正在重连", "disconnected": "连接已断开", "error": "连接失败"}
        for spec in IM_PROVIDER_SPECS:
            current = config["providers"][spec.provider_id]
            sections.append({"id": spec.provider_id, "title": spec.title, "description": spec.description,
                "fields": [field(spec.provider_id + ":" + item.key, item.label, "password" if item.secret else "text",
                    placeholder="已保存，留空保留" if item.secret and current.get(item.key) else item.placeholder, help=item.help_text)
                    for item in spec.fields],
                "status": (states.get(status.get("state"), "尚未连接") + (" · " + status["error"] if status.get("error") else "")) if status.get("provider") == spec.provider_id else "已保存凭据" if spec.is_configured(current) else "尚未配置",
                "actions": [action("im.qr:" + spec.provider_id, spec.reconnect_label if spec.is_configured(current) else spec.connect_label)] if spec.auth_kind == "qr" else []})
            for item in spec.fields:
                values[spec.provider_id + ":" + item.key] = "" if item.secret else current.get(item.key, "")
        sections.append({"actions": [action("im.restart", "重新连接"), action("im.disconnect", "停止接收")]})
        return {"title": "消息连接", "description": "选择一个聊天软件接收任务，回复仍保存在本机对话中。", "values": values,
                "sections": sections, "revision": DesktopSettings.revision(config)}

    def save(self, values, p):
        with self.lock:
            if p.get("revision") != self.page()["revision"]:
                raise ValueError("消息连接已变化，请刷新后重新核对。")
            config = self.configuration()
            for spec in IM_PROVIDER_SPECS:
                for item in spec.fields:
                    value = str(values.get(spec.provider_id + ":" + item.key) or "").strip()
                    if value or not item.secret:
                        config["providers"][spec.provider_id][item.key] = value
            selected = str(values.get("enabled_provider") or "")
            if selected:
                spec = get_provider_spec(selected)
                if not spec or not spec.is_configured(config["providers"].get(selected)):
                    raise ValueError("请先完成所选渠道的连接信息或扫码。")
                config = update_selected_provider(config, selected, config["providers"][selected])
            else:
                config = disable_im_gateway(config)
            self.s.config.set("im_gateway", config)
            self.restart()
            page = self.page()
            return {"saved": True, "values": page["values"], "revision": page["revision"]}

    def close(self):
        with self.lock:
            process, self.process = self.process, None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            from core.im_gateway_status import write_im_gateway_status
            write_im_gateway_status("", "stopped")

    def restart(self):
        with self.lock:
            self.close()
            enabled = self.configuration()["enabled_providers"]
            if not enabled or self.s.closing:
                return
            from core.process_utils import subprocess_kwargs_no_window
            from core.runtime_logs import log_path
            root = Path(__file__).resolve().parents[1]
            command = [sys.executable, *( [] if getattr(sys, "frozen", False) else ["-X", "utf8", "-B", "-m", "core.desktop_service"]),
                       "--data-dir", self.s.config.data_dir, "--im-gateway"]
            # The child consumes these values before invoking any model tools.
            env = {**os.environ, "COWORK_GATEWAY_PORT": str(self.s.port), "COWORK_GATEWAY_TOKEN": self.s.token}
            path = Path(log_path("im_gateway_stderr", self.s.config.data_dir))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab", buffering=0) as handle:
                self.process = subprocess.Popen(command, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                    stdout=handle, stderr=handle, **subprocess_kwargs_no_window())
            log.info("gateway_started provider=%s pid=%s", enabled[0], self.process.pid)

    def dispatch(self, method, p):
        with self.lock:
            if not self.verifying:
                raise ValueError("当前没有等待配对码的连接。")
            code = str(p.get("code") or "").strip()
            if not code or len(code) > 32:
                raise ValueError("请输入手机上显示的配对码。")
            self.verify_code, self.verifying = code, False
            return {"accepted": True}

    def action(self, identity, p):
        if identity == "im.restart":
            self.restart()
            return {"refresh": True}
        if identity == "im.disconnect":
            self.s.config.set("im_gateway", disable_im_gateway(self.configuration()))
            self.close()
            return {"refresh": True}
        provider = identity.split(":", 1)[1]
        spec = get_provider_spec(provider)
        if not spec or spec.auth_kind != "qr":
            raise ValueError("此渠道不支持扫码。")
        with self.lock:
            if self.onboarding:
                raise ValueError("另一个扫码连接正在进行，请先完成或取消。")
            self.onboarding = True
        existing = self.configuration()["providers"][provider].copy()
        def onboard(cancelled, progress):
            def report(message, **extra):
                if cancelled.is_set():
                    raise JobCancelled("已取消扫码，原连接保留。")
                progress(message, **extra)
            def qr(url, expires=300):
                import qrcode
                buffer = io.BytesIO()
                qrcode.make(url).save(buffer, format="PNG")
                report("请使用" + spec.title + "扫码确认", qr_data="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(), expires_at=time.time() + expires)
            try:
                report("正在获取二维码…")
                if provider == "feishu":
                    from core.im_gateway_registration import register_feishu_app
                    credentials = register_feishu_app(lambda info: qr(str(info.get("url") or ""), int(info.get("expire_in") or 300)),
                        lambda info: report("等待飞书确认…"), cancelled, str(existing.get("app_id") or ""))
                else:
                    credentials = asyncio.run(self.async_onboard(provider, existing, cancelled, report, qr))
                report("正在保存连接…", qr_data="", verification_required=False)
                with self.lock:
                    # Preserve changes to other channels made while the user scanned.
                    config = update_selected_provider(self.configuration(), provider, credentials)
                    self.s.config.set("im_gateway", config)
                    self.restart()
                return {"refresh": True, "message": "连接已保存，正在接收消息。"}
            finally:
                with self.lock:
                    self.onboarding, self.verifying, self.verify_code = False, False, ""
        return self.s.jobs.submit("连接" + spec.title, onboard)

    async def async_onboard(self, provider, existing, cancelled, report, qr):
        async def run():
            if provider == "qq":
                from qqbot_agent_sdk import start_onboard
                logging.getLogger("qqbot_agent_sdk").setLevel(logging.WARNING)
                result = await start_onboard(on_qr_ready=lambda url: qr(str(url)), poll_timeout=300.0)
                return {"app_id": str(result.app_id), "client_secret": str(result.client_secret), "user_openid": str(result.user_openid or "")}
            from core.im_gateway.wechat_ilink import WeChatIlinkClient
            client = WeChatIlinkClient()
            try:
                code = await client.create_qr_code(local_tokens=[existing["bot_token"]] if existing.get("bot_token") else [])
                qr(code.url, code.expires_in)
                deadline, verify = time.monotonic() + code.expires_in, ""
                while time.monotonic() < deadline:
                    status = await client.poll_qr_status(code.qrcode, verify_code=verify)
                    verify = ""
                    state = str(status.get("status") or "wait")
                    if state == "wait":
                        continue
                    if state == "scaned":
                        report("已扫码，请在微信确认…")
                    elif state == "need_verifycode":
                        self.verifying = True
                        report("请输入手机微信显示的配对码", verification_required=True)
                        while not self.verify_code:
                            await asyncio.sleep(.1)
                        verify, self.verify_code = self.verify_code, ""
                        report("正在确认配对码…", verification_required=False)
                    elif state == "scaned_but_redirect":
                        client.use_redirect_host(status.get("redirect_host"))
                    elif state == "binded_redirect" and existing.get("bot_token") and existing.get("ilink_bot_id"):
                        return existing
                    elif state == "confirmed":
                        return client.credentials_from_status({"baseurl": client.base_url, **status}).as_dict()
                    else:
                        raise ValueError("微信扫码未完成：" + state)
                raise TimeoutError("二维码已过期，请重新扫码。")
            finally:
                await client.close()
        task = asyncio.create_task(run())
        try:
            while not task.done():
                if cancelled.is_set():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise JobCancelled("已取消扫码，原连接保留。")
                await asyncio.wait({task}, timeout=.2)
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
