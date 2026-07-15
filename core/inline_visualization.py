import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from datetime import datetime

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
_REMOTE_URL_RE = re.compile(r"https?://", re.IGNORECASE)


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
    requested = [str(item or "").strip() for item in (requested_origins or []) if str(item or "").strip()]
    if requested or _REMOTE_URL_RE.search(fragment):
        raise ValueError("首版内联可视化仅支持完全离线、自包含 Fragment，不能引用外部 URL。")
    return {"fragment": fragment, "bytes": size, "origins": []}


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


def build_visualization_document(fragment, initial_state=None, read_only=False, theme_css=""):
    state_json = json.dumps(initial_state if isinstance(initial_state, dict) else {}, ensure_ascii=False).replace("<", "\\u003c")
    token = hashlib.sha256(os.urandom(32)).hexdigest()
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
  const reportError = (message) => parent.postMessage({{
    token, type: 'error', message: String(message || '未知 JavaScript 错误')
  }}, '*');
  addEventListener('error', event => reportError(event.message));
  addEventListener('unhandledrejection', event => reportError(event.reason));
  new ResizeObserver(report).observe(document.documentElement);
  addEventListener('load', report, {{ once: true }});
}})();
</script>
"""
    inner = f"""<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' data: blob:; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'\"><style>{theme_css}</style></head><body>{child_bootstrap}{fragment}</body></html>"""
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
