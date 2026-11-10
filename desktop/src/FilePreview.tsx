import { useState } from 'react';
import type { FileSession } from './types';

export default function FilePreview({file, text, change}: {file: FileSession; text: string; change: (text: string) => void}) {
  const [source, setSource] = useState(false);
  const [url, setUrl] = useState(file.payload.url), [failure, setFailure] = useState('');
  const kind = file.payload.preview_kind;
  const html = file.payload.kind === 'html';
  return <div className="file-preview">
    {html && <div className="file-view-switch"><button className={!source ? 'selected' : ''} onClick={async () => {try {setUrl(await window.cowork.call('file.preview_draft', {url: file.payload.url, text})); setSource(false); setFailure('');} catch (error) {setFailure((error as Error).message);}}}>预览</button><button className={source ? 'selected' : ''} onClick={() => setSource(true)}>源码</button></div>}
    {failure && <p role="alert" className="error-text">{failure}</p>}
    {html && source ? <textarea aria-label="HTML 源码" spellCheck={false} value={text} onChange={event => change(event.target.value)}/>
      : kind === 'image' ? <div className="image-preview"><img src={file.payload.url} alt={file.name}/></div>
      : kind && ['doc','ppt','xls'].includes(kind) ? <div className="empty-workspace">此格式需要使用本机应用打开，或转换为 DOCX、PPTX、XLSX。</div>
      : <iframe title={'预览 ' + file.name} sandbox={kind === 'pdf' ? 'allow-scripts allow-same-origin' : 'allow-scripts'} src={url}/>}
  </div>;
}
