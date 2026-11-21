import {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {ChevronLeft, ChevronRight, Image as ImageIcon, Minus, Plus, X} from 'lucide-react';
import {useHost} from './host/context';
import './image-attachments.css';

export function isImageAttachment(file: {name?: string; path: string}) {
  return /\.(?:png|jpe?g|webp|gif|bmp|avif)$/i.test(file.name || file.path);
}

type ImageFile = {path: string; name: string};
type OpenImage = {index: number; url: string; trigger: HTMLButtonElement};

function Thumbnail({cid, file, open, onRemove}: {
  cid: string; file: ImageFile; open: (url: string, trigger: HTMLButtonElement) => void; onRemove?: () => void;
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
    <button className="image-attachment-open" title={file.name} aria-label={'查看图片 ' + file.name} aria-haspopup="dialog"
      onClick={event => open(failure ? '' : url, event.currentTarget)}>
      {url && !failure ? <img src={url} alt={file.name} loading="lazy" onError={() => setFailure('预览不可用，点击查看文件')}/>
        : <span className="image-attachment-status"><ImageIcon size={20}/><span>{file.name}</span><small>{failure || '正在加载…'}</small></span>}
    </button>
    {onRemove && <button className="image-attachment-remove" title={'移除图片 ' + file.name} aria-label={'移除图片 ' + file.name} onClick={onRemove}><X size={14}/></button>}
  </div>;
}

export default function ImageAttachments({cid, files, onOpen, onRemove}: {
  cid: string; files: ImageFile[]; onOpen?: (path: string) => void; onRemove?: (path: string) => void;
}) {
  const [selected, setSelected] = useState<{image: OpenImage; files: ImageFile[]; cid: string} | null>(null);
  return <div className="image-attachments">
    {files.map((file, index) => <Thumbnail key={file.path} cid={cid} file={file}
      onRemove={onRemove && (() => onRemove(file.path))}
      open={(url, trigger) => setSelected({image:{index, url, trigger}, files, cid})}/>)}
    {selected?.cid === cid && <ImagePreview cid={cid} files={selected.files}
      selected={selected.image} onClose={() => setSelected(null)} onOpen={onOpen}/>}
  </div>;
}

export function ImagePreviewLink({cid, file, onOpen, children}: {
  cid: string; file: ImageFile; onOpen?: (path: string) => void; children: React.ReactNode;
}) {
  const [selected, setSelected] = useState<OpenImage | null>(null);
  return <>
    <button type="button" className="inline-file" aria-haspopup="dialog"
      onClick={event => setSelected({index:0,url:'',trigger:event.currentTarget})}><ImageIcon size={16}/>{children}</button>
    {selected && <ImagePreview cid={cid} files={[file]} selected={selected} onClose={() => setSelected(null)} onOpen={onOpen}/>}
  </>;
}

export function ImagePreview({cid, files, selected, onClose, onOpen}: {
  cid: string; files: ImageFile[]; selected: OpenImage; onClose: () => void; onOpen?: (path: string) => void;
}) {
  const host = useHost();
  const dialog = useRef<HTMLDialogElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const [index, setIndex] = useState(selected.index);
  const [source, setSource] = useState({index: selected.index, url: selected.url});
  const [failure, setFailure] = useState(false);
  const [natural, setNatural] = useState({width:0, height:0});
  const [fit, setFit] = useState(1);
  const [zoom, setZoom] = useState<number | null>(null);
  const pan = useRef<{x:number; y:number; left:number; top:number} | null>(null);
  const center = useRef<{x:number; y:number} | null>(null);
  const file = files[index];
  const scale = zoom ?? fit;
  const minimum = Math.min(.1, fit);

  useLayoutEffect(() => {
    const element = dialog.current;
    host.set('chat:image-overlay', true);
    element?.showModal();
    closeButton.current?.focus({preventScroll:true});
    return () => {
      element?.close();
      host.set('chat:image-overlay', false);
      if (selected.trigger.isConnected) selected.trigger.focus({preventScroll:true});
    };
  }, [host, selected.trigger]);

  useEffect(() => {
    if (!file || source.index === index && source.url) return;
    let alive = true;
    void window.cowork.call('file.open', {conversation_id:cid, path:file.path}).then(result => {
      if (!alive) return;
      const url = result.payload?.url;
      if (url) setSource({index, url});
      else setFailure(true);
    }).catch(() => {if (alive) setFailure(true);});
    return () => {alive = false;};
  }, [cid, file?.path, index, source.index, source.url]);

  useLayoutEffect(() => {
    const element = stage.current;
    if (!element || !natural.width) return;
    const measure = () => {
      const sides = files.length > 1 ? 80 : 24;
      setFit(Math.max(.01, Math.min(1, (element.clientWidth - sides)/natural.width, (element.clientHeight - 24)/natural.height)));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  }, [natural, files.length]);

  useLayoutEffect(() => {
    const element = stage.current;
    if (!element) return;
    const point = center.current;
    center.current = null;
    if (zoom === null) element.scrollTo(0,0);
    else if (point) element.scrollTo(point.x*element.scrollWidth-element.clientWidth/2, point.y*element.scrollHeight-element.clientHeight/2);
  }, [zoom, index]);

  function step(delta: number) {
    const next = index + delta;
    if (!files[next]) return;
    setNatural({width:0, height:0});
    setSource({index:next,url:''});
    setFailure(false);
    setZoom(null);
    setIndex(next);
  }

  function changeScale(value: number) {
    const element = stage.current;
    if (!element || !natural.width) return;
    const next = Math.min(3, Math.max(minimum, value));
    if (next === scale) return;
    center.current = {
      x:(element.scrollLeft+element.clientWidth/2)/element.scrollWidth,
      y:(element.scrollTop+element.clientHeight/2)/element.scrollHeight
    };
    setZoom(next);
  }

  function panEnd() {
    pan.current = null;
    stage.current?.removeAttribute('data-dragging');
  }

  if (!file) return null;
  return createPortal(<dialog ref={dialog} className="chat-image-preview" aria-label="图片预览"
    onClick={event => {if (event.target === event.currentTarget) onClose();}}
    onCancel={event => {event.preventDefault();onClose();}}
    onKeyDown={event => {
      if (event.key === 'Escape') {event.preventDefault();event.stopPropagation();onClose();return;}
      const action = event.key === 'ArrowLeft' ? () => step(-1) : event.key === 'ArrowRight' ? () => step(1)
        : ['+','='].includes(event.key) ? () => changeScale(scale*1.25) : event.key === '-' ? () => changeScale(scale/1.25)
          : event.key === '0' ? () => setZoom(null) : null;
      if (action) {event.preventDefault();action();}
    }}>
    <div className="chat-image-viewer">
      <header className="chat-image-toolbar">
        <div className="chat-image-filename"><ImageIcon size={16}/><span title={file.name}>{file.name}</span></div>
        <div className="chat-image-controls">
          <button type="button" className="icon-button" aria-label="缩小" title="缩小" disabled={!natural.width || scale <= minimum} onClick={() => changeScale(scale/1.25)}><Minus size={17}/></button>
          <button type="button" className="chat-image-scale" aria-label={`适应窗口，当前 ${Math.round(scale*100)}%`} title="适应窗口" disabled={!natural.width} onClick={() => setZoom(null)}>{Math.round(scale*100)}%</button>
          <button type="button" className="icon-button" aria-label="放大" title="放大" disabled={!natural.width || scale >= 3} onClick={() => changeScale(scale*1.25)}><Plus size={17}/></button>
          <button type="button" ref={closeButton} className="icon-button" aria-label="关闭预览" title="关闭预览 · Esc" onClick={onClose}><X size={18}/></button>
        </div>
      </header>
      <div className="chat-image-viewport">
        <div className="chat-image-stage" ref={stage}
          onPointerDown={event => {
            const element = event.currentTarget;
            if (event.pointerType !== 'mouse' || event.button !== 0 ||
              element.scrollWidth <= element.clientWidth && element.scrollHeight <= element.clientHeight) return;
            pan.current = {x:event.clientX,y:event.clientY,left:element.scrollLeft,top:element.scrollTop};
            element.setPointerCapture(event.pointerId);
            element.setAttribute('data-dragging','');
          }}
          onPointerMove={event => {
            const start = pan.current;
            if (start) event.currentTarget.scrollTo(start.left + start.x-event.clientX, start.top + start.y-event.clientY);
          }}
          onPointerUp={panEnd} onPointerCancel={panEnd} onLostPointerCapture={panEnd}>
          <div className="chat-image-canvas">
            {failure ? <div className="chat-image-error"><p role="alert">图片预览不可用</p>
              {onOpen && <button type="button" className="secondary" onClick={() => {onClose();onOpen(file.path);}}>在工作区打开</button>}</div>
              : source.index === index && source.url ? <img key={file.path} src={source.url} alt={file.name} draggable={false}
                style={{width: natural.width ? Math.round(natural.width*scale) : undefined, visibility:natural.width ? undefined : 'hidden'}}
                onLoad={event => setNatural({width:event.currentTarget.naturalWidth,height:event.currentTarget.naturalHeight})} onError={() => setFailure(true)}/>
                : <p className="muted" role="status">正在读取图片…</p>}
          </div>
        </div>
        {files.length > 1 && <>
          <button type="button" className="icon-button chat-image-step" aria-label="上一张" disabled={index===0} onClick={() => step(-1)}><ChevronLeft size={18}/></button>
          <button type="button" className="icon-button chat-image-step next" aria-label="下一张" disabled={index===files.length-1} onClick={() => step(1)}><ChevronRight size={18}/></button>
          <span className="chat-image-count" aria-live="polite">{index+1} / {files.length}</span>
        </>}
      </div>
    </div>
  </dialog>, document.body);
}
