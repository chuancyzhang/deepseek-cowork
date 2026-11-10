import { useEffect, useRef, useState } from 'react';
import type { FileSession } from './types';

export default function OfficeEditor({ file, text, onChange, error, register }: { file: FileSession; text: string; onChange: (text: string) => void;
  error: (message: string) => void; register: (path: string, flush?: () => Promise<string>) => void }) {
  const frame = useRef<HTMLIFrameElement>(null), latest = useRef({ text, onChange, error });
  latest.current = { text, onChange, error };
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let loaded = false, dirty = false, chunks: string[] = [], expected = 0, kind = '', timer: ReturnType<typeof setTimeout>;
    let pending: { resolve: (text: string) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }[] = [];
    let exporting = false, generation = 0, exportedGeneration = 0;
    const send = (method: string, ...args: any[]) => frame.current?.contentWindow?.postMessage({ channel: 'cowork-editor-host-v1', method, args }, '*');
    function flush(): Promise<string> {
      if (!loaded) return Promise.resolve(latest.current.text);
      if (!dirty) return Promise.resolve(latest.current.text);
      return new Promise((resolve, reject) => {
        const entry = { resolve, reject, timer: setTimeout(() => { pending = pending.filter(item => item !== entry); exporting = false; reject(new Error('编辑器导出超时，当前文档仍保留，请重试。')); }, 30000) };
        pending.push(entry);
        if (!exporting) { exporting = true; exportedGeneration = generation; send('requestSave'); }
      });
    }
    register(file.path, flush);
    const listener = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.channel !== 'cowork-editor-v1') return;
      const { method, args = [] } = event.data;
      if (method === 'ready') {
        try {
        let payload = file.payload;
        if (latest.current.text) {
          const saved = JSON.parse(latest.current.text);
          payload = saved.kind === 'docx_review' ? { ...file.payload, snapshot: { ...file.payload.snapshot, coworkDraft: JSON.parse(saved.value) } }
            : saved.kind === 'docx' ? { kind: 'docx', binary: saved.value } : { kind: saved.kind, snapshot: JSON.parse(saved.value) };
        }
        const content = payload.kind === 'docx' ? payload.binary : JSON.stringify(payload.snapshot);
        const size = 192 * 1024, count = Math.max(1, Math.ceil(content.length / size));
        send('beginLoad', file.id, count);
        for (let i = 0; i < count; i++) send('appendLoadChunk', i, content.slice(i*size, (i+1)*size));
        send('finishLoad');
        } catch (failure) { latest.current.error('文档草稿无法载入，已保留原始内容：' + (failure as Error).message); }
      } else if (method === 'editorLoaded') { loaded = true; setReady(true); }
      else if (method === 'setDirty' && args[0]) {
        dirty = true; generation++; clearTimeout(timer);
        timer = setTimeout(() => { void flush().catch(failure => latest.current.error(failure.message)); }, 1200);
      } else if (method === 'beginPayload' && args[0] === file.id) {
        kind = args[1]; expected = args[2];
        if (!Number.isInteger(expected) || expected < 1 || expected > 1000) { latest.current.error('编辑器返回的文档过大。'); return; }
        chunks = new Array(expected);
      } else if (method === 'appendPayload' && args[0] === file.id && Number.isInteger(args[1]) && args[1] >= 0 && args[1] < expected && typeof args[2] === 'string') chunks[args[1]] = args[2];
      else if (method === 'finishPayload' && args[0] === file.id) {
        if (!expected || Array.from(chunks).some(chunk => typeof chunk !== 'string')) { latest.current.error('文档传输不完整，请重新保存。'); return; }
        const value = JSON.stringify({ kind, value: chunks.join('') }); chunks = []; expected = 0; exporting = false;
        dirty = generation !== exportedGeneration;
        latest.current.text = value; latest.current.onChange(value);
        if (dirty) { exporting = true; exportedGeneration = generation; send('requestSave'); }
        else { for (const entry of pending) { clearTimeout(entry.timer); entry.resolve(value); } pending = []; }
      } else if (method === 'reportError') {
        exporting = false; const failure = new Error(String(args[0]));
        for (const entry of pending) { clearTimeout(entry.timer); entry.reject(failure); } pending = [];
        latest.current.error(failure.message);
      }
    };
    window.addEventListener('message', listener);
    return () => { clearTimeout(timer); register(file.path); window.removeEventListener('message', listener); for (const entry of pending) { clearTimeout(entry.timer); entry.reject(new Error('编辑器已关闭，未保存草稿仍保留。')); } };
  }, [file.id]);
  const page = file.payload.kind === 'docx_review' ? 'docx-review' : file.payload.kind;
  return <div className="plugin-editor">{!ready && <div className="editor-loading">正在打开文档…</div>}<iframe ref={frame} title={file.name} sandbox="allow-scripts allow-same-origin" src={`cowork-editor://builtin/${page}.html`}/></div>;
}
