let bridge = null;
let sessionId = "";
let chunks = [];
let snapshot = null;
let state = null;
let cleanState = "";
let savedPayload = "";
let history = [];
let historyIndex = 0;
let lastEdit = { region: "", time: 0 };
const documentView = document.getElementById("word-document");
const revisionView = document.getElementById("word-revisions");

const clone = value => JSON.parse(JSON.stringify(value));
const serialized = () => JSON.stringify(state);
function notice(message = "") {
  const target = document.getElementById("word-notice");
  target.textContent = message;
  target.hidden = !message;
}
function updateDirty() {
  bridge?.setDirty(serialized() !== cleanState);
  document.getElementById("undo").disabled = historyIndex === 0;
  document.getElementById("redo").disabled = historyIndex >= history.length - 1;
}
function remember(region = "") {
  const now = Date.now();
  const coalesce = region && lastEdit.region === region && now - lastEdit.time < 700 && historyIndex > 0;
  history.splice(historyIndex + 1);
  if (coalesce) history[historyIndex] = clone(state);
  else { history.push(clone(state)); historyIndex += 1; }
  if (history.length > 80) { history.shift(); historyIndex -= 1; }
  lastEdit = { region, time: now };
  updateDirty();
}
function undo(direction) {
  const target = historyIndex + direction;
  if (target < 0 || target >= history.length) return;
  historyIndex = target;
  state = clone(history[target]);
  lastEdit = { region: "", time: 0 };
  renderDocument();
  renderRevisions();
  updateDirty();
}
function jump(identifier) {
  const section = [...documentView.querySelectorAll("section")].find(item => item.dataset.section === identifier);
  section?.scrollIntoView({ block: "start", behavior: "smooth" });
  section?.querySelector('[contenteditable="true"]')?.focus({ preventScroll: true });
}
function renderDocument() {
  const top = documentView.scrollTop;
  documentView.replaceChildren();
  for (const section of snapshot.sections) {
    const element = document.createElement("section");
    element.className = "word-section";
    element.dataset.section = section.id;
    const title = document.createElement("h2");
    title.textContent = section.title;
    element.append(title);
    const content = document.createElement("div");
    // Only the local OOXML projection builds this escaped, fixed-tag markup.
    // Edits are sent as text and IDs, never as HTML to the save worker.
    content.innerHTML = section.html;
    element.append(content);
    documentView.append(element);
  }
  for (const region of documentView.querySelectorAll("[data-region]")) {
    const id = region.dataset.region;
    if (state.regions[id] !== snapshot.regions[id]) region.textContent = state.regions[id];
  }
  for (const change of documentView.querySelectorAll("[data-revision]")) {
    const info = snapshot.revisions.find(item => item.id === change.dataset.revision);
    const decision = state.revisions[info.id];
    const insertion = ["ins", "moveTo"].includes(info.kind);
    const discard = decision && insertion !== (decision === "accept");
    change.hidden = Boolean(discard);
    change.classList.toggle("pending", !decision);
    change.classList.toggle("resolved", Boolean(decision));
    change.title = decision ? "已处理，保存后生效" : `${info.label}修订 · ${info.author || "未署名"} · 在修订面板中处理`;
  }
  for (const region of documentView.querySelectorAll("[data-region]")) {
    let node = region.parentElement;
    let editable = true;
    while (node && node !== documentView) {
      if (node.dataset.revision && !state.revisions[node.dataset.revision]) editable = false;
      node = node.parentElement;
    }
    region.contentEditable = String(editable);
    region.setAttribute("role", "textbox");
    region.setAttribute("aria-label", "编辑段落文字");
    region.setAttribute("aria-multiline", "true");
  }
  documentView.scrollTop = top;
}
function actionButton(label, callback) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", callback);
  return button;
}
function decide(identifier, decision) {
  if (decision) state.revisions[identifier] = decision;
  else delete state.revisions[identifier];
  remember();
  renderDocument();
  renderRevisions();
}
function decideAll(decision) {
  for (const revision of snapshot.revisions) if (revision.supported) state.revisions[revision.id] = decision;
  remember(); renderDocument(); renderRevisions();
}
function renderRevisions() {
  revisionView.replaceChildren();
  const title = document.createElement("h2");
  const pending = snapshot.revisions.filter(item => !state.revisions[item.id]).length;
  title.textContent = `修订 · ${pending} 项待处理`;
  document.getElementById("toggle-revisions").textContent = `修订（${pending}）`;
  revisionView.append(title);
  if (!snapshot.revisions.length) {
    const empty = document.createElement("p");
    empty.textContent = "文档没有待处理的修订。";
    revisionView.append(empty);
    return;
  }
  const actions = document.createElement("div");
  actions.className = "word-review-actions";
  const limited = snapshot.revisions.some(item => !item.supported);
  if (limited) {
    const explanation = document.createElement("p");
    explanation.textContent = "移动、单元格合并和编号等复杂修订会原样保留，需在 Word 中处理。批量操作仅处理下方可操作的修订。";
    revisionView.append(explanation);
  }
  actions.append(actionButton(limited ? "接受可处理项" : "接受全部", () => decideAll("accept")), actionButton(limited ? "拒绝可处理项" : "拒绝全部", () => decideAll("reject")));
  revisionView.append(actions);
  for (const item of snapshot.revisions) {
    const article = document.createElement("section");
    article.className = "word-revision-item";
    article.dataset.change = item.id;
    const heading = document.createElement("strong");
    const choice = state.revisions[item.id];
    heading.textContent = `${item.label} · ${choice === "accept" ? "已接受" : choice === "reject" ? "已拒绝" : "待处理"}`;
    const meta = document.createElement("p");
    meta.className = "word-revision-meta";
    const location = item.part.includes("footnotes") ? "脚注" : item.part.includes("endnotes") ? "尾注" : "正文";
    meta.textContent = [location, item.author, item.date].filter(Boolean).join(" · ");
    const text = document.createElement("p");
    text.textContent = item.text || "段落、表格或文字格式的修改";
    const buttons = document.createElement("div");
    buttons.className = "word-review-actions";
    if (item.supported) buttons.append(actionButton("接受", () => decide(item.id, "accept")), actionButton("拒绝", () => decide(item.id, "reject")));
    else buttons.textContent = "原样保留 · 请在 Word 中处理";
    if (choice) buttons.append(actionButton("保留修订", () => decide(item.id, "")));
    article.append(heading, meta, text, buttons);
    revisionView.append(article);
  }
}
function textValue(node) {
  return node.innerText.replace(/\r\n?/g, "\n");
}
function currentRegion(node) {
  const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
  return element?.closest('[data-region][contenteditable="true"]');
}
documentView.addEventListener("beforeinput", event => {
  if (event.inputType.startsWith("format")) {
    event.preventDefault();
    notice("这里支持文字编辑，并保留原有格式；调整版式或格式请在 Word 中完成。");
    return;
  }
  const selection = window.getSelection();
  if (selection?.rangeCount && currentRegion(selection.anchorNode) !== currentRegion(selection.focusNode)) {
    event.preventDefault();
    notice("请在同一段文字内修改。脚注编号和待处理修订可使用对应入口操作。");
  }
});
documentView.addEventListener("drop", event => {
  event.preventDefault();
  notice("请使用复制和粘贴修改文字，以保留脚注编号与修订位置。");
});
documentView.addEventListener("input", event => {
  const region = event.target.closest("[data-region]");
  if (!region) return;
  state.regions[region.dataset.region] = textValue(region);
  notice(); remember(region.dataset.region);
});
documentView.addEventListener("paste", event => {
  if (!event.target.closest('[contenteditable="true"]')) return;
  event.preventDefault();
  document.execCommand("insertText", false, event.clipboardData.getData("text/plain"));
});
documentView.addEventListener("click", event => {
  const reference = event.target.closest("[data-note]");
  if (reference) { jump(reference.dataset.note); return; }
  const revision = event.target.closest("[data-revision].pending");
  if (revision) {
    revisionView.hidden = false;
    document.getElementById("toggle-revisions").setAttribute("aria-expanded", "true");
    [...revisionView.querySelectorAll("[data-change]")]
      .find(item => item.dataset.change === revision.dataset.revision)
      ?.scrollIntoView({ block: "nearest" });
  }
});
document.addEventListener("keydown", event => {
  if (!(event.ctrlKey || event.metaKey)) return;
  const key = event.key.toLowerCase();
  if (key === "z" || key === "y") {
    event.preventDefault(); undo(key === "y" || event.shiftKey ? 1 : -1);
  }
});
document.getElementById("undo").addEventListener("click", () => undo(-1));
document.getElementById("redo").addEventListener("click", () => undo(1));
document.getElementById("sections").addEventListener("change", event => jump(event.target.value));
document.getElementById("toggle-revisions").addEventListener("click", event => {
  revisionView.hidden = !revisionView.hidden;
  event.target.setAttribute("aria-expanded", String(!revisionView.hidden));
  if (!revisionView.hidden) revisionView.scrollIntoView({ block: "nearest" });
});
window.coworkEditor = {
  beginLoad(id, total) { sessionId = String(id); chunks = new Array(total); },
  appendLoadChunk(index, value) { chunks[index] = String(value); },
  finishLoad() {
    try {
      if (!chunks.length || chunks.includes(undefined)) throw new Error("Word 编辑内容传输不完整，请重新打开。");
      snapshot = JSON.parse(chunks.join("")); chunks = [];
      state = { version: 1, regions: clone(snapshot.regions), revisions: {} };
      cleanState = serialized(); savedPayload = ""; history = [clone(state)]; historyIndex = 0;
      lastEdit = { region: "", time: 0 };
      const sections = document.getElementById("sections"); sections.replaceChildren();
      for (const item of snapshot.sections) {
        const option = document.createElement("option"); option.value = item.id; option.textContent = item.title; sections.append(option);
      }
      renderDocument(); renderRevisions(); updateDirty();
      revisionView.hidden = true;
      document.getElementById("toggle-revisions").setAttribute("aria-expanded", String(!revisionView.hidden));
      bridge.editorLoaded(sessionId);
    } catch (error) { bridge.reportError(String(error.message || error)); }
  },
  requestSave() {
    if (!state) return;
    const payload = serialized();
    savedPayload = payload;
    const size = 192 * 1024;
    const count = Math.max(1, Math.ceil(payload.length / size));
    bridge.beginPayload(sessionId, "docx_review", count);
    for (let index = 0; index < count; index += 1) bridge.appendPayload(sessionId, index, payload.slice(index * size, (index + 1) * size));
    bridge.finishPayload(sessionId);
  },
  markClean() { cleanState = savedPayload || serialized(); savedPayload = ""; lastEdit = { region: "", time: 0 }; updateDirty(); },
  setTheme(theme) { for (const [key, value] of Object.entries(theme || {})) document.documentElement.style.setProperty(`--cowork-${key}`, value); },
  dispose() { snapshot = state = null; chunks = []; history = []; documentView.replaceChildren(); revisionView.replaceChildren(); }
};
new QWebChannel(qt.webChannelTransport, channel => {
  bridge = channel.objects.deliverableEditorBridge;
  bridge.ready("docx_review");
});
