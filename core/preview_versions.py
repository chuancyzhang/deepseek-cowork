"""Content versions for local previews; run disk reads outside the GUI thread."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import url2pathname


MANUAL_PREVIEW_EXTENSIONS = {".html", ".htm", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}


def path_key(path):
    return os.path.normcase(os.path.abspath(path))


def html_resources(path, data=None):
    from bs4 import BeautifulSoup
    from core.deliverable_editing import decode_text_bytes
    text, *_ = decode_text_bytes(Path(path).read_bytes() if data is None else data)
    soup = BeautifulSoup(text, "html.parser")
    base = Path(path).resolve().as_uri()
    base_node = soup.find("base", href=True)
    if base_node:
        base = urljoin(base, base_node["href"])
    result = []
    for node in soup.select("link[href], script[src]"):
        url = urlparse(urljoin(base, node.get("href") or node.get("src") or ""))
        if url.scheme != "file" or url.netloc:
            continue
        target = os.path.abspath(url2pathname(url.path))
        if Path(target).suffix.lower() in {".css", ".js", ".mjs"} and target not in result:
            result.append(target)
    return result


def _signature(stat):
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _read_version(path, *, keep_bytes=False):
    try:
        before = os.stat(path)
    except FileNotFoundError:
        return None, None
    digest = hashlib.sha256()
    chunks = []
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            if keep_bytes:
                chunks.append(chunk)
    if _signature(before) != _signature(os.stat(path)):
        raise OSError("文件仍在写入，请稍后刷新。")
    return digest.hexdigest(), b"".join(chunks) if keep_bytes else None


@dataclass(frozen=True)
class PreviewVersion:
    source: str | None
    resources: tuple[tuple[str, str | None], ...] = ()


def read_preview_version(path):
    html = Path(path).suffix.lower() in {".html", ".htm"}
    source, data = _read_version(path, keep_bytes=html)
    resources = []
    if html and source is not None:
        for resource in html_resources(path, data)[:100]:
            fingerprint, _ = _read_version(resource)
            resources.append((path_key(resource), fingerprint))
    return PreviewVersion(source, tuple(sorted(resources)))
