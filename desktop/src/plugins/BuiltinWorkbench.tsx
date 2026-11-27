import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, useMemo, lazy, Suspense, useSyncExternalStore } from 'react';
import {ArrowLeft, ChartColumn, ChevronDown, Copy, ExternalLink, FileDiff, Folder, History, LoaderCircle, PanelRight, RefreshCw, Sparkles, X} from 'lucide-react';
import type { Conversation, TurnFile } from '../types';
import { contextRecoveryForRun, displayMessages, messageGroups, isActive, mergeEvents, messageText, runErrorSummary, isUserStopped, unwrapEvents, turnTimings } from '../runtime';
import PluginSlot from './PluginSlot';
import Usage, {UsageToolbarButton, type UsageView} from '../Usage';
import { Drafts, type Draft } from '../drafts';
import DraftRecovery from '../DraftRecovery';
import Sidebar from '../Sidebar';
import MessageBubble, {AssistantTurn, GenerationTaskStatus, RunStatus} from '../MessageBubble';
import {activeReview} from '../reviews';
import HistoryEditor from '../HistoryEditor';
import Dialog from '../Dialog';
import OfficeEditor from '../OfficeEditor';
import FilePreview, {SpreadsheetWorkspace} from '../FilePreview';
import MoreActions, {type MenuAction} from '../MoreActions';
import {FileToolbarControls,FileToolbarProvider} from '../FileToolbar';
import {FileHistoryPanel, ChangesPanel, WorkspaceDiff} from '../WorkspaceChanges';
import {pathKey} from '../projects';
import Workspace, { emptyWorkspace, type WorkspaceView } from '../Workspace';
import Composer from '../Composer';
import HomeComposerFooter from '../HomeComposerFooter';
import { themeStyle } from '../theme';
import ThemePreview from '../ThemePreview';
import {enterpriseExpertModel, type EnterpriseCapability} from '../enterpriseCapabilities';
import {conversationAt} from '../host/conversations';
import {readVideoTaskLayout, videoPathKey} from '../host/videoTasks';
const Library = lazy(() => import('../management/Library'));
const Management = lazy(() => import('../Management'));
const ChatMediaTasks = lazy(() => import('./multimedia/ChatTasks'));
const VideoTasks = lazy(() => import('./multimedia/ChatTasks').then(module => ({default: module.VideoTasks})));
const CodeEditor = lazy(() => import('../CodeEditor'));
const managementNames: Record<string, string> = { settings: '设置', capabilities: '能力', library: '资料库', favorites: '常用任务', team: '团队空间' };

const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
type Ask = { title: string; detail: string; choices: string[]; resolve: (index: number) => void };

import {useWorkbench} from '../host/Host';
import {useHostField, useSessionField} from '../host/context';
export default function BuiltinWorkbench() {
  const {
    projectDialog, sidebarLayout, sidebarConversations, changeSidebar, host, usePluginAction,
    creationOpen, creationTab, enterCreation, exitCreation, settings, setSettings,
    conversations, hasMoreConversations, runningConversations, current, setCurrent, events, setEvents,
    route, loading, serviceError, setServiceError, projectionError, loadConversation, draft, attachments, drafts, sending, sidebar,
    workspaceOpen, setWorkspaceOpen, workspaceExpanded, setWorkspaceExpanded, workspaceView, changeWorkspace,
    fileTree, workspaceChanges, refreshChanges, editors, setEditors, fileChanged, interaction, ask, historyEdit, setHistoryEdit,
    historyUndo, setHistoryUndo, olderLoading, pluginPreview, themeCandidate, setThemeCandidate, themePreview,
    setThemePreview, usageOpen, setUsageOpen, usageView, setUsageView, selectedSkills, showSkills, rename, setRename, title, setTitle,
    scroll, nearBottom, composer, selectedId, registerEditorFlush, theme, registerLeave, active, error, confirm,
    setDraft, setAttachments, refreshList, moreConversations, loadOlder, onMessageAction, onMessageFile, addProject,
    conversationAction, projectAction, saveView, refreshFiles, switchConversation, newConversation, send, stop,
    recoverSend, ensureConversation, preferences, attach, browseLibrary, addLibraryReference, openReview,
    activateFile, openFile, saveEditor, reloadFile, saveCopy, changeEditor, closeEditor, createDocument, goRoute,
    messages, questions, editor, registry,
  } = useWorkbench();
  const sidebarTools = useCallback((target: HTMLDivElement | null) => {host.set('navigation:sidebarTools', target);}, [host]);
  const [showHistory, setShowHistory] = useState(false);
  const [changeSelection, setChangeSelection] = useSessionField<{directory: string; path: string} | null>('workspaceChangeSelection', null);
  const changes = workspaceChanges?.data;
  const selectedChange = changeSelection?.directory === workspaceChanges?.directory ? changeSelection : null;
  const editorPath = editor?.file.path;
  const fileChange = useMemo(() => editorPath ? changes?.files.find(file => pathKey(file.absolute_path) === pathKey(editorPath)) : undefined, [changes, editorPath]);
  const showingDiff = Boolean(editor?.showChanges);
  function selectChange(path: string) {
    setChangeSelection(path ? {directory: workspaceChanges?.directory || '', path} : null);
  }
  async function openChanges(path?: string) {
    if (!current) return;
    const cid = current.id;
    try {
      await saveView(cid);
      if (selectedId.current !== cid) return;
      if (path) selectChange(path);
      changeWorkspace({selected: 'changes', changesOpen: true, ...(!workspaceView.filesPinned ? {filesOpen:false} : {})});
      setWorkspaceOpen(true);
      void refreshChanges(cid);
    } catch (failure) {error((failure as Error).message);}
  }
  async function openFileHistory(files: TurnFile[], selected = files[0]?.id || '') {
    if (!current) return;
    const cid = current.id;
    try {
      await saveView(cid);
      if (selectedId.current !== cid) return;
      changeWorkspace({selected: 'file-history', fileHistory: {files, selected}, ...(!workspaceView.filesPinned ? {filesOpen: false} : {})});
      setWorkspaceOpen(true);
    } catch (failure) {error((failure as Error).message);}
  }
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const historyTrigger = useRef<HTMLButtonElement>(null);
  const usageTrigger = useRef<HTMLButtonElement>(null);
  const usagePanelId = useId();
  const historyPanel = useRef<HTMLDivElement>(null);
  const hasMessages = messages.length > 0;
  const home = !hasMessages && !creationOpen;
  const chatVisible = !workspaceExpanded || workspaceView.selected === 'conversation';
  const usageAvailable = usageOpen && route === 'chat' && !loading && hasMessages;
  const showUsage = usageAvailable && chatVisible && !workspaceOpen;
  const closeHistory = useCallback((restoreFocus = false) => {
    setShowHistory(false);
    if (restoreFocus) historyTrigger.current?.focus({preventScroll: true});
  }, []);
  useEffect(() => {closeHistory();}, [route, current?.id, hasMessages, chatVisible, closeHistory]);
  useLayoutEffect(() => {
    const viewport = scroll.current;
    if (!viewport || !hasMessages || !chatVisible) return;
    const follow = () => {
      if (nearBottom.current && viewport.clientHeight) viewport.scrollTop = viewport.scrollHeight;
    };
    follow();
    if (!showUsage) return;
    // The side-by-side layout can reflow the message list without any new messages.
    const observer = new ResizeObserver(follow);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [route, loading, current?.id, hasMessages, chatVisible, showUsage, scroll, nearBottom]);
  useEffect(() => {
    if (!showHistory) return;
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!historyPanel.current?.contains(target) && !historyTrigger.current?.contains(target) && !usageTrigger.current?.contains(target)) closeHistory();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented && !document.querySelector('dialog[open]')) {
        event.preventDefault();
        closeHistory(true);
      }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [showHistory, closeHistory]);
  useSyncExternalStore(registry.subscribe,registry.snapshot);
  const usageToolbar = registry.resolve('toolbar','chat')[0]?.extension;
  const activeRunId = current?.run?.status === 'running' ? current.run.id : undefined;
  const timings = useMemo(() => turnTimings(events), [events]);
  const groups = useMemo(() => messageGroups(messages, questions), [messages, questions]);
  const cid = current?.id || '';
  const mediaKey = host.sessionKey('media_tasks', cid);
  const readVideos = useMemo(() => readVideoTaskLayout(host, cid, groups), [host, cid, groups]);
  const videoLayout = useSyncExternalStore(useCallback(listener => host.subscribe(mediaKey, listener), [host, mediaKey]), readVideos);
  const openChatFile = !videoLayout.files.size ? onMessageFile : (path: string) => {
    const owner = videoLayout.files.get(videoPathKey(path, current?.meta.workspace_dir || current?.meta.workspace_path));
    if (!owner) {onMessageFile(path); return;}
    if (!owner.groupId) {
      setWorkspaceOpen(true);
      changeWorkspace({selected: 'tasks', tasksPinned: true, tasksOpen: false,
        task: {...workspaceView.task, section: 'calls', callId: undefined, agentId: undefined, runId: undefined}});
    }
    host.set(host.sessionKey('videoPreview:' + owner.taskId, cid), owner.path);
  };
  const mediaProps = {cid, configured: settings?.media_capabilities || [], openFile: onMessageFile, configure: () => void goRoute('multimedia')};
  const videoCards = (ids: string[] | undefined, visible = true, heading = false) => ids?.length
    ? <Suspense fallback={null}><VideoTasks {...mediaProps} ids={ids} visible={visible} heading={heading} videoFiles={videoLayout.files}/></Suspense>
    : undefined;
  const runLabel = current?.run?.status === 'stopping' ? '正在停止，保留已完成内容…' : interaction ? '等待你的回答' : '正在处理';
  const reviewTask = activeReview(current?.reviews || [], current?.run, current?.id || '');
  const latestGroup = groups.at(-1);
  const questionGroup = active && interaction && groups.find(group => group.questions?.some(question =>
    !question.outcome && question.runId === current?.run?.id && question.request.request_id === interaction.request_id));
  const latestActiveGroup = latestGroup?.assistant && latestGroup.messages.some(message =>
    (message.meta?.turn_id || message.meta?.ui_turn_id) === current?.run?.id) ? latestGroup.id : undefined;
  const activeGroup = active ? questionGroup?.id || latestActiveGroup : undefined;
  const userStopped = !active && isUserStopped(current?.run);
  const failedRun = !active && !userStopped && current?.run?.error ? current.run : undefined;
  const contextRecovery = useMemo(() => failedRun && contextRecoveryForRun(events, failedRun.id), [events, failedRun]);
  const adjustTarget = contextRecovery === 'increase_target' && settings?.context_policy.enabled
    && settings.context_policy.target_tokens < 1_000_000;
  const stoppedGroup = userStopped ? [...groups].reverse().find(group => group.assistant && group.messages.some(message =>
    (message.meta?.turn_id || message.meta?.ui_turn_id) === current?.run?.id)) : undefined;
  const layout = useRef<HTMLDivElement>(null);
  const [, setSettingsTab] = useHostField('management:tab:settings', 'appearance');
  async function useEnterpriseCapability(item: EnterpriseCapability) {
    const cid = await ensureConversation();
    if (selectedId.current !== cid) return;
    const chat = conversationAt(host, cid);
    if (item.kind === 'expert') {
      const model = enterpriseExpertModel(item, chat, settings);
      await preferences({conversation_agent: {id: item.selection_key, name: item.name}, ...(model ? {selected_model_id: model} : {})}, cid);
    } else {
      const skills = chat?.meta.selected_skill_names ?? chat?.view?.selectedSkills ?? [];
      await preferences({selected_skill_names: [...new Set([...skills, item.selection_key])]}, cid);
    }
    if (selectedId.current === cid) {
      await goRoute('chat');
      requestAnimationFrame(() => {if (selectedId.current === cid) composer.current?.focus();});
    }
  }
  function finishInteraction(conversationId: string, requestId: string) {
    host.set<{request_id: string} | null>(host.sessionKey('interaction', conversationId),
      pending => pending?.request_id === requestId ? null : pending);
  }
  function openUsage(view: UsageView) {
    if (workspaceOpen) closeWorkspace();
    setUsageView(view);
    setUsageOpen(true);
    closeHistory();
  }
  function closeUsage() {
    setUsageOpen(false);
    usageTrigger.current?.focus({preventScroll: true});
  }
  function toggleUsage() {
    if (showUsage) closeUsage();
    else openUsage(usageOpen ? usageView : 'overview');
  }
  function continueTask() {
    if (!current) return;
    const entry = drafts.get(current.id);
    if (!entry.value.text.trim() && !entry.value.attachments.length && !entry.pending) {
      setDraft('请继续上一轮未完成的任务。先核对已经生成的文件与完成的操作，保留有效结果，避免重复已完成的操作。');
      if (contextRecovery) void send(current.id).catch(() => {});
    }
    composer.current?.focus();
  }
  function closeWorkspace() {
    setWorkspaceOpen(false);
    setWorkspaceExpanded(false);
  }
  async function toggleWorkspace() {
    if (workspaceOpen) {closeWorkspace(); return;}
    try {await ensureConversation(); setWorkspaceOpen(true); void refreshFiles(); void refreshChanges();}
    catch (failure) {error((failure as Error).message);}
  }
  function resizeWorkspace(clientX: number) {
    const box = layout.current?.getBoundingClientRect();
    if (!box) return;
    const width = Math.max(Math.min(280, box.width), box.right - clientX);
    if (width >= box.width - 80) {setWorkspaceExpanded(true); return;}
    setWorkspaceExpanded(false); changeWorkspace({width: Math.min(95, width / box.width * 100)});
  }
const fileTools = editor && current && <>{editor.file.payload.untitled?<input className="draft-file-name" aria-label="文稿文件名" value={editor.file.name} onChange={event=>setEditors(previous=>previous.map(item=>item.file.path===editor.file.path?{...item,file:{...item.file,name:event.target.value}}:item))}/>:<span title={editor.file.path}>{editor.file.name}</span>}
  {!showingDiff&&<FileToolbarControls/>}
  {!showingDiff && !editor.file.payload.read_only && editor.text !== editor.saved && (!editor.file.payload.untitled || editor.text.trim()) && <button className="primary" onClick={() => void saveEditor(editor.file.path).catch(failure => error(failure.message))}>保存</button>}
  {!showingDiff && editor.file.payload.recovery_available && editor.text !== editor.saved && <button className="primary" onClick={() => void saveCopy()}>保存恢复副本</button>}
  {!showingDiff && editor.file.payload.office_family && <button className="secondary" onClick={() => void call<string>('file.external', {conversation_id:current.id,path:editor.file.path}).then(message => {if(message)error(message);}).catch(failure => error(failure.message))}>打开应用编辑</button>}
  {(fileChange || showingDiff) && <button className="secondary" onClick={() => setEditors(previous => previous.map(item =>
    item.file.path === editor.file.path ? {...item, showChanges: !showingDiff} : item
  ))}><FileDiff size={14} aria-hidden="true"/>{showingDiff ? '隐藏更改' : '显示更改'}</button>}
  <MoreActions key={editor.file.id} editorMenu={!showingDiff} label="更多文件操作" actions={editor.file.payload.untitled?[]:[
    ['在资源管理器中打开', Folder, () => call('file.reveal', {conversation_id:current.id,path:editor.file.path})],
    ['复制路径', Copy, () => call('clipboard.write', {text:editor.file.path})],
    ...(!showingDiff && !editor.file.payload.office_family ? [['用本机应用打开', ExternalLink, async () => {const message=await call<string>('file.external', {conversation_id:current.id,path:editor.file.path});if(message)throw Error(message);}]] as MenuAction[] : []),
    ...(showingDiff ? [['刷新变更', RefreshCw, () => refreshChanges()]] : [['重新载入文件', RefreshCw, reloadFile]]) as MenuAction[],
  ]} error={error}/></>;

return <div className={`app-shell ${sidebar ? '' : 'sidebar-hidden'}`}>
    {sidebar && <Sidebar conversations={conversations} supplemental={sidebarConversations} layout={sidebarLayout} toolsRef={sidebarTools} onLayoutAction={changeSidebar} onError={error} projects={settings?.projects || []} selected={current?.id || ''} route={route} running={runningConversations}
      onSelect={id => void switchConversation(id)} onNew={async workspace => {await newConversation(workspace).catch(failure => error(failure.message));}}
      onRoute={next => void goRoute(next)} onProject={() => void addProject()}
      onProjectAction={(action, project) => void projectAction(action, project)} onAction={(action, item) => void conversationAction(action, item)} onMore={moreConversations} hasMore={hasMoreConversations}/>}
    <main style={{'--workspace-width': (workspaceView.width || 46) + '%'} as React.CSSProperties} className={`main-shell ${route === 'chat' && workspaceOpen ? 'chat-workspace' : ''} ${workspaceExpanded && route === 'chat' ? 'workspace-expanded' : ''}`}><header className="app-header"><div className="header-title">{route !== 'chat' && <button className="back-link" onClick={() => void goRoute('chat')}><ArrowLeft size={16}/>返回聊天</button>}
      {route === 'chat' ? rename ? <input autoFocus value={title} onChange={e => setTitle(e.target.value)} onBlur={() => setRename(false)} onKeyDown={async e => { if (e.key === 'Escape') setRename(false); if (e.key === 'Enter' && current) { await call('conversation.rename', { conversation_id: current.id, title }); setCurrent({ ...current, title }); await refreshList(); setRename(false); } }}/> : <button className="title-button" title="点击修改标题" onClick={() => { if (current) { setTitle(current.title); setRename(true); } }}>{current?.has_messages || current?.messages?.length ? current.title : ''}</button> : <h1 className="sr-only">{managementNames[route] || '插件与工作台'}</h1>}
      </div><div className="header-actions">{themeCandidate && !creationOpen && <button className="toolbar-button" title="查看 AI 主题候选" onClick={async () => {try {const value = await call('themes.preview'); setThemeCandidate(value.preview); if (value.preview) setThemePreview(value);} catch (failure) {error((failure as Error).message);}}}><Sparkles size={16}/>主题预览</button>}
      {route === 'chat' && <div className="chat-header-tools" role="group" aria-label="聊天工具">
        {hasMessages && <button ref={historyTrigger} className="icon-button" aria-label="历史提问"
          aria-haspopup="dialog" aria-expanded={showHistory} onClick={() => setShowHistory(!showHistory)}>
          <History size={16} aria-hidden="true"/>
        </button>}
        {hasMessages && settings && (usageToolbar?.showRate
          ? <UsageToolbarButton key={current?.id} iconOnly events={events} activeRunId={activeRunId} showRate
            open={showUsage} panelId={usagePanelId} buttonRef={usageTrigger} onClick={toggleUsage}/>
          : <button ref={usageTrigger} className="icon-button" aria-label="用量统计" aria-controls={showUsage ? usagePanelId : undefined}
            aria-expanded={showUsage} onClick={toggleUsage}><ChartColumn size={16} aria-hidden="true"/></button>)}
        <button className="icon-button" aria-label="工作区" aria-expanded={workspaceOpen}
          onClick={() => void toggleWorkspace()}><PanelRight size={16} aria-hidden="true"/></button>
      </div>}
      </div></header>
      {route === 'chat' && settings && showHistory && <div className="header-popovers" ref={historyPanel}>
      <section className="history-popover" role="dialog" aria-label="历史提问">
        <div className="navigator-heading"><strong>历史提问</strong><button autoFocus className="icon-button"
          aria-label="关闭历史提问" onClick={() => closeHistory(true)}><X size={15}/></button></div>
        <div>{messages.filter(message => message.role === 'user').map(message => <button key={message.id} onClick={() => {
          document.getElementById('message-' + message.id)?.scrollIntoView({block: 'center'});
          closeHistory(true);
        }}>{messageText(message.content).slice(0,160)}</button>)}</div>
        {current?.has_more && <button className="secondary" disabled={olderLoading} onClick={() => void loadOlder()}>载入更早的对话</button>}
      </section></div>}
      {serviceError && <div className="service-error"><strong>本地服务异常</strong><span>{serviceError}</span><button onClick={() => void call('app.reconnect').catch(failure => setServiceError(failure.message))}>重新连接</button><button onClick={() => void call('app.logs')}>查看诊断日志</button></div>}
      {!serviceError && route==='chat' && !loading && projectionError && <div className="service-error" role="alert"><span>{projectionError}</span><button onClick={() => {if(current)void loadConversation(current.id,true).catch(failure=>error(failure.message));}}>重试</button><button onClick={() => void call('app.logs')}>查看诊断日志</button></div>}
      <PluginSlot kind="page" target={route}>
      {loading ? <div className="loading"><LoaderCircle size={23} className="spin"/><p>正在打开你的工作台…</p></div> : route === 'chat' ? <div ref={layout} className={`work-layout ${workspaceOpen ? 'with-workspace' : ''} ${workspaceExpanded ? 'expanded' : ''}`}>
        <section className={`conversation ${hasMessages ? '' : 'home-conversation'} ${home ? 'workbench-home' : ''}`} style={chatVisible ? undefined : {display:'none'}}>
        <div className={hasMessages ? 'chat-layout' : 'chat-empty-layout'}>
        {usageAvailable && settings && <div className="usage-dock" hidden={!showUsage}><Usage
          key={current?.id}
          id={usagePanelId}
          events={events}
          activeRunId={activeRunId}
          turnId={current?.run?.id}
          messages={current?.render_messages || current?.messages}
          hasMoreHistory={current?.has_more}
          settings={settings}
          view={usageView}
          onView={setUsageView}
          onClose={closeUsage}
          onOpenFile={onMessageFile}
          onSave={async target => setSettings(await call('settings.context', {target_tokens: target}))}
        /></div>}
        <div className="message-scroll chat-reading-content" ref={scroll} onScroll={() => { if (scroll.current) nearBottom.current = scroll.current.scrollHeight - scroll.current.scrollTop - scroll.current.clientHeight < 120; }}>
          {!hasMessages ? <div className="welcome">{home ? <h2>今天，想一起完成什么？</h2> : <>
            <div className="welcome-eyebrow">从想法到你的专属界面</div><h2>想把界面变成什么样？</h2>
            <p>选择左侧区域，描述你的想法；生成后预览，再决定是否应用。</p>
          </>}</div> : <div className="messages">
{current?.has_more && <button className="load-history" disabled={olderLoading} onClick={() => void loadOlder()}>{olderLoading ? <LoaderCircle size={14} className="spin"/> : <ChevronDown size={14}/>}更早的消息</button>}
{groups.map((group, groupIndex) => { const message = group.messages[0]; return historyEdit?.conversationId === current?.id && historyEdit?.prepared.message.id === message.id ? <div className="message-edit-container" key={message.id}><HistoryEditor conversationId={historyEdit.conversationId} prepared={historyEdit.prepared} onClose={() => setHistoryEdit(null)} onError={error} submit={async value => {
      const result = await drafts.submit(historyEdit.conversationId, {...value,
        model_id: current?.meta.selected_model_id || settings?.selected_model_id, consume_draft: false,
        rewrite: {hash: historyEdit.prepared.hash, revision: historyEdit.prepared.revision, message_id: historyEdit.prepared.message.id}});
      return {...result, conversation: await call<Conversation>('conversation.get', {conversation_id: historyEdit.conversationId})};
    }} onSaved={result => {
      setHistoryUndo(null);
      if (selectedId.current === historyEdit.conversationId) { setCurrent(result.conversation); setEvents(unwrapEvents(result.conversation.events || [])); }
      setHistoryEdit(null); void refreshList();
    }}/></div> : group.assistant ? <AssistantTurn reviewTask={group.id === activeGroup ? reviewTask : undefined} stopped={group.id === stoppedGroup?.id}
      statusLabel={runLabel} key={current!.id + ':' + group.id} timing={timings.get(message.meta?.turn_id || message.meta?.ui_turn_id)}
      activeRun={group.id === activeGroup ? current?.run?.id : undefined} questions={group.questions}
      interactionRun={active ? current?.run?.id : undefined}
      showDuration={!groups.slice(groupIndex + 1).some(next => next.assistant && (next.messages[0].meta?.turn_id || next.messages[0].meta?.ui_turn_id) === (message.meta?.turn_id || message.meta?.ui_turn_id))}
      onInteractionDone={requestId => finishInteraction(current!.id, requestId)}
      onHistory={(files, selected) => void openFileHistory(files, selected)}
      onHistoryRetry={() => void loadConversation(current!.id, true).catch(failure => error(failure.message))}
      messages={group.messages} cid={current!.id} active={active} onAction={onMessageAction} onFile={openChatFile}
      videoFiles={videoLayout.files.size ? videoLayout.files : undefined} videoTasks={videoCards(videoLayout.groups.get(group.id), chatVisible)}/>
      : <MessageBubble key={current!.id + ':' + message.id} cid={current!.id} message={message} active={active} onAction={onMessageAction} onFile={onMessageFile}/>;}) }
            {videoLayout.hasOtherTasks && <Suspense fallback={null}><ChatMediaTasks key={cid} {...mediaProps}/></Suspense>}
            {active && !activeGroup && (reviewTask ? <GenerationTaskStatus task={reviewTask} statusLabel={runLabel} waiting={current?.run?.status === 'queued'}/> : <RunStatus label={runLabel}/>)}
            {userStopped && !stoppedGroup && <RunStatus label="已停止"/>}
            {failedRun && <div className="run-error">
              <span>{failedRun.status === 'interrupted' ? '任务意外中断' : '这次任务未完成'}</span>
              <p>{adjustTarget ? '已保存聊天和已有内容。可在用量详情中把下次目标调到 1M，再继续这个任务。'
                : contextRecovery === 'new_chat' ? '当前内容超过模型可用长度，聊天已保留。可以新建聊天，或减少本次材料后再试。'
                : contextRecovery ? '聊天和已有内容已保留。' : runErrorSummary(failedRun.error)}</p>
              <details><summary>查看具体原因</summary><pre>{failedRun.error}</pre></details>
              <div className="button-row">
                {adjustTarget ? <button className="secondary" onClick={() => openUsage('details')}>打开用量详情</button>
                  : contextRecovery !== 'new_chat' && <button className="secondary" disabled={sending} onClick={continueTask}>继续这个任务</button>}
                {contextRecovery && !adjustTarget && <button className="secondary"
                  onClick={() => void newConversation(current?.meta.workspace_dir).catch(failure => error(failure.message))}>新建聊天</button>}
              </div>
            </div>}
            </div>}
        </div>
        <div className="composer-container">
<DraftRecovery pending={drafts.needsRecovery(selectedId.current)} busy={sending} recover={retry => void recoverSend(retry)}/>
          {current?.meta.ppt_agent_mode && <p className="muted" role="status">PPT 助手已下线，历史与文件保留。后续消息将通过普通对话处理。</p>}
          <Composer useAction={usePluginAction} modes={registry.inventory.modes} creation={creationOpen && Boolean(current?.meta.creation_session)} enterCreation={enterCreation} exitCreation={exitCreation} conversation={current} conversations={conversations} settings={settings} text={draft} setText={setDraft} attachments={attachments} setAttachments={setAttachments} skills={selectedSkills} active={active} sending={sending} textarea={composer} ensureConversation={ensureConversation} preferences={preferences} send={send} stop={stop} attach={attach} browseLibrary={browseLibrary} openFile={path => void openFile(path).catch(error)} route={next => {if (next === 'settings') setSettingsTab('models'); return goRoute(next);}} error={error}
            home={home}
            selectModel={async id => {await preferences({selected_model_id: id}); setSettings(await call('settings.select_model', {model_id: id}));}}
            permission={async enabled => {setSettings(await call('settings.permission', {enabled}));}}/>
          {home && <HomeComposerFooter key={current?.id} conversation={current} projects={settings?.projects || []}
            disabled={!settings || active || sending || drafts.needsRecovery(selectedId.current)}
            selectProject={async project => {await newConversation(project?.path, selectedId.current);}}
            onOpenChange={setProjectMenuOpen} createProject={() => addProject()}
            startPrompt={text => {setDraft(text); composer.current?.focus();}}
            enterCreation={registry.inventory.modes?.some(mode => mode.mode === 'creation' && mode.enabled)
              ? () => void enterCreation().catch(failure => error(failure.message)) : undefined}/>}
        </div>
        </div>
        </section>
        {workspaceOpen && current && <div className="workspace-divider" role="separator" tabIndex={0} aria-label="调整工作区宽度" aria-orientation="vertical" aria-valuemin={25} aria-valuemax={100} aria-valuenow={workspaceExpanded ? 100 : workspaceView.width || 46} onPointerDown={event => {event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);}} onPointerMove={event => {if (event.currentTarget.hasPointerCapture(event.pointerId)) resizeWorkspace(event.clientX);}} onPointerUp={event => event.currentTarget.releasePointerCapture(event.pointerId)} onKeyDown={event => {if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {event.preventDefault(); if (event.key === 'End') setWorkspaceExpanded(true); else {setWorkspaceExpanded(false); changeWorkspace({width: event.key === 'Home' ? 46 : Math.max(25, Math.min(95, (workspaceExpanded ? 100 : workspaceView.width || 46) + (event.key === 'ArrowLeft' ? 5 : -5)))});}}}}/>}
        {workspaceOpen && current && <PluginSlot kind="panel" target="workspace" scope={{conversationId:current.id}}><Workspace key={current.id} conversation={current} value={workspaceView} change={value => {if (selectedId.current === current.id) changeWorkspace(value);}} expanded={workspaceExpanded} expand={() => setWorkspaceExpanded(!workspaceExpanded)} close={closeWorkspace} obscured={Boolean(projectDialog || projectMenuOpen || showHistory || ask || historyEdit || showSkills || pluginPreview || themePreview || creationOpen && creationTab!=='design')} dark={theme === 'dark'} editors={editors} fileTree={fileTree} preserve={saveView} openReview={openReview} registerEditorFlush={registerEditorFlush} selectFile={activateFile} openFile={openFile} closeFile={closeEditor} refreshFiles={refreshFiles} createDocument={createDocument}
          canViewChanges={Boolean(changes?.repository || workspaceChanges?.error)} openChanges={() => void openChanges()}
          videoTasks={videoCards(videoLayout.unplaced, true, true)}
          historyContent={workspaceView.fileHistory && <FileHistoryPanel cid={current.id} {...workspaceView.fileHistory}
            select={selected => changeWorkspace({fileHistory: {...workspaceView.fileHistory!, selected}})} openFile={onMessageFile}/>}
          changesContent={<ChangesPanel cid={current.id} read={workspaceChanges} selection={selectedChange} select={selectChange}
            openFile={path => void openFile(path, false, true)} refresh={refreshChanges} error={error}/>}
          error={error} confirm={confirm} libraryContent={<Suspense fallback={<div className="loading">正在打开资料库…</div>}><Library scope={'workspace:' + current.id} onError={error} confirm={confirm} projectsKey={JSON.stringify(settings?.projects || [])} onReference={(source, items) => addLibraryReference(source, items, current.id)}/></Suspense>} fileContent={<FileToolbarProvider key={current.id+':'+editor?.file.path}>
          {editor && showingDiff && <div className="workspace-file-changes">
            <div className="file-toolbar">{fileTools}</div>
            {fileChange && changes ? <WorkspaceDiff key={fileChange.absolute_path} cid={current.id} file={fileChange} snapshot={changes} refresh={refreshChanges}/>
              : <p className="workspace-diff-empty" role="status">{workspaceChanges?.error || (workspaceChanges?.pending ? '正在读取变更…' : '此文件没有更改。')}</p>}
          </div>}
          <div className="workspace-file-editor" hidden={showingDiff}>
          {editor && !["preview", "html"].includes(editor.file.payload.kind) && <div className="file-toolbar">{!showingDiff && fileTools}</div>}
          {editor && fileChanged && <div className="file-update-notice" role="status">文件已更新<button className="text-link" onClick={() => void reloadFile()}>刷新</button></div>}
          {editor?.file.payload.recovery_available && editor.text !== editor.saved && <div className="file-update-notice" role="status">上次未保存的编辑已恢复。请保存恢复副本，再用本机应用继续编辑。</div>}
          {editor && (editor.file.conflict || editor.file.unavailable) && <div className="run-error">原文件已变化或暂时无法打开。当前编辑仍保留，可以复制内容或<button className="secondary" onClick={() => void saveCopy()}>另存副本</button>。</div>}
          {editor ? <PluginSlot kind="editor" target={editor.file.payload.editor_target || editor.file.payload.kind} scope={{conversationId:current?.id,filePath:editor.file.path}}>{(['preview', 'html'].includes(editor.file.payload.kind) ? <FilePreview key={editor.file.id} cid={current!.id} toolbar={!showingDiff && fileTools} file={editor.file} text={editor.text} change={text => changeEditor(editor.file.path, text)} register={registerEditorFlush}/> : editor.file.payload.kind === 'sheet' ? <SpreadsheetWorkspace key={editor.file.id} cid={current!.id} file={editor.file} text={editor.text} onChange={text => changeEditor(editor.file.path, text)} error={error} register={registerEditorFlush}/> : editor.file.payload.kind !== 'text' ? <OfficeEditor key={editor.file.id} file={editor.file} text={editor.text} onChange={text => changeEditor(editor.file.path, text)} error={error} register={registerEditorFlush}/> : <Suspense fallback={<div className="loading">正在载入编辑器…</div>}><CodeEditor key={current.id + ":" + editor.file.path} cid={current.id} file={editor.file} text={editor.text} saved={editor.saved} change={text => changeEditor(editor.file.path,text)} save={async () => {await saveEditor(editor.file.path);}}/></Suspense> )}</PluginSlot> : <div className="empty-workspace">选择一个文件开始查看。</div>}
          </div>
        </FileToolbarProvider>}/></PluginSlot>}

      </div> : <Suspense fallback={<div className="loading"><LoaderCircle className="spin"/>正在载入…</div>}><Management returnToChat={()=>void goRoute('chat')} key={route} route={route} settings={settings} updateSettings={setSettings} openConversation={id => void switchConversation(id)} onError={error} confirm={confirm} registerLeave={registerLeave}
        onReference={addLibraryReference} useEnterpriseCapability={useEnterpriseCapability} openEnterpriseAccount={() => {setSettingsTab('enterprise'); void goRoute('settings');}}/></Suspense>}
      </PluginSlot>
    </main>

    {historyUndo && <div className="undo-toast" role="status"><span>历史已更新</span><button onClick={async () => {
      try { const value = await call<Conversation>('history.undo', { conversation_id: historyUndo.conversationId, undo: historyUndo.token });
        if (selectedId.current === value.id) { setCurrent(value); setEvents(unwrapEvents(value.events || [])); }
        setHistoryUndo(null); await refreshList();
      } catch (failure) { error((failure as Error).message); }
    }}>撤销</button><button aria-label="关闭撤销提示" onClick={() => setHistoryUndo(null)}><X size={14}/></button></div>}

  </div>;

}
