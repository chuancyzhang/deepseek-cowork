import {useCallback,useEffect,useLayoutEffect,useRef,useState,useMemo} from 'react';
import type {Attachment,Conversation,FileSession,HostEvent,Message,Plugin,ProjectAction,ProjectRecord,Run,RuntimeEvent,Settings,SidebarMethod,SidebarSnapshot,WorkspaceChangeList,WorkspaceChangeRead} from '../types';
import type {LibrarySelection} from '../management/libraryCatalog';
import type {UsageView} from '../Usage';
import {MessageProjection,isActive,mergeEvents,unwrapEvents,messageText,interactionRecords,type ProjectionIssue} from '../runtime';
import {Drafts,type Draft} from '../drafts';
import {reviewId} from '../reviews';
import {emptyWorkspace,type WorkspaceView} from '../Workspace';
import {themeStyle} from '../theme';
const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
type Ask = { title: string; detail: string; choices: string[]; resolve: (index: number) => void };
type Editor = { file: FileSession; text: string; saved: string; plugin: Plugin; showChanges?: boolean };
type ProjectForm = {kind: 'create'; openChat: boolean} | {kind: 'edit'; project: ProjectRecord};

const workspaceDirectory = (conversation: Conversation): string => conversation.meta.workspace_dir || conversation.meta.workspace_path || '';
const usageOpenKey = 'usage:open';

import {useHost,useHostField,useSessionField,useConversation} from './context';
import {conversationAt,receiveConversation,receiveEvents,receiveRun,presentOpenedBrowser} from './conversations';
import {useDraft} from './draftState';
import {SaveCoordinator} from './saveCoordinator';
export function useWorkbenchController() {
  const host = useHost();
  const [selectedConversationId] = useHostField('navigation:selected', '');
  const saves = host.get('saveCoordinator', () => new SaveCoordinator());

  const [settings, setSettings] = useHostField<Settings | null>('settings', null);
  const [conversations, setConversations] = useHostField<Conversation[]>('conversations', []);
  const [sidebarSnapshot, setSidebarSnapshot] = useHostField<SidebarSnapshot | null>('sidebar:layout', null);
  const [sidebarConversations, setSidebarConversations] = useHostField<Conversation[]>('sidebar:conversations', []);
  const [hasMoreConversations, setHasMoreConversations] = useHostField('hasMoreConversations', false);
  const [runs] = useHostField<Record<string,Run>>('runs', {});
  const runningConversations = useMemo(() => new Set(Object.values(runs).filter(run => isActive(run.status)).map(run => run.conversation_id)), [runs]);
  const [current, setCurrent] = useConversation();
  const [events, setEvents] = useSessionField<RuntimeEvent[]>('events', []);
  const [route, setRoute] = useHostField('navigation:route', 'chat');
  const [creationOpen, setCreationOpen] = useSessionField('creationOpen', false);
  const [creationTab, setCreationTab] = useSessionField('creationTab', 'design');
  const [creationScope, setCreationScope] = useSessionField('creationScope', '整个界面');
  const [loading, setLoading] = useHostField('loading', true);
  const [serviceError, setServiceError] = useHostField('serviceError', '');
  const [notice, setNotice] = useState('');
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 6000); return () => clearTimeout(timer); }, [notice]);
  useEffect(() => { setNotice(''); }, [current?.id, route]);
  const [drafts] = useState(() => new Drafts(call, localStorage));
  const draftEntry = useDraft(drafts, selectedConversationId);
  const draft = draftEntry.value.text, attachments = draftEntry.value.attachments;
  const [sending, setSending] = useSessionField('sending', false);
  const [query, setQuery] = useState('');
  const [sidebar, setSidebar] = useState(true);
  const [projectDialog, setProjectDialog] = useState<ProjectForm | null>(null);
  const [workspaceOpen, setWorkspaceOpen] = useSessionField('workspaceOpen', false);
  const [workspaceExpanded, setWorkspaceExpanded] = useSessionField('workspaceExpanded', false);
  const [workspaceView, setWorkspaceView] = useSessionField<WorkspaceView>('workspaceView', emptyWorkspace);
  const changeWorkspace = useCallback((value: Partial<WorkspaceView>) => setWorkspaceView(previous => ({...previous, ...value})), []);
  const [fileTree, setFileTree] = useSessionField<any>('fileTree', { files: [] });
  const [changeRead] = useSessionField<WorkspaceChangeRead | null>('workspaceChanges', null);
  const directory = current ? workspaceDirectory(current) : '';
  const workspaceChanges = changeRead?.directory === directory ? changeRead : null;
  const refreshChanges = useCallback(async (id = host.get('navigation:selected', '')) => {
    if (!id || id !== host.get('navigation:selected', '')) return;
    const conversation = conversationAt(host, id);
    if (!conversation) return;
    const key = host.sessionKey('workspaceChanges', id), directory = workspaceDirectory(conversation);
    const previous = host.get<WorkspaceChangeRead | null>(key, null);
    const pending: WorkspaceChangeRead = {directory, data: previous?.directory === directory ? previous.data : undefined, pending: true};
    host.set(key, pending);
    const finish = (result: WorkspaceChangeRead) => {
      const latest = conversationAt(host, id);
      if (latest && workspaceDirectory(latest) === directory && host.get(key, null) === pending) host.set(key, result);
    };
    try { finish({directory, data: await call<WorkspaceChangeList>('workspace.changes', {conversation_id: id})}); }
    catch (failure) { finish({directory, data: pending.data, error: (failure as Error).message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '')}); }
  }, [host]);
  const [editors, setEditors] = useSessionField<Editor[]>('editors', []);
  const [activeFile, setActiveFile] = useSessionField('activeFile', '');
  const [fileChanged, setFileChanged] = useState(false);
  const [interaction, setInteraction] = useSessionField<any>('interaction', null);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [historyEdit, setHistoryEdit] = useState<{ conversationId: string; prepared: any } | null>(null);
  const [historyUndo, setHistoryUndo] = useState<{ conversationId: string; token: string } | null>(null);
  const [olderLoading, setOlderLoading] = useState(false);
  const [pluginPreview, setPluginPreview] = useSessionField<Plugin | null>('pluginPreview',null);
  const [themeCandidate, setThemeCandidate] = useState<any>(null), [themePreview, setThemePreview] = useState<any>(null);
  const [previewText, setPreviewText] = useState('# 让想法成为成果\n\n这里是独立的预览内容。\n\n## 今天的工作\n\n- 整理资料\n- 写下结论\n\n**继续完善。**');
  const [usageOpen, updateUsageOpen] = useState(() => {
    try { return localStorage.getItem(usageOpenKey) === 'true'; }
    catch { return false; }
  });
  const [usageView, setUsageView] = useSessionField<UsageView>('usageView', 'overview');
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
  const editorSaves = useRef(new Map<string, Promise<any>>());
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
    if (route === 'chat' && !active && !loading && !serviceError && current?.id) void refreshChanges(current.id);
  }, [route, active, loading, serviceError, current?.id, directory, refreshChanges]);
  useEffect(() => {
    setFileChanged(false);
    if (!activeFile || !current?.id || !workspaceOpen) return;
    let alive = true;
    const check = async () => {
      const item = editorsRef.current.find(item => item.file.path === activeFile);
      if (!item || item.file.unavailable || item.file.payload.untitled) return;
      try { const result = await call('file.describe', {conversation_id: current.id, path: activeFile}); if (alive) setFileChanged(result.fingerprint !== item.file.fingerprint); }
      catch { if (alive) setFileChanged(true); }
    };
    const timer = setInterval(() => void check(), 5000);
    return () => {alive = false; clearInterval(timer);};
  }, [activeFile, current?.id, workspaceOpen]);
  const error = useCallback((message: string) => setNotice(message), []);
  const setUsageOpen = useCallback((open: boolean) => {
    updateUsageOpen(open);
    try { localStorage.setItem(usageOpenKey, String(open)); }
    catch { error('用量浮窗开关暂未保存，当前窗口仍保留选择。'); }
  }, [error]);
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

  const listRefresh = useRef(0);
  const receiveSidebar = useCallback((snapshot?: SidebarSnapshot) => {
    if (snapshot) setSidebarSnapshot(previous => !previous || snapshot.revision > previous.revision ? snapshot : previous);
  }, [setSidebarSnapshot]);
  const refreshList = useCallback(async () => {
    const generation = ++listRefresh.current;
    const result = await call<{ conversations: Conversation[]; sidebar: SidebarSnapshot; sidebar_conversations: Conversation[] }>('conversation.list', { sidebar: true });
    if (generation !== listRefresh.current) return;
    const page = draftSummaries(result.conversations);
    setConversations(previous => [...page, ...previous.slice(500).filter(item => !page.some(next => next.id === item.id))]);
    setSidebarConversations(draftSummaries(result.sidebar_conversations));
    receiveSidebar(result.sidebar);
    setHasMoreConversations(page.length === 500);
  }, [receiveSidebar]);

  async function changeSidebar(method: SidebarMethod, params: unknown) {
    const snapshot = await call<SidebarSnapshot>(method, params);
    receiveSidebar(snapshot);
    return snapshot.value;
  }
  async function moreConversations() {
    try {
      const page = draftSummaries(await call<Conversation[]>('conversation.list', { offset: conversations.length }));
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
          media_tasks: older.media_tasks,
          render_messages: prepend(older.render_messages, previous.render_messages), message_positions: { ...older.message_positions, ...previous.message_positions } };
      });
      setEvents(previous => mergeEvents(unwrapEvents(older.events || []), previous));
      requestAnimationFrame(() => { if (element) element.scrollTop = oldTop + element.scrollHeight - oldHeight; });
    } catch (failure) { error((failure as Error).message); } finally { setOlderLoading(false); }
  }
  function draftSummaries(items: Conversation[]) {
    return items.map(item => {
      const entry = drafts.recovery(item.id);
      if (!entry || item.has_messages || item.meta.manual_title) return item;
      if (!entry.value.text.trim() && !entry.value.attachments.length) return {...item, display_title:item.title};
      return {...item, has_content:true, display_title: '草稿 · ' +
        (entry.value.text.trim().slice(0,32) || entry.value.attachments[0]?.name || item.title)};
    });
  }
  function openReview(review: string, cid = selectedId.current) {
    if (!cid) return;
    host.set<WorkspaceView>(host.sessionKey('workspaceView', cid), previous => {
      const view = previous || emptyWorkspace();
      return {...view, selected:'review:' + review, reviewOpen:[...new Set([...(view.reviewOpen || []),review])], reviewClosed:(view.reviewClosed || []).filter(id=>id!==review)};
    });
    host.set(host.sessionKey('workspaceOpen', cid), true);
  }
  const messageActionRef = useRef<(action: string, message: Message) => Promise<void>>(async () => {});
  const agentNavigationSequence = useRef(0);
  const fileActionRef = useRef<(path: string) => Promise<void>>(async () => {});
  const onMessageAction = useCallback((action: string, message: Message) => { void messageActionRef.current(action, message); }, []);
  const onMessageFile = useCallback((path: string) => { void fileActionRef.current(path); }, []);
  messageActionRef.current = async (action, message) => {
    const id = selectedId.current;
    const navigationSequence = ++agentNavigationSequence.current;
    if (action === 'tool' && reviewId(message.result_obj || message.content)) {openReview(reviewId(message.result_obj || message.content)!); return;}
    if (action === 'tool') { setWorkspaceOpen(true); changeWorkspace({selected: 'tasks', tasksPinned: true, tasksOpen: false, task: {...stateRef.current.workspaceView.task, section: 'calls', callId: message.tool_call_id, runId: message.meta?.turn_id || message.meta?.ui_turn_id, agentId: undefined}}); return; }
    if (action === 'agent') {
      try {
        const target = message.meta?.ui_agent_target;
        let agentId: string | undefined;
        if (target) {
          const agents = await call<any[]>('agent.list', {conversation_id:id});
          const matches = agents.filter(agent=>agent.id === target || agent.name === target);
          const agent = matches.find(agent=>agent.id === target) || (matches.length === 1 ? matches[0] : undefined);
          if (!agent) throw Error('无法定位这个子任务，请从子任务列表查看。');
          agentId = agent.id;
        }
        if (selectedId.current !== id || agentNavigationSequence.current !== navigationSequence) return;
        setWorkspaceOpen(true); changeWorkspace({selected:'agents',agentsOpen:true,agents:{...stateRef.current.workspaceView.agents,section:'agents',agentId,callId:undefined,runId:undefined}});
      } catch (failure) {if (selectedId.current === id && agentNavigationSequence.current === navigationSequence) error((failure as Error).message);}
      return;
    }
    try {
      if (action === 'office' || action === 'skill') {
        await saveView(id);
        const review = await call('reviews.create', {conversation_id:id, kind:action, message_id:message.id});
        if (selectedId.current === id) openReview(review.id, id);
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
          consume_draft: false,
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
  function addProject(openChat = true) {
    setProjectDialog(previous => previous ?? {kind: 'create', openChat});
  }
  async function saveProject(name: string, path: string) {
    if (!projectDialog) return;
    if (projectDialog.kind === 'edit') {
      const project = projectDialog.project;
      const projects = await call<ProjectRecord[]>('project.edit', {original_path: project.path, original_name: project.name, name, path});
      setSettings(previous => previous && {...previous, projects});
      setProjectDialog(null);
      return;
    }
    const project = await call<ProjectRecord>('project.save', {name, path: path || undefined});
    setProjectDialog(null);
    try {
      setSettings(await call('settings.get'));
      if (projectDialog.openChat) await newConversation(project.path);
    } catch (failure) {
      error('项目已创建，打开时遇到问题：' + (failure as Error).message);
    }
  }
  async function conversationAction(action: string, item: Conversation) {
    try {
      if (action === 'rename') { await switchConversation(item.id); setTitle(item.title); setRename(true); return; }
      if (action === 'delete' && await confirm('移到回收站？', '聊天将从列表移除，可以在归档中恢复。工作目录中的文件会保留。', ['取消', '移到回收站']) !== 1) return;
      if (action === 'pin') {
        const pinned = !item.meta.pinned;
        await call('conversation.pin', { conversation_id: item.id, pinned });
        const update = (items: Conversation[]) => items.map(entry => entry.id === item.id ? { ...entry, meta: { ...entry.meta, pinned } } : entry);
        setConversations(update); setSidebarConversations(update);
        await refreshList();
        return;
      }
      await saveDraft(); await saveView();
      await call('conversation.' + action, { conversation_id: item.id });
      if (selectedId.current === item.id && ['archive', 'delete'].includes(action)) {
        selectedId.current = ''; setCurrent(null); setEvents([]); setInteraction(null); setEditors([]); setActiveFile(''); setWorkspaceOpen(false);
        const state = await call('app.bootstrap');
        const entry = drafts.restore('', state.home_draft); applyDraft(entry.value, false);
      }
      await refreshList();
    } catch (failure) { error((failure as Error).message); }
  }
  async function projectAction(action: ProjectAction, project: ProjectRecord) {
    if (action === 'edit') {
      setProjectDialog(previous => previous ?? {kind: 'edit', project});
      return;
    }
    try {
      await call('project.' + action, { path: project.path });
      if (action === 'open_folder') return;
      setSettings(await call('settings.get'));
      setNotice(action === 'archive' ? '项目已归档，聊天和文件均保留。可在侧栏归档中恢复。' : '项目已恢复。');
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
    const value = { creationOpen:field('creationOpen',false), creationTab:field('creationTab','design'), creationScope:field('creationScope','整个界面'), workspaceOpen: field('workspaceOpen',false), workspace: field('workspaceView',emptyWorkspace()),
      activeFile: field('activeFile',''), scrollTop: id===selectedId.current ? scroll.current?.scrollTop ?? field('scrollTop',0) : field('scrollTop',0),
      editors: field<Editor[]>('editors',[]).map(editor => ({ path: editor.file.path, name:editor.file.name, untitled:Boolean(editor.file.payload.untitled), text: snapshots.get(editor.file.path) ?? editor.text, saved: editor.saved, fingerprint: editor.file.fingerprint })) };
    if (value.workspace.reviewEdits) value.workspace = {...value.workspace, reviewEdits:Object.fromEntries(Object.entries(value.workspace.reviewEdits).map(([key,edit]) => [key, {...edit,...(edit.filePath && snapshots.has(edit.filePath) ? {officeText:snapshots.get(edit.filePath)} : {})}]))};
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
      drafts.restore(id, {value: value.draft || {text:'', attachments:[]}});
      await drafts.recoverSubmission(id);
      if (selectedId.current !== id || generation !== loadGeneration.current) return;
      applyDraft(drafts.get(id).value, false);
      viewRevisions.current.set(id, value.view_revision ?? -1);
      setCreationOpen(value.view?.creationOpen ?? Boolean(value.meta.creation_session));
      setCreationTab(value.view?.creationTab || 'design'); setCreationScope(value.view?.creationScope || '整个界面');
      setWorkspaceOpen(Boolean(value.view?.workspaceOpen)); setWorkspaceView({...emptyWorkspace(), ...value.view?.workspace}); setEditors([]); setActiveFile('');
      const plugin = await call<Plugin>('plugins.load', { id: 'cowork.writing' });
      const restored: Editor[] = [];
      for (const item of value.view?.editors || []) {
        if (item.untitled) {restored.push({file:{id:item.path,path:item.path,name:item.name || '未命名.md',fingerprint:'',payload:{kind:'text',editor_target:'markdown',language:'markdown',untitled:true}},text:item.text,saved:'',plugin});continue;}
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
      selectedId.current = id; setLoading(true); setRoute('chat'); setInteraction(null); setEvents([]); setWorkspaceExpanded(false);
      await loadConversation(id);
    } catch (failure) { error((failure as Error).message); } finally { setLoading(false); }
  }
  async function newConversation(workspace?: string, homeSource?: string) {
    const fromSameHome = () => homeSource === undefined || (
      selectedId.current === homeSource && host.get('navigation:route', 'chat') === 'chat' &&
      !host.get(host.sessionKey('sending', homeSource), false) && !isActive(conversationAt(host, homeSource)?.run?.status)
    );
    if (!await leaveManagement.current() || !fromSameHome()) return;
    await saveDraft(homeSource); await saveView(homeSource);
    if (!fromSameHome()) return;
    const sourceId = selectedId.current;
    const blank = stateRef.current.current;
    if (blank && !blank.meta.creation_session && homeSource === undefined && !workspace && !blank.has_messages && !blank.messages?.length && !drafts.get(blank.id).pending && !blank.meta.manual_title && !blank.meta.has_file_changes && !blank.meta.conversation_agent && !blank.meta.selected_skill_names?.length && !blank.meta.knowledge_refs?.length && !blank.meta.conversation_refs?.length && blank.meta.mode !== 'grilling' && !editorsRef.current.length && !isActive(blank.run?.status)) {
      setRoute('chat'); composer.current?.focus(); return blank;
    }
    const value = await call<Conversation>('conversation.create',
      homeSource !== undefined && workspace ? {project_path: workspace} : {workspace_dir: workspace});
    if (!fromSameHome()) return;
    await drafts.carry(sourceId, value.id, async input => {
      const copied = input.attachments.length ? await call<Attachment[]>('workspace.attach', {
        conversation_id:value.id, paths:input.attachments.map(file => file.path),
      }) : [];
      if (!fromSameHome()) throw new Error('聊天已切换，原草稿仍保留。');
      return {...input, attachments:copied.map((file,index) => ({...file, name:input.attachments[index].name}))};
    });
    if (!fromSameHome()) return;
    selectedId.current = value.id; setCurrent(value); setEvents([]);
    viewRevisions.current.set(value.id, -1);
    setEditors([]); setActiveFile(''); setWorkspaceView(emptyWorkspace());
    setWorkspaceOpen(false); setWorkspaceExpanded(false); setInteraction(null); setRoute('chat'); nearBottom.current = true;
    await refreshList(); composer.current?.focus();
    return value;
  }

  useEffect(() => {
    let mounted = true;
    call('app.bootstrap').then(async value => {
      if (!mounted) return;
      setSettings(value.settings); setConversations(draftSummaries(value.conversations));
      setSidebarConversations(draftSummaries(value.sidebar_conversations || []));
      receiveSidebar(value.sidebar);
      setHasMoreConversations(value.conversations.length === 500);
      for(const run of value.active_runs)receiveRun(host,run,run.conversation_id);
      const id = window.history.state?.conversationId;
      if (id && value.conversations.some((c: Conversation) => c.id === id && !c.meta.archived)) { selectedId.current = id; await loadConversation(id); }
      else { const entry = drafts.restore('', value.home_draft); applyDraft(entry.value, false); }
      setLoading(false);
    }).catch(failure => { if (mounted) { setServiceError(failure.message); setLoading(false); } });
    return () => { mounted = false; };
  }, []);
  useEffect(() => {
    if (!loading && !serviceError) window.history.replaceState({conversationId: selectedConversationId}, '');
  }, [loading, serviceError, selectedConversationId]);
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
        await call('app.titlebar', {background: resolved.tokens.bg_main, foreground: resolved.tokens.text_primary});
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
    if (loading || !current?.id) return;
    const update = (previous: Conversation[]) => previous.map(item => item.id === current.id ? draftSummaries([item])[0] : item);
    setConversations(update); setSidebarConversations(update);
  }, [draft, attachments, current?.id, loading]);
  useEffect(() => {
    if (loading || !current) return;
    const timer = setTimeout(() => { void saveView().catch(failure => error('工作区草稿尚未保存：' + failure.message)); }, 500);
    return () => clearTimeout(timer);
  }, [editors, activeFile, workspaceOpen, workspaceView, creationOpen, creationTab, creationScope, saveView, error, loading]);
  useLayoutEffect(() => { if (nearBottom.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [events, current?.messages, interaction, workspaceChanges?.data]);

  useEffect(() => window.cowork.onEvents((batch: HostEvent[]) => {
    receiveEvents(host,batch);
    for (const event of batch) {
      presentOpenedBrowser(host,event,emptyWorkspace);
      if (event.event === 'review_changed' && event.conversation_id && event.data?.open) {
        const cid = event.conversation_id, review = event.data.id;
        host.set<WorkspaceView>(host.sessionKey('workspaceView', cid), previous => {const view = previous || emptyWorkspace(); return {...view,reviewOpen:[...new Set([...(view.reviewOpen || []),review])],reviewClosed:(view.reviewClosed || []).filter(id=>id!==review)};});
        if (cid === selectedId.current && !host.get(host.sessionKey('workspaceOpen',cid),false)) setNotice('草稿已就绪，可打开工作区审阅。');
      }
      if (event.event === 'query_sync') host.set('query:pending', Boolean((event as any).pending));
      if (event.event === 'settings_changed') void call<Settings>('settings.get').then(setSettings).catch(failure=>error(failure.message));
      if (event.event === 'service_error') setServiceError(event.error || '本地服务已断开。');
      if (event.event === 'service_reconnecting') setServiceError('连接暂时中断，正在重新连接后台…');
      if (event.event === 'service_reconnected') { setServiceError(''); void refreshList().catch(failure => error(failure.message)); if (selectedId.current) void loadConversation(selectedId.current, true).catch(failure => error(failure.message)); }
      if (event.event === 'window_hidden') void (async () => { try { await saves.preserve('hide'); } catch (failure) { error((failure as Error).message); } })();
      if (event.event === 'close_requested') void closeApp();
      if (event.event === 'update_install_requested') void closeApp(true);
      if (event.event === 'reload_requested') void (async () => { try { await saves.preserve('reload'); await call('app.reload'); } catch (failure) { error('暂时不能重载，输入和编辑仍保留：' + (failure as Error).message); } })();
      if (event.event === 'sidebar_changed') receiveSidebar(event.data);
      if (event.event === 'conversation_changed' || event.event === 'conversation_list_changed') {
        void refreshList().catch(failure => error(failure.message));
        if (event.event === 'conversation_changed' && event.conversation_id === selectedId.current) void loadConversation(event.conversation_id!, true).catch(failure => error(failure.message));
      }
      if (event.event === 'run_error' && event.conversation_id === selectedId.current) { error(event.error!); void loadConversation(event.conversation_id!, true); }
    }
  }), [refreshList, receiveSidebar, error]);

  async function closeApp(installUpdate = false) {
    if (closePending.current) return;
    closePending.current = true;
    let transaction:{id:string}|undefined;
    try {
      await saves.preserve(installUpdate ? 'update' : 'quit');
      const unsaved = editorsRef.current.some(e => e.text !== e.saved);
      const state = await call('app.bootstrap');
      if (installUpdate) {
        if (state.active_runs.length) {error('请等后台任务结束后再更新；草稿和编辑已保留。'); return;}
        if (!await leaveManagement.current()) return;
        const update = await call('updates.get');
        const label = update.reinstall ? '重启并重新安装' : '重启并更新';
        if (await confirm(label + ' DeepSeek Cowork？', `目标版本 ${update.version}。草稿和未保存编辑已保留。应用将关闭，安装完成后自动重新打开。`, ['继续工作', label]) !== 1) return;
        const preparation=await call('app.install_update');
        if(preparation.cancelled)return;
        transaction=preparation;
        try { await saves.preserve('update'); }
        catch(failure) {await call('updates.abort_install',transaction);throw failure;}
        await call('app.commit_update',transaction); return;
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
    let targetSendingKey = sendingKey;
    const submittedText = input.text; const submittedAttachments = input.attachments;
    try {
      let conversation = conversationAt(host, requestedId);
      if (requestedId && !conversation) conversation = await call<Conversation>('conversation.get', {conversation_id: requestedId});
      if (!conversation) conversation = conversationAt(host, await ensureConversation());
      const id = conversation!.id;
      targetSendingKey = host.sessionKey('sending', id);host.set(targetSendingKey, true);
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
          ...(conversation?.meta.creation_session ? {creation_scope:host.get(host.sessionKey('creationScope',id),'整个界面'),display_text:submittedText} : {}),
          model_id: conversation?.meta?.selected_model_id || settings?.selected_model_id,
          skills: conversation?.meta.selected_skill_names ?? conversation?.view?.selectedSkills ?? [] });
      }
      if (selectedId.current === id) applyDraft(drafts.get(id).value, false);
      await saveDraft(id);
      nearBottom.current = true; await loadConversation(id, true); await refreshList();
    } catch (failure) { error((failure as Error).message); throw failure; } finally { host.set(sendingKey, false);host.set(targetSendingKey, false); if(selectedId.current===requestedId)composer.current?.focus(); }
  }
  async function stop() {
    if (!current?.run) return;
    try { await stopRun(current.id, current.run.id); }
    catch (failure) { error((failure as Error).message); }
  }
  async function stopRun(id: string, runId: string) {
    const latest = host.get<Record<string,Run>>('runs', {})[id];
    if (!latest || latest.id !== runId || !isActive(latest.status)) return;
    await call('conversation.stop', {conversation_id:id,run_id:runId});
    // A stop from the overview must never reload or select a different chat.
    if (selectedId.current === id) await loadConversation(id, true);
    else {
      const value = await call<Conversation>('conversation.get', {conversation_id:id});
      receiveRun(host,value.run || null,id);
    }
  }
  async function recoverSend(retry = false) {
    const id = selectedId.current;
    const sendingKey=host.sessionKey('sending',id);
    if(host.get(sendingKey,false))return;
    host.set(sendingKey,true);
    try {
      const pending = drafts.get(id).pending;
      if (!pending) return;
      const receipt = retry ? await drafts.submit(id, JSON.parse(pending.signature)) : await drafts.recoverSubmission(id);
      if (receipt?.accepted) {
        if (selectedId.current === id) { applyDraft(drafts.get(id).value, false); }
        await loadConversation(id, true); await saveDraft(id);
      } else error('尚未找到接收记录，可重试原消息。当前新输入会保留。');
    } catch (failure) { error((failure as Error).message); }
    finally {host.set(sendingKey,false);}
  }
  const creatingConversation = useRef<Promise<string> | null>(null);
  async function ensureConversation(): Promise<string> {
    if (selectedId.current) return selectedId.current;
    if (creatingConversation.current) return creatingConversation.current;
    creatingConversation.current = (async () => {
      await saveDraft('');
      const value = await call<Conversation>('conversation.create');
      await drafts.carry('', value.id); viewRevisions.current.set(value.id, -1);
      receiveConversation(host, value.id, value);
      if (!selectedId.current) selectedId.current = value.id;
      await saveDraft(value.id);
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
  async function browseLibrary() {
    await saveView();
    const id = await ensureConversation();
    if (selectedId.current !== id) return;
    changeWorkspace({selected: 'library', libraryOpen: true}); setWorkspaceOpen(true);
  }
  async function addLibraryReference(source: string, items: LibrarySelection[], targetId?: string) {
    const id = targetId || await ensureConversation();
    const refs = await call<any[]>('library.references', {source, items});
    if (source === 'local') {
      const added = await call<Attachment[]>('workspace.attach', {conversation_id: id, paths: refs.map(ref => ref.path)});
      const value = drafts.get(id).value;
      drafts.change(id, {...value, attachments: [...value.attachments, ...added.filter(file => !value.attachments.some(old => old.path === file.path))]});
      if (selectedId.current === id) applyDraft(drafts.get(id).value, false);
      await drafts.save(id);
      return;
    }
    const result = await call('conversation.preferences', {conversation_id: id, append_knowledge_refs: refs});
    receiveConversation(host, id, previous => previous ? {...previous, meta: {...previous.meta, ...result.meta}} : previous);
    await refreshList();
  }
  async function activateFile(path: string) {
    try { await saveView(); setActiveFile(path); changeWorkspace({selected: 'file:' + path}); } catch (failure) { error((failure as Error).message); }
  }
  async function openFile(path: string, edit = false, showChanges = false) {
    const id = selectedId.current;
    try { await saveView(); } catch (failure) { error((failure as Error).message); return; }
    if (selectedId.current !== id) return;
    const select = (file: FileSession) => {
      if (edit && file.payload.kind === 'html') host.set('file-source:' + file.id, true);
      if (showChanges || edit) setEditors(previous => previous.map(item => item.file.path === file.path ? {...item, showChanges} : item));
      setActiveFile(file.path);
      changeWorkspace({selected: 'file:' + file.path, ...(!workspaceView.filesPinned ? {filesOpen: false} : {})});
      setWorkspaceOpen(true);
    };
    const alreadyOpen = editorsRef.current.find(item => item.file.path === path);
    if (alreadyOpen) { select(alreadyOpen.file); return; }
    try {
      const file = await call<FileSession>('file.open', { conversation_id: id, path });
      if (selectedId.current !== id) return;
      if (!editorsRef.current.some(item => item.file.path === file.path)) {
        const plugin = await call<Plugin>('plugins.load', { id: 'cowork.writing' });
        if (selectedId.current !== id) return;
        const text = file.payload.text ?? file.payload.content ?? '';
        if (edit && !file.payload.read_only) file.payload.start_edit = true;
        setEditors(previous => previous.some(item => item.file.path === file.path) ? previous : [...previous, {file, text, saved: text, plugin}]);
      }
      select(editorsRef.current.find(item => item.file.path === file.path)?.file || file);
    } catch (failure) { if (selectedId.current === id) error((failure as Error).message); }
  }
  async function saveEditor(path: string, id = selectedId.current): Promise<any> {
    const key = id + ':' + path;
    if (editorSaves.current.has(key)) return editorSaves.current.get(key);
    const task = persistEditor(path, id);
    editorSaves.current.set(key, task);
    try {return await task;} finally {editorSaves.current.delete(key);}
  }
  async function persistEditor(path: string, id: string) {
    const editorKey = host.sessionKey('editors', id);
    const editor = host.get<Editor[]>(editorKey, []).find(e => e.file.path === path);
    if (!editor) return;
    if (editor.file.payload.read_only) {
      if (editor.text !== editor.saved) throw new Error('请先保存恢复副本，再关闭文件。原草稿仍保留。');
      return;
    }
    const text = selectedId.current === id && editorFlush.current.has(path) ? await editorFlush.current.get(path)!() : editor.text;
    if (!text && !['text', 'html'].includes(editor.file.payload.kind)) return;
    if (editor.file.payload.untitled) {
      if (!text.trim()) return {saved:false,empty:true};
      const name = editor.file.name.trim();
      if (!name || /[\\/:*?"<>|]/.test(name) || !/\.(md|txt)$/i.test(name)) throw new Error('请填写 Markdown 或文本文件名，例如「工作笔记.md」。');
      const file = await call<FileSession>('workspace.create', {conversation_id:id,name,text});file.payload.start_edit=true;
      host.set<Editor[]>(editorKey, previous => previous.map(e => e.file.path === path ? {...e,file,saved:text} : e));
      const activeKey=host.sessionKey('activeFile',id),workspaceKey=host.sessionKey('workspaceView',id);
      if(host.get(activeKey,'')===path)host.set(activeKey,file.path);
      host.set<WorkspaceView>(workspaceKey, previous=>previous.selected==='file:'+path?{...previous,selected:'file:'+file.path}:previous);
      await saveView(id);void refreshFiles(id);void refreshChanges(id);
      return file;
    }
    const result = await call('file.save', { conversation_id: id, id: editor.file.id, payload: ['text', 'html'].includes(editor.file.payload.kind) ? text : JSON.parse(text) });
    host.set<Editor[]>(editorKey, previous => previous.map(e => e.file.path === path ? { ...e, saved: text, file: { ...e.file, fingerprint: result.fingerprint } } : e));
    void refreshFiles(id);void refreshChanges(id);return {...result,path};
  }
  async function reloadFile() {
    const id = selectedId.current, item = editorsRef.current.find(item => item.file.path === activeFile);
    if (!item) return;
    if (item.text !== item.saved && await confirm('重新载入文件？', '将用磁盘上的内容替换此文件的编辑草稿。可以取消并先另存副本。', ['取消', '重新载入']) !== 1) return;
    try {
      const file = await call<FileSession>('file.open', {conversation_id: id, path: item.file.path});
      if (selectedId.current !== id) return;
      file.payload.manual_preview_refresh=crypto.randomUUID();
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
      setActiveFile(file.path); void refreshChanges(); await refreshFiles();
    } catch (failure) { error((failure as Error).message); }
  }
  function changeEditor(path: string, text: string) {
    setEditors(previous => previous.map(e => e.file.path === path ? { ...e, text } : e));
  }
  async function closeEditor(path: string) {
    const id=selectedId.current;
    try { await saveView(); } catch (failure) { error((failure as Error).message); return; }
    const editor = editors.find(e => e.file.path === path);
    if (editor && editor.saved !== editor.text && (!editor.file.payload.untitled || editor.text.trim())) {
      const choice = await confirm('保存修改？', editor.file.name + ' 中有尚未保存的内容。', ['取消', '不保存并关闭', '保存并关闭']);
      if (choice === 0) return;
      if (choice === 2) { try { const result=await saveEditor(path,id);path=result?.path || path; } catch (failure) { error((failure as Error).message); return; } }
    }
    const editorKey=host.sessionKey('editors',id),activeKey=host.sessionKey('activeFile',id);
    const remaining=host.get<Editor[]>(editorKey,[]).filter(e=>e.file.path!==path);host.set(editorKey,remaining);
    if(host.get(activeKey,'')===path){const next=remaining[0]?.file.path||'';host.set(activeKey,next);host.set<WorkspaceView>(host.sessionKey('workspaceView',id),previous=>({...previous,...(previous?.selected==='file:'+path?{selected:next?'file:'+next:'new',newOpen:false}:{})}));}
    await saveView(id);
  }
  async function createDocument() {
    try {
      const id = await ensureConversation();
      if (!id) return;
      const plugin = await call<Plugin>('plugins.load', {id:'cowork.writing'});
      if(selectedId.current!==id)return;
      const path='untitled:'+crypto.randomUUID();
      const file:FileSession={id:path,path,name:'写作-'+new Date().toLocaleString('sv').replace(/[: ]/g,'-')+'.md',fingerprint:'',payload:{kind:'text',editor_target:'markdown',language:'markdown',untitled:true}};
      setEditors(previous=>[...previous,{file,text:'',saved:'',plugin}]);setActiveFile(path);setWorkspaceOpen(true);changeWorkspace({selected:'file:'+path,...(!workspaceView.filesPinned?{filesOpen:false}:{})});
      await saveView(id);
    } catch (failure) { error((failure as Error).message); }
  }
  async function usePluginAction(actionId: string) {
    const origin = selectedId.current;
    const latestSettings = await call<Settings>('settings.get');
    if (selectedId.current !== origin) return;
    setSettings(latestSettings);
    const action = latestSettings.plugin_actions?.find(item => item.id === actionId);
    if (!action) throw Error('此功能已停用，请重新启用后再试。');
    if (!action.configured) {
      host.set('management:tab:' + action.page, action.tab);
      host.set(action.plugin + ':editing', action.id);
      await goRoute(action.page);
      return;
    }
    if (!await leaveManagement.current()) return;
    const id = await ensureConversation();
    if (!id || selectedId.current !== id) return;
    const existing = drafts.get(id).value;
    drafts.change(id, {...existing, text: existing.text.trim() ? existing.text + '\n\n' + action.prompt : action.prompt});
    await saveDraft(id); if (selectedId.current !== id) return; setRoute('chat');
    requestAnimationFrame(() => {if(selectedId.current===id)composer.current?.focus();});
  }

  async function goRoute(next: string) {
    if (next !== route && !await leaveManagement.current()) return;
    try {
      if (next === 'plugins') { host.set('management:tab:capabilities', 'plugins'); next = 'capabilities'; }
      if (next === 'multimedia') { host.set('management:tab:capabilities', 'skills'); host.set('management:skill','multimedia'); next = 'capabilities'; }
      await saveDraft(); await saveView(); setRoute(next);
      if (next === 'settings') setSettings(await call('settings.get'));
    } catch (failure) { error((failure as Error).message); }
  }
  async function enterCreation(tab = 'design') {
    if (host.get('creation:opening',false) || host.get('loading',true)) return;
    host.set('creation:opening',true);
    try {
      if (!await leaveManagement.current()) return;
      const source = await ensureConversation();
      await saveDraft(source); await saveView(source);
      const result = await call('creation.prepare',{conversation_id:source});
      if (selectedId.current !== source) return;
      await switchConversation(result.conversation_id);
      if (selectedId.current !== result.conversation_id || conversationAt(host,result.conversation_id)?.id !== result.conversation_id) return;
      setCreationOpen(true); setCreationTab(tab); setRoute('chat');
      await saveView(result.conversation_id); await refreshList();
    } finally {host.set('creation:opening',false);}
  }
  async function exitCreation() {
    const id = selectedId.current, source = conversationAt(host,id)?.meta.creation_source_id;
    await saveDraft(id); await saveView(id);
    if (selectedId.current !== id) return;
    if (source) await switchConversation(source);
    else {setCreationOpen(false); await saveView(id);}
  }
  async function applyPlugin() {
    if (!pluginPreview?.token) return;
    const cid=selectedId.current;
    try { const registry = host.get<import('../plugins/registry').PluginRegistry>('pluginRegistry'); await registry.apply(pluginPreview,saves); host.set(host.sessionKey('pluginPreview',cid),null); }
    catch (failure) { error((failure as Error).message); }
  }

  const projection = host.get(host.sessionKey('messageProjection'),() => new MessageProjection());
  const {messages, projectionIssues} = useMemo(() => {
    const projectionIssues:ProjectionIssue[]=[];
    const messages=projection.project(current?.render_messages || current?.messages || [], events, active ? current?.run?.id : undefined, issue=>projectionIssues.push(issue));
    return {messages,projectionIssues};
  }, [projection,current?.render_messages, current?.messages, events, active, current?.run?.id]);
  const projectionDiagnostic=JSON.stringify(projectionIssues);
  useEffect(() => {
    if(projectionDiagnostic!=='[]')console.warn('message_projection_unplaced',JSON.stringify({conversation_id:current?.id,issues:JSON.parse(projectionDiagnostic)}));
  }, [current?.id,projectionDiagnostic]);
  const projectionError=projectionIssues.length ? '部分执行过程暂时无法恢复到原位置，原始记录仍保留。请重试加载。' : '';
  const questions = useMemo(() => interactionRecords(events, messages), [events, messages]);
  const editor = editors.find(e => e.file.path === activeFile);
  const filtered = conversations.filter(c => !c.meta.archived && (!query || c.title.toLocaleLowerCase().includes(query.toLocaleLowerCase())));
  useEffect(() => saves.register('conversation-work', async () => {
    for(const id of drafts.entries.keys()) await saveDraft(id);
    for(const key of host.values.keys()) if(key.startsWith('session:')&&key.endsWith(':current')&&host.get(key)) await saveView(key.slice(8,-8));
  }), [saves]);
  return {projectionError, projectDialog, setProjectDialog, saveProject, sidebarLayout:sidebarSnapshot?.value || null, sidebarConversations, changeSidebar, usePluginAction,creationOpen,setCreationOpen,creationTab,setCreationTab,creationScope,setCreationScope,enterCreation,exitCreation,settings, setSettings, conversations, setConversations, hasMoreConversations, setHasMoreConversations, runningConversations, current, setCurrent, events, setEvents, route, setRoute, loading, setLoading, serviceError, setServiceError, notice, setNotice, draft, attachments, drafts, sending, setSending, query, setQuery, sidebar, setSidebar, workspaceOpen, setWorkspaceOpen: (value: boolean | ((previous:boolean)=>boolean)) => {const open=typeof value==='function' ? value(host.get(host.sessionKey('workspaceOpen'),false)) : value; if (!open) changeWorkspace({browserPresentationSuppressed:true}); setWorkspaceOpen(open);}, workspaceExpanded, setWorkspaceExpanded, workspaceView, setWorkspaceView, changeWorkspace, fileTree, setFileTree, workspaceChanges, refreshChanges, editors, setEditors, activeFile, setActiveFile, fileChanged, setFileChanged, interaction, setInteraction, ask, setAsk, historyEdit, setHistoryEdit, historyUndo, setHistoryUndo, olderLoading, setOlderLoading, pluginPreview, setPluginPreview, themeCandidate, setThemeCandidate, themePreview, setThemePreview, previewText, setPreviewText, usageOpen, setUsageOpen, usageView, setUsageView, skills, setSkills, selectedSkills, showSkills, setShowSkills, modelForm, setModelForm, formBusy, setFormBusy, rename, setRename, title, setTitle, scroll, nearBottom, composer, selectedId, loadGeneration, draftRef, editorsRef, editorFlush, registerEditorFlush, stateRef, pendingSteering, closePending, viewRevisions, viewQueue, theme, leaveManagement, registerLeave, active, error, confirm, applyDraft, setDraft, setAttachments, refreshList, moreConversations, loadOlder, messageActionRef, fileActionRef, onMessageAction, onMessageFile, addProject, conversationAction, projectAction, saveDraft, saveView, refreshFiles, loadConversation, switchConversation, newConversation, closeApp, send, stop, stopRun, recoverSend, creatingConversation, ensureConversation, preferences, attach, browseLibrary, addLibraryReference, openReview, activateFile, openFile, saveEditor, reloadFile, saveCopy, changeEditor, closeEditor, createDocument, goRoute, applyPlugin, messages, questions, editor, filtered, saves, host};
}
export type WorkbenchController = ReturnType<typeof useWorkbenchController>;
