import {useEffect, useState} from 'react';
import {Image as ImageIcon, X} from 'lucide-react';
import './image-attachments.css';

export function isImageAttachment(file: {name?: string; path: string}) {
  return /\.(?:png|jpe?g|webp|gif|bmp|avif)$/i.test(file.name || file.path);
}

export default function ImageAttachment({cid, file, onOpen, onRemove}: {
  cid: string; file: {path: string; name: string}; onOpen?: (path: string) => void; onRemove?: () => void;
}) {
  const [preview, setPreview] = useState<{path: string; url: string} | null>(null), [failure, setFailure] = useState('');
  useEffect(() => {
    let alive = true;
    setFailure('');
    void window.cowork.call('file.open', {conversation_id: cid, path: file.path}).then(result => {
      if (alive) setPreview({path: file.path, url: result.payload.url});
    }).catch(() => {if (alive) setFailure('预览不可用，点击查看文件');});
    return () => {alive = false;};
  }, [cid, file.path]);
  const url = preview?.path === file.path ? preview.url : '';
  return <div className="image-attachment">
    <button className="image-attachment-open" title={file.name} aria-label={'查看图片 ' + file.name} onClick={() => onOpen?.(file.path)}>
      {url && !failure ? <img src={url} alt={file.name} loading="lazy" onError={() => setFailure('预览不可用，点击查看文件')}/>
        : <span className="image-attachment-status"><ImageIcon size={20}/><span>{file.name}</span><small>{failure || '正在加载…'}</small></span>}
    </button>
    {onRemove && <button className="image-attachment-remove" title={'移除图片 ' + file.name} aria-label={'移除图片 ' + file.name} onClick={onRemove}><X size={14}/></button>}
  </div>;
}
