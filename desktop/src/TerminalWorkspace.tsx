import { useCallback, useEffect, useRef, useState } from 'react';
import { Hand, Play, Square } from 'lucide-react';
import type { WorkbenchTab } from './Workspace';

const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
type Tab = { id: string; title: string; cwd: string; owner: string; control_revision: number; status: string };

function TerminalView({ cid, tab, dark, error }: { cid: string; tab: Tab; dark: boolean; error: (message: string) => void }) {
  const frame = useRef<HTMLIFrameElement>(null), current = useRef({ tab, error, dark });
  current.current = { tab, error, dark };
  const [status, setStatus] = useState('starting');
  const send = useCallback((method: string, ...args: any[]) => frame.current?.contentWindow?.postMessage({ channel: 'cowork-editor-host-v1', method, args }, '*'), []);
  const theme = useCallback(() => send('setTheme', { background: current.current.dark ? '#1d2025' : '#fff', foreground: current.current.dark ? '#e6e8eb' : '#242424', cursor: current.current.dark ? '#e6e8eb' : '#242424' }, 'Consolas, "Microsoft YaHei", monospace', 13), [send]);
  useEffect(() => { send('setInput', tab.owner === 'human'); theme(); }, [tab.owner, dark, send, theme]);
  useEffect(() => {
    let stopped = false, cursor = 0, awaitingAck = false, started = false, chain = Promise.resolve();
    let timer: ReturnType<typeof setTimeout>;
    const run = (method: string, params: any = {}) => call(method, { conversation_id: cid, tab_id: tab.id, ...params });
    const fail = (failure: unknown) => { if (!stopped) current.current.error((failure as Error).message); };
    async function poll() {
      if (stopped || awaitingAck) return;
      try {
        const value = await run('workbench.read', { cursor });
        if (stopped) return;
        setStatus(value.status);
        if (value.error) { current.current.error(value.error); return; }
        if (value.output) {
          awaitingAck = true;
          send('write', (value.truncated ? '\r\n[较早的输出已超出缓冲区]\r\n' : '') + value.output, value.cursor);
        } else if (['starting', 'running'].includes(value.status)) timer = setTimeout(poll, 200);
      } catch (failure) { fail(failure); }
    }
    const listener = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.channel !== 'cowork-editor-v1') return;
      const { method, args = [] } = event.data;
      if (method === 'ready' && !started) { started = true; theme(); send('setInput', current.current.tab.owner === 'human'); void poll(); }
      if (method === 'ack' && awaitingAck) { cursor = Number(args[0]); awaitingAck = false; timer = setTimeout(poll, 20); }
      if (method === 'resizeTerminal') void run('workbench.resize', { rows: args[0], cols: args[1] }).catch(fail);
      if (method === 'reply') chain = chain.then(() => stopped ? undefined : run('workbench.reply', { text: args[0] })).catch(fail);
      if (method === 'input' && current.current.tab.owner === 'human') {
        const revision = current.current.tab.control_revision;
        // Preserve paste/keystroke ordering; never replay uncertain input.
        chain = chain.then(() => stopped ? undefined : run('workbench.input', { text: args[0], control_revision: revision })).catch(fail);
      }
      if (method === 'copy') void call('clipboard.write', { text: args[0] }).catch(fail);
      if (method === 'paste') void call<string>('clipboard.read').then(value => { if (!stopped) send('paste', value); }).catch(fail);
      if (method === 'contextMenu' && args[0]) void call('clipboard.write', { text: args[0] }).catch(fail);
      if (method === 'reportError') current.current.error(String(args[0]));
    };
    const unsubscribe = window.cowork.onEvents(events => { if (events.some(event => event.event === 'service_reconnected') && started) { clearTimeout(timer); void poll(); } });
    window.addEventListener('message', listener);
    return () => { stopped = true; clearTimeout(timer); unsubscribe(); window.removeEventListener('message', listener); };
  }, [cid, tab.id, send, theme]);
  return <div className="terminal-view"><div className="terminal-state"><span title={tab.cwd}>{tab.cwd}</span><span>{({ starting: '启动中', running: '运行中', exited: '已结束', closed: '已关闭', failed: '启动失败' } as Record<string, string>)[status]}</span></div><iframe ref={frame} title={tab.title} sandbox="allow-scripts allow-same-origin" src="cowork-editor://builtin/terminal.html"/></div>;
}

export default function TerminalWorkspace({ cid, tab, dark, error, refresh }: { cid: string; tab: WorkbenchTab; dark: boolean; error: (message: string) => void; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  async function action(method: string, params: any = {}) {
    setBusy(true);
    try { await call(method, {conversation_id: cid, tab_id: tab.id, ...params}); await refresh(); }
    catch (failure) { error((failure as Error).message); } finally { setBusy(false); }
  }
  return <div className="terminal-workspace"><div className="terminal-toolbar"><span>{tab.owner === 'human' ? '由你操作' : 'AI 可操作'}</span><button className="secondary" disabled={busy} onClick={() => void action('workbench.control', {owner: tab.owner === 'human' ? 'available' : 'human'})}>{tab.owner === 'human' ? <Play size={14}/> : <Hand size={14}/>} {tab.owner === 'human' ? '交还 AI' : '接管'}</button><button className="icon-button" title="中断当前命令" disabled={busy || tab.owner !== 'human'} onClick={() => void action('workbench.input', {text: '\x03', control_revision: tab.control_revision})}><Square size={13}/></button></div><TerminalView cid={cid} tab={tab} dark={dark} error={error}/></div>;
}
