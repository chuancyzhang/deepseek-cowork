import { useEffect, useRef, useState } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { FileSession, Plugin } from './types';

export default function PluginEditor({ file, plugin, text, theme, onChange, onSave, error }: {
  file: FileSession; plugin: Plugin; text: string; theme: string;
  onChange: (text: string) => void; onSave: () => Promise<void>; error: (message: string) => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const current = useRef({ text, onChange, onSave, theme, file });
  current.current = { text, onChange, onSave, theme, file };
  const [ready, setReady] = useState(false);
  useEffect(() => { setReady(false); }, [plugin.hash, file.id]);
  useEffect(() => {
    const listener = async (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.channel !== 'cowork-plugin-v1') return;
      const { method, params, id } = event.data;
      const reply = (result: any, failure?: string) => frame.current?.contentWindow?.postMessage({ channel: 'cowork-host-v1', id, result, error: failure }, '*');
      try {
        if (method === 'ready') {
          setReady(true);
          frame.current?.contentWindow?.postMessage({ channel: 'cowork-host-v1', event: 'document', data: { name: current.current.file.name, text: current.current.text, theme: current.current.theme } }, '*');
        } else if (method === 'document.change') {
          if (!plugin.capabilities.includes('document.edit')) throw new Error('插件未声明编辑能力。');
          if (typeof params?.text !== 'string' || params.text.length > 10 * 1024 * 1024) throw new Error('编辑内容无效或过大。');
          current.current.text = params.text; current.current.onChange(params.text); reply({ saved: false });
        } else if (method === 'document.save') {
          if (!plugin.capabilities.includes('document.edit')) throw new Error('插件未声明编辑能力。');
          await current.current.onSave(); reply({ saved: true });
        } else if (method === 'document.read') {
          if (!plugin.capabilities.includes('document.read')) throw new Error('插件未声明读取能力。');
          reply({ text: current.current.text, name: current.current.file.name });
        } else if (method === 'document.render') {
          if (!plugin.capabilities.includes('document.read')) throw new Error('插件未声明读取能力。');
          // Parse only the current document; raw HTML and remote resources are
          // deliberately not executable in an editor or its isolated preview.
          reply({ html: renderToStaticMarkup(<ReactMarkdown remarkPlugins={[remarkGfm]} components={{
            a: ({children}) => <span className="document-link">{children}</span>,
            img: ({alt}) => <span>[图片：{alt || '未命名'}]</span>,
          }}>{current.current.text}</ReactMarkdown>) });
        } else throw new Error('插件请求了不支持的操作。');
      } catch (failure) { const message = String((failure as Error).message); reply(null, message); error(message); }
    };
    window.addEventListener('message', listener);
    return () => window.removeEventListener('message', listener);
  }, [plugin, error]);
  useEffect(() => { if (ready) frame.current?.contentWindow?.postMessage({ channel: 'cowork-host-v1', event: 'theme', data: theme }, '*'); }, [theme, ready]);
  return <div className="plugin-editor">{!ready && <div className="editor-loading">正在打开编辑器…</div>}<iframe ref={frame} key={`${file.id}:${plugin.hash}`} title={`${plugin.name} · ${file.name}`} sandbox="allow-scripts" src={plugin.url} /></div>;
}
