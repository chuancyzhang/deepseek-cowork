import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import type { FileSession } from './types';
import {useHostField} from './host/context';
import {operation, rpc} from './managementApi';
const JobProgress = lazy(() => import('./Management').then(module => ({default:module.JobProgress})));
import OfficeEditor from './OfficeEditor';

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
      : kind && ['doc','ppt','xls'].includes(kind) ? <div className="empty-workspace">此格式需要使用本机应用打开，或转换为 DOCX、PPTX、XLSX。</div>
      : <iframe title={'预览 ' + file.name} sandbox={kind === 'pdf' ? undefined : 'allow-scripts'} src={kind === 'pdf' ? url + '#navpanes=0&view=FitH' : url}/>}
  </div>;
}

function OfficePreview({file, cid}: {file: FileSession; cid: string}) {
  const [preview, setPreview] = useHostField('office-preview:' + cid + ':' + file.path, {
    url: '', fingerprint: '', attempted: '', busy: false, failure: '', job: null as any,
    available: Boolean(file.payload.renderer?.available),
  });
  const starting = useRef('');
  async function render(install = false) {
    starting.current = file.id;
    setPreview(value => ({...value, busy: true, attempted: file.id, failure: ''}));
    const progress = (job: any) => setPreview(value => ({...value, job}));
    try {
      if (install) {
        await operation('file.install_renderer', {conversation_id:cid}, progress);
        setPreview(value => ({...value, available: true}));
      }
      let jobId = '';
      await operation('file.render', {conversation_id:cid, path:file.path, fingerprint:file.fingerprint}, job => {jobId = job.id; progress(job);});
      const result = await rpc<FileSession>('file.render_result', {conversation_id:cid, job_id:jobId});
      setPreview(value => ({...value, url:result.payload.url, fingerprint:result.fingerprint, available:true}));
    } catch (error) {
      const reason = (error as Error).message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '');
      setPreview(value => ({...value, failure:(value.url ? '刷新未完成，已有预览保留。' : '') + reason}));
    } finally {setPreview(value => ({...value, busy:false, job:null}));}
  }
  useEffect(() => {
    if (!preview.busy && (preview.available || file.payload.renderer?.available) && preview.attempted !== file.id && starting.current !== file.id) void render();
  }, [file.id, preview.busy]);
  return <div className="office-preview">
    {preview.job && <Suspense fallback={<p role="status" className="muted">{preview.job.message}</p>}><JobProgress job={preview.job}/></Suspense>}
    {preview.busy && !preview.job && <p role="status" className="muted">正在准备页面预览…</p>}
    {preview.failure && <div className="file-update-notice" role="alert"><span>{preview.failure}</span><button className="text-link" disabled={preview.busy} onClick={() => void render(!preview.available)}>重试</button></div>}
    {!preview.available && !file.payload.renderer?.available && !preview.busy && !preview.url && <div className="empty-workspace"><p>安装 LibreOffice 后预览 Word 和 PPT 的页面版式。</p>{file.payload.renderer?.install_supported ? <button className="primary" onClick={() => void render(true)}>安装预览组件 · 356 MiB</button> : <button className="secondary" onClick={() => void rpc('external.open', {url:'https://www.libreoffice.org/download/download-libreoffice/'}).catch(error => setPreview(value => ({...value,failure:error.message})))}>下载 LibreOffice</button>}</div>}
    {/* Chromium's PDF viewer cannot run in an HTML-sandboxed iframe. The host
        validates PDF bytes/MIME; Electron's process sandbox stays enabled. */}
    {preview.url && <iframe title={'页面预览 ' + file.name} src={preview.url + '#navpanes=0&view=FitH'}/>}
  </div>;
}
