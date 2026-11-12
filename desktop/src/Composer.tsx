import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { ArrowLeft, ArrowUp, BookOpen, Check, ChevronDown, ChevronRight, FileText, Folder, Link, LoaderCircle, MessageCircleQuestion, MessageSquare, Paperclip, Play, Plus, Puzzle, Search, ShieldCheck, SlidersHorizontal, Square, UserRound, X } from 'lucide-react';
import type { Attachment, Conversation, Settings } from './types';
import { receiveAttachments } from './attachments';

const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
type Props = {
  conversation: Conversation | null; conversations: Conversation[]; settings: Settings | null;
  text: string; setText: (value: string) => void; attachments: Attachment[]; setAttachments: (value: Attachment[]) => void;
  skills: string[]; active: boolean; sending: boolean; textarea: RefObject<HTMLTextAreaElement | null>;
  ensureConversation: () => Promise<string>; preferences: (value: Record<string, any>, conversationId?: string) => Promise<void>;
  send: () => Promise<void>; stop: () => Promise<void>; attach: () => Promise<void>; permission: (enabled: boolean) => Promise<void>;
  selectModel: (id: string) => Promise<void>; route: (name: string) => Promise<void>; error: (message: string) => void;
};
const categories = [['mode', '模式', SlidersHorizontal], ['experts', '专家', UserRound], ['skills', '技能', Puzzle], ['connectors', '连接器', Link], ['conversations', '对话', MessageSquare], ['library', '资料', BookOpen], ['variables', '插入变量', FileText]] as const;
const modes = [['execution', '直接推进', '讨论、执行并完成任务'], ['grilling', '拷问模式', '先澄清问题，再确认执行']] as const;

export default function Composer(props: Props) {
  const { conversation, conversations, settings, text, setText, attachments, setAttachments, skills, active, sending, textarea, error } = props;
  const [menu, setMenu] = useState(''), [section, setSection] = useState(''), [query, setQuery] = useState(''), [items, setItems] = useState<any[]>([]), [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  const [pending, setPending] = useState<{skills: string[]; expert: any; references: string[]} | null>(null), [mentionStart, setMentionStart] = useState(-1), [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [placement, setPlacement] = useState({above:true, height:420});
  const root = useRef<HTMLDivElement>(null), latest = useRef(props); latest.current = props;
  const previousId = useRef(conversation?.id), hover = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const prefs = conversation?.meta || {}, refs: any[] = prefs.conversation_refs || [], knowledge: any[] = prefs.knowledge_refs || [];
  const model = settings?.models.find(model => model.id === (prefs.selected_model_id || settings.selected_model_id));
  function close() { clearTimeout(hover.current); setMenu(''); setSection(''); setQuery(''); setPending(null); }
  function open(target: string, part = '') { setMenu(target); setSection(part); setQuery(''); setFailure(''); }
  useEffect(() => {
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) close(); };
    const blur = () => close();
    document.addEventListener('pointerdown', outside); window.addEventListener('blur', blur);
    return () => { document.removeEventListener('pointerdown', outside); window.removeEventListener('blur', blur); };
  }, []);
  useEffect(() => { if (previousId.current) close(); previousId.current = conversation?.id; }, [conversation?.id]);
  useLayoutEffect(() => { if (textarea.current) { textarea.current.style.height = 'auto'; textarea.current.style.height = Math.min(180, Math.max(70, textarea.current.scrollHeight)) + 'px'; } }, [text, textarea]);
  useLayoutEffect(() => {
    if (!menu || !root.current) return;
    const place = () => {const box = root.current!.getBoundingClientRect(); const above = Math.max(100, box.top - 60), below = Math.max(100, innerHeight - box.bottom - 12); const up = above >= 240 || above >= below; setPlacement({above:up, height:Math.min(500, up ? above : below)});};
    place(); const observer = new ResizeObserver(place); observer.observe(root.current); window.addEventListener('resize',place);
    return () => {observer.disconnect(); window.removeEventListener('resize',place);};
  }, [menu, section]);
  useEffect(() => {
    setLoading(false); setItems([]); setFailure('');
    if (!menu || !section || ['mode', 'conversations', 'library'].includes(section)) return;
    let alive = true; setLoading(true);
    const method = section === 'experts' ? 'experts.list' : section === 'variables' ? 'variables.list' : 'skills.list';
    void call<any[]>(method).then(value => { if (alive) setItems(value); }).catch(failure => { if (alive) setFailure(failure.message); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [menu, section]);
  async function applyPreference(value: Record<string, any>, dismiss = false) { setBusy(true); setFailure(''); try { await props.preferences(value); if (dismiss) close(); } catch (failure) { setFailure((failure as Error).message); } finally { setBusy(false); } }
  async function reference(sourceId: string) {
    const cid = await props.ensureConversation();
    const ref = await call('conversation.reference', {conversation_id: cid, source_id: sourceId});
    await props.preferences({conversation_refs: [...refs.filter(item => item.session_id !== sourceId), ref]}, cid);
  }
  async function select(item: any) {
    if (section === 'experts') { const expert = item.id ? {id: item.id, name: item.name} : {}; if (pending) setPending({...pending, expert}); else await applyPreference({conversation_agent: expert}, true); }
    if (['skills', 'connectors'].includes(section)) {
      const selected = pending?.skills || skills, next = selected.includes(item.name) ? selected.filter(name => name !== item.name) : [...selected, item.name];
      if (pending) setPending({...pending, skills: next}); else await applyPreference({selected_skill_names: next});
    }
    if (section === 'conversations') {
      if (pending) setPending({...pending, references: pending.references.includes(item.id) ? pending.references.filter(id => id !== item.id) : [...pending.references, item.id]});
      else { setBusy(true); try { if (refs.some(ref => ref.session_id === item.id)) await props.preferences({conversation_refs: refs.filter(ref => ref.session_id !== item.id)}); else await reference(item.id); } catch (failure) { setFailure((failure as Error).message); } finally { setBusy(false); } }
    }
    if (section === 'variables') { setText(text + '${' + item.name + '}'); close(); textarea.current?.focus(); }
  }
  async function applyMention() {
    if (!pending) return;
    setBusy(true);
    try {
      const cid = await props.ensureConversation(), nextRefs = refs.filter(ref => pending.references.includes(ref.session_id));
      for (const source of pending.references.filter(id => !nextRefs.some(ref => ref.session_id === id))) nextRefs.push(await call('conversation.reference', {conversation_id: cid, source_id: source}));
      await props.preferences({conversation_agent: pending.expert, selected_skill_names: pending.skills, conversation_refs: nextRefs}, cid);
      if (mentionStart >= 0 && latest.current.conversation?.id === cid) setText(text.slice(0, mentionStart) + text.slice(textarea.current?.selectionStart || mentionStart + 1));
      close(); textarea.current?.focus();
    } catch (failure) { setFailure((failure as Error).message); } finally { setBusy(false); }
  }
  async function importFiles(files: File[]) {
    const cid = await props.ensureConversation();
    const added = await receiveAttachments(cid, files);
    if (latest.current.conversation?.id === cid) latest.current.setAttachments([...latest.current.attachments, ...added.filter(item => !latest.current.attachments.some(old => old.path === item.path))]);
  }
  const list = section === 'conversations' ? conversations.filter(item => item.id !== conversation?.id && !item.meta.archived && item.has_messages)
    : section === 'experts' ? [{id: '', name: '默认助手', description: ''}, ...items.filter(item => item.enabled !== false)]
    : section === 'variables' ? items.filter(item => item.kind === 'text') : items.filter(item => item.enabled !== false && item.name.startsWith('mcp-server-') === (section === 'connectors'));
  function selected(item: any) { return section === 'conversations' ? (pending?.references || refs.map(ref => ref.session_id)).includes(item.id) : section === 'experts' ? (pending?.expert || prefs.conversation_agent)?.id === item.id : (pending?.skills || skills).includes(item.name); }
  return <div ref={root} className={'composer-shell ' + (dragging ? 'dragging' : '')} onDragOver={event => {event.preventDefault(); setDragging(true);}} onDragLeave={event => {if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false);}} onDrop={event => {event.preventDefault(); setDragging(false); void importFiles([...event.dataTransfer.files]).catch(failure => error(failure.message));}} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); section && menu === 'add' ? setSection('') : close(); } }}>
    <div className="composer">
      {(skills.length > 0 || refs.length > 0 || knowledge.length > 0 || attachments.length > 0) && <div className="composer-contexts">
        {refs.map(ref => <div className="context-chip conversation-chip" key={ref.id}><MessageSquare size={13}/><button title={'查看引用快照：' + ref.title} onClick={() => {open('add', 'conversations');}}><span>对话 {ref.title}</span></button><button aria-label={'移除引用 ' + ref.title} onClick={() => void applyPreference({conversation_refs: refs.filter(item => item.id !== ref.id)})}><X size={12}/></button></div>)}
        {skills.map(name => <div className="context-chip skill-chip" key={name}>{name.startsWith('mcp-server-') ? <Link size={13}/> : <Puzzle size={13}/>}<span title={name}>{name.startsWith('mcp-server-') ? '连接器' : '技能'} {name}</span><button disabled={busy} aria-label={'移除能力 ' + name} onClick={() => void applyPreference({selected_skill_names: skills.filter(item => item !== name)})}><X size={12}/></button></div>)}
        {knowledge.map((ref, index) => <div className="context-chip knowledge-chip" key={JSON.stringify(ref)}><BookOpen size={13}/><button title={ref.title || ref.name} onClick={() => void props.route('library')}><span>资料 {ref.title || ref.name}</span></button><button aria-label={'移除资料 ' + (ref.title || ref.name)} onClick={() => void applyPreference({knowledge_refs: knowledge.filter((_, i) => i !== index)})}><X size={12}/></button></div>)}
        {attachments.map(file => <div className="context-chip" key={file.path}><Paperclip size={13}/><span title={file.path}>{file.name}</span><button aria-label={'移除附件 ' + file.name} onClick={() => setAttachments(attachments.filter(item => item.path !== file.path))}><X size={12}/></button></div>)}
      </div>}
      <textarea ref={textarea} aria-label="消息输入" placeholder={active ? '补充要求，或留下下一步想法…' : '描述任务，输入 @ 引用会话或选择专家'} value={text} onChange={event => {
        setText(event.target.value);
        if (event.target.value[event.target.selectionStart - 1] === '@') { setMentionStart(event.target.selectionStart - 1); setPending({skills: [...skills], expert: prefs.conversation_agent || {}, references: refs.map(ref => ref.session_id)}); open('mention', 'experts'); }
      }} onKeyDown={event => {if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && !menu) {event.preventDefault(); void props.send().catch(failure => error(failure.message));}}} onPaste={event => {
        const files = [...event.clipboardData.files];
        if (files.length) {event.preventDefault(); void importFiles(files).catch(failure => error(failure.message)); return;}
        const value = event.clipboardData.getData('text/plain');
        if (value.length >= 4000 || value.split('\n').length > 80) {event.preventDefault(); const start = event.currentTarget.selectionStart, end = event.currentTarget.selectionEnd; void importFiles([new File([value], '粘贴文本-' + Date.now() + '.txt', {type: 'text/plain'})]).catch(failure => {latest.current.setText(latest.current.text.slice(0,start) + value + latest.current.text.slice(end)); error('文字附件未保存，原文已保留在输入框。' + failure.message);});}
      }}/>
      <div className="composer-toolbar"><div className="composer-left">
        <button className="icon-button" title="添加上下文" aria-label="添加上下文" aria-expanded={menu === 'add'} onClick={() => menu === 'add' ? close() : open('add')}><Plus size={18}/></button>
        <button className="text-tool" title={settings?.god_mode ? '完全访问' : '请求批准'} onClick={() => menu === 'permission' ? close() : open('permission')}><ShieldCheck size={14}/><span>{settings?.god_mode ? '完全访问' : '请求批准'}</span></button>
        <button className="text-tool" title="选择专家" onClick={() => open('add','experts')}><UserRound size={14}/><span>{prefs.conversation_agent?.name || '专家'}</span></button>
        {prefs.mode === 'grilling' && <div className="mode-chip">{!active && <button aria-label="取消拷问模式" onClick={() => void applyPreference({mode: 'execution', grill_execution_confirmed: false})}><X size={12}/></button>}<button onClick={() => open('add','mode')}>拷问模式</button></div>}
      </div><div className="composer-right"><button className="model-trigger" title={model?.label || '选择模型'} onClick={() => menu === 'model' ? close() : open('model')}><span>{model?.label || '选择模型'}{prefs.reasoning_effort ? ' · ' + prefs.reasoning_effort : ''}</span><ChevronDown size={12}/></button><button className="send-button" title={active && !text.trim() && !attachments.length ? '停止任务' : '发送消息'} disabled={sending || conversation?.run?.status === 'stopping' || !active && !text.trim() && !attachments.length} onClick={() => void (active && !text.trim() && !attachments.length ? props.stop() : props.send()).catch(failure => error(failure.message))}>{sending ? <LoaderCircle className="spin" size={18}/> : active && !text.trim() && !attachments.length ? <Square size={15} fill="currentColor"/> : <ArrowUp size={20}/>}</button></div></div>
    </div>
    <div className="composer-footer"><button title={prefs.workspace_dir || '聊天工作目录'} onClick={async () => {try {const path = await call<string | null>('dialog.workspace'); if (path) {const cid = await props.ensureConversation(); await call('conversation.workspace', {conversation_id: cid, path});}} catch (failure) {error((failure as Error).message);}}}><Folder size={13}/><span>工作目录 · {prefs.workspace_source === 'chat' || !prefs.workspace_dir ? '聊天工作目录' : prefs.workspace_dir.split(/[\\/]/).pop()}</span></button><span>Enter 发送 · Shift+Enter 换行</span></div>
    {menu && <div className={'composer-menu ' + menu + '-menu'} style={{maxHeight:placement.height, ...(placement.above ? {bottom:'calc(100% + 6px)'} : {bottom:'auto',top:'calc(100% + 6px)'})}} role="dialog" aria-label={menu === 'model' ? '选择模型' : menu === 'permission' ? '执行权限' : '添加上下文'}>
      {['add','mention'].includes(menu) ? <>
        <nav className="composer-menu-nav">{categories.filter(([id]) => menu !== 'mention' || ['experts','skills','connectors','conversations'].includes(id)).map(([id,label,Icon]) => <button key={id} className={section === id ? 'selected' : ''} onMouseEnter={() => {clearTimeout(hover.current); if (menu === 'add' && !busy) hover.current = setTimeout(() => {setSection(id); setQuery('');}, 140);}} onMouseLeave={() => clearTimeout(hover.current)} onClick={() => {setSection(id); setQuery('');}} onKeyDown={event => {if (event.key === 'ArrowRight') setSection(id);}}><Icon size={15}/><span>{label}</span><ChevronRight size={13}/></button>)}{menu === 'add' && <><hr/><button onClick={() => {close(); void props.attach();}}><Paperclip size={15}/>添加文件</button>{active && <button onClick={() => {close(); void props.stop();}}><Square size={14}/>停止当前任务</button>}</>}</nav>
        {section && <section className="composer-submenu"><div className="submenu-heading"><button className="icon-button submenu-back" title="返回" onClick={() => setSection('')}><ArrowLeft size={14}/></button><strong>{categories.find(([id]) => id === section)?.[1]}</strong></div>
          {section === 'mode' ? modes.map(([id, label, detail]) => <button className="picker-row" disabled={active || busy} key={id} onClick={() => void applyPreference({mode: id, grill_execution_confirmed: false}, true)}>{id === 'grilling' ? <MessageCircleQuestion size={16}/> : <Play size={16}/>}<span>{label}<small>{detail}</small></span>{(prefs.mode || 'execution') === id && <Check size={14}/>}</button>) : section === 'library' ? <><p className="muted">{knowledge.length ? `已选择 ${knowledge.length} 项资料` : '选择本次任务需要的资料'}</p><button className="picker-row" onClick={() => {close(); void props.route('library');}}><BookOpen size={16}/><span>浏览资料库</span><ChevronRight size={14}/></button></> : <>
            <label className="picker-search"><Search size={14}/><input aria-label="搜索上下文" placeholder="搜索" value={query} onChange={event => setQuery(event.target.value)}/></label>
            {loading ? <div className="picker-empty"><LoaderCircle size={16} className="spin"/>正在读取…</div> : <div className="picker-list">{list.filter(item => [item.name,item.display_name,item.title,item.description].some(part => String(part || '').toLocaleLowerCase().includes(query.toLocaleLowerCase()))).map(item => <button className={'picker-row ' + (selected(item) ? 'checked' : '')} aria-pressed={selected(item)} disabled={busy} key={item.id || item.name} onClick={() => void select(item)}><span>{item.display_name || item.title || item.name}<small>{item.description || ''}</small></span>{selected(item) && <Check size={14}/>}</button>)}{!list.length && <p className="picker-empty">暂无可选内容</p>}</div>}
          </>}{failure && <p role="alert" className="error-text">{failure}</p>}
        </section>}
        {pending && <div className="mention-actions"><button className="secondary" onClick={close}>取消</button><button className="primary" disabled={busy} onClick={() => void applyMention()}>应用选择</button></div>}
      </> : menu === 'permission' ? <section className="single-menu">{[[false, '请求批准', '需要授权的操作先询问你'], [true, '完全访问', '在当前用户权限范围内直接执行']].map(([enabled, label, detail]) => <button key={String(enabled)} className="picker-row" disabled={busy} aria-pressed={Boolean(settings?.god_mode) === enabled} onClick={async () => {setBusy(true); try {await props.permission(Boolean(enabled)); close();} catch (failure) {setFailure((failure as Error).message);} finally {setBusy(false);}}}><ShieldCheck size={16}/><span>{label}<small>{detail}</small></span>{Boolean(settings?.god_mode) === enabled && <Check size={14}/>}</button>)}{failure && <p className="error-text">{failure}</p>}</section> : <section className="single-menu"><label className="picker-search"><Search size={14}/><input aria-label="搜索模型" placeholder="搜索模型" value={query} onChange={event => setQuery(event.target.value)}/></label><div className="picker-list">{[...new Set(settings?.models.map(item => item.channel_id))].map(channel => {
        const models = settings?.models.filter(item => item.channel_id === channel && [item.label, item.model_name].some(text => text.toLocaleLowerCase().includes(query.toLocaleLowerCase()))) || [];
        return models.length > 0 && <div key={channel}><div className="model-group">{models[0].channel_display_name}</div>{models.map(item => <button className="picker-row" key={item.id} disabled={busy} aria-pressed={item.id === model?.id} onClick={async () => {setBusy(true); try {await props.selectModel(item.id); setFailure('');} catch (failure) {setFailure((failure as Error).message);} finally {setBusy(false);}}}><span>{item.display_name || item.model_name}{!item.configured && <small>未配置</small>}</span>{item.id === model?.id && <Check size={14}/>}</button>)}</div>;
      })}</div><label className="reasoning-select">推理深度<select disabled={busy} value={prefs.reasoning_effort || ''} onChange={event => void applyPreference({reasoning_effort: event.target.value})}>{['', ...new Set([...(model?.reasoning_efforts || ['none','low','medium','high','xhigh','max']), ...(prefs.reasoning_effort ? [prefs.reasoning_effort] : [])])].map(level => <option key={level} value={level}>{({none:'关闭',low:'低',medium:'中',high:'高',xhigh:'更高',max:'最高'} as Record<string,string>)[level] || level || '服务默认'}</option>)}</select></label>{failure && <p className="error-text">{failure}</p>}<button className="picker-row" onClick={() => {close(); void props.route('settings');}}>管理模型与服务<ChevronRight size={14}/></button></section>}
    </div>}
  </div>;
}
