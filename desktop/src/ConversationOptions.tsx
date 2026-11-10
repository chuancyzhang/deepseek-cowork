import { useEffect, useRef, useState } from 'react';
import { SlidersHorizontal, UserRound, MessageCircleQuestion, Play } from 'lucide-react';
import type { Conversation } from './types';

export default function ConversationOptions({ conversation, changed, error }: { conversation: Conversation; changed: (id: string, meta: Record<string, any>) => void; error: (message: string) => void }) {
  const [open, setOpen] = useState(false), [profiles, setProfiles] = useState<any[]>([]), [busy, setBusy] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => { if (!open) return; void window.cowork.call<any[]>('experts.list').then(setProfiles).catch(failure => error(failure.message)); const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); }; window.addEventListener('pointerdown', close); return () => window.removeEventListener('pointerdown', close); }, [open, error]);
  async function save(value: Record<string, any>) {
    setBusy(true);
    try { await window.cowork.call('conversation.preferences', { conversation_id: conversation.id, value }); changed(conversation.id, value); }
    catch (failure) { error((failure as Error).message); }
    finally { setBusy(false); }
  }
  const selected = conversation.meta.conversation_agent;
  return <div className="conversation-options" ref={root}><button className="text-tool" title="任务方式与专家" aria-expanded={open} onClick={() => setOpen(!open)}><SlidersHorizontal size={14}/><span>{selected?.name || (conversation.meta.mode === 'grilling' ? '先问清楚' : '任务方式')}</span></button>{open && <section className="conversation-options-panel" aria-label="任务方式与专家" onKeyDown={event => { if (event.key === 'Escape') setOpen(false); }}><h3>这次怎样协作</h3><div className="button-row"><button disabled={busy} className={'secondary ' + (conversation.meta.mode !== 'grilling' ? 'selected' : '')} onClick={() => void save({ mode: 'execution', grill_execution_confirmed: false })}><Play size={14}/>直接推进</button><button disabled={busy} className={'secondary ' + (conversation.meta.mode === 'grilling' ? 'selected' : '')} onClick={() => void save({ mode: 'grilling', grill_execution_confirmed: false, grill_round_count: 0 })}><MessageCircleQuestion size={14}/>先问清楚</button></div><label><UserRound size={14}/>专家<select disabled={busy} aria-label="选择专家" value={selected?.id || ''} onChange={event => { const profile = profiles.find(item => item.id === event.target.value); void save({ conversation_agent: profile ? { id: profile.id, name: profile.name } : {} }); }}><option value="">普通助手</option>{profiles.filter(item => item.enabled !== false).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><p>用于下一次发送，当前正在执行的任务保持原有设置。</p></section>}</div>;
}
