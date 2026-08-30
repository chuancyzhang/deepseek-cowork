from dataclasses import dataclass
from typing import Mapping, Tuple


ARTIFACT_DELIVERY_NATIVE = "native"
ARTIFACT_DELIVERY_LINK = "link"
ARTIFACT_DELIVERY_NONE = "none"

SCHEDULED_DELIVERY_NATIVE = "native"
SCHEDULED_DELIVERY_TEXT = "text"
SCHEDULED_DELIVERY_NONE = "none"


@dataclass(frozen=True)
class ProviderFieldSpec:
    key: str
    label: str
    placeholder: str = ""
    secret: bool = False
    required: bool = False
    advanced: bool = False
    help_text: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    title: str
    subtitle: str
    description: str
    icon: str
    auth_kind: str
    required_keys: Tuple[str, ...]
    runtime_adapter: str
    runtime_entry: str
    event_types: Tuple[str, ...]
    fields: Tuple[ProviderFieldSpec, ...] = ()
    connect_label: str = "开始接入"
    reconnect_label: str = "重新接入"
    capabilities: Tuple[str, ...] = ("文字", "链接")
    artifact_delivery_mode: str = ARTIFACT_DELIVERY_NONE
    scheduled_delivery_mode: str = SCHEDULED_DELIVERY_NONE

    def is_configured(self, value) -> bool:
        config = value if isinstance(value, dict) else {}
        return all(str(config.get(key) or "").strip() for key in self.required_keys)


IM_PROVIDER_SPECS: Tuple[ProviderSpec, ...] = (
    ProviderSpec(
        provider_id="feishu",
        title="飞书",
        subtitle="扫码即可创建并连接应用",
        description="使用飞书或 Lark 扫码确认，Cowork 会自动完成应用创建和长连接。",
        icon="fa5s.paper-plane",
        auth_kind="qr",
        required_keys=("app_id", "app_secret"),
        runtime_adapter="FeishuProvider",
        runtime_entry="feishu_long_connection",
        event_types=("im.message.receive_v1",),
        connect_label="扫码接入飞书",
        reconnect_label="重新扫码",
        capabilities=("文字", "链接", "文件", "图片"),
        artifact_delivery_mode=ARTIFACT_DELIVERY_NATIVE,
        scheduled_delivery_mode=SCHEDULED_DELIVERY_NATIVE,
    ),
    ProviderSpec(
        provider_id="dingtalk",
        title="钉钉",
        subtitle="连接钉钉机器人 Stream",
        description="准备好钉钉机器人的应用信息后，在这里完成连接。多数用户只需要填写应用凭据。",
        icon="fa5s.comment-dots",
        auth_kind="form",
        required_keys=("client_id", "client_secret", "ws_url"),
        runtime_adapter="DingTalkProvider",
        runtime_entry="websocket",
        event_types=("im.message.receive", "im.message.receive_v1", "message", "text"),
        fields=(
            ProviderFieldSpec(
                "client_id",
                "Client ID / App Key",
                "粘贴钉钉应用的 Client ID 或 App Key",
                required=True,
            ),
            ProviderFieldSpec(
                "client_secret",
                "Client Secret",
                "粘贴钉钉应用的 Client Secret",
                secret=True,
                required=True,
            ),
            ProviderFieldSpec(
                "ws_url",
                "Stream / WS URL",
                "wss://...",
                required=True,
                advanced=True,
                help_text="由钉钉 Stream 接入配置提供。",
            ),
            ProviderFieldSpec(
                "robot_code",
                "Robot Code",
                "可选",
                advanced=True,
            ),
            ProviderFieldSpec(
                "webhook_url",
                "Webhook URL",
                "https://...",
                advanced=True,
            ),
            ProviderFieldSpec(
                "secret",
                "签名密钥",
                "可选",
                secret=True,
                advanced=True,
            ),
        ),
        connect_label="连接钉钉",
        reconnect_label="保存并重新连接",
        artifact_delivery_mode=ARTIFACT_DELIVERY_LINK,
        scheduled_delivery_mode=SCHEDULED_DELIVERY_TEXT,
    ),
    ProviderSpec(
        provider_id="wecom",
        title="企业微信",
        subtitle="连接企业微信智能机器人",
        description="在企业微信后台创建智能机器人，然后粘贴 Bot ID 和 Secret。",
        icon="fa5s.comments",
        auth_kind="form",
        required_keys=("bot_id", "secret"),
        runtime_adapter="WeComProvider",
        runtime_entry="wecom",
        event_types=("im.message.receive", "im.message.receive_v1", "message", "text"),
        fields=(
            ProviderFieldSpec(
                "bot_id",
                "Bot ID",
                "粘贴企业微信机器人 Bot ID",
                required=True,
            ),
            ProviderFieldSpec(
                "secret",
                "Secret",
                "粘贴企业微信机器人 Secret",
                secret=True,
                required=True,
            ),
        ),
        connect_label="连接企业微信",
        reconnect_label="保存并重新连接",
        artifact_delivery_mode=ARTIFACT_DELIVERY_LINK,
        scheduled_delivery_mode=SCHEDULED_DELIVERY_TEXT,
    ),
    ProviderSpec(
        provider_id="qq",
        title="QQ",
        subtitle="手机 QQ 扫码连接官方机器人",
        description="用手机 QQ 扫码确认，Cowork 会自动创建或绑定 QQ 官方机器人。",
        icon="fa5b.qq",
        auth_kind="qr",
        required_keys=("app_id", "client_secret"),
        runtime_adapter="QQProvider",
        runtime_entry="qq",
        event_types=("message",),
        connect_label="使用 QQ 扫码",
        reconnect_label="重新扫码",
    ),
    ProviderSpec(
        provider_id="wechat",
        title="微信",
        subtitle="手机微信扫码连接",
        description="用手机微信扫码确认后，即可在微信私聊中使用 Cowork。",
        icon="fa5b.weixin",
        auth_kind="qr",
        required_keys=("bot_token", "ilink_bot_id"),
        runtime_adapter="WeChatProvider",
        runtime_entry="wechat",
        event_types=("message",),
        connect_label="使用微信扫码",
        reconnect_label="重新扫码",
    ),
)

IM_PROVIDER_ORDER = tuple(spec.provider_id for spec in IM_PROVIDER_SPECS)
IM_PROVIDER_BY_ID: Mapping[str, ProviderSpec] = {
    spec.provider_id: spec for spec in IM_PROVIDER_SPECS
}


def get_provider_spec(provider_id):
    return IM_PROVIDER_BY_ID.get(str(provider_id or "").strip().lower())


def provider_title(provider_id, default="企业消息"):
    spec = get_provider_spec(provider_id)
    return spec.title if spec else default


def provider_artifact_delivery_mode(provider_id):
    spec = get_provider_spec(provider_id)
    return spec.artifact_delivery_mode if spec else ARTIFACT_DELIVERY_NONE


def artifact_capable_provider_ids():
    return tuple(
        spec.provider_id
        for spec in IM_PROVIDER_SPECS
        if spec.artifact_delivery_mode != ARTIFACT_DELIVERY_NONE
    )


def provider_scheduled_delivery_mode(provider_id):
    spec = get_provider_spec(provider_id)
    return spec.scheduled_delivery_mode if spec else SCHEDULED_DELIVERY_NONE


def scheduled_delivery_provider_ids():
    return tuple(
        spec.provider_id
        for spec in IM_PROVIDER_SPECS
        if spec.scheduled_delivery_mode != SCHEDULED_DELIVERY_NONE
    )
