"""Submission snapshots using the existing desktop conversation conventions.

System prompts, tool schemas, and provider history projection are unchanged.
Only the newly accepted message and its existing run-context fields are built.
"""
import mimetypes
import os
from pathlib import Path
import re

from core.audio_attachments import partition_model_visible_attachments
from core.clarify_mode import normalize_run_context, normalize_selected_skill_names
from core.conversation_actions import append_reference_context
from core.conversation_agents import agent_role_context, builtin_agent_profiles


def is_image(path):
    return mimetypes.guess_type(str(path))[0] in {"image/png", "image/jpeg", "image/webp", "image/gif"}


def referenced_images(text, workspace, existing):
    root = Path(workspace).resolve()
    candidates = [match.group("path").strip("()[]{}<>\"'`") for match in re.finditer(
        r"(?P<path>[^\s\"'，。；;：:]+?\.(?:png|jpg|jpeg|webp|gif))", text, re.IGNORECASE)]
    result, seen = [], {os.path.normcase(str(Path(path).resolve())) for path in existing}
    def add(path):
        path = Path(path).resolve()
        key = os.path.normcase(str(path))
        if key not in seen and path.is_relative_to(root) and path.is_file() and is_image(path):
            seen.add(key)
            result.append(str(path))
    for candidate in candidates:
        add(root / candidate)
    names = {Path(candidate).name for candidate in candidates if not Path(candidate).is_absolute()}
    if names:
        for index, (folder, _dirs, files) in enumerate(os.walk(root)):
            if index >= 800:
                break
            for name in names.intersection(files):
                add(Path(folder) / name)
    return result


def user_payload(text, paths, *, workspace, supports_vision=False, keep_audio_local=False,
                 audio_transcription_backend="local", references=(), branch_file_map=None):
    text = str(text or "").strip()
    paths = list(dict.fromkeys(os.path.normpath(str(path)) for path in paths))
    if any(not Path(path).is_file() for path in paths):
        raise ValueError("附件已不存在，请恢复文件或重新添加。输入内容已保留。")
    images_from_text = referenced_images(text, workspace, paths)
    paths.extend(images_from_text)
    images = [path for path in paths if is_image(path)]
    if images and not supports_vision:
        raise ValueError("当前模型未配置图片理解。图片已保留，请切换到支持图片的模型后再发送。")
    visible, local_audio = partition_model_visible_attachments(paths, keep_audio_local=keep_audio_local)
    blocks = []
    if visible:
        lines = ["[用户添加的文件]", "以下文件由用户通过“添加文件”手动附加，请优先视为本轮任务的重要输入："]
        for path in visible:
            lines.extend([f"- 文件名: {os.path.basename(path) or path}", f"  路径: {path}"])
        blocks.append("\n".join(lines))
    remote_audio = audio_transcription_backend == "openai_compatible"
    if local_audio:
        blocks.append("[语音转文字专用附件]\n本轮音频附件仅授权给用户已配置的语音转文字服务；未向当前对话模型提供文件名、路径、大小或音频内容。" if remote_audio else
                      "[本地音频附件]\n本轮音频附件仅授权给本地语音转文字工具；未向当前模型提供文件名、路径、大小或音频内容。")
    if text:
        blocks.append(text)
    parts = [{"type": "text", "text": text}] if text else []
    for path in [path for path in visible if path in images] + [path for path in visible if path not in images]:
        parts.append({"type": "input_image" if path in images else "input_file", "path": path,
                      "name": os.path.basename(path) or path, "source": "user_added"})
    meta = {"display_content": text} if text else {}
    if paths:
        meta["user_added_files"] = paths
    if images:
        meta.update(user_added_images=images, vision_requested=bool(supports_vision))
    if images_from_text:
        meta["workspace_referenced_images"] = images_from_text
    if local_audio:
        prefix = "transcription_only_audio" if remote_audio else "local_only_audio"
        meta[prefix + "_files"] = local_audio
        meta[prefix + "_count"] = len(local_audio)
        if remote_audio:
            meta["audio_transcription_backend"] = "openai_compatible"
    return append_reference_context({"content": "\n\n".join(blocks).strip(), "display_content": text,
        "attachments": [{"path": path, "name": os.path.basename(path)} for path in paths],
        "content_parts": parts or None, "meta": meta or None}, references, branch_file_map)


def run_context(service, cid, payload, profile):
    from core.long_context import desktop_policy
    from core.knowledge_sources import MultiSourceKnowledgeService
    meta = service.storage.get_conversation_meta(cid) or {}
    preferences = payload.get("preferences") or meta
    selection = preferences.get("conversation_agent")
    profiles = service.config.get_agent_profiles()
    profiles += builtin_agent_profiles(profiles)
    expert = next((item for item in profiles if item["id"] == (selection or {}).get("id")), None)
    agent = agent_role_context(selection, expert, workspace=str(service.workspace(cid)), message_id=payload["message_id"])
    skills = normalize_selected_skill_names(payload.get("skills", preferences.get("selected_skill_names", [])))
    if expert and expert.get("skill_names"):
        skills = normalize_selected_skill_names(skills + expert["skill_names"])
        agent["allowed_skill_names"] = skills
    binding = service.storage.get_team_space_binding(cid)
    if meta.get("team_space_required") and not binding:
        raise ValueError("空间会话归属不完整。历史已保留，请恢复原绑定后继续。")
    context = {**agent, "mode": preferences.get("mode", "execution"),
        "selected_skill_names": skills, "selected_model_id": profile["id"],
        "reasoning_effort": preferences.get("reasoning_effort", profile.get("reasoning_effort", "")),
        "workspace_mode": "project", "app_variable_access": True,
        "desktop_long_context": desktop_policy(service.config),
        "knowledge_context": MultiSourceKnowledgeService().snapshot(preferences.get("knowledge_refs"), cid),
        **({"team_space": binding} if binding else {})}
    for key in ("clarify_mode_state", "pending_clarify_questions", "grill_round_count", "grill_cycle_count",
                "grill_execution_confirmed", "workflow_mode", "office_output_profile", "office_conversion_target",
                "office_source_files", "office_template_file", "ppt_agent_mode", "ppt_agent_strategy",
                "ppt_agent_selected_strategy", "ppt_agent_preference", "ppt_agent_template_file", "ppt_agent_output_format"):
        if key in preferences:
            context[key] = preferences[key]
    return normalize_run_context(context)
