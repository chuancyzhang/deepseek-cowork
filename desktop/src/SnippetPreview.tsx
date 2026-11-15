import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {Copy,Play,RotateCw,Square} from 'lucide-react';
import './snippet.css';

type Run={token:string;url:string};
export default function SnippetPreview({source,language,complete,scope}:{source:string;language:string;complete:boolean;scope:string}) {
  const kind=language==='html'?'html':['js','javascript'].includes(language)?'js':null;
  const [view,setView]=useState<'code'|'preview'>('code'),[run,setRun]=useState<Run|null>(null);
  const [busy,setBusy]=useState(false),[failure,setFailure]=useState(''),[notice,setNotice]=useState(''),[status,setStatus]=useState('');
  const [logs,setLogs]=useState<{level:string;text:string}[]>([]),[height,setHeight]=useState(220);
  const [hasPage,setHasPage]=useState(false);
  const viewRef=useRef(view);viewRef.current=view;
  const frame=useRef<HTMLIFrameElement>(null),root=useRef<HTMLElement>(null),epoch=useRef(0),current=useRef<Run|null>(null),lastBeat=useRef(0);
  const release=(value:Run|null)=>{if(value)void window.cowork.call('snippet.release',{token:value.token}).catch(error=>console.warn('snippet_release_failed',{scope,message:error.message}));};
  const stop=()=>{epoch.current++;release(current.current);current.current=null;setRun(null);setBusy(false);};
  useLayoutEffect(()=>{setRun(null);setBusy(false);setView('code');setFailure('');setNotice('');setStatus('');setLogs([]);setHeight(220);setHasPage(false);return()=>{epoch.current++;release(current.current);current.current=null;};},[source,scope]);
  const theme=()=>{
    const style=getComputedStyle(root.current||document.documentElement);
    return `:root{color-scheme:${document.documentElement.dataset.theme==='dark'?'dark':'light'};color:${style.getPropertyValue('--text')};background:${style.getPropertyValue('--bg')};font-family:${style.fontFamily};font-size:${style.fontSize}}body{margin:20px;overflow-wrap:anywhere}*{box-sizing:border-box}button,input,textarea,select{font:inherit}`;
  };
  const diagnostic=(value:Run,event:string,reason?:string)=>void window.cowork.call('snippet.event',{token:value.token,status:event,reason}).catch(()=>{});
  useEffect(()=>{
    if(!run)return;
    let live=true,started=false,failed=false;
    lastBeat.current=Date.now();
    const error=(message:string,reason='runtime')=>{if(!live)return;failed=true;setFailure(message);setStatus('运行失败');diagnostic(run,'error',reason);};
    const listener=(event:MessageEvent)=>{
      if(event.source!==frame.current?.contentWindow||event.data?.token!==run.token)return;
      const data=event.data;
      if(data.type==='gate'&&!started){started=true;void window.cowork.call('snippet.start',{token:run.token}).then(()=>{
        if(live){lastBeat.current=Date.now();frame.current?.contentWindow?.postMessage({token:run.token,type:'start',css:theme()},'*');}
      }).catch(failure=>{if(live){error(failure.message,'isolation');stop();}});}
      if(data.type==='heartbeat')lastBeat.current=Date.now();
      if(data.type==='loaded'){if(!failed)setStatus(kind==='html'?'预览已就绪':'本次执行完成');diagnostic(run,'loaded');}
      if(data.type==='height'&&Number.isFinite(data.height)){setHeight(Math.max(180,Math.min(720,data.height)));setHasPage(data.hasPage===true);}
      if(data.type==='error')error(String(data.message||'代码无法运行。').slice(0,4000),String(data.reason||'runtime'));
      if(data.type==='console')setLogs(items=>items.length>=101?items:[...items,{level:String(data.level),text:String(data.text).slice(0,4000)}]);
    };
    window.addEventListener('message',listener);
    const unsubscribe=window.cowork.onEvents(events=>{
      if(events.some(event=>event.event==='snippet_navigation_blocked'&&event.data?.token===run.token))error('预览内不支持跳转网页。源码和当前页面已保留。','navigation');
      if(events.some(event=>event.event==='snippet_navigation_failed'&&event.data?.token===run.token)){error('预览不支持跳转网页，已停止。源码和已收到的输出已保留。','navigation');stop();}
    });
    const observer=new MutationObserver(()=>frame.current?.contentWindow?.postMessage({token:run.token,type:'theme',css:theme()},'*'));
    for(let element:HTMLElement|null=root.current;element;element=element.parentElement)observer.observe(element,{attributes:true,attributeFilter:['style','class','data-theme']});
    const visible=()=>{lastBeat.current=Date.now();};document.addEventListener('visibilitychange',visible);
    const timer=setInterval(()=>{
      if(document.visibilityState!=='visible'||viewRef.current==='code'){lastBeat.current=Date.now();return;}
      if(Date.now()-lastBeat.current>10000){diagnostic(run,'timeout');error('预览超过 10 秒未响应，已停止。源码已保留，可以重新运行。','timeout');stop();}
    },1000);
    return()=>{live=false;window.removeEventListener('message',listener);unsubscribe();observer.disconnect();document.removeEventListener('visibilitychange',visible);clearInterval(timer);};
  },[run]);
  const start=async()=>{
    if(!kind||!complete)return;
    stop();const attempt=++epoch.current;setBusy(true);setFailure('');setNotice('');setHasPage(false);setLogs([]);setView('preview');setStatus('正在启动预览…');
    try{const value:Run=await window.cowork.call('snippet.create',{source,kind,scope});if(attempt!==epoch.current){release(value);return;}current.current=value;setRun(value);}
    catch(error){if(attempt===epoch.current){setFailure((error as Error).message);setStatus('启动失败');}}
    finally{if(attempt===epoch.current)setBusy(false);}
  };
  return <section className="snippet-preview" ref={root} aria-label={`${language} 代码片段`}>
    <div className="snippet-toolbar">
      <span>{language==='html'?'HTML':language==='javascript'||language==='js'?'JavaScript':language}</span>
      <button aria-pressed={view==='code'} onClick={()=>setView('code')}>代码</button>
      {kind&&<button aria-pressed={view==='preview'} disabled={!complete||busy} onClick={()=>{if(run)setView('preview');else void start();}}>{kind==='html'?'预览':'运行 / 输出'}</button>}
      <div className="snippet-actions"><button onClick={async()=>{try{await window.cowork.call('clipboard.write',{text:source});setNotice('已复制代码');}catch{setNotice('复制失败，请选择源码后复制。');setView('code');}}}><Copy size={14}/>复制</button>
        {(run||failure)&&kind&&<button disabled={!complete||busy} onClick={()=>void start()}><RotateCw size={14}/>重新运行</button>}
        {(run||busy)&&<button onClick={()=>{stop();setStatus('预览已停止');}}><Square size={14}/>停止预览</button>}</div>
    </div>
    {!complete&&<p className="snippet-notice">代码块尚未结束，暂不能运行。已收到的源码仍可复制。</p>}
    {!kind&&<p className="snippet-notice">此片段需要额外运行环境。当前预览支持自包含 HTML 和浏览器原生 JavaScript。</p>}
    {failure&&<div className="snippet-error" role="alert"><strong>未能完成预览，源码已保留。</strong><pre>{failure}</pre><span>请检查语法、页面元素或依赖；外部资源、模块导入和 Node.js 接口不在此预览环境中提供。</span></div>}
    <pre className="snippet-source" hidden={view!=='code'}><code>{source}</code></pre>
    <div hidden={view!=='preview'} className="snippet-result">
      {run&&<iframe ref={frame} title={kind==='html'?'HTML 页面预览':'JavaScript 页面输出'} src={run.url} sandbox="allow-scripts" referrerPolicy="no-referrer" style={{height:kind==='js'&&!hasPage?0:height}} onLoad={()=>frame.current?.contentWindow?.postMessage({token:run.token,type:'gate-request'},'*')}/>}
      {kind==='js'&&!hasPage&&!logs.length&&status==='本次执行完成'&&<p className="snippet-notice">本次执行未产生页面或控制台输出。</p>}
      {!run&&!busy&&!failure&&<button className="snippet-start" onClick={()=>void start()}><Play size={14}/>运行预览</button>}
      {logs.length>0&&<details className="snippet-console" open={kind==='js'||logs.some(log=>['alert','error'].includes(log.level))}><summary>控制台输出（{logs.length}）</summary><pre>{logs.map((log,index)=><div key={index}>{['warn','error'].includes(log.level)?`[${log.level}] `:''}{log.text}</div>)}</pre></details>}
    </div>
    {(status||notice)&&<div className="snippet-footer" role="status"><span>{status}</span><span>{notice}</span></div>}
  </section>;
}
