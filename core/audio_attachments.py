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
