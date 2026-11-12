import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowLeft, ChevronRight, FileText, Folder, Globe, ListChecks, Maximize2, MessageSquare, Minimize2, Pin, PinOff, Plus, RefreshCw, Search, Terminal, X } from 'lucide-react';
import type { Conversation, FileSession } from './types';
import PluginSlot from './plugins/PluginSlot';
import {useHostField} from './host/context';
import BrowserWorkspace from './BrowserWorkspace';
import TerminalWorkspace from './TerminalWorkspace';
import TaskWorkspace, { type TaskView } from './TaskWorkspace';

const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
export type WorkbenchTab = { id: string; kind: string; title: string; cwd: string; url?: string; owner: string; control_revision: number; status: string };
export type WorkspaceView = { selected: string; newOpen: boolean; filesOpen: boolean; filesPinned: boolean; tasksOpen: boolean; tasksPinned: boolean; fileQuery: string; task: TaskView; width?: number };
export const emptyWorkspace = (): WorkspaceView => ({ selected: 'new', newOpen: true, filesOpen: false, filesPinned: false, tasksOpen: false, tasksPinned: false, fileQuery: '', task: { section: 'calls' } });
type Props = {
  conversation: Conversation; value: WorkspaceView; change: (value: Partial<WorkspaceView>) => void;
  expanded: boolean; expand: () => void; close: () => void; obscured: boolean; dark: boolean;
  editors: { file: FileSession; text: string; saved: string }[]; fileTree: any; fileContent: ReactNode;
  selectFile: (path: string) => Promise<void>; openFile: (path: string) => Promise<void>; closeFile: (path: string) => Promise<void>;
  refreshFiles: (id?: string, path?: string) => Promise<void>; createDocument: () => Promise<void>;
  error: (message: string) => void; confirm: (title: string, detail: string, choices: string[]) => Promise<number>;
};

export default function Workspace(props: Props) {
  const { conversation, value, change, expanded, editors, error } = props;
  const cid = conversation.id;
  const [tabs, setTabs] = useState<WorkbenchTab[]>([]), [profiles, setProfiles] = useState<{id: string; name: string}[]>([]);
  const [address, setAddress] = useHostField('workspace:'+cid+':address',''), [shell, setShell] = useHostField('workspace:'+cid+':shell',''), [busy, setBusy] = useState(false), [loadError, setLoadError] = useState('');
  const root = useRef<HTMLElement>(null), alive = useRef(true);
  const current = useRef(props); current.current = props;
  const refresh = useCallback(async () => {
    try { const result = await call('workbench.list', { conversation_id: cid }); if (alive.current) { setTabs(result.tabs); setLoadError(''); } }
    catch (failure) { if (alive.current) setLoadError((failure as Error).message); }
  }, [cid]);
  useEffect(() => {
    alive.current = true; void refresh();
    void call('workbench.profiles', { conversation_id: cid }).then(value => { if (alive.current) setProfiles(value); }).catch(failure => { if (alive.current) setLoadError(failure.message); });
    const unsubscribe = window.cowork.onEvents(events => { if (events.some(event => event.event === 'service_reconnected' || event.event === 'workbench_changed' && event.conversation_id === cid)) void refresh(); });
    return () => { alive.current = false; unsubscribe(); };
  }, [cid, refresh]);
  useEffect(() => {
    const dismiss = (event: PointerEvent | KeyboardEvent) => {
      const p = current.current;
      if (event instanceof KeyboardEvent && event.key !== 'Escape') return;
      if (event instanceof PointerEvent && (event.target as HTMLElement).closest('[data-workspace-flyout], [data-workspace-toggle]')) return;
      p.change({ ...(!p.value.filesPinned ? { filesOpen: false } : {}), tasksOpen: false });
    };
    document.addEventListener('pointerdown', dismiss); document.addEventListener('keydown', dismiss);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', dismiss); };
  }, []);
  const selectedTab = tabs.find(tab => 'workbench:' + tab.id === value.selected);
  const showTasks = value.selected === 'tasks' && value.tasksPinned;
  const floating = value.filesOpen && !value.filesPinned;
  const fileSelected = value.selected.startsWith('file:');
  async function openNative(kind: 'web' | 'terminal') {
    setBusy(true);
    try {
      const result = await call(kind === 'web' ? 'browser.open' : 'workbench.open', { conversation_id: cid, ...(kind === 'web' ? {url: address} : {shell}) });
      if (result.ok === false) throw new Error(result.error);
      if (!alive.current) return;
      await refresh(); change({ selected: 'workbench:' + result.tab.id, newOpen: false });
    } catch (failure) { error((failure as Error).message); } finally { if (alive.current) setBusy(false); }
  }
  async function closeNative(tab: WorkbenchTab) {
    if (tab.kind === 'terminal' && await props.confirm('关闭终端？', '此终端及其启动的进程将结束。', ['取消', '关闭终端']) !== 1) return;
    try {
      await call(tab.kind === 'web' ? 'browser.close' : 'workbench.close', { conversation_id: cid, tab_id: tab.id });
      if (!alive.current) return;
      await refresh(); if (value.selected === 'workbench:' + tab.id) change({ selected: 'new', newOpen: true });
    } catch (failure) { error((failure as Error).message); }
  }
  function tabButton(id: string, name: string, icon: ReactNode, select: () => void, close?: () => void) {
    return <div className={'workspace-tab ' + (value.selected === id ? 'active' : '')} key={id}>
      <button role="tab" aria-selected={value.selected === id} title={name} onClick={select}>{icon}<span>{name}</span></button>
      {close && <button className="tab-close" aria-label={'关闭 ' + name} onClick={close}><X size={13}/></button>}
    </div>;
  }
  const taskContent = <PluginSlot kind="panel" target="tasks" scope={{conversationId:cid}}><TaskWorkspace cid={cid} conversation={conversation} value={value.task} change={task => change({task: {...value.task, ...task}})} error={error}/></PluginSlot>;
  return <section className="workspace" ref={root} aria-label="工作区">
    <div className="workspace-bar">
      <button data-workspace-toggle className={'icon-button ' + (value.filesOpen ? 'selected' : '')} title="工作区文件" aria-label="工作区文件" aria-expanded={value.filesOpen} onClick={() => { change({filesOpen: !value.filesOpen, tasksOpen: false}); void props.refreshFiles(); }}><Folder size={17}/></button>
      <button data-workspace-toggle className={'icon-button ' + (showTasks ? 'selected' : '')} title="任务" aria-label="任务" aria-expanded={showTasks} onClick={() => change({selected: 'tasks', tasksPinned: true, tasksOpen: false, ...(!value.filesPinned ? {filesOpen: false} : {})})}><ListChecks size={17}/></button>
      <div className="workspace-tabs" role="tablist" aria-label="工作区标签">
        {expanded && tabButton('conversation', '对话', <MessageSquare size={14}/>, () => change({selected: 'conversation'}))}
        {value.tasksPinned && tabButton('tasks', '任务', <ListChecks size={14}/>, () => change({selected: 'tasks'}), () => change({tasksPinned: false, ...(showTasks ? {selected: 'new', newOpen: true} : {})}))}
        {editors.map(editor => tabButton('file:' + editor.file.path, editor.file.name + (editor.text !== editor.saved ? ' •' : ''), <FileText size={14}/>, () => void props.selectFile(editor.file.path), () => void props.closeFile(editor.file.path)))}
        {tabs.map(tab => tabButton('workbench:' + tab.id, tab.title, tab.kind === 'web' ? <Globe size={14}/> : <Terminal size={14}/>, () => { change({selected: 'workbench:' + tab.id}); void call('workbench.activate', {conversation_id: cid, tab_id: tab.id}).catch(failure => error(failure.message)); }, () => void closeNative(tab)))}
        {value.newOpen && tabButton('new', '新标签页', <Plus size={14}/>, () => change({selected: 'new'}), () => change({newOpen: false, selected: editors[0] ? 'file:' + editors[0].file.path : tabs[0] ? 'workbench:' + tabs[0].id : value.tasksPinned ? 'tasks' : 'empty'}))}
      </div>
      <button className="icon-button" title="新标签页" onClick={() => change({selected: 'new', newOpen: true})}><Plus size={17}/></button>
      <button className="icon-button" title={expanded ? '恢复分栏' : '展开工作区'} onClick={props.expand}>{expanded ? <Minimize2 size={16}/> : <Maximize2 size={16}/>}</button>
      <button className="icon-button" title="收起工作区" onClick={props.close}><X size={16}/></button>
    </div>
    <div className={'workspace-body ' + (value.filesOpen && value.filesPinned ? 'files-pinned' : '')}>
      {value.filesOpen && <PluginSlot kind="panel" target="files" scope={{conversationId:cid}}><aside data-workspace-flyout className={'file-navigator ' + (value.filesPinned ? 'pinned' : 'floating')} aria-label="工作区文件导航">
        <div className="navigator-heading"><strong>工作区文件</strong><small>{props.fileTree.files.length} 项</small><button className="icon-button" title={value.filesPinned ? '取消固定文件导航' : '固定文件导航'} onClick={() => change({filesPinned: !value.filesPinned})}>{value.filesPinned ? <PinOff size={14}/> : <Pin size={14}/>}</button></div>
        <label className="navigator-search"><Search size={14}/><input aria-label="搜索当前目录" placeholder="搜索当前目录" value={value.fileQuery} onChange={event => change({fileQuery: event.target.value})}/></label>
        <div className="file-location"><button className="icon-button" title="返回工作目录" onClick={() => void props.refreshFiles()}><ArrowLeft size={14}/></button><span title={props.fileTree.folder}>{props.fileTree.folder?.split(/[\\/]/).pop()}</span><button className="icon-button" title="刷新文件" onClick={() => void props.refreshFiles(cid, props.fileTree.folder)}><RefreshCw size={14}/></button></div>
        {props.fileTree.error && <div className="inline-error" role="alert"><p>{props.fileTree.error}</p><button onClick={() => void props.refreshFiles(cid)}>重试</button><button disabled={['running','queued','stopping'].includes(conversation.run?.status || '')} onClick={async () => { try {const path = await call('dialog.workspace'); if (!path) return; await call('conversation.workspace', {conversation_id: cid, path}); await props.refreshFiles(cid);} catch (failure) {error((failure as Error).message);} }}>重新选择目录</button></div>}
        <div className="navigator-files">{props.fileTree.files.filter((file: any) => file.name.toLocaleLowerCase().includes(value.fileQuery.toLocaleLowerCase())).map((file: any) => <button className="file-row" key={file.path} title={file.path} onClick={() => { if (file.directory) { change({fileQuery: ''}); void props.refreshFiles(cid, file.path); } else void props.openFile(file.path); }}>{file.directory ? <Folder size={15}/> : <FileText size={15}/>}<span>{file.name}</span>{file.directory && <ChevronRight size={13}/>}</button>)}</div>
        {!props.fileTree.files.length && <div className="empty-workspace"><Folder size={28}/><h3>还没有文件</h3><p>在对话中创建文件后，它们会显示在这里。</p></div>}
      </aside></PluginSlot>}
      <div className="workspace-content" hidden={expanded && value.selected === 'conversation'}>
        {loadError && <div className="inline-error" role="alert">{loadError}<button onClick={() => void refresh()}>重试</button></div>}
        {showTasks ? taskContent : fileSelected ? props.fileContent : selectedTab?.kind === 'terminal' ? <PluginSlot kind="panel" target="terminal" scope={{conversationId:cid,tabId:selectedTab.id}}><TerminalWorkspace key={selectedTab.id} cid={cid} tab={selectedTab} dark={props.dark} error={error} refresh={refresh}/></PluginSlot> : selectedTab?.kind === 'web' ? <PluginSlot kind="panel" target="browser" scope={{conversationId:cid,tabId:selectedTab.id}}><BrowserWorkspace key={selectedTab.id} cid={cid} tab={selectedTab} obscured={props.obscured || floating} error={error} refresh={refresh}/></PluginSlot> : <div className="workspace-start">
          <Globe size={28}/><form className="new-page-address" onSubmit={event => {event.preventDefault(); void openNative('web');}}><input aria-label="新网页地址" placeholder="输入网址" value={address} onChange={event => setAddress(event.target.value)}/><button className="icon-button" title="打开网页" disabled={!address.trim() || busy}><ChevronRight size={17}/></button></form>
          <div className="workspace-start-actions"><button className="secondary" onClick={() => void props.createDocument()}><FileText size={16}/>新增页面</button><button className="secondary" disabled={busy} onClick={() => void openNative('terminal')}><Terminal size={16}/>终端</button></div>
          <select aria-label="新终端类型" value={shell} onChange={event => setShell(event.target.value)}><option value="">默认终端</option>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select>
        </div>}
      </div>
    </div>
  </section>;
}
