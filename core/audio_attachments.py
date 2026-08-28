import os


AUDIO_ATTACHMENT_EXTENSIONS = frozenset({
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".mp4",
    ".webm",
})


def is_audio_attachment(path):
    return os.path.splitext(str(path or ""))[1].strip().lower() in AUDIO_ATTACHMENT_EXTENSIONS


def partition_model_visible_attachments(paths, *, keep_audio_local):
    normalized = list(paths or [])
    if not keep_audio_local:
        return normalized, []
    local_audio = [path for path in normalized if is_audio_attachment(path)]
    local_keys = {os.path.normcase(os.path.normpath(path)) for path in local_audio}
    model_visible = [
        path
        for path in normalized
        if os.path.normcase(os.path.normpath(path)) not in local_keys
    ]
    return model_visible, local_audio
