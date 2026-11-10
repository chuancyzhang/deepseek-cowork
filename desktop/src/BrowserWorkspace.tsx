import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, ChevronDown, ChevronUp, ExternalLink, Globe, Hand, Play, RefreshCw, Search, X, ZoomIn, ZoomOut } from 'lucide-react';
import type { WorkbenchTab } from './Workspace';
const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);

export default function BrowserWorkspace({ cid, tab, obscured, error, refresh }: { cid: string; tab: WorkbenchTab; obscured: boolean; error: (message: string) => void; refresh: () => Promise<void> }) {
  const [address, setAddress] = useState(tab.url || ''), [busy, setBusy] = useState(false), [previewError, setPreviewError] = useState('');
  const [info, setInfo] = useState({canBack:false,canForward:false,zoom:1}), [find, setFind] = useState(false), [query, setQuery] = useState(''), [matches, setMatches] = useState('');
  const surface = useRef<HTMLDivElement>(null), searchInput = useRef<HTMLInputElement>(null);
  const active = tab.id, human = tab.owner === 'human';
  useEffect(() => {setAddress(tab.url || '');}, [tab.id, tab.url]);
  useEffect(() => {
    let alive = true;
    void call('browser.command', {conversation_id:cid,tab_id:active,command:'info'}).then(value => {if (alive && value.ok) setInfo(value);}).catch(failure => {if(alive) error(failure.message);});
    return () => {alive=false;};
  }, [cid, active, tab.url, tab.status, error]);
  useEffect(() => window.cowork.onEvents(events => {
    for (const event of events) if (event.conversation_id === cid && event.data?.tab_id === active) {
      if (event.event === 'browser_find') {setFind(true); setTimeout(() => searchInput.current?.focus(), 0);}
      if (event.event === 'browser_matches') setMatches(event.data.matches ? `${event.data.activeMatchOrdinal} / ${event.data.matches}` : '没有匹配');
    }
  }), [cid, active]);
  useEffect(() => {
    let stopped = false;
    const position = async () => {try {
      const rect = surface.current?.getBoundingClientRect();
      await call('browser.surface', {conversation_id:cid, tab_id:!obscured && rect ? active : '',bounds:rect ? {x:rect.x,y:rect.y,width:rect.width,height:rect.height}:null});
      if (!stopped) setPreviewError('');
    } catch (failure) {if (!stopped) setPreviewError((failure as Error).message);}};
    const observer = new ResizeObserver(() => {void position();});
    if (surface.current) observer.observe(surface.current);
    void position();
    return () => {stopped=true;observer.disconnect();void call('browser.surface',{tab_id:''}).catch(failure => error(failure.message));};
  }, [cid, active, tab.owner, tab.status, obscured, error]);
  async function action(method: string, params: any = {}) {
    setBusy(true);
    try {const result = await call(method,{conversation_id:cid,tab_id:active,...params});if(result.ok===false) throw new Error(result.error); if ('zoom' in result) setInfo(result);await refresh();}
    catch(failure){error((failure as Error).message);}finally{setBusy(false);}
  }
  const command = (name: string, params: any = {}) => action('browser.command',{command:name,...params});
  const search = (reverse = false, next = false) => command('find',{text:query,reverse,next});
  return <div className="browser-workspace">
    <form className="browser-address" onSubmit={event => {event.preventDefault();void action('browser.navigate',{url:address});}}>
      <button type="button" className="icon-button" title="后退" disabled={busy || !human || !info.canBack} onClick={() => void command('back')}><ArrowLeft size={15}/></button>
      <button type="button" className="icon-button" title="前进" disabled={busy || !human || !info.canForward} onClick={() => void command('forward')}><ArrowRight size={15}/></button>
      <input aria-label="网页地址" placeholder="输入网址" value={address} onChange={event => setAddress(event.target.value)} disabled={busy || !human}/>
      <button className="icon-button" title="前往" disabled={busy || !address || !human}><ArrowRight size={16}/></button>
      <button type="button" className="icon-button" title="刷新页面" disabled={busy || !human} onClick={() => void command('reload')}><RefreshCw size={14}/></button>
    </form>
    <div className="browser-controls"><button className="secondary" disabled={busy} onClick={() => void action('workbench.control',{owner:human?'available':'human'})}>{human?<Play size={13}/>:<Hand size={13}/>} {human?'交还 AI':'接管网页'}</button><span role="status">{tab.status==='loading'?'正在载入…':tab.status==='failed'?'加载失败，可刷新重试':human?'由你操作':'AI 可操作'}</span>
      <button className="icon-button" title="查找网页内容" disabled={!human} onClick={() => {setFind(!find);if(find) void command('stop_find');}}><Search size={14}/></button>
      <button className="icon-button" title="缩小网页" disabled={!human || info.zoom<=.25} onClick={() => void command('zoom',{value:Math.max(.25,info.zoom-.1)})}><ZoomOut size={14}/></button>
      <button className="browser-zoom" title="恢复网页缩放" disabled={!human} onClick={() => void command('zoom',{value:1})}>{Math.round(info.zoom*100)}%</button>
      <button className="icon-button" title="放大网页" disabled={!human || info.zoom>=3} onClick={() => void command('zoom',{value:Math.min(3,info.zoom+.1)})}><ZoomIn size={14}/></button>
      <button className="icon-button" title="在系统浏览器打开" onClick={() => void call('external.open',{url:tab.url}).catch(failure => error(failure.message))}><ExternalLink size={14}/></button>
    </div>
    {find && <form className="browser-find" onSubmit={event => {event.preventDefault();void search();}}><input autoFocus ref={searchInput} aria-label="在网页中查找" value={query} onChange={event => setQuery(event.target.value)} disabled={!human} onKeyDown={event => {if(event.key==='Enter'){event.preventDefault();void search(event.shiftKey,true);}if(event.key==='Escape'){setFind(false);void command('stop_find');}}}/><small>{matches}</small><button type="button" title="上一个匹配" disabled={!human || !query} onClick={() => void search(true,true)}><ChevronUp size={14}/></button><button type="submit" title="下一个匹配" disabled={!human || !query}><ChevronDown size={14}/></button><button type="button" title="关闭查找" onClick={() => {setFind(false);if(human) void command('stop_find');}}><X size={14}/></button></form>}
    <div className="browser-surface" ref={surface}>{previewError && <p className="run-error">{previewError}</p>}</div>
  </div>;
}
