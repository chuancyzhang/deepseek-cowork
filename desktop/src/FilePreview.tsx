import { lazy, Suspense, useEffect, useRef, useState, type ReactNode, type ComponentProps } from 'react';
import type { FileSession } from './types';
import {useHostField} from './host/context';
import {operation, rpc} from './managementApi';
const JobProgress = lazy(() => import('./Management').then(module => ({default:module.JobProgress})));
import OfficeEditor from './OfficeEditor';
import './pdf-reader.css';
const PdfReader = lazy(() => import('./PdfReader'));
const DocumentReviewRecords = lazy(() => import('./DocumentReviewRecords'));

export default function FilePreview({file, text, change, toolbar, cid, register}: {file: FileSession; text: string; change: (text: string) => void; toolbar?: ReactNode; cid: string; register: (path: string, flush?: () => Promise<string>) => void}) {
  const [source, setSource] = useHostField('file-source:' + file.id, false);
  const [url, setUrl] = useHostField('file-preview-url:' + file.id, file.payload.url);
  const [failure, setFailure] = useState('');
  const [switching, setSwitching] = useState(false);
  const flush = useRef<(() => Promise<string>) | undefined>(undefined);
  const kind = file.payload.preview_kind;
  const html = file.payload.kind === 'html';
  return <div className="file-preview">
    <div className="file-toolbar">{toolbar}{html && <button className="secondary" disabled={switching} onClick={async () => {
      if (!source) {setSource(true); return;}
      setSwitching(true);
      try {
        const payload = flush.current ? await flush.current() : text;
        const preview = await rpc('file.preview', {conversation_id:cid, id:file.id, payload});
        setUrl(preview.payload.url); setSource(false); setFailure('');
      } catch (error) {setFailure('预览未完成，编辑内容已保留。' + (error as Error).message);}
      finally {setSwitching(false);}
    }}>{source ? '预览' : '编辑'}</button>}</div>
    {failure && <p role="alert" className="error-text">{failure}</p>}
    {kind === 'office' ? <OfficePreview file={file} cid={cid}/> : html && source ? <Suspense fallback={<p role="status">正在打开编辑器…</p>}><OfficeEditor file={file} text={text} onChange={change} error={setFailure} register={(path, callback) => {flush.current=callback; register(path, callback);}}/></Suspense>
      : kind === 'image' ? <div className="image-preview"><img src={file.payload.url} alt={file.name}/></div>
      : kind === 'video' || kind === 'audio' ? <div className="file-media-preview">{kind === 'video' ? <video src={file.payload.url} controls preload="metadata" aria-label={file.name} onError={() => setFailure('无法在应用内播放此格式，可以用本机应用打开。')}/> : <audio src={file.payload.url} controls preload="metadata" aria-label={file.name} onError={() => setFailure('无法在应用内播放此格式，可以用本机应用打开。')}/>}</div>
      : kind && ['doc','ppt','xls'].includes(kind) ? <div className="empty-workspace">此格式需要使用本机应用打开，或转换为 DOCX、PPTX、XLSX。</div>
      : kind === 'pdf' ? <Suspense fallback={<p role="status" className="muted">正在打开阅读器…</p>}><PdfReader url={url} identity={cid+':'+file.path} name={file.name} footer={jump=><><span>{file.name}</span><DocumentReviewRecords cid={cid} path={file.path} fingerprint={file.fingerprint} jump={jump}/></>}/></Suspense>
      : <iframe title={'预览 ' + file.name} sandbox={kind === 'pdf' ? undefined : 'allow-scripts'} src={kind === 'pdf' ? url + '#navpanes=0&view=FitH' : url}/>}
  </div>;
}

export function OfficePreview({file, cid}: {file: FileSession; cid: string}) {
  const excel=/\.xlsx?$/i.test(file.path);
  const [excelOptions,setExcelOptions]=useHostField('excel-reading:'+cid+':'+file.path,{mode:'region',sheet:'',range:'',identity_columns:'',header_rows:''});
  const [areaOptions,setAreaOptions]=useState(false);
  const [preview, setPreview] = useHostField('office-preview:' + cid + ':' + file.path, {
    url: '', fingerprint: '', attempted: '', busy: false, failure: '', job: null as any,
    renderer: '', rendererName:'', configuration:'', token:'', refreshToken:'', warnings:[] as string[],
    catalog: file.payload.renderer || {available:false,candidates:[]},
  });
  const [choices,setChoices]=useState(false),[checking,setChecking]=useState(false);
  const starting = useRef('');
  const candidates=preview.catalog.candidates || [];
  const selected=preview.renderer || candidates[0]?.id || '';
  async function detect() {
    setChecking(true);
    try {
      const catalog=await rpc('file.renderers',{conversation_id:cid,path:file.path});
      setPreview(value=>({...value,catalog,failure:catalog.candidates?.find((item:any)=>item.id===(value.renderer||catalog.candidates[0]?.id))?.reason||''}));
    } catch(error){setPreview(value=>({...value,failure:(error as Error).message}));}
    finally {setChecking(false);}
  }
  async function render(install = false, renderer=selected) {
    const token=crypto.randomUUID();
    starting.current = file.id;
    setPreview(value => ({...value, token, busy: true, attempted: file.id, failure: '', renderer,refreshToken:file.payload.manual_preview_refresh||''}));
    const update=(patch:any)=>setPreview(value=>value.token===token?{...value,...patch}:value);
    const progress = (job: any) => update({job});
    try {
      if (install) {
        await operation('file.install_renderer', {conversation_id:cid}, progress);
        renderer='libreoffice';
        update({renderer,catalog:await rpc('file.renderers',{conversation_id:cid,path:file.path})});
      }
      let jobId = '';
      await operation('file.render', {conversation_id:cid, path:file.path, fingerprint:file.fingerprint,renderer:renderer||'auto',...(excel?{excel_options:excelOptions}:{})}, job => {jobId = job.id; progress(job);});
      const result = await rpc<FileSession>('file.render_result', {conversation_id:cid, job_id:jobId});
      update({url:result.payload.url,fingerprint:result.fingerprint,renderer:result.payload.renderer,
        rendererName:result.payload.renderer_name,configuration:result.payload.render_configuration,warnings:result.payload.warnings||[]});
    } catch (error) {
      const reason = (error as Error).message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '');
      setPreview(value => value.token===token?{...value, failure:(value.url ? '刷新未完成，已有预览保留。' : '') + reason}:value);
    } finally {update({busy:false,job:null});}
  }
  useEffect(()=>{if(!file.payload.renderer)void detect();},[cid,file.path]);
  useEffect(() => {
    const requested=file.payload.manual_preview_refresh&&file.payload.manual_preview_refresh!==preview.refreshToken;
    if ((!preview.url||requested) && !preview.busy && candidates.find((item:any)=>item.id===selected)?.available && preview.attempted !== file.id && starting.current !== file.id) void render();
  }, [file.id, preview.busy, preview.catalog, file.payload.manual_preview_refresh]);
  return <div className="office-preview">
    {preview.url&&file.fingerprint!==preview.fingerprint&&!preview.busy&&!preview.failure&&<div className="file-update-notice" role="status"><span>文件已更新</span><button className="text-link" onClick={()=>void render()}>刷新</button></div>}
    {excel&&<div className="office-renderer-choices"><select aria-label="表格阅读方式" value={excelOptions.mode} disabled={preview.busy} onChange={event=>{setExcelOptions(value=>({...value,mode:event.target.value}));setAreaOptions(true);}}><option value="region">区域阅读</option><option value="print">打印预览</option></select><button className="text-link" onClick={()=>setAreaOptions(!areaOptions)}>{areaOptions?'收起范围':'阅读范围'}</button></div>}
    {excel&&areaOptions&&<div className="office-reading-options">
      {excelOptions.mode==='region'&&<><label>工作表<input placeholder="全部可见工作表" value={excelOptions.sheet} onChange={event=>setExcelOptions(value=>({...value,sheet:event.target.value}))}/></label><label>范围<input placeholder="已使用区域，如 A1:R80" value={excelOptions.range} onChange={event=>setExcelOptions(value=>({...value,range:event.target.value.toUpperCase()}))}/></label><label>重复标识列<input placeholder="可选，如 A:B" value={excelOptions.identity_columns} onChange={event=>setExcelOptions(value=>({...value,identity_columns:event.target.value.toUpperCase()}))}/></label><label>重复标题行<input placeholder="可选，如 1:2" value={excelOptions.header_rows} onChange={event=>setExcelOptions(value=>({...value,header_rows:event.target.value}))}/></label></>}
      <button className="secondary" disabled={preview.busy} onClick={()=>void render()}>更新预览</button><span className="muted">{excelOptions.mode==='region'?'只调整阅读副本，宽表按列分片。':'使用原文件的打印区域和分页。'}</span>
    </div>}
    {preview.job && <Suspense fallback={<p role="status" className="muted">{preview.job.message}</p>}><JobProgress job={preview.job}/></Suspense>}
    {preview.busy && !preview.job && <p role="status" className="muted">正在准备页面预览…</p>}
    {preview.warnings.length>0&&<details className="office-preview-diagnostics"><summary>预览提示</summary>{preview.warnings.map(value=><p key={value}>{value}</p>)}</details>}
    {preview.failure && <div className="file-update-notice" role="alert"><span>{preview.failure}</span><button className="text-link" disabled={preview.busy} onClick={() => void render()}>重试</button><button className="text-link" disabled={checking} onClick={()=>void detect()}>重新检测可用性</button><button className="text-link" onClick={()=>setChoices(true)}>更换渲染方式</button></div>}
    {(choices||!preview.url&&!preview.busy)&&<div className="office-renderer-choices">
      {!preview.url&&candidates.find((item:any)=>item.id===selected)?.reason&&<span role="status">{candidates.find((item:any)=>item.id===selected).reason}</span>}
      {candidates.length>0&&<><select aria-label="本次预览使用" value={selected} disabled={preview.busy} onChange={event=>setPreview(value=>({...value,renderer:event.target.value}))}>{candidates.map((item:any)=><option key={item.id} value={item.id}>{item.name}{item.state==='ready'?'':' · 等待清理'}</option>)}</select><button className="secondary" disabled={preview.busy} onClick={()=>void render()}>生成预览</button></>}
      {!candidates.length&&<span>本机尚未发现可用的 Office、WPS 或 LibreOffice。</span>}
      <button className="text-link" disabled={checking} onClick={()=>void detect()}>{checking?'正在检测…':'重新检测'}</button>
      {!candidates.some((item:any)=>item.id==='libreoffice')&&(preview.catalog.install_supported?<button className="text-link" disabled={preview.busy} onClick={()=>void render(true)}>安装 LibreOffice · 356 MiB</button>:<button className="text-link" onClick={()=>void rpc('external.open',{url:'https://www.libreoffice.org/download/download-libreoffice/'}).catch(error=>setPreview(value=>({...value,failure:error.message})))}>下载 LibreOffice</button>)}
      {choices&&<button className="text-link" onClick={()=>setChoices(false)}>收起</button>}
    </div>}
    {preview.url && <Suspense fallback={<p role="status" className="muted">正在打开阅读器…</p>}><PdfReader url={preview.url} identity={cid+':'+file.path} name={file.name} footer={jump=><><span title={preview.configuration}>{preview.rendererName}</span><DocumentReviewRecords cid={cid} path={file.path} fingerprint={preview.fingerprint} configuration={preview.configuration} jump={jump}/><button className="text-link" onClick={()=>setChoices(!choices)}>渲染方式</button></>}/></Suspense>}
  </div>;
}

export function SpreadsheetWorkspace({cid,...props}:ComponentProps<typeof OfficeEditor>&{cid:string}){
  const [reading,setReading]=useHostField('spreadsheet-reading:'+cid+':'+props.file.path,false);
  return <div className="office-preview"><div className="file-toolbar"><button className="text-link" aria-pressed={!reading} onClick={()=>setReading(false)}>表格</button><button className="text-link" aria-pressed={reading} onClick={()=>setReading(true)}>页面阅读</button>{reading&&<span className="muted">显示已保存的文件</span>}</div>
    <div className="spreadsheet-editor-surface" style={{display:reading?'none':'flex'}}><OfficeEditor {...props}/></div>
    {reading&&<OfficePreview key={cid+':'+props.file.path} cid={cid} file={props.file}/>}
  </div>;
}
