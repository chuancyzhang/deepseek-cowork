import { createUniver, LocaleType, mergeLocales } from "@univerjs/presets";
import { UniverSheetsCorePreset } from "@univerjs/preset-sheets-core";
import zhCN from "@univerjs/preset-sheets-core/locales/zh-CN";
import "@univerjs/preset-sheets-core/lib/index.css";

const CHUNK_SIZE = 192 * 1024;
let bridge = null;
let sessionId = "";
let loadingChunks = [];
let loadingExpected = 0;
let univer = null;
let univerAPI = null;
let workbook = null;
let commandListener = null;
let suppressDirty = true;
let cleanSnapshot = "";
let dirtyCheckScheduled = false;

function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

function reportError(error) {
  if (bridge) bridge.reportError(errorText(error));
}

function sheetEditorStylesReady() {
  try {
    const stylesheet = document.getElementById("sheet-editor-styles");
    return Boolean(stylesheet && stylesheet.sheet && stylesheet.sheet.cssRules.length);
  } catch (_error) {
    return false;
  }
}

function sendTextPayload(kind, value) {
  const total = Math.max(1, Math.ceil(value.length / CHUNK_SIZE));
  bridge.beginPayload(sessionId, kind, total);
  for (let index = 0; index < total; index += 1) {
    bridge.appendPayload(
      sessionId,
      index,
      value.slice(index * CHUNK_SIZE, (index + 1) * CHUNK_SIZE)
    );
  }
  bridge.finishPayload(sessionId);
}

function dispose() {
  suppressDirty = true;
  cleanSnapshot = "";
  dirtyCheckScheduled = false;
  if (commandListener && commandListener.dispose) commandListener.dispose();
  commandListener = null;
  if (workbook && workbook.dispose) workbook.dispose();
  workbook = null;
  if (univer && univer.dispose) univer.dispose();
  univer = null;
  univerAPI = null;
  loadingChunks = [];
  document.getElementById("sheet-root").replaceChildren();
}

function serializedWorkbook() {
  if (!workbook) throw new Error("表格模型尚未加载完成。");
  return JSON.stringify(workbook.save());
}

function scheduleDirtyCheck() {
  if (suppressDirty || dirtyCheckScheduled || !bridge || !workbook) return;
  dirtyCheckScheduled = true;
  queueMicrotask(() => {
    dirtyCheckScheduled = false;
    if (!suppressDirty && bridge && workbook) {
      bridge.setDirty(serializedWorkbook() !== cleanSnapshot);
    }
  });
}

function trackWorkbookMutation(command) {
  // Univer type 2 is MUTATION: persisted model data changed. Selection,
  // focus, scrolling, and other view-only operations must not mark dirty.
  if (command && command.type === 2) scheduleDirtyCheck();
}

function markClean() {
  cleanSnapshot = serializedWorkbook();
  if (bridge) bridge.setDirty(false);
}

function loadWorkbook() {
  try {
    const snapshot = JSON.parse(loadingChunks.join(""));
    loadingChunks = [];
    dispose();
    const created = createUniver({
      locale: LocaleType.ZH_CN,
      locales: { [LocaleType.ZH_CN]: mergeLocales(zhCN) },
      presets: [
        UniverSheetsCorePreset({
          container: "sheet-root",
          header: true,
          toolbar: true,
          footer: true,
          disableForceStringAlert: true,
          disableForceStringMark: true
        })
      ]
    });
    univer = created.univer;
    univerAPI = created.univerAPI;
    suppressDirty = true;
    workbook = univerAPI.createWorkbook(snapshot);
    commandListener = univerAPI.addEvent(
      univerAPI.Event.CommandExecuted,
      trackWorkbookMutation
    );
    window.setTimeout(() => {
      cleanSnapshot = serializedWorkbook();
      suppressDirty = false;
      bridge.setDirty(false);
      bridge.editorLoaded(sessionId);
    }, 0);
  } catch (error) {
    reportError(error);
  }
}

function requestSave() {
  try {
    sendTextPayload("sheet", serializedWorkbook());
  } catch (error) {
    reportError(error);
  }
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
      reportError("表格分块传输不完整，无法进入编辑。");
      return;
    }
    loadWorkbook();
  },
  requestSave,
  markClean,
  command() {},
  dispose,
  setTheme
};

new QWebChannel(qt.webChannelTransport, (channel) => {
  bridge = channel.objects.deliverableEditorBridge;
  if (!sheetEditorStylesReady()) {
    bridge.reportError("表格编辑器样式资源加载失败，请修复安装后重试。");
    return;
  }
  bridge.ready("sheet");
});
