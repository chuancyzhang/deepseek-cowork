import { useState, type ReactNode } from 'react';
import type { FileSession } from './types';
import {useHostField} from './host/context';

export default function FilePreview({file, text, change, toolbar}: {file: FileSession; text: string; change: (text: string) => void; toolbar?: ReactNode}) {
  const [source, setSource] = useHostField('file-source:' + file.id, false);
  const [url, setUrl] = useHostField('file-preview-url:' + file.id, file.payload.url);
  const [failure, setFailure] = useState('');
  const kind = file.payload.preview_kind;
  const html = file.payload.kind === 'html';
  return <div className="file-preview">
    <div className="file-toolbar">{toolbar}{html && <button className="secondary" onClick={async () => {if (!source) {setSource(true); return;} try {setUrl(await window.cowork.call('file.preview_draft', {url: file.payload.url, text})); setSource(false); setFailure('');} catch (error) {setFailure((error as Error).message);}}}>{source ? '预览' : '编辑源码'}</button>}</div>
    {failure && <p role="alert" className="error-text">{failure}</p>}
    {html && source ? <textarea aria-label="HTML 源码" spellCheck={false} value={text} onChange={event => change(event.target.value)}/>
      : kind === 'image' ? <div className="image-preview"><img src={file.payload.url} alt={file.name}/></div>
      : kind && ['doc','ppt','xls'].includes(kind) ? <div className="empty-workspace">此格式需要使用本机应用打开，或转换为 DOCX、PPTX、XLSX。</div>
      : <iframe title={'预览 ' + file.name} sandbox={kind === 'pdf' ? 'allow-scripts allow-same-origin' : 'allow-scripts'} src={url}/>}
  </div>;
}
