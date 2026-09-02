from urllib.parse import urlsplit


SPEECH_TO_TEXT_SKILL_ID = "speech-to-text"
ASR_BACKEND_FIELD = "ASR_BACKEND"
ASR_API_URL_FIELD = "ASR_API_URL"
ASR_MODEL_NAME_FIELD = "ASR_MODEL_NAME"
ASR_API_KEY_FIELD = "ASR_API_KEY"
ASR_BACKEND_LOCAL = "local"
ASR_BACKEND_OPENAI_COMPATIBLE = "openai_compatible"
SUPPORTED_ASR_BACKENDS = {
    ASR_BACKEND_LOCAL,
    ASR_BACKEND_OPENAI_COMPATIBLE,
}


def speech_to_text_config(values):
    source = values if isinstance(values, dict) else {}
    backend = str(source.get(ASR_BACKEND_FIELD) or ASR_BACKEND_LOCAL).strip().lower()
    return {
        "backend": backend,
        "api_url": str(source.get(ASR_API_URL_FIELD) or "").strip(),
        "model_name": str(source.get(ASR_MODEL_NAME_FIELD) or "").strip(),
        "api_key": str(source.get(ASR_API_KEY_FIELD) or "").strip(),
    }


def validate_transcription_api_url(value):
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("语音转文字接口地址无效。") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("语音转文字接口地址必须是完整的 HTTP 或 HTTPS URL。")
    if parsed.username or parsed.password:
        raise ValueError("语音转文字接口地址不能包含用户名或密码。")
    if parsed.fragment:
        raise ValueError("语音转文字接口地址不能包含 URL 片段。")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("语音转文字接口端口无效。")
    return raw


def validate_speech_to_text_config(values):
    config = speech_to_text_config(values)
    backend = config["backend"]
    if backend not in SUPPORTED_ASR_BACKENDS:
        raise ValueError(f"不支持的语音转文字方式：{backend or '空'}。")
    if backend == ASR_BACKEND_LOCAL:
        return config
    missing = [
        label
        for key, label in (
            ("api_url", "接口地址"),
            ("model_name", "模型名称"),
            ("api_key", "API Key"),
        )
        if not config[key]
    ]
    if missing:
        raise ValueError("远程语音转文字还缺少：" + "、".join(missing) + "。")
    config["api_url"] = validate_transcription_api_url(config["api_url"])
    return config


def speech_to_text_uses_remote(values):
    return speech_to_text_config(values)["backend"] == ASR_BACKEND_OPENAI_COMPATIBLE


def speech_to_text_http_warning(values):
    config = speech_to_text_config(values)
    if config["backend"] != ASR_BACKEND_OPENAI_COMPATIBLE:
        return ""
    try:
        parsed = urlsplit(config["api_url"])
    except ValueError:
        return ""
    if parsed.scheme.lower() == "http":
        return "当前接口使用 HTTP，音频与 API Key 将通过未加密连接传输。"
    return ""
