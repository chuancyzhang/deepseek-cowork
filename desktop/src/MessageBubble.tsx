import { createContext, useContext, memo, useRef, useState, useEffect, type ReactNode } from 'react';
import ReactMarkdown, {type Components} from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { BookOpen, Check, ChevronDown, ChevronRight, CircleHelp, Copy, ExternalLink, FileText, GitBranch, Image as ImageIcon, Link, LoaderCircle, MessageSquare, MoreHorizontal, Pencil, Puzzle, RefreshCw, Sparkles, Trash2, Users, Wrench } from 'lucide-react';
import {agentRecords, agentTools} from './subagents';
import type { FileReference, MediaTask, Message } from './types';
import { messageText, toolErrorText, messageTime, replyParts, elapsedTime, thinkingTime, formatDuration, type TurnTiming, type QuestionRecord } from './runtime';
import {useHost, useHostField} from './host/context';
import InlineVisualization from './InlineVisualization';
import { markdownFilePath, markdownUrl, messageAttachments, remarkFileReferences } from './attachments';
import Interaction from './Interaction';
import MermaidDiagram from './MermaidDiagram';
import SnippetPreview from './SnippetPreview';
import ImageAttachments, {ImagePreviewLink, isImageAttachment} from './ImageAttachment';
import {reviewTitle, type ReviewSummary} from './reviews';

export function MediaResult({file,cid,onFile,onPlayable}:{file:FileReference;cid:string;onFile:(path:string)=>void;onPlayable?:(playable:boolean)=>void}) {
  const [url,setUrl]=useState(''),[failure,setFailure]=useState('');
  useEffect(()=>{let live=true;setUrl('');setFailure('');void window.cowork.call('file.open',{conversation_id:cid,path:file.path}).then(result=>{if(live)setUrl(result.payload.url);}).catch(error=>{if(live)setFailure(error.message);});return()=>{live=false;};},[cid,file.path]);
  const playbackError = () => {setFailure('此格式无法在应用内播放，请打开文件查看。'); onPlayable?.(false);};
  return <figure>{url&&(file.kind==='video'?<video src={url} controls preload={onPlayable ? 'auto' : 'metadata'} aria-label={file.name} onLoadedData={()=>onPlayable?.(true)} onError={playbackError}/>:<audio src={url} controls preload="metadata" aria-label={file.name} onError={playbackError}/>)}<figcaption><button className="text-link" onClick={()=>onFile(file.path)}>{file.name}</button></figcaption>{failure&&<p role="alert" className="error-text">{failure}</p>}</figure>;
}

function CodeBlock({ children }: { children?: React.ReactNode }) {
  const source = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  const [failure, setFailure] = useState('');
  return <div className="code-block"><button title="复制代码" onClick={async () => {
    try {await window.cowork.call('clipboard.write', {text: source.current?.textContent || ''}); setCopied(true); setFailure(''); setTimeout(() => setCopied(false), 1500);}
    catch (error) {setFailure('复制未完成：' + (error as Error).message);}
  }}>{copied ? <Check size={14}/> : <Copy size={14}/>}</button><pre ref={source}>{children}</pre>{failure && <p className="error-text" role="alert">{failure}</p>}</div>;
}

const MarkdownContext = createContext<{previewScope: string; cid?: string; files?: FileReference[]; onFile?: (path: string) => void}>({previewScope:''});
function remarkDiagramState({streaming}: {streaming: boolean}) {
  return (tree: any, file: any) => {
    const visit = (node: any) => {
      if (node.type === 'code') {
        const original = String(file.value).slice(node.position?.start.offset, node.position?.end.offset);
        const fence = original.match(/^\s*(`{3,}|~{3,})/);
        const closed = fence && new RegExp('\\n[ \\t]*' + fence[1][0] + '{' + fence[1].length + ',}[ \\t]*$').test(original);
        node.data = {...node.data, hProperties: {...node.data?.hProperties, 'data-diagram-pending': streaming && !closed, 'data-code-complete': Boolean(closed)}};
      }
      node.children?.forEach(visit);
    };
    visit(tree);
  };
}

// Keep the component identity stable when later tokens arrive in the same answer.
function MarkdownPre({children, node}: any) {
      const {previewScope: scope} = useContext(MarkdownContext);
      const code = node?.children[0];
      if (code?.type === 'element' && code.tagName === 'code') {
        const source = code.children.map((child: any) => child.type === 'text' ? child.value : '').join('');
        const language = String(code.properties.className || '').toLowerCase();
        if (language === 'language-mermaid' || !language && /^\s*(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b/.test(source)) {
          return <MermaidDiagram source={source} pending={Boolean(code.properties['data-diagram-pending'])}/>;
        }
        const snippetLanguage = language.replace(/^language-/, '');
        if (scope && ['html','js','javascript','jsx','tsx','node','nodejs'].includes(snippetLanguage)) {
          const identity = scope + ':' + node.position?.start.offset;
          return <SnippetPreview key={identity} source={source} language={snippetLanguage} complete={Boolean(code.properties['data-code-complete'])} scope={identity}/>;
        }
      }
      return <CodeBlock>{children}</CodeBlock>;
}

const markdownComponents: Components = {
  pre: MarkdownPre,
  a: function MarkdownLink({href, children}) {
    const {cid, files, onFile} = useContext(MarkdownContext);
    const external = /^https?:/i.test(href || '');
    const image = cid && files?.find(file => file.kind === 'image' && file.url === href);
    if (image && cid) return <ImagePreviewLink cid={cid} file={image} onOpen={onFile}>{children}</ImagePreviewLink>;
    return <button className="text-link" onClick={() => {
      if (!href) return;
      if (external) void window.cowork.call('external.open', {url:href});
      else onFile?.(markdownFilePath(href));
    }}>{children}{external && <ExternalLink size={12}/>}</button>;
  },
  img: function MarkdownImage({src, alt}) {
    const {cid, onFile} = useContext(MarkdownContext);
    const path = typeof src === 'string' && !/^https?:/i.test(src) ? markdownFilePath(src) : '';
    if (cid && path) return <ImagePreviewLink cid={cid} file={{path,name:path.split(/[\\/]/).pop() || path}} onOpen={onFile}>{alt || '查看图片'}</ImagePreviewLink>;
    return <button className="inline-file" onClick={() => typeof src === 'string' && onFile?.(src)}><ImageIcon size={16}/>{alt || '查看图片'}</button>;
  }
};

export function Markdown({ text, onFile, files = [], streaming = false, previewScope = '', cid }: { text: string; onFile?: (path: string) => void; files?: FileReference[]; streaming?: boolean; previewScope?: string; cid?: string }) {
  return <MarkdownContext.Provider value={{previewScope, cid, files, onFile}}><ReactMarkdown urlTransform={markdownUrl}
    remarkPlugins={[remarkGfm, [remarkFileReferences, files], [remarkDiagramState, {streaming}]]} components={markdownComponents}>{text}</ReactMarkdown></MarkdownContext.Provider>;
}

function Disclosure({ title, children, stateKey, kind = 'tool-entry' }: { title: React.ReactNode; children: React.ReactNode; stateKey: string; kind?: string }) {
  const [open, setOpen] = useHostField('disclosure:' + stateKey, false);
  const Chevron = open ? ChevronDown : ChevronRight;
  return <details className={kind} open={open} onToggle={event => {if (open !== event.currentTarget.open) setOpen(event.currentTarget.open);}}><summary>{title}<Chevron size={13} aria-hidden="true"/></summary>{open && children}</details>;
}

function MessageTimestamp({createdAt}: {createdAt?: number}) {
  const time = messageTime(createdAt);
  return time ? <time className="message-time" dateTime={time.iso} title={time.full} aria-label={time.full}>{time.label}</time> : null;
}

function ViewedImages({cid, callId, images, onFile, onDetails}: {cid: string; callId: string; images: {path: string; name: string}[]; onFile: (path: string) => void; onDetails: () => void}) {
  const [open, setOpen] = useHostField('viewed-images:' + cid + ':' + callId, false);
  return <div className="process-step tool-step viewed-images"><ImageIcon className="timeline-node" size={14}/>
    <div className="viewed-images-heading"><button className="process-tool" aria-expanded={open} onClick={() => setOpen(!open)}><span>已查看 {images.length} 张图片</span>{open ? <ChevronDown size={13}/> : <ChevronRight size={13}/>}</button><button className="text-link" onClick={onDetails}>详情</button></div>
    {open && <ImageAttachments cid={cid} files={images} onOpen={onFile}/>}
  </div>;
}

function MessageVideo({file, cid, onFile}: {file: FileReference; cid: string; onFile: (path: string) => void}) {
  const host = useHost();
  const [tasks] = useHostField<MediaTask[]>(host.sessionKey('media_tasks', cid), []);
  if (tasks.some(task => task.conversation_id === cid && task.capability === 'video_generate' && task.files?.includes(file.path))) return null;
  return <MediaResult file={file} cid={cid} onFile={onFile}/>;
}

const MessageBubble = memo(function MessageBubble({ message, onAction, onFile, active, cid, answerOnly = false, changeSummary, readOnly = false }: {
  message: Message; active: boolean; cid: string; onAction: (action: string, message: Message) => void; onFile: (path: string) => void; answerOnly?: boolean; changeSummary?: ReactNode; readOnly?: boolean;
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
  const files = messageAttachments(message);
  const references = message.role === 'user' ? message.meta?.conversation_refs || [] : [];
  const context = message.role === 'user' ? message.meta?.ui_context || {} : {};
  const referencedFiles = (message.file_references || []).filter(file => file.references.some(reference => text.includes(reference)));
  const documents = referencedFiles.filter(file => !['image','audio','video'].includes(file.kind));
  const media = referencedFiles.filter(file => ['audio','video'].includes(file.kind));
  const imageFiles = message.role === 'user' ? files.filter(isImageAttachment) : referencedFiles.filter(file => file.kind === 'image');
  const otherFiles = files.filter((file: any) => !isImageAttachment(file));
  if (message.role === 'tool') return <button className="message-tool-link" onClick={() => onAction('tool', message)}><Wrench size={14}/><span>{message.meta?.tool_name || '工具结果'}</span><ChevronDown size={13}/></button>;
  return <article id={'message-' + message.id} className={`message ${message.role}`}>
    {message.role !== 'user' && imageFiles.length > 0 && <ImageAttachments cid={cid} files={imageFiles} onOpen={onFile}/>}
    <div className="message-body" hidden={!text && !imageFiles.length && !otherFiles.length && !references.length && !context.skills?.length && !context.knowledge?.length && message.role === 'user'}>
    {media.length > 0 && <div className="message-media">{media.map(file => file.kind === 'video' ? <MessageVideo key={file.path} file={file} cid={cid} onFile={onFile}/> : <MediaResult key={file.path} file={file} cid={cid} onFile={onFile}/>)}</div>}
    {(references.length > 0 || context.skills?.length > 0 || context.knowledge?.length > 0) && <div className="message-contexts" aria-label="发送时选中的上下文">
      {references.map((ref: any) => <span className="context-chip conversation-chip" key={ref.id || ref.session_id} title={'对话快照：' + ref.title}><MessageSquare size={13}/><span>对话 {ref.title}</span></span>)}
      {context.skills?.map((name: string) => <span className="context-chip skill-chip" key={name} title={name}>{name.startsWith('mcp-server-') ? <Link size={13}/> : <Puzzle size={13}/>}<span>{name.startsWith('mcp-server-') ? '连接器' : '技能'} {name}</span></span>)}
      {context.knowledge?.map((ref: any, index: number) => <span className="context-chip knowledge-chip" key={index} title={ref.title}><BookOpen size={13}/><span>资料 {ref.title}</span></span>)}
    </div>}
    {!answerOnly && !!message.reasoning_content && <Disclosure stateKey={cid + ':' + message.id} kind="thinking" title={<><Sparkles size={13}/>深度思考</>}><div className="markdown"><Markdown text={message.reasoning_content} streaming={message.live} onFile={onFile} cid={cid}/></div></Disclosure>}
    {!answerOnly && !!message.tool_calls?.length && <div className="message-tool-calls">{message.tool_calls.map((tool, index) => <button className="message-tool-link" key={tool.id || index} onClick={() => onAction('tool', {...message, tool_call_id: tool.id})}><Wrench size={14}/><span>{tool.function?.name || tool.name || '工具调用'}</span><ChevronDown size={13}/></button>)}</div>}
    {!!otherFiles.length && <div className="message-files">{otherFiles.map(file => <button key={file.path} title={file.path} onClick={() => onFile(file.path)}><FileText size={14}/>{file.name}</button>)}</div>}
    {text && <div className="markdown">{message.role === 'user' ? <div className="user-text">{text}</div> : text.split(/^::cowork-inline-vis\{file="([a-z0-9][a-z0-9.-]*\.html)"\}\s*$/m).map((part, index) => index % 2 ? <InlineVisualization key={part} cid={cid} file={part}/> : <Markdown key={index} text={part} streaming={message.live} onFile={onFile} files={referencedFiles} previewScope={cid+':'+message.id+':'+index} cid={cid}/>)}</div>}
    {message.role === 'user' && imageFiles.length > 0 && <ImageAttachments cid={cid} files={imageFiles} onOpen={onFile}/>}
    {documents.length > 0 && <section className="message-file-table" aria-label="回复中的文件"><strong>共 {documents.length} 个文件</strong>{(filesExpanded ? documents : documents.slice(0,3)).map(file => <button key={file.path} title={file.path} aria-label={'打开文件 ' + file.name} onClick={() => onFile(file.path)}><FileText size={16}/><span>{file.name}</span><small>{file.label}</small></button>)}{documents.length > 3 && <button className="message-files-toggle" onClick={() => setFilesExpanded(!filesExpanded)}>{filesExpanded ? '收起文件列表' : `再显示 ${documents.length - 3} 个文件`}</button>}</section>}
    {changeSummary}
    </div>
    <div className="message-footer">{message.role !== 'assistant' && <MessageTimestamp createdAt={message.created_at}/>}<div className="message-actions">
      <button title="复制消息" aria-label="复制消息" onClick={async () => { try {await window.cowork.call('clipboard.write', {text}); setCopied(true); setFailure(''); setTimeout(() => setCopied(false), 1500);} catch (error) {setFailure('复制未完成：' + (error as Error).message);} }}>{copied ? <Check size={14}/> : <Copy size={14}/>}</button>
      {!readOnly && message.role === 'user' && !message.meta?.same_turn_guidance && <><button title="编辑消息" disabled={active} onClick={() => onAction('edit', message)}><Pencil size={14}/></button><button title="删除此处及后续对话" disabled={active} onClick={() => onAction('delete', message)}><Trash2 size={14}/></button></>}
      {!readOnly && message.role === 'assistant' && !message.live && <><button title="重新生成" disabled={active} onClick={() => onAction('retry', message)}><RefreshCw size={14}/></button><button title="从这里创建分支" onClick={() => onAction('branch', message)}><GitBranch size={14}/></button></>}
      {!readOnly && message.role === 'assistant' && !message.live && <div className="message-more" ref={menu}><button title="更多操作" aria-expanded={more} onClick={() => setMore(!more)}><MoreHorizontal size={15}/></button>{more && <div className="message-more-menu">{[['office', '生成办公稿'], ['skill', '沉淀为 Skill']].map(([action, title]) => <button key={action} onClick={() => {setMore(false); onAction(action, message);}}>{title}</button>)}</div>}</div>}
    </div>{message.role === 'assistant' && <MessageTimestamp createdAt={message.created_at}/>}</div>
    {failure && <p className="error-text" role="alert">{failure}</p>}
  </article>;
});
export default MessageBubble;

const toolTitles = new Map([
  ['run_python_code', '运行 Python'],
  ['run_node_code', '运行 JavaScript'],
  ['request_user_input', '向你提问'],
  ['request_user_approval', '请求确认'],
  ['read_file', '读取文件'],
  ['text_file_read', '读取文件'],
  ['write_file', '写入文件'],
]);

function processError(error: unknown) {
  const text = toolErrorText(error).trim();
  if (!text) return '工具执行失败';
  if (text.includes('Traceback (most recent call last):')) {
    const lines = text.split(/\r?\n/);
    return lines.findLast(line => /^[\w.]+(?:Error|Exception):\s/.test(line)) || lines[0];
  }
  return text;
}

export function RunStatus({label, duration}: {label: string; duration?: string}) {
  const Icon = label === '等待你的回答' ? CircleHelp : LoaderCircle;
  return <div className="run-state">
    {label !== '已停止' && <Icon className={Icon === LoaderCircle ? 'spin' : undefined} size={14} aria-hidden="true"/>}
    <span role="status">{label}</span>{duration && <small className="process-duration">{duration}</small>}
  </div>;
}

export function GenerationTaskStatus({task, statusLabel = '正在处理', duration, expanded, toggle, waiting = false}: {
  task: ReviewSummary; statusLabel?: string; duration?: number; expanded?: boolean; toggle?: () => void; waiting?: boolean;
}) {
  const office = task.kind === 'office';
  const target = office ? reviewTitle(task) : task.title;
  const format = office ? ({docx:'Word 文档',pptx:'PPT 演示文稿',xlsx:'Excel 工作簿'}[task.format] || task.format.toUpperCase()) : '可复用技能';
  const stage = statusLabel !== '正在处理' ? statusLabel : waiting ? '等待开始' : task.status !== 'generating' ? '草稿已提交，正在完成回复' : office ? '正在整理内容' : '正在提炼可复用方法';
  return <div className="generation-task">
    <div className="generation-task-heading"><span className="generation-task-icon" aria-hidden="true">{office ? <FileText size={22}/> : <Sparkles size={22}/>}</span><div className="generation-task-identity"><strong role="status">{office ? '正在生成办公稿' : '正在沉淀为 Skill'}</strong><div className="generation-task-target" title={target + ' · ' + format}>{target} · {format}</div></div></div>
    <div className="generation-task-footer"><span className="generation-task-stage" role="status"><LoaderCircle className="spin" size={14}/>{stage}</span><div className="generation-task-process">{toggle && <button className="process-summary" aria-expanded={expanded} onClick={toggle}>{expanded ? '收起执行过程' : '查看执行过程'}<ChevronRight size={13} className={expanded ? 'rotated' : ''}/></button>}{duration != null && <small className="process-duration">已用时 {formatDuration(duration)}</small>}</div></div>
  </div>;
}

export function AssistantTurn({messages, cid, active, onAction, onFile, timing, activeRun, interactionRun = activeRun, showDuration = true, stopped = false, statusLabel = '正在处理', questions = [], onInteractionDone = () => {}, reviewTask, changeSummary, readOnly = false}: {
  messages: Message[]; cid: string; active: boolean; onAction: (action: string, message: Message) => void; onFile: (path: string) => void; timing?: TurnTiming; activeRun?: string; interactionRun?: string; showDuration?: boolean; stopped?: boolean; statusLabel?: string; questions?: QuestionRecord[]; onInteractionDone?: (requestId: string) => void; reviewTask?: ReviewSummary; changeSummary?: ReactNode; readOnly?: boolean;
}) {
  const {answer, results, hasProcess: messageProcess} = replyParts(messages);
  const hasProcess = messageProcess || questions.length > 0;
  const running = Boolean(activeRun && timing?.end == null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {if (!running) return; setNow(Date.now() / 1000); const timer = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(timer);}, [running]);
  const duration = showDuration ? elapsedTime(timing?.start, timing?.end ?? (running ? now : undefined)) : undefined;
  const durationLabel = duration == null ? undefined : (running ? '已用时 ' : '用时 ') + formatDuration(duration);
  function thoughtDuration(message: Message) {
    if (message.meta?.ui_split_reasoning) return null;
    const seconds = thinkingTime(timing, message.meta?.ui_source_message_id || message.id, running ? now : undefined);
    return seconds == null ? null : <small className="process-duration">{formatDuration(seconds)}</small>;
  }
  const [expanded, setExpanded] = useHostField<boolean | null>('reply:' + cid + ':' + messages[0].id, null);
  const task = activeRun && reviewTask?.run_id === activeRun ? reviewTask : undefined;
  const previousTask = useRef(task);
  useEffect(() => {
    if (previousTask.current && !task) setExpanded(null);
    previousTask.current = task;
  }, [task, setExpanded]);
  const pending = Boolean(interactionRun) && questions.some(question => !question.outcome && question.runId === interactionRun);
  const open = pending || (expanded ?? (running && !task));
  const status = running ? statusLabel : stopped ? '已停止' : durationLabel ? '' : '执行过程';
  const summary = <>
    {running ? status === '等待你的回答' ? <CircleHelp size={14} aria-hidden="true"/>
      : <LoaderCircle className="spin" size={14} aria-hidden="true"/> : !stopped && <Sparkles size={14} aria-hidden="true"/>}
    {status && <span role={pending && running ? 'status' : undefined} className={running && status === '正在处理' ? 'sr-only' : undefined}>{status}</span>}
    {durationLabel && <small className="process-duration">{durationLabel}</small>}
  </>;
  const expert = messages.find(message => message.meta?.conversation_agent?.name || message.meta?.ui_conversation_agent?.name);
  const callIds = new Set(messages.flatMap(message => message.tool_calls || []).map(tool => tool.id));
  const collaboration = readOnly ? [] : agentRecords(messages, results);
  const agentNames = [...new Set(collaboration.map(item=>item.name).filter(Boolean))];
  const agentTitle = agentNames.length ? agentNames.slice(0,3).join('、') + (agentNames.length > 3 ? ' 等子任务' : '') : '子任务协作';
  function questionRow(question: QuestionRecord) {
    return <Interaction key={cid + ':' + question.request.request_id} request={question.request} outcome={question.outcome}
      conversationId={cid} active={active && question.runId === interactionRun} done={onInteractionDone}/>;
  }
  function toolRow(message: Message, tool: any, result?: Message) {
    const resultObject = result?.result_obj;
    const failed = Boolean(resultObject?.error || ['failed', 'error'].includes(resultObject?.status));
    const toolName = tool.function?.name || tool.name || result?.meta?.tool_name;
    if (!readOnly && agentTools.has(toolName)) {
      if (tool.id !== collaboration[0]?.callId) return null;
      return <div className="process-step agent-step" key={tool.id}><Users className="timeline-node" size={14} aria-hidden="true"/><Disclosure stateKey={cid+':agents:'+tool.id} kind="thinking agent-process" title={<>{agentTitle}{collaboration.some(item=>item.failed) && <small className="error-text">有调用失败</small>}</>}>
        <div className="agent-process-records">{collaboration.map(item=><div className="agent-process-record" key={item.key}>
          <button className="process-tool" onClick={()=>onAction(item.failed ? 'tool' : 'agent', {...item.message,tool_call_id:item.callId,meta:{...item.message.meta,ui_agent_target:item.target}})}>
            <span>{item.verb}{item.name ? ' '+item.name : '子任务'}</span>{item.failed && <small className="process-tool-state" data-failed>失败</small>}<ChevronRight size={13} aria-hidden="true"/>
          </button>{item.text && <p className="agent-process-text" title={item.text}>{item.text}</p>}{item.failed && <p className="error-text">{processError(item.error)}</p>}
        </div>)}</div>
      </Disclosure></div>;
    }
    const title = toolTitles.get(toolName) || toolName || '工具调用';
    const toolQuestions = questions.filter(question => question.callId === tool.id);
    const mediaSnapshot = (toolName === 'media_create' || toolName === 'media_task') && resultObject?.task_id;
    const state = result ? failed ? '失败' : mediaSnapshot ? '已返回' : ['running','queued'].includes(resultObject?.status) ? '运行中' : '已完成'
      : running ? toolQuestions.length ? '已发起' : '运行中' : '结果未确认';
    let args = tool.function?.arguments ?? tool.arguments ?? tool.args ?? {};
    if (typeof args === 'string') {try {args = JSON.parse(args);} catch {args = {};}}
    const paths = ['path','file_path','filename','file'].flatMap(key => typeof args?.[key] === 'string' ? [args[key]] : []);
    const images = toolName === 'view_image' && result && !failed ? (resultObject?.content_parts || []).filter((part: any) => part.type === 'input_image' && typeof part.path === 'string') : [];
    if (images.length) return <ViewedImages key={tool.id} cid={cid} callId={tool.id} images={images} onFile={onFile} onDetails={() => onAction('tool', {...message, tool_call_id: tool.id})}/>;
    return <div className="process-step tool-step" key={tool.id}>
      <Wrench className="timeline-node" size={14} aria-hidden="true"/>
      <button className="process-tool" onClick={() => onAction('tool', {...message, tool_call_id: tool.id})}>
        <span>{title}</span><small className="process-tool-state" data-failed={failed || undefined}>{state}</small>
        <span className="process-tool-detail">详情 <ChevronRight size={13} aria-hidden="true"/></span>
      </button>
      {toolName && title !== toolName && <code className="process-tool-name">{toolName}</code>}
      {[...new Set(paths)].map(path => isImageAttachment({path})
        ? <ImagePreviewLink key={path} cid={cid} file={{path, name:path.split(/[\\/]/).pop() || path}} onOpen={onFile}>{path}</ImagePreviewLink>
        : <button className="text-link process-path" key={path} onClick={() => onFile(path)}>{path}</button>)}
      {failed && <p className="process-tool-error error-text">{processError(resultObject?.error)}</p>}
      {toolQuestions.map(questionRow)}
    </div>;
  }
  return <section className="assistant-turn">
    {expert && !readOnly && <div className="assistant-name">{expert.meta?.conversation_agent?.name || expert.meta?.ui_conversation_agent?.name}</div>}
    {(hasProcess || task) && <div className="reply-process">
      {task ? <GenerationTaskStatus task={task} statusLabel={statusLabel} duration={duration} expanded={open} toggle={hasProcess && !pending ? () => setExpanded(!open) : undefined}/>
        : pending ? <div className="process-summary">{summary}</div>
        : <button className="process-summary" aria-expanded={open} onClick={() => setExpanded(!open)}>
          {summary}<ChevronRight size={13} className={open ? 'rotated' : ''} aria-hidden="true"/>
        </button>}
      {hasProcess && open && <div className="process-timeline">{messages.map(message => <div key={message.id} id={message !== answer ? 'message-' + message.id : undefined}>
      {!!message.reasoning_content && <div className="process-step"><Sparkles className="timeline-node" size={14}/><Disclosure stateKey={cid + ':' + message.id} kind="thinking" title={<>深度思考{thoughtDuration(message)}</>}><div className="markdown"><Markdown text={message.reasoning_content} streaming={message.live} onFile={onFile} cid={cid}/><button className="text-link" onClick={() => void window.cowork.call('clipboard.write', {text: message.reasoning_content})}>复制思考原文</button></div></Disclosure></div>}
      {message !== answer && message.role === 'assistant' && messageText(message.content) && <div className="process-step stage-text"><FileText className="timeline-node" size={14}/><Markdown text={messageText(message.content)} streaming={message.live} onFile={onFile} files={message.file_references} previewScope={cid+':'+message.id} cid={cid}/></div>}
      {message.tool_calls?.map(tool => toolRow(message, tool, results.get(tool.id)))}
      {message.role === 'tool' && !callIds.has(message.tool_call_id) && toolRow(message, {id: message.tool_call_id}, message)}
      {questions.filter(question => !question.callId && question.sourceId === (message.meta?.ui_source_message_id || message.id)).map(questionRow)}
    </div>)}{questions.filter(question => question.callId ? !callIds.has(question.callId) && !messages.some(message => message.tool_call_id === question.callId) : !messages.some(message => (message.meta?.ui_source_message_id || message.id) === question.sourceId)).map(questionRow)}</div>}</div>}
    {!hasProcess && !task && (running || stopped
      ? <RunStatus label={running ? statusLabel : '已停止'} duration={durationLabel}/>
      : durationLabel && <div className="process-duration">{durationLabel}</div>)}
    {answer && <MessageBubble message={answer} cid={cid} active={active} onAction={onAction} onFile={onFile} answerOnly readOnly={readOnly} changeSummary={!answer.live && changeSummary}/>}
  </section>;
}
