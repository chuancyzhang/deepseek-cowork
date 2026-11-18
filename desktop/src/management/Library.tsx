import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, BookOpen, ChevronRight, ExternalLink, FileText, Folder, FolderPlus, HardDrive, Link2, LogOut, Plus, RefreshCw, Search, Upload } from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Empty, ErrorState, Field, JobProgress } from '../Management';
import { operation, rpc } from '../managementApi';
import { useHostField } from '../host/context';
import {useWorkbench} from '../host/Host';
import { appendLibraryRows, libraryCollections, librarySpaces } from './libraryCatalog';

const sources = [['local','本地资料库'], ['weknora','WeKnora'], ['tencent-docs','腾讯文档'], ['lexiang','腾讯乐享']];
function list(value: any): any[] { if (Array.isArray(value)) return value; if (!value || typeof value !== 'object') return []; for (const key of ['data','items','knowledge','files','results','documents','list']) if (value[key]) { const items = list(value[key]); if (items.length) return items; } return []; }
function title(item: any) {return item?.title || item?.name || item?.file_name || '未命名资料';}
function date(value: any) {if (!value) return '—'; const d = new Date(typeof value === 'number' && value < 1e12 ? value*1000 : value); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('zh-CN');}
type Props = { scope?: string; projectsKey: string; onError: (message: string) => void; onReference: (ref: any) => Promise<void>; confirm: (title: string, detail: string, choices: string[]) => Promise<number>; onDirty: (dirty: boolean) => void };
export default function Library(props: Props) {
  const [source, setSource] = useHostField((props.scope || 'library') + ':source', 'local');
  return <LibrarySource key={source} {...props} source={source} setSource={setSource}/>;
}
function LibrarySource({source, setSource, onError, onReference, confirm, projectsKey, scope = 'library'}: Props & {source: string; setSource: (source: string) => void}) {
  const {addProject} = useWorkbench();
  const local = source === 'local', sourceName = sources.find(item => item[0] === source)?.[1] || '';
  const [status, setStatus] = useState<any>(null);
  const [view, setView] = useHostField<any>(scope + ':view:' + source, {catalog:null, collection:null, parents:[], rows:[], page:1, more:false, query:'', searching:false, loaded:false, scroll:0, unavailable:[]});
  const {catalog, collection, parents, rows, page, more, query, searching} = view;
  const category = view.category || 'all';
  const directory = !collection && !searching;
  const publicFiles = local && collection?.id === 'public';
  const [folderName, setFolderName] = useState(''), [creatingFolder, setCreatingFolder] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState(''), [job, setJob] = useState<any>(null);
  const [form, setForm] = useState({url:'', email:'', password:'', company:'', token:''}), [authUrl, setAuthUrl] = useState('');
  const [preview, setPreview] = useState<any>(null), [notice, setNotice] = useState('');
  const [reading, setReading] = useState(false), [readJob, setReadJob] = useState<any>(null);
  const readRequest = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const generation = useRef(0), scroller = useRef<HTMLDivElement>(null);
  const update = (patch: any) => setView((previous:any) => ({...previous, ...patch}));
  const connected = local || Boolean(status?.sources[source] && !status.sources[source].unavailable);
  async function loadStatus() { const value = await rpc('library.status'); if(alive.current)setStatus(value); return value; }
  useEffect(() => {alive.current=true;if(!local) void loadStatus().catch(error => {if(alive.current)setFailure(error.message);}); return () => {alive.current=false;++generation.current;readRequest.current?.abort();};}, []);
  function leavePreview() {readRequest.current?.abort();readRequest.current=null;setReading(false);setReadJob(null);setPreview(null);setFailure('');}
  useEffect(() => {if (!notice) return; const timer=setTimeout(()=>setNotice(''),6000); return ()=>clearTimeout(timer);},[notice]);
  async function execute(method: string, params: any = {}) {
    if(!alive.current)return;
    const current = ++generation.current;
    setBusy(true); setFailure('');
    try {const result=await operation(method, {source,...params}, value=>{if(current===generation.current)setJob(value);}); return current===generation.current ? result : undefined;}
    catch(error) {if(current===generation.current)setFailure((error as Error).message);}
    finally {if(current===generation.current){setBusy(false);setJob(null);}}
  }
  async function catalogLoad() {
    const result = await execute('library.catalog');
    if (result) update({catalog:result});
    return result;
  }
  async function children(target = collection, trail = parents, nextPage = 1, search = '') {
    leavePreview();
    if (!target?.id && !search) {update({rows:[], page:1, more:false, searching:false});return;}
    const value=await execute(search ? 'library.search' : 'library.children', {collection:target?.id || '',parent:trail.at(-1)?.id || '',page:nextPage, ...(search ? {query:search} : {})});
    if (!value) return;
    const items = search ? (local ? value.items : list(value)) : local || source === 'weknora' ? value.items : value[0];
    update({collection:target,parents:trail,page:nextPage,rows:nextPage===1?items:appendLibraryRows(rows,items),more:search&&!local?false:local||source==='weknora'?value.has_more:Boolean(value[1]),loaded:true,searching:Boolean(search),unavailable:value.unavailable || [],total:value.total,scroll:nextPage===1?0:view.scroll});
    if(nextPage===1&&scroller.current)scroller.current.scrollTop=0;
  }
  useEffect(()=>{if(connected) void (async()=>{
    const result=await catalogLoad();
    if(!result)return;
    if(local) {
      const retained=collection && [result.public,...(result.mine || [])].some((item:any)=>item?.id === collection.id);
      const target = retained ? collection : !view.loaded ? result.public : null;
      update({projectsKey, loaded:true, ...(retained ? {} : {collection:target,parents:[],rows:[],more:false,page:1})});
      if(target && (!view.loaded || view.projectsKey !== projectsKey)) await children(target,retained ? parents : [],1,searching ? query : '');
    }
  })();},[connected,projectsKey]);
  useEffect(()=>{if(scroller.current)scroller.current.scrollTop=view.scroll || 0;},[]);
  async function selectCollection(item: any) {setPreview(null);update({query:''});await children(item,[]);}
  async function reference(item: any, entire=false) {
    const scope=generation.current;
    const ref=await execute('library.reference',{collection:item.collection_id || item.knowledge_base_id || collection?.id || item.id,item:entire?'':item.item_id || item.id,title:title(item)});
    if(ref) {try {await onReference(ref);if(generation.current===scope+1)setNotice('已添加到当前对话：'+title(item));} catch(error) {if(generation.current===scope+1)setFailure((error as Error).message);}}
  }
  async function read(item: any) {
    leavePreview();
    const request = new AbortController();readRequest.current=request;setReading(true);
    setPreview({...item,data:null});
    try {
      const data=await operation('library.read',{source,collection:item.collection_id || item.knowledge_base_id || collection?.id,item:item.item_id || item.id},value=>{if(!request.signal.aborted)setReadJob(value);},request.signal);
      if(!request.signal.aborted&&alive.current)setPreview({...item,data});
    } catch(error) {if(!request.signal.aborted&&alive.current)setFailure((error as Error).message);}
    finally {if(!request.signal.aborted&&alive.current){setReading(false);setReadJob(null);}}
  }
  async function external(item: any) {try{await rpc(local?'library.external':'external.open',local?{source,collection:item.collection_id,item:item.id}:{url:item.url});}catch(error){setFailure((error as Error).message);}}
  const spaces=librarySpaces(catalog);
  const collections=libraryCollections(catalog, local ? 'all' : category);
  const visibleCollections=local && query.trim() ? collections.filter(item=>[title(item),item.path].join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) : collections;
  const categoryTitle=local?'项目资料':category==='mine'?'我的资料':category==='shared'?'共享资料':category.startsWith('org:')?spaces.find(item=>'org:'+item.id===category)?.name || '共享空间':'全部资料空间';
  const all=async(nextCategory=category)=>{++generation.current;setBusy(false);setJob(null);leavePreview();setCreatingFolder(false);update({category:nextCategory,query:'',collection:null,parents:[],rows:[],page:1,more:false,searching:false,scroll:0,unavailable:[],loaded:true});};
  async function importFiles(paths:string[]) {
    if(!paths.length || !publicFiles)return;
    const result=await execute('library.import',{collection:'public',parent:parents.at(-1)?.id || '',paths});
    if(result){await children();setNotice(`已添加 ${result.added.length} 个文件`);if(result.failed.length)setFailure(result.failed.map((item:any)=>item.name+'：'+item.reason).join('；'));}
  }
  async function openFolder(){try{await rpc('library.open_folder',{source:'local',collection:collection.id,parent:parents.at(-1)?.id || ''});}catch(error){setFailure((error as Error).message);}}
  return <div className="library-space">
    <nav className="library-spaces" aria-label="资料空间"><label className="library-source-select">{local?<HardDrive size={17}/>:<BookOpen size={17}/>}<select aria-label="选择资料空间" value={source} onChange={event=>setSource(event.target.value)}>{sources.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
      {connected&&<div className="library-collections">
        {local ? <><button className={publicFiles?'selected':''} aria-current={publicFiles?'page':undefined} onClick={()=>catalog?.public&&void selectCollection(catalog.public)}><Folder size={16}/>公共资料</button><button className={!publicFiles?'selected':''} aria-current={!publicFiles?'page':undefined} onClick={()=>void all()}><BookOpen size={16}/>项目资料</button></> : <>
          <button className={directory&&category==='all'?'selected':''} onClick={()=>void all('all')}><Folder size={16}/>全部资料空间</button>
          <button className={directory&&category==='mine'?'selected':''} onClick={()=>void all('mine')}><BookOpen size={16}/>我的资料</button>
          <div className="library-collection-group"><h3>共享空间</h3>{spaces.map(item=><button key={item.id} className={category==='org:'+item.id?'selected':''} onClick={()=>void all('org:'+item.id)}><Folder size={16}/><span>{item.name}</span></button>)}{!spaces.length&&<button className={category==='shared'?'selected':''} onClick={()=>void all('shared')}>共享资料</button>}</div>
        </>}
      </div>}
    </nav>
    <section className="library-main" aria-label={sourceName}>
      {connected&&<header className="library-heading"><div><h2>{preview?title(preview):parents.length?title(parents.at(-1)):collection?title(collection):categoryTitle}</h2></div><div className="button-row">
        {!preview&&<button className="icon-button" aria-label="刷新资料" title="刷新资料" disabled={busy} onClick={()=>void (async()=>{await catalogLoad();if(collection||searching)await children(collection,parents,1,searching?query:'');})()}><RefreshCw size={17}/></button>}
        {!preview&&local&&!publicFiles&&<button className="secondary" onClick={() => addProject(false)}><FolderPlus size={16}/>新建项目</button>}
        {!preview&&publicFiles&&<><button className="secondary" disabled={busy} onClick={()=>void openFolder()}><ExternalLink size={16}/>打开文件夹</button><button className="secondary" disabled={busy} onClick={()=>setCreatingFolder(!creatingFolder)}><FolderPlus size={16}/>新建文件夹</button><button className="primary" disabled={busy} onClick={async()=>{try{const paths=await rpc('dialog.choose',{kind:'files'});await importFiles(paths || []);}catch(error){setFailure((error as Error).message);}}}><Plus size={16}/>添加文件</button></>}
        {!preview&&!local&&connected&&<button className="icon-button" title="断开连接" aria-label="断开连接" disabled={busy} onClick={async()=>{if(await confirm('断开'+sourceName+'？','已选资料仍保留，重新连接后可继续使用。',['取消','断开'])===1){await execute('library.logout');await loadStatus();update({catalog:null,collection:null,rows:[],loaded:false});}}}><LogOut size={17}/></button>}
      </div></header>}
      <JobProgress job={readJob || job}/>{failure&&<ErrorState message={failure}/>} {notice&&<p className="library-notice" role="status">{notice}</p>}
      {!connected ? <div className="library-connection"><Empty icon={BookOpen} title={'连接'+sourceName} detail=""/>{source==='tencent-docs'?<div className="button-row"><button className="primary" disabled={busy} onClick={async()=>{const result=await execute('library.authorize');if(result){setAuthUrl(result.url);await rpc('external.open',{url:result.url});}}}><ExternalLink size={16}/>在浏览器授权</button>{authUrl&&<button className="secondary" disabled={busy} onClick={async()=>{await execute('library.confirm');await loadStatus();}}>已完成授权</button>}</div>:<form onSubmit={async e=>{e.preventDefault();const result=await execute('library.login',form);if(result){setForm({...form,password:'',token:''});await loadStatus();}}}><div className="form-grid">{(source==='weknora'?[['url','服务地址','url'],['email','邮箱','email'],['password','密码','password']]:[['company','企业标识','text'],['token','访问令牌','password']]).map(([key,label,type])=><Field key={key} label={label} wide><input required type={type} autoComplete="off" value={(form as any)[key]} onChange={e=>setForm({...form,[key]:e.target.value})}/></Field>)}</div><button className="primary" disabled={busy}><Link2 size={16}/>连接</button></form>}</div> : <>
        {preview&&<div className="library-document"><div className="library-document-tools"><button className="back-link" onClick={leavePreview}><ArrowLeft size={16}/>返回列表</button><div className="button-row">{(local||preview.url)&&<button className="secondary" onClick={()=>void external(preview)}><ExternalLink size={15}/>{local?'在本机打开':'在原平台打开'}</button>}<button className="primary" disabled={busy} onClick={()=>void reference(preview)}><Plus size={16}/>添加到当前对话</button></div></div><div className="library-document-content">{preview.data?<LibraryPreview data={preview.data}/>:<p className="muted" role="status">{reading?'正在读取资料…':'读取未完成，可返回列表重试。'}</p>}</div></div>}
        <div className={'library-list-view'+(dragging?' library-dragging':'')} hidden={Boolean(preview)} onDragOver={event=>{if(publicFiles&&!busy&&event.dataTransfer.types.includes('Files')){event.preventDefault();setDragging(true);}}} onDragLeave={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node))setDragging(false);}} onDrop={event=>{event.preventDefault();setDragging(false);if(publicFiles&&!busy)void importFiles(Array.from(event.dataTransfer.files).map(file=>window.cowork.pathForFile(file)).filter(Boolean));}}>
          {creatingFolder&&publicFiles&&<form className="page-search" onSubmit={async event=>{event.preventDefault();const result=await execute('library.mkdir',{collection:'public',parent:parents.at(-1)?.id || '',name:folderName});if(result){setCreatingFolder(false);setFolderName('');await children();}}}><input autoFocus required aria-label="文件夹名称" placeholder="文件夹名称" value={folderName} onChange={event=>setFolderName(event.target.value)}/><button type="button" onClick={()=>setCreatingFolder(false)}>取消</button><button disabled={busy}>创建</button></form>}
          {dragging&&<p role="status">松开以复制到公共资料，原文件保留</p>}
          <form className="page-search" onSubmit={e=>{e.preventDefault();if(directory&&(local||!query.trim())){return;}void children(collection,[],1,query.trim());}}><Search size={17}/><input aria-label="搜索资料" placeholder={local?(directory?'搜索项目':'搜索当前范围的文件名'):'搜索'+sourceName+'资料'} value={query} onChange={e=>update({query:e.target.value})}/><button disabled={busy}>搜索</button></form>
          {((collection&&(!publicFiles||parents.length>0))||searching)&&<div className="library-breadcrumb">{!publicFiles&&<button onClick={()=>void all()}>{categoryTitle}</button>}{collection&&<>{!publicFiles&&<ChevronRight size={14}/>}<button disabled={busy} onClick={()=>void children(collection,[])}>{title(collection)}</button></>}{parents.map((parent:any,i:number)=><span key={parent.id}><ChevronRight size={14}/><button disabled={busy} onClick={()=>void children(collection,parents.slice(0,i+1))}>{title(parent)}</button></span>)}{searching&&<span>／ 搜索结果</span>}</div>}
          {collection?.id&&!local&&<div className="button-row library-list-actions"><button className="secondary" disabled={busy} onClick={()=>void reference(collection,true)}><Plus size={15}/>引用整个资料库</button><button className="secondary" disabled={busy} onClick={async()=>{const path=await rpc('dialog.choose',{kind:'file'});if(path){const result=await execute('library.upload',{path,collection:collection.id,parent:parents.at(-1)?.id||''});if(result){onError('上传已提交，可在后台操作中核对结果。');await children();}}}}><Upload size={15}/>上传文件</button></div>}
          {view.unavailable?.length>0&&<p className="error-text">以下项目有目录不可用，列表可能不完整：{view.unavailable.join('、')}。恢复目录后刷新。</p>}
          <div ref={scroller} className="library-list-scroll" onScroll={e=>update({scroll:e.currentTarget.scrollTop})}>
            <div className="library-columns"><span>名称</span><span>{local&&directory?'位置':!local&&directory?'所属空间':'类型'}</span><span>更新时间</span><span/></div>
            {(directory?visibleCollections:rows).map((item:any,index:number)=>{const folder=directory||item.is_folder;return <div className="library-row" key={item.id||item.item_id||index}><button className="library-item-name" disabled={busy} title={item.relative||title(item)} onClick={()=>void (directory?selectCollection(item):folder?children(collection,[...parents,item]):read(item))}>{folder?<Folder size={19}/>:<FileText size={19}/>}<span>{title(item)}{local&&item.relative&&item.relative!==title(item)&&<small>{item.relative}</small>}</span></button><span className="library-item-meta">{local?(directory?item.path:folder?'文件夹':item.type||'文件'):folder?(directory?(item.sharedWith.join('、') || (item.group==='mine'?'我的资料':'共享资料')):'文件夹'):item.file_type||item.type||'文档'}</span><time>{date(item.updated_at||item.update_time||item.modified_time)}</time><div className="button-row">{folder?<ChevronRight size={16}/>:<button className="icon-button" title="添加到当前对话" aria-label={'添加到当前对话：'+title(item)} disabled={busy} onClick={()=>void reference(item)}><Plus size={17}/></button>}</div></div>})}
            {!busy&&!(directory?visibleCollections:rows).length&&<Empty title={searching?'没有找到资料':publicFiles?'添加文件，开始整理公共资料':local&&directory?'添加项目，开始整理资料':'这里还没有资料'} detail={searching?'换一个关键词，或返回上级范围。':publicFiles?'拖入文件或点击添加文件，所有项目和独立聊天均可引用。':local?'添加项目目录后，在这里浏览资料。':'可以刷新列表，或选择其他资料空间。'}/>}
            {busy&&!job&&<p className="muted" role="status">正在读取…</p>}{!directory&&more&&<button className="secondary library-load-more" disabled={busy} onClick={()=>void children(collection,parents,page+1,searching?query:'')}>加载更多</button>}
          </div>
        </div>
      </>}
    </section>
  </div>;
}
function LibraryPreview({data}:{data:any}) {
  const kind=data.payload?.preview_kind;
  if(kind==='external')return <Empty icon={FileText} title="在本机应用中查看这份资料" detail="可以打开原文件，或将它添加到当前对话。"/>;
  if(kind==='pdf')return <iframe title="资料预览" src={data.payload.url+'#navpanes=0&view=FitH'}/>;
  if(kind==='image')return <img className="library-preview-image" alt="资料预览" src={data.payload.url}/>;
  if(kind==='html')return <iframe title="资料预览" sandbox="allow-scripts" src={data.payload.url}/>;
  if(data.html)return <iframe title="资料预览" sandbox="" referrerPolicy="no-referrer" srcDoc={'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: https:; style-src \'unsafe-inline\'">'+data.html}/>;
  const text=typeof data==='string'?data:data.text||data.content||JSON.stringify(data,null,2);
  return data.format==='text'?<pre>{text}</pre>:<article className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{typeof text==='string'?text:JSON.stringify(text,null,2)}</Markdown></article>;
}
