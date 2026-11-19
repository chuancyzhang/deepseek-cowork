import {useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject} from 'react';
import {createPortal} from 'react-dom';
import {ArrowUpRight, Bell, ChevronRight, CircleHelp, Folder, FolderPlus, LoaderCircle, MessageSquare, PanelLeft, Search, SquarePen, X} from 'lucide-react';
import Dialog from './Dialog';
import {useWorkbench} from './host/Host';
import {useHostField} from './host/context';
import {receiveRun} from './host/conversations';
import {emptyWorkspace, type WorkspaceView} from './Workspace';
import {elapsedTime, formatDuration, isActive, mergeEvents, unwrapEvents} from './runtime';
import {chatWorkspace, pathKey, projectPaths} from './projects';
import type {Conversation, ProjectRecord, Run, RuntimeEvent} from './types';
import './navigation.css';

const call = <T = any,>(method: string, params?: unknown) => window.cowork.call<T>(method, params);
const menuNames = [['file','文件'],['edit','编辑'],['view','视图'],['help','帮助']] as const;
const shortcuts = [['新建聊天','Ctrl+N'],['新建项目','Ctrl+O'],['搜索聊天与项目','Ctrl+K'],['显示 / 隐藏侧边栏','Ctrl+Shift+S'],['设置','Ctrl+,'],['保存当前文稿','Ctrl+S']];

export default function AppNavigation() {
  const c = useWorkbench();
  const [runs] = useHostField<Record<string,Run>>('runs',{});
  const [sidebarTarget] = useHostField<HTMLDivElement | null>('navigation:sidebarTools',null);
  const [panel,setPanel] = useState<''|'search'|'tasks'|'shortcuts'>('');
  const [menu,setMenu] = useState('');
  const searchTrigger = useRef<HTMLButtonElement>(null), tasksTrigger = useRef<HTMLButtonElement>(null);
  const toolsTarget = c.sidebar ? sidebarTarget : null;
  const activeRuns = useMemo(() => Object.values(runs).filter(run => isActive(run.status)).sort((a,b) => (a.created_at || 0)-(b.created_at || 0)),[runs]);
  const close = () => setPanel('');
  useEffect(() => {c.host.set('navigation:overlay',Boolean(panel || menu));return () => {c.host.set('navigation:overlay',false);};},[c.host,panel,menu]);
  useEffect(() => {void call('app.menu_state',{canExport:Boolean(c.current?.has_messages || c.current?.messages?.length)}).catch(reason=>c.error(reason.message));},[c.current?.id,c.current?.has_messages,c.current?.messages?.length]);

  async function settings(tab?:string) {
    if (c.route === 'settings' && tab && !await c.leaveManagement.current()) return;
    await c.goRoute('settings');
    if (tab && c.host.get('navigation:route') === 'settings') c.host.set('management:tab:settings',tab);
  }
  async function command(action:string) {
    if (document.querySelector('dialog[open]') && !panel) return;
    if (['search','tasks','shortcuts'].includes(action)) {setPanel(previous=>previous===action?'':action as typeof panel);return;}
    close();
    if (action === 'sidebar') c.setSidebar(previous=>!previous);
    else if (action === 'new-chat') await c.newConversation();
    else if (action === 'add-project') c.addProject();
    else if (action === 'appearance') {
      const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      await call('settings.theme', {theme});
      c.setSettings(previous => previous ? {...previous, theme} : null);
    }
    else if (action === 'settings' || action === 'updates') await settings(action === 'updates' ? 'update' : undefined);
    else if (action === 'workspace') {
      await c.goRoute('chat');
      if (c.host.get('navigation:route') !== 'chat') return;
      const id = await c.ensureConversation();
      if (c.selectedId.current !== id) return;
      c.setWorkspaceOpen(previous=>!previous);void c.refreshFiles(id);
    } else if (action === 'export-chat' && c.selectedId.current) {
      const id = c.selectedId.current;
      await c.saveDraft(id);await c.saveView(id);
      const result = await call('app.export_conversation',{conversation_id:id});
      if (result.saved) c.error('聊天记录已导出。');
    }
  }
  const handler = useRef(command);handler.current=command;
  useEffect(() => window.cowork.onEvents(batch => {
    for (const event of batch) {
      if (event.event === 'navigation_command') void handler.current(event.data.command).catch(reason=>c.error(reason.message));
      if (event.event === 'navigation_error') c.error(event.error || '菜单操作未完成。');
    }
  }),[c.error]);
  async function openMenu(name:string, button:HTMLButtonElement) {
    close();setMenu(name);
    const rect = button.getBoundingClientRect();
    try {await call('app.menu',{menu:name,x:rect.left,y:rect.bottom+4});}
    catch(reason){c.error((reason as Error).message);}
    finally {setMenu('');}
  }
  async function openRun(run:Run, details=false) {
    close();await c.switchConversation(run.conversation_id);
    const id = run.conversation_id;
    if (!details || c.selectedId.current !== id || c.host.get<any>(c.host.sessionKey('current',id))?.id !== id) return;
    c.host.set<WorkspaceView>(c.host.sessionKey('workspaceView',id),previous=>({...previous || emptyWorkspace(),selected:'tasks',tasksPinned:true,tasksOpen:false,task:{section:'calls',runId:run.id}}));
    c.host.set(c.host.sessionKey('workspaceOpen',id),true);
  }
  const tools = <div className="navigation-tools">
    <button ref={tasksTrigger} className={'navigation-tasks icon-button'+(panel==='tasks'?' selected':'')} title="运行任务" aria-label={'运行任务'+(activeRuns.length?'，'+activeRuns.length+' 项进行中':'')} aria-haspopup="dialog" aria-expanded={panel==='tasks'} onClick={()=>setPanel(panel==='tasks'?'':'tasks')}>
      <Bell size={17}/>{activeRuns.length>0&&<span className="navigation-count" aria-hidden="true">{activeRuns.length > 99 ? '99+' : activeRuns.length}</span>}
    </button>
    <button ref={searchTrigger} className="icon-button" title="搜索聊天与项目 · Ctrl+K" aria-label="搜索聊天与项目" aria-haspopup="dialog" aria-expanded={panel==='search'} onClick={()=>setPanel('search')}><Search size={17}/></button>
  </div>;
  return <>
    <header className={'window-titlebar app-navigation'+(/Windows/i.test(navigator.userAgent)?' windows':'')} aria-label="应用导航">
      <button className="icon-button navigation-sidebar" title="显示 / 隐藏侧边栏 · Ctrl+Shift+S" aria-label={c.sidebar?'收起侧边栏':'展开侧边栏'} aria-expanded={c.sidebar} onClick={()=>c.setSidebar(previous=>!previous)}><PanelLeft size={18}/></button>
      <span className="navigation-divider" aria-hidden="true"/>
      <nav className="application-menus" aria-label="应用菜单">{menuNames.map(([id,label])=><button key={id} aria-haspopup="menu" aria-expanded={menu===id} onMouseDown={event=>event.preventDefault()} onClick={event=>void openMenu(id,event.currentTarget)} onKeyDown={event=>{if(event.key==='ArrowDown'){event.preventDefault();void openMenu(id,event.currentTarget);}}}>{label}</button>)}</nav>
      <div className="navigation-space"/>
      {!toolsTarget && tools}
    </header>
    {toolsTarget && createPortal(tools, toolsTarget)}
    {panel==='search'&&<GlobalSearch conversations={c.conversations} projects={c.settings?.projects || []} close={close} onChat={async id=>{close();await c.switchConversation(id);}} onProject={async path=>{close();await c.newConversation(path);}} onAction={command} error={c.error}/>}
    {panel==='tasks'&&<RunningTasks runs={activeRuns} trigger={tasksTrigger} placement={toolsTarget} close={close} open={openRun} background={async()=>{close();await settings('jobs');}}/>}
    {panel==='shortcuts'&&<Dialog title="键盘快捷键" onClose={close} className="navigation-shortcuts"><dl>{shortcuts.map(([label,key])=><div key={key}><dt>{label}</dt><dd><kbd>{key}</kbd></dd></div>)}</dl></Dialog>}
  </>;
}

type SearchResult = {key:string;kind:string;title:string;detail?:string;shortcut?:string;run:()=>Promise<void>};
export function GlobalSearch({conversations,projects,close,onChat,onProject,onAction,error}:{conversations:Conversation[];projects:ProjectRecord[];close:()=>void;onChat:(id:string)=>Promise<void>;onProject:(path:string)=>Promise<void>;onAction:(action:string)=>Promise<void>;error:(message:string)=>void}) {
  const [query,setQuery] = useState(''), [filter,setFilter] = useState('all'), [selected,setSelected] = useState(0), [revision,setRevision]=useState(0);
  const [limit,setLimit] = useState(40);
  const [response,setResponse] = useState<{query:string;items:Conversation[];error?:string}|null>(null);
  const input=useRef<HTMLInputElement>(null),results=useRef<HTMLDivElement>(null);
  const term=query.trim(),chatSearch=Boolean(term)&&(filter==='all'||filter==='chat');
  useEffect(()=>{input.current?.focus();},[]);
  useEffect(()=>{
    if(!chatSearch)return;
    let valid=true;
    const timer=setTimeout(()=>{void call<Conversation[]>('conversation.search',{query:term,limit:200}).then(items=>{if(valid)setResponse({query:term,items});}).catch(reason=>{if(valid)setResponse({query:term,items:[],error:reason.message});});},200);
    return()=>{valid=false;clearTimeout(timer);};
  },[term,chatSearch,revision]);
  const loading=chatSearch&&response?.query!==term, failure=chatSearch&&response?.query===term?response.error:'';
  const match=(value:string)=>value.toLocaleLowerCase().includes(term.toLocaleLowerCase());
  const archivedPaths = useMemo(() => new Set(projects.filter(project => project.archived || project.hidden).flatMap(projectPaths).map(pathKey)), [projects]);
  const archived=(item:Conversation)=>item.meta.archived||archivedPaths.has(chatWorkspace(item));
  const rows:SearchResult[]=[];
  if(filter==='all'||filter==='action')for(const [action,title,key] of [['new-chat','新建聊天','Ctrl+N'],['add-project','新建项目…','Ctrl+O']])if(match(title))rows.push({key:action,kind:'操作',title,shortcut:key,run:()=>onAction(action)});
  if(filter==='all'||filter==='chat')for(const item of term?(response?.query===term?response.items:[]):conversations.filter(item=>!archived(item)&&item.has_content!==false).slice(0,5))rows.push({key:item.id,kind:'聊天',title:item.display_title||item.title,detail:[item.meta.project_name|| (item.meta.workspace_source!=='chat'?(item.meta.workspace_dir||'').split(/[\\/]/).pop():''),archived(item)?'已归档':''].filter(Boolean).join(' · '),run:()=>onChat(item.id)});
  if(filter==='all'||filter==='project')for(const project of projects.filter(p=>!p.archived&&!p.hidden&&match(p.name+' '+p.path)))rows.push({key:project.path,kind:'项目',title:project.name,detail:project.path,shortcut:'新建聊天',run:()=>onProject(project.path)});
  const visible=rows.slice(0,limit),active=Math.min(selected,Math.max(0,visible.length-1));
  useEffect(()=>setSelected(0),[term,filter,response]);
  useEffect(()=>setLimit(40),[term,filter]);
  useEffect(()=>{results.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({block:'nearest'});},[active]);
  async function choose(row:SearchResult){try{await row.run();}catch(reason){error((reason as Error).message);}}
  return <Dialog title="搜索聊天与项目" onClose={close} className="global-search">
    <div className="global-search-input"><Search size={20}/><input ref={input} autoFocus aria-label="搜索聊天、内容和项目" role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls="global-search-results" aria-activedescendant={rows[active]?'search-result-'+active:undefined} placeholder="搜索聊天、内容和项目…" value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{
      if(event.nativeEvent.isComposing)return;
      if(['ArrowDown','ArrowUp'].includes(event.key)&&visible.length){event.preventDefault();setSelected((active+(event.key==='ArrowDown'?1:visible.length-1))%visible.length);}
      if(event.key==='Enter'&&rows[active]){event.preventDefault();void choose(rows[active]);}
    }}/></div>
    <div className="global-search-filters" aria-label="搜索范围">{[['all','全部'],['chat','聊天'],['project','项目'],['action','操作']].map(([id,label])=><button key={id} aria-pressed={filter===id} onClick={()=>{setFilter(id);input.current?.focus();}}>{label}</button>)}</div>
    <div className="global-search-results" ref={results} id="global-search-results" role="listbox" aria-label="搜索结果" aria-busy={loading}>
      {visible.map((row,index)=><div key={row.kind+row.key}>{index===0||rows[index-1].kind!==row.kind?<div className="global-search-group">{!term&&row.kind==='聊天'?'最近聊天':row.kind==='操作'?'快捷操作':row.kind}</div>:null}<button id={'search-result-'+index} role="option" aria-selected={index===active} className="global-search-result" title={row.detail?row.title+'\n'+row.detail:row.title} onFocus={()=>setSelected(index)} onClick={()=>void choose(row)}>{row.kind==='项目'?<Folder size={18}/>:row.kind==='聊天'?<MessageSquare size={18}/>:row.key==='new-chat'?<SquarePen size={18}/>:<FolderPlus size={18}/>}<span><strong>{row.title}</strong>{row.detail&&<small>{row.detail}</small>}</span>{row.shortcut&&<kbd>{row.shortcut}</kbd>}</button></div>)}
      {rows.length>limit&&<button className="sidebar-more" onClick={()=>setLimit(value=>value+40)}>显示更多结果</button>}
      {chatSearch&&response?.query===term&&response.items.length>=200&&<p className="navigation-search-limit">聊天结果较多，请输入更具体的关键词。</p>}
      {loading&&<p className="navigation-empty" role="status"><LoaderCircle size={16} className="spin"/>正在搜索聊天…</p>}
      {failure&&<div className="navigation-search-error" role="alert"><p>聊天搜索未完成：{failure}</p><button className="secondary" onClick={()=>{setResponse(null);setRevision(value=>value+1);}}>重试</button></div>}
      {!rows.length&&!loading&&!failure&&<p className="navigation-empty">{term?'没有找到匹配内容，试试其他关键词。':'还没有聊天或项目。'}</p>}
    </div>
    <footer className="global-search-footer"><span>↑ ↓ 选择　Enter 打开</span><span>Esc 关闭</span></footer>
  </Dialog>;
}

function RunningTasks({runs,trigger,placement,close,open,background}:{runs:Run[];trigger:RefObject<HTMLButtonElement|null>;placement:HTMLDivElement|null;close:()=>void;open:(run:Run,details?:boolean)=>Promise<void>;background:()=>Promise<void>}) {
  const root=useRef<HTMLElement>(null);
  const position = useCallback(() => {
    const box = trigger.current?.getBoundingClientRect(), element = root.current;
    if (!box || !element) return;
    element.style.top = (box.bottom + 8) + 'px';
    element.style.left = Math.max(12, Math.min(box.right - element.offsetWidth, innerWidth - element.offsetWidth - 12)) + 'px';
    element.style.maxHeight = Math.max(0, innerHeight - box.bottom - 20) + 'px';
  }, [trigger]);
  useLayoutEffect(position, [position, placement, runs.length]);
  useEffect(()=>{
    root.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const dismiss=(event:PointerEvent)=>{if(!root.current?.contains(event.target as Node)&&!trigger.current?.contains(event.target as Node))close();};
    const key=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();close();trigger.current?.focus();}};
    const blur=()=>close();
    document.addEventListener('pointerdown',dismiss);document.addEventListener('keydown',key);window.addEventListener('resize',position);window.addEventListener('blur',blur);
    return()=>{document.removeEventListener('pointerdown',dismiss);document.removeEventListener('keydown',key);window.removeEventListener('resize',position);window.removeEventListener('blur',blur);};
  },[position]);
  return <section className="running-tasks" ref={root} role="dialog" aria-label="运行任务">
    <header><h2>运行任务</h2><span>{runs.length?runs.length+' 项进行中':''}</span><button className="icon-button" aria-label="关闭运行任务" onClick={()=>{close();trigger.current?.focus();}}><X size={17}/></button></header>
    <div className="running-task-list">{runs.length?runs.map(run=><RunningTask key={run.id} run={run} open={open}/>):<p className="navigation-empty">当前没有正在运行的任务</p>}</div>
    <footer><button onClick={()=>void background()}>后台操作<ChevronRight size={15}/></button></footer>
  </section>;
}

function RunningTask({run,open}:{run:Run;open:(run:Run,details?:boolean)=>Promise<void>}) {
  const c=useWorkbench(),cid=run.conversation_id;
  const [liveEvents]=useHostField<RuntimeEvent[]>(c.host.sessionKey('events',cid),[]);
  const [snapshot,setSnapshot]=useState<Conversation|null>(null),[failure,setFailure]=useState(''),[readFailed,setReadFailed]=useState(false),[revision,setRevision]=useState(0),[busy,setBusy]=useState(false),[now,setNow]=useState(Date.now()/1000);
  const item=c.conversations.find(item=>item.id===cid)||snapshot;
  useEffect(()=>{
    let valid=true;setReadFailed(false);setFailure('');
    // Read the initial pending question too; subsequent changes stay host-owned.
    void call<Conversation>('conversation.get',{conversation_id:cid}).then(value=>{
      if(!valid)return;setSnapshot(value);receiveRun(c.host,value.run||null,cid);
    }).catch(reason=>{if(valid){setReadFailed(true);setFailure('任务信息暂时无法读取：'+reason.message);}});
    const timer=setInterval(()=>setNow(Date.now()/1000),1000);
    return()=>{valid=false;clearInterval(timer);};
  },[cid,run.id,c.host,revision]);
  // Snapshot metadata never writes interaction state. Durable live events win,
  // including a finished receipt arriving before the initial read completes.
  const events=mergeEvents(unwrapEvents(snapshot?.events||[]),liveEvents).filter(event=>event.run_id===run.id);
  const pending=[...events].reverse().find(event=>event.type==='interaction_request');
  const waiting=Boolean(pending&&!events.some(event=>event.type==='interaction_finished'&&event.data?.request_id===pending.data?.request_id));
  const duration=elapsedTime(run.created_at,now);
  const status=run.status==='stopping'?'正在停止':waiting?'等待你的回答':run.status==='queued'?'等待开始':'正在运行';
  async function stop(){setBusy(true);setFailure('');try{await c.stopRun(cid,run.id);}catch(reason){setFailure('停止未完成：'+(reason as Error).message);}finally{setBusy(false);}}
  return <article className="running-task">
    <strong title={item?.display_title||item?.title}>{item?.display_title||item?.title||(readFailed?'未能读取聊天名称':'正在读取聊天…')}</strong>
    <div className="running-task-meta">{item?.meta.project_name||(item?.meta.workspace_source!=='chat'?(item?.meta.workspace_dir||'').split(/[\\/]/).pop():'')||'独立聊天'}{duration!=null&&<> · {formatDuration(duration)}</>}</div>
    <div className="running-task-status">{waiting?<CircleHelp size={15}/>:<LoaderCircle size={15} className="spin"/>}{status}</div>
    {failure&&<p role="alert" className="error-text">{failure}{readFailed&&<button onClick={()=>setRevision(value=>value+1)}>重试</button>}</p>}
    <div className="running-task-actions"><button onClick={()=>void open(run)}>{waiting?'前往回答':'查看对话'}<ArrowUpRight size={14}/></button><button onClick={()=>void open(run,true)}>任务详情</button><button className="running-task-stop" disabled={busy||run.status==='stopping'} onClick={()=>void stop()}>{busy||run.status==='stopping'?'正在停止…':'停止'}</button></div>
  </article>;
}
