import {forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode} from 'react';
import {ChevronLeft, ChevronRight, Minus, PanelLeft, Plus, Search, SlidersHorizontal, X} from 'lucide-react';
import {getDocument, GlobalWorkerOptions, TextLayer, type PDFDocumentProxy, type RenderTask} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import {useHostField} from './host/context';
import './pdf-reader.css';

GlobalWorkerOptions.workerSrc = workerUrl;
const resources = new URL(/* @vite-ignore */ '../pdfjs/', import.meta.url).href;
type ReadingPosition = {page:number; zoom:string; thumbnails:boolean; brightness:number; mode?:'continuous'|'single'; x?:number; y?:number};
type PageSize = {width:number;height:number};
type ViewportActions = {go:(page:number)=>void;zoom:(value:string)=>void;zoomBy:(factor:number)=>void};

/** The PDF is immutable. Theme, layout and reading controls never alter its bytes. */
export default function PdfReader({url, identity, name, footer}: {url:string; identity:string; name:string; footer?:ReactNode|((jump:(page:number)=>void)=>ReactNode)}) {
  const [position, setPosition] = useHostField<ReadingPosition>('pdf-reader:'+identity, {page:1, zoom:'width', thumbnails:/\.pptx?$/i.test(name), brightness:100,mode:'continuous',x:0,y:0});
  const [document, setDocument] = useState<PDFDocumentProxy|null>(null), [failure, setFailure] = useState('');
  const [pages,setPages]=useState<PageSize[]>([]);
  const [retry, setRetry] = useState(0), [searchOpen, setSearchOpen] = useState(false), [query, setQuery] = useState('');
  const [matches, setMatches] = useState<number[]>([]), [searching, setSearching] = useState(false), [options, setOptions] = useState(false);
  const viewport=useRef<ViewportActions>(null);
  const loadedTask=useRef<ReturnType<typeof getDocument>|null>(null);
  useEffect(()=>()=>{void loadedTask.current?.destroy();loadedTask.current=null;},[]);
  useEffect(() => {
    let active = true;
    setFailure(''); setMatches([]);
    const task = getDocument({url, cMapUrl:resources+'cmaps/', cMapPacked:true,
      standardFontDataUrl:resources+'standard_fonts/', wasmUrl:resources+'wasm/', iccUrl:resources+'iccs/',
      enableXfa:false, disableWorkerRendering:true});
    task.onPassword = () => {if(active)setFailure('此 PDF 需要密码，请用本机应用打开后导出未加密副本。'); void task.destroy();};
    console.info('pdf_reader_load_started',{identity});
    void task.promise.then(async value => {
      const sizes:PageSize[]=[];
      // Reserve exact page geometry before displaying a new version; lazy canvases cannot move it.
      for(let start=1;start<=value.numPages&&active;start+=16){
        sizes.push(...await Promise.all(Array.from({length:Math.min(16,value.numPages-start+1)},async(_,i)=>{const page=await value.getPage(start+i),size=page.getViewport({scale:1});return {width:size.width,height:size.height};})));
      }
      if(!active)return;
      const previous=loadedTask.current;loadedTask.current=task;
      setPages(sizes);setDocument(value); setPosition(previous=>({...previous,page:Math.max(1,Math.min(previous.page,value.numPages))}));
      if(previous)void previous.destroy();
      console.info('pdf_reader_load_completed',{identity,pages:value.numPages});
    }).catch(error=>{if(active){console.error('pdf_reader_load_failed',{identity,reason:error.message});setFailure(previous=>previous || (loadedTask.current?'刷新未完成，已有预览保留：':'无法打开页面预览：')+error.message);}});
    return ()=>{active=false;if(loadedTask.current!==task)void task.destroy();};
  }, [url, retry]);
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
        if(active){setMatches(found);setSearching(false);if(found.length)go(found.includes(position.page)?position.page:found[0]);}
      })().catch(error=>{if(active){setSearching(false);setFailure('查找未完成：'+error.message);}});
    },250);
    return ()=>{active=false;clearTimeout(timer);};
  },[document, query, searchOpen]);
  const pageCount=document?.numPages||0;
  function go(page:number){const next=Math.max(1,Math.min(page,pageCount||1));if(viewport.current)viewport.current.go(next);else setPosition(previous=>({...previous,page:next}));}
  const matchIndex=matches.indexOf(position.page);
  return <section className="pdf-reader" aria-label={name+'阅读器'} onKeyDown={event=>{
    if(event.key==='Escape'){setOptions(false);setSearchOpen(false);}
    if((event.ctrlKey||event.metaKey)&&event.key==='f'){event.preventDefault();setSearchOpen(true);}
    if((event.target as HTMLElement).closest('input,select,button'))return;
    if((event.ctrlKey||event.metaKey)&&['+','=','-','0'].includes(event.key)){event.preventDefault();if(event.key==='0')viewport.current?.zoom('width');else viewport.current?.zoomBy(event.key==='-'?1/1.15:1.15);}
    if(position.mode==='single'&&event.key==='PageDown'){event.preventDefault();go(position.page+1);}
    if(position.mode==='single'&&event.key==='PageUp'){event.preventDefault();go(position.page-1);}
  }}>
    <div className="pdf-reader-toolbar">
      <button className="icon-button" aria-label="页面缩略图" title="页面缩略图" aria-pressed={position.thumbnails} onClick={()=>setPosition(value=>({...value,thumbnails:!value.thumbnails}))}><PanelLeft size={16}/></button>
      <div className="pdf-pagination">
        <button className="icon-button" aria-label="上一页" disabled={!document||position.page<=1} onClick={()=>go(position.page-1)}><ChevronLeft size={16}/></button>
        <input aria-label="页码" inputMode="numeric" key={position.page} defaultValue={position.page} onBlur={event=>{const next=Number(event.target.value);if(Number.isInteger(next)&&next>0)go(next);else event.target.value=String(position.page);}} onKeyDown={event=>{if(event.key==='Enter')event.currentTarget.blur();}}/>
        <span>/ {pageCount || '—'}</span>
        <button className="icon-button" aria-label="下一页" disabled={!document||position.page>=pageCount} onClick={()=>go(position.page+1)}><ChevronRight size={16}/></button>
      </div>
      <select aria-label="阅读方式" value={position.mode||'continuous'} onChange={event=>setPosition(value=>({...value,mode:event.target.value as ReadingPosition['mode']}))}><option value="continuous">连续阅读</option><option value="single">单页查看</option></select>
      <div className="pdf-zoom"><button className="icon-button" aria-label="缩小" title="缩小" disabled={!document} onClick={()=>viewport.current?.zoomBy(1/1.15)}><Minus size={15}/></button>
      <select aria-label="缩放" value={position.zoom} onChange={event=>viewport.current?.zoom(event.target.value)}>
        <option value="page">适应页面</option><option value="width">适应宽度</option>
        {!['page','width','25','50','75','100','125','150','200','300','400'].includes(position.zoom)&&<option value={position.zoom}>{position.zoom}%</option>}
        {[25,50,75,100,125,150,200,300,400].map(value=><option key={value} value={String(value)}>{value}%</option>)}
      </select>
      <button className="icon-button" aria-label="放大" title="放大" disabled={!document} onClick={()=>viewport.current?.zoomBy(1.15)}><Plus size={15}/></button></div>
      <span className="pdf-toolbar-space"/>
      <button className="icon-button" title="查找文字" aria-label="查找文字" aria-expanded={searchOpen} onClick={()=>setSearchOpen(!searchOpen)}><Search size={16}/></button>
      <button className="icon-button" title="阅读亮度" aria-label="阅读亮度" aria-expanded={options} onClick={()=>setOptions(!options)}><SlidersHorizontal size={16}/></button>
    </div>
    {options&&<div className="pdf-reader-options"><label>阅读亮度 <input aria-label="阅读亮度" type="range" min="85" max="100" value={position.brightness} onChange={event=>setPosition(value=>({...value,brightness:Number(event.target.value)}))}/><output>{position.brightness}%</output></label><button className="text-link" onClick={()=>setPosition(value=>({...value,brightness:100}))}>恢复原色</button></div>}
    {searchOpen&&<div className="pdf-reader-search"><input autoFocus aria-label="查找文档内容" placeholder="查找文档内容" value={query} onChange={event=>setQuery(event.target.value)}/><span role="status">{searching?'查找中…':query?matches.length?`${matchIndex+1} / ${matches.length} 页`:'未找到':''}</span><button className="icon-button" aria-label="上一个匹配页" disabled={!matches.length} onClick={()=>go(matches[(matchIndex-1+matches.length)%matches.length])}><ChevronLeft size={15}/></button><button className="icon-button" aria-label="下一个匹配页" disabled={!matches.length} onClick={()=>go(matches[(matchIndex+1)%matches.length])}><ChevronRight size={15}/></button><button className="icon-button" aria-label="关闭查找" onClick={()=>setSearchOpen(false)}><X size={15}/></button></div>}
    {failure&&<div className="file-update-notice" role="alert"><span>{failure}</span><button className="text-link" onClick={()=>setRetry(value=>value+1)}>重试</button></div>}
    <div className={'pdf-reader-body'+(position.thumbnails?' with-thumbnails':'')}>
      {position.thumbnails&&document&&<nav className="pdf-thumbnails" aria-label="页面导航">{pages.map((base,index)=><Thumbnail key={index} document={document} page={index+1} base={base} selected={position.page===index+1} select={()=>go(index+1)}/>)}</nav>}
      {document?<PdfViewport ref={viewport} document={document} pages={pages} position={position} change={setPosition} query={searchOpen?query:''}/>:<div className="pdf-page-area">{!failure&&<p role="status" className="muted">正在读取页面…</p>}</div>}
    </div>
    <footer className="pdf-reader-footer">{typeof footer==='function'?footer(go):footer || <span>{name}</span>}{position.brightness<100&&<button className="text-link" onClick={()=>setPosition(value=>({...value,brightness:100}))}>恢复原色</button>}</footer>
  </section>;
}

type Anchor={page:number;x:number;y:number;clientX:number;clientY:number};
const PdfViewport=forwardRef<ViewportActions,{document:PDFDocumentProxy;pages:PageSize[];position:ReadingPosition;change:(update:(value:ReadingPosition)=>ReadingPosition)=>void;query:string}>(function PdfViewport({document,pages,position,change,query},ref){
  const area=useRef<HTMLDivElement>(null),frame=useRef(0);
  const [size,setSize]=useState({width:600,height:700,padding:20}),[visible,setVisible]=useState({first:position.page-1,last:position.page});
  const mode=position.mode||'continuous';
  const pending=useRef<Anchor|null>({page:position.page,x:position.x||0,y:position.y||0,clientX:0,clientY:0});
  const layout=useMemo(()=>{let top=0;return pages.map(base=>{const scale=position.zoom==='width'?size.width/base.width:position.zoom==='page'?Math.min(size.width/base.width,size.height/base.height):Number(position.zoom)/100;const width=base.width*scale,height=base.height*scale,result={width,height,scale,top};top+=height+16;return result;});},[pages,size,position.zoom]);
  function pageAt(offset:number){let low=0,high=layout.length-1;while(low<high){const mid=Math.ceil((low+high)/2);if(layout[mid].top<=offset)low=mid;else high=mid-1;}return low;}
  function capture(clientX=0,clientY?:number):Anchor|null{
    const element=area.current;if(!element||!layout.length)return null;
    const index=mode==='single'?position.page-1:pageAt(element.scrollTop+(clientY??size.height/2)),page=element.querySelector<HTMLElement>(`[data-reading-page="${index+1}"]`);if(!page)return null;
    const bounds=element.getBoundingClientRect(),rect=page.getBoundingClientRect();
    return {page:index+1,x:(bounds.left+size.padding+clientX-rect.left)/rect.width,y:(bounds.top+size.padding+(clientY??0)-rect.top)/rect.height,clientX,clientY:clientY??0};
  }
  function update(){
    const element=area.current;if(!element||!layout.length)return;
    const first=mode==='single'?position.page-1:pageAt(Math.max(0,element.scrollTop-element.clientHeight)),last=mode==='single'?first:pageAt(element.scrollTop+element.clientHeight*2);
    setVisible(previous=>previous.first===first&&previous.last===last?previous:{first,last});
    const anchor=capture();if(anchor)change(previous=>previous.page===anchor.page&&Math.abs((previous.x||0)-anchor.x)<.0001&&Math.abs((previous.y||0)-anchor.y)<.0001?previous:{...previous,page:anchor.page,x:anchor.x,y:anchor.y});
  }
  function zoom(value:string,x=size.width/2,y=size.height/2){pending.current=capture(x,y);change(previous=>({...previous,zoom:value}));}
  function zoomBy(factor:number,x?:number,y?:number){const current=(layout[position.page-1]?.scale||1)*100,target=factor>1?Math.max(current,Math.min(400,current*factor)):Math.min(current,Math.max(10,current*factor));zoom(String(Math.round(target*100)/100),x,y);}
  const latest=useRef({capture,update,zoomBy});latest.current={capture,update,zoomBy};
  useImperativeHandle(ref,()=>({zoom,zoomBy,go(page){
    if(mode==='single'){pending.current={page,x:0,y:0,clientX:0,clientY:0};change(previous=>({...previous,page,x:0,y:0}));}
    else if(area.current){area.current.scrollTop=layout[page-1]?.top||0;area.current.scrollLeft=0;update();}
  }}));
  useLayoutEffect(()=>{
    const element=area.current!;
    const measure=()=>{const padding=parseFloat(getComputedStyle(element).paddingLeft)||0,next={width:Math.max(80,element.clientWidth-padding*2),height:Math.max(80,element.clientHeight-padding*2),padding};
      setSize(previous=>{if(previous.width===next.width&&previous.height===next.height&&previous.padding===next.padding)return previous;if(!pending.current)pending.current=latest.current.capture();return next;});};
    measure();const observer=new ResizeObserver(measure);observer.observe(element);
    return()=>observer.disconnect();
  },[]);
  useLayoutEffect(()=>{
    const element=area.current!;const anchor=pending.current||{page:position.page,x:position.x||0,y:position.y||0,clientX:0,clientY:0};
    const page=element.querySelector<HTMLElement>(`[data-reading-page="${Math.min(anchor.page,pages.length)}"]`);
    if(page){const bounds=element.getBoundingClientRect(),rect=page.getBoundingClientRect();element.scrollLeft+=rect.left+anchor.x*rect.width-bounds.left-size.padding-anchor.clientX;element.scrollTop+=rect.top+anchor.y*rect.height-bounds.top-size.padding-anchor.clientY;pending.current=null;}
    update();
  },[document,layout,mode,mode==='single'?position.page:0]);
  useLayoutEffect(()=>()=>{cancelAnimationFrame(frame.current);latest.current.update();},[]);
  useEffect(()=>{
    const element=area.current!;
    const wheel=(event:WheelEvent)=>{if(!event.ctrlKey&&!event.metaKey)return;event.preventDefault();event.stopPropagation();const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?element.clientHeight:1),bounds=element.getBoundingClientRect();latest.current.zoomBy(Math.exp(-Math.max(-100,Math.min(100,delta))*.002),event.clientX-bounds.left-size.padding,event.clientY-bounds.top-size.padding);};
    element.addEventListener('wheel',wheel,{passive:false});return()=>element.removeEventListener('wheel',wheel);
  },[size.padding]);
  const shown=layout.map((page,index)=>({...page,index})).filter(page=>mode==='continuous'||page.index===position.page-1);
  return <div className="pdf-page-area" ref={area} tabIndex={0} aria-label="文档页面" onScroll={()=>{cancelAnimationFrame(frame.current);frame.current=requestAnimationFrame(()=>latest.current.update());}}><div className="pdf-page-list" style={{width:Math.max(size.width,...shown.map(page=>page.width))}}>{shown.map(({index,width,height,scale})=><div key={index} className="pdf-page-slot" data-reading-page={index+1} style={{width,height}}>{(mode==='single'||index>=visible.first&&index<=visible.last)&&<PdfPage document={document} page={index+1} width={width+40} height={height+40} zoom={String(scale*100)} brightness={position.brightness} query={query}/>}</div>)}</div></div>;
});

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
    })().catch(error=>{if(active&&error.name!=='RenderingCancelledException'){console.error('pdf_reader_page_failed',{fingerprint:document.fingerprints[0],page,reason:error.message});setFailure('此页未能显示：'+error.message);}});
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
    {!ready&&!failure&&!thumbnail&&<p role="status" className="pdf-page-loading muted">正在绘制第 {page} 页…</p>}
  </div>;
}

function Thumbnail({document,page,base,selected,select}:{document:PDFDocumentProxy;page:number;base:PageSize;selected:boolean;select:()=>void}) {
  const target=useRef<HTMLButtonElement>(null),[visible,setVisible]=useState(false);
  useEffect(()=>{const observer=new IntersectionObserver(([entry])=>setVisible(entry.isIntersecting),{rootMargin:'120px'});if(target.current)observer.observe(target.current);return ()=>observer.disconnect();},[]);
  useEffect(()=>{const element=target.current,parent=element?.parentElement;if(!selected||!element||!parent)return;const item=element.getBoundingClientRect(),bounds=parent.getBoundingClientRect();if(item.top<bounds.top)parent.scrollTop+=item.top-bounds.top;else if(item.bottom>bounds.bottom)parent.scrollTop+=item.bottom-bounds.bottom;},[selected]);
  return <button ref={target} className="pdf-thumbnail" aria-label={'第 '+page+' 页'} aria-current={selected?'page':undefined} onClick={select}>
    <div className="pdf-thumbnail-image" style={{aspectRatio:`${base.width}/${base.height}`}}>{visible&&<PdfPage document={document} page={page} width={88} height={88*base.height/base.width} zoom="width" thumbnail/>}</div><span>{page}</span>
  </button>;
}
