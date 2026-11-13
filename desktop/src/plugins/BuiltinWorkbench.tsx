import { useCallback, useEffect, useLayoutEffect, useRef, useState, useMemo, lazy, Suspense } from 'react';
import { ArrowUp, Square, Plus, Search, ChartColumn, History, Settings as SettingsIcon, PanelsTopLeft, Folder, FileText, ChevronRight, ChevronDown, X, Copy, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Maximize2, Minimize2, Puzzle, Check, LoaderCircle, Paperclip, ShieldCheck, Sun, Moon, RefreshCw, ArrowLeft, Download, Code2, BookOpen, Sparkles, ExternalLink } from 'lucide-react';
import type { Attachment, Conversation, FileSession, HostEvent, Message, Plugin, RuntimeEvent, Settings } from '../types';
import { displayMessages, messageGroups, isActive, mergeEvents, messageText, runErrorSummary, unwrapEvents, turnTimings, replyParts } from '../runtime';
import PluginSlot from './PluginSlot';
import PluginManager from './PluginManager';
import Usage from '../Usage';
import { Drafts, type Draft } from '../drafts';
import DraftRecovery from '../DraftRecovery';
import Sidebar from '../Sidebar';
import MessageBubble, {AssistantTurn} from '../MessageBubble';
import HistoryEditor from '../HistoryEditor';
import Dialog from '../Dialog';
import OfficeEditor from '../OfficeEditor';
import FilePreview from '../FilePreview';
import Workspace, { emptyWorkspace, type WorkspaceView } from '../Workspace';
import Composer from '../Composer';
import { themeStyle } from '../theme';
import ThemePreview from '../ThemePreview';
const Management = lazy(() => import('../Management'));
const managementNames: Record<string, string> = { settings: '设置', capabilities: '能力', library: '资料库', favorites: '常用任务', team: '团队空间' };

const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
type Ask = { title: string; detail: string; choices: string[]; resolve: (index: number) => void };
type Editor = { file: FileSession; text: string; saved: string; plugin: Plugin };

import {useWorkbench} from '../host/Host';
import {useHostField} from '../host/context';
export default function BuiltinWorkbench() {
  const {settings, setSettings, conversations, setConversations, hasMoreConversations, setHasMoreConversations, runningConversations, current, setCurrent, events, setEvents, route, setRoute, loading, setLoading, serviceError, setServiceError, notice, setNotice, draft, attachments, drafts, draftConflict, setDraftConflict, sending, setSending, query, setQuery, sidebar, setSidebar, workspaceOpen, setWorkspaceOpen, workspaceExpanded, setWorkspaceExpanded, workspaceView, setWorkspaceView, changeWorkspace, fileTree, setFileTree, editors, setEditors, activeFile, setActiveFile, fileChanged, setFileChanged, interaction, setInteraction, ask, setAsk, historyEdit, setHistoryEdit, historyUndo, setHistoryUndo, olderLoading, setOlderLoading, pluginPreview, setPluginPreview, themeCandidate, setThemeCandidate, themePreview, setThemePreview, previewText, setPreviewText, showUsage, setShowUsage, headerPanel, setHeaderPanel, messageQuery, setMessageQuery, skills, setSkills, selectedSkills, showSkills, setShowSkills, modelForm, setModelForm, formBusy, setFormBusy, rename, setRename, title, setTitle, scroll, nearBottom, composer, selectedId, loadGeneration, draftRef, editorsRef, editorFlush, registerEditorFlush, stateRef, pendingSteering, closePending, viewRevisions, viewQueue, theme, leaveManagement, registerLeave, active, error, confirm, applyDraft, setDraft, setAttachments, refreshList, moreConversations, loadOlder, messageActionRef, fileActionRef, onMessageAction, onMessageFile, addProject, conversationAction, saveDraft, saveView, refreshFiles, loadConversation, switchConversation, newConversation, closeApp, send, stop, resolveDraft, recoverSend, creatingConversation, ensureConversation, preferences, attach, activateFile, openFile, saveEditor, reloadFile, saveCopy, changeEditor, closeEditor, createDocument, goRoute, applyPlugin, messages, questions, editor, filtered, registry, saves} = useWorkbench();
  const timings = useMemo(() => turnTimings(events), [events]);
  const groups = useMemo(() => messageGroups(messages, questions), [messages, questions]);
  const runLabel = current?.run?.status === 'stopping' ? '正在停止，保留已完成内容…' : interaction ? '等待你的回答' : '正在处理';
  const latestGroup = groups.at(-1);
  const activeGroup = active && latestGroup?.assistant && latestGroup.messages.some(message =>
    (message.meta?.turn_id || message.meta?.ui_turn_id) === current?.run?.id) ? latestGroup.id : undefined;
  const activeProcess = activeGroup && latestGroup && (replyParts(latestGroup.messages).hasProcess || latestGroup.questions?.length);
  const layout = useRef<HTMLDivElement>(null);
  const [, setSettingsTab] = useHostField('management:tab:settings', 'appearance');
  function resizeWorkspace(clientX: number) {
    const box = layout.current?.getBoundingClientRect();
    if (!box) return;
    const width = Math.max(Math.min(280, box.width), box.right - clientX);
    if (width >= box.width - 80) {setWorkspaceExpanded(true); return;}
    setWorkspaceExpanded(false); changeWorkspace({width: Math.min(95, width / box.width * 100)});
  }
const fileTools = editor && current && <><span title={editor.file.path}>{editor.file.name}</span>
  {!editor.file.payload.read_only && editor.text !== editor.saved && <button className="primary" onClick={() => void saveEditor(editor.file.path).catch(failure => error(failure.message))}>保存</button>}
  {editor.file.payload.recovery_available && editor.text !== editor.saved && <button className="primary" onClick={() => void saveCopy()}>保存恢复副本</button>}
  {editor.file.payload.office_family && <button className="secondary" onClick={() => void call<string>('file.external', {conversation_id:current.id,path:editor.file.path}).then(message => {if(message)error(message);}).catch(failure => error(failure.message))}>打开应用编辑</button>}
  <FileActions key={editor.file.id} actions={[
    ['在文件夹中显示', Folder, () => call('file.reveal', {conversation_id:current.id,path:editor.file.path})],
    ['复制路径', Copy, () => call('clipboard.write', {text:editor.file.path})],
    ...(!editor.file.payload.office_family ? [['用本机应用打开', ExternalLink, async () => {const message=await call<string>('file.external', {conversation_id:current.id,path:editor.file.path});if(message)throw Error(message);}]] as FileAction[] : []),
    ['重新载入文件', RefreshCw, reloadFile],
  ]} error={error}/></>;

return <div className={`app-shell ${sidebar ? '' : 'sidebar-hidden'}`}>
    {sidebar && <Sidebar conversations={conversations} projects={settings?.projects || []} selected={current?.id || ''} route={route} running={runningConversations}
      onSelect={id => void switchConversation(id)} onNew={workspace => void newConversation(workspace).catch(failure => error(failure.message))}
      onRoute={next => void goRoute(next)} onCollapse={() => setSidebar(false)} onProject={() => void addProject()}
      onAction={(action, item) => void conversationAction(action, item)} onMore={moreConversations} hasMore={hasMoreConversations}/>}
    <main style={{'--workspace-width': (workspaceView.width || 46) + '%'} as React.CSSProperties} className={`main-shell ${route === 'chat' && workspaceOpen ? 'chat-workspace' : ''} ${workspaceExpanded && route === 'chat' ? 'workspace-expanded' : ''}`}><header className="app-header"><div className="header-title">{!sidebar && <button className="icon-button" title="展开侧栏" onClick={() => setSidebar(true)}><PanelLeftOpen size={18}/></button>}{route !== 'chat' && <button className="back-link" onClick={() => void goRoute('chat')}><ArrowLeft size={16}/>返回聊天</button>}
      {route === 'chat' ? rename ? <input autoFocus value={title} onChange={e => setTitle(e.target.value)} onBlur={() => setRename(false)} onKeyDown={async e => { if (e.key === 'Escape') setRename(false); if (e.key === 'Enter' && current) { await call('conversation.rename', { conversation_id: current.id, title }); setCurrent({ ...current, title }); await refreshList(); setRename(false); } }}/> : <button className="title-button" title="点击修改标题" onClick={() => { if (current) { setTitle(current.title); setRename(true); } }}>{current?.has_messages || current?.messages?.length ? current.title : ''}</button> : <h1 className="sr-only">{managementNames[route] || '插件与工作台'}</h1>}
      </div><div className="header-actions">{themeCandidate && <button className="toolbar-button" title="查看 AI 主题候选" onClick={async () => {try {const value = await call('themes.preview'); setThemeCandidate(value.preview); if (value.preview) setThemePreview(value);} catch (failure) {error((failure as Error).message);}}}><Sparkles size={16}/>主题预览</button>}{route === 'chat' && <>
      {messages.length > 0 && <><button className="icon-button" title="用量" aria-expanded={showUsage} onClick={() => {setShowUsage(!showUsage); setHeaderPanel('');}}><ChartColumn size={17}/></button><button className="icon-button" title="搜索当前对话" onClick={() => {setHeaderPanel(headerPanel === 'search' ? '' : 'search'); setShowUsage(false);}}><Search size={17}/></button><button className="icon-button" title="历史提问" onClick={() => {setHeaderPanel(headerPanel === 'history' ? '' : 'history'); setShowUsage(false);}}><History size={17}/></button></>}
      {!workspaceOpen && <button className="toolbar-button" onClick={async () => {try {await ensureConversation(); setWorkspaceOpen(true); void refreshFiles();} catch (failure) {error((failure as Error).message);}}}><Folder size={16}/>工作区</button>}
      </>}

      <button className="icon-button" title={theme === 'dark' ? '切换浅色' : '切换深色'} onClick={async () => { const next = theme === 'dark' ? 'light' : 'dark'; await call('settings.theme', { theme: next }); setSettings(previous => previous ? { ...previous, theme: next } : null); }}>{theme === 'dark' ? <Sun size={17}/> : <Moon size={17}/>}</button></div></header>
      {route === 'chat' && settings && <div className="header-popovers">{showUsage && <Usage visible={showUsage} events={events} settings={settings} onClose={() => setShowUsage(false)} onSave={async target => setSettings(await call('settings.context', {target_tokens: target}))}/>} {headerPanel && <section className="history-popover"><div className="navigator-heading"><strong>{headerPanel === 'search' ? '搜索当前对话' : '历史提问'}</strong><button className="icon-button" title="关闭" onClick={() => setHeaderPanel('')}><X size={15}/></button></div>{headerPanel === 'search' && <input autoFocus aria-label="搜索当前对话" placeholder="输入关键词" value={messageQuery} onChange={event => setMessageQuery(event.target.value)}/>}<div>{messages.filter(message => headerPanel === 'history' ? message.role === 'user' : messageQuery.trim() && messageText(message.content).toLowerCase().includes(messageQuery.toLowerCase())).map(message => <button key={message.id} onClick={() => {document.getElementById('message-' + message.id)?.scrollIntoView({block: 'center'}); setHeaderPanel('');}}>{messageText(message.content).slice(0,160)}</button>)}</div>{current?.has_more && <button className="secondary" disabled={olderLoading} onClick={() => void loadOlder()}>载入更早的对话</button>}</section>}</div>}
      {serviceError && <div className="service-error"><strong>本地服务连接中断</strong><span>{serviceError}</span><button onClick={() => void call('app.reconnect').catch(failure => setServiceError(failure.message))}>重新连接</button><button onClick={() => void call('app.logs')}>查看诊断日志</button></div>}
      <PluginSlot kind="page" target={route}>
      {loading ? <div className="loading"><LoaderCircle size={23} className="spin"/><p>正在打开你的工作台…</p></div> : route === 'chat' ? <div ref={layout} className={`work-layout ${workspaceOpen ? 'with-workspace' : ''} ${workspaceExpanded ? 'expanded' : ''}`}>
        <section className={`conversation ${!messages.length ? 'home-conversation' : ''}`} style={workspaceExpanded && workspaceView.selected !== 'conversation' ? {display:'none'} : undefined}><div className="message-scroll" ref={scroll} onScroll={() => { if (scroll.current) nearBottom.current = scroll.current.scrollHeight - scroll.current.scrollTop - scroll.current.clientHeight < 120; }}>
          {!messages.length ? <div className="welcome"><div className="welcome-eyebrow">从一个想法，开始今天的工作</div><h2>今天，想一起完成什么？</h2><p>整理资料、分析数据，或把一个想法做出来。</p></div> : <div className="messages">
{current?.has_more && <button className="load-history" disabled={olderLoading} onClick={() => void loadOlder()}>{olderLoading ? <LoaderCircle size={14} className="spin"/> : <ChevronDown size={14}/>}更早的消息</button>}
{groups.map((group, groupIndex) => { const message = group.messages[0]; return historyEdit?.conversationId === current?.id && historyEdit?.prepared.message.id === message.id ? <div className="message-edit-container" key={message.id}><HistoryEditor conversationId={historyEdit.conversationId} prepared={historyEdit.prepared} onClose={() => setHistoryEdit(null)} onError={error} submit={async value => {
      const result = await drafts.submit(historyEdit.conversationId, {...value,
        model_id: current?.meta.selected_model_id || settings?.selected_model_id, skills: selectedSkills, consume_draft: false,
        rewrite: {hash: historyEdit.prepared.hash, revision: historyEdit.prepared.revision, message_id: historyEdit.prepared.message.id}});
      return {...result, conversation: await call<Conversation>('conversation.get', {conversation_id: historyEdit.conversationId})};
    }} onSaved={result => {
      setHistoryUndo(null);
      if (selectedId.current === historyEdit.conversationId) { setCurrent(result.conversation); setEvents(unwrapEvents(result.conversation.events || [])); }
      setHistoryEdit(null); void refreshList();
    }}/></div> : group.assistant ? <AssistantTurn statusLabel={runLabel} key={group.id} timing={timings.get(message.meta?.turn_id || message.meta?.ui_turn_id)} activeRun={group.id === activeGroup ? current?.run?.id : undefined} questions={group.questions} interactionRun={active ? current?.run?.id : undefined} showDuration={!groups.slice(groupIndex + 1).some(next => next.assistant && (next.messages[0].meta?.turn_id || next.messages[0].meta?.ui_turn_id) === (message.meta?.turn_id || message.meta?.ui_turn_id))} onInteractionDone={() => setInteraction(null)} messages={group.messages} cid={current!.id} active={active} onAction={onMessageAction} onFile={onMessageFile}/> : <MessageBubble key={message.id} cid={current!.id} message={message} active={active} onAction={onMessageAction} onFile={onMessageFile}/>;}) }
            {active && !activeProcess && <div className="run-state"><LoaderCircle size={14} className="spin"/>{runLabel}</div>}
            {!active && current?.run?.error && <div className="run-error"><span>{current.run.status === 'interrupted' ? '任务已停止' : '这次任务未完成'}</span><p>{runErrorSummary(current.run.error)}</p><details><summary>查看具体原因</summary><pre>{current.run.error}</pre></details><button className="secondary" onClick={() => {setDraft('请继续上一轮未完成的任务。先核对已经生成的文件与完成的操作，保留有效结果，避免重复已完成的操作。');composer.current?.focus();}}>继续这个任务</button></div>}
            </div>}
        </div><div className="composer-container">
<DraftRecovery conflict={draftConflict} pending={Boolean(drafts.get(selectedId.current).pending)} resolve={keep => void resolveDraft(keep)} recover={retry => void recoverSend(retry)}/>
          <Composer conversation={current} conversations={conversations} settings={settings} text={draft} setText={setDraft} attachments={attachments} setAttachments={setAttachments} skills={selectedSkills} active={active} sending={sending} textarea={composer} ensureConversation={ensureConversation} preferences={preferences} send={send} stop={stop} attach={attach} route={next => {if (next === 'settings') setSettingsTab('models'); return goRoute(next);}} error={error}
            selectModel={async id => {await preferences({selected_model_id: id}); setSettings(await call('settings.select_model', {model_id: id}));}}
            permission={async enabled => {setSettings(await call('settings.permission', {enabled}));}}/>
        </div>

          {!messages.length && <div className="home-followup"><div className="home-shortcuts">{[['PPT 助手', '帮我制作一份演示文稿。'], ['金融分析', '帮我分析这些金融资料。'], ['数据分析', '帮我分析这份数据，找出关键趋势。'], ['浏览器自动化', '帮我在浏览器中完成这个任务。']].map(([label, text]) => <button className="secondary" key={label} onClick={() => {setDraft(text); composer.current?.focus();}}><Sparkles size={16}/>{label}</button>)}</div><div className="recent-heading"><span>继续最近的任务</span><button onClick={() => setSidebar(true)}>查看全部</button></div>{conversations.filter(item => !item.meta.archived && item.id !== current?.id && item.has_content !== false).slice(0,3).map(item => <button key={item.id} className="recent-task" onClick={() => void switchConversation(item.id)}><FileText size={17}/><span>{item.display_title || item.title}<small>{item.meta.project_name || '独立聊天'} · {new Date(item.updated_at * 1000).toLocaleDateString()}</small></span><ChevronRight size={15}/></button>)}<details className="home-options"><summary>工作台选项</summary><button onClick={() => void goRoute('plugins')}>界面插件</button><button onClick={() => void goRoute('settings')}>外观与设置</button></details></div>}
        </section>
        {workspaceOpen && current && <div className="workspace-divider" role="separator" tabIndex={0} aria-label="调整工作区宽度" aria-orientation="vertical" aria-valuemin={25} aria-valuemax={100} aria-valuenow={workspaceExpanded ? 100 : workspaceView.width || 46} onPointerDown={event => {event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);}} onPointerMove={event => {if (event.currentTarget.hasPointerCapture(event.pointerId)) resizeWorkspace(event.clientX);}} onPointerUp={event => event.currentTarget.releasePointerCapture(event.pointerId)} onKeyDown={event => {if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {event.preventDefault(); if (event.key === 'End') setWorkspaceExpanded(true); else {setWorkspaceExpanded(false); changeWorkspace({width: event.key === 'Home' ? 46 : Math.max(25, Math.min(95, (workspaceExpanded ? 100 : workspaceView.width || 46) + (event.key === 'ArrowLeft' ? 5 : -5)))});}}}}/>}
        {workspaceOpen && current && <PluginSlot kind="panel" target="workspace" scope={{conversationId:current.id}}><Workspace key={current.id} conversation={current} value={workspaceView} change={value => {if (selectedId.current === current.id) changeWorkspace(value);}} expanded={workspaceExpanded} expand={() => setWorkspaceExpanded(!workspaceExpanded)} close={() => {setWorkspaceOpen(false); setWorkspaceExpanded(false);}} obscured={Boolean(ask || historyEdit || showSkills || pluginPreview || themePreview)} dark={theme === 'dark'} editors={editors} fileTree={fileTree} selectFile={activateFile} openFile={openFile} closeFile={closeEditor} refreshFiles={refreshFiles} createDocument={createDocument} error={error} confirm={confirm} fileContent={<>
          {editor && !["preview", "html"].includes(editor.file.payload.kind) && <div className="file-toolbar">{fileTools}</div>}
          {editor && fileChanged && <div className="file-update-notice" role="status">文件已更新<button className="text-link" onClick={() => void reloadFile()}>刷新</button></div>}
          {editor?.file.payload.recovery_available && editor.text !== editor.saved && <div className="file-update-notice" role="status">上次未保存的编辑已恢复。请保存恢复副本，再用本机应用继续编辑。</div>}
          {editor && (editor.file.conflict || editor.file.unavailable) && <div className="run-error">原文件已变化或暂时无法打开。当前编辑仍保留，可以复制内容或<button className="secondary" onClick={() => void saveCopy()}>另存副本</button>。</div>}
          {editor ? <PluginSlot kind="editor" target={editor.file.payload.kind} scope={{conversationId:current?.id,filePath:editor.file.path}}>{(['preview', 'html'].includes(editor.file.payload.kind) ? <FilePreview key={editor.file.id} cid={current!.id} toolbar={fileTools} file={editor.file} text={editor.text} change={text => changeEditor(editor.file.path, text)}/> : editor.file.payload.kind !== 'text' ? <OfficeEditor key={editor.file.id} file={editor.file} text={editor.text} onChange={text => changeEditor(editor.file.path, text)} error={error} register={registerEditorFlush}/> : <textarea className="plain-editor" aria-label="文档正文" value={editor.text} onChange={event => changeEditor(editor.file.path,event.target.value)}/> )}</PluginSlot> : <div className="empty-workspace">选择一个文件开始查看。</div>}
        </>}/></PluginSlot>}

      </div> : managementNames[route] ? <Suspense fallback={<div className="loading"><LoaderCircle className="spin"/>正在载入…</div>}><Management key={route} route={route} settings={settings} updateSettings={setSettings} openPlugins={() => void goRoute('plugins')} openConversation={id => void switchConversation(id)} onError={error} confirm={confirm} registerLeave={registerLeave}
        onReference={async ref => {
          const id = await ensureConversation();
          const target = await call<Conversation>('conversation.get', {conversation_id: id});
          const refs = [...(target.meta.knowledge_refs || []).filter((item: any) => JSON.stringify(item) !== JSON.stringify(ref)), ref];
          await preferences({knowledge_refs: refs}, id);
        }}/></Suspense> : <PluginManager/>}
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

type FileAction = [string, React.ElementType, () => Promise<unknown>];
function FileActions({actions, error}: {actions: FileAction[]; error: (message: string) => void}) {
  const [open, setOpen] = useState(false), root = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {if (!root.current?.contains(event.target as Node)) setOpen(false);};
    const blur = () => setOpen(false);
    document.addEventListener('pointerdown', dismiss);window.addEventListener('blur', blur);
    root.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    return () => {document.removeEventListener('pointerdown', dismiss);window.removeEventListener('blur', blur);};
  }, [open]);
  return <div className="file-actions" ref={root} onBlur={event => {if (!event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);}} onKeyDown={event => {
    if(event.key === 'Escape'){event.preventDefault();setOpen(false);trigger.current?.focus();}
    if(open && ['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();const buttons=[...root.current!.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')],index=buttons.indexOf(document.activeElement as HTMLButtonElement);buttons[(index+(event.key==='ArrowDown'?1:buttons.length-1))%buttons.length]?.focus();}
  }}><button ref={trigger} className="icon-button" title="更多文件操作" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}><MoreHorizontal size={17}/></button>
    {open && <div className="file-actions-menu" role="menu">{actions.map(([label,Icon,action]) => <button role="menuitem" key={label} onClick={() => {setOpen(false);trigger.current?.focus();void action().catch(failure => error(failure.message));}}><Icon size={15}/>{label}</button>)}</div>}
  </div>;
}
