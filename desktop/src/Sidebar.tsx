import {useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type Ref} from 'react';
import {Archive, BookOpen, Check, ChevronDown, ChevronRight, Folder, LoaderCircle, MessageSquare, MoreHorizontal, Pencil, Pin, PinOff, Plus, Settings, Sparkles, SquarePen, Star, Trash2, Undo2, Users, X} from 'lucide-react';
import type {Conversation, SidebarEntry, SidebarLayout, SidebarMethod} from './types';
import {useHost, useHostField} from './host/context';
import {UpdateNotice} from './management/Updates';
import SidebarMenu, {type MenuAnchor} from './SidebarMenu';
import {useSidebarDrag} from './sidebarDrag';
import './sidebar.css';

type ProjectRecord = {path: string; name: string; pinned?: boolean; updated_at?: number; archived?: boolean; hidden?: boolean};
type Project = ProjectRecord & {sessions: Conversation[]; groupId?: string; pinnedAt: number};
type Item = {kind: 'chat'; chat: Conversation} | {kind: 'project'; project: Project};
type Props = {
  conversations: Conversation[];
  supplemental: Conversation[];
  projects: ProjectRecord[];
  layout: SidebarLayout | null;
  selected: string;
  route: string;
  running: Set<string>;
  toolsRef: Ref<HTMLDivElement>;
  onSelect: (id: string) => void;
  onNew: (workspace?: string) => void;
  onRoute: (route: string) => void;
  onProject: () => void;
  onAction: (action: string, conversation: Conversation) => void;
  onMore: () => Promise<void>;
  hasMore: boolean;
  onProjectAction: (action: string, project: ProjectRecord) => void;
  onLayoutAction: (method: SidebarMethod, params: unknown) => Promise<SidebarLayout>;
  onError: (message: string) => void;
};

const pathKey = (value: string) => /^[a-z]:|\\/i.test(value)
  ? value.replace(/\\/g, '/').toLowerCase().replace(/\/+$/, '') : value.replace(/\/+$/, '');
const archived = (project?: ProjectRecord) => Boolean(project?.archived || project?.hidden);
const chatLabel = (chat: Conversation) => chat.display_title || chat.title || '新对话';
const projectLabel = (path: string) => path.split(/[\\/]/).filter(Boolean).at(-1) || path;
const chatProject = (chat: Conversation) => chat.meta.workspace_source === 'chat' || chat.meta.team_space_required
  ? '' : pathKey(chat.meta.workspace_dir || chat.meta.workspace_path || '');
const entryOf = (item: Item): SidebarEntry => item.kind === 'project'
  ? {kind: 'project', id: item.project.path} : {kind: 'chat', id: item.chat.id};
const entryKey = (entry: SidebarEntry) => entry.kind === 'project' ? 'project:' + pathKey(entry.id) : 'chat:' + entry.id;
const destinations = [['capabilities', '能力', Sparkles], ['library', '资料库', BookOpen], ['favorites', '常用任务', Star], ['team', '团队空间', Users]] as const;

function organize(conversations: Conversation[], supplemental: Conversation[], configured: ProjectRecord[], layout: SidebarLayout | null) {
  const byId = new Map(conversations.map(chat => [chat.id, chat]));
  for (const chat of supplemental) byId.set(chat.id, chat);
  const chats = [...byId.values()].sort((a, b) => b.updated_at - a.updated_at);
  const projects = new Map<string, Project>();
  const placement = new Map(Object.values(layout?.projects || {}).map(item => [pathKey(item.path), item]));
  const add = (project: ProjectRecord) => {
    const key = pathKey(project.path);
    const saved = placement.get(key);
    const pinnedAt = saved?.pinned_at ?? (project.pinned ? project.updated_at || 1 : 0);
    const row = {...project, groupId: saved?.group_id, pinnedAt, sessions: [] as Conversation[]};
    projects.set(key, row);
    return row;
  };
  for (const project of configured) add(project);
  for (const chat of chats) {
    const key = chatProject(chat);
    if (!key || chat.has_content === false) continue;
    let project = projects.get(key);
    if (!project && !chat.meta.archived) {
      const path = chat.meta.workspace_dir || chat.meta.workspace_path;
      project = add({path, name: projectLabel(path)});
    }
    project?.sessions.push(chat);
  }
  for (const [key, saved] of placement) {
    if (!projects.has(key) && (saved.group_id || saved.pinned_at)) add({path: saved.path, name: projectLabel(saved.path)});
  }
  const visible = chats.filter(chat => !chat.meta.archived && chat.has_content !== false && !archived(projects.get(chatProject(chat))));
  const archiveChats = chats.filter(chat => chat.meta.archived && !archived(projects.get(chatProject(chat))));
  const groupById = new Map((layout?.groups || []).map(group => [group.id, {projects: [] as Project[], chats: [] as Conversation[]}]));
  const availableProjects = [...projects.values()].filter(project => !archived(project)).sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  const ungroupedProjects: Project[] = [], recent: Conversation[] = [];
  const pins: {item: Item; time: number}[] = [];
  for (const project of availableProjects) {
    if (project.pinnedAt) pins.push({item: {kind: 'project', project}, time: project.pinnedAt});
    else (groupById.get(project.groupId || '')?.projects || ungroupedProjects).push(project);
  }
  for (const chat of visible) {
    if (chat.meta.pinned) pins.push({item: {kind: 'chat', chat}, time: chat.updated_at});
    else {
      const group = groupById.get(layout?.chats[chat.id] || '');
      if (group) group.chats.push(chat);
      else if (!projects.has(chatProject(chat))) recent.push(chat);
    }
  }
  return {byId, projects, visible, archiveChats, groupById, ungroupedProjects, recent,
    archiveProjects: [...projects.values()].filter(archived),
    pinned: pins.sort((a, b) => b.time - a.time).map(row => row.item)};
}

export default function Sidebar(props: Props) {
  const host = useHost();
  const [archive, setArchive] = useState(false);
  const [expanded, setExpanded] = useHostField<Record<string, boolean>>('sidebar:expanded', {});
  const [limit, setLimit] = useState(40);
  const [projectLimit, setProjectLimit] = useState(5);
  const [menu, setMenu] = useState<{item: Item; anchor: MenuAnchor} | null>(null);
  const [draft, setDraft] = useState<{id: string; name: string; entry?: SidebarEntry; error?: string} | null>(null);
  const [saving, setSaving] = useState(false);
  const groupInput = useRef<HTMLInputElement>(null);
  const groupAdd = useRef<HTMLButtonElement>(null);
  const changing = useRef(false);
  const model = useMemo(() => organize(props.conversations, props.supplemental, props.projects, props.layout),
    [props.conversations, props.supplemental, props.projects, props.layout]);
  const selected = model.byId.get(props.selected);
  const selectedGroup = props.layout?.chats[props.selected] || (selected && model.projects.get(chatProject(selected))?.groupId);
  const groups = props.layout?.groups || [];
  const drag = useSidebarDrag((entry, group) => void move(entry, group), () => setMenu(null));

  useLayoutEffect(() => {
    host.set('sidebar:overlay', Boolean(menu || drag.dragging));
    return () => {host.set('sidebar:overlay', false);};
  }, [host, Boolean(menu || drag.dragging)]);
  useEffect(() => {if (draft) groupInput.current?.focus();}, [draft?.id]);

  function setOpen(key: string, value: boolean) {
    setExpanded(previous => ({...previous, [key]: value}));
  }
  function focusGroup(id: string) {
    requestAnimationFrame(() => drag.list.current?.querySelector<HTMLButtonElement>('[data-sidebar-group="' + id + '"] > button')?.focus());
  }
  function currentGroup(entry: SidebarEntry) {
    return entry.kind === 'chat' ? props.layout?.chats[entry.id] : model.projects.get(pathKey(entry.id))?.groupId;
  }
  async function move(entry: SidebarEntry, group: string | null, focus = false) {
    if (changing.current || !props.layout) return;
    if ((currentGroup(entry) || null) === group) {
      if (focus && group) focusGroup(group);
      return;
    }
    changing.current = true;
    try {
      await props.onLayoutAction('sidebar.move', {entry, group_id: group});
      if (group) {
        setOpen('group:' + group, true);
        if (focus) focusGroup(group);
      }
    } catch (failure) {
      props.onError('移动未完成：' + (failure as Error).message);
    } finally {changing.current = false;}
  }
  function beginGroup(entry?: SidebarEntry) {
    if (saving || !props.layout) return;
    setMenu(null);
    setDraft({id: crypto.randomUUID().replaceAll('-', ''), name: '', entry});
  }
  function cancelGroup() {
    if (saving) return;
    setDraft(null);
    groupAdd.current?.focus();
  }
  async function saveGroup() {
    if (!draft?.name.trim() || changing.current) return;
    changing.current = true;
    setSaving(true);
    try {
      await props.onLayoutAction('sidebar.create_group', {group_id: draft.id, name: draft.name, entry: draft.entry});
      setOpen('group:' + draft.id, true);
      setDraft(null);
      focusGroup(draft.id);
    } catch (failure) {
      setDraft(previous => previous && {...previous, error: (failure as Error).message});
      requestAnimationFrame(() => groupInput.current?.focus());
    } finally {changing.current = false; setSaving(false);}
  }
  function selectMenu(event: MouseEvent, item: Item, context = false) {
    event.preventDefault();
    const trigger = context ? event.currentTarget.querySelector<HTMLButtonElement>('[data-sidebar-more]') : event.currentTarget as HTMLButtonElement;
    if (!context && menu?.anchor.trigger === trigger) {setMenu(null); return;}
    const rect = trigger?.getBoundingClientRect() || event.currentTarget.getBoundingClientRect();
    const point = context && (event.clientX || event.clientY);
    setMenu({item, anchor: {trigger, x: point ? event.clientX : rect.left, below: point ? event.clientY : rect.bottom + 4, above: point ? event.clientY : rect.top - 4}});
  }
  function closeAction(run: () => void) {
    const trigger = menu?.anchor.trigger;
    setMenu(null);
    trigger?.focus({preventScroll: true});
    run();
  }
  async function pinProject(project: Project) {
    if (changing.current) return;
    changing.current = true;
    try {await props.onLayoutAction('sidebar.pin_project', {path: project.path, pinned: !project.pinnedAt});}
    catch (failure) {props.onError('项目置顶未更新：' + (failure as Error).message);}
    finally {changing.current = false;}
  }
  function more(item: Item, label: string) {
    return <button className="row-more icon-button" data-sidebar-more title={label} aria-label={label} aria-haspopup="menu"
      aria-expanded={menu ? entryKey(entryOf(menu.item)) === entryKey(entryOf(item)) : false}
      onClick={event => selectMenu(event, item)}><MoreHorizontal size={15}/></button>;
  }
  function chatRow(chat: Conversation) {
    const item = {kind: 'chat', chat} as const, entry = entryOf(item), label = chatLabel(chat);
    const group = groups.find(group => group.id === props.layout?.chats[chat.id]);
    const selected = chat.id === props.selected && props.route === 'chat';
    return <div key={chat.id} className={'history-row' + (selected ? ' selected' : '')}
      data-drag-source={drag.dragging && entryKey(drag.dragging.entry) === entryKey(entry) ? '' : undefined}
      onContextMenu={event => selectMenu(event, item, true)}>
      <button className="history-select" title={group ? label + '\n' + group.name : label} aria-current={selected || undefined}
        onPointerDown={event => {if (groups.length && !archive) drag.press(event, {entry, label});}}
        onClick={() => props.onSelect(chat.id)}>
        {props.running.has(chat.id) ? <LoaderCircle size={14} className="spin" aria-label="正在运行"/> : chat.meta.pinned && !archive ? <Pin size={14}/> : <MessageSquare size={14}/>}
        <span>{label}</span>
      </button>
      {more(item, label + ' 聊天操作')}
    </div>;
  }
  function projectRow(project: Project) {
    const item = {kind: 'project', project} as const, entry = entryOf(item), key = entryKey(entry);
    const chats = archive ? project.sessions : project.sessions.filter(chat => !chat.meta.archived && !chat.meta.pinned && !props.layout?.chats[chat.id]);
    const open = expanded[key] ?? chats.some(chat => chat.id === props.selected);
    return <section className="project-group" key={key}>
      <div className="project-heading" onContextMenu={event => selectMenu(event, item, true)}
        data-drag-source={drag.dragging && entryKey(drag.dragging.entry) === key ? '' : undefined}>
        <button title={project.path} aria-expanded={open} onClick={() => setOpen(key, !open)}
          onPointerDown={event => {if (groups.length && !archive) drag.press(event, {entry, label: project.name});}}>
          {open ? <ChevronDown size={13}/> : <ChevronRight size={13}/>}<Folder size={15}/><span>{project.name}</span>
        </button>
        {more(item, project.name + ' 项目操作')}
      </div>
      {open && <div className="project-conversations">
        {chats.slice(0, limit).map(chatRow)}
        {!archive && !project.sessions.length && <button className="sidebar-empty" onClick={() => props.onNew(project.path)}>在项目中新建聊天</button>}
      </div>}
    </section>;
  }
  function toggleArchive() {
    setArchive(previous => !previous);
    setMenu(null);
  }
  const loadedCount = archive ? model.archiveChats.length : model.recent.length;
  const moreChats = loadedCount > limit || [...model.projects.values()].some(project => project.sessions.length > limit)
    || [...model.groupById.values()].some(group => group.chats.length > limit);
  const menuEntry = menu && entryOf(menu.item);
  const menuProject = menu?.item.kind === 'project' ? menu.item.project : null;
  const menuChat = menu?.item.kind === 'chat' ? menu.item.chat : null;
  const canOrganize = !archive && !!props.layout;

  return <aside className="sidebar" onClickCapture={drag.interceptClick}>
    <div className="brand"><strong title="DeepSeek Cowork">DeepSeek Cowork</strong><div ref={props.toolsRef}/></div>
    <button className="new-chat" onClick={() => props.onNew()}><SquarePen size={18}/>新建聊天</button>
    <nav className={'conversation-list' + (drag.dragging ? ' sidebar-dragging' : '')} ref={drag.list} aria-label="分组、项目与聊天"
      onScroll={() => {if (menu) setMenu(null);}} onDragStart={event => event.preventDefault()}>
      {archive ? <>
        <div className="sidebar-section"><span>已归档</span><button title="返回侧栏列表" aria-pressed onClick={toggleArchive}><Archive size={14}/></button></div>
        {model.archiveProjects.map(projectRow)}
        {model.archiveChats.slice(0, limit).map(chatRow)}
        {!model.archiveProjects.length && !model.archiveChats.length && <p className="sidebar-empty">还没有归档的项目或聊天</p>}
      </> : <>
        {!!model.pinned.length && <section aria-label="置顶">
          <div className="sidebar-section">置顶</div>
          {model.pinned.map(item => item.kind === 'project' ? projectRow(item.project) : chatRow(item.chat))}
        </section>}
        <section className="sidebar-group-section" aria-label="分组">
          <div className={'sidebar-section' + (model.pinned.length ? ' spaced' : '')}><span>分组</span>
            <button ref={groupAdd} title="新建分组" aria-expanded={Boolean(draft)} disabled={!props.layout || saving} onClick={() => draft ? cancelGroup() : beginGroup()}><Plus size={15}/></button>
          </div>
          {draft && <form className="sidebar-group-form" onSubmit={event => {event.preventDefault(); void saveGroup();}} onKeyDown={event => {
            if (event.key === 'Escape' && !event.nativeEvent.isComposing) {event.preventDefault(); cancelGroup();}
          }}>
            <div>
              <input ref={groupInput} aria-label="分组名称" placeholder="分组名称" value={draft.name} maxLength={60}
                readOnly={saving} onChange={event => setDraft({...draft, name: event.target.value, error: undefined})}/>
              <button className="icon-button" type="submit" title="创建分组" disabled={saving || !draft.name.trim()}>{saving ? <LoaderCircle size={14} className="spin"/> : <Check size={15}/>}</button>
              <button className="icon-button" type="button" title="取消新建分组" disabled={saving} onClick={cancelGroup}><X size={15}/></button>
            </div>
            {draft.error && <p role="alert" className="error-text">{draft.error}</p>}
          </form>}
          {groups.map(group => {
            const entries = model.groupById.get(group.id)!;
            const key = 'group:' + group.id, open = expanded[key] ?? group.id === selectedGroup;
            return <section className="sidebar-user-group" key={group.id}>
              <div className="sidebar-group-heading" data-sidebar-group={group.id}>
                <button aria-expanded={open} title={group.name} onClick={() => setOpen(key, !open)}>
                  {open ? <ChevronDown size={13}/> : <ChevronRight size={13}/>}<span>{group.name}</span>
                </button>
              </div>
              {open && <div className="sidebar-group-members">
                {entries.projects.map(projectRow)}
                {entries.chats.slice(0, limit).map(chatRow)}
                {!entries.projects.length && !entries.chats.length && <p className="sidebar-empty">暂无可显示的条目</p>}
              </div>}
            </section>;
          })}
        </section>
        <section aria-label="项目">
          <div className="sidebar-section spaced"><span>项目</span><div>
            <button title="查看归档" aria-pressed={false} onClick={toggleArchive}><Archive size={14}/></button>
            <button title="新建项目" onClick={props.onProject}><Plus size={15}/></button>
          </div></div>
          {model.ungroupedProjects.slice(0, projectLimit).map(projectRow)}
          {model.ungroupedProjects.length > projectLimit && <button className="sidebar-more" onClick={() => setProjectLimit(projectLimit + 5)}><ChevronDown size={13}/>更多项目</button>}
        </section>
        <section aria-label="最近">
          <div className="sidebar-section spaced">最近</div>
          {model.recent.slice(0, limit).map(chatRow)}
        </section>
        {!model.visible.length && !model.projects.size && <p className="sidebar-empty">从一个新想法开始</p>}
      </>}
      {(moreChats || props.hasMore) && <button className="sidebar-more" onClick={() => {setLimit(limit + 40); if (!moreChats && props.hasMore) void props.onMore();}}><ChevronDown size={13}/>更早的聊天</button>}
    </nav>
    <div className="sidebar-bottom">
      <div className="sidebar-destinations">{destinations.map(([route, label, Icon]) => <button key={route} className={props.route === route || route === 'capabilities' && props.route === 'plugins' ? 'selected' : ''} onClick={() => props.onRoute(route)}><Icon size={17}/><span>{label}</span></button>)}</div>
      <UpdateNotice/>
      <div className="sidebar-utilities"><button className={props.route === 'settings' ? 'selected' : ''} aria-current={props.route === 'settings' ? 'page' : undefined} onClick={() => props.onRoute('settings')}><Settings size={17}/><span>设置</span><ChevronRight size={15} className="sidebar-settings-arrow" aria-hidden="true"/></button></div>
    </div>
    {menu && menuEntry && <SidebarMenu key={entryKey(menuEntry)} anchor={menu.anchor}
      groups={canOrganize ? groups : undefined} currentGroup={currentGroup(menuEntry)}
      onMove={id => closeAction(() => void move(menuEntry, id, true))} onNew={() => beginGroup(menuEntry)} onClose={() => setMenu(null)}
      first={menuProject ? !archive && <>
        <button role="menuitem" onClick={() => closeAction(() => void pinProject(menuProject))}>{menuProject.pinnedAt ? <PinOff size={15}/> : <Pin size={15}/>} {menuProject.pinnedAt ? '取消置顶' : '置顶项目'}</button>
        <button role="menuitem" onClick={() => closeAction(() => props.onNew(menuProject.path))}><SquarePen size={15}/>在项目中新建聊天</button>
      </> : menuChat && <>
        {!archive && <button role="menuitem" onClick={() => closeAction(() => props.onAction('pin', menuChat))}>{menuChat.meta.pinned ? <PinOff size={15}/> : <Pin size={15}/>} {menuChat.meta.pinned ? '取消置顶' : '置顶聊天'}</button>}
        <button role="menuitem" onClick={() => closeAction(() => props.onAction('rename', menuChat))}><Pencil size={15}/>重命名</button>
      </>}
      last={menuProject ? <button role="menuitem" onClick={() => closeAction(() => props.onProjectAction(archived(menuProject) ? 'restore' : 'archive', menuProject))}>
        {archived(menuProject) ? <Undo2 size={15}/> : <Archive size={15}/>} {archived(menuProject) ? '恢复项目' : '归档项目'}
      </button> : menuChat && <>
        <button role="menuitem" onClick={() => closeAction(() => props.onAction(menuChat.meta.archived ? 'restore' : 'archive', menuChat))}>{menuChat.meta.archived ? <Undo2 size={15}/> : <Archive size={15}/>} {menuChat.meta.archived ? '恢复聊天' : '归档聊天'}</button>
        <button role="menuitem" onClick={() => closeAction(() => props.onAction('delete', menuChat))}><Trash2 size={15}/>移到回收站</button>
      </>}/>}
    {drag.preview}
  </aside>;
}
