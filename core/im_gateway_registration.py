import threading


FEISHU_ADDONS = {
    "scopes": {
        "tenant": [
            "im:message",
            "im:message:send_as_bot",
            "cardkit:card:write",
        ],
    },
    "events": {"items": {"tenant": ["im.message.receive_v1"]}},
}


def register_feishu_app(
    on_qr_code,
    on_status_change=None,
    cancel_event=None,
    existing_app_id="",
):
    try:
        import lark_oapi as lark
    except Exception as exc:
        raise RuntimeError("飞书 SDK 未安装，请安装 lark-oapi 1.5.5 或更高版本。") from exc
    register = getattr(lark, "register_app", None)
    if not callable(register):
        raise RuntimeError("飞书 SDK 版本过低，请升级到 lark-oapi 1.5.5 或更高版本。")
    kwargs = {
        "on_qr_code": on_qr_code,
        "on_status_change": on_status_change,
        "cancel_event": cancel_event or threading.Event(),
        "source": "cowork-enterprise-message",
        "app_preset": {
            "name": "Cowork 智能体",
            "desc": "在飞书会话中使用 Cowork 默认主助手处理任务。",
        },
        "addons": FEISHU_ADDONS,
    }
    app_id = str(existing_app_id or "").strip()
    if app_id:
        kwargs["app_id"] = app_id
    else:
        kwargs["create_only"] = True
    result = register(**kwargs)
    if not isinstance(result, dict):
        raise RuntimeError("飞书未返回应用凭据。")
    client_id = str(result.get("client_id") or "").strip()
    client_secret = str(result.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("飞书返回的 App ID 或 App Secret 为空。")
    return {"app_id": client_id, "app_secret": client_secret}
