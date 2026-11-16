import {useCallback, useEffect, useState} from 'react';
import {ArrowRight, Check, ChevronRight, LoaderCircle, MoreHorizontal, Plus, RefreshCw, Trash2, X} from 'lucide-react';
import {ErrorState, Field, PageHead, useFormDraft} from '../../Management';
import {rpc} from '../../managementApi';
import {useHostField} from '../../host/context';
import {sameFormValue} from '../../host/forms';
import './multimedia.css';

type Props = {useAction:(id:string)=>Promise<void>;onError:(message:string)=>void; onDirty:(value:boolean)=>void; openConversation:(id:string)=>void; manageServices:()=>void;
  confirm:(title:string,detail:string,choices:string[])=>Promise<number>};
const labels:Record<string,string> = {image_generate:'图片生成',image_understand:'图片理解',video_generate:'视频生成',video_understand:'视频理解',audio_generate:'文字配音',audio_understand:'音频转写'};
const statuses:Record<string,string> = {queued:'等待提交',submitting:'正在提交',running:'正在生成',downloading:'保存成果',completed:'已完成',paused:'已暂停',unknown:'提交待核查',download_failed:'下载未完成',failed:'失败',cancelled:'已停止'};
const active = ['queued','submitting','running','downloading'];

export default function Multimedia(props:Props) {
  const [data,setData]=useState<any>(null),[failure,setFailure]=useState('');
  const load=useCallback(async()=>{try{setData(await rpc('media.config'));setFailure('');}catch(error){setFailure((error as Error).message);}},[]);
  useEffect(()=>{let live=true;void rpc('media.config').then(value=>{if(live)setData(value);}).catch(error=>{if(live)setFailure(error.message);});return()=>{live=false;};},[]);
  return <div className="multimedia-plugin">{failure&&<ErrorState message={failure} retry={()=>void load()}/>} {data?<MediaForm {...props} data={data} loaded={setData}/>:!failure&&<p role="status"><LoaderCircle className="spin" size={16}/>正在读取多媒体配置…</p>}</div>;
}

function MediaForm({useAction,data,loaded,onDirty,onError,confirm,openConversation,manageServices}:Props & {data:any;loaded:(value:any)=>void}) {
  const form=useFormDraft('multimedia',{bindings:data.bindings,services:[],apiKeys:{}},onDirty,['apiKeys']);
  const [editing,setEditing]=useHostField('multimedia:editing',Object.keys(data.bindings)[0]||'image_generate');
  const [revision,setRevision]=useState(data.revision),[busy,setBusy]=useState(false),[failure,setFailure]=useState('');
  const [tasks,setTasks]=useState<any[]>(data.tasks),[preview,setPreview]=useState<any>(null),[taskBusy,setTaskBusy]=useState('');
  const [remoteIds,setRemoteIds]=useState<Record<string,string>>({});
  const channels=[...data.channels,...form.value.services];
  const binding=form.value.bindings[editing]||{adapter:'openai',channel_id:'',model:''};
  const draftRequest={bindings:form.value.bindings,services:form.value.services.map((item:any)=>({...item,api_key:form.value.apiKeys[item.channel_id]||''}))};
  const draftKey=JSON.stringify(draftRequest);
  const [checked,setChecked]=useState<{key:string;items:Record<string,any>;error?:string}>({key:'',items:{}});
  const checking=checked.key!==draftKey, check=checking?undefined:checked.items[editing];
  const changed=(key:string)=>!sameFormValue(form.value.bindings[key],form.saved.bindings[key]);
  useEffect(()=>{if(!editing)setEditing(Object.keys(data.bindings)[0]||'image_generate');},[editing]);
  useEffect(()=>{
    let live=true;
    const timer=setTimeout(()=>void rpc<Record<string,any>>('media.validate',draftRequest)
      .then(items=>{if(live)setChecked({key:draftKey,items});})
      .catch(error=>{if(live)setChecked({key:draftKey,items:{},error:error.message});}),160);
    return()=>{live=false;clearTimeout(timer);};
  },[draftKey]);
  const patch=(values:any)=>form.change({...form.value,...values});
  function editBinding(values:any){setFailure('');patch({bindings:{...form.value.bindings,[editing]:{...binding,...values}}});}
  useEffect(()=>window.cowork.onEvents(events=>{
    const changes=events.filter(event=>event.event==='media_task').map(event=>event.data);
    if(changes.some(item=>item&&!active.includes(item.status)))void rpc('media.config').then(loaded).catch(error=>setFailure(error.message));
    if(changes.length)setTasks(previous=>{
      const merged=new Map(previous.map(item=>[item.id,item]));
      for(const item of changes)if(item&&(!merged.has(item.id)||item.updated_at>=(merged.get(item.id)?.updated_at||0)))merged.set(item.id,item);
      return [...merged.values()].sort((a,b)=>b.created_at-a.created_at).slice(0,100);
    });
  }),[]);
  async function refresh(){try{const next=await rpc<any[]>('media.tasks');setTasks(previous=>next.map(item=>{const newer=previous.find(old=>old.id===item.id);return newer?.updated_at>item.updated_at?newer:item;}));}catch(error){setFailure((error as Error).message);}}
  async function taskAction(item:any,action:string){setTaskBusy(item.id);setFailure('');try{await rpc('media.'+action,{id:item.id,...(remoteIds[item.id]?{remote_id:remoteIds[item.id]}:{})});await refresh();}catch(error){setFailure((error as Error).message);}finally{setTaskBusy('');}}
  async function openFile(item:any,path:string){setTaskBusy(item.id);setFailure('');try{const file=await rpc('file.open',{conversation_id:item.conversation_id,path});setPreview({...file,conversation_id:item.conversation_id});}catch(error){setFailure((error as Error).message);}finally{setTaskBusy('');}}
  function newService(){const id=crypto.randomUUID(),preset=data.adapters[binding.adapter];patch({services:[...form.value.services,{channel_id:id,display_name:preset.label==='OpenAI 兼容'?'':preset.label,base_url:preset.base_url}],bindings:{...form.value.bindings,[editing]:{...binding,channel_id:id}}});}
  const newChannel=form.value.services.find((item:any)=>item.channel_id===binding.channel_id);
  function serviceField(key:string,value:string){patch({services:form.value.services.map((item:any)=>item.channel_id===binding.channel_id?{...item,[key]:value}:item)});}
  const recent=!changed(editing)&&data.binding_status?.[editing]?.status?data.binding_status[editing]:null;
  const canUse=Boolean(binding.model&&!form.dirty&&!checking&&check?.valid);
  function configState(key:string){
    if(changed(key))return form.value.bindings[key]?.model?'待保存':form.saved.bindings[key]?'待移除':'待填写';
    if(!form.value.bindings[key]?.model)return '未配置';
    return (checking?data.binding_status?.[key]?.valid===false:checked.items[key]?.valid===false)?'需调整':'已配置';
  }
  return <>
    <PageHead compact title="多媒体"><button className="secondary" onClick={manageServices}>模型与服务</button></PageHead>
    <form className="detail-form media-config-form" onSubmit={async event=>{event.preventDefault();setBusy(true);setFailure('');try{
      const result=await rpc('media.save',{revision,...draftRequest});
      form.done({bindings:result.bindings,services:[],apiKeys:{}});setRevision(result.revision);loaded(result);
    }catch(error){setFailure((error as Error).message);}finally{setBusy(false);}}}>
      <div className="media-config-layout"><div className="media-capabilities"><table className="media-model-table"><caption className="sr-only">选择要配置的多媒体能力</caption><thead><tr><th scope="col">素材</th><th scope="col">生成</th><th scope="col">理解</th></tr></thead><tbody>
        {[['image','图片'],['video','视频'],['audio','音频']].map(([kind,label])=><tr key={kind}><th scope="row">{label}</th>{['generate','understand'].map(operation=>{const key=kind+'_'+operation,item=form.value.bindings[key];return <td key={key}><button type="button" className={'media-model-choice'+(editing===key?' selected':'')} aria-label={'配置'+labels[key]} aria-pressed={editing===key} onClick={()=>setEditing(key)}><span>{labels[key]}</span><small>{configState(key)}</small>{editing===key&&<ChevronRight size={13}/>}</button></td>;})}</tr>)}
      </tbody></table></div>
      {editing&&<section className="media-binding-editor" aria-label={labels[editing]+'配置'}><div className="section-heading"><h3>{labels[editing]}</h3>{binding.model&&<details className="media-config-more" key={editing}><summary aria-label="更多配置操作"><MoreHorizontal size={18}/></summary><div><button type="button" disabled={busy} onClick={event=>{event.currentTarget.closest('details')?.removeAttribute('open');const next={...form.value.bindings};delete next[editing];patch({bindings:next});}}><Trash2 size={14}/>移除此项配置</button><small>保存后生效，已有任务和文件保留。</small></div></details>}</div><div className="form-grid">
        <Field label="服务"><div className="input-action"><select value={binding.channel_id} disabled={busy} onChange={e=>editBinding({channel_id:e.target.value})}><option value="">选择服务</option>{channels.filter((item:any)=>!item.provider_type||item.provider_type==='openai').map((item:any)=><option key={item.channel_id} value={item.channel_id}>{item.display_name||'新服务'}</option>)}</select><button type="button" className="secondary" disabled={busy} onClick={newService}><Plus size={14}/>添加</button></div></Field>
        <Field label="模型"><input list="media-models" value={binding.model} disabled={busy} onChange={e=>editBinding({model:e.target.value})} placeholder="选择或填写模型标识"/><datalist id="media-models">{[...new Set<string>([...Object.keys(data.known_models).filter(name=>data.known_models[name].adapter===binding.adapter),...(channels.find((item:any)=>item.channel_id===binding.channel_id)?.models||[])])].filter(name=>binding.adapter!=='agnes'||!data.known_models?.[name]||data.known_models[name].capabilities.includes(editing)).map(name=><option key={name} value={name}/>)}</datalist></Field>
        <Field label="接入方式"><select value={binding.adapter} disabled={busy} onChange={e=>editBinding({adapter:e.target.value})}>{Object.entries(data.adapters).filter(([,v]:any)=>v.capabilities.includes(editing)).map(([key,value]:any)=><option key={key} value={key}>{value.label}</option>)}</select></Field>
        {newChannel&&<><Field label="服务名称"><input value={newChannel.display_name} disabled={busy} onChange={e=>serviceField('display_name',e.target.value)}/></Field><Field label="服务地址"><input type="url" value={newChannel.base_url} disabled={busy} onChange={e=>serviceField('base_url',e.target.value)} placeholder="https://example.com/v1"/></Field><Field label="API Key" wide><input type="password" autoComplete="off" value={form.value.apiKeys[newChannel.channel_id]||''} disabled={busy} onChange={e=>patch({apiKeys:{...form.value.apiKeys,[newChannel.channel_id]:e.target.value}})}/></Field></>}
      </div>{editing==='video_understand'&&<p className="muted">素材需提供服务可访问的视频链接。</p>}{binding.adapter==='agnes'&&binding.model==='agnes-video-2.5-flash'&&<p className="muted">720P · 4–12 秒 · 最多 5 张参考图，图片需提供公网链接。</p>}
      {!checking&&check?.message&&<div className="media-config-issue" role="status"><p>{check.message}</p>{check.suggested_adapter&&<button type="button" className="secondary" disabled={busy} onClick={()=>editBinding({adapter:check.suggested_adapter})}>使用 {data.adapters[check.suggested_adapter].label}</button>}</div>}
      {!checking&&checked.error&&<ErrorState message={'配置检查未完成：'+checked.error}/>}
      {binding.model&&!changed(editing)&&<div className="media-call-status"><span>最近调用</span><span>{recent?recent.label:'暂无记录'}</span>{recent&&<p>{recent.message}</p>}</div>}
      </section>}</div>
      {failure&&<ErrorState message={failure}/>}
      <div className="media-config-actions"><span role="status">{form.dirty?'有未保存的修改':canUse?'发送需求后开始生成或理解素材。':checking?'正在检查配置…':binding.model?'请先调整当前配置。':'选择服务和模型后保存。'}</span>{form.dirty?<><button type="button" className="secondary" disabled={busy} onClick={()=>{form.reset();setFailure('');}}>取消修改</button><button className="primary" disabled={busy}><Check size={16}/>{busy?'正在保存…':'保存配置'}</button></>:canUse?<button type="button" className="primary" onClick={()=>void useAction(editing).catch(error=>setFailure(error.message))}>去聊天使用<ArrowRight size={15}/></button>:null}</div>
    </form>
    <section className="settings-section media-tasks"><div className="section-heading"><h3>任务与成果</h3><button className="icon-button" title="刷新任务" onClick={()=>void refresh()}><RefreshCw size={16}/></button></div>
      {!tasks.length&&<p className="muted">生成与解析记录会保留在这里。</p>}
      {tasks.map(item=><article className="media-task" key={item.id}><div className="section-heading"><strong>{labels[item.capability]}</strong><span role="status">{statuses[item.status]||item.status}{item.progress!=null&&item.status==='running'?' · '+item.progress+'%':''}</span></div><small>{item.model} · {item.service_name}</small><p className={['unknown','failed','download_failed'].includes(item.status)?'error-text':'muted'}>{item.message}</p><div className="button-row">
        {item.can_resume&&<button className="secondary" disabled={taskBusy===item.id} onClick={()=>void taskAction(item,'resume')}>{item.status==='download_failed'?'重试下载':'恢复任务'}</button>}
        {active.includes(item.status)&&<button className="secondary" disabled={taskBusy===item.id} onClick={()=>void taskAction(item,'stop')}>停止等待</button>}
        {(item.files||[]).map((path:string)=><button className="secondary" key={path} disabled={taskBusy===item.id} title={path} onClick={()=>void openFile(item,path)}>{path.split(/[\\/]/).pop()}</button>)}
        <button className="text-link" onClick={async()=>{if(!form.dirty||await confirm('保留未保存的编辑？','草稿会保留，下次进入可以继续。',['继续编辑','返回对话'])===1)openConversation(item.conversation_id);}}>返回对话</button>
      </div>{item.can_attach_id&&<details><summary>补充远端编号</summary><p className="muted">先在服务后台核对提交结果。Agnes 填写 video_id，其他服务填写任务 id。</p><div className="input-action"><input aria-label="远端任务编号" value={remoteIds[item.id]||''} onChange={event=>setRemoteIds({...remoteIds,[item.id]:event.target.value})}/><button className="secondary" disabled={!remoteIds[item.id]||taskBusy===item.id} onClick={()=>void taskAction(item,'resume')}>恢复查询</button></div></details>}</article>)}
    </section>
    {preview&&<section className="media-result settings-section"><div className="section-heading"><h3>{preview.name}</h3><button className="icon-button" title="关闭成果预览" onClick={()=>setPreview(null)}><X size={16}/></button></div>
      {preview.payload.preview_kind==='video'?<video src={preview.payload.url} controls preload="metadata"/>:preview.payload.preview_kind==='audio'?<audio src={preview.payload.url} controls preload="metadata"/>:preview.payload.preview_kind==='image'?<img src={preview.payload.url} alt={preview.name}/>:<pre>{preview.payload.text||preview.payload.content||''}</pre>}
      <button className="secondary" onClick={()=>void rpc<string>('file.external',{conversation_id:preview.conversation_id,path:preview.path}).then(error=>{if(error)setFailure(error);}).catch(error=>setFailure(error.message))}>用本机应用打开</button>
    </section>}
  </>;
}
