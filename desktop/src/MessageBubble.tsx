import { memo, useRef, useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { BookOpen, Check, ChevronDown, ChevronRight, Copy, ExternalLink, FileText, GitBranch, Link, LoaderCircle, MessageSquare, MoreHorizontal, Pencil, Puzzle, RefreshCw, Sparkles, Trash2, Wrench } from 'lucide-react';
import type { FileReference, Message } from './types';
import { messageText, messageTime, replyParts, elapsedTime, thinkingTime, formatDuration, type TurnTiming, type QuestionRecord } from './runtime';
import {useHostField} from './host/context';
import InlineVisualization from './InlineVisualization';
import { markdownFilePath, markdownUrl, remarkFileReferences } from './attachments';
import Interaction from './Interaction';

function CodeBlock({ children }: { children?: React.ReactNode }) {
  const source = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  const [failure, setFailure] = useState('');
  return <div className="code-block"><button title="复制代码" onClick={async () => {
    try {await window.cowork.call('clipboard.write', {text: source.current?.textContent || ''}); setCopied(true); setFailure(''); setTimeout(() => setCopied(false), 1500);}
    catch (error) {setFailure('复制未完成：' + (error as Error).message);}
  }}>{copied ? <Check size={14}/> : <Copy size={14}/>}</button><pre ref={source}>{children}</pre>{failure && <p className="error-text" role="alert">{failure}</p>}</div>;
}

export function Markdown({ text, onFile, files = [] }: { text: string; onFile?: (path: string) => void; files?: FileReference[] }) {
  return <ReactMarkdown urlTransform={markdownUrl} remarkPlugins={[remarkGfm, [remarkFileReferences, files]]} components={{
    pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
    a: ({ href, children }) => <button className="text-link" onClick={() => {
      if (!href) return;
      if (/^https?:/i.test(href)) void window.cowork.call('external.open', { url: href });
      else if (onFile) onFile(markdownFilePath(href));
    }}>{children}{/^https?:/i.test(href || '') && <ExternalLink size={12}/>}</button>,
    img: ({ src, alt }) => <button className="inline-file" onClick={() => src && onFile?.(src)}><FileText size={16}/>{alt || '查看图片'}</button>
  }}>{text}</ReactMarkdown>;
}

function Disclosure({ title, children, stateKey, kind = 'tool-entry' }: { title: React.ReactNode; children: React.ReactNode; stateKey: string; kind?: string }) {
  const [open, setOpen] = useHostField('disclosure:' + stateKey, false);
  return <details className={kind} open={open} onToggle={event => {if (open !== event.currentTarget.open) setOpen(event.currentTarget.open);}}><summary>{title}<ChevronDown size={13}/></summary>{open && children}</details>;
}

function MessageTimestamp({createdAt}: {createdAt?: number}) {
  const time = messageTime(createdAt);
  return time ? <time className="message-time" dateTime={time.iso} title={time.full} aria-label={time.full}>{time.label}</time> : null;
}

const MessageBubble = memo(function MessageBubble({ message, onAction, onFile, active, cid, answerOnly = false }: {
  message: Message; active: boolean; cid: string; onAction: (action: string, message: Message) => void; onFile: (path: string) => void; answerOnly?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [filesExpanded, setFilesExpanded] = useHostField('message-files:' + cid + ':' + message.id, false);
  const [more, setMore] = useState(false), [failure, setFailure] = useState('');
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!more) return;
    const close = (event: PointerEvent) => {if (!menu.current?.contains(event.target as Node)) setMore(false);};
    const key = (event: KeyboardEvent) => {if (event.key === 'Escape') setMore(false);};
    document.addEventListener('pointerdown', close); document.addEventListener('keydown', key);
    return () => {document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', key);};
  }, [more]);
  const text = message.role === 'user' && typeof message.meta?.display_content === 'string' ? message.meta.display_content : messageText(message.content);
  const files = message.meta?.desktop_attachments || (message.meta?.user_added_files || []).map((path: string) => ({ path, name: path.split(/[\\/]/).pop() }));
  const references = message.role === 'user' ? message.meta?.conversation_refs || [] : [];
  const context = message.role === 'user' ? message.meta?.ui_context || {} : {};
  const referencedFiles = (message.file_references || []).filter(file => file.references.some(reference => text.includes(reference)));
  const documents = referencedFiles.filter(file => file.kind !== 'image');
  if (message.role === 'tool') return <button className="message-tool-link" onClick={() => onAction('tool', message)}><Wrench size={14}/><span>{message.meta?.tool_name || '工具结果'}</span><ChevronDown size={13}/></button>;
  return <article id={'message-' + message.id} className={`message ${message.role}`}>
    <div className="message-body">
    {(references.length > 0 || context.skills?.length > 0 || context.knowledge?.length > 0) && <div className="message-contexts" aria-label="发送时选中的上下文">
      {references.map((ref: any) => <span className="context-chip conversation-chip" key={ref.id || ref.session_id} title={'对话快照：' + ref.title}><MessageSquare size={13}/><span>对话 {ref.title}</span></span>)}
      {context.skills?.map((name: string) => <span className="context-chip skill-chip" key={name} title={name}>{name.startsWith('mcp-server-') ? <Link size={13}/> : <Puzzle size={13}/>}<span>{name.startsWith('mcp-server-') ? '连接器' : '技能'} {name}</span></span>)}
      {context.knowledge?.map((ref: any, index: number) => <span className="context-chip knowledge-chip" key={index} title={ref.title}><BookOpen size={13}/><span>资料 {ref.title}</span></span>)}
    </div>}
    {!answerOnly && !!message.reasoning_content && <Disclosure stateKey={cid + ':' + message.id} kind="thinking" title={<><Sparkles size={13}/>深度思考</>}><div className="markdown"><Markdown text={message.reasoning_content} onFile={onFile}/></div></Disclosure>}
    {!answerOnly && !!message.tool_calls?.length && <div className="message-tool-calls">{message.tool_calls.map((tool, index) => <button className="message-tool-link" key={tool.id || index} onClick={() => onAction('tool', {...message, tool_call_id: tool.id})}><Wrench size={14}/><span>{tool.function?.name || tool.name || '工具调用'}</span><ChevronDown size={13}/></button>)}</div>}
    {!!files.length && <div className="message-files">{files.map((file: any) => <button key={file.path} title={file.path} onClick={() => onFile(file.path)}><FileText size={14}/>{file.name}</button>)}</div>}
    {text && <div className="markdown">{message.role === 'user' ? <div className="user-text">{text}</div> : text.split(/^::cowork-inline-vis\{file="([a-z0-9][a-z0-9.-]*\.html)"\}\s*$/m).map((part, index) => index % 2 ? <InlineVisualization key={part} cid={cid} file={part}/> : <Markdown key={index} text={part} onFile={onFile} files={referencedFiles}/>)}</div>}
    {documents.length > 0 && <section className="message-file-table" aria-label="回复中的文件"><strong>共 {documents.length} 个文件</strong>{(filesExpanded ? documents : documents.slice(0,3)).map(file => <button key={file.path} title={file.path} aria-label={'打开文件 ' + file.name} onClick={() => onFile(file.path)}><FileText size={16}/><span>{file.name}</span><small>{file.label}</small></button>)}{documents.length > 3 && <button className="message-files-toggle" onClick={() => setFilesExpanded(!filesExpanded)}>{filesExpanded ? '收起文件列表' : `再显示 ${documents.length - 3} 个文件`}</button>}</section>}
    </div>
    <div className="message-footer"><MessageTimestamp createdAt={message.created_at}/><div className="message-actions">
      <button title="复制消息" aria-label="复制消息" onClick={async () => { try {await window.cowork.call('clipboard.write', {text}); setCopied(true); setFailure(''); setTimeout(() => setCopied(false), 1500);} catch (error) {setFailure('复制未完成：' + (error as Error).message);} }}>{copied ? <Check size={14}/> : <Copy size={14}/>}</button>
      {message.role === 'user' && !message.meta?.same_turn_guidance && <><button title="编辑消息" disabled={active} onClick={() => onAction('edit', message)}><Pencil size={14}/></button><button title="删除此处及后续对话" disabled={active} onClick={() => onAction('delete', message)}><Trash2 size={14}/></button></>}
      {message.role === 'assistant' && !message.live && <><button title="重新生成" disabled={active} onClick={() => onAction('retry', message)}><RefreshCw size={14}/></button><button title="从这里创建分支" onClick={() => onAction('branch', message)}><GitBranch size={14}/></button></>}
      {message.role === 'assistant' && !message.live && <div className="message-more" ref={menu}><button title="更多操作" aria-expanded={more} onClick={() => setMore(!more)}><MoreHorizontal size={15}/></button>{more && <div className="message-more-menu">{[['office', '生成办公稿'], ['skill', '沉淀为 Skill']].map(([action, title]) => <button key={action} onClick={() => {setMore(false); onAction(action, message);}}>{title}</button>)}</div>}</div>}
    </div></div>
    {failure && <p className="error-text" role="alert">{failure}</p>}
  </article>;
});
export default MessageBubble;

export function AssistantTurn({messages, cid, active, onAction, onFile, timing, activeRun, interactionRun = activeRun, showDuration = true, stopped = false, statusLabel = '正在处理', questions = [], onInteractionDone = () => {}}: {
  messages: Message[]; cid: string; active: boolean; onAction: (action: string, message: Message) => void; onFile: (path: string) => void; timing?: TurnTiming; activeRun?: string; interactionRun?: string; showDuration?: boolean; stopped?: boolean; statusLabel?: string; questions?: QuestionRecord[]; onInteractionDone?: () => void;
}) {
  const {answer, results, hasProcess: messageProcess} = replyParts(messages);
  const hasProcess = messageProcess || questions.length > 0;
  const running = Boolean(activeRun && (messages.some(message => message.live) || timing?.id === activeRun && timing.end == null));
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {if (!running) return; setNow(Date.now() / 1000); const timer = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(timer);}, [running]);
  const duration = showDuration ? elapsedTime(timing?.start, timing?.end ?? (running ? now : undefined)) : undefined;
  function thoughtDuration(message: Message) {
    if (message.meta?.ui_split_reasoning) return null;
    const seconds = thinkingTime(timing, message.meta?.ui_source_message_id || message.id, running ? now : undefined);
    return seconds == null ? null : <small className="process-duration">{formatDuration(seconds)}</small>;
  }
  const [expanded, setExpanded] = useHostField<boolean | null>('reply:' + cid + ':' + messages[0].id, null);
  const pending = questions.some(question => !question.outcome && question.runId === interactionRun);
  const open = pending || (expanded ?? running);
  const expert = messages.find(message => message.meta?.conversation_agent?.name || message.meta?.ui_conversation_agent?.name);
  const callIds = new Set(messages.flatMap(message => message.tool_calls || []).map(tool => tool.id));
  function questionRow(question: QuestionRecord) {
    return <Interaction key={question.request.request_id} request={question.request} outcome={question.outcome}
      conversationId={cid} active={active && question.runId === interactionRun} done={onInteractionDone}/>;
  }
  function toolRow(message: Message, tool: any, result?: Message) {
    const resultObject = result?.result_obj;
    const failed = Boolean(resultObject?.error || ['failed', 'error'].includes(resultObject?.status));
    const state = result ? failed ? '失败' : ['running','queued'].includes(resultObject?.status) ? '运行中' : '已完成' : running ? '运行中' : '结果未确认';
    let args = tool.function?.arguments ?? tool.arguments ?? tool.args ?? {};
    if (typeof args === 'string') {try {args = JSON.parse(args);} catch {args = {};}}
    const paths = ['path','file_path','filename','file'].flatMap(key => typeof args?.[key] === 'string' ? [args[key]] : []);
    return <div className="process-step tool-step" key={tool.id}><Wrench className="timeline-node" size={14}/><button className="process-tool" onClick={() => onAction('tool', {...message, tool_call_id: tool.id})}><span>{tool.function?.name || tool.name || result?.meta?.tool_name || '工具调用'}</span><small>{state}</small><ChevronRight size={13}/></button>{paths.map(path => <button className="text-link process-path" key={path} onClick={() => onFile(path)}>{path}</button>)}{failed && <p className="error-text">{String(resultObject.error || '工具执行失败')}</p>}{questions.filter(question => question.callId === tool.id).map(questionRow)}</div>;
  }
  return <section className="assistant-turn">
    {expert && <div className="assistant-name">{expert.meta?.conversation_agent?.name || expert.meta?.ui_conversation_agent?.name}</div>}
    {hasProcess && <div className="reply-process"><button className="process-summary" aria-expanded={open} disabled={pending} onClick={() => setExpanded(!open)}>{running ? <LoaderCircle className="spin" size={14}/> : <Sparkles size={14}/>}<span>{running ? statusLabel : stopped ? '已停止' : '执行过程'}</span>{duration != null && <small className="process-duration">{running ? '已用时' : '用时'} {formatDuration(duration)}</small>}<ChevronRight size={13} className={open ? 'rotated' : ''}/></button>{open && <div className="process-timeline">{messages.map(message => <div key={message.id} id={message !== answer ? 'message-' + message.id : undefined}>
      {!!message.reasoning_content && <div className="process-step"><Sparkles className="timeline-node" size={14}/><Disclosure stateKey={cid + ':' + message.id} kind="thinking" title={<>深度思考{thoughtDuration(message)}</>}><div className="markdown"><Markdown text={message.reasoning_content} onFile={onFile}/><button className="text-link" onClick={() => void window.cowork.call('clipboard.write', {text: message.reasoning_content})}>复制思考原文</button></div></Disclosure></div>}
      {message !== answer && message.role === 'assistant' && messageText(message.content) && <div className="process-step stage-text"><FileText className="timeline-node" size={14}/><Markdown text={messageText(message.content)} onFile={onFile} files={message.file_references}/></div>}
      {message.tool_calls?.map(tool => toolRow(message, tool, results.get(tool.id)))}
      {message.role === 'tool' && !callIds.has(message.tool_call_id) && toolRow(message, {id: message.tool_call_id}, message)}
      {questions.filter(question => !question.callId && question.sourceId === (message.meta?.ui_source_message_id || message.id)).map(questionRow)}
    </div>)}{questions.filter(question => question.callId ? !callIds.has(question.callId) && !messages.some(message => message.tool_call_id === question.callId) : !messages.some(message => (message.meta?.ui_source_message_id || message.id) === question.sourceId)).map(questionRow)}</div>}</div>}
    {!hasProcess && (stopped || duration != null) && <div className="process-duration">{stopped && '已停止'}{duration != null && <>{stopped && ' · '}{running ? '已用时' : '用时'} {formatDuration(duration)}</>}</div>}
    {answer && <MessageBubble message={answer} cid={cid} active={active} onAction={onAction} onFile={onFile} answerOnly/>}
  </section>;
}
