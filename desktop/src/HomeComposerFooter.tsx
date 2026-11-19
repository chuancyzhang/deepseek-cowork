import {useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent} from 'react';
import {ChartNoAxesCombined, Check, ChevronDown, Folder, Globe, Plus, Search, Sparkles, Table2, X} from 'lucide-react';
import {chatWorkspace, pathKey, projectPaths} from './projects';
import type {Conversation, ProjectRecord} from './types';

const shortcuts = [
  ['金融分析', '帮我分析这些金融资料。', ChartNoAxesCombined],
  ['数据分析', '帮我分析这份数据，找出关键趋势。', Table2],
  ['浏览器自动化', '帮我在浏览器中完成这个任务。', Globe],
] as const;

export default function HomeComposerFooter({conversation, projects, disabled, onOpenChange, selectProject, createProject, startPrompt, enterCreation}: {
  conversation: Conversation | null;
  projects: ProjectRecord[];
  disabled: boolean;
  onOpenChange: (open: boolean) => void;
  selectProject: (project: ProjectRecord | null) => Promise<void>;
  createProject: () => void;
  startPrompt: (text: string) => void;
  enterCreation?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const selecting = useRef(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const panelId = useId();
  const workspace = conversation ? chatWorkspace(conversation) : '';
  const directory = conversation?.meta.workspace_dir || conversation?.meta.workspace_path || workspace;
  const available = projects.filter(project => !project.archived && !project.hidden);
  const selected = available.find(project => projectPaths(project).some(path => pathKey(path) === workspace));
  const name = selected?.name || (workspace ? directory.split(/[\\/]/).filter(Boolean).at(-1) || directory : '独立聊天');
  const term = query.trim().toLocaleLowerCase();
  const matching = available.filter(project =>
    project.name.toLocaleLowerCase().includes(term) || project.path.toLocaleLowerCase().includes(term));

  useLayoutEffect(() => {
    onOpenChange(open);
    return () => onOpenChange(false);
  }, [open, onOpenChange]);

  useLayoutEffect(() => {
    const anchor = trigger.current, menu = panel.current;
    if (!open || !anchor || !menu) return;
    const surface = anchor.closest('.workbench-home');
    const place = () => {
      const box = anchor.getBoundingClientRect(), area = surface?.getBoundingClientRect();
      const above = box.top - Math.max(0, area?.top ?? 0) - 12;
      const below = Math.min(innerHeight, area?.bottom ?? innerHeight) - box.bottom - 12;
      const upwards = below < 230 && above > below;
      const right = Math.min(innerWidth, area?.right ?? innerWidth) - 8;
      const left = Math.max(8, area?.left ?? 8);
      const width = Math.max(0, Math.min(304, right - left));
      menu.style.width = width + 'px';
      menu.style.left = Math.max(left, Math.min(box.left, right - width)) + 'px';
      menu.style.top = upwards ? 'auto' : box.bottom + 5 + 'px';
      menu.style.bottom = upwards ? innerHeight - box.top + 5 + 'px' : 'auto';
      menu.style.maxHeight = Math.max(0, upwards ? above : below) + 'px';
    };
    place();
    search.current?.focus({preventScroll: true});
    const observer = new ResizeObserver(place);
    if (surface) observer.observe(surface);
    if (root.current) observer.observe(root.current);
    surface?.addEventListener('scroll', place);
    window.addEventListener('resize', place);
    return () => {
      observer.disconnect();
      surface?.removeEventListener('scroll', place);
      window.removeEventListener('resize', place);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const blur = () => setOpen(false);
    document.addEventListener('pointerdown', outside);
    window.addEventListener('blur', blur);
    return () => {
      document.removeEventListener('pointerdown', outside);
      window.removeEventListener('blur', blur);
    };
  }, [open]);

  function close(restoreFocus = false) {
    setOpen(false);
    if (restoreFocus) trigger.current?.focus({preventScroll: true});
  }

  async function choose(project: ProjectRecord | null) {
    if (disabled || selecting.current) return;
    if ((project ? pathKey(project.path) : '') === workspace) {
      close(true);
      return;
    }
    selecting.current = true;
    setBusy(true);
    setFailure('');
    try {
      await selectProject(project);
      close();
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      selecting.current = false;
      setBusy(false);
    }
  }

  function navigate(event: KeyboardEvent<HTMLDivElement>) {
    if (!open || event.nativeEvent.isComposing) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (event.key === 'Enter' && event.target === search.current && term && matching[0]) {
      event.preventDefault();
      void choose(matching[0]);
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const items = [...panel.current?.querySelectorAll<HTMLElement>('input, button:not(:disabled)') ?? []];
      if (!items.length) return;
      const index = items.indexOf(document.activeElement as HTMLElement);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      const origin = index < 0 && step < 0 ? 0 : index;
      event.preventDefault();
      items[(origin + step + items.length) % items.length].focus();
    }
  }

  return <div className="home-composer-footer">
    <div className="home-project-control" ref={root} onKeyDown={navigate} onBlur={event => {
      if (!selecting.current && event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) close();
    }}>
      <button ref={trigger} className="home-project-trigger" type="button" disabled={disabled || busy}
        title={selected?.path || directory || name} aria-label={'切换项目，当前为 ' + name}
        aria-haspopup="dialog" aria-controls={open ? panelId : undefined} aria-expanded={open} onClick={() => {
          if (open) close();
          else {setQuery(''); setFailure(''); setOpen(true);}
        }}><Folder size={15}/><span>{name}</span><ChevronDown size={13}/></button>
      {open && <div id={panelId} className="home-project-menu" ref={panel} role="dialog" aria-label="切换项目" aria-busy={busy}>
        <label className="home-project-search"><Search size={15}/><input ref={search} type="search"
          aria-label="搜索项目" placeholder="搜索项目" value={query} onChange={event => setQuery(event.target.value)}/></label>
        <div className="home-project-list">
          {matching.map(project => <button key={project.path} className="picker-row" type="button" title={project.path}
            aria-pressed={selected === project} disabled={disabled || busy} onClick={() => void choose(project)}>
            <Folder size={15}/><span>{project.name}</span>{selected === project && <Check size={15}/>}
          </button>)}
          {!matching.length && <div className="picker-empty">{term ? '没有匹配的项目' : '还没有项目'}</div>}
        </div>
        <button className="picker-row home-project-action" type="button" disabled={disabled || busy}
          onClick={() => {close(true); createProject();}}><Plus size={15}/><span>新建项目</span></button>
        <button className="picker-row home-project-action" type="button" aria-pressed={!workspace} disabled={disabled || busy}
          onClick={() => void choose(null)}><X size={15}/><span>不在项目中工作</span>{!workspace && <Check size={15}/>}</button>
        {failure && <p className="home-project-error error-text" role="alert">{failure}</p>}
      </div>}
    </div>
    <div className="home-shortcut-actions">
      {shortcuts.map(([label, text, Icon]) => <button key={label} type="button" onClick={() => startPrompt(text)}>
        <Icon size={15}/>{label}
      </button>)}
      {enterCreation && <button type="button" onClick={enterCreation}><Sparkles size={15}/>创造模式</button>}
    </div>
  </div>;
}
