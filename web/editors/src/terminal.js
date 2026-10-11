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
terminal.attachCustomKeyEventHandler(event => {
  if (event.type !== "keydown") return true;
  if ((event.ctrlKey && event.shiftKey && event.code === "KeyC") ||
      (event.ctrlKey && event.code === "KeyC" && terminal.hasSelection())) {
    bridge?.copy(terminal.getSelection()); return false;
  }
  if (event.ctrlKey && event.shiftKey && event.code === "KeyV") {
    if (userInput) bridge?.paste(); return false;
  }
  return true;
});
window.coworkTerminal = {
  write(text, cursor) { terminal.write(text, () => bridge?.ack(cursor)); },
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
