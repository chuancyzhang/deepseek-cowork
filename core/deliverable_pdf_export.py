"""Local PDF export for Markdown, HTML, and DOCX deliverables.

The Qt WebEngine page must live on the UI thread. File inspection and DOCX
preflight run in a worker so large documents do not freeze the application.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree

from PySide6.QtCore import QMarginsF, QObject, QSizeF, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QPageLayout, QPageSize

from core.deliverable_editing import create_edit_session, load_editor_payload
from core.file_capabilities import file_capability


PDF_EXPORT_EXTENSIONS = frozenset({".md", ".markdown", ".html", ".htm", ".docx"})
PDF_EXPORT_TIMEOUT_MS = 60_000


class DeliverablePdfExportError(RuntimeError):
    """A stable, user-actionable PDF export failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code or "pdf_export_failed")
        self.message = str(message or "PDF 导出失败。")


def default_pdf_target(source_path: str) -> str:
    source = os.path.abspath(str(source_path or ""))
    stem = os.path.splitext(os.path.basename(source))[0]
    return os.path.join(os.path.dirname(source), f"{stem}.pdf")


def validate_pdf_bytes(data: bytes) -> int:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise DeliverablePdfExportError("pdf_empty", "PDF 渲染器没有返回任何数据。")
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(bytes(data)), strict=False)
        page_count = len(reader.pages)
        if page_count <= 0:
            raise ValueError("PDF 不包含页面")
        for index, page in enumerate(reader.pages):
            box = page.mediabox
            if float(box.width) <= 0 or float(box.height) <= 0:
                raise ValueError(f"第 {index + 1} 页尺寸无效")
        return page_count
    except DeliverablePdfExportError:
        raise
    except Exception as exc:
        raise DeliverablePdfExportError(
            "pdf_invalid",
            f"导出的 PDF 无法重新打开：{exc}",
        ) from exc


def write_pdf_atomic(target_path: str, data: bytes) -> int:
    target = os.path.abspath(str(target_path or ""))
    if os.path.splitext(target)[1].lower() != ".pdf":
        raise DeliverablePdfExportError("target_extension_invalid", "导出文件必须使用 .pdf 扩展名。")
    directory = os.path.dirname(target)
    if not os.path.isdir(directory):
        raise DeliverablePdfExportError("target_directory_missing", "PDF 保存目录不存在。")
    page_count = validate_pdf_bytes(data)
    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(target)}.",
            suffix=".tmp",
            dir=directory,
        )
    except OSError as exc:
        raise DeliverablePdfExportError(
            "target_write_failed",
            f"无法在目标目录创建 PDF 临时文件：{exc}",
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(bytes(data))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        if isinstance(exc, DeliverablePdfExportError):
            raise
        raise DeliverablePdfExportError("target_write_failed", f"无法保存 PDF：{exc}") from exc
    return page_count


def _prepare_source(source_path: str) -> dict:
    source = os.path.abspath(str(source_path or ""))
    if not source or not os.path.isfile(source):
        raise DeliverablePdfExportError("source_missing", "源文件不存在或已被删除。")
    extension = os.path.splitext(source)[1].lower()
    if extension not in PDF_EXPORT_EXTENSIONS:
        raise DeliverablePdfExportError("source_format_unsupported", "当前文件格式不支持导出 PDF。")
    capability = file_capability(extension)
    size = os.path.getsize(source)
    if capability is not None and capability.max_bytes and size > capability.max_bytes:
        raise DeliverablePdfExportError(
            "source_too_large",
            f"源文件大小 {size / (1024 * 1024):.1f} MiB，超过 {capability.max_bytes // (1024 * 1024)} MiB 上限。",
        )
    if extension in {".md", ".markdown"}:
        try:
            text = Path(source).read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise DeliverablePdfExportError(
                "markdown_encoding_invalid",
                "Markdown 文件不是 UTF-8 编码，无法按当前预览样式导出。",
            ) from exc
        except OSError as exc:
            raise DeliverablePdfExportError("source_read_failed", f"无法读取 Markdown 文件：{exc}") from exc
        return {"kind": "markdown", "source_path": source, "text": text, "size": size}
    if extension in {".html", ".htm"}:
        return {"kind": "html", "source_path": source, "size": size}
    try:
        session, report = create_edit_session(source)
        payload = load_editor_payload(session)
        page_layout = _read_docx_page_layout(source)
        if int(report.metadata.get("image_count") or 0):
            raise DeliverablePdfExportError(
                "docx_inline_image_unsupported",
                "DOCX 包含内嵌图片，当前内置渲染器无法保证图片进入 PDF。请移除图片或使用 Word 导出。",
            )
        if int(page_layout.get("explicit_page_breaks") or 0):
            raise DeliverablePdfExportError(
                "docx_manual_page_break_unsupported",
                "DOCX 包含手动分页符，内置渲染器无法保证分页位置准确。请移除手动分页符或使用 Word 导出。",
            )
    except Exception as exc:
        code = str(getattr(exc, "code", "docx_preflight_failed"))
        message = str(getattr(exc, "message", "") or exc or "DOCX 未通过兼容性预检。")
        raise DeliverablePdfExportError(code, message) from exc
    return {
        "kind": "docx",
        "source_path": source,
        "binary": bytes(payload.get("binary") or b""),
        "size": size,
        "compatibility": dict(report.metadata or {}),
        "page_layout": page_layout,
    }


def _read_docx_page_layout(path: str) -> dict:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    qualified = lambda name: f"{{{namespace}}}{name}"
    try:
        with zipfile.ZipFile(path, "r") as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except Exception as exc:
        raise DeliverablePdfExportError("docx_layout_invalid", f"无法读取 DOCX 页面设置：{exc}") from exc
    layouts = []
    for section in root.iter(qualified("sectPr")):
        page_size = section.find(qualified("pgSz"))
        if page_size is None:
            layouts.append((11906, 16838, "vertical"))
            continue
        try:
            width_twips = int(page_size.get(qualified("w")) or 11906)
            height_twips = int(page_size.get(qualified("h")) or 16838)
        except (TypeError, ValueError) as exc:
            raise DeliverablePdfExportError("docx_layout_invalid", "DOCX 页面尺寸不是有效数值。") from exc
        orientation = str(page_size.get(qualified("orient")) or "").lower()
        landscape = orientation == "landscape" or width_twips > height_twips
        layouts.append(
            (
                min(width_twips, height_twips),
                max(width_twips, height_twips),
                "horizontal" if landscape else "vertical",
            )
        )
    unique_layouts = list(dict.fromkeys(layouts or [(11906, 16838, "vertical")]))
    if len(unique_layouts) > 1:
        raise DeliverablePdfExportError(
            "docx_mixed_page_layout",
            "DOCX 包含多种纸张尺寸或方向，内置渲染器暂时无法可靠导出。请在 Word 中统一页面设置后重试。",
        )
    width_twips, height_twips, direction = unique_layouts[0]
    explicit_breaks = sum(
        1
        for node in root.iter(qualified("br"))
        if str(node.get(qualified("type")) or "").lower() == "page"
    )
    return {
        "width_px": width_twips * 96 / 1440,
        "height_px": height_twips * 96 / 1440,
        "direction": direction,
        "explicit_page_breaks": explicit_breaks,
    }


class _PdfExportPrepareWorker(QThread):
    completed = Signal(dict)
    failed = Signal(str, str)

    def __init__(self, source_path: str, parent=None):
        super().__init__(parent)
        self.source_path = os.path.abspath(str(source_path or ""))

    def run(self):
        try:
            self.completed.emit(_prepare_source(self.source_path))
        except DeliverablePdfExportError as exc:
            self.failed.emit(exc.code, exc.message)
        except Exception as exc:
            self.failed.emit("prepare_unexpected", f"准备 PDF 导出失败：{exc}")


class _DocxPdfExportBridge(QObject):
    ready_reported = Signal(str)
    editor_loaded_reported = Signal(str)
    pdf_ready_reported = Signal(str, str)
    error_reported = Signal(str)

    @Slot(str)
    def ready(self, mode):
        self.ready_reported.emit(str(mode or ""))

    @Slot(bool)
    def setDirty(self, _dirty):
        return

    @Slot(str)
    def editorLoaded(self, session_id):
        self.editor_loaded_reported.emit(str(session_id or ""))

    @Slot(str, str)
    def pdfExportReady(self, session_id, metadata):
        self.pdf_ready_reported.emit(str(session_id or ""), str(metadata or ""))

    @Slot(str)
    def reportError(self, message):
        self.error_reported.emit(str(message or "DOCX 渲染器发生未知错误。"))


_DOCUMENT_READY_SCRIPT = r"""
(() => {
  window.__coworkPdfExportState = {status: "waiting"};
  (async () => {
    try {
      const resourceNodes = Array.from(document.querySelectorAll(
        "img[src],script[src],link[rel='stylesheet'][href],iframe[src],video[src],audio[src],source[src],object[data],embed[src]"
      ));
      const remoteResources = resourceNodes
        .map((node) => String(node.currentSrc || node.src || node.href || node.data || ""))
        .filter((value) => /^https?:/i.test(value));
      const inlineRemote = Array.from(document.querySelectorAll("style,[style]"))
        .some((node) => /url\(\s*['\"]?https?:/i.test(String(node.textContent || node.getAttribute("style") || "")));
      if (remoteResources.length || inlineRemote) {
        throw new Error("页面引用了远程资源；为保护本地文件，PDF 导出仅允许本地或 data: 资源。");
      }
      const images = Array.from(document.images || []);
      await Promise.all(images.map((image) => {
        if (image.complete) return Promise.resolve();
        return new Promise((resolve) => {
          image.addEventListener("load", resolve, {once: true});
          image.addEventListener("error", resolve, {once: true});
        });
      }));
      const broken = images
        .filter((image) => !image.complete || image.naturalWidth <= 0 || image.naturalHeight <= 0)
        .map((image) => String(image.currentSrc || image.src || "未命名图片"))
        .slice(0, 3);
      if (broken.length) {
        throw new Error(`以下图片无法加载：${broken.join("；")}`);
      }
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await new Promise((resolve) => setTimeout(resolve, 250));
      window.__coworkPdfExportState = {status: "ready", imageCount: images.length};
    } catch (error) {
      window.__coworkPdfExportState = {
        status: "error",
        message: error instanceof Error ? error.message : String(error)
      };
    }
  })();
  return true;
})()
"""


class DeliverablePdfExportController(QObject):
    """Coordinates one local PDF export without mutating the source file."""

    stage_changed = Signal(str, dict)
    succeeded = Signal(dict)
    failed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._view = None
        self._page = None
        self._channel = None
        self._bridge = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(120)
        self._poll_timer.timeout.connect(self._poll_document_ready)
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(
            lambda: self._fail("export_timeout", "PDF 导出超过 60 秒，请检查文档资源后重试。")
        )
        self._source_path = ""
        self._target_path = ""
        self._docx_editor_path = ""
        self._markdown_renderer: Callable[[str], str] | None = None
        self._prepared = None
        self._session_id = ""
        self._layout = None
        self._finished = False
        self._started_at = 0.0

    @property
    def running(self) -> bool:
        return bool(self._started_at and not self._finished)

    def start(
        self,
        source_path: str,
        target_path: str,
        *,
        markdown_renderer: Callable[[str], str],
        docx_editor_path: str,
    ) -> None:
        if self.running:
            raise DeliverablePdfExportError("export_busy", "已有 PDF 正在导出。")
        self._source_path = os.path.abspath(str(source_path or ""))
        self._target_path = os.path.abspath(str(target_path or ""))
        self._docx_editor_path = os.path.abspath(str(docx_editor_path or ""))
        self._markdown_renderer = markdown_renderer
        self._prepared = None
        self._finished = False
        self._started_at = time.monotonic()
        self._timeout_timer.start(PDF_EXPORT_TIMEOUT_MS)
        self.stage_changed.emit(
            "start",
            {
                "source_path": self._source_path,
                "format": os.path.splitext(self._source_path)[1].lower().lstrip("."),
            },
        )
        worker = _PdfExportPrepareWorker(self._source_path, self)
        self._worker = worker
        worker.completed.connect(self._handle_prepared)
        worker.failed.connect(self._fail)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _handle_prepared(self, prepared: dict) -> None:
        self._worker = None
        if self._finished:
            return
        self._prepared = dict(prepared or {})
        kind = str(self._prepared.get("kind") or "")
        self.stage_changed.emit("prepared", {"format": kind, "size": int(self._prepared.get("size") or 0)})
        if kind == "docx":
            self._start_docx_render()
        else:
            self._start_document_render()

    def _new_web_page(self):
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except Exception as exc:
            raise DeliverablePdfExportError(
                "webengine_unavailable",
                f"QtWebEngine PDF 组件不可用，请修复安装后重试：{exc}",
            ) from exc
        if not hasattr(QWebEnginePage, "printToPdf"):
            raise DeliverablePdfExportError(
                "webengine_version_unsupported",
                "当前 PySide6 版本不支持 PDF 导出，请更新应用。",
            )
        view = QWebEngineView()
        view.resize(1280, 900)
        view.setWindowFlag(Qt.WindowType.Tool, True)
        view.setWindowOpacity(0.0)
        view.move(-32_000, -32_000)
        view.show()
        page = view.page()
        if hasattr(page, "setVisible"):
            page.setVisible(True)
        settings = page.settings()
        attributes = QWebEngineSettings.WebAttribute
        settings.setAttribute(attributes.JavascriptEnabled, True)
        settings.setAttribute(attributes.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(attributes.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(attributes.JavascriptCanAccessClipboard, False)
        self._view = view
        self._page = page
        return page

    def _start_document_render(self) -> None:
        try:
            page = self._new_web_page()
            kind = str(self._prepared.get("kind") or "")
            margins = QMarginsF(16, 16, 16, 16) if kind == "markdown" else QMarginsF(0, 0, 0, 0)
            self._layout = QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Portrait,
                margins,
                QPageLayout.Unit.Millimeter,
            )
            page.loadFinished.connect(self._handle_document_loaded)
            if kind == "markdown":
                if not callable(self._markdown_renderer):
                    raise DeliverablePdfExportError("markdown_renderer_missing", "Markdown PDF 渲染器未初始化。")
                document = self._markdown_renderer(str(self._prepared.get("text") or ""))
                base_url = QUrl.fromLocalFile(os.path.dirname(self._source_path) + os.sep)
                page.setHtml(document, base_url)
            else:
                page.load(QUrl.fromLocalFile(self._source_path))
        except DeliverablePdfExportError as exc:
            self._fail(exc.code, exc.message)
        except Exception as exc:
            self._fail("web_page_init_failed", f"初始化 PDF 页面失败：{exc}")

    def _handle_document_loaded(self, ok: bool) -> None:
        if self._finished:
            return
        if not ok:
            self._fail("document_load_failed", "源页面加载失败，请检查 HTML 或本地资源。")
            return
        self.stage_changed.emit(
            "page_loaded",
            {"format": str((self._prepared or {}).get("kind") or "")},
        )
        self._page.runJavaScript(
            _DOCUMENT_READY_SCRIPT,
            lambda _result: self._poll_timer.start() if not self._finished else None,
        )

    def _poll_document_ready(self) -> None:
        if self._finished or self._page is None:
            self._poll_timer.stop()
            return
        self._page.runJavaScript(
            "JSON.stringify(window.__coworkPdfExportState || {status:'waiting'})",
            self._handle_document_ready_state,
        )

    def _handle_document_ready_state(self, raw_state) -> None:
        if self._finished:
            return
        try:
            state = json.loads(str(raw_state or "{}"))
        except json.JSONDecodeError:
            return
        status = str(state.get("status") or "waiting")
        if status == "error":
            self._fail("document_resource_failed", str(state.get("message") or "页面资源加载失败。"))
            return
        if status != "ready":
            return
        self._poll_timer.stop()
        self.stage_changed.emit(
            "render_ready",
            {"format": str(self._prepared.get("kind") or ""), "image_count": int(state.get("imageCount") or 0)},
        )
        self._print_page()

    def _start_docx_render(self) -> None:
        if not self._docx_editor_path or not os.path.isfile(self._docx_editor_path):
            self._fail("docx_editor_missing", "缺少内置 DOCX 渲染器资源，请修复安装后重试。")
            return
        try:
            from PySide6.QtWebChannel import QWebChannel

            page = self._new_web_page()
            bridge = _DocxPdfExportBridge(page)
            channel = QWebChannel(page)
            channel.registerObject("deliverableEditorBridge", bridge)
            page.setWebChannel(channel)
            self._bridge = bridge
            self._channel = channel
            bridge.ready_reported.connect(self._handle_docx_editor_ready)
            bridge.editor_loaded_reported.connect(self._handle_docx_model_loaded)
            bridge.pdf_ready_reported.connect(self._handle_docx_pdf_ready)
            bridge.error_reported.connect(lambda message: self._fail("docx_render_failed", message))
            page.loadFinished.connect(self._handle_docx_page_loaded)
            self._session_id = os.urandom(12).hex()
            page.load(QUrl.fromLocalFile(self._docx_editor_path))
        except DeliverablePdfExportError as exc:
            self._fail(exc.code, exc.message)
        except Exception as exc:
            self._fail("docx_renderer_init_failed", f"初始化 DOCX 渲染器失败：{exc}")

    def _handle_docx_page_loaded(self, ok: bool) -> None:
        if not ok and not self._finished:
            self._fail("docx_editor_load_failed", "内置 DOCX 渲染器页面加载失败。")

    def _handle_docx_editor_ready(self, mode: str) -> None:
        if self._finished:
            return
        if mode != "docx":
            self._fail("docx_editor_mode_invalid", "内置渲染器与 DOCX 格式不匹配。")
            return
        try:
            import base64

            encoded = base64.b64encode(bytes(self._prepared.get("binary") or b"")).decode("ascii")
            chunk_size = 192 * 1024
            chunks = [encoded[index : index + chunk_size] for index in range(0, len(encoded), chunk_size)] or [""]
            self._page.runJavaScript(
                "window.coworkEditor.beginLoad("
                + json.dumps(self._session_id)
                + f",{len(chunks)})"
            )
            for index, chunk in enumerate(chunks):
                self._page.runJavaScript(
                    f"window.coworkEditor.appendLoadChunk({index},"
                    + json.dumps(chunk, ensure_ascii=True)
                    + ")"
                )
            self._page.runJavaScript("window.coworkEditor.finishLoad()")
        except Exception as exc:
            self._fail("docx_payload_failed", f"向 DOCX 渲染器传输文件失败：{exc}")

    def _handle_docx_model_loaded(self, session_id: str) -> None:
        if self._finished or session_id != self._session_id:
            return
        layout = dict((self._prepared or {}).get("page_layout") or {})
        self._page.runJavaScript(
            "window.coworkEditor.preparePdfExport("
            + json.dumps(layout, ensure_ascii=True)
            + ")"
        )

    def _handle_docx_pdf_ready(self, session_id: str, raw_metadata: str) -> None:
        if self._finished or session_id != self._session_id:
            return
        try:
            metadata = json.loads(raw_metadata)
            width_mm = float(metadata.get("widthMm") or 0)
            height_mm = float(metadata.get("heightMm") or 0)
            page_count = int(metadata.get("pageCount") or 0)
            if width_mm <= 0 or height_mm <= 0 or page_count <= 0:
                raise ValueError("DOCX 页面尺寸或页数无效")
            page_size = QPageSize(
                QSizeF(width_mm, height_mm),
                QPageSize.Unit.Millimeter,
                "DOCX",
                QPageSize.SizeMatchPolicy.ExactMatch,
            )
            self._layout = QPageLayout(
                page_size,
                QPageLayout.Orientation.Portrait,
                QMarginsF(0, 0, 0, 0),
                QPageLayout.Unit.Millimeter,
            )
        except Exception as exc:
            self._fail("docx_print_metadata_invalid", f"DOCX 打印参数无效：{exc}")
            return
        self.stage_changed.emit(
            "render_ready",
            {
                "format": "docx",
                "page_count": page_count,
                "width_mm": round(width_mm, 2),
                "height_mm": round(height_mm, 2),
            },
        )
        self._print_page()

    def _print_page(self) -> None:
        if self._finished or self._page is None or self._layout is None:
            return
        try:
            self._page.printToPdf(self._handle_pdf_bytes, self._layout)
        except Exception as exc:
            self._fail("pdf_print_start_failed", f"启动 PDF 渲染失败：{exc}")

    def _handle_pdf_bytes(self, pdf_data) -> None:
        if self._finished:
            return
        try:
            if not os.path.isfile(self._source_path):
                raise DeliverablePdfExportError(
                    "source_removed_during_export",
                    "源文件在导出过程中被删除，PDF 未保存。",
                )
            data = bytes(pdf_data)
            page_count = write_pdf_atomic(self._target_path, data)
        except DeliverablePdfExportError as exc:
            self._fail(exc.code, exc.message)
            return
        except Exception as exc:
            self._fail("pdf_write_unexpected", f"保存 PDF 失败：{exc}")
            return
        elapsed_ms = round((time.monotonic() - self._started_at) * 1000)
        result = {
            "source_path": self._source_path,
            "target_path": self._target_path,
            "format": str((self._prepared or {}).get("kind") or ""),
            "page_count": page_count,
            "bytes_written": len(data),
            "elapsed_ms": elapsed_ms,
        }
        self._finish()
        self.succeeded.emit(result)

    def _fail(self, code: str, message: str) -> None:
        if self._finished:
            return
        self._finish()
        self.failed.emit(str(code or "pdf_export_failed"), str(message or "PDF 导出失败。"))

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._timeout_timer.stop()
        self._poll_timer.stop()
        page = self._page
        view = self._view
        self._page = None
        self._view = None
        self._channel = None
        self._bridge = None
        if view is not None:
            view.close()
            view.deleteLater()
        elif page is not None:
            page.deleteLater()
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.finished.connect(self.deleteLater)
        else:
            QTimer.singleShot(0, self.deleteLater)
