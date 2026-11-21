import {useEffect, useLayoutEffect, useRef, useState, type RefObject} from 'react';
import {MoreHorizontal} from 'lucide-react';

export type MenuAction = [string, React.ElementType, () => Promise<unknown>];
export default function MoreActions({label, actions, error, triggerRef, onOpenChange}: {
  label: string; actions: MenuAction[]; error: (message: string) => void;
  triggerRef?: RefObject<HTMLButtonElement | null>; onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false), root = useRef<HTMLDivElement>(null), internalTrigger = useRef<HTMLButtonElement>(null);
  const trigger = triggerRef || internalTrigger;
  useLayoutEffect(() => {
    onOpenChange?.(open);
    return () => onOpenChange?.(false);
  }, [open, onOpenChange]);
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
  }}><button ref={trigger} className="icon-button" title={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}><MoreHorizontal size={17}/></button>
    {open && <div className="file-actions-menu" role="menu">{actions.map(([label,Icon,action]) => <button role="menuitem" key={label} onClick={() => {setOpen(false);trigger.current?.focus();void action().catch(failure => error(failure.message));}}><Icon size={15}/>{label}</button>)}</div>}
  </div>;
}
