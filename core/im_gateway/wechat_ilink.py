import base64
import os
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

import httpx

from core.app_version import APP_VERSION


DEFAULT_ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_ILINK_BOT_TYPE = "3"
QR_POLL_TIMEOUT_SECONDS = 38.0
UPDATES_POLL_TIMEOUT_SECONDS = 40.0


class WeChatIlinkError(RuntimeError):
    pass


class WeChatTokenExpired(WeChatIlinkError):
    pass


class WeChatQrExpired(WeChatIlinkError):
    pass


class WeChatVerifyCodeBlocked(WeChatIlinkError):
    pass


@dataclass(frozen=True)
class WeChatQrCode:
    qrcode: str
    url: str
    expires_in: int = 300


@dataclass(frozen=True)
class WeChatQrCredentials:
    bot_token: str
    ilink_bot_id: str
    ilink_user_id: str = ""
    base_url: str = DEFAULT_ILINK_BASE_URL

    def as_dict(self):
        return {
            "bot_token": self.bot_token,
            "ilink_bot_id": self.ilink_bot_id,
            "ilink_user_id": self.ilink_user_id,
            "base_url": self.base_url,
        }


def _client_version_number():
    pieces = []
    for part in str(APP_VERSION or "0.0.0").split(".")[:3]:
        try:
            pieces.append(int(part))
        except (TypeError, ValueError):
            pieces.append(0)
    while len(pieces) < 3:
        pieces.append(0)
    major, minor, patch = pieces
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


def _random_wechat_uin():
    number = int.from_bytes(os.urandom(4), "big")
    return base64.b64encode(str(number).encode("utf-8")).decode("ascii")


def validate_ilink_base_url(value):
    raw = str(value or DEFAULT_ILINK_BASE_URL).strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or not hostname:
        raise WeChatIlinkError("微信返回了无效的连接地址。")
    if hostname != "ilinkai.weixin.qq.com" and not hostname.endswith(".weixin.qq.com"):
        raise WeChatIlinkError("微信返回了未经允许的连接地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise WeChatIlinkError("微信返回了无效的连接地址。")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise WeChatIlinkError("微信返回了无效的连接地址。") from exc
    if parsed_port not in (None, 443):
        raise WeChatIlinkError("微信返回了不受支持的连接端口。")
    port = f":{parsed_port}" if parsed_port else ""
    return f"https://{hostname}{port}"


def parse_wechat_updates(payload):
    if not isinstance(payload, dict):
        raise WeChatIlinkError("微信消息响应格式无效。")
    messages = payload.get("msgs")
    if not isinstance(messages, list):
        return []
    events = []
    for raw in messages:
        if not isinstance(raw, dict) or int(raw.get("message_type") or 0) == 2:
            continue
        items = raw.get("item_list")
        items = items if isinstance(items, list) else []
        text = ""
        item_type = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            current_type = int(item.get("type") or 0)
            if not item_type:
                item_type = current_type
            if current_type == 1:
                text_item = item.get("text_item")
                if isinstance(text_item, dict):
                    text = str(text_item.get("text") or "").strip()
                    if text:
                        item_type = 1
                        break
        user_id = str(raw.get("from_user_id") or "").strip()
        message_id = str(raw.get("message_id") or "").strip()
        if not user_id or not message_id:
            continue
        events.append(
            {
                "provider": "wechat",
                "event_type": "message",
                "sender_type": "user",
                "message_type": "text" if text else "unsupported",
                "unsupported_type": item_type,
                "text": text,
                "user_id": user_id,
                "chat_id": user_id,
                "message_id": message_id,
                "create_time": str(raw.get("create_time_ms") or ""),
                "context_token": str(raw.get("context_token") or ""),
            }
        )
    return events


class WeChatIlinkClient:
    def __init__(self, base_url=DEFAULT_ILINK_BASE_URL, http_client=None):
        self.base_url = validate_ilink_base_url(base_url)
        self._client = http_client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = http_client is None

    async def close(self):
        if self._owns_client:
            await self._client.aclose()

    def set_base_url(self, value):
        self.base_url = validate_ilink_base_url(value)
        return self.base_url

    def use_redirect_host(self, value):
        return self.set_base_url(value)

    @staticmethod
    def _common_headers():
        return {
            "iLink-App-Id": "bot",
            "iLink-App-ClientVersion": str(_client_version_number()),
        }

    @classmethod
    def _authenticated_headers(cls, token):
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_wechat_uin(),
            **cls._common_headers(),
        }
        token_text = str(token or "").strip()
        if token_text:
            headers["Authorization"] = f"Bearer {token_text}"
        return headers

    @staticmethod
    def _base_info():
        return {"channel_version": f"cowork-{APP_VERSION}"}

    async def _request_json(
        self,
        method,
        path,
        *,
        headers=None,
        json_body=None,
        timeout=15.0,
        timeout_is_wait=False,
    ):
        url = self.base_url.rstrip("/") + "/" + str(path or "").lstrip("/")
        try:
            response = await self._client.request(
                method,
                url,
                headers=headers or {},
                json=json_body,
                timeout=timeout,
            )
        except httpx.TimeoutException:
            if timeout_is_wait:
                return {"status": "wait"}
            raise WeChatIlinkError("连接微信超时，请检查网络后重试。") from None
        except httpx.HTTPError as exc:
            raise WeChatIlinkError(f"无法连接微信：{exc}") from exc
        if int(getattr(response, "status_code", 0) or 0) != 200:
            raise WeChatIlinkError(
                f"微信接口返回 HTTP {getattr(response, 'status_code', '未知')}。"
            )
        try:
            data = response.json()
        except Exception as exc:
            raise WeChatIlinkError("微信接口返回了无法识别的数据。") from exc
        if not isinstance(data, dict):
            raise WeChatIlinkError("微信接口返回格式无效。")
        return data

    async def create_qr_code(self, local_tokens=None):
        self.base_url = DEFAULT_ILINK_BASE_URL
        query = urlencode({"bot_type": DEFAULT_ILINK_BOT_TYPE})
        data = await self._request_json(
            "POST",
            f"/ilink/bot/get_bot_qrcode?{query}",
            headers=self._authenticated_headers(""),
            json_body={"local_token_list": list(local_tokens or [])[-10:]},
            timeout=15.0,
        )
        qrcode = str(data.get("qrcode") or "").strip()
        url = str(data.get("qrcode_img_content") or "").strip()
        if not qrcode or not url:
            raise WeChatIlinkError("微信没有返回可用的二维码。")
        return WeChatQrCode(qrcode=qrcode, url=url)

    async def poll_qr_status(self, qrcode, verify_code=""):
        query = {"qrcode": str(qrcode or "")}
        if verify_code:
            query["verify_code"] = str(verify_code)
        data = await self._request_json(
            "GET",
            "/ilink/bot/get_qrcode_status?" + urlencode(query),
            headers=self._common_headers(),
            timeout=QR_POLL_TIMEOUT_SECONDS,
            timeout_is_wait=True,
        )
        status = str(data.get("status") or "wait").strip().lower()
        if status == "expired":
            raise WeChatQrExpired("微信二维码已过期，请重新生成。")
        if status == "verify_code_blocked":
            raise WeChatVerifyCodeBlocked("配对码多次输入错误，请稍后重新扫码。")
        return data

    @staticmethod
    def credentials_from_status(data):
        payload = data if isinstance(data, dict) else {}
        bot_token = str(payload.get("bot_token") or "").strip()
        bot_id = str(payload.get("ilink_bot_id") or "").strip()
        if not bot_token or not bot_id:
            raise WeChatIlinkError("微信已确认，但没有返回完整的登录凭据。")
        return WeChatQrCredentials(
            bot_token=bot_token,
            ilink_bot_id=bot_id,
            ilink_user_id=str(payload.get("ilink_user_id") or "").strip(),
            base_url=validate_ilink_base_url(
                payload.get("baseurl") or DEFAULT_ILINK_BASE_URL
            ),
        )

    async def get_updates(self, token, cursor=""):
        data = await self._request_json(
            "POST",
            "/ilink/bot/getupdates",
            headers=self._authenticated_headers(token),
            json_body={
                "get_updates_buf": str(cursor or ""),
                "base_info": self._base_info(),
            },
            timeout=UPDATES_POLL_TIMEOUT_SECONDS,
            timeout_is_wait=True,
        )
        if data.get("status") == "wait":
            return {"ret": 0, "msgs": [], "get_updates_buf": str(cursor or "")}
        self._raise_protocol_error(data, "接收微信消息")
        return data

    async def send_text(self, token, to_user_id, context_token, text):
        client_id = "cowork_" + uuid.uuid4().hex
        data = await self._request_json(
            "POST",
            "/ilink/bot/sendmessage",
            headers=self._authenticated_headers(token),
            json_body={
                "msg": {
                    "from_user_id": "",
                    "to_user_id": str(to_user_id or ""),
                    "client_id": client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "item_list": [
                        {
                            "type": 1,
                            "text_item": {"text": str(text or "")},
                        }
                    ],
                    "context_token": str(context_token or ""),
                },
                "base_info": self._base_info(),
            },
            timeout=20.0,
        )
        self._raise_protocol_error(data, "发送微信消息")
        return data.get("message_id") or data.get("msg_id") or client_id

    @staticmethod
    def _raise_protocol_error(data, action):
        errcode = int(data.get("errcode") or 0)
        ret = int(data.get("ret") or 0)
        if errcode == -14 or ret == -14:
            raise WeChatTokenExpired("微信登录已过期，请重新扫码接入。")
        if ret != 0 or errcode != 0:
            message = str(data.get("errmsg") or data.get("msg") or "未知错误")
            raise WeChatIlinkError(
                f"{action}失败：ret={ret}，errcode={errcode}，{message}"
            )
