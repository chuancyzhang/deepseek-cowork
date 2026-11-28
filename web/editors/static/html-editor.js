(function () {
  "use strict";

  const frame = document.getElementById("html-frame");
  let bridge = null;
  let sessionId = "";
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
    if (!suppressDirty && bridge) bridge.setDirty(sessionId, true);
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
    bridge.setDirty(sessionId, false);
    bridge.editorLoaded(sessionId);
  }

  function loadDocument(content) {
    try {
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
      bridge.payload(sessionId, "html", value);
    } catch (error) {
      reportError(error);
    }
  }

  function dispose() {
    suppressDirty = true;
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
    load(id, value) {
      dispose();
      sessionId = id;
      return loadDocument(value);
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

  window.connectDesktopBridge("deliverableEditorBridge", nativeBridge => {
    bridge = nativeBridge;
    bridge.ready("html");
  });
}());
