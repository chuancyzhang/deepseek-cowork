import {useEffect, useRef, useState} from 'react';
import {Check, FileText, Folder, LoaderCircle, Pencil, RefreshCw, Square} from 'lucide-react';
import type {Conversation, FileSession} from './types';
import {Markdown} from './MessageBubble';
import FilePreview from './FilePreview';
import OfficeEditor from './OfficeEditor';
import type {ReviewEdit} from './reviews';

const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
const active = (run: any) => ['queued','running','stopping'].includes(run?.status);
type Props = {
  cid: string; id: string; conversation: Conversation; edit?: ReviewEdit;
  change: (edit: ReviewEdit) => void; openReview: (id: string) => void; openFile: (path: string) => Promise<void>;
  saved: () => void; register: (path: string, flush?: () => Promise<string>) => void;
  confirm: (title: string, detail: string, choices: string[]) => Promise<number>;
};

export default function ReviewWorkspace(props: Props) {
  const {cid, id, edit, change, confirm} = props;
  const [entry, setEntry] = useState<any>(null), [failure, setFailure] = useState(''), [busy, setBusy] = useState('');
  const [setup, setSetup] = useState(false), [editing, setEditing] = useState(false), [inventory, setInventory] = useState<any[]>([]);
  const [file, setFile] = useState<FileSession | null>(null);
  const alive = useRef(true), sequence = useRef(0), fileSequence = useRef(0), flush = useRef<(() => Promise<string>) | undefined>(undefined);
  const latest = useRef({props, entry, file}); latest.current = {props, entry, file};
  const dirty = Boolean(edit && (Object.keys(edit.patch).length || edit.officeText));
  const conflict = dirty && entry && edit!.revision !== entry.revision;
  const data = entry ? {...entry, ...edit?.patch} : null;
  const content = data?.content || {};
  const generating = entry?.status === 'generating' && active(entry?.run);
  const preparing = entry && (entry.status === 'setup' || setup || entry.status === 'generating');

  async function refresh() {
    const token = ++sequence.current;
    const result = await call('reviews.get', {conversation_id:cid, id});
    if (alive.current && token === sequence.current) setEntry(result);
    return result;
  }
  useEffect(() => {
    alive.current = true;
    void refresh().catch(error=>setFailure(error.message));
    const off = window.cowork.onEvents(events=>{
      if (events.some(event=>event.event === 'service_reconnected' || event.conversation_id === cid && (event.event === 'review_changed' && event.data?.id === id || event.event === 'conversation_changed' || event.event === 'runtime'))) {
        // Runtime events arrive in batches; only terminal run transitions need
        // another read. Streaming tokens must not trigger a full RPC per delta.
        if (events.some(event=>event.event !== 'runtime' || event.run && !active(event.run))) void refresh().catch(error=>{if(alive.current)setFailure(error.message);});
      }
    });
    return ()=>{alive.current=false;sequence.current++;fileSequence.current++;off();};
  }, [cid,id]);
  useEffect(()=>{
    if (entry?.kind === 'skill') {
      let current = true;
      void call<any[]>('skills.inventory').then(items=>{if(current)setInventory(items.filter(item=>item.editable));}).catch(error=>{if(current)setFailure(error.message);});
      return ()=>{current=false;};
    }
  }, [entry?.kind]);
  useEffect(()=>{
    if (entry?.kind === 'office' && entry.file_path && entry.status !== 'saved') void readFile(false).catch(error=>{if(alive.current)setFailure(error.message);});
  }, [entry?.file_path]);
  useEffect(()=>{if(entry?.status === 'ready'){setSetup(false);}},[entry?.status]);

  function patch(value: Record<string, any>) {
    const state = latest.current, local = state.props.edit;
    state.props.change({...local, revision:local && (Object.keys(local.patch).length || local.officeText) ? local.revision : state.entry.revision, patch:{...local?.patch,...value}});
  }
  function officeText(text: string) {
    const state=latest.current, local=state.props.edit;
    state.props.change({...local, revision:local && (Object.keys(local.patch).length || local.officeText) ? local.revision : state.entry.revision,
      patch:local?.patch || {}, officeText:text, filePath:state.file?.path, fingerprint:state.file?.fingerprint});
  }
  async function readFile(discard: boolean, ask = true) {
    const token=++fileSequence.current, local=latest.current.props.edit;
    if (discard && ask && local?.officeText && await confirm('重新载入草稿文件？','将用文件中的内容替换表格编辑。可以取消并先保存草稿。',['取消','重新载入']) !== 1) return;
    const value=await call<FileSession>('reviews.open',{conversation_id:cid,id,...(!discard && local?.officeText ? {fingerprint:local.fingerprint} : {})});
    if (!alive.current || token!==fileSequence.current) return;
    setFile(value);
    const current = latest.current.props.edit;
    change({revision:current?.revision ?? latest.current.entry.revision,patch:current?.patch || {},officeText:discard?'':current?.officeText,filePath:value.path,fingerprint:value.fingerprint});
    return value;
  }
  async function loadLatest() {
    if(await confirm('载入最新草稿？','将替换当前尚未保存的编辑。可以取消并先复制内容。',['取消','载入最新草稿'])!==1)return;
    setBusy('reload');setFailure('');
    try {
      const opened=entry.kind==='office'?await readFile(true,false):undefined;
      if(alive.current)change({revision:entry.revision,patch:{},...(opened?{filePath:opened.path,fingerprint:opened.fingerprint}:{})});
    }catch(error){if(alive.current)setFailure((error as Error).message);}finally{if(alive.current)setBusy('');}
  }
  async function persist() {
    const state = latest.current, local=state.props.edit;
    if (local && (Object.keys(local.patch).length || local.officeText) && local.revision!==state.entry.revision) throw new Error('草稿已更新，当前编辑仍保留。请先核对最新版本。');
    const text = flush.current ? await flush.current() : local?.officeText || '';
    if (!Object.keys(local?.patch || {}).length && !text) return state.entry;
    const value=await call('reviews.update',{conversation_id:cid,id,revision:state.entry.revision,value:local?.patch || {},
      ...(text && state.file ? {file_id:state.file.id,payload:JSON.parse(text)} : {})});
    if (alive.current) {
      setEntry(value); change({revision:value.revision,patch:{},filePath:state.file?.path,fingerprint:value.file_fingerprint || state.file?.fingerprint});
      if (state.file) {
        const nextFile={...state.file,fingerprint:text ? value.file_fingerprint : state.file.fingerprint};
        latest.current.file=nextFile; setFile(nextFile);
      }
    }
    return value;
  }
  async function perform(action: 'generate' | 'save' | 'draft') {
    setBusy(action);setFailure('');
    try {
      const value=await persist();
      if(action==='draft') return;
      const result=await call('reviews.'+action,{conversation_id:cid,id:value.id,revision:value.revision,
        ...(action==='save' && file ? {fingerprint:latest.current.file?.fingerprint || file.fingerprint} : {})});
      if(!alive.current)return;
      if(result.id!==id) {props.openReview(result.id);return;}
      setEntry(result);change({revision:result.revision,patch:{}});setSetup(false);setEditing(false);
      if(action==='save')props.saved();
    } catch(error) {if(alive.current){setFailure((error as Error).message);void refresh().catch(()=>{});}}
    finally {if(alive.current)setBusy('');}
  }
  async function rebase() {
    setBusy('rebase');setFailure('');
    try {
      const current=await persist();
      const result=await call('reviews.rebase',{conversation_id:cid,id,revision:current.revision});
      if(alive.current){setEntry(result);change({revision:result.revision,patch:{}});}
    }catch(error){if(alive.current)setFailure((error as Error).message);}finally{if(alive.current)setBusy('');}
  }
  const field=(label:string,key:string,multiline=false)=><label className="review-field"><span>{label}</span>{multiline?<textarea disabled={Boolean(busy)} value={data[key] || ''} onChange={event=>patch({[key]:event.target.value})} rows={3}/>:<input disabled={Boolean(busy)} maxLength={128} value={data[key] || ''} onChange={event=>patch({[key]:event.target.value})}/>}</label>;
  const sources=data && <details className="review-sources"><summary>来源：{data.scope==='answer'?'本轮回答':data.scope==='task'?'本轮任务':'自选内容'} · 查看与调整</summary><div>
    <label className="review-field"><span>使用范围</span><select value={data.scope} disabled={generating || entry.status==='saved'} onChange={event=>patch({scope:event.target.value})}><option value="answer">本轮回答</option><option value="task">本轮任务</option><option value="custom">自选内容</option></select></label>
    {data.source?.length ? data.source.map((source:any)=><details key={source.id}><summary>{data.scope==='custom'&&<input type="checkbox" aria-label={'选择来源 '+source.id} checked={data.source_ids.includes(source.id)} disabled={generating || entry.status==='saved'} onClick={event=>event.stopPropagation()} onChange={event=>patch({source_ids:event.target.checked?[...data.source_ids,source.id]:data.source_ids.filter((id:string)=>id!==source.id)})}/>}<span>{source.role==='user'?'需求':source.role==='tool'?'执行结果':'回答'} · {source.content.slice(0,80) || '工具调用'}</span></summary><pre>{source.content}{source.tool_calls?.length>0?'\n'+JSON.stringify(source.tool_calls,null,2):''}</pre></details>):<p className="muted">来自当前对话中的工具产物。</p>}
    {data.kind==='skill'&&data.scope==='answer'&&<p className="muted">仅用最终回答可能缺少执行依据；可加入本轮任务后重新提炼。</p>}
  </div></details>;

  if(!entry)return <div className="review-workspace"><p role="status">{failure || '正在读取草稿…'}</p>{failure&&<button className="secondary" onClick={()=>void refresh().catch(error=>setFailure(error.message))}>重试</button>}</div>;
  return <div className={'review-workspace '+(data.kind==='office'&&!preparing?'review-office':'')}>
    <header className="review-heading"><h2>{entry.status==='saved'?(data.kind==='office'?'办公稿已保存':'Skill 已保存'):preparing?(data.kind==='office'?'生成办公稿':'沉淀为 Skill'):(data.kind==='office'?'审阅办公稿':'审阅 Skill')}</h2>
      {entry.status==='ready'&&<button className="text-link" disabled={Boolean(busy)} onClick={()=>setSetup(!setup)}>{setup?'返回审阅':'调整要求'}</button>}
    </header>
    {failure&&<div className="inline-error" role="alert"><p>{failure}</p>{entry.target_skill&&<button className="secondary" disabled={Boolean(busy)} onClick={()=>void rebase()}>核对目标最新版本</button>}</div>}
    {conflict&&<div className="inline-error"><p>草稿已有新版本，当前编辑仍保留。</p><button className="secondary" disabled={Boolean(busy)} onClick={()=>void loadLatest()}>载入最新草稿</button><button className="secondary" disabled={Boolean(busy)} onClick={()=>change({...edit!,revision:entry.revision})}>保留当前编辑并合并</button></div>}
    {entry.status==='saved'?<div className="review-saved"><Check size={28}/><strong>{entry.saved_name || data.title+'.'+data.format}</strong><p className="review-path">{entry.saved_path}</p>
      {entry.catalog_pending&&<div className="inline-error"><p>文件已保存，技能列表尚未刷新。</p><button className="secondary" disabled={Boolean(busy)} onClick={()=>void perform('save')}>重新载入技能</button></div>}
      {data.kind==='office'?<button className="primary" onClick={()=>void props.openFile(entry.saved_path)}>打开文件</button>:<div className="markdown review-paper"><Markdown text={content.instructions_md || ''}/></div>}
    </div>:generating?<div className="review-generating"><LoaderCircle size={24} className="spin"/><h3>{data.kind==='office'?'正在整理办公稿':'正在提炼可复用方法'}</h3><p className="muted">过程可在对话中查看。关闭标签后任务继续，草稿会保留。</p><button className="secondary" disabled={Boolean(busy)||entry.run?.status==='stopping'} onClick={async()=>{setBusy('stop');setFailure('');try{await call('conversation.stop',{conversation_id:cid,run_id:entry.run_id});await refresh();}catch(error){if(alive.current)setFailure((error as Error).message);}finally{if(alive.current)setBusy('');}}}><Square size={14}/>{entry.run?.status==='stopping'?'正在停止':'停止'}</button></div>:preparing?<>
      <div className="review-scroll">{sources}
        {data.kind==='office'&&<div className="review-field"><span>文件格式</span><div className="review-formats">{[['docx','Word'],['pptx','PPT'],['xlsx','Excel']].map(([value,label])=><button className={data.format===value?'selected':''} aria-pressed={data.format===value} key={value} disabled={Boolean(data.file_path)} onClick={()=>patch({format:value})}><FileText size={16}/>{label}</button>)}</div></div>}
        {field(data.kind==='office'?'文件标题':'要复用的方法','title')}{field(data.kind==='office'?'补充要求 · 可选':'提炼重点 · 可选','request',true)}
        {entry.generation_error&&<p className="inline-error" role="alert">{entry.generation_error}</p>}
        {active(props.conversation.run)&&props.conversation.run?.id!==entry.run_id&&<p className="muted">当前对话还有任务运行，结束后可以生成草稿。</p>}
      </div><footer className="review-footer"><span><Folder size={14}/>{data.kind==='office'?'保存到当前项目':'保存到我的技能'}</span><button className="primary" disabled={Boolean(busy)||conflict||active(props.conversation.run)&&props.conversation.run?.id!==entry.run_id} onClick={()=>void perform('generate')}>{busy==='generate'?'正在提交…':data.kind==='office'?'生成办公稿草稿':'生成 Skill 草稿'}</button></footer>
    </>:<>
      <div className="review-scroll">{data.kind==='office'?<>
        {field('文件名称','title')}
        <div className="review-file-tools"><span>{data.format.toUpperCase()} 草稿</span>{file?.payload.read_only&&<button className="secondary" onClick={async()=>{try{const error=await call('file.external',{conversation_id:cid,path:entry.file_path});if(error)throw new Error(error);}catch(error){setFailure((error as Error).message);}}}><Pencil size={14}/>在本机编辑</button>}<button className="secondary" disabled={Boolean(busy)} onClick={()=>void readFile(true).catch(error=>setFailure(error.message))}><RefreshCw size={14}/>刷新预览</button></div>
        {file?.conflict&&<p className="inline-error">文件已变化，恢复的表格编辑仍保留。请复制所需内容，刷新后重新核对。</p>}
        {file?<div className="review-file">{file.payload.kind==='preview'?<FilePreview cid={cid} file={file} text="" change={()=>{}} register={()=>{}}/>:<OfficeEditor file={file} text={edit?.officeText || ''} onChange={officeText} error={setFailure} register={(path,callback)=>{flush.current=callback;props.register(path,callback);}}/>}</div>:<p className="muted" role="status">正在打开文件预览…</p>}
      </>:<>
        <div className="review-file-tools"><span>{content.skill_name} · 未保存</span><button className="text-link" onClick={()=>setEditing(!editing)}>{editing?'查看预览':'编辑内容'}</button></div>
        {editing?<div className="review-skill-editor">{[['skill_name','技能名称'],['description','何时使用'],['instructions_md','指导正文 · 步骤、验证与边界']].map(([key,label])=><label className="review-field" key={key}><span>{label}</span>{key==='skill_name'?<input value={content[key] || ''} onChange={event=>patch({content:{...content,[key]:event.target.value}})}/>:<textarea rows={key==='instructions_md'?14:3} value={content[key] || ''} onChange={event=>patch({content:{...content,[key]:event.target.value}})}/>}</label>)}</div>:<div className="markdown review-paper"><h3>何时使用</h3><p>{content.description}</p><Markdown text={content.instructions_md || ''}/></div>}
        {content.validation?.issues?.filter((item:any)=>item.severity==='warning').map((item:any)=><p className="muted" key={item.code}>{item.message}</p>)}
        {content.resources?.length>0&&<details className="review-sources"><summary>附带资源 · {content.resources.length} 项</summary>{content.resources.map((resource:any,index:number)=><details key={resource.path}><summary><input type="checkbox" aria-label={'附带 '+resource.path} checked={resource.selected!==false} onClick={event=>event.stopPropagation()} onChange={event=>patch({content:{...content,resources:content.resources.map((item:any,i:number)=>i===index?{...item,selected:event.target.checked}:item)}})}/><span>{resource.path}</span></summary><pre>{resource.content}</pre></details>)}</details>}
        <label className="review-field"><span>保存方式</span><select value={data.target_skill || ''} disabled={Boolean(busy)} onChange={event=>patch({target_skill:event.target.value})}><option value="">新建 Skill</option>{inventory.map(item=><option key={item.name} value={item.name}>更新已有 Skill · {item.display_name || item.name}</option>)}</select></label>
        {data.target_skill&&<div className="review-diff"><strong>追加到「{data.target_skill}」</strong><p>保留原有指导，追加本次方法和选中的资源。</p>{data.target_skill!==entry.target_skill?<button className="secondary" disabled={Boolean(busy)} onClick={()=>void perform('draft')}>查看目标与更新内容</button>:entry.target_snapshot&&<details><summary>查看已有指导</summary><div className="markdown"><Markdown text={entry.target_snapshot.body || ''}/>{entry.target_snapshot.recent_experience?.map((item:any,i:number)=><Markdown key={i} text={item.experience_text}/>)}</div></details>}<details><summary>查看新增指导</summary><div className="markdown"><Markdown text={content.instructions_md || ''}/></div></details></div>}
        {sources}
      </>}</div>
      <footer className="review-footer"><span>{dirty?'有未保存的编辑':'草稿已保留'}</span>{dirty&&<button className="secondary" disabled={Boolean(busy)||conflict} onClick={()=>void perform('draft')}>保留草稿</button>}<button className="primary" disabled={Boolean(busy)||conflict||Boolean(file?.conflict)||data.kind==='office'&&!file||Boolean(data.target_skill&&data.target_skill!==entry.target_skill)} onClick={()=>void perform('save')}>{busy==='save'?'正在保存…':data.kind==='office'?'保存到项目':data.target_skill?'保存更新':'保存到我的技能'}</button></footer>
    </>}
  </div>;
}
