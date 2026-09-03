import json
import mimetypes
import os
import re
import sys

import requests


MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _emit(payload, *, failed=False):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    sys.stdout.flush()
    if failed:
        raise SystemExit(1)


def _redact(value, secret):
    text = str(value or "")
    if secret:
        text = text.replace(secret, "***")
    return re.sub(r"(?i)Bearer\s+[^\s,;]+", "Bearer ***", text).strip()


def _server_error(response, secret):
    message = ""
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or ""
        elif error:
            message = error
        if not message:
            message = payload.get("message") or payload.get("detail") or ""
    message = _redact(message, secret)[:1000]
    base = f"远程语音接口返回 HTTP {response.status_code}。"
    return f"{base} {message}".strip() if message else base


def _request_error_message(prefix, error, secret, source_path):
    detail = _redact(error, secret)
    if source_path:
        detail = detail.replace(str(source_path), "<audio>")
    detail = detail[:1000]
    return f"{prefix}：{detail}" if detail else prefix


def main():
    api_url = str(os.environ.get("ASR_API_URL") or "").strip()
    model_name = str(os.environ.get("ASR_MODEL_NAME") or "").strip()
    api_key = str(os.environ.get("ASR_API_KEY") or "").strip()
    source_path = str(os.environ.get("ASR_AUDIO_PATH") or "").strip()
    language = str(os.environ.get("ASR_LANGUAGE") or "auto").strip().lower()
    timeout_seconds = max(30, min(int(os.environ.get("ASR_TIMEOUT_SECONDS") or 1800), 3600))
    if not api_url or not model_name or not api_key or not source_path:
        _emit({
            "ok": False,
            "error": {
                "code": "remote_config_missing",
                "message": "远程语音转文字运行配置不完整。",
            },
        }, failed=True)
    if not os.path.isfile(source_path):
        _emit({
            "ok": False,
            "error": {
                "code": "audio_not_found",
                "message": "待转录音频文件不存在。",
            },
        }, failed=True)

    data = {"model": model_name, "stream": "false"}
    if language and language != "auto":
        data["language"] = language
    content_type = mimetypes.guess_type(source_path)[0] or "application/octet-stream"
    upload_name = "audio" + (os.path.splitext(source_path)[1].lower() or ".bin")
    try:
        with open(source_path, "rb") as audio_stream:
            response = requests.post(
                api_url,
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files={"file": (upload_name, audio_stream, content_type)},
                timeout=(min(30, timeout_seconds), timeout_seconds),
                allow_redirects=False,
            )
    except requests.exceptions.InvalidURL as exc:
        _emit({
            "ok": False,
            "error": {
                "code": "remote_invalid_url",
                "message": _request_error_message("远程语音接口地址无效", exc, api_key, source_path),
                "recovery": "检查语音转文字能力中的接口地址。",
                "retryable": False,
            },
        }, failed=True)
    except requests.exceptions.SSLError as exc:
        _emit({
            "ok": False,
            "error": {
                "code": "remote_tls_failed",
                "message": _request_error_message("远程语音接口 TLS 校验失败", exc, api_key, source_path),
                "recovery": "检查接口证书链或系统代理证书配置后重试。",
                "retryable": False,
            },
        }, failed=True)
    except requests.Timeout as exc:
        _emit({
            "ok": False,
            "error": {
                "code": "remote_timeout",
                "message": _request_error_message("远程语音接口请求超时", exc, api_key, source_path),
                "recovery": "检查接口状态或提高超时时间后重试。",
                "retryable": True,
            },
        }, failed=True)
    except requests.ConnectionError as exc:
        _emit({
            "ok": False,
            "error": {
                "code": "remote_connection_failed",
                "message": _request_error_message("远程连接在建立或上传过程中中断", exc, api_key, source_path),
                "recovery": "检查网络与服务端上传限制后重试；若同一文件重复失败，请不要继续重试。",
                "retryable": True,
            },
        }, failed=True)
    except requests.RequestException as exc:
        _emit({
            "ok": False,
            "error": {
                "code": "remote_request_failed",
                "message": _request_error_message("远程语音请求失败", exc, api_key, source_path),
                "recovery": "根据错误详情修正请求后重试。",
                "retryable": False,
            },
        }, failed=True)

    if response.status_code < 200 or response.status_code >= 300:
        _emit({
            "ok": False,
            "error": {
                "code": "remote_http_error",
                "message": _server_error(response, api_key),
                "http_status": response.status_code,
                "recovery": "根据服务端错误检查鉴权、模型和上传文件限制。",
                "retryable": response.status_code in {408, 429} or response.status_code >= 500,
            },
        }, failed=True)
    if len(response.content) > MAX_RESPONSE_BYTES:
        _emit({
            "ok": False,
            "error": {
                "code": "remote_response_too_large",
                "message": "远程语音接口响应超过 2 MiB，已拒绝处理。",
            },
        }, failed=True)
    try:
        payload = response.json()
    except (TypeError, ValueError):
        _emit({
            "ok": False,
            "error": {
                "code": "remote_invalid_json",
                "message": "远程语音接口未返回有效 JSON。",
            },
        }, failed=True)
    if not isinstance(payload, dict):
        _emit({
            "ok": False,
            "error": {
                "code": "remote_invalid_response",
                "message": "远程语音接口响应必须是 JSON 对象。",
            },
        }, failed=True)
    transcript = str(payload.get("text") or "").strip()
    if not transcript:
        _emit({
            "ok": False,
            "error": {
                "code": "remote_empty_transcript",
                "message": "远程语音接口响应缺少非空 text 字段。",
            },
        }, failed=True)
    _emit({
        "ok": True,
        "transcript": transcript,
        "model": model_name,
        "lang": language if language != "auto" else "",
        "duration": 0,
        "diarized": False,
        "speaker_count": 0,
        "warnings": ["远程接口返回纯文本，未执行本地说话人分离或时间戳对齐。"],
    })


if __name__ == "__main__":
    main()
