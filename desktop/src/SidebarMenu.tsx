import {useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode} from 'react';
import {Check, ChevronRight, FolderPlus, List} from 'lucide-react';
import type {SidebarGroup} from './types';

export type MenuAnchor = {x: number; below: number; above: number; trigger: HTMLButtonElement | null};

export default function SidebarMenu({anchor, first, last, groups, currentGroup, onMove, onNew, onClose, label = '条目操作', className = ''}: {
  anchor: MenuAnchor;
  first: ReactNode;
  last?: ReactNode;
  groups?: SidebarGroup[];
  currentGroup?: string;
  onMove?: (id: string | null) => void;
  onNew?: () => void;
  onClose: () => void;
  label?: string;
  className?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const main = useRef<HTMLDivElement>(null);
  const submenu = useRef<HTMLDivElement>(null);
  const moveButton = useRef<HTMLButtonElement>(null);
  const openByKeyboard = useRef(false);
  const [open, setOpen] = useState(false);

  useLayoutEffect(() => {
    const primary = main.current;
    if (!primary) return;
    const left = Math.max(6, Math.min(anchor.x, innerWidth - primary.offsetWidth - 6));
    const belowFits = anchor.below + primary.offsetHeight < innerHeight - 8;
    const above = anchor.above - primary.offsetHeight;
    primary.style.left = left + 'px';
    primary.style.top = Math.max(6, belowFits ? anchor.below : above >= 6 ? above : innerHeight - primary.offsetHeight - 6) + 'px';
    primary.querySelector<HTMLButtonElement>('button')?.focus({preventScroll: true});
  }, [anchor]);

  useLayoutEffect(() => {
    const nested = submenu.current, trigger = moveButton.current, primary = main.current;
    if (!open || !nested || !trigger || !primary) return;
    const rect = primary.getBoundingClientRect();
    const left = rect.right + nested.offsetWidth + 10 <= innerWidth
      ? rect.right + 4 : Math.max(6, rect.left - nested.offsetWidth - 4);
    nested.style.left = left + 'px';
    nested.style.top = Math.max(6, Math.min(trigger.getBoundingClientRect().top - 4, innerHeight - nested.offsetHeight - 6)) + 'px';
    if (openByKeyboard.current) nested.querySelector<HTMLButtonElement>('button')?.focus({preventScroll: true});
    openByKeyboard.current = false;
  }, [open, groups]);

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!root.current?.contains(target) && !anchor.trigger?.contains(target)) onClose();
    };
    document.addEventListener('pointerdown', dismiss);
    window.addEventListener('blur', onClose);
    window.addEventListener('resize', onClose);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      window.removeEventListener('blur', onClose);
      window.removeEventListener('resize', onClose);
    };
  }, [anchor, onClose]);

  function enterSubmenu() {
    if (open) submenu.current?.querySelector<HTMLButtonElement>('button')?.focus({preventScroll: true});
    else {
      openByKeyboard.current = true;
      setOpen(true);
    }
  }

  function key(event: KeyboardEvent) {
    const inSubmenu = submenu.current?.contains(event.target as Node);
    if (event.key === 'Escape' || event.key === 'ArrowLeft' && inSubmenu) {
      event.preventDefault();
      event.stopPropagation();
      if (open) {
        setOpen(false);
        moveButton.current?.focus();
      } else {
        onClose();
        anchor.trigger?.focus({preventScroll: true});
      }
    } else if (event.key === 'ArrowRight' && event.target === moveButton.current) {
      event.preventDefault();
      enterSubmenu();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const buttons = [...(inSubmenu ? submenu.current : main.current)?.querySelectorAll<HTMLButtonElement>('button') ?? []];
      if (!buttons.length) return;
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      buttons[(index + step + buttons.length) % buttons.length]?.focus();
    } else if (event.key === 'Tab') {
      onClose();
      anchor.trigger?.focus({preventScroll: true});
    }
  }

  return <div ref={root} onKeyDown={key}>
    <div ref={main} className={'context-menu sidebar-context-menu ' + className} role="menu" aria-label={label}>
      {first}
      {groups && <button ref={moveButton} role="menuitem" aria-haspopup="menu" aria-expanded={open}
        onPointerEnter={event => {if (event.pointerType === 'mouse') setOpen(true);}} onClick={enterSubmenu}>
        <List size={15}/><span>移到分组</span><ChevronRight size={14}/>
      </button>}
      {last}
    </div>
    {open && groups && <div ref={submenu} className="context-menu sidebar-context-menu sidebar-group-menu" role="menu" aria-label="移到分组">
      {groups.map(group => <button key={group.id} role="menuitemradio" aria-checked={currentGroup === group.id} title={group.name} onClick={() => onMove?.(group.id)}>
        <span>{group.name}</span>{currentGroup === group.id && <Check size={14}/>}
      </button>)}
      {currentGroup && <button role="menuitem" onClick={() => onMove?.(null)}>移出分组</button>}
      <button role="menuitem" className={groups.length ? 'sidebar-new-group-option' : undefined} onClick={onNew}><FolderPlus size={15}/>新建分组…</button>
    </div>}
  </div>;
}
