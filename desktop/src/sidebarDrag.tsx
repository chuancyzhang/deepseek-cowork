import {useEffect, useRef, useState, type MouseEvent, type PointerEvent as ReactPointerEvent} from 'react';
import {Folder, MessageSquare} from 'lucide-react';
import type {SidebarEntry} from './types';

type DragItem = {entry: SidebarEntry; label: string};
type Gesture = {
  item: DragItem; pointer: number; x: number; y: number; startX: number; startY: number;
  timer: number; frame: number; active: boolean; target: HTMLElement | null;
};

export function useSidebarDrag(onDrop: (entry: SidebarEntry, group: string) => void, onStart: () => void) {
  const list = useRef<HTMLElement>(null);
  const ghost = useRef<HTMLDivElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const suppressClick = useRef(false);
  const handlers = useRef({onDrop, onStart});
  handlers.current = {onDrop, onStart};
  const [dragging, setDragging] = useState<DragItem | null>(null);

  useEffect(() => {
    function finish(current: Gesture) {
      gesture.current = null;
      window.clearTimeout(current.timer);
      cancelAnimationFrame(current.frame);
      current.target?.removeAttribute('data-drop-target');
      if (list.current?.hasPointerCapture(current.pointer)) list.current.releasePointerCapture(current.pointer);
    }
    function cancel() {
      if (!gesture.current) return;
      finish(gesture.current);
      setDragging(null);
    }
    function move(event: PointerEvent) {
      const current = gesture.current;
      if (!current || current.pointer !== event.pointerId) return;
      current.x = event.clientX;
      current.y = event.clientY;
      if (!current.active && Math.hypot(current.x - current.startX, current.y - current.startY) > 9) cancel();
    }
    function release(event: PointerEvent) {
      const current = gesture.current;
      if (!current || current.pointer !== event.pointerId) return;
      const target = current.active ? groupAt(event.clientX, event.clientY) : null;
      cancel();
      if (target?.dataset.sidebarGroup) handlers.current.onDrop(current.item.entry, target.dataset.sidebarGroup);
    }
    function key(event: KeyboardEvent) {
      if (event.key === 'Escape' && gesture.current?.active) {
        event.preventDefault();
        cancel();
      }
    }
    function resetClick() { if (!gesture.current) suppressClick.current = false; }
    window.addEventListener('pointerdown', resetClick, true);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', release);
    window.addEventListener('pointercancel', cancel);
    window.addEventListener('blur', cancel);
    window.addEventListener('keydown', key);
    return () => {
      if (gesture.current) finish(gesture.current);
      window.removeEventListener('pointerdown', resetClick, true);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', release);
      window.removeEventListener('pointercancel', cancel);
      window.removeEventListener('blur', cancel);
      window.removeEventListener('keydown', key);
    };
  }, []);

  function groupAt(x: number, y: number) {
    const target = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-sidebar-group]');
    return target && list.current?.contains(target) ? target : null;
  }

  function paint(current: Gesture) {
    if (gesture.current !== current || !list.current) return;
    const {x, y} = current;
    const bounds = list.current.getBoundingClientRect();
    if (x >= bounds.left && x < bounds.right && y >= bounds.top && y <= bounds.bottom) {
      if (y < bounds.top + 25) list.current.scrollTop -= 9;
      else if (y > bounds.bottom - 25) list.current.scrollTop += 9;
    }
    const target = groupAt(x, y);
    if (target !== current.target) {
      current.target?.removeAttribute('data-drop-target');
      target?.setAttribute('data-drop-target', '');
      current.target = target;
    }
    if (ghost.current) {
      ghost.current.style.left = Math.max(4, Math.min(x + 12, innerWidth - ghost.current.offsetWidth - 6)) + 'px';
      ghost.current.style.top = Math.max(4, Math.min(y + 12, innerHeight - ghost.current.offsetHeight - 6)) + 'px';
    }
    current.frame = requestAnimationFrame(() => paint(current));
  }

  function press(event: ReactPointerEvent, item: DragItem) {
    if (event.pointerType !== 'mouse' || event.button !== 0 || gesture.current) return;
    const {pointerId: pointer, clientX: x, clientY: y} = event;
    const current: Gesture = {item, pointer, x, y, startX: x, startY: y, timer: 0, frame: 0, active: false, target: null};
    gesture.current = current;
    current.timer = window.setTimeout(() => {
      try { list.current?.setPointerCapture(pointer); }
      catch { gesture.current = null; return; }
      current.active = true;
      suppressClick.current = true;
      handlers.current.onStart();
      setDragging(item);
      current.frame = requestAnimationFrame(() => paint(current));
    }, 320);
  }

  function interceptClick(event: MouseEvent) {
    if (!suppressClick.current || event.detail === 0) return;
    suppressClick.current = false;
    event.preventDefault();
    event.stopPropagation();
  }

  return {list, dragging, press, interceptClick, preview: dragging && <div ref={ghost} className="sidebar-drag-preview" aria-hidden="true">
    {dragging.entry.kind === 'project' ? <Folder size={15}/> : <MessageSquare size={15}/>}
    <span>{dragging.label}</span>
  </div>};
}
