import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

export default function Dialog({ title, children, onClose, className = '' }: {
  title: string; children: React.ReactNode; onClose: () => void; className?: string;
}) {
  const element = useRef<HTMLDialogElement>(null);
  useEffect(() => { element.current?.showModal(); return () => element.current?.close(); }, []);
  return <dialog ref={element} className={`modal native-dialog ${className}`} aria-label={title} onCancel={event => { event.preventDefault(); onClose(); }}>
    <div className="section-heading"><h2>{title}</h2><button className="icon-button" title="关闭" onClick={onClose}><X size={18}/></button></div>{children}
  </dialog>;
}
