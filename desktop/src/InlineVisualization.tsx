import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';

export default function InlineVisualization({ cid, file }: {cid: string; file: string}) {
  const root = useRef<HTMLElement>(null), frame = useRef<HTMLIFrameElement>(null);
  const [visible, setVisible] = useState(false), [value, setValue] = useState<any>(null);
  const [failure, setFailure] = useState(''), [height, setHeight] = useState(180), [retry, setRetry] = useState(0);
  const pending = useRef<Record<string, any> | null>(null), queue = useRef(Promise.resolve());
  useEffect(() => {
    const observer = new IntersectionObserver(entries => {if (entries.some(entry => entry.isIntersecting)) {setVisible(true); observer.disconnect();}}, {rootMargin: '500px'});
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible) return;
    let alive = true, token = '';
    const release = () => {if (token) void window.cowork.call('visualization.release', {token});};
    void window.cowork.call('visualization.get', {conversation_id: cid, file}).then(result => {token=result.token;if (alive) {setValue(result); setFailure('');} else release();}).catch(error => {if (alive) setFailure(error.message);});
    return () => {alive = false;release();};
  }, [cid, file, visible, retry]);
  const theme = useCallback(() => {
    if (!value) return;
    const styles = getComputedStyle(document.documentElement);
    const css = `:root{color-scheme:${document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'};font-family:${styles.fontFamily};color:${styles.getPropertyValue('--text')};background:${styles.getPropertyValue('--bg')};}body{margin:12px;overflow-wrap:anywhere}button,input,select,textarea{font:inherit}*{box-sizing:border-box}`;
    frame.current?.contentWindow?.postMessage({token:value.token, type:'theme', css}, '*');
  }, [value]);
  useEffect(() => {
    if (!value) return;
    const observer = new MutationObserver(theme);
    observer.observe(document.documentElement, {attributes:true,attributeFilter:['style','data-theme']});
    const listener = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.token !== value.token) return;
      if (event.data.type === 'height') setHeight(Math.max(180, Math.min(900, Number(event.data.height) || 420)));
      if (event.data.type === 'error') setFailure(String(event.data.message || '可视化运行失败。'));
      if (event.data.type === 'state') {
        const state = event.data.state;
        if (!state || typeof state !== 'object' || Array.isArray(state)) {setFailure('交互状态格式无效，尚未保存。'); return;}
        pending.current = state;
        queue.current = queue.current.then(async () => {
          await window.cowork.call('visualization.state', {conversation_id:cid, file, sha256:value.sha256, state});
          if (pending.current === state) pending.current = null;
        }).catch(error => setFailure('交互状态保存失败：' + error.message));
      }
    };
    window.addEventListener('message', listener);
    return () => {observer.disconnect(); window.removeEventListener('message', listener);};
  }, [cid, file, value, theme]);
  return <section ref={root} className="inline-visualization" aria-label={value?.title || '交互可视化'}>
    {value?.title && <strong>{value.title}{value.read_only && <small> · Visualize 已停用，仅供查看</small>}</strong>}
    {failure && <div className="inline-error" role="alert">{failure}<button onClick={async () => {
      if (pending.current && value) {try {await queue.current; if (!pending.current) {setFailure(''); return;} await window.cowork.call('visualization.state', {conversation_id:cid,file,sha256:value.sha256,state:pending.current}); pending.current=null;setFailure('');} catch(error) {setFailure((error as Error).message);}}
      else setRetry(number => number + 1);
    }}><RefreshCw size={14}/>重试</button></div>}
    {value ? <iframe ref={frame} title={value.title || '交互可视化'} sandbox="allow-scripts" referrerPolicy="no-referrer" src={value.url} tabIndex={value.read_only ? -1 : 0} style={{height,pointerEvents:value.read_only?'none':undefined}} onLoad={theme}/> : !failure && <p>正在准备可视化…</p>}
  </section>;
}
