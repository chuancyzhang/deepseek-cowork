import {useCallback,useEffect,useLayoutEffect,useRef,useState,useMemo} from 'react';
import type {Attachment,Conversation,FileSession,HostEvent,Message,Plugin,Run,RuntimeEvent,Settings} from '../types';
import {MessageProjection,isActive,mergeEvents,unwrapEvents,messageText,interactionRecords} from '../runtime';
import {Drafts,type Draft} from '../drafts';
import {emptyWorkspace,type WorkspaceView} from '../Workspace';
import {themeStyle} from '../theme';
const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
type Ask = { title: string; detail: string; choices: string[]; resolve: (index: number) => void };
type Editor = { file: FileSession; text: string; saved: string; plugin: Plugin };

import {useHost,useHostField,useSessionField,useConversation} from './context';
import {conversationAt,receiveConversation,receiveEvents,receiveRun} from './conversations';
import {useDraft} from './draftState';
import {SaveCoordinator} from './saveCoordinator';
export function useWorkbenchController() {
  const host = useHost();
  const [selectedConversationId] = useHostField('navigation:selected', '');
  const saves = host.get('saveCoordinator', () => new SaveCoordinator());

  const [settings, setSettings] = useHostField<Settings | null>('settings', null);
  const [conversations, setConversations] = useHostField<Conversation[]>('conversations', []);
  const [hasMoreConversations, setHasMoreConversations] = useHostField('hasMoreConversations', false);
  const [runs] = useHostField<Record<string,Run>>('runs', {});
  const runningConversations = useMemo(() => new Set(Object.values(runs).filter(run => isActive(run.status)).map(run => run.conversation_id)), [runs]);
  const [current, setCurrent] = useConversation();
  const [events, setEvents] = useSessionField<RuntimeEvent[]>('events', []);
  const [route, setRoute] = useHostField('navigation:route', 'chat');
  const [loading, setLoading] = useHostField('loading', true);
  const [serviceError, setServiceError] = useHostField('serviceError', '');
  const [notice, setNotice] = useState('');
  const [drafts] = useState(() => new Drafts(call, localStorage));
  const draftEntry = useDraft(drafts, selectedConversationId);
  const draft = draftEntry.value.text, attachments = draftEntry.value.attachments, draftConflict = draftEntry.conflict;
  const setDraftConflict = (value:boolean) => { const id=selectedId.current; drafts.get(id).conflict=value; drafts.persist(id); };
  const [sending, setSending] = useSessionField('sending', false);
  const [query, setQuery] = useState('');
  const [sidebar, setSidebar] = useState(true);
  const [workspaceOpen, setWorkspaceOpen] = useSessionField('workspaceOpen', false);
  const [workspaceExpanded, setWorkspaceExpanded] = useSessionField('workspaceExpanded', false);
  const [workspaceView, setWorkspaceView] = useSessionField<WorkspaceView>('workspaceView', emptyWorkspace);
  const changeWorkspace = useCallback((value: Partial<WorkspaceView>) => setWorkspaceView(previous => ({...previous, ...value})), []);
  const [fileTree, setFileTree] = useSessionField<any>('fileTree', { files: [] });
  const [editors, setEditors] = useSessionField<Editor[]>('editors', []);
  const [activeFile, setActiveFile] = useSessionField('activeFile', '');
  const [fileChanged, setFileChanged] = useState(false);
  const [interaction, setInteraction] = useSessionField<any>('interaction', null);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [historyEdit, setHistoryEdit] = useState<{ conversationId: string; prepared: any } | null>(null);
  const [historyUndo, setHistoryUndo] = useState<{ conversationId: string; token: string } | null>(null);
  const [olderLoading, setOlderLoading] = useState(false);
  const [pluginPreview, setPluginPreview] = useState<Plugin | null>(null);
  const [themeCandidate, setThemeCandidate] = useState<any>(null), [themePreview, setThemePreview] = useState<any>(null);
  const [previewText, setPreviewText] = useState('# 让想法成为成果\n\n这里是独立的预览内容。\n\n## 今天的工作\n\n- 整理资料\n- 写下结论\n\n**继续完善。**');
  const [showUsage, setShowUsage] = useState(false);
  const [headerPanel, setHeaderPanel] = useState('');
  const [messageQuery, setMessageQuery] = useState('');
  const [skills, setSkills] = useState<any[]>([]);
  const selectedSkills: string[] = current?.meta.selected_skill_names ?? current?.view?.selectedSkills ?? [];
  const [showSkills, setShowSkills] = useState(false);
  const [modelForm, setModelForm] = useState({ name: 'DeepSeek', base_url: 'https://api.deepseek.com', model_name: 'deepseek-chat', api_key: '', provider_type: 'openai', api_protocol: 'chat_completions' });
  const [formBusy, setFormBusy] = useState(false);
  const [rename, setRename] = useState(false);
  const [title, setTitle] = useState('');
  const scroll = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const composer = useRef<HTMLTextAreaElement>(null);
  const selectedId = host.ref<string>('navigation:selected', '');
  const loadGeneration = useRef(0);
  const draftRef = {get current() {return drafts.get(selectedId.current).value;}};
  const editorsRef = host.ref<Editor[]>(() => host.sessionKey('editors'), []);
  const editorFlush = useRef(new Map<string, () => Promise<string>>());
  const registerEditorFlush = useCallback((path: string, flush?: () => Promise<string>) => { if (flush) editorFlush.current.set(path, flush); else editorFlush.current.delete(path); }, []);
  const stateRef = {get current() {return {
    current:conversationAt(host,selectedId.current), settings:host.get<Settings|null>('settings',null),
    workspaceOpen:host.get<boolean>(host.sessionKey('workspaceOpen'),false), activeFile:host.get<string>(host.sessionKey('activeFile'),''),
    selectedSkills:conversationAt(host,selectedId.current)?.meta.selected_skill_names ?? conversationAt(host,selectedId.current)?.view?.selectedSkills ?? [], workspaceView:host.get<WorkspaceView>(host.sessionKey('workspaceView'),emptyWorkspace),
  };}};
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
    if (action === 'tool') { setWorkspaceOpen(true); changeWorkspace({selected: 'tasks', tasksPinned: true, tasksOpen: false, task: {...stateRef.current.workspaceView.task, section: 'calls', callId: message.tool_call_id, runId: message.meta?.turn_id || message.meta?.ui_turn_id, agentId: undefined}}); return; }
    try {
      if (action === 'office' || action === 'skill') {
        const source = messageText(message.content);
        const instruction = action === 'office' ? '请将下面这份回答整理为可编辑的办公文档，选择适合内容的格式，并保存到当前工作目录。保留事实、来源和已完成成果。' : '请将下面这轮任务中的可复用方法整理为 Skill 草稿，明确适用范围、操作步骤与验证方式，先展示草稿供我审阅。';
        const existing = drafts.get(id).value.text;
        setDraft([existing, instruction + '\n\n' + source].filter(Boolean).join('\n\n'));
        composer.current?.focus();
        return;
      }
      if (!['branch', 'edit', 'retry', 'delete'].includes(action)) return;
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
  const saveView = useCallback(async (id = selectedId.current) => {
    if (!id) return;
    const snapshots = new Map<string, string>();
    if(id===selectedId.current)for (const [path, flush] of editorFlush.current) snapshots.set(path, await flush());
    const field=<T,>(name:string,initial:T)=>host.get<T>(host.sessionKey(name,id),initial);
    const value = { workspaceOpen: field('workspaceOpen',false), workspace: field('workspaceView',emptyWorkspace()),
      activeFile: field('activeFile',''), scrollTop: id===selectedId.current ? scroll.current?.scrollTop ?? field('scrollTop',0) : field('scrollTop',0),
      editors: field<Editor[]>('editors',[]).map(editor => ({ path: editor.file.path, text: snapshots.get(editor.file.path) ?? editor.text, saved: editor.saved, fingerprint: editor.file.fingerprint })) };
    host.set(host.sessionKey('scrollTop',id),value.scrollTop);
    viewQueue.current = viewQueue.current.catch(() => undefined).then(async () => {
      const receipt = await call('view.save', { conversation_id: id, value, revision: viewRevisions.current.get(id) ?? -1 });
      viewRevisions.current.set(id, receipt.revision);
    });
    await viewQueue.current;
  }, []);
  async function refreshFiles(id = selectedId.current, path?: string) {
    if (!id) return;
    try { const result = await call('workspace.list', { conversation_id: id, path }); if (selectedId.current === id) setFileTree(result); }
    catch (failure) { if (selectedId.current === id) setFileTree((previous: any) => ({...previous, error: (failure as Error).message})); }
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
      setWorkspaceOpen(Boolean(value.view?.workspaceOpen)); setWorkspaceView({...emptyWorkspace(), ...value.view?.workspace}); setEditors([]); setActiveFile('');
      const plugin = await call<Plugin>('plugins.load', { id: 'cowork.writing' });
      const restored: Editor[] = [];
      for (const item of value.view?.editors || []) {
        try { const file = await call<FileSession>('file.open', { conversation_id: id, path: item.path, expected_fingerprint: item.fingerprint, recover_draft: item.text !== item.saved }); restored.push({ file, text: item.text, saved: item.saved, plugin }); }
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
    const blank = stateRef.current.current;
    if (blank && !workspace && !blank.has_messages && !blank.messages?.length && !drafts.get(blank.id).value.text.trim() && !drafts.get(blank.id).value.attachments.length && !blank.meta.manual_title && !blank.meta.has_file_changes && !blank.meta.conversation_agent && !blank.meta.selected_skill_names?.length && !blank.meta.knowledge_refs?.length && !blank.meta.conversation_refs?.length && blank.meta.mode !== 'grilling' && !editorsRef.current.length && !isActive(blank.run?.status)) {
      setRoute('chat'); composer.current?.focus(); return blank;
    }
    const value = await call<Conversation>('conversation.create', { workspace_dir: workspace });
    selectedId.current = value.id; setCurrent(value); setEvents([]);
    drafts.restore(value.id, { value: { text: '', attachments: [] }, revision: -1 });
    applyDraft({ text: '', attachments: [] }, false); setDraftConflict(false); viewRevisions.current.set(value.id, -1);
    setEditors([]); setActiveFile(''); setWorkspaceView(emptyWorkspace());
    setShowUsage(false);
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
      for(const run of value.active_runs)receiveRun(host,run,run.conversation_id);
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
    const timer = setTimeout(() => { void saveDraft(id).then(() => id ? refreshList() : undefined).catch(failure => error('草稿尚未保存：' + failure.message)); }, 400);
    return () => clearTimeout(timer);
  }, [draft, attachments, current?.id, saveDraft, error, loading]);
  useEffect(() => {
    if (loading || !current) return;
    const timer = setTimeout(() => { void saveView().catch(failure => error('工作区草稿尚未保存：' + failure.message)); }, 500);
    return () => clearTimeout(timer);
  }, [editors, activeFile, workspaceOpen, workspaceView, saveView, error, loading]);
  useLayoutEffect(() => { if (nearBottom.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [events, current?.messages, interaction]);

  useEffect(() => window.cowork.onEvents((batch: HostEvent[]) => {
    receiveEvents(host,batch);
    for (const event of batch) {
      if (event.event === 'query_sync') host.set('query:pending', Boolean((event as any).pending));
      if (event.event === 'service_error') setServiceError(event.error || '本地服务已断开。');
      if (event.event === 'service_reconnecting') setServiceError('连接暂时中断，正在重新连接后台…');
      if (event.event === 'service_reconnected') { setServiceError(''); void refreshList(); if (selectedId.current) void loadConversation(selectedId.current, true).catch(failure => error(failure.message)); }
      if (event.event === 'window_hidden') void (async () => { try { await saves.preserve('hide'); } catch (failure) { error((failure as Error).message); } })();
      if (event.event === 'close_requested') void closeApp();
      if (event.event === 'update_install_requested') void closeApp(true);
      if (event.event === 'reload_requested') void (async () => { try { await saves.preserve('reload'); await call('app.reload'); } catch (failure) { error('暂时不能重载，输入和编辑仍保留：' + (failure as Error).message); } })();
      if (event.event === 'conversation_changed') { void refreshList(); if (event.conversation_id === selectedId.current) void loadConversation(event.conversation_id!, true).catch(failure => error(failure.message)); }
      if (event.event === 'run_error' && event.conversation_id === selectedId.current) { error(event.error!); void loadConversation(event.conversation_id!, true); }
    }
  }), [refreshList, error]);

  async function closeApp(installUpdate = false) {
    if (closePending.current) return;
    closePending.current = true;
    try {
      await saves.preserve(installUpdate ? 'update' : 'quit');
      const unsaved = editorsRef.current.some(e => e.text !== e.saved);
      const state = await call('app.bootstrap');
      if (installUpdate) {
        if (state.active_runs.length) {error('请等后台任务结束后再更新；草稿和编辑已保留。'); return;}
        if (!await leaveManagement.current()) return;
        if (await confirm('重启并更新 DeepSeek Cowork？', '草稿和未保存编辑已保留。应用将关闭，安装完成后自动重新打开。', ['继续工作', '重启并更新']) !== 1) return;
        await call('app.install_update'); return;
      }
      if (unsaved || state.active_runs.length) {
        const choice = await confirm('退出 DeepSeek Cowork？', `${state.active_runs.length ? '进行中的任务将停止，已完成的内容会保留。' : ''}${unsaved ? '未保存的编辑已作为草稿保留，下次打开可以继续。' : ''}`, ['继续工作', '退出应用']);
        if (choice !== 1) return;
      }
      await call('app.quit');
    } catch (failure) {
      error((failure as Error).message);
      if (installUpdate) return;
      if (await confirm('保存未完成', '本地服务无法确认保存。输入草稿已保留在界面缓存中；建议继续工作并复制未保存的编辑。', ['继续工作', '仍然退出']) === 1) await call('app.quit');
    } finally { closePending.current = false; }
  }
  async function send(requestedId = selectedId.current) {
    const input = drafts.get(requestedId).value;
    const sendingKey = host.sessionKey('sending', requestedId);
    if (host.get(sendingKey, false) || (!input.text.trim() && !input.attachments.length)) return;
    host.set(sendingKey, true); setNotice('');
    const submittedText = input.text; const submittedAttachments = input.attachments;
    try {
      let conversation = conversationAt(host, requestedId);
      if (requestedId && !conversation) conversation = await call<Conversation>('conversation.get', {conversation_id: requestedId});
      if (!conversation) conversation = conversationAt(host, await ensureConversation());
      const id = conversation!.id;
      if (isActive(conversation?.run?.status)) {
        const signature = JSON.stringify([id, conversation!.run!.id, submittedText, submittedAttachments]);
        if (pendingSteering.current?.signature !== signature) pendingSteering.current = { id: crypto.randomUUID(), signature };
        const result = await call('conversation.steer', { conversation_id: id, run_id: conversation!.run!.id, attachments: submittedAttachments, message: { id: pendingSteering.current.id, role: 'user', content: submittedText, meta: { kind: 'user_guidance', turn_id: conversation!.run!.id } } });
        const accepted = result.result || result;
        if (!accepted.accepted && !accepted.message_committed) throw new Error('补充消息未被接收，内容已保留。');
        if (!accepted.accepted) error('补充内容已保存，但本轮未确认接收。请在对话中核对后继续。');
        pendingSteering.current = null;
        const value = drafts.get(id).value;
        drafts.change(id, { text: value.text === submittedText ? '' : value.text,
          attachments: value.attachments.filter(file => !submittedAttachments.some(sent => sent.path === file.path)) });
      } else {
        await drafts.submit(id, { text: submittedText, attachments: submittedAttachments,
          model_id: conversation?.meta?.selected_model_id || settings?.selected_model_id,
          skills: conversation?.meta.selected_skill_names ?? conversation?.view?.selectedSkills ?? [] });
      }
      if (selectedId.current === id) applyDraft(drafts.get(id).value, false);
      await saveDraft(id);
      if (!requestedId) { drafts.change('', { text: '', attachments: [] }); await saveDraft(''); }
      nearBottom.current = true; await loadConversation(id, true); await refreshList();
    } catch (failure) { error((failure as Error).message); throw failure; } finally { host.set(sendingKey, false); if(selectedId.current===requestedId)composer.current?.focus(); }
  }
  async function stop() {
    if (!current?.run) return;
    const id = current.id;
    try { await call('conversation.stop', { conversation_id: id, run_id: current.run.id }); await loadConversation(id, true); }
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
      const homeDraft = drafts.get('').value;
      drafts.change(value.id, homeDraft); viewRevisions.current.set(value.id, -1);
      receiveConversation(host, value.id, value);
      if (!selectedId.current) selectedId.current = value.id;
      await saveDraft(value.id);
      if (drafts.get('').value === homeDraft) { drafts.change('', {text: '', attachments: []}); await saveDraft(''); }
      await refreshList();
      return value.id;
    })();
    try { return await creatingConversation.current; } finally { creatingConversation.current = null; }
  }
  async function preferences(value: Record<string, any>, targetId?: string) {
    const id = targetId ?? await ensureConversation();
    const result = await call('conversation.preferences', {conversation_id: id, value});
    receiveConversation(host, id, previous => previous ? {...previous, meta: {...previous.meta, ...result.meta}} : previous);
    await refreshList();
  }
  async function attach() {
    try { const id = await ensureConversation(); const added = await call<Attachment[]>('dialog.attach', {conversation_id: id}); if (selectedId.current === id) setAttachments(previous => [...previous, ...added.filter(item => !previous.some(old => old.path === item.path))]); }
    catch (failure) { error((failure as Error).message); }
  }
  async function activateFile(path: string) {
    try { await saveView(); setActiveFile(path); changeWorkspace({selected: 'file:' + path}); } catch (failure) { error((failure as Error).message); }
  }
  async function openFile(path: string) {
    const id = selectedId.current;
    try { await saveView(); } catch (failure) { error((failure as Error).message); return; }
    if (selectedId.current !== id) return;
    if (editors.some(e => e.file.path === path)) { setActiveFile(path); changeWorkspace({selected: 'file:' + path, ...(!workspaceView.filesPinned ? {filesOpen: false} : {})}); setWorkspaceOpen(true); return; }
    try {
      const file = await call<FileSession>('file.open', { conversation_id: id, path });
      if (selectedId.current !== id) return;
      if (editorsRef.current.some(item => item.file.path === file.path)) {
        setActiveFile(file.path); changeWorkspace({selected:'file:' + file.path, ...(!workspaceView.filesPinned ? {filesOpen:false} : {})}); setWorkspaceOpen(true); return;
      }

      const plugin = await call<Plugin>('plugins.load', { id: 'cowork.writing' });
      if (selectedId.current !== id) return;
      const text = file.payload.text ?? file.payload.content ?? '';
      setEditors(previous => [...previous, { file, text, saved: text, plugin }]); setActiveFile(file.path); changeWorkspace({selected: 'file:' + file.path, ...(!workspaceView.filesPinned ? {filesOpen: false} : {})}); setWorkspaceOpen(true);
    } catch (failure) { error((failure as Error).message); }
  }
  async function saveEditor(path: string) {
    const editor = editorsRef.current.find(e => e.file.path === path);
    if (!editor) return;
    if (editor.file.payload.read_only) {
      if (editor.text !== editor.saved) throw new Error('请先保存恢复副本，再关闭文件。原草稿仍保留。');
      return;
    }
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
    setEditors(previous => previous.map(e => e.file.path === path ? { ...e, text } : e));
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
      if (next === 'plugins') await host.get<import('../plugins/registry').PluginRegistry>('pluginRegistry').refresh();
      if (next === 'settings') setSettings(await call('settings.get'));
    } catch (failure) { error((failure as Error).message); }
  }
  async function applyPlugin() {
    if (!pluginPreview?.token) return;
    try { const registry = host.get<import('../plugins/registry').PluginRegistry>('pluginRegistry'); await registry.apply(pluginPreview,saves); setPluginPreview(null); }
    catch (failure) { error((failure as Error).message); }
  }

  const projection = host.get(host.sessionKey('messageProjection'),() => new MessageProjection());
  const messages = useMemo(() => projection.project(current?.render_messages || current?.messages || [], events, active ? current?.run?.id : undefined), [projection,current?.render_messages, current?.messages, events, active, current?.run?.id]);
  const questions = useMemo(() => interactionRecords(events, messages), [events, messages]);
  const editor = editors.find(e => e.file.path === activeFile);
  const filtered = conversations.filter(c => !c.meta.archived && (!query || c.title.toLocaleLowerCase().includes(query.toLocaleLowerCase())));
  useEffect(() => saves.register('conversation-work', async () => {
    for(const id of drafts.entries.keys()) await saveDraft(id);
    for(const key of host.values.keys()) if(key.startsWith('session:')&&key.endsWith(':current')&&host.get(key)) await saveView(key.slice(8,-8));
  }), [saves]);
  return {settings, setSettings, conversations, setConversations, hasMoreConversations, setHasMoreConversations, runningConversations, current, setCurrent, events, setEvents, route, setRoute, loading, setLoading, serviceError, setServiceError, notice, setNotice, draft, attachments, drafts, draftConflict, setDraftConflict, sending, setSending, query, setQuery, sidebar, setSidebar, workspaceOpen, setWorkspaceOpen, workspaceExpanded, setWorkspaceExpanded, workspaceView, setWorkspaceView, changeWorkspace, fileTree, setFileTree, editors, setEditors, activeFile, setActiveFile, fileChanged, setFileChanged, interaction, setInteraction, ask, setAsk, historyEdit, setHistoryEdit, historyUndo, setHistoryUndo, olderLoading, setOlderLoading, pluginPreview, setPluginPreview, themeCandidate, setThemeCandidate, themePreview, setThemePreview, previewText, setPreviewText, showUsage, setShowUsage, headerPanel, setHeaderPanel, messageQuery, setMessageQuery, skills, setSkills, selectedSkills, showSkills, setShowSkills, modelForm, setModelForm, formBusy, setFormBusy, rename, setRename, title, setTitle, scroll, nearBottom, composer, selectedId, loadGeneration, draftRef, editorsRef, editorFlush, registerEditorFlush, stateRef, pendingSteering, closePending, viewRevisions, viewQueue, theme, leaveManagement, registerLeave, active, error, confirm, applyDraft, setDraft, setAttachments, refreshList, moreConversations, loadOlder, messageActionRef, fileActionRef, onMessageAction, onMessageFile, addProject, conversationAction, saveDraft, saveView, refreshFiles, loadConversation, switchConversation, newConversation, closeApp, send, stop, resolveDraft, recoverSend, creatingConversation, ensureConversation, preferences, attach, activateFile, openFile, saveEditor, reloadFile, saveCopy, changeEditor, closeEditor, createDocument, goRoute, applyPlugin, messages, questions, editor, filtered, saves, host};
}
export type WorkbenchController = ReturnType<typeof useWorkbenchController>;
