import Editor, {
  ListStyle,
  ListType,
  RowFlex,
  TitleLevel
} from "@hufe921/canvas-editor";
import docxPlugin from "@hufe921/canvas-editor-plugin-docx";
import { Buffer } from "buffer";

window.Buffer = Buffer;

let bridge = null;
let editor = null;
let sessionId = "";
let suppressDirty = true;
let dirtyTrackingArmed = false;
let exportCapture = null;

function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

function reportError(error) {
  if (bridge) bridge.reportError(errorText(error));
}

function bytesFromBase64(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function sendBinaryPayload(kind, bytes) {
  bridge.payload(sessionId, kind, Buffer.from(bytes).toString("base64"));
}

function installExportCapture() {
  const nativeCreateObjectURL = URL.createObjectURL.bind(URL);
  const nativeAnchorClick = HTMLAnchorElement.prototype.click;
  URL.createObjectURL = (blob) => {
    if (!exportCapture || !(blob instanceof Blob)) {
      return nativeCreateObjectURL(blob);
    }
    const capture = exportCapture;
    exportCapture = null;
    blob.arrayBuffer()
      .then((buffer) => sendBinaryPayload("docx", new Uint8Array(buffer)))
      .catch(reportError);
    return "about:blank";
  };
  // The DOCX plugin clicks a detached anchor, so document event delegation
  // cannot intercept it. Consume only the export captured above.
  HTMLAnchorElement.prototype.click = function () {
    if (this.hasAttribute("download") && this.href === "about:blank") return;
    return nativeAnchorClick.call(this);
  };
}

function setDirty() {
  if (!suppressDirty && dirtyTrackingArmed && bridge) bridge.setDirty(sessionId, true);
}

function armDirtyTracking() {
  if (editor && !suppressDirty) dirtyTrackingArmed = true;
}

function command(name, value) {
  if (!editor) return;
  armDirtyTracking();
  const commands = editor.command;
  const mapping = {
    undo: () => commands.executeUndo(),
    redo: () => commands.executeRedo(),
    bold: () => commands.executeBold(),
    italic: () => commands.executeItalic(),
    underline: () => commands.executeUnderline(),
    strikeout: () => commands.executeStrikeout(),
    alignLeft: () => commands.executeRowFlex(RowFlex.LEFT),
    alignCenter: () => commands.executeRowFlex(RowFlex.CENTER),
    alignRight: () => commands.executeRowFlex(RowFlex.RIGHT),
    justify: () => commands.executeRowFlex(RowFlex.JUSTIFY),
    bullets: () => commands.executeList(ListType.UL, ListStyle.DISC),
    numbering: () => commands.executeList(ListType.OL, ListStyle.DECIMAL),
    table: () => commands.executeInsertTable(3, 3)
  };
  if (name === "title") {
    const levels = {
      first: TitleLevel.FIRST,
      second: TitleLevel.SECOND,
      third: TitleLevel.THIRD
    };
    commands.executeTitle(levels[value] || null);
    return;
  }
  if (mapping[name]) mapping[name]();
}

async function loadDocument(encoded) {
  try {
    const bytes = bytesFromBase64(encoded);
    suppressDirty = true;
    dirtyTrackingArmed = false;
    if (editor) editor.destroy();
    editor = new Editor(document.getElementById("docx-canvas"), { main: [] }, {
      mode: "edit",
      pageMode: "paging",
      defaultFont: "Microsoft YaHei",
      scale: 1.1
    });
    editor.use(docxPlugin);
    editor.listener.contentChange = setDirty;
    await Promise.resolve(editor.command.executeImportDocx({
      arrayBuffer: bytes.buffer
    }));
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
    bridge.setDirty(sessionId, false);
    bridge.editorLoaded(sessionId);
    suppressDirty = false;
  } catch (error) {
    reportError(error);
  }
}

function requestSave() {
  if (!editor || exportCapture) return;
  exportCapture = { startedAt: Date.now() };
  editor.command.executeExportDocx({ fileName: "cowork-deliverable" });
  window.setTimeout(() => {
    if (exportCapture) {
      exportCapture = null;
      reportError("DOCX 导出超时，编辑内容仍保留在当前页面。");
    }
  }, 30000);
}

async function preparePdfExport(layout = {}) {
  try {
    if (!editor) throw new Error("DOCX 渲染模型尚未加载完成。");
    const requestedWidth = Number(layout.width_px);
    const requestedHeight = Number(layout.height_px);
    const requestedDirection = String(layout.direction || "").toLowerCase();
    if (requestedWidth > 0 && requestedHeight > 0) {
      editor.command.executePaperSize(requestedWidth, requestedHeight);
    }
    if (requestedDirection === "horizontal" || requestedDirection === "vertical") {
      editor.command.executePaperDirection(requestedDirection);
    }
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    await new Promise((resolve) => window.setTimeout(resolve, 750));
    const options = editor.command.getOptions();
    const rendered = await Promise.resolve(editor.command.getImage({
      pixelRatio: 2,
      mode: "print"
    }));
    const pageImages = Array.isArray(rendered) ? rendered : [rendered];
    if (!pageImages.length || pageImages.some((item) => typeof item !== "string" || !item)) {
      throw new Error("DOCX 渲染器没有生成可打印页面。");
    }

    const direction = String(options.paperDirection || "vertical").toLowerCase();
    const landscape = direction.includes("horizontal") || direction.includes("landscape");
    const baseWidth = Number(options.width) || 794;
    const baseHeight = Number(options.height) || 1123;
    const widthPx = landscape ? baseHeight : baseWidth;
    const heightPx = landscape ? baseWidth : baseHeight;
    const widthMm = widthPx * 25.4 / 96;
    const heightMm = heightPx * 25.4 / 96;

    const printable = document.createElement("main");
    printable.id = "cowork-pdf-pages";
    const imageElements = pageImages.map((source) => {
      const page = document.createElement("section");
      page.className = "cowork-pdf-page";
      const image = document.createElement("img");
      image.src = source;
      image.alt = "";
      page.append(image);
      printable.append(page);
      return image;
    });

    const style = document.createElement("style");
    style.id = "cowork-pdf-print-style";
    style.textContent = `
      @page { size: ${widthMm}mm ${heightMm}mm; margin: 0; }
      html, body { width: ${widthMm}mm; margin: 0; padding: 0; overflow: visible; background: #fff; }
      #cowork-pdf-pages { width: ${widthMm}mm; margin: 0; padding: 0; }
      .cowork-pdf-page {
        box-sizing: border-box;
        width: ${widthMm}mm;
        height: ${heightMm}mm;
        margin: 0;
        padding: 0;
        break-after: page;
        page-break-after: always;
        overflow: hidden;
        background: #fff;
      }
      .cowork-pdf-page:last-child { break-after: auto; page-break-after: auto; }
      .cowork-pdf-page img { display: block; width: 100%; height: 100%; }
    `;
    document.head.append(style);
    document.body.replaceChildren(printable);
    await Promise.all(imageElements.map((image) => image.decode()));
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    bridge.pdfExportReady(sessionId, JSON.stringify({
      pageCount: pageImages.length,
      widthMm,
      heightMm,
      pixelRatio: 2,
      explicitPageBreaks: Number(layout.explicit_page_breaks) || 0
    }));
  } catch (error) {
    reportError(error);
  }
}

function dispose() {
  suppressDirty = true;
  dirtyTrackingArmed = false;
  exportCapture = null;
  if (editor) {
    editor.destroy();
    editor = null;
  }
  document.getElementById("docx-canvas").replaceChildren();
}

function setTheme(theme) {
  for (const [key, value] of Object.entries(theme || {})) {
    document.documentElement.style.setProperty(`--cowork-${key}`, value);
  }
}

window.coworkEditor = {
  load(id, value) {
    dispose();
    sessionId = id;
    return loadDocument(value);
  },
  requestSave,
  preparePdfExport,
  command,
  dispose,
  setTheme
};

installExportCapture();

document.querySelector(".editor-toolbar").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-command]");
  if (button) command(button.dataset.command);
});
document.querySelector("select[data-command]").addEventListener("change", (event) => {
  command("title", event.target.value);
});

for (const eventName of ["beforeinput", "keydown", "paste", "cut", "drop", "contextmenu"]) {
  document.addEventListener(eventName, armDirtyTracking, true);
}

window.connectDesktopBridge("deliverableEditorBridge", nativeBridge => {
  bridge = nativeBridge;
  bridge.ready("docx");
});
