"""Single source of truth for workspace file preview and editing capabilities."""

from __future__ import annotations

import os
from dataclasses import dataclass


MIB = 1024 * 1024
TEXT_FILE_MAX_BYTES = 10 * MIB
OFFICE_FILE_MAX_BYTES = 25 * MIB


@dataclass(frozen=True)
class FileCapability:
    extension: str
    preview_kind: str
    label: str
    icon: str
    editor_kind: str = ""
    editor_label: str = ""
    max_bytes: int = 0
    web_based: bool = False
    visual: bool = False
    office_family: str = ""

    @property
    def editable(self) -> bool:
        return bool(self.editor_kind)


def _capability(
    extension: str,
    preview_kind: str,
    label: str,
    icon: str,
    *,
    editor_kind: str = "",
    editor_label: str = "",
    max_bytes: int = 0,
    web_based: bool = False,
    visual: bool = False,
    office_family: str = "",
) -> FileCapability:
    return FileCapability(
        extension=extension,
        preview_kind=preview_kind,
        label=label,
        icon=icon,
        editor_kind=editor_kind,
        editor_label=editor_label,
        max_bytes=max_bytes,
        web_based=web_based,
        visual=visual,
        office_family=office_family,
    )


_TEXT_SOURCE_EXTENSIONS = (
    ".py",
    ".pyw",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".less",
    ".vue",
    ".svelte",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".psm1",
    ".bat",
    ".cmd",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".properties",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".hpp",
    ".cs",
    ".php",
    ".rb",
    ".swift",
    ".dart",
    ".lua",
    ".r",
    ".jsonl",
    ".ndjson",
)


_CAPABILITIES = [
    _capability(
        ".html", "html", "HTML", "fa5s.file-code",
        editor_kind="html", editor_label="HTML", max_bytes=TEXT_FILE_MAX_BYTES,
        web_based=True, visual=True,
    ),
    _capability(
        ".htm", "html", "HTML", "fa5s.file-code",
        editor_kind="html", editor_label="HTML", max_bytes=TEXT_FILE_MAX_BYTES,
        web_based=True, visual=True,
    ),
    _capability(
        ".md", "markdown", "Markdown", "fa5s.file-alt",
        editor_kind="text", editor_label="文本", max_bytes=TEXT_FILE_MAX_BYTES,
    ),
    _capability(
        ".markdown", "markdown", "Markdown", "fa5s.file-alt",
        editor_kind="text", editor_label="文本", max_bytes=TEXT_FILE_MAX_BYTES,
    ),
    _capability(
        ".txt", "text", "TXT", "fa5s.file-alt",
        editor_kind="text", editor_label="文本", max_bytes=TEXT_FILE_MAX_BYTES,
    ),
    _capability(
        ".json", "text", "JSON", "fa5s.file-code",
        editor_kind="text", editor_label="文本", max_bytes=TEXT_FILE_MAX_BYTES,
    ),
    _capability(
        ".xml", "text", "XML", "fa5s.file-code",
        editor_kind="text", editor_label="文本", max_bytes=TEXT_FILE_MAX_BYTES,
    ),
    _capability(
        ".yaml", "text", "YAML", "fa5s.file-code",
        editor_kind="text", editor_label="文本", max_bytes=TEXT_FILE_MAX_BYTES,
    ),
    _capability(
        ".yml", "text", "YAML", "fa5s.file-code",
        editor_kind="text", editor_label="文本", max_bytes=TEXT_FILE_MAX_BYTES,
    ),
    _capability(
        ".log", "text", "LOG", "fa5s.file-alt",
        editor_kind="text", editor_label="文本", max_bytes=TEXT_FILE_MAX_BYTES,
    ),
    _capability(
        ".csv", "table", "CSV", "fa5s.file-csv",
        editor_kind="sheet", editor_label="表格", max_bytes=TEXT_FILE_MAX_BYTES,
        web_based=True,
    ),
    _capability(
        ".tsv", "table", "TSV", "fa5s.file-csv",
        editor_kind="sheet", editor_label="表格", max_bytes=TEXT_FILE_MAX_BYTES,
        web_based=True,
    ),
]

_CAPABILITIES.extend(
    _capability(
        extension,
        "text",
        extension[1:].upper(),
        "fa5s.file-code",
        editor_kind="text",
        editor_label="文本",
        max_bytes=TEXT_FILE_MAX_BYTES,
    )
    for extension in _TEXT_SOURCE_EXTENSIONS
)

_CAPABILITIES.extend(
    (
        _capability(".png", "image", "图片", "fa5s.file-image"),
        _capability(".jpg", "image", "图片", "fa5s.file-image"),
        _capability(".jpeg", "image", "图片", "fa5s.file-image"),
        _capability(".gif", "image", "图片", "fa5s.file-image"),
        _capability(".webp", "image", "图片", "fa5s.file-image"),
        _capability(".bmp", "image", "图片", "fa5s.file-image"),
        _capability(".svg", "image", "SVG", "fa5s.file-image"),
        _capability(".pdf", "pdf", "PDF", "fa5s.file-pdf"),
        _capability(
            ".doc", "doc", "DOC", "fa5s.file-word", office_family="word"
        ),
        _capability(
            ".docx", "docx", "DOCX", "fa5s.file-word",
            editor_kind="docx", editor_label="DOCX", max_bytes=OFFICE_FILE_MAX_BYTES,
            web_based=True, visual=True, office_family="word",
        ),
        _capability(
            ".ppt", "ppt", "PPT", "fa5s.file-powerpoint", office_family="powerpoint"
        ),
        _capability(
            ".pptx", "pptx", "PPTX", "fa5s.file-powerpoint", office_family="powerpoint"
        ),
        _capability(
            ".xls", "xls", "XLS", "fa5s.file-excel", office_family="excel"
        ),
        _capability(
            ".xlsx", "xlsx", "XLSX", "fa5s.file-excel",
            editor_kind="sheet", editor_label="XLSX", max_bytes=OFFICE_FILE_MAX_BYTES,
            web_based=True, office_family="excel",
        ),
    )
)


FILE_CAPABILITIES = {item.extension: item for item in _CAPABILITIES}
if len(FILE_CAPABILITIES) != len(_CAPABILITIES):
    raise RuntimeError("文件能力注册表包含重复扩展名。")


def file_capability(path_or_extension: str) -> FileCapability | None:
    raw = str(path_or_extension or "").strip()
    extension = raw.lower() if raw.startswith(".") else os.path.splitext(raw)[1].lower()
    return FILE_CAPABILITIES.get(extension)


def editable_extensions() -> frozenset[str]:
    return frozenset(
        extension
        for extension, capability in FILE_CAPABILITIES.items()
        if capability.editable
    )


def editor_extensions(kind: str) -> tuple[str, ...]:
    return tuple(
        extension
        for extension, capability in FILE_CAPABILITIES.items()
        if capability.editor_kind == kind
    )
