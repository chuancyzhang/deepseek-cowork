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
_KNOWN_REMOTE_LIMITS = {
    ("open.bigmodel.cn", "glm-asr-2512"): {
        "label": "GLM-ASR-2512",
        "extensions": {".wav", ".mp3"},
        "max_file_bytes": 25_000_000,
        "max_duration_seconds": 30,
    },
}


class RemoteTranscriptionInputError(ValueError):
    def __init__(self, message, *, code="remote_input_unsupported", recovery="", details=None):
        super().__init__(message)
        self.code = str(code or "remote_input_unsupported")
        self.recovery = str(recovery or "")
        self.retryable = False
        self.details = dict(details or {})


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


def validate_remote_transcription_input(config, audio_path):
    """Fail before AI/tool execution when a known remote provider rejects the input contract."""
    source = config if isinstance(config, dict) else {}
    if str(source.get("backend") or "").strip().lower() != ASR_BACKEND_OPENAI_COMPATIBLE:
        return None
    try:
        hostname = str(urlsplit(str(source.get("api_url") or "")).hostname or "").lower()
    except ValueError:
        return None
    model_name = str(source.get("model_name") or "").strip().lower()
    limits = _KNOWN_REMOTE_LIMITS.get((hostname, model_name))
    if not limits:
        return None

    import os

    path = os.path.abspath(str(audio_path or ""))
    extension = os.path.splitext(path)[1].lower()
    try:
        file_size_bytes = os.path.getsize(path)
    except OSError:
        return None
    issues = []
    if extension not in limits["extensions"]:
        issues.append(f"当前格式为 {extension.lstrip('.').upper() or '未知'}")
    if file_size_bytes > limits["max_file_bytes"]:
        issues.append(f"当前大小为 {file_size_bytes / 1_000_000:.1f} MB")
    if not issues:
        return None

    accepted = "/".join(item.lstrip(".").upper() for item in sorted(limits["extensions"]))
    message = (
        f"{limits['label']} 仅接受 {accepted}，文件不超过 25 MB、音频不超过 "
        f"{limits['max_duration_seconds']} 秒；" + "，".join(issues) + "。"
    )
    raise RemoteTranscriptionInputError(
        message,
        recovery="请切换到本地语音组件，或先将音频转换并切分为符合接口限制的小文件。",
        details={
            "provider": hostname,
            "model": model_name,
            "extension": extension,
            "file_size_bytes": file_size_bytes,
            "max_file_bytes": limits["max_file_bytes"],
        },
    )
