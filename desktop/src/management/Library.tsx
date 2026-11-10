import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, BookOpen, Check, ChevronRight, ExternalLink, FileText, Folder, Link2, LoaderCircle, LogOut, Plus, RefreshCw, Search, Upload, X } from 'lucide-react';
import { Empty, ErrorState, Field, JobProgress, PageHead } from '../Management';
import { operation, rpc } from '../managementApi';

const sources = [['weknora','WeKnora'], ['tencent-docs','腾讯文档'], ['lexiang','腾讯乐享']];
function list(value: any): any[] { if (Array.isArray(value)) return value; if (!value || typeof value !== 'object') return []; for (const key of ['data','items','knowledge','files','results','documents','list']) if (value[key]) { const items = list(value[key]); if (items.length) return items; } return []; }
export default function Library({ onError, onReference, confirm }: { onError: (message: string) => void; onReference: (ref: any) => Promise<void>; confirm: (title: string, detail: string, choices: string[]) => Promise<number>; onDirty: (dirty: boolean) => void }) {
  const [status, setStatus] = useState<any>(null), [source, setSource] = useState('weknora'), [catalog, setCatalog] = useState<any>(null);
  const [collection, setCollection] = useState<any>(null), [parents, setParents] = useState<any[]>([]), [rows, setRows] = useState<any[]>([]);
  const [page, setPage] = useState(1), [more, setMore] = useState(false), [query, setQuery] = useState(''), [busy, setBusy] = useState(false), [failure, setFailure] = useState(''), [job, setJob] = useState<any>(null);
  const [login, setLogin] = useState(false), [form, setForm] = useState({ url: '', email: '', password: '', company: '', token: '' }), [authUrl, setAuthUrl] = useState(''), [preview, setPreview] = useState<any>(null);
  const generation = useRef(0);
  async function loadStatus() { const value = await rpc('library.status'); setStatus(value); return value; }
  useEffect(() => { void loadStatus().then(value => setSource(value.active)).catch(error => setFailure(error.message)); }, []);
  async function execute(method: string, params: any = {}) {
    const current = generation.current;
    setBusy(true); setFailure('');
    try { const result = await operation(method, { source, ...params }, setJob); return current === generation.current ? result : undefined; }
    catch (error) { if (current === generation.current) setFailure((error as Error).message); }
    finally { if (current === generation.current) { setBusy(false); setJob(null); } }
  }
  const connected = Boolean(status?.sources[source] && !status.sources[source].unavailable);
  useEffect(() => { ++generation.current; setCatalog(null); setCollection(null); setRows([]); setParents([]); setPreview(null); setLogin(false); setAuthUrl(''); setFailure('');
    if (connected) void execute('library.catalog').then(value => { if (value) setCatalog(value); });
  }, [source, connected]);
  async function children(target = collection, trail = parents, nextPage = 1) {
    const value = await execute('library.children', { collection: target.id, parent: trail.at(-1)?.id || '', page: nextPage });
    if (value) {
      setCollection(target); setParents(trail); setPage(nextPage);
      const items = source === 'weknora' ? [...list(value.folders?.folders).map(item => ({ ...item, is_folder: true })), ...list(value.files)] : value[0];
      setRows(nextPage === 1 ? items : [...rows, ...items]); setMore(source === 'weknora' ? list(value.files).length >= 30 : Boolean(value[1]));
    }
  }
  async function reference(item: any, entire = false) {
    const ref = await execute('library.reference', { collection: item.collection_id || item.knowledge_base_id || collection?.id || item.id,
      item: entire ? '' : item.item_id || item.id, title: item.title || item.name });
    if (ref) { await onReference(ref); onError('资料已添加到当前对话，下次发送时生效。'); }
  }
  return <><PageHead title="资料库" detail="浏览资料，选择本次任务需要的来源。"><button className="icon-button" title="刷新" disabled={busy} onClick={() => collection ? void children() : void execute('library.catalog').then(setCatalog)}><RefreshCw size={17}/></button>
    {connected && <button className="secondary" onClick={async () => { if (await confirm('断开当前资料来源？', '已选择的资料仍保留，重新连接原账号后可以继续使用。', ['取消','断开']) === 1) { await execute('library.logout'); await loadStatus(); } }}><LogOut size={16}/>断开</button>}</PageHead>
    <div className="segmented">{sources.map(([id,name]) => <button key={id} className={source === id ? 'selected' : ''} onClick={() => { setSource(id); void rpc('library.select', { source: id }); }}>{name}</button>)}</div>
    <JobProgress job={job}/>{failure && <ErrorState message={failure}/>}
    {!connected ? <section className="connection-form"><Empty icon={BookOpen} title={'连接' + sources.find(item => item[0] === source)?.[1]} detail="连接后可浏览目录、引用资料和上传文件。"/>
      {source === 'tencent-docs' ? <div className="button-row"><button className="primary" disabled={busy} onClick={async () => { const result = await execute('library.authorize'); if (result) { setAuthUrl(result.url); await rpc('external.open', { url: result.url }); } }}><ExternalLink size={16}/>在浏览器授权</button>{authUrl && <button className="secondary" disabled={busy} onClick={async () => { await execute('library.confirm'); await loadStatus(); }}>已完成授权</button>}</div> :
        <form onSubmit={async e => { e.preventDefault(); const result = await execute('library.login', form); if (result) { setForm({ ...form, password: '', token: '' }); await loadStatus(); } }}><div className="form-grid">{(source === 'weknora' ? [['url','服务地址','url'],['email','邮箱','email'],['password','密码','password']] : [['company','企业标识','text'],['token','访问令牌','password']]).map(([key,label,type]) => <Field key={key} label={label} wide><input required type={type} autoComplete="off" value={(form as any)[key]} onChange={e => setForm({ ...form, [key]: e.target.value })}/></Field>)}</div><button className="primary" disabled={busy}><Link2 size={16}/>连接</button></form>}
    </section> : <>
      <form className="page-search" onSubmit={async e => { e.preventDefault(); if (!query.trim()) { if (collection) await children(); return; } const value = await execute('library.search', { query }); if (value) { setRows(list(value)); setCollection({ id: '', name: '搜索结果' }); setParents([]); setMore(false); } }}><Search size={17}/><input aria-label="搜索资料" placeholder="搜索资料" value={query} onChange={e => setQuery(e.target.value)}/><button disabled={busy}>搜索</button></form>
      {collection ? <>
        <div className="library-breadcrumb"><button onClick={() => { setCollection(null); setParents([]); setRows([]); }}><BookOpen size={16}/>全部资料库</button><ChevronRight size={14}/><button onClick={() => void children(collection, [])}>{collection.name}</button>{parents.map((parent,i) => <span key={parent.id}><ChevronRight size={14}/><button onClick={() => void children(collection, parents.slice(0,i+1))}>{parent.title || parent.name}</button></span>)}</div>
        {collection.id && <div className="button-row"><button className="secondary" onClick={() => void reference(collection, true)}><Plus size={15}/>引用整个资料库</button><button className="secondary" disabled={busy} onClick={async () => { const path = await rpc('dialog.choose', { kind: 'file' }); if (path) { const result = await execute('library.upload', { path, collection: collection.id, parent: parents.at(-1)?.id || '' }); if (result) { onError('上传已提交，可在后台操作中核对结果。'); await children(); } } }}><Upload size={15}/>上传文件</button></div>}
        {!rows.length && !busy && <Empty title="这里还没有资料" detail="可以返回上级目录，或上传需要使用的文件。"/>}
        <div className="resource-list">{rows.map((item, index) => <div className="model-row" key={item.id || item.item_id || index}>{item.is_folder ? <Folder size={20}/> : <FileText size={20}/>}<button className="resource-title grow" onClick={async () => { if (item.is_folder) await children(collection, [...parents,item]); else { const data = await execute('library.read', { collection: item.collection_id || collection.id, item: item.item_id || item.id }); if (data) setPreview({ ...item, data }); } }}>{item.title || item.name || item.file_name}</button><button className="icon-button" title="添加到当前对话" onClick={() => void reference(item)}><Plus size={17}/></button>{item.url && <button className="icon-button" title="在原平台打开" onClick={() => void rpc('external.open', { url: item.url })}><ExternalLink size={16}/></button>}</div>)}</div>
        {more && <button className="secondary" disabled={busy} onClick={() => void children(collection, parents, page+1)}>加载更多</button>}
      </> : <div className="capability-grid">{['mine','others','shared'].flatMap(group => (catalog?.[group] || []).map((item: any) => <button className="library-collection" key={group+item.id} onClick={() => void children(item, [])}><BookOpen size={24}/><span><strong>{item.name || item.title}</strong><small>{({ mine:'我的资料',others:'其他资料',shared:'共享资料' } as any)[group]}</small></span><ChevronRight size={16}/></button>))}</div>}
    </>}
    {preview && <section className="library-preview"><div className="section-heading"><h3>{preview.title || preview.name}</h3><button className="icon-button" title="关闭预览" onClick={() => setPreview(null)}><X size={17}/></button></div>{preview.data.html ? <iframe sandbox="" title="资料预览" referrerPolicy="no-referrer" srcDoc={'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: https:; style-src \'unsafe-inline\'">' + preview.data.html}/> : <pre>{typeof preview.data === 'string' ? preview.data : preview.data.text || preview.data.content || JSON.stringify(preview.data, null, 2)}</pre>}<button className="primary" onClick={() => void reference(preview)}><Plus size={16}/>引用这份资料</button></section>}
  </>;
}
