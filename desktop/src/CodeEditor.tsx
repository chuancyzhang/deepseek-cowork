import {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {CornerDownRight, Search, WrapText} from 'lucide-react';
import {useFileToolbarContribution} from './FileToolbar';
import type {MenuAction} from './MoreActions';
import {Compartment, EditorState, StateEffect, type Extension} from '@codemirror/state';
import {EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap, indentWithTab} from '@codemirror/commands';
import {bracketMatching, foldGutter, foldKeymap, indentOnInput, syntaxHighlighting, HighlightStyle} from '@codemirror/language';
import {tags} from '@lezer/highlight';
import {closeBrackets, closeBracketsKeymap} from '@codemirror/autocomplete';
import {searchKeymap, highlightSelectionMatches, openSearchPanel, gotoLine} from '@codemirror/search';
import {useHost} from './host/context';
import type {FileSession} from './types';

export async function languageExtension(language: string): Promise<Extension> {
  switch (language) {
    case 'java': return (await import('@codemirror/lang-java')).java();
    case 'python': return (await import('@codemirror/lang-python')).python();
    case 'javascript': case 'typescript': case 'jsx': case 'tsx':
      return (await import('@codemirror/lang-javascript')).javascript({typescript: ['typescript','tsx'].includes(language), jsx: ['jsx','tsx'].includes(language)});
    case 'json': return (await import('@codemirror/lang-json')).json();
    case 'css': return (await import('@codemirror/lang-css')).css();
    case 'sql': return (await import('@codemirror/lang-sql')).sql();
    case 'xml': return (await import('@codemirror/lang-xml')).xml();
    case 'yaml': return (await import('@codemirror/lang-yaml')).yaml();
    default: return [];
  }
}
type Snapshot = {state: EditorState; top: number; left: number; wrap: boolean};
type Props = {cid: string; file: FileSession; text: string; saved: string; change: (text: string) => void; save: () => Promise<void>};
const highlight = HighlightStyle.define([
  {tag:tags.keyword,color:'var(--code-keyword)'}, {tag:[tags.string,tags.regexp],color:'var(--code-string)'},
  {tag:[tags.number,tags.bool,tags.null],color:'var(--code-number)'}, {tag:tags.comment,color:'var(--muted)',fontStyle:'italic'},
  {tag:[tags.typeName,tags.className],color:'var(--code-type)'}, {tag:tags.function(tags.variableName),color:'var(--link)'},
]);
export default function CodeEditor(props: Props) {
  const host = useHost(), key = host.sessionKey('code:' + props.file.path, props.cid);
  const snapshot = host.get<Snapshot | null>(key, null);
  const root = useRef<HTMLDivElement>(null), editor = useRef<EditorView | null>(null), latest = useRef(props); latest.current = props;
  const [wrap, setWrap] = useState(snapshot?.wrap || false), [position, setPosition] = useState('1:1'), [failure, setFailure] = useState('');
  const language = props.file.payload.language || 'text';
  const toolbar=useMemo(()=>({controls:<button className="file-toolbar-action" title="查找替换 (Ctrl+F)" aria-label="查找替换" onClick={()=>editor.current&&openSearchPanel(editor.current)}><Search size={15}/><span>查找替换</span></button>,menu:[
    ['跳转行',CornerDownRight,async()=>{if(editor.current)gotoLine(editor.current);}],
    [wrap?'取消自动换行':'自动换行',WrapText,async()=>setWrap(value=>!value)],
  ] as MenuAction[]}),[wrap]);
  const integrated=useFileToolbarContribution(toolbar);
  const wrapping = useRef(new Compartment()), syntax = useRef(new Compartment());
  useLayoutEffect(() => {
    let alive = true;
    const extensions: Extension[] = [lineNumbers(), highlightActiveLine(), highlightActiveLineGutter(), drawSelection(), history(), indentOnInput(),
      bracketMatching(), closeBrackets(), foldGutter(), highlightSelectionMatches(), syntaxHighlighting(highlight),
      EditorState.readOnly.of(Boolean(props.file.payload.read_only)), EditorView.editable.of(!props.file.payload.read_only),
      EditorState.lineSeparator.of(props.text.includes('\r\n') ? '\r\n' : '\n'),
      EditorView.contentAttributes.of({'aria-label': '代码正文', spellcheck: 'false'}),
      EditorState.phrases.of({'Find':'查找','Replace':'替换','next':'下一项','previous':'上一项','all':'全部',
        'match case':'区分大小写','regexp':'正则表达式','by word':'全词匹配','replace':'替换','replace all':'全部替换',
        'close':'关闭','Go to line':'跳转到行','go':'跳转','Fold line':'折叠代码','Unfold line':'展开代码',
        'current match':'当前匹配','on line':'所在行','replaced $ matches':'已替换 $ 处','replaced match on line $':'已替换第 $ 行匹配项'}),
      keymap.of([{key:'Mod-s', run:()=>{void latest.current.save().catch(error=>setFailure(error.message));return true;}}, ...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, ...searchKeymap, ...foldKeymap, indentWithTab]),
      wrapping.current.of(wrap ? EditorView.lineWrapping : []), syntax.current.of([]),
      EditorView.updateListener.of(update => {
        if (update.docChanged) latest.current.change(update.state.sliceDoc());
        const selection = update.state.selection.main, line = update.state.doc.lineAt(selection.head);
        setPosition(`${line.number}:${selection.head - line.from + 1}`);
      }),
      EditorView.theme({'&': {height:'100%',color:'var(--text)',backgroundColor:'var(--bg)'}, '.cm-scroller':{fontFamily:'var(--mono-font)',overflow:'auto'},
        '.cm-content':{padding:'12px 0'}, '.cm-gutters':{backgroundColor:'var(--soft)',color:'var(--muted)',borderColor:'var(--line)'},
        '.cm-activeLine, .cm-activeLineGutter':{backgroundColor:'var(--hover)'}, '.cm-cursor':{borderLeftColor:'var(--text)'},
        '&.cm-focused .cm-selectionBackground, .cm-selectionBackground':{backgroundColor:'var(--hover)'},
        '.cm-panels':{backgroundColor:'var(--soft)',color:'var(--text)'}, '.cm-textfield, .cm-button':{background:'var(--bg)',color:'var(--text)',border:'1px solid var(--line)'}}),
    ];
    let state = snapshot && snapshot.state.sliceDoc() === props.text ? snapshot.state.update({effects:StateEffect.reconfigure.of(extensions)}).state : EditorState.create({doc:props.text, extensions});
    const view = new EditorView({state,parent:root.current!}); editor.current = view;
    if (snapshot) {view.scrollDOM.scrollTop=snapshot.top;view.scrollDOM.scrollLeft=snapshot.left;}
    const line = state.doc.lineAt(state.selection.main.head); setPosition(`${line.number}:${state.selection.main.head-line.from+1}`);
    void languageExtension(language).then(value=>{if(alive)view.dispatch({effects:syntax.current.reconfigure(value)});}).catch(error=>{if(alive)setFailure('语法高亮加载失败：'+error.message);});
    return () => {alive=false;host.set<Snapshot>(key,{state:view.state,top:view.scrollDOM.scrollTop,left:view.scrollDOM.scrollLeft,wrap:host.get<boolean>(key+':wrap',wrap)});view.destroy();editor.current=null;};
  }, [key, language]);
  useEffect(() => {host.set(key+':wrap',wrap);editor.current?.dispatch({effects:wrapping.current.reconfigure(wrap ? EditorView.lineWrapping : [])});}, [wrap,key]);
  useEffect(() => {
    const view=editor.current;
    if(view && view.state.sliceDoc()!==props.text)view.dispatch({changes:{from:0,to:view.state.doc.length,insert:props.text}});
  }, [props.text]);
  return <section className="code-editor" aria-label="源码编辑器">
    {!integrated&&<div className="code-editor-actions"><button onClick={()=>editor.current&&openSearchPanel(editor.current)}>查找替换</button><button onClick={()=>editor.current&&gotoLine(editor.current)}>跳转行</button><label><input type="checkbox" checked={wrap} onChange={event=>setWrap(event.target.checked)}/>自动换行</label></div>}
    {failure && <p className="error-text" role="alert">{failure}</p>}
    <div className="code-editor-surface" ref={root}/>
    <footer><span>{language === 'text' ? '纯文本' : language}</span><span>行:列 {position}</span><span>{props.text===props.saved?'已保存':'未保存'}</span></footer>
  </section>;
}
