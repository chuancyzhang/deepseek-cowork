import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .env_utils import get_app_data_dir


INLINE_VISUALIZATION_SKILL = "visualize"
INLINE_VISUALIZATION_MAX_BYTES = 2 * 1024 * 1024
INLINE_VISUALIZATION_STATE_MAX_BYTES = 64 * 1024
INLINE_VISUALIZATION_DIRECTIVE_RE = re.compile(
    r'^::cowork-inline-vis\{file="(?P<file>[a-z0-9][a-z0-9.-]*\.html)"\}\s*$',
    re.MULTILINE,
)
_SAFE_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}\.html$")
_FORBIDDEN_DOCUMENT_RE = re.compile(
    r"<!doctype\b|<\s*(?:html|head|body)(?:\s|>)",
    re.IGNORECASE,
)
_REMOTE_URL_RE = re.compile(r"https?://[^\s\"'<>`]+", re.IGNORECASE)
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(?P<url>[^)'\"]+)\1\s*\)", re.IGNORECASE)
_TRUSTED_EXTERNAL_HOSTS = frozenset(
    {
        "cdnjs.cloudflare.com",
        "esm.sh",
        "cdn.jsdelivr.net",
        "unpkg.com",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "fonts.bunny.net",
    }
)
_ORIGIN_DEPENDENCIES = {
    "https://fonts.googleapis.com": frozenset({"https://fonts.gstatic.com"}),
}
_NON_NETWORK_NAMESPACE_URIS = frozenset(
    {
        "http://www.w3.org/2000/svg",
        "http://www.w3.org/1999/xlink",
        "http://www.w3.org/xml/1998/namespace",
        "http://www.w3.org/2000/xmlns/",
    }
)
_RESOURCE_SRC_TAGS = frozenset(
    {"audio", "embed", "iframe", "img", "input", "script", "source", "track", "video"}
)
_RESOURCE_HREF_TAGS = frozenset({"image", "link", "use"})


def _clean_external_url(value):
    candidate = str(value or "").strip().strip("\"'")
    return candidate.rstrip("),.;]}")


def _urls_in_script_or_style(value):
    return [_clean_external_url(match.group(0)) for match in _REMOTE_URL_RE.finditer(str(value or ""))]


class _VisualizationResourceParser(HTMLParser):
    """Collect URLs that can load resources without treating namespace URIs as network access."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []
        self._script_depth = 0
        self._style_depth = 0

    def _append(self, value):
        candidate = _clean_external_url(value)
        if not candidate or candidate.lower() in _NON_NETWORK_NAMESPACE_URIS:
            return
        if candidate.startswith("//"):
            raise ValueError(f"外部资源必须使用完整 HTTPS URL：{candidate}")
        if candidate.lower().startswith(("http://", "https://")):
            self.urls.append(candidate)

    def _scan_css(self, value):
        for match in _CSS_URL_RE.finditer(str(value or "")):
            self._append(match.group("url"))
        for candidate in _urls_in_script_or_style(value):
            self._append(candidate)

    def _handle_tag(self, tag, attrs):
        tag_name = str(tag or "").lower()
        for raw_name, raw_value in attrs:
            name = str(raw_name or "").lower()
            value = str(raw_value or "")
            if name == "style":
                self._scan_css(value)
            elif name == "src" and tag_name in _RESOURCE_SRC_TAGS:
                self._append(value)
            elif name in {"href", "xlink:href"} and tag_name in _RESOURCE_HREF_TAGS:
                self._append(value)
            elif name == "poster" and tag_name == "video":
                self._append(value)
            elif name == "srcset" and tag_name in {"img", "source"}:
                for item in value.split(","):
                    self._append(item.strip().split()[0] if item.strip() else "")
        if tag_name == "script":
            self._script_depth += 1
        elif tag_name == "style":
            self._style_depth += 1

    def handle_starttag(self, tag, attrs):
        self._handle_tag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._handle_tag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag_name = str(tag or "").lower()
        if tag_name == "script" and self._script_depth:
            self._script_depth -= 1
        elif tag_name == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data):
        if self._script_depth:
            for candidate in _urls_in_script_or_style(data):
                self._append(candidate)
        elif self._style_depth:
            self._scan_css(data)


def _normalize_external_origin(value):
    candidate = _clean_external_url(value)
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"外部资源 URL 无效：{candidate}") from exc
    if parsed.scheme.lower() != "https":
        raise ValueError(f"外部资源仅支持 HTTPS：{candidate}")
    host = str(parsed.hostname or "").lower().rstrip(".")
    if not host or parsed.username or parsed.password or (port not in {None, 443}):
        raise ValueError(f"外部资源 URL 无效：{candidate}")
    if host not in _TRUSTED_EXTERNAL_HOSTS:
        raise ValueError(f"外部资源来源不在受信 CDN 白名单中：{host}")
    return f"https://{host}"


def _extract_visualization_origins(fragment):
    parser = _VisualizationResourceParser()
    try:
        parser.feed(str(fragment or ""))
        parser.close()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"无法解析可视化 Fragment 中的外部资源：{exc}") from exc
    origins = {_normalize_external_origin(url) for url in parser.urls}
    for origin in tuple(origins):
        origins.update(_ORIGIN_DEPENDENCIES.get(origin, ()))
    return sorted(origins)


def _validated_origins(origins):
    return sorted({_normalize_external_origin(value) for value in (origins or []) if str(value or "").strip()})


def _content_security_policy(origins):
    trusted = _validated_origins(origins)
    remote_sources = "" if not trusted else " " + " ".join(trusted)
    return (
        "default-src 'none'; "
        f"script-src 'unsafe-inline' 'unsafe-eval' data: blob:{remote_sources}; "
        f"style-src 'unsafe-inline'{remote_sources}; "
        f"img-src data: blob:{remote_sources}; "
        f"font-src data:{remote_sources}; "
        "connect-src 'none'; media-src 'none'; frame-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    )


def _safe_session_key(conversation_id):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(conversation_id or "").strip())
    if not value:
        raise ValueError("会话 ID 为空，无法创建可视化目录。")
    return value


def visualization_conversation_root(conversation_id, create=False):
    date_path = datetime.now().strftime("%Y/%m/%d")
    root = os.path.join(
        get_app_data_dir(),
        "visualizations",
        date_path,
        _safe_session_key(conversation_id),
    )
    if create:
        os.makedirs(root, exist_ok=True)
    return os.path.normpath(root)


def visualization_staging_dir(conversation_id, create=False):
    path = os.path.join(visualization_conversation_root(conversation_id, create=create), "staging")
    if create:
        os.makedirs(path, exist_ok=True)
    return os.path.normpath(path)


def visualization_published_dir(conversation_id, create=False):
    path = os.path.join(visualization_conversation_root(conversation_id, create=create), "published")
    if create:
        os.makedirs(path, exist_ok=True)
    return os.path.normpath(path)


def is_visualize_enabled_context(context):
    if not isinstance(context, dict):
        return False
    manager = context.get("skill_manager")
    records = getattr(manager, "skill_records", {}) if manager is not None else {}
    return INLINE_VISUALIZATION_SKILL in records


def find_inline_visualization_files(text):
    return [match.group("file") for match in INLINE_VISUALIZATION_DIRECTIVE_RE.finditer(str(text or ""))]


def strip_inline_visualization_directives(text, registered_files=None):
    allowed = set(registered_files or [])

    def replace(match):
        return "" if match.group("file") in allowed else match.group(0)

    value = INLINE_VISUALIZATION_DIRECTIVE_RE.sub(replace, str(text or ""))
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_visualization_fragment(path, requested_origins=None):
    source = os.path.abspath(path)
    if not os.path.isfile(source):
        raise FileNotFoundError(f"可视化文件不存在：{source}")
    size = os.path.getsize(source)
    if size <= 0:
        raise ValueError("可视化文件为空。")
    if size > INLINE_VISUALIZATION_MAX_BYTES:
        raise ValueError("可视化 Fragment 超过 2 MB，请先聚合、抽样或降低数据精度。")
    try:
        with open(source, "r", encoding="utf-8") as handle:
            fragment = handle.read()
    except UnicodeDecodeError as exc:
        raise ValueError("可视化 Fragment 必须使用 UTF-8 编码。") from exc
    if _FORBIDDEN_DOCUMENT_RE.search(fragment):
        raise ValueError("可视化文件必须是 HTML Fragment，不能包含 doctype、html、head 或 body。")
    if not re.search(r"<[^>]+\bid\s*=\s*['\"][^'\"]+['\"]", fragment, re.IGNORECASE):
        raise ValueError("可视化 Fragment 的根元素必须包含唯一 id。")
    origins = _extract_visualization_origins(fragment)
    requested = _validated_origins(requested_origins)
    undeclared = sorted(set(requested) - set(origins))
    if undeclared:
        raise ValueError(f"声明了 Fragment 未实际引用的外部来源：{', '.join(undeclared)}")
    return {"fragment": fragment, "bytes": size, "origins": origins}


def publish_visualization_fragment(conversation_id, filename, title="", requested_origins=None):
    name = str(filename or "").strip()
    if not _SAFE_FILE_RE.fullmatch(name):
        raise ValueError("文件名必须是 ASCII 小写连字符格式并以 .html 结尾。")
    staging = visualization_staging_dir(conversation_id, create=True)
    source = os.path.abspath(os.path.join(staging, name))
    if os.path.commonpath([staging, source]) != staging:
        raise ValueError("可视化文件必须位于当前会话暂存目录。")
    validated = validate_visualization_fragment(source, requested_origins=requested_origins)
    digest = sha256_file(source)
    stem = os.path.splitext(name)[0]
    published_name = f"{stem}-{digest[:8]}.html"
    published_dir = visualization_published_dir(conversation_id, create=True)
    destination = os.path.join(published_dir, published_name)
    if not os.path.isfile(destination):
        fd, temp_path = tempfile.mkstemp(prefix="inline-vis-", suffix=".tmp", dir=published_dir)
        os.close(fd)
        try:
            shutil.copyfile(source, temp_path)
            os.replace(temp_path, destination)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    return {
        "file": published_name,
        "path": os.path.normpath(destination),
        "sha256": digest,
        "bytes": validated["bytes"],
        "title": str(title or "").strip() or stem.replace("-", " "),
        "origins": validated["origins"],
        "directive": f'::cowork-inline-vis{{file="{published_name}"}}',
    }


def build_visualization_document(fragment, initial_state=None, read_only=False, theme_css="", origins=None):
    state_json = json.dumps(initial_state if isinstance(initial_state, dict) else {}, ensure_ascii=False).replace("<", "\\u003c")
    token = hashlib.sha256(os.urandom(32)).hexdigest()
    content_security_policy = _content_security_policy(origins)
    child_bootstrap = f"""
<script>
(() => {{
  const token = {json.dumps(token)};
  const initialState = {state_json};
  window.cowork = Object.freeze({{
    loadState: async () => initialState,
    saveState: (state) => {{
      if ({str(bool(read_only)).lower()}) return;
      parent.postMessage({{ token, type: 'state', state }}, '*');
    }}
  }});
  const report = () => parent.postMessage({{
    token, type: 'height', height: Math.ceil(document.documentElement.scrollHeight)
  }}, '*');
  const reportedErrors = new Set();
  const reportError = (message) => {{
    const detail = String(message || '未知 JavaScript 错误');
    if (reportedErrors.has(detail)) return;
    reportedErrors.add(detail);
    parent.postMessage({{ token, type: 'error', message: detail }}, '*');
  }};
  addEventListener('error', event => {{
    const target = event.target;
    if (target && target !== window) {{
      const url = target.currentSrc || target.src || target.href || target.getAttribute?.('href') || '';
      reportError('外部资源加载失败：' + (url || target.tagName || '未知资源'));
      return;
    }}
    reportError(event.message);
  }}, true);
  addEventListener('unhandledrejection', event => reportError(event.reason));
  addEventListener('securitypolicyviolation', event => {{
    reportError('内容安全策略已阻止资源：' + (event.blockedURI || event.violatedDirective || '未知资源'));
  }});
  document.fonts?.addEventListener('loadingerror', () => {{
    reportError('外部资源加载失败：字体资源');
  }});
  new ResizeObserver(report).observe(document.documentElement);
  addEventListener('load', report, {{ once: true }});
}})();
</script>
"""
    inner = f"""<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta http-equiv=\"Content-Security-Policy\" content=\"{html.escape(content_security_policy, quote=True)}\"><style>{theme_css}</style></head><body>{child_bootstrap}{fragment}</body></html>"""
    readonly_overlay = '<div id="readonly" aria-label="历史可视化只读模式"></div>' if read_only else ""
    readonly_style = "iframe{pointer-events:none}" if read_only else ""
    iframe_tabindex = "-1" if read_only else "0"
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<style>html,body{{margin:0;background:transparent;overflow:hidden}}iframe{{display:block;width:100%;height:420px;border:0}}#readonly{{position:absolute;inset:0;background:transparent;cursor:default}}body{{position:relative}}{readonly_style}</style>
<script src=\"qrc:///qtwebchannel/qwebchannel.js\"></script></head>
<body><iframe id=\"frame\" tabindex=\"{iframe_tabindex}\" sandbox=\"allow-scripts\" referrerpolicy=\"no-referrer\" srcdoc=\"{html.escape(inner, quote=True)}\"></iframe>{readonly_overlay}
<script>
new QWebChannel(qt.webChannelTransport, channel => {{
  const bridge = channel.objects.bridge;
  addEventListener('message', event => {{
    const data = event.data || {{}};
    if (data.token !== {json.dumps(token)}) return;
    if (data.type === 'height') {{
      const height = Math.max(180, Math.min(900, Number(data.height) || 420));
      document.getElementById('frame').style.height = height + 'px';
      bridge.reportHeight(Math.ceil(height));
    }} else if (data.type === 'state') {{
      bridge.saveState(JSON.stringify(data.state || {{}}));
    }} else if (data.type === 'error') {{
      bridge.reportError(String(data.message || '未知 JavaScript 错误'));
    }}
  }});
}});
</script></body></html>"""
