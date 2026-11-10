import { useEffect, useRef, useState } from 'react';
import { Paperclip, Plus, X } from 'lucide-react';
import type { Attachment } from './types';
import { receiveAttachments } from './attachments';

export default function HistoryEditor({ conversationId, prepared, onClose, onSaved, onError, submit }: {
  conversationId: string; prepared: any; onClose: () => void; onSaved: (result: any) => void; onError: (message: string) => void;
  submit: (value: {text: string; attachments: Attachment[]}) => Promise<any>;
}) {
  const key = `cowork-history-edit:${conversationId}:${prepared.message.id}`;
  const [draft, setDraft] = useState<{text: string; attachments: Attachment[]}>(() => {
    const fallback = {text: prepared.message.meta?.display_content ?? prepared.message.content ?? '', attachments: prepared.message.meta?.desktop_attachments || (prepared.message.meta?.user_added_files || []).map((path: string) => ({path, name: path.split(/[\\/]/).pop(), size: 0}))};
    const saved = localStorage.getItem(key); if (!saved) return fallback;
    try { const value = JSON.parse(saved); return typeof value?.text === 'string' && Array.isArray(value.attachments) ? value : {...fallback, text: saved}; } catch { return {...fallback, text: saved}; }
  });
  const latest = useRef(draft); latest.current = draft;
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  useEffect(() => { try {localStorage.setItem(key, JSON.stringify(draft));} catch {setFailure('编辑内容尚未写入本机缓存，请保存或复制当前内容。');} }, [key, draft]);
  function add(files: Attachment[]) { setDraft(previous => ({...previous, attachments: [...previous.attachments, ...files.filter(file => !previous.attachments.some(old => old.path === file.path))]})); }
  async function save() {
    setBusy(true); setFailure('');
    try { const result = await submit(draft); localStorage.removeItem(key); onSaved(result); }
    catch (failure) {setFailure((failure as Error).message);} finally {setBusy(false);}
  }
  return <section className="history-editor inline-history-editor" aria-label="编辑已发送消息" onDragOver={event => event.preventDefault()} onDrop={event => {event.preventDefault(); void receiveAttachments(conversationId, [...event.dataTransfer.files]).then(add).catch(failure => onError(failure.message));}}>
    <div className="composer-contexts">{draft.attachments.map(file => <div className="context-chip" key={file.path}><Paperclip size={13}/><span title={file.path}>{file.name}</span><button aria-label={'移除附件 ' + file.name} disabled={busy} onClick={() => setDraft({...draft, attachments: draft.attachments.filter(item => item.path !== file.path)})}><X size={12}/></button></div>)}</div>
    <textarea aria-label="编辑消息内容" autoFocus rows={6} value={draft.text} onChange={event => setDraft({...draft, text: event.target.value})} disabled={busy} onKeyDown={event => {if (event.key === 'Escape' && !busy) onClose();}} onPaste={event => {
      const files = [...event.clipboardData.files], value = event.clipboardData.getData('text/plain');
      if (files.length) {event.preventDefault(); void receiveAttachments(conversationId, files).then(add).catch(failure => onError(failure.message));}
      else if (value.length >= 4000 || value.split('\n').length > 80) {event.preventDefault(); void receiveAttachments(conversationId, [new File([value], '粘贴文本-' + Date.now() + '.txt')]).then(add).catch(failure => {setDraft({...latest.current, text: latest.current.text + value}); onError('文字附件未保存，原文已保留在编辑区。' + failure.message);});}
    }}/>
    <p className="muted">{prepared.downstream ? `发送将替换此消息及后续 ${prepared.downstream} 条消息，并重新回答。` : '发送后将替换这条消息，并重新回答。'}已生成的文件会保留。</p>
    {failure && <p role="alert" className="error-text">{failure}</p>}
    <div className="button-row"><button className="icon-button" title="添加附件" disabled={busy} onClick={() => void window.cowork.call<Attachment[]>('dialog.attach', {conversation_id: conversationId}).then(add).catch(failure => onError(failure.message))}><Plus size={17}/></button><button className="secondary" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy || !draft.text.trim() && !draft.attachments.length} onClick={() => void save()}>{busy ? '正在提交…' : '保存并重新回答'}</button></div>
  </section>;
}
