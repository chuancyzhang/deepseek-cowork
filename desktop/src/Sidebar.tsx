import { useEffect, useMemo, useRef, useState } from 'react';
import { Archive, BookOpen, Users, Sparkles, Star, ChevronDown, ChevronRight, Folder, LoaderCircle, MessageSquare, MoreHorizontal, PanelLeftClose, Pin, Plus, Search, Settings, Undo2, Trash2, Pencil, X } from 'lucide-react';
import type { Conversation } from './types';

type Props = {
  conversations: Conversation[]; projects: any[]; selected: string; route: string; running: Set<string>;
  onSelect: (id: string) => void; onNew: (workspace?: string) => void; onRoute: (route: string) => void;
  onCollapse: () => void; onProject: () => void; onAction: (action: string, conversation: Conversation) => void;
  onMore: () => Promise<void>; hasMore: boolean;
};
const pathKey = (value: string) => /^[a-z]:|\\/i.test(value) ? value.replace(/\\/g, '/').toLowerCase().replace(/\/$/, '') : value.replace(/\/$/, '');

export default function Sidebar(props: Props) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<Conversation[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [archive, setArchive] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [limit, setLimit] = useState(40);
  const [projectLimit, setProjectLimit] = useState(5);
  const [menu, setMenu] = useState<{ item: Conversation; x: number; y: number } | null>(null);
  const menuElement = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!query.trim()) { setHits([]); setSearching(false); setSearchError(''); return; }
    let valid = true;
    setSearching(true);
    const timer = setTimeout(() => {
      window.cowork.call<Conversation[]>('conversation.search', { query }).then(result => {
        if (valid) { setHits(result); setSearchError(''); }
      }).catch(error => { if (valid) setSearchError(error.message); }).finally(() => { if (valid) setSearching(false); });
    }, 220);
    return () => { valid = false; clearTimeout(timer); };
  }, [query]);
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (!menuElement.current?.contains(event.target as Node)) setMenu(null); };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenu(null);
      if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
        event.preventDefault(); const buttons = [...menuElement.current!.querySelectorAll('button')];
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
        buttons[(index + (event.key === 'ArrowDown' ? 1 : buttons.length - 1)) % buttons.length]?.focus();
      }
    };
    document.addEventListener('pointerdown', close); document.addEventListener('keydown', key);
    menuElement.current?.querySelector('button')?.focus();
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', key); };
  }, [menu]);
  const visible = (query.trim() ? hits : props.conversations).filter(item => archive ? item.meta.archived : !item.meta.archived && (query.trim() || item.has_content !== false));
  const projects = useMemo(() => {
    const groups = new Map<string, any>();
    for (const project of props.projects) if (!project.hidden && !project.archived) groups.set(pathKey(project.path), { ...project, sessions: [] });
    for (const item of props.conversations) {
      const path = item.meta.workspace_dir || item.meta.workspace_path;
      if (!path || item.has_content === false || item.meta.workspace_source === 'chat' || item.meta.team_space_required || item.meta.archived) continue;
      const key = pathKey(path);
      if (!groups.has(key)) groups.set(key, { path, name: path.split(/[\\/]/).pop(), sessions: [] });
      groups.get(key).sessions.push(item);
    }
    return [...groups.values()].sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || (b.updated_at || 0) - (a.updated_at || 0));
  }, [props.projects, props.conversations]);
  const grouped = new Set(projects.flatMap(project => project.sessions.map((item: Conversation) => item.id)));
  const pinned = visible.filter(item => item.meta.pinned);
  const recent = visible.filter(item => !item.meta.pinned && !grouped.has(item.id));
  function row(item: Conversation) {
    return <div key={item.id} className={`history-row ${item.id === props.selected && props.route === 'chat' ? 'selected' : ''}`}
      onContextMenu={event => { event.preventDefault(); setMenu({ item, x: Math.min(event.clientX, innerWidth - 190), y: Math.min(event.clientY, innerHeight - 210) }); }}>
      <button className="history-select" title={item.display_title || item.title || '新对话'} onClick={() => props.onSelect(item.id)}>
        {props.running.has(item.id) ? <LoaderCircle size={14} className="spin" aria-label="正在运行"/> : <MessageSquare size={14}/>}
        <span>{item.display_title || item.title || '新对话'}</span>
      </button>
      <button className="row-more icon-button" title="对话操作" aria-haspopup="menu" onClick={event => {
        const box = event.currentTarget.getBoundingClientRect(); setMenu({ item, x: box.left, y: Math.min(box.bottom + 4, innerHeight - 210) });
      }}><MoreHorizontal size={15}/></button>
    </div>;
  }
  return <aside className="sidebar">
    <div className="brand"><span className="brand-symbol">✳</span><strong>DeepSeek Cowork</strong><button className="icon-button" title="收起侧栏" onClick={props.onCollapse}><PanelLeftClose size={17}/></button></div>
    <button className="new-chat" onClick={() => props.onNew()}><Plus size={18}/>新建聊天</button>
    <label className="search"><Search size={15}/><input aria-label="搜索项目和聊天内容" placeholder="搜索" value={query} onChange={event => setQuery(event.target.value)}/>{query && <button title="清除搜索" onClick={() => setQuery('')}><X size={13}/></button>}</label>
    <nav className="conversation-list" aria-label="项目与对话">
      <div className="sidebar-section"><span>{query ? '搜索结果' : archive ? '已归档' : '项目'}</span><div>
        <button title={archive ? '返回最近对话' : '查看归档'} aria-pressed={archive} onClick={() => setArchive(!archive)}><Archive size={14}/></button>
        {!archive && <button title="添加项目" onClick={props.onProject}><Plus size={15}/></button>}
      </div></div>
      {query || archive ? <>{searching && <div className="sidebar-empty"><LoaderCircle size={13} className="spin"/> 正在搜索…</div>}{searchError && <p role="alert" className="sidebar-empty">{searchError}</p>}{visible.slice(0, limit).map(row)}</> : <>
        {projects.slice(0, projectLimit).map(project => {
          const key = pathKey(project.path);
          const open = expanded[key] ?? project.sessions.some((item: Conversation) => item.id === props.selected);
          return <section className="project-group" key={key}>
            <div className="project-heading"><button title={project.path} aria-expanded={open} onClick={() => setExpanded(previous => ({ ...previous, [key]: !open }))}>
              {open ? <ChevronDown size={13}/> : <ChevronRight size={13}/>}<Folder size={15}/><span>{project.name}</span>{project.pinned && <Pin size={11}/>}</button>
              <button className="project-add icon-button" title={`在 ${project.name} 中新建聊天`} onClick={() => props.onNew(project.path)}><Plus size={14}/></button></div>
            {open && <div className="project-conversations">{project.sessions.filter((item: Conversation) => !item.meta.pinned).slice(0, limit).map(row)}{!project.sessions.length && <button className="sidebar-empty" onClick={() => props.onNew(project.path)}>开始第一段聊天</button>}</div>}
          </section>;
        })}
        {projects.length > projectLimit && <button className="sidebar-more" onClick={() => setProjectLimit(projectLimit + 5)}><ChevronDown size={13}/>更多项目</button>}
        {!!pinned.length && <><div className="sidebar-section spaced">置顶</div>{pinned.map(row)}</>}
        <div className="sidebar-section spaced">最近</div>{recent.slice(0, limit).map(row)}
      </>}
      {!visible.length && !searching && <p className="sidebar-empty">{query ? '没有匹配的聊天' : archive ? '还没有归档的聊天' : '从一个新想法开始'}</p>}
      {(visible.length > limit || props.hasMore) && <button className="sidebar-more" onClick={() => { setLimit(limit + 40); if (visible.length <= limit && props.hasMore) void props.onMore(); }}><ChevronDown size={13}/>更早的聊天</button>}
    </nav>
    <div className="sidebar-bottom"><div className="sidebar-destinations">{[['capabilities','能力',Sparkles],['library','资料库',BookOpen],['favorites','常用任务',Star],['team','团队空间',Users]].map(([route,label,Icon]: any) => <button key={route} className={(props.route === route || route === 'capabilities' && props.route === 'plugins') ? 'selected' : ''} onClick={() => props.onRoute(route)}><Icon size={17}/><span>{label}</span></button>)}</div><div className="sidebar-utilities"><button className={props.route === 'settings' ? 'selected' : ''} aria-current={props.route === 'settings' ? 'page' : undefined} onClick={() => props.onRoute('settings')}><Settings size={17}/><span>设置</span><ChevronRight size={15} className="sidebar-settings-arrow" aria-hidden="true"/></button></div></div>
    {menu && <div ref={menuElement} className="context-menu" role="menu" style={{ left: menu.x, top: menu.y }}>
      {([['rename', '重命名', Pencil], ['pin', menu.item.meta.pinned ? '取消置顶' : '置顶', Pin],
        [menu.item.meta.archived ? 'restore' : 'archive', menu.item.meta.archived ? '恢复聊天' : '归档', menu.item.meta.archived ? Undo2 : Archive],
        ['delete', '移到回收站', Trash2]] as const).map(([action, label, Icon]) => <button key={action} role="menuitem" onClick={() => { props.onAction(action, menu.item); setMenu(null); }}><Icon size={15}/>{label}</button>)}
    </div>}
  </aside>;
}
