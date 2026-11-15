import {useEffect, useRef, useState} from 'react';
import './mermaid.css';

// Mermaid has process-global configuration. Serialize theme setup and rendering.
let renderQueue: Promise<unknown> = Promise.resolve();
let nextId = 0;

export default function MermaidDiagram({source, pending = false}: {source: string; pending?: boolean}) {
  const root = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false), [theme, setTheme] = useState('');
  const [result, setResult] = useState<{source: string; url: string; width: number} | null>(null);
  const [failure, setFailure] = useState(''), [retry, setRetry] = useState(0);
  const [showSource, setShowSource] = useState(false), [zoom, setZoom] = useState<number | null>(null);
  const [copyStatus, setCopyStatus] = useState('');
  useEffect(() => {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {setVisible(true); observer.disconnect();}
    }, {rootMargin: '300px'});
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const update = () => {
      if (!root.current) return;
      const styles = getComputedStyle(root.current);
      setTheme(JSON.stringify(['--text', '--soft', '--line', '--bg'].map(key => styles.getPropertyValue(key).trim()).concat(styles.fontFamily)));
    };
    update();
    const observer = new MutationObserver(update);
    for (let element: HTMLElement | null = root.current; element; element = element.parentElement) {
      observer.observe(element, {attributes: true, attributeFilter: ['style', 'class', 'data-theme']});
    }
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible || pending || !theme) return;
    let alive = true;
    setFailure('');
    const id = `cowork-mermaid-${++nextId}`;
    renderQueue = renderQueue.catch(() => {}).then(async () => {
      if (!alive) return;
      const {default: mermaid} = await import('mermaid');
      if (!alive) return;
      const [text, soft, line, background, fontFamily] = JSON.parse(theme);
      mermaid.initialize({startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true,
        maxTextSize: 50000, maxEdges: 500, htmlLabels: false, theme: 'base', fontFamily,
        secure: ['secure', 'securityLevel', 'startOnLoad', 'maxTextSize', 'maxEdges', 'suppressErrorRendering', 'htmlLabels', 'theme', 'themeVariables', 'themeCSS', 'fontFamily', 'flowchart', 'dompurifyConfig'],
        dompurifyConfig: {FORBID_TAGS: ['style', 'img', 'image', 'iframe', 'object', 'embed']},
        flowchart: {htmlLabels: false},
        themeVariables: {primaryColor: soft, primaryTextColor: text, primaryBorderColor: line,
          lineColor: text, secondaryColor: background, tertiaryColor: soft, background,
          textColor: text, clusterBkg: background, clusterBorder: line, fontFamily}});
      const container = document.createElement('div');
      container.style.cssText = 'position:fixed;left:-100000px;top:0;visibility:hidden;pointer-events:none';
      document.body.append(container);
      try {
        const {svg} = await mermaid.render(id, source, container);
        const parsed = new DOMParser().parseFromString(svg, 'image/svg+xml').documentElement;
        const width = Number(parsed.getAttribute('viewBox')?.split(/\s+/)[2]) || 800;
        // An image isolates diagram styles and disables SVG scripts/links in the host.
        if (alive) setResult({source, width, url: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)});
      } finally {container.remove();}
    }).catch(() => {
      if (alive) {
        setFailure('图表未能渲染，请检查 Mermaid 语法后重试。源码已保留。');
        console.warn('mermaid_render_failed', {id, sourceLength: source.length});
      }
    });
    return () => {alive = false;};
  }, [source, pending, visible, theme, retry]);
  const current = result?.source === source ? result : null;
  return <section ref={root} className="mermaid-diagram" aria-label="Mermaid 图表">
    <div className="mermaid-toolbar">
      <button onClick={() => setShowSource(value => !value)} aria-pressed={showSource}>{showSource ? '查看图表' : '查看源码'}</button>
      <button onClick={async () => {
        try {await window.cowork.call('clipboard.write', {text: source}); setCopyStatus('已复制源码');}
        catch {setCopyStatus('复制失败，请选择源码后复制。');}
      }}>复制源码</button>
      {!showSource && current && <><button aria-label="缩小图表" onClick={() => setZoom(value => Math.max(.25, (value ?? 1) - .25))}>−</button><button aria-label="放大图表" onClick={() => setZoom(value => Math.min(3, (value ?? 1) + .25))}>+</button><button onClick={() => setZoom(null)}>适应宽度</button></>}
    </div>
    {copyStatus && <p role="status">{copyStatus}</p>}
    {failure && <p role="alert" className="error-text">{failure} <button onClick={() => setRetry(value => value + 1)}>重试</button></p>}
    {!showSource && current && <div className="mermaid-viewport" tabIndex={0} aria-label="图表，可横向滚动"><img alt="Mermaid 图表，文字内容可通过查看源码阅读" src={current.url} style={{width: zoom == null ? '100%' : current.width * zoom, maxWidth: zoom == null ? current.width : 'none'}}/></div>}
    {!showSource && !current && !failure && <p role="status">{pending ? '图表代码正在生成…' : '正在绘制图表…'}</p>}
    {(showSource || failure || pending) && <pre><code>{source}</code></pre>}
  </section>;
}
