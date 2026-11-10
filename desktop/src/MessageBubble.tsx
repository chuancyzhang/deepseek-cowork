import { memo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Check, ChevronDown, Copy, ExternalLink, FileText, GitBranch, LoaderCircle, Pencil, RefreshCw, Sparkles, Trash2, Wrench } from 'lucide-react';
import type { Message } from './types';
import { messageText } from './runtime';
import InlineVisualization from './InlineVisualization';
import { markdownFilePath, markdownUrl } from './attachments';

function CodeBlock({ children }: { children?: React.ReactNode }) {
  const source = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  return <div className="code-block"><button title="复制代码" onClick={async () => {
    await navigator.clipboard.writeText(source.current?.textContent || ''); setCopied(true); setTimeout(() => setCopied(false), 1500);
  }}>{copied ? <Check size={14}/> : <Copy size={14}/>}</button><pre ref={source}>{children}</pre></div>;
}

export function Markdown({ text, onFile }: { text: string; onFile?: (path: string) => void }) {
  return <ReactMarkdown urlTransform={markdownUrl} remarkPlugins={[remarkGfm]} components={{
    pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
    a: ({ href, children }) => <button className="text-link" onClick={() => {
      if (!href) return;
      if (/^https?:/i.test(href)) void window.cowork.call('external.open', { url: href });
      else if (onFile) onFile(markdownFilePath(href));
    }}>{children}{/^https?:/i.test(href || '') && <ExternalLink size={12}/>}</button>,
    img: ({ src, alt }) => <button className="inline-file" onClick={() => src && onFile?.(src)}><FileText size={16}/>{alt || '查看图片'}</button>
  }}>{text}</ReactMarkdown>;
}

function Disclosure({ title, children, kind = 'tool-entry' }: { title: React.ReactNode; children: React.ReactNode; kind?: string }) {
  const [open, setOpen] = useState(false);
  return <details className={kind} onToggle={event => setOpen(event.currentTarget.open)}><summary>{title}<ChevronDown size={13}/></summary>{open && children}</details>;
}

export default memo(function MessageBubble({ message, onAction, onFile, active, cid }: {
  message: Message; active: boolean; cid: string; onAction: (action: string, message: Message) => void; onFile: (path: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const text = message.role === 'user' && typeof message.meta?.display_content === 'string' ? message.meta.display_content : messageText(message.content);
  const files = message.meta?.desktop_attachments || (message.meta?.user_added_files || []).map((path: string) => ({ path, name: path.split(/[\\/]/).pop() }));
  if (message.role === 'tool') return <button className="message-tool-link" onClick={() => onAction('tool', message)}><Wrench size={14}/><span>{message.meta?.tool_name || '工具结果'}</span><ChevronDown size={13}/></button>;
  return <article id={'message-' + message.id} className={`message ${message.role}`}>
    {message.role === 'assistant' && <div className="assistant-name"><span className="brand-symbol small">✳</span>{message.meta?.conversation_agent?.name || message.meta?.ui_conversation_agent?.name || 'Cowork'}{message.live && <LoaderCircle className="spin" size={13}/>}</div>}
    {!!message.reasoning_content && <Disclosure kind="thinking" title={<><Sparkles size={13}/>深度思考</>}><div className="markdown"><Markdown text={message.reasoning_content} onFile={onFile}/></div></Disclosure>}
    {!!message.tool_calls?.length && <div className="message-tool-calls">{message.tool_calls.map((tool, index) => <button className="message-tool-link" key={tool.id || index} onClick={() => onAction('tool', {...message, tool_call_id: tool.id})}><Wrench size={14}/><span>{tool.function?.name || tool.name || '工具调用'}</span><ChevronDown size={13}/></button>)}</div>}
    {!!files.length && <div className="message-files">{files.map((file: any) => <button key={file.path} title={file.path} onClick={() => onFile(file.path)}><FileText size={14}/>{file.name}</button>)}</div>}
    {text && <div className="markdown">{message.role === 'user' ? <div className="user-text">{text}</div> : text.split(/^::cowork-inline-vis\{file="([a-z0-9][a-z0-9.-]*\.html)"\}\s*$/m).map((part, index) => index % 2 ? <InlineVisualization key={part} cid={cid} file={part}/> : <Markdown key={index} text={part} onFile={onFile}/>)}</div>}
    <div className="message-actions">
      <button title="复制消息" onClick={async () => { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>{copied ? <Check size={14}/> : <Copy size={14}/>}</button>
      {message.role === 'user' && !message.meta?.same_turn_guidance && <><button title="编辑消息" disabled={active} onClick={() => onAction('edit', message)}><Pencil size={14}/></button><button title="删除此处及后续对话" disabled={active} onClick={() => onAction('delete', message)}><Trash2 size={14}/></button></>}
      {message.role === 'assistant' && !message.live && <><button title="重新生成" disabled={active} onClick={() => onAction('retry', message)}><RefreshCw size={14}/></button><button title="从这里创建分支" onClick={() => onAction('branch', message)}><GitBranch size={14}/></button></>}
    </div>
  </article>;
});
