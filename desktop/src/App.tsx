import { useCallback, useEffect, useLayoutEffect, useRef, useState, useMemo, lazy, Suspense } from 'react';
import { ArrowUp, Square, Plus, Search, ChartColumn, History, Settings as SettingsIcon, PanelsTopLeft, Folder, FileText, ChevronRight, ChevronDown, X, Copy, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Maximize2, Minimize2, Puzzle, Check, LoaderCircle, Paperclip, ShieldCheck, Sun, Moon, RefreshCw, ArrowLeft, Download, Code2, BookOpen, Sparkles, ExternalLink } from 'lucide-react';
import type { Attachment, Conversation, FileSession, HostEvent, Message, Plugin, RuntimeEvent, Settings } from './types';
import { displayMessages, isActive, mergeEvents, messageText, runErrorSummary, unwrapEvents } from './runtime';
import PluginEditor from './PluginEditor';
import Usage from './Usage';
import { Drafts, type Draft } from './drafts';
import DraftRecovery from './DraftRecovery';
import Sidebar from './Sidebar';
import MessageBubble from './MessageBubble';
import HistoryEditor from './HistoryEditor';
import Dialog from './Dialog';
import OfficeEditor from './OfficeEditor';
import FilePreview from './FilePreview';
import Workspace, { emptyWorkspace, type WorkspaceView } from './Workspace';
import Composer from './Composer';
import Interaction from './Interaction';
import { themeStyle } from './theme';
import ThemePreview from './ThemePreview';
const Management = lazy(() => import('./Management'));
const managementNames: Record<string, string> = { settings: '设置', capabilities: '能力', library: '资料库', favorites: '常用任务', team: '团队空间' };

const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
type Ask = { title: string; detail: string; choices: string[]; resolve: (index: number) => void };
type Editor = { file: FileSession; text: string; saved: string; plugin: Plugin };

export default function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [hasMoreConversations, setHasMoreConversations] = useState(false);
  const [runningConversations, setRunningConversations] = useState<Set<string>>(new Set());
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [route, setRoute] = useState('chat');
  const [loading, setLoading] = useState(true);
  const [serviceError, setServiceError] = useState('');
  const [notice, setNotice] = useState('');
  const [draft, updateDraftText] = useState('');
  const [attachments, updateAttachments] = useState<Attachment[]>([]);
  const [drafts] = useState(() => new Drafts(call, localStorage));
  const [draftConflict, setDraftConflict] = useState(false);
  const [sending, setSending] = useState(false);
  const [query, setQuery] = useState('');
  const [sidebar, setSidebar] = useState(true);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [workspaceExpanded, setWorkspaceExpanded] = useState(false);
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView>(emptyWorkspace);
  const changeWorkspace = useCallback((value: Partial<WorkspaceView>) => setWorkspaceView(previous => ({...previous, ...value})), []);
  const [fileTree, setFileTree] = useState<any>({ files: [] });
  const [editors, setEditors] = useState<Editor[]>([]);
  const [activeFile, setActiveFile] = useState('');
  const [fileChanged, setFileChanged] = useState(false);
  const [interaction, setInteraction] = useState<any>(null);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [historyEdit, setHistoryEdit] = useState<{ conversationId: string; prepared: any } | null>(null);
  const [historyUndo, setHistoryUndo] = useState<{ conversationId: string; token: string } | null>(null);
  const [olderLoading, setOlderLoading] = useState(false);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [pluginPreview, setPluginPreview] = useState<Plugin | null>(null);
  const [themeCandidate, setThemeCandidate] = useState<any>(null), [themePreview, setThemePreview] = useState<any>(null);
  const [previewText, setPreviewText] = useState('# 让想法成为成果\n\n这里是独立的预览内容。\n\n## 今天的工作\n\n- 整理资料\n- 写下结论\n\n**继续完善。**');
  const [showUsage, setShowUsage] = useState(false);
  const [headerPanel, setHeaderPanel] = useState('');
  const [messageQuery, setMessageQuery] = useState('');
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
  const editorFlush = useRef(new Map<string, () => Promise<string>>());
  const registerEditorFlush = useCallback((path: string, flush?: () => Promise<string>) => { if (flush) editorFlush.current.set(path, flush); else editorFlush.current.delete(path); }, []);
  const stateRef = useRef({ current, settings, workspaceOpen, activeFile, selectedSkills, workspaceView });
  stateRef.current = { current, settings, workspaceOpen, activeFile, selectedSkills, workspaceView };
  editorsRef.current = editors;
  draftRef.current = { text: draft, attachments };
  const pendingSteering = useRef<{ id: string; signature: string } | null>(null);
  const closePending = useRef(false);
  const viewRevisions = useRef(new Map<string, number>());
  const viewQueue = useRef(Promise.resolve<any>(null));
  const theme = settings?.theme || 'light';
  const leaveManagement = useRef<() => Promise<boolean>>(async () => true);
  const registerLeave = useCallback((callback: () => Promise<boolean>) => { leaveManagement.current = callback; }, []);
  const active = isActive(current?.run?.status);
  useEffect(() => {
    setFileChanged(false);
    if (!activeFile || !current?.id || !workspaceOpen) return;
    let alive = true;
    const check = async () => {
      const item = editorsRef.current.find(item => item.file.path === activeFile);
      if (!item || item.file.unavailable) return;
      try { const result = await call('file.describe', {conversation_id: current.id, path: activeFile}); if (alive) setFileChanged(result.fingerprint !== item.file.fingerprint); }
      catch { if (alive) setFileChanged(true); }
    };
    const timer = setInterval(() => void check(), 5000);
    return () => {alive = false; clearInterval(timer);};
  }, [activeFile, current?.id, workspaceOpen]);
  const error = useCallback((message: string) => setNotice(message), []);
  const confirm = useCallback((title: string, detail: string, choices: string[]) => new Promise<number>(resolve => setAsk({ title, detail, choices, resolve })), []);

  function applyDraft(value: Draft, dirty = true) {
    draftRef.current = value;
    updateDraftText(value.text); updateAttachments(value.attachments);
    if (dirty) {
      try { drafts.change(selectedId.current, value); }
      catch { error('本机草稿缓存无法写入，请保存或复制当前内容。'); }
    }
  }
  function setDraft(text: string) { applyDraft({ ...draftRef.current, text }); }
  function setAttachments(value: Attachment[] | ((previous: Attachment[]) => Attachment[])) {
    applyDraft({ ...draftRef.current, attachments: typeof value === 'function' ? value(draftRef.current.attachments) : value });
  }

  const refreshList = useCallback(async () => {
    const page = await call<Conversation[]>('conversation.list');
    setConversations(previous => [...page, ...previous.slice(500).filter(item => !page.some(next => next.id === item.id))]);
    setHasMoreConversations(page.length === 500);
  }, []);
  async function moreConversations() {
    try {
      const page = await call<Conversation[]>('conversation.list', { offset: conversations.length });
      setConversations(previous => [...previous, ...page.filter(item => !previous.some(current => current.id === item.id))]);
      setHasMoreConversations(page.length === 500);
    } catch (failure) { error((failure as Error).message); }
  }
  async function loadOlder() {
    const conversation = stateRef.current.current;
    if (!conversation?.has_more || olderLoading) return;
    setOlderLoading(true);
    const element = scroll.current, oldHeight = element?.scrollHeight || 0, oldTop = element?.scrollTop || 0;
    nearBottom.current = false;
    try {
      const older = await call<Conversation>('conversation.get', { conversation_id: conversation.id, before: conversation.before });
      if (selectedId.current !== conversation.id) return;
      setCurrent(previous => {
        if (!previous || previous.id !== conversation.id) return previous;
        const prepend = (old: Message[] = [], recent: Message[] = []) => [...old.filter(item => !recent.some(next => next.id === item.id)), ...recent];
        return { ...previous, before: older.before, has_more: older.has_more, messages: prepend(older.messages, previous.messages),
          render_messages: prepend(older.render_messages, previous.render_messages), message_positions: { ...older.message_positions, ...previous.message_positions } };
      });
      setEvents(previous => mergeEvents(unwrapEvents(older.events || []), previous));
      requestAnimationFrame(() => { if (element) element.scrollTop = oldTop + element.scrollHeight - oldHeight; });
    } catch (failure) { error((failure as Error).message); } finally { setOlderLoading(false); }
  }
  const messageActionRef = useRef<(action: string, message: Message) => Promise<void>>(async () => {});
  const fileActionRef = useRef<(path: string) => Promise<void>>(async () => {});
  const onMessageAction = useCallback((action: string, message: Message) => { void messageActionRef.current(action, message); }, []);
  const onMessageFile = useCallback((path: string) => { void fileActionRef.current(path); }, []);
  messageActionRef.current = async (action, message) => {
    const id = selectedId.current;
    if (action === 'tool') { setWorkspaceOpen(true); changeWorkspace({tasksOpen: true, task: {...stateRef.current.workspaceView.task, section: 'calls', callId: message.tool_call_id, runId: message.meta?.turn_id, agentId: undefined}}); return; }
    try {
      await saveDraft(); await saveView();
      if (action === 'branch') {
        const result = await call<Conversation>('conversation.branch', { conversation_id: id, message_id: message.id });
        await refreshList(); await switchConversation(result.id); return;
      }
      const prepared = await call('history.prepare', { conversation_id: id, message_id: message.id });
      if (selectedId.current !== id) return;
      if (action === 'edit') { setHistoryEdit({ conversationId: id, prepared }); return; }
      const retry = action === 'retry';
      if (await confirm(retry ? '从这里重新生成？' : '删除此处及后续对话？', retry
        ? '将替换这一轮回答及后续对话。已生成的文件会保留，重新生成可能再次执行工具。'
        : `将移除此处及后续 ${prepared.downstream} 条消息。工作目录中的文件会保留。`, ['取消', retry ? '替换并重新生成' : '删除']) !== 1) return;
      if (retry) {
        const source = prepared.source_user;
        const result = await drafts.submit(id, { text: source.meta?.display_content ?? source.content,
          attachments: source.meta?.desktop_attachments || (source.meta?.user_added_files || []).map((path: string) => ({ path, name: path.split(/[\\/]/).pop() })),
          model_id: stateRef.current.current?.meta.selected_model_id || stateRef.current.settings?.selected_model_id,
          skills: stateRef.current.selectedSkills, consume_draft: false,
          rewrite: { hash: prepared.hash, revision: prepared.revision, message_id: prepared.rewrite_message_id } });
        if (result.undo) setHistoryUndo({ conversationId: id, token: result.undo });
        await loadConversation(id, true);
      } else {
        const result = await call('history.delete', { conversation_id: id, message_id: message.id, guard: { hash: prepared.hash, revision: prepared.revision } });
        setHistoryUndo({ conversationId: id, token: result.undo }); setCurrent(result.conversation); setEvents(unwrapEvents(result.conversation.events || []));
      }
    } catch (failure) { error((failure as Error).message); }
  };
  fileActionRef.current = openFile;
  async function addProject() {
    try {
      const path = await call<string | null>('dialog.workspace');
      if (!path) return;
      await call('project.save', { path }); setSettings(await call('settings.get')); await newConversation(path);
    } catch (failure) { error((failure as Error).message); }
  }
  async function conversationAction(action: string, item: Conversation) {
    try {
      if (action === 'rename') { await switchConversation(item.id); setTitle(item.title); setRename(true); return; }
      if (action === 'delete' && await confirm('移到回收站？', '聊天将从列表移除，可以在归档中恢复。工作目录中的文件会保留。', ['取消', '移到回收站']) !== 1) return;
      await saveDraft(); await saveView();
      await call('conversation.' + action, { conversation_id: item.id, ...(action === 'pin' ? { pinned: !item.meta.pinned } : {}) });
      if (selectedId.current === item.id && ['archive', 'delete'].includes(action)) {
        selectedId.current = ''; setCurrent(null); setEvents([]); setInteraction(null); setEditors([]); setActiveFile(''); setWorkspaceOpen(false);
        const state = await call('app.bootstrap');
        const entry = drafts.restore('', state.home_draft); applyDraft(entry.value, false); setDraftConflict(entry.conflict);
      }
      await refreshList();
    } catch (failure) { error((failure as Error).message); }
  }
  const saveDraft = useCallback((id = selectedId.current, value?: Draft) => {
    if (value) drafts.change(id, value);
    return drafts.save(id);
  }, []);
  const saveView = useCallback(async () => {
    const id = stateRef.current.current?.id;
    if (!id) return;
    const snapshots = new Map<string, string>();
    for (const [path, flush] of editorFlush.current) snapshots.set(path, await flush());
    const value = { workspaceOpen: stateRef.current.workspaceOpen, workspace: stateRef.current.workspaceView,
      activeFile: stateRef.current.activeFile, selectedSkills: stateRef.current.selectedSkills, scrollTop: scroll.current?.scrollTop || 0,
      editors: editorsRef.current.map(editor => ({ path: editor.file.path, text: snapshots.get(editor.file.path) ?? editor.text, saved: editor.saved, fingerprint: editor.file.fingerprint })) };
    viewQueue.current = viewQueue.current.catch(() => undefined).then(async () => {
      const receipt = await call('view.save', { conversation_id: id, value, revision: viewRevisions.current.get(id) ?? -1 });
      viewRevisions.current.set(id, receipt.revision);
    });
    await viewQueue.current;
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
    const prior = stateRef.current.current;
    const retained = preserve && prior?.id === id ? (prior.messages || []).filter(message => (prior.message_positions?.[message.id] ?? Infinity) < (value.ledger_start || 0)) : [];
    const retainedIds = new Set(retained.map(message => message.id));
    const combined = { ...value, messages: [...retained, ...(value.messages || [])],
      render_messages: [...(prior?.render_messages || []).filter(message => retainedIds.has(message.id)), ...(value.render_messages || value.messages || [])],
      message_positions: { ...(retained.length ? prior?.message_positions : {}), ...value.message_positions },
      ...(retained.length ? { before: prior?.before, has_more: prior?.has_more } : {}) };
    const visibleRuns = new Set(combined.messages.map(message => message.meta?.turn_id));
    setCurrent(combined); setEvents(previous => preserve ? mergeEvents(unwrapEvents(value.events || []), previous.filter(event => visibleRuns.has(event.run_id))) : unwrapEvents(value.events || []));
    const pending = [...unwrapEvents(value.events || [])].reverse().find(event => event.type === 'interaction_request');
    const finished = pending && unwrapEvents(value.events || []).some(event => event.type === 'interaction_finished' && event.data?.request_id === pending.data?.request_id);
    setInteraction(isActive(value.run?.status) && !finished ? pending?.data || null : null);
    if (!preserve) {
      drafts.restore(id, { value: value.draft || { text: '', attachments: [] }, revision: value.draft_revision ?? -1 });
      await drafts.recoverSubmission(id);
      if (selectedId.current !== id || generation !== loadGeneration.current) return;
      applyDraft(drafts.get(id).value, false); setDraftConflict(drafts.get(id).conflict);
      viewRevisions.current.set(id, value.view_revision ?? -1);
      setSelectedSkills(value.view?.selectedSkills || []);
      setWorkspaceOpen(Boolean(value.view?.workspaceOpen)); setWorkspaceView({...emptyWorkspace(), ...value.view?.workspace}); setEditors([]); setActiveFile('');
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
    if (!await leaveManagement.current()) return;
    if (id === selectedId.current) { setRoute('chat'); return; }
    try {
      await saveDraft(); await saveView();
      selectedId.current = id; setLoading(true); setRoute('chat'); setInteraction(null); setEvents([]); setWorkspaceExpanded(false); setShowUsage(false);
      await loadConversation(id);
    } catch (failure) { error((failure as Error).message); } finally { setLoading(false); }
  }
  async function newConversation(workspace?: string) {
    if (!await leaveManagement.current()) return;
    await saveDraft(); await saveView();
    const value = await call<Conversation>('conversation.create', { workspace_dir: workspace });
    selectedId.current = value.id; setCurrent(value); setEvents([]);
    drafts.restore(value.id, { value: { text: '', attachments: [] }, revision: -1 });
    applyDraft({ text: '', attachments: [] }, false); setDraftConflict(false); viewRevisions.current.set(value.id, -1);
    setEditors([]); setActiveFile(''); setWorkspaceView(emptyWorkspace());
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
      setHasMoreConversations(value.conversations.length === 500);
      setRunningConversations(new Set(value.active_runs.map((run: any) => run.conversation_id)));
      const id = value.last_conversation;
      if (id && value.conversations.some((c: Conversation) => c.id === id && !c.meta.archived)) { selectedId.current = id; await loadConversation(id); }
      else { const entry = drafts.restore('', value.home_draft); applyDraft(entry.value, false); setDraftConflict(entry.conflict); }
      setLoading(false);
      requestAnimationFrame(() => { void call('app.ready').catch(failure => error(failure.message)); });
    }).catch(failure => { if (mounted) { setServiceError(failure.message); setLoading(false); } });
    return () => { mounted = false; };
  }, []);
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    let mounted = true;
    async function apply() {
      try { const inventory = await call('themes.list');
        if (mounted) setThemeCandidate(inventory.preview);
        const mode = inventory.appearance.mode;
        const dark = mode === 'dark' || mode === 'system' && media.matches;
        const resolved = await call('themes.resolve', { dark });
        if (!mounted) return;
        document.documentElement.dataset.theme = dark ? 'dark' : 'light';
        document.documentElement.dataset.density = resolved.density || 'standard';
        for (const [key, value] of Object.entries(themeStyle(resolved))) document.documentElement.style.setProperty(key, String(value));
      } catch (failure) { if (mounted) error('外观加载失败：' + (failure as Error).message); }
    }
    void apply(); media.addEventListener('change', apply);
    const unsubscribe = window.cowork.onEvents(events => {
      if (events.some(event => event.event === 'appearance_changed')) void apply();
      else if (events.some(event => event.event === 'runtime' && ['tool_result', 'final'].includes(event.data?.type))) void call('themes.preview').then(value => {if (mounted) setThemeCandidate(value.preview);}).catch(failure => error(failure.message));
    });
    return () => { mounted = false; media.removeEventListener('change', apply); unsubscribe(); };
  }, [theme]);
  useEffect(() => {
    if (loading) return;
    const id = current?.id || '';
    const timer = setTimeout(() => { void saveDraft(id).catch(failure => error('草稿尚未保存：' + failure.message)); }, 400);
    return () => clearTimeout(timer);
  }, [draft, attachments, current?.id, saveDraft, error, loading]);
  useEffect(() => {
    if (loading || !current) return;
    const timer = setTimeout(() => { void saveView().catch(failure => error('工作区草稿尚未保存：' + failure.message)); }, 500);
    return () => clearTimeout(timer);
  }, [editors, activeFile, workspaceOpen, workspaceView, selectedSkills, saveView, error, loading]);
  useLayoutEffect(() => { if (nearBottom.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [events, current?.messages, interaction]);

  useEffect(() => window.cowork.onEvents((batch: HostEvent[]) => {
    const relevant: RuntimeEvent[] = [];
    for (const event of batch) {
      if (event.conversation_id && ['runtime', 'run_error'].includes(event.event)) setRunningConversations(previous => {
        const next = new Set(previous);
        if (event.event === 'run_error' || event.data?.type === 'final') next.delete(event.conversation_id!); else next.add(event.conversation_id!);
        return next;
      });
      if (event.event === 'service_error') setServiceError(event.error || '本地服务已断开。');
      if (event.event === 'service_reconnecting') setServiceError('连接暂时中断，正在重新连接后台…');
      if (event.event === 'service_reconnected') { setServiceError(''); void refreshList(); if (selectedId.current) void loadConversation(selectedId.current, true).catch(failure => error(failure.message)); }
      if (event.event === 'window_hidden') void (async () => { try { await saveDraft(); await saveView(); } catch (failure) { error((failure as Error).message); } })();
      if (event.event === 'close_requested') void closeApp();
      if (event.event === 'update_install_requested') void closeApp(true);
      if (event.event === 'reload_requested') void (async () => { try { await saveDraft(); await saveView(); await call('app.reload'); } catch (failure) { error('暂时不能重载，输入和编辑仍保留：' + (failure as Error).message); } })();
      if (event.event === 'runtime' && event.conversation_id === selectedId.current) {
        relevant.push(event.data);
        if (event.data.type === 'interaction_request') setInteraction(event.data.data);
        if (event.data.type === 'interaction_finished') setInteraction((previous: any) => previous?.request_id === event.data.data?.request_id ? null : previous);
        if (event.data.type === 'final') void loadConversation(event.conversation_id!, true).catch(failure => error(failure.message));
      }
      if (event.event === 'conversation_changed') { void refreshList(); if (event.conversation_id === selectedId.current) void loadConversation(event.conversation_id!, true).catch(failure => error(failure.message)); }
      if (event.event === 'run_error' && event.conversation_id === selectedId.current) { error(event.error!); void loadConversation(event.conversation_id!, true); }
    }
    if (relevant.length) setEvents(previous => mergeEvents(previous, relevant));
  }), [refreshList, error]);

  async function closeApp(installUpdate = false) {
    if (closePending.current) return;
    closePending.current = true;
    try {
      await saveDraft(); await saveView();
      const unsaved = editorsRef.current.some(e => e.text !== e.saved);
      const state = await call('app.bootstrap');
      if (installUpdate) {
        if (state.active_runs.length) {error('请等后台任务结束后再更新；草稿和编辑已保留。'); return;}
        if (!await leaveManagement.current()) return;
        if (await confirm('重启并更新 Cowork？', '草稿和未保存编辑已保留。应用将关闭，安装完成后自动重新打开。', ['继续工作', '重启并更新']) !== 1) return;
        await call('app.install_update'); return;
      }
      if (unsaved || state.active_runs.length) {
        const choice = await confirm('退出 Cowork？', `${state.active_runs.length ? '后台任务会继续运行，重新打开后可以查看进度。' : ''}${unsaved ? '未保存的编辑已作为草稿保留，下次打开可以继续。' : ''}`, ['继续工作', '退出界面']);
        if (choice !== 1) return;
      }
      await call('app.quit');
    } catch (failure) {
      error((failure as Error).message);
      if (installUpdate) return;
      if (await confirm('保存未完成', '本地服务无法确认保存。输入草稿已保留在界面缓存中；建议继续工作并复制未保存的编辑。', ['继续工作', '仍然退出']) === 1) await call('app.quit');
    } finally { closePending.current = false; }
  }
  async function send() {
    if (sending || (!draft.trim() && !attachments.length)) return;
    setSending(true); setNotice('');
    const submittedText = draft; const submittedAttachments = attachments;
    try {
      let conversation = current;
      if (!conversation) {
        await saveDraft('');
        conversation = await call('conversation.create'); selectedId.current = conversation!.id; setCurrent(conversation);
        drafts.restore(conversation!.id, { value: { text: '', attachments: [] }, revision: -1 });
        drafts.change(conversation!.id, drafts.get('').value);
        viewRevisions.current.set(conversation!.id, -1);
      }
      const id = conversation!.id;
      if (isActive(conversation?.run?.status)) {
        const signature = JSON.stringify([id, conversation!.run!.id, submittedText, submittedAttachments]);
        if (pendingSteering.current?.signature !== signature) pendingSteering.current = { id: crypto.randomUUID(), signature };
        const result = await call('conversation.steer', { conversation_id: id, run_id: conversation!.run!.id, attachments: submittedAttachments, message: { id: pendingSteering.current.id, role: 'user', content: submittedText, meta: { kind: 'user_guidance', turn_id: conversation!.run!.id } } });
        const accepted = result.result || result;
        if (!accepted.accepted && !accepted.message_committed) throw new Error('补充消息未被接收，内容已保留。');
        pendingSteering.current = null;
        const value = drafts.get(id).value;
        drafts.change(id, { text: value.text === submittedText ? '' : value.text,
          attachments: value.attachments.filter(file => !submittedAttachments.some(sent => sent.path === file.path)) });
      } else {
        await drafts.submit(id, { text: submittedText, attachments: submittedAttachments,
          model_id: conversation?.meta?.selected_model_id || settings?.selected_model_id, skills: selectedSkills });
      }
      if (selectedId.current === id) applyDraft(drafts.get(id).value, false);
      await saveDraft(id);
      if (!current) { drafts.change('', { text: '', attachments: [] }); await saveDraft(''); }
      nearBottom.current = true; await loadConversation(id, true); await refreshList();
    } catch (failure) { error((failure as Error).message); } finally { setSending(false); composer.current?.focus(); }
  }
  async function stop() {
    if (!current?.run) return;
    const id = current.id;
    try { setCurrent(previous => previous ? { ...previous, run: previous.run ? { ...previous.run, status: 'stopping' } : null } : null); await call('conversation.stop', { conversation_id: id, run_id: current.run.id }); await loadConversation(id, true); }
    catch (failure) { error((failure as Error).message); }
  }
  async function resolveDraft(keepLocal: boolean) {
    const id = selectedId.current;
    try {
      const data = id ? await call('conversation.get', { conversation_id: id }) : await call('app.bootstrap');
      const remote = id ? { value: data.draft, revision: data.draft_revision } : data.home_draft;
      const value = drafts.resolve(id, keepLocal, remote);
      if (id === selectedId.current) { applyDraft(value, false); setDraftConflict(false); }
      if (keepLocal) await saveDraft(id);
    } catch (failure) { error((failure as Error).message); }
  }
  async function recoverSend(retry = false) {
    const id = selectedId.current;
    try {
      const pending = drafts.get(id).pending;
      if (!pending) return;
      const receipt = retry ? await drafts.submit(id, JSON.parse(pending.signature)) : await drafts.recoverSubmission(id);
      if (receipt?.accepted) {
        if (selectedId.current === id) { applyDraft(drafts.get(id).value, false); setDraftConflict(false); }
        await loadConversation(id, true); await saveDraft(id);
      } else error('尚未找到接收记录，可重试原消息。当前新输入会保留。');
    } catch (failure) { error((failure as Error).message); }
  }
  const creatingConversation = useRef<Promise<string> | null>(null);
  async function ensureConversation(): Promise<string> {
    if (selectedId.current) return selectedId.current;
    if (creatingConversation.current) return creatingConversation.current;
    creatingConversation.current = (async () => {
      await saveDraft('');
      const value = await call<Conversation>('conversation.create');
      drafts.restore(value.id, {value: {text: '', attachments: []}, revision: -1});
      drafts.change(value.id, draftRef.current); viewRevisions.current.set(value.id, -1);
      selectedId.current = value.id; setCurrent(value); await refreshList();
      return value.id;
    })();
    try { return await creatingConversation.current; } finally { creatingConversation.current = null; }
  }
  async function preferences(value: Record<string, any>) {
    const id = await ensureConversation();
    const result = await call('conversation.preferences', {conversation_id: id, value});
    if (selectedId.current === id) setCurrent(previous => previous?.id === id ? {...previous, meta: {...previous.meta, ...result.meta}} : previous);
  }
  async function attach() {
    try { const id = await ensureConversation(); const added = await call<Attachment[]>('dialog.attach', {conversation_id: id}); if (selectedId.current === id) setAttachments(previous => [...previous, ...added.filter(item => !previous.some(old => old.path === item.path))]); }
    catch (failure) { error((failure as Error).message); }
  }
  async function activateFile(path: string) {
    try { await saveView(); setActiveFile(path); changeWorkspace({selected: 'file:' + path}); } catch (failure) { error((failure as Error).message); }
  }
  async function openFile(path: string) {
    try { await saveView(); } catch (failure) { error((failure as Error).message); return; }
    if (editors.some(e => e.file.path === path)) { setActiveFile(path); changeWorkspace({selected: 'file:' + path, ...(!workspaceView.filesPinned ? {filesOpen: false} : {})}); setWorkspaceOpen(true); return; }
    try {
      const id = selectedId.current;
      const file = await call<FileSession>('file.open', { conversation_id: id, path });

      const plugin = await call<Plugin>('plugins.load', { id: 'cowork.writing' });
      if (selectedId.current !== id) return;
      const text = file.payload.text ?? file.payload.content ?? '';
      setEditors(previous => [...previous, { file, text, saved: text, plugin }]); setActiveFile(file.path); changeWorkspace({selected: 'file:' + file.path, ...(!workspaceView.filesPinned ? {filesOpen: false} : {})}); setWorkspaceOpen(true);
    } catch (failure) { error((failure as Error).message); }
  }
  async function saveEditor(path: string) {
    const editor = editorsRef.current.find(e => e.file.path === path);
    if (!editor || editor.file.payload.read_only) return;
    const text = editorFlush.current.has(path) ? await editorFlush.current.get(path)!() : editor.text;
    if (!text && !['text', 'html'].includes(editor.file.payload.kind)) return;
    const id = selectedId.current;
    const result = await call('file.save', { conversation_id: id, id: editor.file.id, payload: ['text', 'html'].includes(editor.file.payload.kind) ? text : JSON.parse(text) });
    if (selectedId.current !== id) return;
    setEditors(previous => previous.map(e => e.file.path === path ? { ...e, saved: text, file: { ...e.file, fingerprint: result.fingerprint } } : e));
    void refreshFiles();
  }
  async function reloadFile() {
    const id = selectedId.current, item = editorsRef.current.find(item => item.file.path === activeFile);
    if (!item) return;
    if (item.text !== item.saved && await confirm('重新载入文件？', '将用磁盘上的内容替换此文件的编辑草稿。可以取消并先另存副本。', ['取消', '重新载入']) !== 1) return;
    try {
      const file = await call<FileSession>('file.open', {conversation_id: id, path: item.file.path});
      if (selectedId.current !== id) return;
      const text = file.payload.text ?? file.payload.content ?? '';
      setEditors(previous => previous.map(editor => editor.file.path === item.file.path ? {...editor, file, text, saved: text} : editor));
      setFileChanged(false);
    } catch (failure) {error('刷新未完成，已有预览和草稿仍保留。' + (failure as Error).message);}
  }
  async function saveCopy() {
    if (!editor) return;
    try {
      const text = editorFlush.current.has(editor.file.path) ? await editorFlush.current.get(editor.file.path)!() : editor.text;
      const extension = editor.file.name.match(/\.[^.]+$/)?.[0] || '.txt';
      const name = editor.file.name.replace(/\.[^.]+$/, '') + '-恢复-' + Date.now() + extension;
      const file = await call<FileSession>('file.save_copy', { conversation_id: selectedId.current, id: editor.file.id, name, payload: ['text', 'html'].includes(editor.file.payload.kind) ? text : JSON.parse(text) });
      setEditors(previous => previous.map(e => e.file.path === editor.file.path ? { ...e, file, text, saved: text } : e));
      setActiveFile(file.path); await refreshFiles();
    } catch (failure) { error((failure as Error).message); }
  }
  function changeEditor(path: string, text: string) {
    editorsRef.current = editorsRef.current.map(e => e.file.path === path ? { ...e, text } : e);
    setEditors(editorsRef.current);
  }
  async function closeEditor(path: string) {
    try { await saveView(); } catch (failure) { error((failure as Error).message); return; }
    const editor = editors.find(e => e.file.path === path);
    if (editor && editor.saved !== editor.text) {
      const choice = await confirm('保存修改？', editor.file.name + ' 中有尚未保存的内容。', ['取消', '不保存并关闭', '保存并关闭']);
      if (choice === 0) return;
      if (choice === 2) { try { await saveEditor(path); } catch (failure) { error((failure as Error).message); return; } }
    }
    setEditors(previous => previous.filter(e => e.file.path !== path));
    if (activeFile === path) { const next = editors.find(e => e.file.path !== path)?.file.path || ''; setActiveFile(next); changeWorkspace({selected: next ? 'file:' + next : 'new', newOpen: true}); }
  }
  async function createDocument() {
    try {
      const id = await ensureConversation();
      if (!id) return;
      const name = '写作-' + new Date().toLocaleString('sv').replace(/[: ]/g, '-');
      const file = await call<FileSession>('workspace.create', { conversation_id: id, name: name + '.md' });
      await openFile(file.path); await refreshFiles(id);
    } catch (failure) { error((failure as Error).message); }
  }
  async function goRoute(next: string) {
    if (next !== route && !await leaveManagement.current()) return;
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

  const messages = useMemo(() => displayMessages(current?.render_messages || current?.messages || [], events, active ? current?.run?.id : undefined), [current?.render_messages, current?.messages, events, active, current?.run?.id]);
  const questions = events.filter(event => event.type === 'interaction_request').filter((event, index, all) => all.findIndex(item => item.data?.request_id === event.data?.request_id) === index).map(event => ({request: event.data, runId: event.run_id,
    outcome: events.find(item => item.type === 'interaction_finished' && item.data?.request_id === event.data?.request_id)?.data}));
  const editor = editors.find(e => e.file.path === activeFile);
  const filtered = conversations.filter(c => !c.meta.archived && (!query || c.title.toLocaleLowerCase().includes(query.toLocaleLowerCase())));
  return <div className={`app-shell ${sidebar ? '' : 'sidebar-hidden'}`}>
    {sidebar && <Sidebar conversations={conversations} projects={settings?.projects || []} selected={current?.id || ''} route={route} running={runningConversations}
      onSelect={id => void switchConversation(id)} onNew={workspace => void newConversation(workspace).catch(failure => error(failure.message))}
      onRoute={next => void goRoute(next)} onCollapse={() => setSidebar(false)} onProject={() => void addProject()}
      onAction={(action, item) => void conversationAction(action, item)} onMore={moreConversations} hasMore={hasMoreConversations}/>}
    <main className={`main-shell ${route === 'chat' && workspaceOpen ? 'chat-workspace' : ''} ${workspaceExpanded && route === 'chat' ? 'workspace-expanded' : ''}`}><header className="app-header"><div className="header-title">{!sidebar && <button className="icon-button" title="展开侧栏" onClick={() => setSidebar(true)}><PanelLeftOpen size={18}/></button>}
      {route === 'chat' ? rename ? <input autoFocus value={title} onChange={e => setTitle(e.target.value)} onBlur={() => setRename(false)} onKeyDown={async e => { if (e.key === 'Escape') setRename(false); if (e.key === 'Enter' && current) { await call('conversation.rename', { conversation_id: current.id, title }); setCurrent({ ...current, title }); await refreshList(); setRename(false); } }}/> : <button className="title-button" title="点击修改标题" onClick={() => { if (current) { setTitle(current.title); setRename(true); } }}>{current?.has_messages || current?.messages?.length ? current.title : ''}</button> : <h1>{managementNames[route] || '插件与工作台'}</h1>}
      </div><div className="header-actions">{themeCandidate && <button className="toolbar-button" title="查看 AI 主题候选" onClick={async () => {try {const value = await call('themes.preview'); setThemeCandidate(value.preview); if (value.preview) setThemePreview(value);} catch (failure) {error((failure as Error).message);}}}><Sparkles size={16}/>主题预览</button>}{route === 'chat' && <>
      {messages.length > 0 && <><button className="icon-button" title="用量" aria-expanded={showUsage} onClick={() => {setShowUsage(!showUsage); setHeaderPanel('');}}><ChartColumn size={17}/></button><button className="icon-button" title="搜索当前对话" onClick={() => {setHeaderPanel(headerPanel === 'search' ? '' : 'search'); setShowUsage(false);}}><Search size={17}/></button><button className="icon-button" title="历史提问" onClick={() => {setHeaderPanel(headerPanel === 'history' ? '' : 'history'); setShowUsage(false);}}><History size={17}/></button></>}
      {!workspaceOpen && <button className="toolbar-button" onClick={async () => {try {await ensureConversation(); setWorkspaceOpen(true); void refreshFiles();} catch (failure) {error((failure as Error).message);}}}><Folder size={16}/>工作区</button>}
      </>}

      <button className="icon-button" title={theme === 'dark' ? '切换浅色' : '切换深色'} onClick={async () => { const next = theme === 'dark' ? 'light' : 'dark'; await call('settings.theme', { theme: next }); setSettings(previous => previous ? { ...previous, theme: next } : null); }}>{theme === 'dark' ? <Sun size={17}/> : <Moon size={17}/>}</button></div></header>
      {route === 'chat' && settings && <div className="header-popovers">{showUsage && <Usage visible={showUsage} events={events} settings={settings} onClose={() => setShowUsage(false)} onSave={async target => setSettings(await call('settings.context', {target_tokens: target}))}/>} {headerPanel && <section className="history-popover"><div className="navigator-heading"><strong>{headerPanel === 'search' ? '搜索当前对话' : '历史提问'}</strong><button className="icon-button" title="关闭" onClick={() => setHeaderPanel('')}><X size={15}/></button></div>{headerPanel === 'search' && <input autoFocus aria-label="搜索当前对话" placeholder="输入关键词" value={messageQuery} onChange={event => setMessageQuery(event.target.value)}/>}<div>{messages.filter(message => headerPanel === 'history' ? message.role === 'user' : messageQuery.trim() && messageText(message.content).toLowerCase().includes(messageQuery.toLowerCase())).map(message => <button key={message.id} onClick={() => {document.getElementById('message-' + message.id)?.scrollIntoView({block: 'center'}); setHeaderPanel('');}}>{messageText(message.content).slice(0,160)}</button>)}</div>{current?.has_more && <button className="secondary" disabled={olderLoading} onClick={() => void loadOlder()}>载入更早的对话</button>}</section>}</div>}
      {serviceError && <div className="service-error"><strong>本地服务连接中断</strong><span>{serviceError}</span><button onClick={() => void call('app.reconnect').catch(failure => setServiceError(failure.message))}>重新连接</button><button onClick={() => void call('app.logs')}>查看诊断日志</button></div>}
      {loading ? <div className="loading"><LoaderCircle size={23} className="spin"/><p>正在打开你的工作台…</p></div> : route === 'chat' ? <div className={`work-layout ${workspaceOpen ? 'with-workspace' : ''} ${workspaceExpanded ? 'expanded' : ''}`}>
        <section className={`conversation ${!messages.length ? 'home-conversation' : ''}`} style={workspaceExpanded && workspaceView.selected !== 'conversation' ? {display:'none'} : undefined}><div className="message-scroll" ref={scroll} onScroll={() => { if (scroll.current) nearBottom.current = scroll.current.scrollHeight - scroll.current.scrollTop - scroll.current.clientHeight < 120; }}>
          {!messages.length ? <div className="welcome"><div className="welcome-eyebrow">从一个想法，开始今天的工作</div><h2>今天，想一起完成什么？</h2><p>整理资料、分析数据，或把一个想法做出来。</p></div> : <div className="messages">
{current?.has_more && <button className="load-history" disabled={olderLoading} onClick={() => void loadOlder()}>{olderLoading ? <LoaderCircle size={14} className="spin"/> : <ChevronDown size={14}/>}更早的消息</button>}
{messages.map(message => historyEdit?.conversationId === current?.id && historyEdit?.prepared.message.id === message.id ? <div className="message-edit-container" key={message.id}><HistoryEditor conversationId={historyEdit.conversationId} prepared={historyEdit.prepared} onClose={() => setHistoryEdit(null)} onError={error} submit={async value => {
      const result = await drafts.submit(historyEdit.conversationId, {...value,
        model_id: current?.meta.selected_model_id || settings?.selected_model_id, skills: selectedSkills, consume_draft: false,
        rewrite: {hash: historyEdit.prepared.hash, revision: historyEdit.prepared.revision, message_id: historyEdit.prepared.message.id}});
      return {...result, conversation: await call<Conversation>('conversation.get', {conversation_id: historyEdit.conversationId})};
    }} onSaved={result => {
      setHistoryUndo({ conversationId: historyEdit.conversationId, token: result.undo });
      if (selectedId.current === historyEdit.conversationId) { setCurrent(result.conversation); setEvents(unwrapEvents(result.conversation.events || [])); }
      setHistoryEdit(null); void refreshList();
    }}/></div> : <MessageBubble key={message.id} cid={current!.id} message={message} active={active} onAction={onMessageAction} onFile={onMessageFile}/>) }
            {active && <div className="run-state"><LoaderCircle size={14} className="spin"/>{current?.run?.status === 'stopping' ? '正在停止，保留已完成内容…' : interaction ? '等待你的回答' : '正在处理…'}</div>}
            {!active && current?.run?.error && <div className="run-error"><span>{current.run.status === 'interrupted' ? '任务已停止' : '这次任务未完成'}</span><p>{runErrorSummary(current.run.error)}</p><details><summary>查看具体原因</summary><pre>{current.run.error}</pre></details><button className="secondary" onClick={() => {setDraft('请继续上一轮未完成的任务。先核对已经生成的文件与完成的操作，保留有效结果，避免重复已完成的操作。');composer.current?.focus();}}>继续这个任务</button></div>}
            {current && questions.map(({request, outcome, runId}) => <Interaction key={request.request_id} request={request} outcome={outcome} conversationId={current.id} active={active && runId === current.run?.id} done={() => setInteraction(null)}/>)}</div>}
        </div><div className="composer-container">
<DraftRecovery conflict={draftConflict} pending={Boolean(drafts.get(selectedId.current).pending)} resolve={keep => void resolveDraft(keep)} recover={retry => void recoverSend(retry)}/>
          <Composer conversation={current} conversations={conversations} settings={settings} text={draft} setText={setDraft} attachments={attachments} setAttachments={setAttachments} skills={selectedSkills} setSkills={setSelectedSkills} active={active} sending={sending} textarea={composer} ensureConversation={ensureConversation} preferences={preferences} send={send} stop={stop} attach={attach} route={goRoute} error={error}
            selectModel={async id => {await preferences({selected_model_id: id}); setSettings(await call('settings.select_model', {model_id: id}));}}
            permission={async () => {const enabled = !settings?.god_mode; if (enabled && await confirm('启用完全访问？', '当前用户权限范围内的工具操作将不再逐次请求批准。可以随时切回请求批准。', ['取消', '启用完全访问']) !== 1) return; setSettings(await call('settings.permission', {enabled}));}}/>
        </div>

          {!messages.length && <div className="home-followup"><div className="home-shortcuts">{[['PPT 助手', '帮我制作一份演示文稿。'], ['金融分析', '帮我分析这些金融资料。'], ['数据分析', '帮我分析这份数据，找出关键趋势。'], ['浏览器自动化', '帮我在浏览器中完成这个任务。']].map(([label, text]) => <button className="secondary" key={label} onClick={() => {setDraft(text); composer.current?.focus();}}><Sparkles size={16}/>{label}</button>)}</div><div className="recent-heading"><span>继续最近的任务</span><button onClick={() => setSidebar(true)}>查看全部</button></div>{conversations.filter(item => !item.meta.archived && item.id !== current?.id && item.has_messages).slice(0,3).map(item => <button key={item.id} className="recent-task" onClick={() => void switchConversation(item.id)}><FileText size={17}/><span>{item.title}<small>{item.meta.project_name || '独立聊天'} · {new Date(item.updated_at * 1000).toLocaleDateString()}</small></span><ChevronRight size={15}/></button>)}<details className="home-options"><summary>工作台选项</summary><button onClick={() => void goRoute('plugins')}>界面插件</button><button onClick={() => void goRoute('settings')}>外观与设置</button></details></div>}
        </section>
        {workspaceOpen && current && <Workspace key={current.id} conversation={current} value={workspaceView} change={value => {if (selectedId.current === current.id) changeWorkspace(value);}} expanded={workspaceExpanded} expand={() => setWorkspaceExpanded(!workspaceExpanded)} close={() => {setWorkspaceOpen(false); setWorkspaceExpanded(false);}} obscured={Boolean(ask || historyEdit || showSkills || pluginPreview || themePreview)} dark={theme === 'dark'} editors={editors} fileTree={fileTree} selectFile={activateFile} openFile={openFile} closeFile={closeEditor} refreshFiles={refreshFiles} createDocument={createDocument} error={error} confirm={confirm} fileContent={<>
          {editor && <div className="file-toolbar"><span title={editor.file.path}>{editor.file.name}</span><button className="icon-button" title="在文件夹中显示" onClick={() => void call('file.reveal', {conversation_id:current.id,path:editor.file.path}).catch(failure => error(failure.message))}><Folder size={15}/></button><button className="icon-button" title="复制文件路径" onClick={() => void call('clipboard.write',{text:editor.file.path}).catch(failure => error(failure.message))}><Copy size={15}/></button><button className="icon-button" title="用本机应用打开" onClick={() => void call<string>('file.external', {conversation_id: current.id, path: editor.file.path}).then(message => {if (message) error(message);}).catch(failure => error(failure.message))}><ExternalLink size={15}/></button><button className="icon-button" title="重新载入文件" onClick={() => void reloadFile()}><RefreshCw size={15}/></button>{!editor.file.payload.read_only && <button className="secondary" onClick={() => void saveEditor(editor.file.path).catch(failure => error(failure.message))}>保存</button>}</div>}
          {editor && fileChanged && <div className="file-update-notice" role="status">文件已更新<button className="text-link" onClick={() => void reloadFile()}>刷新</button></div>}
          {editor && (editor.file.conflict || editor.file.unavailable) && <div className="run-error">原文件已变化或暂时无法打开。当前编辑仍保留，可以复制内容或<button className="secondary" onClick={() => void saveCopy()}>另存副本</button>。</div>}
          {editor ? (['preview', 'html'].includes(editor.file.payload.kind) ? <FilePreview key={editor.file.id} file={editor.file} text={editor.text} change={text => changeEditor(editor.file.path, text)}/> : editor.file.payload.kind !== 'text' ? <OfficeEditor key={editor.file.id} file={editor.file} text={editor.text} onChange={text => changeEditor(editor.file.path, text)} error={error} register={registerEditorFlush}/> : <PluginEditor file={editor.file} plugin={editor.plugin} text={editor.text} theme={theme} onChange={text => changeEditor(editor.file.path, text)} onSave={() => saveEditor(editor.file.path)} error={error}/>) : <div className="empty-workspace">选择一个文件开始查看。</div>}
        </>}/>}

      </div> : managementNames[route] ? <Suspense fallback={<div className="loading"><LoaderCircle className="spin"/>正在载入…</div>}><Management key={route} route={route} settings={settings} updateSettings={setSettings} openPlugins={() => void goRoute('plugins')} openConversation={id => void switchConversation(id)} onError={error} confirm={confirm} registerLeave={registerLeave}
        onReference={async ref => {
          let target = stateRef.current.current;
          if (!target) { target = await call<Conversation>('conversation.create', {}); await refreshList(); selectedId.current = target!.id; await loadConversation(target!.id); }
          const refs = [...(target!.meta.knowledge_refs || []).filter((item: any) => JSON.stringify(item) !== JSON.stringify(ref)), ref];
          await call('conversation.preferences', { conversation_id: target!.id, value: { knowledge_refs: refs } });
          setCurrent(previous => previous?.id === target!.id ? { ...previous, meta: { ...previous.meta, knowledge_refs: refs } } : previous);
        }}/></Suspense> : <div className="management"><button className="back-link" onClick={() => void goRoute('capabilities')}>返回能力</button><div className="page-intro"><h2>让工作台随想法生长</h2><p>修改一个界面，增加一种工作方式。已有内容继续保留。</p></div><div className="section-heading"><h3>已安装的插件</h3><button className="primary" onClick={async () => { try { const value = await call<Plugin | null>('plugins.preview'); if (value) setPluginPreview(value); } catch (failure) { error((failure as Error).message); } }}><Plus size={15}/>添加本地插件</button></div><div className="plugin-list">{plugins.map(plugin => <section className="plugin-card" key={plugin.id}><div className="plugin-icon"><FileText size={24}/></div><div><h3>{plugin.name}<small>v{plugin.version}</small></h3><p>{plugin.description}</p><div className="button-row"><button className="secondary" onClick={async () => { try { const result = await call('plugins.export', { id: plugin.id }); if (result) setNotice('可编辑源码已保存到：' + result.path + '。可以让 AI 修改后再添加为新版本。'); } catch (failure) { error((failure as Error).message); } }}><Code2 size={14}/>导出可编辑源码</button>{!plugin.builtin && <button className="secondary" onClick={async () => { try { setPlugins(await call('plugins.rollback', { id: plugin.id })); const loaded = await call<Plugin>('plugins.load', { id: 'cowork.writing' }); setEditors(previous => previous.map(e => ({ ...e, plugin: loaded }))); } catch (failure) { error((failure as Error).message); } }}><RefreshCw size={14}/>{plugin.previous ? '恢复上一版' : '恢复内置版本'}</button>}</div></div></section>)}</div><div className="plugin-explainer"><h3>从自己的工作流开始</h3><p>导出写作工作台的源码到项目目录，让 AI 按你的要求修改。选择修改后的文件夹，预览并应用，就能继续使用新版本。</p><p>插件界面与对话运行独立，切换编辑器版本不会重启任务。</p></div></div>}
      {notice && <div className="notice" role="status"><span>{notice}</span><button className="icon-button" title="关闭提示" onClick={() => setNotice('')}><X size={15}/></button></div>}
    </main>

    {historyUndo && <div className="undo-toast" role="status"><span>历史已更新</span><button onClick={async () => {
      try { const value = await call<Conversation>('history.undo', { conversation_id: historyUndo.conversationId, undo: historyUndo.token });
        if (selectedId.current === value.id) { setCurrent(value); setEvents(unwrapEvents(value.events || [])); }
        setHistoryUndo(null); await refreshList();
      } catch (failure) { error((failure as Error).message); }
    }}>撤销</button><button aria-label="关闭撤销提示" onClick={() => setHistoryUndo(null)}><X size={14}/></button></div>}
    {themePreview && <ThemePreview candidate={themePreview} close={() => setThemePreview(null)} changed={async () => {const value = await call("themes.preview"); setThemeCandidate(value.preview);}}/>}
    {ask && <Dialog title={ask.title} onClose={() => { const resolve = ask.resolve; setAsk(null); resolve(0); }}><p>{ask.detail}</p><div className="button-row">{ask.choices.map((choice, index) => <button key={choice} autoFocus={index === 0} className={index === ask.choices.length - 1 ? 'primary' : 'secondary'} onClick={() => { const resolve = ask.resolve; setAsk(null); resolve(index); }}>{choice}</button>)}</div></Dialog>}
    {showSkills && <div className="modal-backdrop" onClick={() => setShowSkills(false)}><div className="modal skill-modal" onClick={e => e.stopPropagation()}><div className="section-heading"><h2>为当前任务选择能力</h2><button className="icon-button" title="关闭" onClick={() => setShowSkills(false)}><X size={18}/></button></div><div className="skill-list">{skills.map(skill => <label key={skill.name}><input type="checkbox" checked={selectedSkills.includes(skill.name)} onChange={e => setSelectedSkills(previous => e.target.checked ? [...previous, skill.name] : previous.filter(name => name !== skill.name))}/><span><strong>{skill.display_name || skill.name}</strong><small>{skill.description}</small></span></label>)}</div><button className="primary" onClick={() => setShowSkills(false)}>完成</button></div></div>}
    {pluginPreview && <div className="modal-backdrop"><div className="modal preview-modal"><div className="section-heading"><div><h2>{pluginPreview.name}</h2><p>v{pluginPreview.version} · 预览使用示例内容，不修改你的文档</p></div><button className="icon-button" title="取消预览" onClick={() => setPluginPreview(null)}><X size={18}/></button></div><div className="preview-frame"><PluginEditor file={{id: 'preview', name: '预览文稿.md', path: '', fingerprint: '', payload: {}}} plugin={pluginPreview} text={previewText} theme={theme} onChange={setPreviewText} onSave={async () => {}} error={error}/></div><div className="button-row"><button className="secondary" onClick={() => setPluginPreview(null)}>取消</button><button className="primary" onClick={() => void applyPlugin()}>应用这个版本</button></div></div></div>}
  </div>;
}
