import {useEffect, useRef, useState, type CSSProperties, type ReactNode} from 'react';
import {ChevronLeft, ChevronRight, PanelLeft, Search, SlidersHorizontal, X} from 'lucide-react';
import {getDocument, GlobalWorkerOptions, TextLayer, type PDFDocumentProxy, type RenderTask} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import {useHostField} from './host/context';
import './pdf-reader.css';

GlobalWorkerOptions.workerSrc = workerUrl;
const resources = new URL(/* @vite-ignore */ '../pdfjs/', import.meta.url).href;
type ReadingPosition = {page:number; zoom:string; thumbnails:boolean; brightness:number};

/** The PDF is immutable. Theme, layout and reading controls never alter its bytes. */
export default function PdfReader({url, identity, name, footer}: {url:string; identity:string; name:string; footer?:ReactNode|((jump:(page:number)=>void)=>ReactNode)}) {
  const [position, setPosition] = useHostField<ReadingPosition>('pdf-reader:'+identity, {page:1, zoom:'page', thumbnails:false, brightness:100});
  const [document, setDocument] = useState<PDFDocumentProxy|null>(null), [failure, setFailure] = useState('');
  const [retry, setRetry] = useState(0), [searchOpen, setSearchOpen] = useState(false), [query, setQuery] = useState('');
  const [matches, setMatches] = useState<number[]>([]), [searching, setSearching] = useState(false), [options, setOptions] = useState(false);
  const area = useRef<HTMLDivElement>(null), [size, setSize] = useState({width:600, height:700});
  const loadedTask=useRef<ReturnType<typeof getDocument>|null>(null);
  useEffect(()=>()=>{void loadedTask.current?.destroy();loadedTask.current=null;},[]);
  useEffect(() => {
    let active = true;
    setFailure(''); setMatches([]);
    const task = getDocument({url, cMapUrl:resources+'cmaps/', cMapPacked:true,
      standardFontDataUrl:resources+'standard_fonts/', wasmUrl:resources+'wasm/', iccUrl:resources+'iccs/',
      enableXfa:false, disableWorkerRendering:true});
    task.onPassword = () => {if(active)setFailure('此 PDF 需要密码，请用本机应用打开后导出未加密副本。'); void task.destroy();};
    void task.promise.then(value => {
      if(!active)return;
      const previous=loadedTask.current;loadedTask.current=task;
      setDocument(value); setPosition(previous=>({...previous,page:Math.max(1,Math.min(previous.page,value.numPages))}));
      if(previous)void previous.destroy();
    }).catch(error=>{if(active)setFailure(previous=>previous || (loadedTask.current?'刷新未完成，已有预览保留：':'无法打开页面预览：')+error.message);});
    return ()=>{active=false;if(loadedTask.current!==task)void task.destroy();};
  }, [url, retry]);
  useEffect(() => {
    const element=area.current;if(!element)return;
    const observer=new ResizeObserver(([entry])=>setSize({width:entry.contentRect.width,height:entry.contentRect.height}));
    observer.observe(element);return ()=>observer.disconnect();
  }, [document]);
  useEffect(() => {
    let active=true;setMatches([]);
    if(!document || !query.trim() || !searchOpen){setSearching(false);return;}
    const timer=setTimeout(()=>{
      setSearching(true);
      void (async()=>{
        const found:number[]=[], needle=query.trim().toLocaleLowerCase();
        for(let index=1;index<=document.numPages && active;index++){
          const page=await document.getPage(index), content=await page.getTextContent();
          if(!active)return;
          if(content.items.map(item=>'str' in item ? item.str : '').join('').toLocaleLowerCase().includes(needle))found.push(index);
          if(index%8===0)await new Promise(resolve=>setTimeout(resolve,0));
        }
        if(active){setMatches(found);setSearching(false);if(found.length)setPosition(previous=>({...previous,page:found.includes(previous.page)?previous.page:found[0]}));}
      })().catch(error=>{if(active){setSearching(false);setFailure('查找未完成：'+error.message);}});
    },250);
    return ()=>{active=false;clearTimeout(timer);};
  },[document, query, searchOpen]);
  const pageCount=document?.numPages||0;
  const go=(page:number)=>setPosition(previous=>({...previous,page:Math.max(1,Math.min(page,pageCount||1))}));
  const matchIndex=matches.indexOf(position.page);
  return <section className="pdf-reader" aria-label={name+'阅读器'} onKeyDown={event=>{
    if(event.key==='Escape'){setOptions(false);setSearchOpen(false);}
    if((event.ctrlKey||event.metaKey)&&event.key==='f'){event.preventDefault();setSearchOpen(true);}
    if((event.target as HTMLElement).closest('input,select,button'))return;
    if(event.key==='PageDown'){event.preventDefault();go(position.page+1);}
    if(event.key==='PageUp'){event.preventDefault();go(position.page-1);}
  }}>
    <div className="pdf-reader-toolbar">
      <button className="icon-button" aria-label="页面缩略图" title="页面缩略图" aria-pressed={position.thumbnails} onClick={()=>setPosition(value=>({...value,thumbnails:!value.thumbnails}))}><PanelLeft size={16}/></button>
      <div className="pdf-pagination">
        <button className="icon-button" aria-label="上一页" disabled={!document||position.page<=1} onClick={()=>go(position.page-1)}><ChevronLeft size={16}/></button>
        <input aria-label="页码" inputMode="numeric" key={position.page} defaultValue={position.page} onBlur={event=>{const next=Number(event.target.value);if(Number.isInteger(next)&&next>0)go(next);else event.target.value=String(position.page);}} onKeyDown={event=>{if(event.key==='Enter')event.currentTarget.blur();}}/>
        <span>/ {pageCount || '—'}</span>
        <button className="icon-button" aria-label="下一页" disabled={!document||position.page>=pageCount} onClick={()=>go(position.page+1)}><ChevronRight size={16}/></button>
      </div>
      <select aria-label="缩放" value={position.zoom} onChange={event=>setPosition(value=>({...value,zoom:event.target.value}))}>
        <option value="page">适应页面</option><option value="width">适应宽度</option>
        {[50,75,100,125,150,200].map(value=><option key={value} value={String(value)}>{value}%</option>)}
      </select>
      <span className="pdf-toolbar-space"/>
      <button className="icon-button" title="查找文字" aria-label="查找文字" aria-expanded={searchOpen} onClick={()=>setSearchOpen(!searchOpen)}><Search size={16}/></button>
      <button className="icon-button" title="阅读亮度" aria-label="阅读亮度" aria-expanded={options} onClick={()=>setOptions(!options)}><SlidersHorizontal size={16}/></button>
    </div>
    {options&&<div className="pdf-reader-options"><label>阅读亮度 <input aria-label="阅读亮度" type="range" min="85" max="100" value={position.brightness} onChange={event=>setPosition(value=>({...value,brightness:Number(event.target.value)}))}/><output>{position.brightness}%</output></label><button className="text-link" onClick={()=>setPosition(value=>({...value,brightness:100}))}>恢复原色</button></div>}
    {searchOpen&&<div className="pdf-reader-search"><input autoFocus aria-label="查找文档内容" placeholder="查找文档内容" value={query} onChange={event=>setQuery(event.target.value)}/><span role="status">{searching?'查找中…':query?matches.length?`${matchIndex+1} / ${matches.length} 页`:'未找到':''}</span><button className="icon-button" aria-label="上一个匹配页" disabled={!matches.length} onClick={()=>go(matches[(matchIndex-1+matches.length)%matches.length])}><ChevronLeft size={15}/></button><button className="icon-button" aria-label="下一个匹配页" disabled={!matches.length} onClick={()=>go(matches[(matchIndex+1)%matches.length])}><ChevronRight size={15}/></button><button className="icon-button" aria-label="关闭查找" onClick={()=>setSearchOpen(false)}><X size={15}/></button></div>}
    {failure&&<div className="file-update-notice" role="alert"><span>{failure}</span><button className="text-link" onClick={()=>setRetry(value=>value+1)}>重试</button></div>}
    <div className={'pdf-reader-body'+(position.thumbnails?' with-thumbnails':'')}>
      {position.thumbnails&&document&&<nav className="pdf-thumbnails" aria-label="页面导航">{Array.from({length:pageCount},(_,index)=><Thumbnail key={index} document={document} page={index+1} selected={position.page===index+1} select={()=>go(index+1)}/>)}</nav>}
      <div className="pdf-page-area" ref={area} tabIndex={0} aria-label="文档页面">
        {!document&&!failure&&<p role="status" className="muted">正在读取页面…</p>}
        {document&&<PdfPage key={document.fingerprints.join()+':'+position.page} document={document} page={position.page} width={size.width} height={size.height} zoom={position.zoom} brightness={position.brightness} query={searchOpen?query:''}/>}
      </div>
    </div>
    <footer className="pdf-reader-footer">{typeof footer==='function'?footer(go):footer || <span>{name}</span>}{position.brightness<100&&<button className="text-link" onClick={()=>setPosition(value=>({...value,brightness:100}))}>恢复原色</button>}</footer>
  </section>;
}

function PdfPage({document,page,width,height,zoom,brightness=100,query='',thumbnail=false}: {document:PDFDocumentProxy;page:number;width:number;height:number;zoom:string;brightness?:number;query?:string;thumbnail?:boolean}) {
  const wrapper=useRef<HTMLDivElement>(null), canvas=useRef<HTMLCanvasElement>(null), layer=useRef<HTMLDivElement>(null);
  const [failure,setFailure]=useState(''),[ready,setReady]=useState(false),[dimensions,setDimensions]=useState({width:1,height:1,scale:1});
  useEffect(()=>{
    let active=true,render:RenderTask|undefined,text:TextLayer|undefined;
    setFailure('');setReady(false);
    void (async()=>{
      const pdfPage=await document.getPage(page);if(!active||!canvas.current)return;
      const base=pdfPage.getViewport({scale:1}), padding=thumbnail?0:40;
      const fitWidth=Math.max(80,width-padding)/base.width, fitHeight=Math.max(80,height-padding)/base.height;
      const scale=zoom==='page'?Math.min(fitWidth,fitHeight):zoom==='width'?fitWidth:Number(zoom)/100;
      const viewport=pdfPage.getViewport({scale}), ratio=Math.min(window.devicePixelRatio||1,2,Math.sqrt(12000000/(viewport.width*viewport.height)));
      const element=canvas.current;element.width=Math.ceil(viewport.width*ratio);element.height=Math.ceil(viewport.height*ratio);
      setDimensions({width:viewport.width,height:viewport.height,scale});
      render=pdfPage.render({canvas:element,viewport,transform:ratio===1?undefined:[ratio,0,0,ratio,0,0]});
      await render.promise;if(!active)return;
      if(layer.current&&!thumbnail){
        layer.current.replaceChildren();
        text=new TextLayer({textContentSource:pdfPage.streamTextContent(),container:layer.current,viewport});
        await text.render();
      }
      if(active)setReady(true);
    })().catch(error=>{if(active&&error.name!=='RenderingCancelledException')setFailure('此页未能显示：'+error.message);});
    return ()=>{active=false;render?.cancel();text?.cancel();};
  },[document,page,width,height,zoom,thumbnail]);
  useEffect(()=>{
    const needle=query.trim().toLocaleLowerCase();
    layer.current?.querySelectorAll('span').forEach(span=>span.classList.toggle('pdf-text-match',!!needle&&!!span.textContent?.toLocaleLowerCase().includes(needle)));
  },[ready,query]);
  return <div className="pdf-page-wrap" ref={wrapper} data-ready={ready}>
    {failure?<p role="alert">{failure}</p>:<div className="pdf-page" data-page={page} style={{width:dimensions.width,height:dimensions.height,'--total-scale-factor':dimensions.scale,filter:`brightness(${brightness/100})`} as CSSProperties}>
      <canvas ref={canvas} style={{width:dimensions.width,height:dimensions.height}} aria-label={thumbnail?undefined:`第 ${page} 页`} aria-hidden={thumbnail||undefined}/>
      {!thumbnail&&<div ref={layer} className="textLayer"/>}
    </div>}
    {!ready&&!failure&&!thumbnail&&<p role="status" className="muted">正在绘制第 {page} 页…</p>}
  </div>;
}

function Thumbnail({document,page,selected,select}:{document:PDFDocumentProxy;page:number;selected:boolean;select:()=>void}) {
  const target=useRef<HTMLButtonElement>(null),[visible,setVisible]=useState(false);
  useEffect(()=>{const observer=new IntersectionObserver(([entry])=>setVisible(entry.isIntersecting),{rootMargin:'120px'});if(target.current)observer.observe(target.current);return ()=>observer.disconnect();},[]);
  useEffect(()=>{if(selected)target.current?.scrollIntoView({block:'nearest'});},[selected]);
  return <button ref={target} className={'pdf-thumbnail'+(selected?' selected':'')} aria-label={'第 '+page+' 页'} aria-current={selected?'page':undefined} onClick={select}>
    <div className="pdf-thumbnail-image">{visible&&<PdfPage document={document} page={page} width={88} height={116} zoom="page" thumbnail/>}</div><span>{page}</span>
  </button>;
}
