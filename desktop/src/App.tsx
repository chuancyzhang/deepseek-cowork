import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ArrowUp, Square, Plus, Search, Settings as SettingsIcon, PanelsTopLeft, Folder, FileText, ChevronRight, ChevronDown, X, Copy, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Maximize2, Minimize2, Puzzle, Check, LoaderCircle, Paperclip, ShieldCheck, Sun, Moon, RefreshCw, ArrowLeft, Download, Code2, BookOpen, Sparkles, ExternalLink } from 'lucide-react';
import type { Attachment, Conversation, FileSession, HostEvent, Message, Plugin, RuntimeEvent, Settings } from './types';
import { displayMessages, isActive, mergeEvents, messageText, runErrorSummary, unwrapEvents } from './runtime';
import PluginEditor from './PluginEditor';
import Usage from './Usage';

const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
type Ask = { title: string; detail: string; choices: string[]; resolve: (index: number) => void };
type Editor = { file: FileSession; text: string; saved: string; plugin: Plugin };

function Markdown({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ href, children }) => <button className="text-link" onClick={() => { if (href?.match(/^https?:/)) void call('external.open', { url: href }); }}>{children}<ExternalLink size={12}/></button> }}>{text}</ReactMarkdown>;
}
function Bubble({ message }: { message: Message }) {
  const [copied, setCopied] = useState(false);
  const text = messageText(message.content);
  if (message.role === 'tool') return <details className="tool-entry"><summary><PanelsTopLeft size={14}/><span>{message.meta?.tool_name || '工具结果'}</span><ChevronDown size={13}/></summary><pre>{typeof message.result_obj === 'object' ? JSON.stringify(message.result_obj, null, 2) : text}</pre></details>;
  return <article className={`message ${message.role}`}>
    {message.role === 'assistant' && <div className="assistant-name"><span className="brand-symbol small">✳</span>Cowork{message.live && <LoaderCircle className="spin" size={13}/>}</div>}
    {!!message.reasoning_content && <details className="thinking"><summary><Sparkles size={13}/>深度思考<ChevronDown size={13}/></summary><div className="markdown"><Markdown text={message.reasoning_content}/></div></details>}
    {!!message.tool_calls?.length && <details className="tool-entry"><summary><PanelsTopLeft size={14}/>工具调用 · {message.tool_calls.length}<ChevronDown size={13}/></summary>{message.tool_calls.map((tool, index) => <div key={tool.id || index}><strong>{tool.function?.name || tool.name || '工具'}</strong><pre>{typeof tool.function?.arguments === 'string' ? tool.function.arguments : JSON.stringify(tool.arguments || tool, null, 2)}</pre></div>)}</details>}
    {text && <div className="markdown">{message.role === 'user' ? <div className="user-text">{text}</div> : <Markdown text={text}/>}</div>}
    {message.role === 'assistant' && text && <div className="message-actions"><button title="复制回答" onClick={async () => { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>{copied ? <Check size={14}/> : <Copy size={14}/>}</button></div>}
  </article>;
}

function Interaction({ request, conversationId, done, error }: { request: any; conversationId: string; done: () => void; error: (message: string) => void }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  async function respond(value: any) {
    setBusy(true);
    try { const result = await call('interaction.respond', { conversation_id: conversationId, request_id: request.request_id, value }); if (result.status === 'error') throw new Error(result.error); done(); }
    catch (failure) { error((failure as Error).message); } finally { setBusy(false); }
  }
  return <section className="interaction"><div className="interaction-title">{request.title || (request.kind === 'approval' ? '需要你的确认' : '补充一些信息')}</div><p>{request.message}</p>
    {request.kind === 'approval' ? <div className="button-row"><button disabled={busy} className="secondary" onClick={() => void respond(false)}>拒绝</button><button disabled={busy} className="primary" onClick={() => void respond(true)}>允许这次操作</button></div> : <>
      {request.kind === 'questionnaire' ? request.questions?.map((question: any) => <fieldset key={question.id}><legend>{question.question}</legend>{question.options?.map((option: any) => <label className="choice" key={option.value}><input type="radio" name={question.id} checked={answers[question.id] === option.value} onChange={() => setAnswers(previous => ({ ...previous, [question.id]: option.value }))}/><span>{option.label}{option.description && <small>{option.description}</small>}</span></label>)}<input placeholder="补充或填写其他回答" value={question.options?.some((o: any) => o.value === answers[question.id]) ? '' : answers[question.id] || ''} onChange={e => setAnswers(previous => ({ ...previous, [question.id]: e.target.value }))}/></fieldset>) : <><div className="button-row">{request.options?.map((option: any) => <button key={option.value} className={text === option.value ? 'secondary selected' : 'secondary'} onClick={() => setText(option.value)}>{option.label}</button>)}</div><textarea rows={3} placeholder="输入回答" value={text} onChange={e => setText(e.target.value)}/></>}
      <button disabled={busy} className="primary" onClick={() => void respond(request.kind === 'questionnaire' ? { answers } : text)}>{busy ? '正在提交…' : '提交回答'}</button>
    </>}
  </section>;
}

export default function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [route, setRoute] = useState('chat');
  const [loading, setLoading] = useState(true);
  const [serviceError, setServiceError] = useState('');
  const [notice, setNotice] = useState('');
  const [draft, setDraft] = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [sending, setSending] = useState(false);
  const [query, setQuery] = useState('');
  const [sidebar, setSidebar] = useState(true);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [workspaceExpanded, setWorkspaceExpanded] = useState(false);
  const [fileTree, setFileTree] = useState<any>({ files: [] });
  const [editors, setEditors] = useState<Editor[]>([]);
  const [activeFile, setActiveFile] = useState('');
  const [interaction, setInteraction] = useState<any>(null);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [pluginPreview, setPluginPreview] = useState<Plugin | null>(null);
  const [previewText, setPreviewText] = useState('# 让想法成为成果\n\n这里是独立的预览内容。\n\n## 今天的工作\n\n- 整理资料\n- 写下结论\n\n**继续完善。**');
  const [showUsage, setShowUsage] = useState(false);
  const [skills, setSkills] = useState<any[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [showSkills, setShowSkills] = useState(false);
  const [modelForm, setModelForm] = useState({ name: 'DeepSeek', base_url: 'https://api.deepseek.com', model_name: 'deepseek-chat', api_key: '', provider_type: 'openai', api_protocol: 'chat_completions' });
  const [formBusy, setFormBusy] = useState(false);
  const [rename, setRename] = useState(false);
  const [title, setTitle] = useState('');
  const scroll = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const composer = useRef<HTMLTextAreaElement>(null);
  const selectedId = useRef('');
  const loadGeneration = useRef(0);
  const draftRef = useRef({ text: '', attachments: [] as Attachment[] });
  const editorsRef = useRef<Editor[]>([]);
  const stateRef = useRef({ current, settings, workspaceOpen, activeFile, selectedSkills });
  stateRef.current = { current, settings, workspaceOpen, activeFile, selectedSkills };
  editorsRef.current = editors;
  draftRef.current = { text: draft, attachments };
  const pendingSubmission = useRef<{ id: string; message: string; content: string } | null>(null);
  const pendingSteering = useRef<{ id: string; signature: string } | null>(null);
  const closePending = useRef(false);
  const draftQueue = useRef(Promise.resolve<any>(null));
  const theme = settings?.theme || 'light';
  const active = isActive(current?.run?.status);
  const error = useCallback((message: string) => setNotice(message), []);
  const confirm = useCallback((title: string, detail: string, choices: string[]) => new Promise<number>(resolve => setAsk({ title, detail, choices, resolve })), []);

  const refreshList = useCallback(async () => { setConversations(await call('conversation.list')); }, []);
  const saveDraft = useCallback((id = stateRef.current.current?.id || '', value = draftRef.current) => {
    if (!id) return Promise.resolve();
    draftQueue.current = draftQueue.current.catch(() => undefined).then(() => call('draft.save', { conversation_id: id, value }));
    return draftQueue.current;
  }, []);
  const saveView = useCallback(async () => {
    const id = stateRef.current.current?.id;
    if (!id) return;
    await call('view.save', { conversation_id: id, value: { workspaceOpen: stateRef.current.workspaceOpen,
      activeFile: stateRef.current.activeFile, selectedSkills: stateRef.current.selectedSkills, scrollTop: scroll.current?.scrollTop || 0,
      editors: editorsRef.current.map(editor => ({ path: editor.file.path, text: editor.text, saved: editor.saved, fingerprint: editor.file.fingerprint })) } });
  }, []);
  async function refreshFiles(id = selectedId.current, path?: string) {
    if (!id) return;
    try { const result = await call('workspace.list', { conversation_id: id, path }); if (selectedId.current === id) setFileTree(result); }
    catch (failure) { error((failure as Error).message); }
  }
  async function loadConversation(id: string, preserve = false) {
    const generation = ++loadGeneration.current;
    const value = await call<Conversation>('conversation.get', { conversation_id: id });
    if (selectedId.current !== id || generation !== loadGeneration.current) return;
    setCurrent(value); setEvents(previous => preserve ? mergeEvents(unwrapEvents(value.events || []), previous) : unwrapEvents(value.events || []));
    const pending = [...unwrapEvents(value.events || [])].reverse().find(event => event.type === 'interaction_request');
    const finished = pending && unwrapEvents(value.events || []).some(event => event.type === 'interaction_finished' && event.data?.request_id === pending.data?.request_id);
    setInteraction(isActive(value.run?.status) && !finished ? pending?.data || null : null);
    if (!preserve) {
      const local = localStorage.getItem('draft:' + id);
      const restoredDraft = local ? JSON.parse(local) : value.draft;
      setDraft(restoredDraft?.text || ''); setAttachments(restoredDraft?.attachments || []);
      setSelectedSkills(value.view?.selectedSkills || []);
      setWorkspaceOpen(Boolean(value.view?.workspaceOpen)); setEditors([]); setActiveFile('');
      const plugin = await call<Plugin>('plugins.load', { id: 'cowork.writing' });
      const restored: Editor[] = [];
      for (const item of value.view?.editors || []) {
        try { const file = await call<FileSession>('file.open', { conversation_id: id, path: item.path, expected_fingerprint: item.fingerprint }); restored.push({ file, text: item.text, saved: item.saved, plugin }); }
        catch (failure) {
          restored.push({ file: { id: '', path: item.path, name: item.path.split(/[\\/]/).pop(), fingerprint: item.fingerprint, payload: { kind: 'text' }, unavailable: true }, text: item.text, saved: item.saved, plugin });
          error('有文件暂时无法重新打开，草稿仍可查看、复制或另存。' + (failure as Error).message);
        }
      }
      if (selectedId.current !== id || generation !== loadGeneration.current) return;
      setEditors(restored); setActiveFile(restored.find(e => e.file.path === value.view?.activeFile)?.file.path || restored[0]?.file.path || '');
      nearBottom.current = !value.view?.scrollTop;
      requestAnimationFrame(() => { if (scroll.current && value.view?.scrollTop) scroll.current.scrollTop = value.view.scrollTop; });
    }
    void refreshFiles(id);
  }
  async function switchConversation(id: string) {
    if (id === selectedId.current) { setRoute('chat'); return; }
    try {
      await saveDraft(); await saveView();
      selectedId.current = id; setLoading(true); setRoute('chat'); setInteraction(null); setEvents([]); setWorkspaceExpanded(false); setShowUsage(false);
      await loadConversation(id);
    } catch (failure) { error((failure as Error).message); } finally { setLoading(false); }
  }
  async function newConversation(workspace?: string) {
    await saveDraft(); await saveView();
    const value = await call<Conversation>('conversation.create', { workspace_dir: workspace });
    selectedId.current = value.id; setCurrent(value); setEvents([]); setDraft(''); setAttachments([]); setEditors([]); setActiveFile('');
    setSelectedSkills([]); setShowUsage(false);
    setWorkspaceOpen(false); setWorkspaceExpanded(false); setInteraction(null); setRoute('chat'); nearBottom.current = true;
    await refreshList(); composer.current?.focus();
    return value;
  }

  useEffect(() => {
    let mounted = true;
    call('app.bootstrap').then(async value => {
      if (!mounted) return;
      setSettings(value.settings); setConversations(value.conversations);
      const id = value.last_conversation;
      if (id && value.conversations.some((c: Conversation) => c.id === id && !c.meta.archived)) { selectedId.current = id; await loadConversation(id); }
      else { const cached = localStorage.getItem('draft:'); if (cached) { const value = JSON.parse(cached); setDraft(value.text || ''); setAttachments(value.attachments || []); } }
      setLoading(false);
    }).catch(failure => { if (mounted) { setServiceError(failure.message); setLoading(false); } });
    return () => { mounted = false; };
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    if (loading) return;
    const id = current?.id || '';
    const value = { text: draft, attachments };
    try { localStorage.setItem('draft:' + id, JSON.stringify(value)); }
    catch { error('本地草稿空间不足，请先保存当前内容。'); }
    const timer = setTimeout(() => { void saveDraft(id, value).catch(failure => error('草稿尚未保存：' + failure.message)); }, 400);
    return () => clearTimeout(timer);
  }, [draft, attachments, current?.id, saveDraft, error, loading]);
  useEffect(() => {
    if (loading || (!editors.length && !current?.view?.editors?.length && !selectedSkills.length)) return;
    const timer = setTimeout(() => { void saveView().catch(failure => error('工作区草稿尚未保存：' + failure.message)); }, 500);
    return () => clearTimeout(timer);
  }, [editors, activeFile, workspaceOpen, selectedSkills, saveView, error, loading]);
  useLayoutEffect(() => { if (nearBottom.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [events, current?.messages, interaction]);

  useEffect(() => window.cowork.onEvents((batch: HostEvent[]) => {
    const relevant: RuntimeEvent[] = [];
    for (const event of batch) {
      if (event.event === 'service_error') setServiceError(event.error || '本地服务已断开。');
      if (event.event === 'close_requested') void closeApp();
      if (event.event === 'reload_requested') void (async () => { try { await saveDraft(); await saveView(); await call('app.reload'); } catch (failure) { error('暂时不能重载，输入和编辑仍保留：' + (failure as Error).message); } })();
      if (event.event === 'runtime' && event.conversation_id === selectedId.current) {
        relevant.push(event.data);
        if (event.data.type === 'interaction_request') setInteraction(event.data.data);
        if (event.data.type === 'interaction_finished') setInteraction((previous: any) => previous?.request_id === event.data.data?.request_id ? null : previous);
        if (event.data.type === 'final') void loadConversation(event.conversation_id!, true).catch(failure => error(failure.message));
      }
      if (event.event === 'conversation_changed') { void refreshList(); if (event.conversation_id === selectedId.current) void loadConversation(event.conversation_id!, true).catch(failure => error(failure.message)); }
      if (event.event === 'run_error' && event.conversation_id === selectedId.current) { error(event.error!); void loadConversation(event.conversation_id!, true); }
      if (event.event === 'workbench') void respondWorkbench(event.data);
    }
    if (relevant.length) setEvents(previous => mergeEvents(previous, relevant));
  }), [refreshList, error]);

  async function respondWorkbench(request: any) {
    // Execution requests are claimed once by the service; replayed events never execute actions.
    let result: any = { ok: false, status: 'unavailable', error: '当前 Web 工作台尚未迁移这项桌面工具。已保存内容不受影响。' };
    try {
      if (request.action === 'list_tabs') {
        const tabs = request.session_id === selectedId.current
          ? editorsRef.current.map(e => ({ id: e.file.id, title: e.file.name, path: e.file.path, kind: 'file' }))
          : ((await call('view.get', {conversation_id: request.session_id})).editors || []).map((item: any) => ({id: item.path, title: item.path.split(/[\\/]/).pop(), path: item.path, kind: 'file'}));
        result = {ok: true, tabs};
      }
    } finally { await call('workbench.result', { request_id: request.request_id, session_id: request.session_id, run_id: request.run_id, token: request.token, result }); }
  }
  async function closeApp() {
    if (closePending.current) return;
    closePending.current = true;
    try {
      await saveDraft(); await saveView();
      const unsaved = editorsRef.current.some(e => e.text !== e.saved);
      const state = await call('app.bootstrap');
      if (unsaved || state.active_runs.length) {
        const choice = await confirm('退出 Cowork？', `${state.active_runs.length ? '正在运行的任务将停止，已保存的回答会保留。' : ''}${unsaved ? '未保存的编辑将作为草稿保留，下次打开可以继续。' : ''}`, ['继续工作', '保存草稿并退出']);
        if (choice !== 1) return;
      }
      await call('app.quit');
    } catch (failure) {
      error((failure as Error).message);
      if (await confirm('保存未完成', '本地服务无法确认保存。输入草稿已保留在界面缓存中；建议继续工作并复制未保存的编辑。', ['继续工作', '仍然退出']) === 1) await call('app.quit');
    } finally { closePending.current = false; }
  }
  async function send() {
    if (sending || (!draft.trim() && !attachments.length)) return;
    setSending(true); setNotice('');
    const submittedText = draft; const submittedAttachments = attachments;
    try {
      let conversation = current;
      if (!conversation) { conversation = await call('conversation.create'); selectedId.current = conversation!.id; setCurrent(conversation); }
      const id = conversation!.id;
      if (isActive(conversation?.run?.status)) {
        const signature = JSON.stringify([id, conversation!.run!.id, submittedText, submittedAttachments]);
        if (pendingSteering.current?.signature !== signature) pendingSteering.current = { id: crypto.randomUUID(), signature };
        const result = await call('conversation.steer', { conversation_id: id, run_id: conversation!.run!.id, attachments: submittedAttachments, message: { id: pendingSteering.current.id, role: 'user', content: submittedText, meta: { kind: 'user_guidance', turn_id: conversation!.run!.id } } });
        const accepted = result.result || result;
        if (!accepted.accepted && !accepted.message_committed) throw new Error('补充消息未被接收，内容已保留。');
        pendingSteering.current = null;
      } else {
        const signature = JSON.stringify([id, submittedText, submittedAttachments, settings?.selected_model_id]);
        if (!pendingSubmission.current || pendingSubmission.current.content !== signature) pendingSubmission.current = { id: crypto.randomUUID(), message: crypto.randomUUID(), content: signature };
        const response = await call('conversation.send', { conversation_id: id, request_id: pendingSubmission.current.id, message_id: pendingSubmission.current.message,
          text: submittedText, attachments: submittedAttachments, model_id: settings?.selected_model_id, skills: selectedSkills });
        if (!response.accepted) throw new Error('消息未被接收，输入内容已保留。');
        pendingSubmission.current = null;
      }
      const storedDraft = JSON.parse(localStorage.getItem('draft:' + id) || '{"text":"","attachments":[]}');
      const cleared = selectedId.current === id ? { text: draftRef.current.text === submittedText ? '' : draftRef.current.text, attachments: draftRef.current.attachments.filter(a => !submittedAttachments.some(s => s.path === a.path)) } : { text: storedDraft.text === submittedText ? '' : storedDraft.text, attachments: (storedDraft.attachments || []).filter((a: Attachment) => !submittedAttachments.some(s => s.path === a.path)) };
      localStorage.setItem('draft:' + id, JSON.stringify(cleared));
      if (selectedId.current === id) { setDraft(cleared.text); setAttachments(cleared.attachments); }
      await saveDraft(id, cleared);
      nearBottom.current = true; await loadConversation(id, true); await refreshList();
    } catch (failure) { error((failure as Error).message); } finally { setSending(false); composer.current?.focus(); }
  }
  async function stop() {
    if (!current?.run) return;
    const id = current.id;
    try { setCurrent(previous => previous ? { ...previous, run: previous.run ? { ...previous.run, status: 'stopping' } : null } : null); await call('conversation.stop', { conversation_id: id, run_id: current.run.id }); await loadConversation(id, true); }
    catch (failure) { error((failure as Error).message); }
  }
  async function attach() {
    try {
      let id = current?.id;
      if (!id) { const value = await call<Conversation>('conversation.create'); id = value.id; selectedId.current = id; setCurrent(value); await refreshList(); }
      const added = await call<Attachment[]>('dialog.attach', { conversation_id: id });
      if (selectedId.current === id) setAttachments(previous => [...previous, ...added]);
    }
    catch (failure) { error((failure as Error).message); }
  }
  async function openFile(path: string) {
    if (editors.some(e => e.file.path === path)) { setActiveFile(path); setWorkspaceOpen(true); return; }
    try {
      const id = selectedId.current;
      const file = await call<FileSession>('file.open', { conversation_id: id, path });
      if (!['text', 'html'].includes(file.payload.kind)) throw new Error('此版本先提供文本与 Markdown 编辑，Office 编辑器正在迁移。文件内容未改变。');
      const plugin = await call<Plugin>('plugins.load', { id: 'cowork.writing' });
      if (selectedId.current !== id) return;
      const text = file.payload.text ?? file.payload.content ?? '';
      setEditors(previous => [...previous, { file, text, saved: text, plugin }]); setActiveFile(file.path); setWorkspaceOpen(true);
    } catch (failure) { error((failure as Error).message); }
  }
  async function saveEditor(path: string) {
    const editor = editorsRef.current.find(e => e.file.path === path);
    if (!editor) return;
    const text = editor.text;
    const id = selectedId.current;
    const result = await call('file.save', { conversation_id: id, id: editor.file.id, payload: text });
    if (selectedId.current !== id) return;
    setEditors(previous => previous.map(e => e.file.path === path ? { ...e, saved: text, file: { ...e.file, fingerprint: result.fingerprint } } : e));
    void refreshFiles();
  }
  async function saveCopy() {
    if (!editor) return;
    try {
      const name = editor.file.name.replace(/\.[^.]+$/, '') + '-恢复-' + Date.now() + '.md';
      const file = await call<FileSession>('workspace.create', { conversation_id: selectedId.current, name, text: editor.text });
      setEditors(previous => previous.map(e => e.file.path === editor.file.path ? { ...e, file, saved: e.text } : e));
      setActiveFile(file.path); await refreshFiles();
    } catch (failure) { error((failure as Error).message); }
  }
  function changeEditor(path: string, text: string) {
    editorsRef.current = editorsRef.current.map(e => e.file.path === path ? { ...e, text } : e);
    setEditors(editorsRef.current);
  }
  async function closeEditor(path: string) {
    const editor = editors.find(e => e.file.path === path);
    if (editor && editor.saved !== editor.text) {
      const choice = await confirm('保存修改？', editor.file.name + ' 中有尚未保存的内容。', ['取消', '不保存并关闭', '保存并关闭']);
      if (choice === 0) return;
      if (choice === 2) { try { await saveEditor(path); } catch (failure) { error((failure as Error).message); return; } }
    }
    setEditors(previous => previous.filter(e => e.file.path !== path));
    if (activeFile === path) setActiveFile(editors.find(e => e.file.path !== path)?.file.path || '');
  }
  async function createDocument() {
    try {
      const id = current?.id || (await newConversation()).id;
      const name = '写作-' + new Date().toLocaleString('sv').replace(/[: ]/g, '-');
      const file = await call<FileSession>('workspace.create', { conversation_id: id, name: name + '.md' });
      await openFile(file.path); await refreshFiles(id);
    } catch (failure) { error((failure as Error).message); }
  }
  async function goRoute(next: string) {
    try {
      await saveDraft(); await saveView(); setRoute(next); setShowUsage(false);
      if (next === 'plugins') setPlugins(await call('plugins.list'));
      if (next === 'settings') setSettings(await call('settings.get'));
    } catch (failure) { error((failure as Error).message); }
  }
  async function applyPlugin() {
    if (!pluginPreview?.token) return;
    try { setPlugins(await call('plugins.install', { token: pluginPreview.token })); setPluginPreview(null); const plugin = await call<Plugin>('plugins.load', { id: 'cowork.writing' }); setEditors(previous => previous.map(e => ({ ...e, plugin }))); }
    catch (failure) { error((failure as Error).message); }
  }

  const messages = displayMessages(current?.messages || [], events, active ? current?.run?.id : undefined);
  const receipts = events.filter(event => event.type === 'interaction_request').map(event => ({ request: event.data,
    outcome: events.find(item => item.type === 'interaction_finished' && item.data?.request_id === event.data?.request_id)?.data })).filter(item => item.outcome);
  const editor = editors.find(e => e.file.path === activeFile);
  const filtered = conversations.filter(c => !c.meta.archived && (!query || c.title.toLocaleLowerCase().includes(query.toLocaleLowerCase())));
  return <div className={`app-shell ${sidebar ? '' : 'sidebar-hidden'}`}>
    {sidebar && <aside className="sidebar"><div className="brand"><span className="brand-symbol">✳</span><strong>Cowork</strong><button className="icon-button" title="收起侧栏" onClick={() => setSidebar(false)}><PanelLeftClose size={17}/></button></div>
      <button className="new-chat" onClick={() => void newConversation().catch(failure => error(failure.message))}><Plus size={18}/>新建聊天</button>
      <label className="search"><Search size={15}/><input aria-label="搜索对话" placeholder="搜索对话" value={query} onChange={e => setQuery(e.target.value)}/></label>
      <div className="sidebar-section">项目与对话<button title="选择项目目录" onClick={async () => { const folder = await call<string | null>('dialog.workspace'); if (folder) await newConversation(folder); }}><Folder size={14}/></button></div>
      <nav className="conversation-list">{filtered.map(c => <button key={c.id} className={`conversation-row ${c.id === current?.id && route === 'chat' ? 'selected' : ''}`} onClick={() => void switchConversation(c.id)}><span>{c.title}</span><small>{c.id === current?.id && active ? <span className="running-dot"/> : new Date(c.updated_at * 1000).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}</small></button>)}{!filtered.length && <p className="sidebar-empty">{query ? '没有匹配的对话' : '从一个新想法开始'}</p>}</nav>
      <div className="sidebar-bottom"><button className={route === 'plugins' ? 'selected' : ''} onClick={() => void goRoute('plugins')}><Puzzle size={17}/>插件与工作台</button><button className={route === 'settings' ? 'selected' : ''} onClick={() => void goRoute('settings')}><SettingsIcon size={17}/>设置<span className="nav-dot" hidden={settings?.models.some(m => m.configured)}/></button></div>
    </aside>}
    <main className="main-shell"><header className="app-header"><div className="header-title">{!sidebar && <button className="icon-button" title="展开侧栏" onClick={() => setSidebar(true)}><PanelLeftOpen size={18}/></button>}
      {route === 'chat' ? rename ? <input autoFocus value={title} onChange={e => setTitle(e.target.value)} onBlur={() => setRename(false)} onKeyDown={async e => { if (e.key === 'Escape') setRename(false); if (e.key === 'Enter' && current) { await call('conversation.rename', { conversation_id: current.id, title }); setCurrent({ ...current, title }); await refreshList(); setRename(false); } }}/> : <button className="title-button" title="点击修改标题" onClick={() => { if (current) { setTitle(current.title); setRename(true); } }}>{current?.title || '今天，想一起完成什么？'}</button> : <h1>{route === 'settings' ? '设置' : '插件与工作台'}</h1>}
      </div><div className="header-actions">{route === 'chat' && current && <><button className={workspaceOpen ? 'toolbar-button selected' : 'toolbar-button'} onClick={() => { setWorkspaceOpen(!workspaceOpen); void refreshFiles(); }}><PanelsTopLeft size={16}/>工作区</button><button className="icon-button" title="归档对话" onClick={async () => { if (await confirm('归档这段对话？', '历史内容会保留，对话将从最近列表中移除。', ['取消', '归档']) === 1) { try { await call('conversation.archive', { conversation_id: current.id }); selectedId.current = ''; setCurrent(null); setDraft(''); setEvents([]); await refreshList(); } catch (failure) { error((failure as Error).message); } } }}><MoreHorizontal size={19}/></button></>}
      <button className="icon-button" title={theme === 'dark' ? '切换浅色' : '切换深色'} onClick={async () => { const next = theme === 'dark' ? 'light' : 'dark'; await call('settings.theme', { theme: next }); setSettings(previous => previous ? { ...previous, theme: next } : null); }}>{theme === 'dark' ? <Sun size={17}/> : <Moon size={17}/>}</button></div></header>
      {serviceError && <div className="service-error"><strong>本地服务连接中断</strong><span>{serviceError}</span><button onClick={() => void call('app.logs')}>查看诊断日志</button></div>}
      {loading ? <div className="loading"><LoaderCircle size={23} className="spin"/><p>正在打开你的工作台…</p></div> : route === 'chat' ? <div className={`work-layout ${workspaceOpen ? 'with-workspace' : ''} ${workspaceExpanded ? 'expanded' : ''}`}>
        <section className="conversation" style={workspaceExpanded ? {display:'none'} : undefined}><div className="message-scroll" ref={scroll} onScroll={() => { if (scroll.current) nearBottom.current = scroll.current.scrollHeight - scroll.current.scrollTop - scroll.current.clientHeight < 120; }}>
          {!messages.length ? <div className="welcome"><div className="welcome-eyebrow"><span className="brand-symbol">✳</span>从一个想法，开始今天的工作</div><h2>今天，想一起完成什么？</h2><p>整理资料、分析内容，或把一个想法做出来。</p><div className="welcome-options"><button onClick={() => { setDraft('帮我整理这些资料，提炼重点并给出下一步建议。'); composer.current?.focus(); }}><BookOpen size={18}/><span>整理资料<small>从信息到清楚的结论</small></span><ChevronRight size={15}/></button><button onClick={() => void createDocument()}><FileText size={18}/><span>开始写作<small>边讨论，边完善成果</small></span><ChevronRight size={15}/></button></div></div> : <div className="messages">{messages.map(message => <Bubble key={message.id} message={message}/>)}
            {active && <div className="run-state"><LoaderCircle size={14} className="spin"/>{current?.run?.status === 'stopping' ? '正在停止，保留已完成内容…' : interaction ? '等待你的回答' : '正在处理…'}</div>}
            {!active && current?.run?.error && <div className="run-error"><span>{current.run.status === 'interrupted' ? '任务已停止' : '这次任务未完成'}</span><p>{runErrorSummary(current.run.error)}</p><details><summary>查看具体原因</summary><pre>{current.run.error}</pre></details><button className="secondary" onClick={() => {setDraft('请继续上一轮未完成的任务。先核对已经生成的文件与完成的操作，保留有效结果，避免重复已完成的操作。');composer.current?.focus();}}>继续这个任务</button></div>}
            {receipts.map(({request, outcome}) => <details className="interaction-receipt" key={request.request_id}><summary><Check size={14}/>{request.title || '问卷与确认'} · {outcome.answered ? '已回答' : outcome.status === 'timeout' ? '已超时' : '已结束'}<ChevronDown size={13}/></summary><p>{request.message}</p>{request.questions?.map((question: any) => <p key={question.id}>{question.question}</p>)}</details>)}
            {interaction && current && <Interaction key={interaction.request_id} request={interaction} conversationId={current.id} done={() => setInteraction(null)} error={error}/>}</div>}
        </div><div className="composer-container">{selectedSkills.length > 0 && <div className="context-tags" aria-label="已选能力">{selectedSkills.map(name => <button key={name} title={`移除能力 ${name}`} onClick={() => setSelectedSkills(previous => previous.filter(item => item !== name))}><Puzzle size={12}/>{name}<X size={12}/></button>)}</div>}{attachments.length > 0 && <div className="attachments">{attachments.map(a => <div key={a.path}><Paperclip size={13}/><span title={a.name}>{a.name}</span><button title="移除附件" onClick={() => setAttachments(previous => previous.filter(item => item.path !== a.path))}><X size={13}/></button></div>)}</div>}
          <div className="composer"><textarea ref={composer} aria-label="消息输入" placeholder={active ? '补充要求，或留下下一步想法…' : '描述任务，或添加文件开始工作…'} value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}/>
            <div className="composer-toolbar"><div className="composer-left"><button title="添加附件" className="icon-button" onClick={() => void attach()}><Plus size={18}/></button><button className="text-tool" title="选择任务能力" onClick={async () => { try { setSkills(await call('skills.list')); setShowSkills(true); } catch (failure) { error((failure as Error).message); } }}><Puzzle size={14}/><span>能力{selectedSkills.length ? ` ${selectedSkills.length}` : ''}</span></button><button className="text-tool" title="执行授权" onClick={async () => { const enabled = !settings?.god_mode; if (enabled && await confirm('启用完全访问？', '当前用户权限范围内的工具操作将不再逐次请求批准。可以随时切回请求批准。', ['取消', '启用完全访问']) !== 1) return; setSettings(await call('settings.permission', { enabled })); }}><ShieldCheck size={14}/><span>{settings?.god_mode ? '完全访问' : '请求批准'}</span></button></div>
            <div className="composer-right"><select aria-label="选择模型" value={settings?.selected_model_id || ''} onChange={async e => setSettings(await call('settings.select_model', { model_id: e.target.value }))}>{settings?.models.map(model => <option key={model.id} value={model.id}>{model.label}{model.configured ? '' : ' · 未配置'}</option>)}</select>{active && (draft.trim() || attachments.length) && <button className="icon-button" title="停止当前任务" onClick={() => void stop()}><Square size={14}/></button>}<button className="send-button" title={active && !draft.trim() && !attachments.length ? '停止任务' : '发送消息'} disabled={sending || current?.run?.status === 'stopping' || (!active && !draft.trim() && !attachments.length)} onClick={() => void (active && !draft.trim() && !attachments.length ? stop() : send())}>{sending ? <LoaderCircle size={18} className="spin"/> : active && !draft.trim() && !attachments.length ? <Square size={16} fill="currentColor"/> : <ArrowUp size={20}/>}</button></div></div>
          </div><div className="composer-footer"><span title={current?.meta.workspace_dir}><Folder size={12}/>{current ? current.meta.workspace_dir?.split(/[\\/]/).pop() : '独立聊天工作目录'}</span><button className="usage-trigger" aria-expanded={showUsage} onClick={() => setShowUsage(!showUsage)}>用量<ChevronDown size={12}/></button><span>Enter 发送 · Shift+Enter 换行</span></div>{settings && <Usage visible={showUsage} events={events} settings={settings} onClose={() => setShowUsage(false)} onSave={async target => setSettings(await call('settings.context', {target_tokens: target}))}/>}</div>
        </section>
        {workspaceOpen && <section className="workspace"><div className="workspace-header"><span><PanelsTopLeft size={15}/>工作区</span><div><button className="icon-button" title="新建写作页" onClick={() => void createDocument()}><Plus size={16}/></button><button className="icon-button" title={workspaceExpanded ? '恢复分栏' : '展开工作区'} onClick={() => setWorkspaceExpanded(!workspaceExpanded)}>{workspaceExpanded ? <Minimize2 size={16}/> : <Maximize2 size={16}/>}</button><button className="icon-button" title="收起工作区" onClick={() => { setWorkspaceOpen(false); setWorkspaceExpanded(false); }}><X size={16}/></button></div></div>
          <div className="file-tabs"><button className={!activeFile ? 'active' : ''} onClick={() => setActiveFile('')}><Folder size={13}/>文件</button>{editors.map(e => <button key={e.file.path} className={activeFile === e.file.path ? 'active' : ''} onClick={() => setActiveFile(e.file.path)}><FileText size={13}/><span>{e.file.name}{e.text !== e.saved ? ' •' : ''}</span><span role="button" tabIndex={0} aria-label={`关闭 ${e.file.name}`} onClick={event => { event.stopPropagation(); void closeEditor(e.file.path); }} onKeyDown={event => { if (event.key === 'Enter') { event.stopPropagation(); void closeEditor(e.file.path); } }}><X size={12}/></span></button>)}</div>
          {editor && (editor.file.conflict || editor.file.unavailable) && <div className="run-error">原文件已变化或暂时无法打开。当前编辑仍保留，可以复制内容或<button className="secondary" onClick={() => void saveCopy()}>另存副本</button>。</div>}
          {editor ? <PluginEditor file={editor.file} plugin={editor.plugin} text={editor.text} theme={theme} onChange={text => changeEditor(editor.file.path, text)} onSave={() => saveEditor(editor.file.path)} error={error}/> : <div className="files"><div className="file-location"><span title={fileTree.folder}>{fileTree.folder?.split(/[\\/]/).pop()}</span><button title="返回工作目录" className="icon-button" onClick={() => void refreshFiles()}><ArrowLeft size={15}/></button><button title="刷新文件" className="icon-button" onClick={() => void refreshFiles(selectedId.current, fileTree.folder)}><RefreshCw size={14}/></button></div>{fileTree.files.map((file: any) => <button className="file-row" key={file.path} onClick={() => void (file.directory ? refreshFiles(selectedId.current, file.path) : openFile(file.path))}>{file.directory ? <Folder size={16}/> : <FileText size={16}/>}<span>{file.name}</span>{file.directory && <ChevronRight size={13}/>}</button>)}{!fileTree.files.length && <div className="empty-workspace"><FileText size={30}/><h3>成果从这里开始</h3><p>生成的文件会出现在这里，也可以直接开始写作。</p><button className="secondary" onClick={() => void createDocument()}><Plus size={14}/>新建写作页</button></div>}</div>}
        </section>}
      </div> : route === 'settings' ? <div className="management"><div className="page-intro"><h2>让工作台适合你</h2><p>连接模型，调整外观，继续熟悉的工作方式。</p></div><section className="settings-section"><div className="section-heading"><h3>模型与服务</h3><button className="secondary" onClick={async () => { try { const value = await call<Settings | null>('dialog.import_settings'); if (value) setSettings(value); } catch (failure) { error((failure as Error).message); } }}><Download size={14}/>导入旧版模型设置</button></div><div className="model-list">{settings?.models.map(model => <div className="model-row" key={model.id}><span className="model-icon">✳</span><div><strong>{model.display_name || model.model_name}</strong><small>{model.channel_display_name} · {model.configured ? '已配置' : '需要 API Key'}</small></div>{model.id === settings.selected_model_id ? <span className="subtle-badge">当前使用</span> : <button className="secondary" onClick={async () => setSettings(await call('settings.select_model', { model_id: model.id }))}>使用</button>}</div>)}</div>
        <form className="model-form" onSubmit={async e => { e.preventDefault(); setFormBusy(true); try { setSettings(await call('settings.save_model', modelForm)); setModelForm(previous => ({ ...previous, api_key: '' })); } catch (failure) { error((failure as Error).message); } finally { setFormBusy(false); } }}><h4>添加模型服务</h4><div className="form-grid"><label>服务名称<input required value={modelForm.name} onChange={e => setModelForm({ ...modelForm, name: e.target.value })}/></label><label>模型名称<input required value={modelForm.model_name} onChange={e => setModelForm({ ...modelForm, model_name: e.target.value })}/></label><label className="wide">接口地址<input required type="url" value={modelForm.base_url} onChange={e => setModelForm({ ...modelForm, base_url: e.target.value })}/></label><label className="wide">API Key<input required type="password" autoComplete="off" value={modelForm.api_key} onChange={e => setModelForm({ ...modelForm, api_key: e.target.value })} placeholder="只保存在本机，不显示在对话中"/></label><label>接口协议<select value={modelForm.api_protocol} onChange={e => setModelForm({ ...modelForm, api_protocol: e.target.value })}><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option></select></label></div><button className="primary" disabled={formBusy}>{formBusy ? '正在保存…' : '保存并使用'}</button></form></section><section className="settings-section"><h3>外观</h3><div className="theme-options">{['light', 'dark'].map(mode => <button key={mode} className={theme === mode ? 'theme-option selected' : 'theme-option'} onClick={async () => { await call('settings.theme', { theme: mode }); setSettings(previous => previous ? { ...previous, theme: mode } : null); }}><div className={`theme-sample ${mode}`}><span/><main><i/><i/><i/></main></div>{mode === 'light' ? '柔和浅色' : '专注深色'}{theme === mode && <Check size={14}/>}</button>)}</div></section><section className="settings-section"><h3>本机数据</h3><p className="muted">新版对话和草稿保存在独立目录，旧版数据保持原样。</p><code className="data-path">{settings?.data_dir}</code><button className="secondary" onClick={() => void call('app.logs')}>打开诊断日志</button></section></div> : <div className="management"><div className="page-intro"><h2>让工作台随想法生长</h2><p>修改一个界面，增加一种工作方式。已有内容继续保留。</p></div><div className="section-heading"><h3>已安装的插件</h3><button className="primary" onClick={async () => { try { const value = await call<Plugin | null>('plugins.preview'); if (value) setPluginPreview(value); } catch (failure) { error((failure as Error).message); } }}><Plus size={15}/>添加本地插件</button></div><div className="plugin-list">{plugins.map(plugin => <section className="plugin-card" key={plugin.id}><div className="plugin-icon"><FileText size={24}/></div><div><h3>{plugin.name}<small>v{plugin.version}</small></h3><p>{plugin.description}</p><div className="button-row"><button className="secondary" onClick={async () => { try { const result = await call('plugins.export', { id: plugin.id }); if (result) setNotice('可编辑源码已保存到：' + result.path + '。可以让 AI 修改后再添加为新版本。'); } catch (failure) { error((failure as Error).message); } }}><Code2 size={14}/>导出可编辑源码</button>{!plugin.builtin && <button className="secondary" onClick={async () => { try { setPlugins(await call('plugins.rollback', { id: plugin.id })); const loaded = await call<Plugin>('plugins.load', { id: 'cowork.writing' }); setEditors(previous => previous.map(e => ({ ...e, plugin: loaded }))); } catch (failure) { error((failure as Error).message); } }}><RefreshCw size={14}/>{plugin.previous ? '恢复上一版' : '恢复内置版本'}</button>}</div></div></section>)}</div><div className="plugin-explainer"><h3>从自己的工作流开始</h3><p>导出写作工作台的源码到项目目录，让 AI 按你的要求修改。选择修改后的文件夹，预览并应用，就能继续使用新版本。</p><p>插件界面与对话运行独立，切换编辑器版本不会重启任务。</p></div></div>}
      {notice && <div className="notice" role="status"><span>{notice}</span><button className="icon-button" title="关闭提示" onClick={() => setNotice('')}><X size={15}/></button></div>}
    </main>
    {ask && <div className="modal-backdrop"><div className="modal" role="dialog" aria-modal="true" aria-label={ask.title}><h2>{ask.title}</h2><p>{ask.detail}</p><div className="button-row">{ask.choices.map((choice, index) => <button key={choice} autoFocus={index === 0} className={index === ask.choices.length - 1 ? 'primary' : 'secondary'} onClick={() => { const resolve = ask.resolve; setAsk(null); resolve(index); }}>{choice}</button>)}</div></div></div>}
    {showSkills && <div className="modal-backdrop" onClick={() => setShowSkills(false)}><div className="modal skill-modal" onClick={e => e.stopPropagation()}><div className="section-heading"><h2>为当前任务选择能力</h2><button className="icon-button" title="关闭" onClick={() => setShowSkills(false)}><X size={18}/></button></div><div className="skill-list">{skills.map(skill => <label key={skill.name}><input type="checkbox" checked={selectedSkills.includes(skill.name)} onChange={e => setSelectedSkills(previous => e.target.checked ? [...previous, skill.name] : previous.filter(name => name !== skill.name))}/><span><strong>{skill.display_name || skill.name}</strong><small>{skill.description}</small></span></label>)}</div><button className="primary" onClick={() => setShowSkills(false)}>完成</button></div></div>}
    {pluginPreview && <div className="modal-backdrop"><div className="modal preview-modal"><div className="section-heading"><div><h2>{pluginPreview.name}</h2><p>v{pluginPreview.version} · 预览使用示例内容，不修改你的文档</p></div><button className="icon-button" title="取消预览" onClick={() => setPluginPreview(null)}><X size={18}/></button></div><div className="preview-frame"><PluginEditor file={{id: 'preview', name: '预览文稿.md', path: '', fingerprint: '', payload: {}}} plugin={pluginPreview} text={previewText} theme={theme} onChange={setPreviewText} onSave={async () => {}} error={error}/></div><div className="button-row"><button className="secondary" onClick={() => setPluginPreview(null)}>取消</button><button className="primary" onClick={() => void applyPlugin()}>应用这个版本</button></div></div></div>}
  </div>;
}
