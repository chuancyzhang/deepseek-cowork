import {useEffect, useRef, useState, type ReactNode} from 'react';
import {ChevronRight, ExternalLink, RefreshCw, Wrench} from 'lucide-react';
import {useHostField} from './host/context';
import {formatDuration} from './runtime';

type Output = {preview:string; path:string; bytes:number; status:string; incomplete:boolean; reason?:string; started_at:number; finished_at?:number; records?:{channel:string;text:string;sequence:number}[]; cursor:number; has_more:boolean};
const call = <T = any,>(params:any) => window.cowork.call<T>('task.output', params);
const statusNames:Record<string,string> = {running:'运行中', succeeded:'已完成', failed:'失败', cancelled:'已停止', unknown:'结果未确认'};
const preview = (text:string) => new TextDecoder().decode(new TextEncoder().encode(text).slice(-8192)).replace(/\r\n?/g,'\n').trimEnd().split('\n').slice(-8);

export function ToolProcess({cid,runId,callId,title,name,state,running,result,failed,details,children,readOnly=false}: {
  cid:string;runId?:string;callId:string;title:string;name:string;state:string;running:boolean;result?:string;failed:boolean;details:()=>void;children?:ReactNode;readOnly?:boolean;
}) {
  const [expanded,setExpanded] = useHostField('tool-expanded:'+cid+':'+(runId || '')+':'+callId, false);
  const [output,setOutput] = useState<Output|null>(null), [failure,setFailure]=useState(''), [revision,setRevision]=useState(0);
  const root=useRef<HTMLDivElement>(null), [visible,setVisible]=useState(false);
  useEffect(()=>{
    const observer=new IntersectionObserver(entries=>setVisible(entries.some(entry=>entry.isIntersecting)));
    if(root.current)observer.observe(root.current);
    return()=>observer.disconnect();
  },[]);
  useEffect(()=>{
    if(!visible || !expanded || readOnly || !callId || !runId)return;
    let stopped=false, timer:ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const next=await call<Output|null>({conversation_id:cid,run_id:runId,call_id:callId});
        if(stopped)return;
        setOutput(next);setFailure('');
        if(running)timer=setTimeout(update,250);
      } catch(error) {if(!stopped)setFailure('输出暂时无法读取：'+(error as Error).message);}
    }
    void update();return()=>{stopped=true;clearTimeout(timer);};
  },[cid,runId,callId,visible,expanded,running,revision,readOnly]);
  const text=output?.preview || result || '';
  const hasOutput=!['request_user_input','request_user_approval'].includes(name) && Boolean(text || output || ['bash','run_python_code','run_node_code','run_skill_script','install_package'].includes(name));
  const duration=output && Math.max(0,(output.finished_at || Date.now()/1000)-output.started_at);
  return <div ref={root} className="process-step tool-step">
    <Wrench className="timeline-node" size={14} aria-hidden="true"/>
    <div className="process-tool-heading">
      <button className="process-tool" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}><ChevronRight className={expanded?'rotated':''} size={13} aria-hidden="true"/><span>{title}</span><small className="process-tool-state" data-failed={failed || output?.status==='failed' || undefined}>{state==='正在停止' ? state : output ? statusNames[output.status] || state : state}</small>{duration != null && <small className="process-duration">{formatDuration(duration)}</small>}</button>
      <button className="process-tool-detail" aria-label={'查看'+title+'详情'} onClick={details}>详情 <ExternalLink size={12}/></button>
    </div>
    {title!==name && <code className="process-tool-name">{name}</code>}
    {expanded && <>
      {hasOutput && <div className="tool-output-preview" aria-label={name+' 输出'}>{preview(text).map((line,index)=><div className="tool-output-line" key={index}>{line || '\u00a0'}</div>)}</div>}
      {output?.reason && <p className={output.incomplete?'muted':'error-text'}>{output.reason}</p>}
      {failure && <p className="error-text" role="alert">{failure}<button className="text-link" onClick={()=>setRevision(value=>value+1)}>重试</button></p>}
    </>}
    {children}
  </div>;
}

export function ToolOutputDetail({cid,runId,callId,running}:{cid:string;runId?:string;callId:string;running:boolean}) {
  const [page,setPage]=useState<Output|null>(null),[failure,setFailure]=useState(''),[cursor,setCursor]=useState(0),[revision,setRevision]=useState(0),[follow,setFollow]=useState(true);
  const cursors=useRef<number[]>([]);
  useEffect(()=>{setPage(null);setCursor(0);cursors.current=[];setFollow(true);},[cid,runId,callId]);
  useEffect(()=>{
    if(!runId)return;
    let stopped=false,timer:ReturnType<typeof setTimeout>;
    async function update(){
      try {
        const next=await call<Output|null>({conversation_id:cid,run_id:runId,call_id:callId,cursor});
        if(stopped)return;
        setPage(next);setFailure('');
        if(running)timer=setTimeout(update,500);
      }catch(error){if(!stopped)setFailure((error as Error).message);}
    }
    void update();return()=>{stopped=true;clearTimeout(timer);};
  },[cid,runId,callId,cursor,running,revision]);
  useEffect(()=>{
    if(follow && page?.has_more && page.cursor>cursor){cursors.current.push(cursor);setCursor(page.cursor);}
  },[page,follow,cursor]);
  if(!page && !failure)return null;
  return <section className="tool-output-detail" aria-label="已保存输出">
    <div className="context-heading"><strong>输出</strong><button className="text-link" aria-pressed={follow} onClick={()=>{setFollow(!follow);}}>{follow?'停止跟随':'跟随输出'}</button>{page && <button className="text-link" onClick={()=>void window.cowork.call('file.reveal',{conversation_id:cid,path:page.path}).catch(error=>setFailure(error.message))}>打开日志位置</button>}</div>
    {page?.reason && <p className={page.incomplete?'muted':'error-text'}>{page.reason}</p>}
    {failure && <p className="error-text" role="alert">{failure}<button className="text-link" onClick={()=>setRevision(value=>value+1)}><RefreshCw size={12}/>重试</button></p>}
    <div className="tool-log-pages"><button className="text-link" disabled={!cursors.current.length} onClick={()=>{setFollow(false);setCursor(cursors.current.pop() || 0);}}>上一段</button><span>{cursor.toLocaleString()}–{(page?.cursor || cursor).toLocaleString()} / {(page?.bytes || 0).toLocaleString()} 字节</span><button className="text-link" disabled={!page?.has_more} onClick={()=>{setFollow(false);cursors.current.push(cursor);setCursor(page!.cursor);}}>下一段</button></div>
    <pre className="tool-log-content" onWheel={()=>setFollow(false)} onPointerDown={()=>setFollow(false)}>{page?.records?.map(record=><span key={record.sequence} className={record.channel==='stderr'?'tool-log-stderr':undefined}>{record.text}</span>)}</pre>
  </section>;
}
