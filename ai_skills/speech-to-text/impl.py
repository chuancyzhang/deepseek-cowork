import json
import os
import re
import subprocess
import tempfile
import time
from datetime import date

from PySide6.QtCore import QObject, Qt

from core.audio_attachments import is_audio_attachment
from core.runtime_components import speech_to_text_component_status
from core.sandbox_runtime import run_skill_script_in_sandbox
from core.speech_to_text_config import (
    ASR_BACKEND_LOCAL,
    ASR_BACKEND_OPENAI_COMPATIBLE,
    validate_speech_to_text_config,
)


SKILL_ID = "speech-to-text"
SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "transcribe.mjs")
REMOTE_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "scripts", "transcribe_remote.py"
)
SUPPORTED_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".webm"
}


class ComponentNotReadyError(RuntimeError):
    pass


class RemoteTranscriptionError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code or "remote_transcription_failed")


def _json(payload):
    return json.dumps(payload, ensure_ascii=False, allow_nan=False)


def _emit_step(context, message):
    signal = context.get("step_signal") if isinstance(context, dict) else None
    if hasattr(signal, "emit"):
        signal.emit(str(message))


def _emit_diagnostic(context, status, started_at, **fields):
    signal = context.get("observability_signal") if isinstance(context, dict) else None
    if not hasattr(signal, "emit"):
        return
    payload = {
        "type": "speech_to_text",
        "status": status,
        "duration_seconds": round(max(0.0, time.time() - started_at), 3),
        "timestamp": time.time(),
    }
    payload.update(fields)
    signal.emit(payload)


def _error(code, message, recovery="", **extra):
    payload = {
        "ok": False,
        "status": "error",
        "error": {
            "code": code,
            "message": message,
            "recovery": recovery,
        },
    }
    payload.update(extra)
    return payload


def _is_within(path, root):
    try:
        path_value = os.path.normcase(os.path.abspath(path))
        root_value = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([path_value, root_value]) == root_value
    except (OSError, ValueError):
        return False


def _attachment_paths(context):
    paths = []
    seen = set()
    if not isinstance(context, dict):
        return paths
    for message in reversed(context.get("current_messages_snapshot") or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        for value in meta.get("user_added_files") or []:
            if value:
                resolved = os.path.abspath(str(value))
                key = os.path.normcase(resolved)
                if key not in seen:
                    seen.add(key)
                    paths.append(resolved)
        for part in message.get("content_parts") or []:
            if not isinstance(part, dict) or part.get("type") not in {"input_file", "input_audio"}:
                continue
            value = part.get("path")
            if value:
                resolved = os.path.abspath(str(value))
                key = os.path.normcase(resolved)
                if key not in seen:
                    seen.add(key)
                    paths.append(resolved)
        break
    return paths


def _resolve_audio_path(audio_path, workspace_dir, context):
    workspace_root = os.path.abspath(str(workspace_dir or "")) if workspace_dir else ""
    if not workspace_root or not os.path.isdir(workspace_root):
        raise ValueError("当前工作区不可用。")
    raw = os.path.expandvars(os.path.expanduser(str(audio_path or "").strip()))
    if not raw:
        candidates = [
            path
            for path in _attachment_paths(context)
            if is_audio_attachment(path) and os.path.isfile(path)
        ]
        if len(candidates) != 1:
            raise ValueError(
                "未指定 audio_path 时，本轮必须恰好包含一个本地音频附件。"
                f"当前找到 {len(candidates)} 个。"
            )
        resolved = candidates[0]
        auto_selected = True
    else:
        resolved = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(workspace_root, raw))
        auto_selected = False
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"音频文件不存在：{resolved}")
    if os.path.splitext(resolved)[1].lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("不支持该文件格式。支持 WAV、MP3、M4A、AAC、FLAC、OGG、Opus、MP4 和 WebM。")
    attachment_keys = {os.path.normcase(path) for path in _attachment_paths(context)}
    key = os.path.normcase(resolved)
    if not _is_within(resolved, workspace_root) and key not in attachment_keys:
        raise PermissionError("只能读取当前工作区文件或本会话中用户明确附加的文件。")
    return workspace_root, resolved, auto_selected


def _safe_stem(path):
    stem = os.path.splitext(os.path.basename(path))[0].strip() or "audio"
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem).strip(" ._")
    return stem or "audio"


def _resolve_workspace_markdown(path, workspace_root, default_name):
    raw = str(path or "").strip()
    resolved = os.path.abspath(
        raw if raw and os.path.isabs(raw) else os.path.join(workspace_root, raw or default_name)
    )
    if not _is_within(resolved, workspace_root):
        raise PermissionError("输出文件必须位于当前工作区内。")
    if os.path.splitext(resolved)[1].lower() != ".md":
        raise ValueError("输出文件必须使用 .md 扩展名。")
    return resolved


def _raw_sidecar_path(final_path):
    root, ext = os.path.splitext(final_path)
    return root + ".raw" + ext


def _check_write_target(path, overwrite):
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(f"输出文件已存在：{path}。如需覆盖，请先获得用户明确同意。")


def _atomic_write_text(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle, temp_path = tempfile.mkstemp(prefix=".speech-to-text-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _raise_if_aborted(abort_state):
    if isinstance(abort_state, dict) and abort_state.get("aborted"):
        raise InterruptedError("用户已停止语音转文字任务。")


def _require_component():
    status = speech_to_text_component_status(include_size=False)
    if not status.get("ready"):
        detail = str(status.get("health_error") or "语音转文字组件尚未安装。")
        raise ComponentNotReadyError(
            f"{detail} 请前往“设置 → 组件与依赖”安装或修复“语音转文字组件”。"
        )
    paths = status.get("model_paths") if isinstance(status.get("model_paths"), dict) else {}
    required = {"sensevoice_model", "sensevoice_tokens", "segmentation", "embedding"}
    if not required.issubset(paths):
        raise ComponentNotReadyError("语音转文字组件状态缺少模型路径，请在“组件与依赖”中修复。")
    return paths


def _init_abort_state(context):
    state = {"aborted": False, "bridge": None}
    if not isinstance(context, dict) or not context.get("abort_signal"):
        return state

    class SignalBridge(QObject):
        def trigger(self):
            state["aborted"] = True

    bridge = SignalBridge()
    context["abort_signal"].connect(bridge.trigger, Qt.DirectConnection)
    state["bridge"] = bridge
    return state


def _frontmatter_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value or ""), ensure_ascii=False)


def _render_markdown(source_path, payload, transcript, polished, privacy):
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    metadata = {
        "source": os.path.basename(source_path),
        "date": date.today().isoformat(),
        "asr_backend": payload.get("backend") or "local",
        "asr_model": payload.get("model") or "",
        "duration_seconds": round(float(payload.get("duration") or 0.0), 3),
        "language": payload.get("lang") or "",
        "speaker_count": int(payload.get("speaker_count") or 0),
        "speaker_diarization": bool(payload.get("diarized")),
        "ai_polished": bool(polished),
        "privacy": privacy,
        "source_warning": "；".join(str(item) for item in warnings if item),
    }
    header = "\n".join(f"{key}: {_frontmatter_value(value)}" for key, value in metadata.items())
    return f"---\n{header}\n---\n\n{str(transcript or '').strip()}\n"


def _parse_json_stdout(stdout):
    text = str(stdout or "").strip()
    if not text:
        raise RuntimeError("转录脚本没有返回结果。")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"转录脚本返回了无效 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("转录脚本结果必须是 JSON 对象。")
    return payload


def _configured_backend(context):
    values = context.get("skill_config") if isinstance(context, dict) else {}
    return validate_speech_to_text_config(values if isinstance(values, dict) else {})


def _run_remote_transcription(
    source_path,
    workspace_root,
    language,
    timeout_seconds,
    config,
    abort_state,
):
    result = run_skill_script_in_sandbox(
        SKILL_ID,
        REMOTE_SCRIPT_PATH,
        "python",
        args=[],
        cwd=workspace_root,
        timeout_seconds=timeout_seconds,
        extra_env={
            "COWORK_WORKSPACE_DIR": workspace_root,
            "ASR_API_URL": config["api_url"],
            "ASR_MODEL_NAME": config["model_name"],
            "ASR_API_KEY": config["api_key"],
            "ASR_AUDIO_PATH": source_path,
            "ASR_LANGUAGE": language,
            "ASR_TIMEOUT_SECONDS": str(timeout_seconds),
        },
        abort_check=lambda: bool(abort_state["aborted"]),
    )
    if result.get("aborted"):
        return None, True
    try:
        payload = _parse_json_stdout(result.get("stdout"))
    except Exception as exc:
        if result.get("ok"):
            raise
        raise RemoteTranscriptionError(
            "remote_transcription_failed",
            "远程语音接口执行失败，且没有返回可解析的错误信息。",
        ) from exc
    if not result.get("ok") or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        raise RemoteTranscriptionError(
            error.get("code"),
            str(error.get("message") or "远程语音接口转录失败。"),
        )
    return payload, False


def transcribe_audio(
    audio_path,
    polish,
    diarize=None,
    speaker_count=0,
    model=None,
    language="auto",
    output_path="",
    overwrite=False,
    timeout_seconds=1800,
    workspace_dir=None,
    _context=None,
):
    """Transcribe one user-authorized audio file and write a Markdown transcript."""
    started_at = time.time()
    context = _context if isinstance(_context, dict) else {}
    backend = ""
    try:
        if not isinstance(polish, bool):
            raise ValueError("polish 必须是布尔值，并且必须来自用户对是否 AI 润色的明确选择。")
        config = _configured_backend(context)
        backend = config["backend"]
        requested_model = str(model or "").strip().lower()
        if backend == ASR_BACKEND_LOCAL:
            effective_model = requested_model or "sensevoice"
            if effective_model != "sensevoice":
                raise ValueError("本地 model 目前只支持 sensevoice。")
            effective_diarize = True if diarize is None else bool(diarize)
        else:
            if requested_model:
                raise RemoteTranscriptionError(
                    "remote_model_parameter_unsupported",
                    "远程模式的模型由能力设置管理，请不要传入本地 model 参数。",
                )
            if diarize is True or int(speaker_count or 0) > 0:
                raise RemoteTranscriptionError(
                    "remote_diarization_unsupported",
                    "OpenAI 兼容接口模式不执行本地说话人分离；请关闭 diarize 且不要指定 speaker_count。",
                )
            effective_model = config["model_name"]
            effective_diarize = False
        language = str(language or "auto").strip().lower()
        if language not in {"auto", "zh", "en", "ja", "ko", "yue"}:
            raise ValueError("language 只能是 auto、zh、en、ja、ko 或 yue。")
        speaker_count = int(speaker_count or 0)
        if speaker_count < 0 or speaker_count > 20:
            raise ValueError("speaker_count 必须是 0（自动估算）或 1 到 20。")
        timeout_seconds = max(30, min(int(timeout_seconds or 1800), 3600))
        workspace_root, source_path, auto_selected = _resolve_audio_path(
            audio_path,
            workspace_dir,
            context,
        )
        final_path = _resolve_workspace_markdown(
            output_path,
            workspace_root,
            (
                f"local-audio-transcript-{int(started_at)}.md"
                if auto_selected
                else f"{_safe_stem(source_path)}-transcript.md"
            ),
        )
        raw_path = _raw_sidecar_path(final_path) if polish else final_path
        _check_write_target(final_path, bool(overwrite))
        if polish:
            _check_write_target(raw_path, bool(overwrite))

        _emit_diagnostic(
            context,
            "submit",
            started_at,
            extension=os.path.splitext(source_path)[1].lower(),
            file_size_bytes=os.path.getsize(source_path),
            backend=backend,
            model=effective_model,
            diarize=effective_diarize,
            polish=polish,
        )
        abort_state = _init_abort_state(context)
        _raise_if_aborted(abort_state)
        if backend == ASR_BACKEND_LOCAL:
            model_paths = _require_component()
            _emit_diagnostic(context, "start", started_at, operation="local_transcription_pipeline")
            args = [
                "--input", source_path,
                "--language", language,
                "--sensevoice-model", model_paths["sensevoice_model"],
                "--sensevoice-tokens", model_paths["sensevoice_tokens"],
                "--diarize", "true" if effective_diarize else "false",
                "--speaker-count", str(speaker_count),
            ]
            if effective_diarize:
                args.extend([
                    "--segmentation-model", model_paths["segmentation"],
                    "--embedding-model", model_paths["embedding"],
                ])
            _emit_step(
                context,
                (
                    "正在本地分块识别音频并分离说话人；长录音可能需要数分钟…"
                    if effective_diarize
                    else "正在本地分块识别音频；长录音可能需要数分钟…"
                ),
            )
            _emit_diagnostic(context, "run", started_at, operation="local_asr")
            result = run_skill_script_in_sandbox(
                SKILL_ID,
                SCRIPT_PATH,
                "node",
                args=args,
                cwd=workspace_root,
                timeout_seconds=timeout_seconds,
                extra_env={"COWORK_WORKSPACE_DIR": workspace_root},
                abort_check=lambda: bool(abort_state["aborted"]),
            )
            if result.get("aborted"):
                _emit_diagnostic(context, "finish", started_at, outcome="aborted", backend=backend)
                return _json(_error("aborted", "用户已停止语音转文字任务。", "可重新发起转录。"))
            if not result.get("ok"):
                payload = None
                try:
                    payload = _parse_json_stdout(result.get("stdout"))
                except Exception:
                    pass
                error = payload.get("error") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else {}
                message = str(error.get("message") or result.get("stderr") or "本地转录脚本执行失败。").strip()
                raise RuntimeError(message[-2000:])
            payload = _parse_json_stdout(result.get("stdout"))
            if not payload.get("ok"):
                error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                raise RuntimeError(str(error.get("message") or "本地转录失败。"))
        else:
            _emit_diagnostic(context, "start", started_at, operation="remote_transcription_pipeline")
            _emit_step(context, "正在上传音频并等待已配置的语音接口返回转录结果…")
            _emit_diagnostic(context, "run", started_at, operation="remote_asr")
            payload, aborted = _run_remote_transcription(
                source_path,
                workspace_root,
                language,
                timeout_seconds,
                config,
                abort_state,
            )
            if aborted:
                _emit_diagnostic(context, "finish", started_at, outcome="aborted", backend=backend)
                return _json(_error("aborted", "用户已停止语音转文字任务。", "可重新发起转录。"))
        if payload is None:
            _emit_diagnostic(context, "finish", started_at, outcome="aborted")
            return _json(_error("aborted", "用户已停止语音转文字任务。", "可重新发起转录。"))
        payload["backend"] = backend
        transcript = str(payload.get("transcript") or "").strip()
        if not transcript:
            raise RuntimeError(
                "本地模型没有识别出可用文字。"
                if backend == ASR_BACKEND_LOCAL
                else "远程语音接口没有返回可用文字。"
            )
        raw_markdown = _render_markdown(
            source_path,
            payload,
            transcript,
            polished=False,
            privacy=(
                ("local-raw" if polish else "local-only")
                if backend == ASR_BACKEND_LOCAL
                else "remote-asr-raw"
            ),
        )
        _atomic_write_text(raw_path, raw_markdown)
        if polish:
            fallback_markdown = _render_markdown(
                source_path,
                payload,
                transcript,
                polished=False,
                privacy=(
                    "local-raw-fallback"
                    if backend == ASR_BACKEND_LOCAL
                    else "remote-asr-raw-fallback"
                ),
            )
            _atomic_write_text(final_path, fallback_markdown)

        response = {
            "ok": True,
            "status": "completed",
            "backend": backend,
            "output_path": final_path,
            "raw_transcript_path": raw_path,
            "suggested_output_path": final_path if polish else "",
            "model": payload.get("model") or effective_model,
            "duration_seconds": round(float(payload.get("duration") or 0.0), 3),
            "speaker_count": int(payload.get("speaker_count") or 0),
            "diarized": bool(payload.get("diarized")),
            "ai_polish_requested": polish,
            "warnings": payload.get("warnings") if isinstance(payload.get("warnings"), list) else [],
            "asr_chunk_count": int(payload.get("asr_chunk_count") or 0),
            "decoded_chunk_count": int(payload.get("decoded_chunk_count") or 0),
        }
        if polish:
            response.update({
                "transcript": transcript,
                "language": payload.get("lang") or "",
                "emotion": payload.get("emotion") or "",
                "event": payload.get("event") or "",
                "privacy_notice": (
                    "原始转录已按用户选择提供给当前模型，用于 AI 润色。"
                    if backend == ASR_BACKEND_LOCAL
                    else "音频已发送到用户配置的远程语音接口；原始转录已按用户选择"
                    "提供给当前对话模型，用于 AI 润色。"
                ),
            })
        else:
            response["privacy_notice"] = (
                "音频内容和转录正文均由本地模型处理并写入工作区；"
                "正文未返回给当前大模型。当前模型只收到路径和状态元数据。"
                if backend == ASR_BACKEND_LOCAL
                else "音频已发送到用户配置的远程语音接口；转录正文只写入工作区，"
                "未返回给当前对话模型。当前模型只收到路径和状态元数据。"
            )
        _emit_diagnostic(
            context,
            "finish",
            started_at,
            backend=backend,
            model=str(response["model"]),
            speaker_count=response["speaker_count"],
            diarized=response["diarized"],
            polish=polish,
        )
        return _json(response)
    except ComponentNotReadyError as exc:
        _emit_diagnostic(context, "error", started_at, error_type=type(exc).__name__)
        return _json(_error(
            "component_not_ready",
            str(exc),
            "打开“设置 → 组件与依赖”，安装或修复“语音转文字组件”后重试。",
        ))
    except InterruptedError as exc:
        _emit_diagnostic(context, "finish", started_at, outcome="aborted")
        return _json(_error("aborted", str(exc), "可重新发起转录。"))
    except RemoteTranscriptionError as exc:
        _emit_diagnostic(context, "error", started_at, backend=ASR_BACKEND_OPENAI_COMPATIBLE, error_type=type(exc).__name__)
        return _json(_error(
            exc.code,
            str(exc),
            "检查语音转文字能力中的接口地址、模型名称和 API Key 后重试。",
        ))
    except subprocess.TimeoutExpired:
        _emit_diagnostic(context, "error", started_at, error_type="TimeoutExpired")
        return _json(_error(
            "transcription_timeout",
            "语音转文字任务超时。",
            "可提高 timeout_seconds 后重试，或检查远程接口与本地组件状态。",
        ))
    except Exception as exc:
        _emit_diagnostic(context, "error", started_at, error_type=type(exc).__name__)
        return _json(_error(
            "transcription_failed",
            str(exc),
            (
                "检查音频文件与“设置 → 组件与依赖 → 语音转文字组件”的状态后重试。"
                if backend != ASR_BACKEND_OPENAI_COMPATIBLE
                else "检查音频文件和语音转文字能力中的远程接口配置后重试。"
            ),
        ))


def _read_markdown_parts(path):
    with open(path, "r", encoding="utf-8") as stream:
        content = stream.read()
    match = re.match(r"\A---\n(.*?)\n---\n\n?(.*)\Z", content, re.DOTALL)
    if not match:
        raise ValueError("原始转录文件格式无效。")
    return match.group(1), match.group(2).strip()


def _updated_frontmatter(frontmatter, polished):
    fallback_privacy = (
        "remote-asr-raw-fallback"
        if "remote-asr" in frontmatter
        else "local-raw-fallback"
    )
    lines = []
    found_polished = False
    found_privacy = False
    for line in frontmatter.splitlines():
        if line.startswith("ai_polished:"):
            lines.append(f"ai_polished: {'true' if polished else 'false'}")
            found_polished = True
        elif line.startswith("privacy:"):
            lines.append(f"privacy: {_frontmatter_value('ai-polished' if polished else fallback_privacy)}")
            found_privacy = True
        else:
            lines.append(line)
    if not found_polished:
        lines.append(f"ai_polished: {'true' if polished else 'false'}")
    if not found_privacy:
        lines.append(f"privacy: {_frontmatter_value('ai-polished' if polished else fallback_privacy)}")
    return "\n".join(lines)


def save_transcript_result(
    raw_transcript_path,
    polished,
    polished_text="",
    output_path="",
    overwrite=False,
    workspace_dir=None,
    _context=None,
):
    """Save an AI-polished transcript, or promote the exact local raw transcript on polish failure."""
    started_at = time.time()
    context = _context if isinstance(_context, dict) else {}
    try:
        _emit_diagnostic(context, "submit", started_at, operation="save_transcript_result")
        if not isinstance(polished, bool):
            raise ValueError("polished 必须是布尔值。")
        workspace_root = os.path.abspath(str(workspace_dir or "")) if workspace_dir else ""
        if not workspace_root or not os.path.isdir(workspace_root):
            raise ValueError("当前工作区不可用。")
        raw_path = os.path.abspath(
            raw_transcript_path
            if os.path.isabs(str(raw_transcript_path or ""))
            else os.path.join(workspace_root, str(raw_transcript_path or ""))
        )
        if not _is_within(raw_path, workspace_root) or not os.path.isfile(raw_path):
            raise PermissionError("原始转录文件必须存在于当前工作区内。")
        default_output = re.sub(r"\.raw\.md$", ".md", raw_path, flags=re.IGNORECASE)
        if default_output == raw_path:
            raise ValueError("原始转录文件必须是由 transcribe_audio 生成的 .raw.md 文件。")
        _emit_diagnostic(context, "start", started_at, operation="save_transcript_result")
        final_path = _resolve_workspace_markdown(output_path, workspace_root, default_output)
        frontmatter, raw_body = _read_markdown_parts(raw_path)
        generated_fallback = False
        if os.path.isfile(final_path) and os.path.normcase(final_path) == os.path.normcase(default_output):
            try:
                fallback_frontmatter, fallback_body = _read_markdown_parts(final_path)
                generated_fallback = (
                    fallback_body == raw_body
                    and any(
                        f'privacy: "{value}"' in fallback_frontmatter
                        for value in ("local-raw-fallback", "remote-asr-raw-fallback")
                    )
                    and "ai_polished: false" in fallback_frontmatter
                )
            except (OSError, ValueError):
                generated_fallback = False
        if not generated_fallback:
            _check_write_target(final_path, bool(overwrite))
        if polished:
            body = str(polished_text or "").strip()
            if not body:
                raise ValueError("polished=true 时 polished_text 不能为空。")
        else:
            body = raw_body
        _emit_diagnostic(context, "run", started_at, operation="save_transcript_result")
        final_frontmatter = _updated_frontmatter(frontmatter, polished)
        _atomic_write_text(final_path, f"---\n{final_frontmatter}\n---\n\n{body}\n")
        _emit_diagnostic(
            context,
            "finish",
            started_at,
            operation="save_transcript_result",
            polished=polished,
        )
        return _json({
            "ok": True,
            "status": "completed",
            "output_path": final_path,
            "raw_transcript_path": raw_path,
            "ai_polished": polished,
            "message": "AI 润色稿已写入工作区。" if polished else "润色未完成，已将本地原始稿作为最终结果。",
        })
    except Exception as exc:
        _emit_diagnostic(context, "error", started_at, operation="save_transcript_result", error_type=type(exc).__name__)
        return _json(_error(
            "save_transcript_failed",
            str(exc),
            "确认输出路径位于工作区；如需覆盖已有文件，请先获得用户同意。",
        ))


TOOL_EXPORTS = [
    {
        "name": "transcribe_audio",
        "handler": transcribe_audio,
        "description": (
            "Transcribe an authorized audio/video file with the configured local or OpenAI-compatible backend and write Markdown. "
            "The required polish boolean must reflect the user's explicit privacy choice."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Optional workspace-relative path or exact attachment path. Omit it to use the single local audio attachment from the current user turn."
                },
                "polish": {
                    "type": "boolean",
                    "description": "Required explicit user choice. False keeps transcript text out of model context; true returns it for current-model polishing."
                },
                "diarize": {"type": "boolean", "description": "Local backend only. Omit for the configured backend default; explicit true is rejected by the remote backend."},
                "speaker_count": {"type": "integer", "minimum": 0, "maximum": 20, "description": "Local backend only: known number of speakers, or 0 for automatic estimation."},
                "model": {"type": "string", "enum": ["sensevoice"], "description": "Optional local-backend compatibility parameter. Omit for the remote backend, whose model is configured in Skill settings."},
                "language": {"type": "string", "enum": ["auto", "zh", "en", "ja", "ko", "yue"], "description": "Language hint for SenseVoice."},
                "output_path": {"type": "string", "description": "Optional workspace-relative Markdown output path."},
                "overwrite": {"type": "boolean", "description": "Overwrite existing output only after explicit user approval."},
                "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 3600}
            },
            "required": ["polish"]
        },
        "destructive": False,
        "read_only": False,
        "search_hint": "speech to text ASR OpenAI audio transcriptions local meeting speaker diarization 语音转文字 远程接口 转录 说话人分离",
        "result_format": "json",
        "metadata": {"idempotent": True, "requires_workspace": True}
    },
    {
        "name": "save_transcript_result",
        "handler": save_transcript_result,
        "description": "Save the current model's polished transcript, or promote the exact local raw transcript when polishing fails.",
        "parameters": {
            "type": "object",
            "properties": {
                "raw_transcript_path": {"type": "string", "description": "Raw .raw.md path returned by transcribe_audio."},
                "polished": {"type": "boolean", "description": "True for AI-polished text; false to use the exact local raw body."},
                "polished_text": {"type": "string", "description": "Required only when polished is true."},
                "output_path": {"type": "string", "description": "Optional final Markdown path inside the workspace."},
                "overwrite": {"type": "boolean", "description": "Overwrite only after explicit user approval."}
            },
            "required": ["raw_transcript_path", "polished"]
        },
        "destructive": False,
        "read_only": False,
        "search_hint": "save polished transcript raw fallback markdown workspace",
        "result_format": "json",
        "metadata": {"idempotent": True, "requires_workspace": True}
    }
]
