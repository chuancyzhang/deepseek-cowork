import {useEffect, useRef, useState} from 'react';
import {X} from 'lucide-react';
import {rpc} from './managementApi';
import {ImagePreview} from './ImageAttachment';
import type {FileSession} from './types';

type Batch = {id:string;state:string;kind:string;reason?:string;current_version:boolean;source_version:string;
  page_count:number;page_numbers:number[];created_at:number;renderer:string;model?:string;constraints:string;
  delivered_pages:number[];conclusion_pages:number[];remaining_pages:number[];request_id:string;
  conclusions:{page:number;summary:string;issues:{type:string;description:string}[]}[];
  images:{sha256:string;pages:number[]}[];budget:{requests:number;page_times:number;tokens:number;usage_known:boolean}};

export default function DocumentReviewRecords({cid,path,fingerprint,configuration,jump}:{cid:string;path:string;fingerprint:string;configuration?:string;jump:(page:number)=>void}){
  const [open,setOpen]=useState(false),[records,setRecords]=useState<{batches:Batch[];current:Batch|null}>({batches:[],current:null});
  const [failure,setFailure]=useState(''),[image,setImage]=useState<{file:FileSession;trigger:HTMLButtonElement}|null>(null);
  const trigger=useRef<HTMLButtonElement>(null),panel=useRef<HTMLDivElement>(null);
  const identity=cid+':'+path,latest=useRef(identity);latest.current=identity;
  useEffect(()=>{setImage(null);setRecords({batches:[],current:null});setOpen(false);},[identity]);
  useEffect(()=>{
    let active=true,timer:ReturnType<typeof setTimeout>;
    const load=async()=>{
      try{const result=await rpc('file.review_records',{conversation_id:cid,path,fingerprint,configuration});if(active){setRecords(result);setFailure('');}}
      catch(error){if(active)setFailure((error as Error).message);}
      if(active)timer=setTimeout(load,open?2000:6000);
    };
    void load();return ()=>{active=false;clearTimeout(timer);};
  },[cid,path,fingerprint,configuration,open]);
  useEffect(()=>{
    if(!open)return;
    const dismiss=(event:PointerEvent)=>{if(!panel.current?.contains(event.target as Node)&&!trigger.current?.contains(event.target as Node))setOpen(false);};
    document.addEventListener('pointerdown',dismiss);return ()=>document.removeEventListener('pointerdown',dismiss);
  },[open]);
  const current=records.current;
  async function inspect(batch:Batch,digest:string,button:HTMLButtonElement){
    try{const file=await rpc<FileSession>('file.review_image',{conversation_id:cid,path,batch_id:batch.id,sha256:digest});if(latest.current===identity)setImage({file,trigger:button});}
    catch(error){if(latest.current===identity)setFailure((error as Error).message);}
  }
  return <>
    <button ref={trigger} className="text-link document-review-entry" aria-expanded={open} aria-haspopup="dialog" onClick={()=>setOpen(!open)}>
      {failure?'检查记录不可用':current?`检查记录 · ${current.conclusion_pages.length} / ${current.page_count} 页`:'检查记录'}</button>
    {open&&<div ref={panel} role="dialog" aria-label="文档检查记录" className="document-review-records" onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();setOpen(false);trigger.current?.focus();}}}>
      <header><span>检查记录</span><button className="icon-button" aria-label="关闭检查记录" onClick={()=>{setOpen(false);trigger.current?.focus();}}><X size={16}/></button></header>
      {failure&&<p role="alert" className="error-text">{failure}</p>}
      {!records.batches.length&&<p className="muted">还没有这份文件的视觉检查记录。可以在对话中让 AI 检查版式。</p>}
      {records.batches.length>0&&!current&&<p className="muted">当前预览尚未检查。下面保留其他版本或阅读方式的记录。</p>}
      {current&&<p className="document-review-coverage">已送审 {current.delivered_pages.length} 页 · 有细节结论 {current.conclusion_pages.length} / {current.page_count} 页</p>}
      {records.batches.map(batch=><section key={batch.id} className="document-review-batch">
        <div className="document-review-meta"><span>{new Date(batch.created_at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}{!batch.current_version?(batch.source_version===fingerprint?' · 其他阅读范围或版式':' · 历史版本'):''}{batch.kind==='overview'?' · 总览':''}</span><span>{({completed:'已返回结论',reviewing:'检查中',not_reviewed:'未完成检查',failed:'检查未完成',interrupted:'已中断'} as Record<string,string>)[batch.state]||'已渲染'}</span></div>
        {batch.reason&&<p role={batch.state==='failed'?'alert':undefined}>{batch.reason}</p>}
        <div className="document-review-pages">{batch.conclusions.map(row=><button key={row.page} disabled={!batch.current_version} onClick={()=>jump(row.page)}><span>第 {row.page} 页</span><span>{row.summary}{row.issues.map((issue,index)=><small key={index}>{issue.description}</small>)}</span></button>)}</div>
        <details><summary>页面与依据</summary><div className="document-review-evidence">{batch.images.map((item,index)=><button className="text-link" key={item.sha256+index} onClick={event=>void inspect(batch,item.sha256,event.currentTarget)}>查看第 {item.pages.join('、')} 页原图</button>)}</div><p>{batch.renderer}{batch.model?' · '+batch.model:''}</p><p>文件版本 {batch.source_version.slice(0,12)}</p>{batch.constraints&&<p>{batch.constraints}</p>}<p>本轮已使用 {batch.budget.requests} 次请求、{batch.budget.page_times} 页次；{batch.budget.usage_known?batch.budget.tokens+' Token':'部分用量未返回'}。</p><p className="muted">记录证明图片提交与结论覆盖，模型判断仍可能有误。总览不计入细节检查。</p></details>
      </section>)}
    </div>}
    {image&&<ImagePreview cid={cid} files={[{path:image.file.path,name:image.file.name}]} selected={{index:0,url:image.file.payload.url,trigger:image.trigger}} onClose={()=>setImage(null)}/>}
  </>;
}
