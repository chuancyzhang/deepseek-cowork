import {useCallback, useEffect, useState} from 'react';
import {ArrowLeft, ArrowRight, Check, ChevronDown, LoaderCircle} from 'lucide-react';
import {ErrorState, Field, useFormDraft} from '../../Management';
import {rpc} from '../../managementApi';
import {sameFormValue} from '../../host/forms';
import './multimedia.css';

type Binding = {channel_id:string;model:string;adapter:string};
type Props = {onDirty:(value:boolean)=>void;back:()=>void;returnToChat:()=>void;saved:()=>Promise<void>;
  confirm:(title:string,detail:string,choices:string[])=>Promise<number>};
const groups = [{name:'图片',rows:[['image_generate','生成']]},{name:'视频',rows:[['video_generate','生成'],['video_understand','理解']]}];
const labels:Record<string,string> = {image_generate:'图片生成',video_generate:'视频生成',video_understand:'视频理解'};
const identity=(binding:Binding)=>JSON.stringify([binding.channel_id,binding.model,binding.adapter]);

export default function Multimedia(props:Props) {
  const [data,setData]=useState<any>(null),[failure,setFailure]=useState('');
  const load=useCallback(async()=>{try{setData(await rpc('media.config'));setFailure('');}catch(error){setFailure((error as Error).message);}},[]);
  useEffect(()=>{let live=true;void rpc('media.config').then(value=>{if(live)setData(value);}).catch(error=>{if(live)setFailure(error.message);});return()=>{live=false;};},[]);
  return <div className="multimedia-plugin">{failure&&<ErrorState message={failure} retry={()=>void load()}/>} {data?<MediaForm {...props} data={data} loaded={setData}/>:!failure&&<p role="status"><LoaderCircle className="spin" size={16}/>正在读取多媒体配置…</p>}</div>;
}

function MediaForm({data,loaded,onDirty,confirm,back,returnToChat,saved}:Props & {data:any;loaded:(value:any)=>void}) {
  const form=useFormDraft('skill:multimedia:models',{bindings:data.bindings,services:[],apiKeys:{},addition:null},onDirty,['apiKeys']);
  const [revision,setRevision]=useState(data.revision),[busy,setBusy]=useState(false),[failure,setFailure]=useState('');
  const channels=[...data.channels,...form.value.services];
  const patch=(values:any)=>form.change({...form.value,...values});
  const addition=form.value.addition;
  const adapterFor=(channel:any)=>{
    const base=channel?.base_url||'';
    return /^https:\/\/apihub\.agnes-ai\.com(?:\/|$)/.test(base)?'agnes':/^https:\/\/ark\.cn-beijing\.volces\.com(?:\/|$)/.test(base)?'ark':'openai';
  };
  function options(kind:string) {
    const values:Binding[]=[];
    for(const channel of channels.filter((item:any)=>!item.provider_type||item.provider_type==='openai')) {
      const names=new Set<string>([...(channel.models||[]),...Object.values(form.value.bindings).filter((b:any)=>b.channel_id===channel.channel_id).map((b:any)=>b.model)]);
      for(const model of names){
        const existing=Object.values(form.value.bindings).find((item:any)=>item.channel_id===channel.channel_id&&item.model===model&&data.adapters[item.adapter]?.capabilities.includes(kind)) as Binding|undefined;
        const adapter=existing?.adapter||adapterFor(channel),known=adapter==='agnes'?data.known_models[model]:null;
        if(data.adapters[adapter].capabilities.includes(kind)&&(!known||known.capabilities.includes(kind)))values.push({channel_id:channel.channel_id,model,adapter});
      }
    }
    const current=form.value.bindings[kind];
    if(current&&!values.some(item=>identity(item)===identity(current)))values.push(current);
    return values;
  }
  function choose(kind:string,value:string) {
    setFailure('');
    if(value==='__add') {const channel=channels.find((item:any)=>item.provider_type==='openai');patch({addition:{kind,channel_id:channel?.channel_id||'__new',model:'',adapter:adapterFor(channel),service_id:crypto.randomUUID(),display_name:'',base_url:''}});return;}
    const bindings={...form.value.bindings};
    if(!value)delete bindings[kind];else bindings[kind]=options(kind).find(item=>identity(item)===value);
    patch({bindings,addition:null});
  }
  function editAddition(values:any){patch({addition:{...addition,...values}});setFailure('');}
  function cancelAddition(){const apiKeys={...form.value.apiKeys};delete apiKeys[addition.service_id];patch({addition:null,apiKeys});setFailure('');}
  async function add() {
    setBusy(true);setFailure('');
    try {
      if(!addition.model.trim())throw Error('请填写模型标识。');
      const isNew=addition.channel_id==='__new',channelId=isNew?addition.service_id:addition.channel_id;
      const binding={channel_id:channelId,model:addition.model.trim(),adapter:addition.adapter};
      const service={channel_id:channelId,display_name:addition.display_name.trim(),base_url:addition.base_url.trim(),api_key:form.value.apiKeys[channelId]||'',provider_type:'openai',models:[]};
      if(isNew&&(!service.display_name||!/^https?:\/\//.test(service.base_url)||!service.api_key))throw Error('请填写服务名称、地址和 API Key。');
      const services=[...form.value.services.map((item:any)=>({...item,api_key:form.value.apiKeys[item.channel_id]||''})),...(isNew?[service]:[])];
      const checks=await rpc<any>('media.validate',{bindings:{[addition.kind]:binding},services});
      if(!checks[addition.kind]?.valid)throw Error(checks[addition.kind]?.message||'请检查模型配置。');
      const {api_key,...publicService}=service;
      patch({bindings:{...form.value.bindings,[addition.kind]:binding},services:[...form.value.services,...(isNew?[publicService]:[])],addition:null});
    } catch(error){setFailure((error as Error).message);}finally{setBusy(false);}
  }
  async function leave(action:()=>void){if(!form.dirty||await confirm('保留未保存的编辑？','草稿会保留在本机，下次进入可以继续。只有点击保存才会修改配置。',['继续编辑','保留草稿并离开'])===1)action();}
  async function save(){
    setBusy(true);setFailure('');
    try {
      const used=new Set(Object.values(form.value.bindings).map((value:any)=>value.channel_id));
      const result=await rpc('media.save',{revision,enabled:true,bindings:form.value.bindings,
        services:form.value.services.filter((item:any)=>used.has(item.channel_id)).map((item:any)=>({...item,api_key:form.value.apiKeys[item.channel_id]||''}))});
      form.done({bindings:result.bindings,services:[],apiKeys:{},addition:null});setRevision(result.revision);loaded(result);await saved();
    }catch(error){setFailure((error as Error).message);}finally{setBusy(false);}
  }
  async function reload(){
    setBusy(true);
    try{
      const latest=await rpc('media.config');
      const changed=Object.keys(labels).filter(kind=>!sameFormValue(form.value.bindings[kind],form.saved.bindings[kind]));
      const conflicts=changed.filter(kind=>!sameFormValue(latest.bindings[kind],form.saved.bindings[kind])&&!sameFormValue(latest.bindings[kind],form.value.bindings[kind]));
      if(conflicts.length&&await confirm('合并最新配置？',conflicts.map(kind=>labels[kind]).join('、')+'同时有其他修改。保留当前编辑会在这些部分使用你的内容，其余部分读取最新配置；核对后再保存。',['继续编辑','保留当前编辑并合并'])!==1)return;
      const bindings={...latest.bindings};
      for(const kind of changed){if(form.value.bindings[kind])bindings[kind]=form.value.bindings[kind];else delete bindings[kind];}
      const persisted=new Set(latest.channels.map((channel:any)=>channel.channel_id)),apiKeys={...form.value.apiKeys};
      for(const id of persisted)delete apiKeys[id as string];
      patch({bindings,services:form.value.services.filter((item:any)=>!persisted.has(item.channel_id)),apiKeys});loaded(latest);setRevision(latest.revision);setFailure('');
    }catch(error){setFailure((error as Error).message);}finally{setBusy(false);}
  }
  return <>
    <header className="item-editor-heading"><button className="icon-button" title="返回技能" disabled={busy} onClick={()=>void leave(back)}><ArrowLeft size={18}/></button><h2>多媒体</h2><span className="muted">{data.enabled?'已启用':'未启用'}</span></header>
    <form className="detail-form media-config-form" onSubmit={event=>{event.preventDefault();void save();}}>
      {groups.map(group=><section className="media-model-group" key={group.name}><h3>{group.name}</h3><div>{group.rows.map(([kind,label])=>{
        const binding=form.value.bindings[kind],choices=options(kind),editing=addition?.kind===kind;
        const issue=binding&&sameFormValue(binding,data.bindings[kind])&&data.binding_status?.[kind]?.valid===false?data.binding_status[kind].message:'';
        return <div className="media-model-row" key={kind}><label className="media-model-select"><span>{label}</span><select aria-label={labels[kind]+'模型'} disabled={busy} value={editing?'__add':binding?identity(binding):''} onChange={event=>choose(kind,event.target.value)}>
          <option value="">选择模型</option>{choices.map(item=><option key={identity(item)} value={identity(item)}>{channels.find((channel:any)=>channel.channel_id===item.channel_id)?.display_name||'服务不可用'} · {item.model}</option>)}<option value="__add">添加模型…</option>
        </select></label>
        {issue&&<p className="error-text">{issue}</p>}
        {editing&&<div className="media-inline-model"><div className="form-grid">
          <Field label="服务"><select aria-label="模型服务" disabled={busy} value={addition.channel_id} onChange={event=>{const channel=channels.find((item:any)=>item.channel_id===event.target.value);editAddition({channel_id:event.target.value,adapter:adapterFor(channel)});}}><option value="__new">新服务</option>{channels.filter((item:any)=>!item.provider_type||item.provider_type==='openai').map((item:any)=><option value={item.channel_id} key={item.channel_id}>{item.display_name}</option>)}</select></Field>
          <Field label="模型"><input aria-label="模型标识" disabled={busy} value={addition.model} onChange={event=>editAddition({model:event.target.value})} list={'media-models-'+kind} placeholder="选择或填写模型标识"/><datalist id={'media-models-'+kind}>{[...new Set<string>([...(channels.find((item:any)=>item.channel_id===addition.channel_id)?.models||[]),...Object.keys(data.known_models).filter(name=>data.known_models[name].adapter===addition.adapter&&data.known_models[name].capabilities.includes(kind))])].map(name=><option key={name} value={name}/>)}</datalist></Field>
          {addition.channel_id==='__new'&&<><Field label="服务名称"><input aria-label="服务名称" disabled={busy} value={addition.display_name} onChange={event=>editAddition({display_name:event.target.value})}/></Field><Field label="服务地址"><input aria-label="服务地址" type="url" disabled={busy} value={addition.base_url} onChange={event=>editAddition({base_url:event.target.value,adapter:adapterFor({base_url:event.target.value})})} placeholder="https://example.com/v1"/></Field><Field label="API Key" wide><input aria-label="API Key" type="password" autoComplete="off" disabled={busy} value={form.value.apiKeys[addition.service_id]||''} onChange={event=>patch({apiKeys:{...form.value.apiKeys,[addition.service_id]:event.target.value}})}/></Field></>}
        </div><details><summary>连接设置<ChevronDown size={14}/></summary><Field label="接口协议"><select aria-label="接口协议" disabled={busy} value={addition.adapter} onChange={event=>editAddition({adapter:event.target.value})}>{Object.entries(data.adapters).filter(([,value]:any)=>value.capabilities.includes(kind)).map(([key,value]:any)=><option key={key} value={key}>{value.label}</option>)}</select></Field></details>
        {failure&&<ErrorState message={failure}/>}<div className="button-row"><button type="button" className="secondary" disabled={busy} onClick={cancelAddition}>取消添加</button><button type="button" className="secondary" disabled={busy} onClick={()=>void add()}>使用此模型</button></div></div>}
        {kind==='video_understand'&&<small>使用服务可访问的视频链接。</small>}
      </div>;})}</div></section>)}
      {failure&&!addition&&<><ErrorState message={failure}/><button type="button" className="text-link" disabled={busy} onClick={()=>void reload()}>读取最新配置并保留编辑</button></>}
      <div className="media-config-actions"><span role="status">{form.dirty?'有未保存的修改':''}</span>{form.dirty||!data.enabled?<><button type="button" className="secondary" disabled={busy} onClick={()=>{form.reset();setFailure('');}}>取消修改</button><button className="primary" disabled={busy||Boolean(addition)}><Check size={16}/>{busy?'正在保存…':data.enabled?'保存配置':'保存并启用'}</button></>:<button type="button" className="primary" onClick={returnToChat}>去聊天使用<ArrowRight size={15}/></button>}</div>
    </form>
  </>;
}
