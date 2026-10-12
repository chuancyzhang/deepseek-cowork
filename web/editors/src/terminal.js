import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

const terminal = new Terminal({ cursorBlink: true, scrollback: 10000, fontSize: 13,
  fontFamily: 'Consolas, "Microsoft YaHei", monospace', allowProposedApi: false,
  theme: { background: "#ffffff", foreground: "#242424", cursor: "#242424" } });
const fit = new FitAddon();
terminal.loadAddon(fit);
terminal.open(document.getElementById("terminal"));
let bridge = null;
let userInput = true;
let resizeScheduled = false;
function resize() {
  resizeScheduled = false;
  if (document.body.clientWidth < 10 || document.body.clientHeight < 10) return;
  fit.fit();
  if (bridge) bridge.resizeTerminal(terminal.rows, terminal.cols);
}
new ResizeObserver(() => {
  if (!resizeScheduled) { resizeScheduled = true; requestAnimationFrame(resize); }
}).observe(document.body);
terminal.onData(text => {
  if (!bridge) return;
  if (/^\x1b\[(?:\??[0-9;]*c|[0-9;]*[Rn])$/.test(text)) bridge.reply(text);
  else if (userInput) bridge.input(text);
});
function copySelection() {
  if (terminal.hasSelection()) bridge?.copy(terminal.getSelection());
}
terminal.attachCustomKeyEventHandler(event => {
  if (event.type !== "keydown") return true;
  const copy = event.ctrlKey && !event.altKey && !event.metaKey &&
    ((event.code === "KeyC" && (event.shiftKey || terminal.hasSelection())) ||
     (event.code === "Insert" && !event.shiftKey));
  const paste = !event.altKey && !event.metaKey &&
    ((event.ctrlKey && event.code === "KeyV") ||
     (event.shiftKey && !event.ctrlKey && event.code === "Insert"));
  if (copy || paste) {
    // Returning false only stops xterm; also cancel Chromium's native clipboard
    // action so it cannot overwrite the selection or paste the same text twice.
    event.preventDefault();
    event.stopPropagation();
    if (copy) copySelection();
    else if (userInput) bridge?.paste();
    return false;
  }
  return true;
});
document.getElementById("terminal").addEventListener("contextmenu", event => {
  event.preventDefault();
  bridge?.contextMenu(terminal.getSelection());
});
window.coworkTerminal = {
  write(text, cursor) { terminal.write(text, () => bridge?.ack(cursor)); },
  selectAll() { terminal.selectAll(); },
  paste(text) { if (userInput) { terminal.paste(text); terminal.focus(); } },
  setInput(enabled) { userInput = !!enabled; terminal.options.disableStdin = !userInput; },
  setTheme(theme, fontFamily, fontSize) {
    terminal.options.theme = theme;
    terminal.options.fontFamily = fontFamily;
    terminal.options.fontSize = fontSize;
    resize();
  },
  fit: resize,
  focus() { terminal.focus(); }
};
new QWebChannel(qt.webChannelTransport, channel => {
  bridge = channel.objects.terminalBridge;
  bridge.ready(); resize();
});
