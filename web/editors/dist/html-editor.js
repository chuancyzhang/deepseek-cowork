(function () {
  "use strict";

  const CHUNK_SIZE = 192 * 1024;
  const frame = document.getElementById("html-frame");
  let bridge = null;
  let sessionId = "";
  let loadingChunks = [];
  let loadingExpected = 0;
  let suppressDirty = true;

  function reportError(error) {
    if (bridge) {
      bridge.reportError(error instanceof Error ? error.message : String(error));
    }
  }

  function editableDocument() {
    const doc = frame.contentDocument;
    if (!doc) throw new Error("HTML 隔离编辑页尚未加载。");
    return doc;
  }

  function markDirty() {
    if (!suppressDirty && bridge) bridge.setDirty(true);
  }

  function attachEditor() {
    const doc = editableDocument();
    doc.designMode = "on";
    doc.body.contentEditable = "true";
    doc.body.spellcheck = true;
    doc.body.dataset.coworkEditing = "true";
    doc.addEventListener("input", markDirty);
    doc.addEventListener("change", markDirty);
    suppressDirty = false;
    bridge.setDirty(false);
    bridge.editorLoaded(sessionId);
  }

  function loadDocument() {
    try {
      const content = loadingChunks.join("");
      loadingChunks = [];
      suppressDirty = true;
      frame.onload = attachEditor;
      frame.srcdoc = content;
    } catch (error) {
      reportError(error);
    }
  }

  function command(name, value) {
    try {
      const doc = editableDocument();
      doc.execCommand(name, false, value || null);
      frame.contentWindow.focus();
    } catch (error) {
      reportError(error);
    }
  }

  function requestSave() {
    try {
      const doc = editableDocument();
      doc.body.removeAttribute("contenteditable");
      doc.body.removeAttribute("spellcheck");
      delete doc.body.dataset.coworkEditing;
      const doctype = doc.doctype
        ? `<!DOCTYPE ${doc.doctype.name}>`
        : "<!doctype html>";
      const value = `${doctype}\n${doc.documentElement.outerHTML}`;
      doc.body.contentEditable = "true";
      doc.body.spellcheck = true;
      doc.body.dataset.coworkEditing = "true";
      const total = Math.max(1, Math.ceil(value.length / CHUNK_SIZE));
      bridge.beginPayload(sessionId, "html", total);
      for (let index = 0; index < total; index += 1) {
        bridge.appendPayload(
          sessionId,
          index,
          value.slice(index * CHUNK_SIZE, (index + 1) * CHUNK_SIZE)
        );
      }
      bridge.finishPayload(sessionId);
    } catch (error) {
      reportError(error);
    }
  }

  function dispose() {
    suppressDirty = true;
    loadingChunks = [];
    frame.onload = null;
    frame.removeAttribute("srcdoc");
    frame.src = "about:blank";
  }

  function setTheme(theme) {
    Object.entries(theme || {}).forEach(([key, value]) => {
      document.documentElement.style.setProperty(`--cowork-${key}`, value);
    });
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
        reportError("HTML 分块传输不完整，无法进入编辑。");
        return;
      }
      loadDocument();
    },
    requestSave,
    command,
    dispose,
    setTheme
  };

  document.querySelector(".editor-toolbar").addEventListener("click", (event) => {
    const commandButton = event.target.closest("button[data-html-command]");
    if (commandButton) command(commandButton.dataset.htmlCommand);
    const actionButton = event.target.closest("button[data-html-action='link']");
    if (actionButton) {
      const url = window.prompt("输入链接地址");
      if (url) command("createLink", url);
    }
  });
  document.querySelectorAll("select[data-html-command], input[data-html-command]")
    .forEach((control) => {
      control.addEventListener("change", () => {
        command(control.dataset.htmlCommand, control.value);
      });
    });

  new QWebChannel(qt.webChannelTransport, (channel) => {
    bridge = channel.objects.deliverableEditorBridge;
    bridge.ready("html");
  });
}());
