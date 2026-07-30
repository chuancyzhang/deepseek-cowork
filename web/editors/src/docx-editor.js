import Editor, {
  ListStyle,
  ListType,
  RowFlex,
  TitleLevel
} from "@hufe921/canvas-editor";
import docxPlugin from "@hufe921/canvas-editor-plugin-docx";
import { Buffer } from "buffer";

window.Buffer = Buffer;

const CHUNK_SIZE = 192 * 1024;
let bridge = null;
let editor = null;
let sessionId = "";
let loadingChunks = [];
let loadingExpected = 0;
let suppressDirty = true;
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

function base64FromBytes(bytes) {
  let result = "";
  for (let offset = 0; offset < bytes.length; offset += CHUNK_SIZE) {
    result += String.fromCharCode(...bytes.subarray(offset, offset + CHUNK_SIZE));
  }
  return btoa(result);
}

function sendBinaryPayload(kind, bytes) {
  const encoded = base64FromBytes(bytes);
  const total = Math.max(1, Math.ceil(encoded.length / CHUNK_SIZE));
  bridge.beginPayload(sessionId, kind, total);
  for (let index = 0; index < total; index += 1) {
    bridge.appendPayload(
      sessionId,
      index,
      encoded.slice(index * CHUNK_SIZE, (index + 1) * CHUNK_SIZE)
    );
  }
  bridge.finishPayload(sessionId);
}

function installExportCapture() {
  const nativeCreateObjectURL = URL.createObjectURL.bind(URL);
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
  document.addEventListener(
    "click",
    (event) => {
      const anchor = event.target && event.target.closest
        ? event.target.closest("a[download]")
        : null;
      if (anchor && anchor.href === "about:blank") {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true
  );
}

function setDirty() {
  if (!suppressDirty && bridge) bridge.setDirty(true);
}

function command(name, value) {
  if (!editor) return;
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

async function loadDocument() {
  try {
    const encoded = loadingChunks.join("");
    loadingChunks = [];
    const bytes = bytesFromBase64(encoded);
    suppressDirty = true;
    if (editor) editor.destroy();
    editor = new Editor(document.getElementById("docx-canvas"), { main: [] }, {
      mode: "edit",
      pageMode: "paging",
      defaultFont: "Microsoft YaHei"
    });
    editor.use(docxPlugin);
    editor.listener.contentChange = setDirty;
    await Promise.resolve(editor.command.executeImportDocx({
      arrayBuffer: bytes.buffer
    }));
    suppressDirty = false;
    bridge.setDirty(false);
    bridge.editorLoaded(sessionId);
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

function dispose() {
  suppressDirty = true;
  loadingChunks = [];
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
  beginLoad(id, total) {
    dispose();
    sessionId = String(id || "");
    loadingExpected = Number(total) || 0;
    loadingChunks = new Array(loadingExpected);
  },
  appendLoadChunk(index, chunk) {
    loadingChunks[Number(index)] = String(chunk || "");
  },
  finishLoad() {
    if (!loadingExpected || loadingChunks.some((item) => typeof item !== "string")) {
      reportError("DOCX 分块传输不完整，无法进入编辑。");
      return;
    }
    loadDocument();
  },
  requestSave,
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

new QWebChannel(qt.webChannelTransport, (channel) => {
  bridge = channel.objects.deliverableEditorBridge;
  bridge.ready("docx");
});
