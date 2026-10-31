"""Markdown writing pages: explicit creation, conflict-safe saves and recovery data."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from core.deliverable_editing import DeliverableEditError, TEXT_MAX_BYTES
from core.file_write_locks import file_write_lock

PAGE_MARKER = "<!-- cowork:writing-page:v1 -->"


def page_markdown(title, body):
    title = str(title or "").replace("\n", " ").strip()
    title = re.sub(r'([\\`*_\[\]<>])', r'\\\1', title)
    # The marker makes reopening unambiguous without changing ordinary Markdown.
    return PAGE_MARKER + "\n\n# " + (title or "无标题页") + "\n\n" + str(body or "").rstrip() + "\n"


def read_page(path):
    with open(path, "rb") as stream:
        if stream.read(len(PAGE_MARKER.encode("utf-8"))) != PAGE_MARKER.encode("utf-8"):
            return None
    if os.path.getsize(path) > TEXT_MAX_BYTES:
        raise DeliverableEditError("file_too_large", "页面超过编辑大小上限，请使用源码编辑。")
    text = Path(path).read_text(encoding="utf-8")
    rest = text[len(PAGE_MARKER):].lstrip("\n")
    title, _, body = rest.partition("\n")
    title = re.sub(r'\\([\\`*_\[\]<>])', r'\1', title.removeprefix("# "))
    return {"title": title, "body": body.lstrip("\n")}


def page_target(workspace, name):
    name = str(name or "").strip()
    if not name or name in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")):
        raise DeliverableEditError("invalid_filename", "文件名不能包含路径或特殊字符，请换一个名称。")
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
        raise DeliverableEditError("invalid_filename", "这个名称由 Windows 保留，请换一个名称。")
    if not name.lower().endswith(".md"):
        name += ".md"
    if not os.path.isdir(workspace):
        raise DeliverableEditError("workspace_unavailable", "页面所属工作目录不可用，请恢复目录后重试。")
    return os.path.join(os.path.abspath(workspace), name)


def create_page_file(path, data):
    """Publish a fully written file atomically, never replacing an existing name."""
    if len(data) > TEXT_MAX_BYTES:
        raise DeliverableEditError("file_too_large", "页面超过保存大小上限。")
    with file_write_lock(path):
        temporary = ""
        try:
            fd, temporary = tempfile.mkstemp(prefix=".cowork-page-", dir=os.path.dirname(path))
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Windows rename is an atomic no-replace operation; POSIX hardlink
            # publishes the same fully flushed inode without replacing a name.
            if os.name == "nt":
                os.rename(temporary, path)
                temporary = ""
            else:
                os.link(temporary, path)
        except FileExistsError as exc:
            raise DeliverableEditError("file_exists", "同名文件已存在，请改名后保存。") from exc
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
