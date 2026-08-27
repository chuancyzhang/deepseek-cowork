import json
import os
import re
import tarfile
import tempfile
import threading
import time
import urllib.request
from datetime import date

from PySide6.QtCore import QObject, Qt

from core.env_utils import get_app_data_dir
from core.sandbox_runtime import run_skill_script_in_sandbox


SKILL_ID = "speech-to-text"
SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "transcribe.mjs")
SUPPORTED_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".webm"
}
MODELS_VERSION = "v1"
SEGMENTATION_ARCHIVE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
SEGMENTATION_ARCHIVE_SIZE = 6_958_444
EMBEDDING_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)
EMBEDDING_MODEL_SIZE = 39_593_761
_MODEL_LOCK = threading.RLock()


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
    paths = set()
    if not isinstance(context, dict):
        return paths
    for message in context.get("current_messages_snapshot") or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        for value in meta.get("user_added_files") or []:
            if value:
                paths.add(os.path.normcase(os.path.abspath(str(value))))
        for part in message.get("content_parts") or []:
            if not isinstance(part, dict) or part.get("type") not in {"input_file", "input_audio"}:
                continue
            value = part.get("path")
            if value:
                paths.add(os.path.normcase(os.path.abspath(str(value))))
    return paths


def _resolve_audio_path(audio_path, workspace_dir, context):
    workspace_root = os.path.abspath(str(workspace_dir or "")) if workspace_dir else ""
    if not workspace_root or not os.path.isdir(workspace_root):
        raise ValueError("当前工作区不可用。")
    raw = os.path.expandvars(os.path.expanduser(str(audio_path or "").strip()))
    if not raw:
        raise ValueError("audio_path 不能为空。")
    resolved = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(workspace_root, raw))
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"音频文件不存在：{resolved}")
    if os.path.splitext(resolved)[1].lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("不支持该文件格式。支持 WAV、MP3、M4A、AAC、FLAC、OGG、Opus、MP4 和 WebM。")
    key = os.path.normcase(resolved)
    if not _is_within(resolved, workspace_root) and key not in _attachment_paths(context):
        raise PermissionError("只能读取当前工作区文件或本会话中用户明确附加的文件。")
    return workspace_root, resolved


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


def _model_paths():
    root = os.path.join(get_app_data_dir(), "speech-to-text", "models", MODELS_VERSION)
    return {
        "root": root,
        "segmentation": os.path.join(root, "segmentation", "model.onnx"),
        "embedding": os.path.join(root, "embedding", "3dspeaker.onnx"),
    }


def _raise_if_aborted(abort_state):
    if isinstance(abort_state, dict) and abort_state.get("aborted"):
        raise InterruptedError("用户已停止语音转文字任务。")


def _download_to_temp(url, expected_size, directory, abort_state):
    _raise_if_aborted(abort_state)
    os.makedirs(directory, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(prefix=".model-download-", suffix=".tmp", dir=directory)
    os.close(handle)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "DeepSeekCowork/speech-to-text"})
        with urllib.request.urlopen(request, timeout=120) as response, open(temp_path, "wb") as output:
            while True:
                _raise_if_aborted(abort_state)
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        actual_size = os.path.getsize(temp_path)
        if actual_size != expected_size:
            raise RuntimeError(f"模型下载大小不匹配：期望 {expected_size}，实际 {actual_size}")
        return temp_path
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _install_segmentation_model(target, context, abort_state):
    _emit_step(context, "首次使用说话人分离：正在下载本地分段模型（约 7 MB）…")
    archive_path = _download_to_temp(
        SEGMENTATION_ARCHIVE_URL,
        SEGMENTATION_ARCHIVE_SIZE,
        os.path.dirname(target),
        abort_state,
    )
    extracted_temp = ""
    try:
        with tarfile.open(archive_path, mode="r:bz2") as archive:
            candidates = [
                member for member in archive.getmembers()
                if member.isfile() and member.name.replace("\\", "/").endswith("/model.onnx")
            ]
            if len(candidates) != 1:
                raise RuntimeError("说话人分段模型归档结构无效。")
            source = archive.extractfile(candidates[0])
            if source is None:
                raise RuntimeError("无法读取说话人分段模型。")
            handle, extracted_temp = tempfile.mkstemp(
                prefix=".segmentation-", suffix=".onnx", dir=os.path.dirname(target)
            )
            with os.fdopen(handle, "wb") as output:
                while True:
                    _raise_if_aborted(abort_state)
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            if os.path.getsize(extracted_temp) < 1024 * 1024:
                raise RuntimeError("说话人分段模型文件异常。")
            os.replace(extracted_temp, target)
            extracted_temp = ""
    finally:
        for path in (archive_path, extracted_temp):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _install_embedding_model(target, context, abort_state):
    _emit_step(context, "首次使用说话人分离：正在下载本地声纹嵌入模型（约 40 MB）…")
    temp_path = _download_to_temp(
        EMBEDDING_MODEL_URL,
        EMBEDDING_MODEL_SIZE,
        os.path.dirname(target),
        abort_state,
    )
    try:
        os.replace(temp_path, target)
        temp_path = ""
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _ensure_diarization_models(context, abort_state):
    paths = _model_paths()
    with _MODEL_LOCK:
        _raise_if_aborted(abort_state)
        os.makedirs(os.path.dirname(paths["segmentation"]), exist_ok=True)
        os.makedirs(os.path.dirname(paths["embedding"]), exist_ok=True)
        if not os.path.isfile(paths["segmentation"]):
            _install_segmentation_model(paths["segmentation"], context, abort_state)
        if not os.path.isfile(paths["embedding"]):
            _install_embedding_model(paths["embedding"], context, abort_state)
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
    metadata = {
        "source": os.path.basename(source_path),
        "date": date.today().isoformat(),
        "asr_model": payload.get("model") or "",
        "duration_seconds": round(float(payload.get("duration") or 0.0), 3),
        "language": payload.get("lang") or "",
        "speaker_count": int(payload.get("speaker_count") or 0),
        "speaker_diarization": bool(payload.get("diarized")),
        "ai_polished": bool(polished),
        "privacy": privacy,
    }
    header = "\n".join(f"{key}: {_frontmatter_value(value)}" for key, value in metadata.items())
    return f"---\n{header}\n---\n\n{str(transcript or '').strip()}\n"


def _parse_json_stdout(stdout):
    text = str(stdout or "").strip()
    if not text:
        raise RuntimeError("本地转录脚本没有返回结果。")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"本地转录脚本返回了无效 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("本地转录脚本结果必须是 JSON 对象。")
    return payload


def transcribe_audio(
    audio_path,
    polish,
    diarize=True,
    speaker_count=0,
    model="sensevoice",
    language="auto",
    output_path="",
    overwrite=False,
    timeout_seconds=1800,
    workspace_dir=None,
    _context=None,
):
    """Transcribe one user-authorized local audio file and write a Markdown transcript."""
    started_at = time.time()
    context = _context if isinstance(_context, dict) else {}
    try:
        if not isinstance(polish, bool):
            raise ValueError("polish 必须是布尔值，并且必须来自用户对是否 AI 润色的明确选择。")
        model = str(model or "sensevoice").strip().lower()
        if model not in {"sensevoice", "whisper"}:
            raise ValueError("model 只能是 sensevoice 或 whisper。")
        language = str(language or "auto").strip().lower()
        if language not in {"auto", "zh", "en", "ja", "ko", "yue"}:
            raise ValueError("language 只能是 auto、zh、en、ja、ko 或 yue。")
        if model == "whisper" and language not in {"auto", "en"}:
            raise ValueError("Whisper tiny.en 只支持英文；language 必须是 auto 或 en。")
        speaker_count = int(speaker_count or 0)
        if speaker_count < 0 or speaker_count > 20:
            raise ValueError("speaker_count 必须是 0（自动估算）或 1 到 20。")
        timeout_seconds = max(30, min(int(timeout_seconds or 1800), 3600))
        workspace_root, source_path = _resolve_audio_path(audio_path, workspace_dir, context)
        final_path = _resolve_workspace_markdown(
            output_path,
            workspace_root,
            f"{_safe_stem(source_path)}-transcript.md",
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
            model=model,
            diarize=bool(diarize),
            polish=polish,
        )
        abort_state = _init_abort_state(context)
        _raise_if_aborted(abort_state)
        _emit_diagnostic(context, "start", started_at, operation="local_transcription_pipeline")
        model_paths = {"segmentation": "", "embedding": ""}
        if diarize:
            model_paths = _ensure_diarization_models(context, abort_state)

        args = [
            "--input", source_path,
            "--model", model,
            "--language", language,
            "--diarize", "true" if diarize else "false",
            "--speaker-count", str(speaker_count),
        ]
        if diarize:
            args.extend([
                "--segmentation-model", model_paths["segmentation"],
                "--embedding-model", model_paths["embedding"],
            ])
        _emit_step(context, "正在本地识别音频并分离说话人…" if diarize else "正在本地识别音频…")
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
            _emit_diagnostic(context, "finish", started_at, outcome="aborted")
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
        transcript = str(payload.get("transcript") or "").strip()
        if not transcript:
            raise RuntimeError("本地模型没有识别出可用文字。")
        raw_markdown = _render_markdown(
            source_path,
            payload,
            transcript,
            polished=False,
            privacy="local-raw" if polish else "local-only",
        )
        _atomic_write_text(raw_path, raw_markdown)

        response = {
            "ok": True,
            "status": "completed",
            "output_path": final_path if not polish else "",
            "raw_transcript_path": raw_path,
            "suggested_output_path": final_path if polish else "",
            "model": payload.get("model") or model,
            "duration_seconds": round(float(payload.get("duration") or 0.0), 3),
            "speaker_count": int(payload.get("speaker_count") or 0),
            "diarized": bool(payload.get("diarized")),
            "ai_polish_requested": polish,
        }
        if polish:
            response.update({
                "transcript": transcript,
                "language": payload.get("lang") or "",
                "emotion": payload.get("emotion") or "",
                "event": payload.get("event") or "",
                "privacy_notice": "原始转录已按用户选择提供给当前模型，用于 AI 润色。",
            })
        else:
            response["privacy_notice"] = (
                "音频内容和转录正文均由本地模型处理并写入工作区；"
                "正文未返回给当前大模型。当前模型只收到路径和状态元数据。"
            )
        _emit_diagnostic(
            context,
            "finish",
            started_at,
            model=str(response["model"]),
            speaker_count=response["speaker_count"],
            diarized=response["diarized"],
            polish=polish,
        )
        return _json(response)
    except InterruptedError as exc:
        _emit_diagnostic(context, "finish", started_at, outcome="aborted")
        return _json(_error("aborted", str(exc), "可重新发起转录。"))
    except Exception as exc:
        _emit_diagnostic(context, "error", started_at, error_type=type(exc).__name__)
        return _json(_error(
            "transcription_failed",
            str(exc),
            "检查文件、网络和 Skill 依赖状态后重试；依赖失败可在 AI 能力商城使用“重试依赖”。",
        ))


def _read_markdown_parts(path):
    with open(path, "r", encoding="utf-8") as stream:
        content = stream.read()
    match = re.match(r"\A---\n(.*?)\n---\n\n?(.*)\Z", content, re.DOTALL)
    if not match:
        raise ValueError("原始转录文件格式无效。")
    return match.group(1), match.group(2).strip()


def _updated_frontmatter(frontmatter, polished):
    lines = []
    found_polished = False
    found_privacy = False
    for line in frontmatter.splitlines():
        if line.startswith("ai_polished:"):
            lines.append(f"ai_polished: {'true' if polished else 'false'}")
            found_polished = True
        elif line.startswith("privacy:"):
            lines.append(f"privacy: {_frontmatter_value('ai-polished' if polished else 'local-raw-fallback')}")
            found_privacy = True
        else:
            lines.append(line)
    if not found_polished:
        lines.append(f"ai_polished: {'true' if polished else 'false'}")
    if not found_privacy:
        lines.append(f"privacy: {_frontmatter_value('ai-polished' if polished else 'local-raw-fallback')}")
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
        _check_write_target(final_path, bool(overwrite))
        frontmatter, raw_body = _read_markdown_parts(raw_path)
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
            "Locally transcribe an authorized audio/video file, optionally separate speakers, and write Markdown. "
            "The required polish boolean must reflect the user's explicit privacy choice."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string", "description": "Workspace-relative path or exact user attachment path."},
                "polish": {
                    "type": "boolean",
                    "description": "Required explicit user choice. False keeps transcript text out of model context; true returns it for current-model polishing."
                },
                "diarize": {"type": "boolean", "description": "Separate speakers. Defaults to true."},
                "speaker_count": {"type": "integer", "minimum": 0, "maximum": 20, "description": "Known number of speakers, or 0 for automatic estimation."},
                "model": {"type": "string", "enum": ["sensevoice", "whisper"], "description": "Local ASR model. Defaults to sensevoice."},
                "language": {"type": "string", "enum": ["auto", "zh", "en", "ja", "ko", "yue"], "description": "Language hint for SenseVoice."},
                "output_path": {"type": "string", "description": "Optional workspace-relative Markdown output path."},
                "overwrite": {"type": "boolean", "description": "Overwrite existing output only after explicit user approval."},
                "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 3600}
            },
            "required": ["audio_path", "polish"]
        },
        "destructive": False,
        "read_only": False,
        "search_hint": "speech to text ASR transcribe audio meeting speaker diarization 语音转文字 转录 说话人分离",
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
