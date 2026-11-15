import {useCallback,useEffect,useRef,useState,type ReactNode} from 'react';
import {ArrowLeft,Check,Folder,LayoutTemplate,LoaderCircle,MousePointer2,Palette,PanelRight,Plus,Puzzle,RefreshCw,Search,Sparkles} from 'lucide-react';
import {useWorkbench} from '../../host/Host';
import {useHostField} from '../../host/context';
import {themeStyle} from '../../theme';
import PluginFrame from '../PluginFrame';
import PluginManager from '../PluginManager';
import {Appearance} from '../../management/Preferences';
import './creation.css';

const call=<T=any,>(method:string,params?:unknown)=>window.cowork.call<T>(method,params);
export const creationScopes=['整个界面','首页布局','侧栏导航','顶部工具栏','对话输入区','工作区','管理页面','主题'];

export default function CreationWorkbench({children}:{children:ReactNode}) {
  const c=useWorkbench(),{current,registry,creationTab:tab,setCreationTab:setTab,creationScope:scope,setCreationScope:setScope}=c;
  const cid=current!.id;
  const [picking,setPicking]=useState(false),[comparing,setComparing]=useState(false),[busy,setBusy]=useState(false),[failure,setFailure]=useState('');
  const [resolved,setResolved]=useState<any>(null),[selectedTheme,setSelectedTheme]=useState<any>(null),[candidate,setCandidate]=useState<any>(null);
  const [candidateName,setCandidateName]=useState(''),[hasInterface,setHasInterface]=useState(false),[extension,setExtension]=useState(0);
  const [previewPage,setPreviewPage]=useState('首页布局');
  const [status,setStatus]=useState('原对话保留，设计任务单独保存。');
  const alive=useRef(true),generation=useRef(0),candidateIdentity=useRef(''),selectedId=c.selectedId;
  const plugin=c.pluginPreview;
  const clearPlugin=()=>c.host.set(c.host.sessionKey('pluginPreview',cid),null);
  const [lastTheme,setLastTheme]=useHostField<string>('plugin-state:cowork.creation:previous-theme','');
  const [activeTheme,setActiveTheme]=useState('');
  const ready = Boolean(candidate||plugin);
  async function selectTab(next:string){if(next==='design')await c.goRoute('chat');if(alive.current&&selectedId.current===cid)setTab(next);}
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;generation.current++;};},[]);
  const refresh=useCallback(async()=>{
    const request=++generation.current;
    const [themes,preview,generated]=await Promise.all([
      call('themes.list',{conversation_id:cid}),call('themes.preview',{conversation_id:cid}),call('creation.candidate',{conversation_id:cid})]);
    const dark=themes.appearance.mode==='dark'||themes.appearance.mode==='system'&&matchMedia('(prefers-color-scheme: dark)').matches;
    const active=await call('themes.resolve',{dark});
    if(!alive.current||request!==generation.current)return;
    setActiveTheme(themes.active);setResolved(active);setCandidate(preview.preview?preview:null);setHasInterface(Boolean(generated.path));
    if(preview.preview?.preview_id!==candidateIdentity.current){candidateIdentity.current=preview.preview?.preview_id||'';setCandidateName(preview.preview?.name||'');}
  },[cid]);
  useEffect(()=>{
    void refresh().catch(reason=>{if(alive.current)setFailure(reason.message);});
    return window.cowork.onEvents(events=>{
      if(events.some(event=>event.event==='appearance_changed'||event.conversation_id===cid&&event.event==='runtime'&&['tool_result','final','error'].includes(event.data?.type)))void refresh().catch(reason=>{if(alive.current)setFailure(reason.message);});
    });
  },[refresh,cid]);
  useEffect(()=>{setExtension(0);setComparing(false);},[plugin?.hash]);
  async function perform(action:()=>Promise<void>){setBusy(true);setFailure('');try{await action();}catch(reason){if(alive.current)setFailure((reason as Error).message);}finally{if(alive.current)setBusy(false);}}
  async function previewPlugin(){await perform(async()=>{const value=await call('plugins.preview_generated',{conversation_id:cid});if(selectedId.current!==cid||!alive.current){if(value?.token)await call('plugins.discard',{token:value.token});return;}c.setPluginPreview(value);setComparing(false);});}
  async function discard(){await perform(async()=>{if(plugin?.token){await call('plugins.discard',{token:plugin.token});clearPlugin();}else if(candidate){await call('themes.discard_preview',{conversation_id:cid,id:candidate.preview.preview_id,revision:candidate.preview.revision});await refresh();}if(alive.current){setComparing(false);setStatus('候选已放弃，设计描述和生成文件保留。');}});}
  async function apply(){await perform(async()=>{if(plugin){await registry.apply(plugin,c.saves);clearPlugin();}else if(candidate){const previous=activeTheme;await call('themes.commit_preview',{conversation_id:cid,id:candidate.preview.preview_id,revision:candidate.preview.revision,name:candidateName});setLastTheme(previous);await refresh();}if(alive.current){setComparing(false);setSelectedTheme(null);setStatus('已应用到个人界面，退出创造后继续使用。');}});}
  async function seed(text:string,region:string){
    if(c.draft.trim()||c.attachments.length){setStatus('当前输入已保留。请先发送或清空后使用快捷设计。');setTab('design');return;}
    setScope(region);setStatus('已选择'+region+'，补充要求后发送。');c.setDraft(text);setTab('design');await c.saveDraft(cid);c.composer.current?.focus();
  }
  async function recover(){await perform(async()=>{
    if(await c.confirm('恢复界面？','恢复内置工作台和上次应用前的主题。对话、设计草稿与生成文件保留。',['取消','恢复界面'])!==1)return;
    if(!alive.current||selectedId.current!==cid)return;
    await registry.recover(c.saves);if(lastTheme)await call('themes.activate',{id:lastTheme});if(alive.current){setSelectedTheme(null);setComparing(false);await refresh();setStatus('界面已恢复，未应用候选仍可继续修改。');}
  });}
  const previewTheme=comparing?resolved:tab==='themes'?selectedTheme||resolved:candidate?.resolved||resolved;
  const picked=(value:string)=>{setScope(value);setPicking(false);setTab('design');setStatus('已选中'+value+'，在右侧描述修改要求。');};
  const selected=plugin?.contributes[extension]||plugin?.contributes[0];
  const original=selected?registry.resolve(selected.kind,selected.target)[0]:undefined;
  return <div className="creation-workbench">
    <header className="creation-header"><div><Sparkles size={18}/><strong>创造模式</strong><span className="creation-status">{ready?'预览中 · 尚未应用':'设计我的工作台'}</span></div><div>
      <button className={picking?'secondary selected':'secondary'} aria-pressed={picking} disabled={Boolean(plugin)||c.active} onClick={()=>setPicking(!picking)}><MousePointer2 size={15}/>{picking?'点击预览中的区域':'选择界面区域'}</button>
      <button className="secondary" disabled={busy||registry.switching} onClick={()=>void perform(c.exitCreation)}><ArrowLeft size={15}/>退出创造</button>
    </div></header>
    <div className="creation-layout">
      <section className="creation-canvas" aria-label="界面设计预览">
        <div className="creation-preview-toolbar"><span>{plugin?plugin.name:'我的界面'} · {comparing?'原版':'预览'}</span><div>
          {plugin?<label>区域<select aria-label="候选预览区域" value={extension} onChange={event=>setExtension(Number(event.target.value))}>{plugin.contributes.map((item,i)=><option value={i} key={item.id}>{item.title||item.target}</option>)}</select></label>:
          <><button aria-pressed={previewPage==='首页布局'} onClick={()=>setPreviewPage('首页布局')}>首页</button><button aria-pressed={previewPage==='工作区'} onClick={()=>setPreviewPage('工作区')}>工作区</button><button aria-pressed={previewPage==='管理页面'} onClick={()=>setPreviewPage('管理页面')}>管理页面</button></>}
        </div></div>
        <div className="creation-preview-content">
          {tab!=='themes'&&plugin&&comparing&&original&&!original.plugin.builtin?<PluginFrame key={'original'+original.plugin.hash+original.extension.id} plugin={original.plugin} extension={original.extension} mode="preview" scope={{extensionId:original.extension.id}}/>:tab!=='themes'&&plugin&&!comparing&&selected?<PluginFrame key={plugin.hash+selected.id} plugin={plugin} extension={selected} mode="preview" scope={{extensionId:selected.id,conversationId:cid}}/>:
            <WorkbenchPreview theme={previewTheme} page={previewPage} scope={scope} picking={picking} pick={picked}/>}
        </div>
        <div className="creation-canvas-footer"><span>独立预览 · 应用后才改变日常界面</span><button onClick={()=>void recover()} disabled={busy||registry.switching}><RefreshCw size={14}/>恢复界面</button></div>
      </section>
      <aside className="creation-tools" aria-label="创造工具">
        <nav className="creation-tabs" aria-label="创造工具类型">{[['design','AI 设计'],['themes','主题'],['plugins','插件']].map(([id,label])=><button key={id} aria-pressed={tab===id} onClick={()=>void selectTab(id)}>{label}</button>)}</nav>
        {failure&&<div className="creation-error" role="alert"><p>{failure}</p><button className="secondary" onClick={()=>void perform(refresh)}>重新读取</button></div>}
        {ready&&tab!=='themes'&&<section className="creation-candidate"><div className="creation-candidate-heading"><strong>{plugin?'界面候选':candidate.preview.name}</strong><span>仅预览</span></div>
          {!plugin&&<label>主题名称<input aria-label="候选主题名称" value={candidateName} onChange={event=>setCandidateName(event.target.value)}/></label>}
          <div className="button-row"><button className="secondary" onClick={()=>setComparing(!comparing)}>{comparing?'返回候选':'查看原版'}</button><button className="secondary" disabled={busy||registry.switching} onClick={()=>void discard()}>放弃</button><button className="primary" disabled={busy||registry.switching||!plugin&&!candidateName.trim()} onClick={()=>void apply()}>{busy||registry.switching?<LoaderCircle size={14} className="spin"/>:<Check size={14}/>}应用</button></div>
        </section>}
        {hasInterface&&!plugin&&<div className="creation-generated"><span>已有生成的界面</span><button className="secondary" disabled={busy} onClick={()=>void previewPlugin()}>预览界面候选</button></div>}
        <div className="creation-design-controls" hidden={tab!=='design'}><label>修改范围<select aria-label="修改范围" value={scope} disabled={c.active} onChange={event=>{setScope(event.target.value);setStatus('已选择'+event.target.value+'，在下方描述修改要求。');}}>{creationScopes.map(value=><option key={value}>{value}</option>)}</select></label>
          <div className="button-row"><button onClick={()=>void seed('重新设计我的工作台，让常用入口更容易找到，输入区更简洁。','整个界面').catch(reason=>setFailure(reason.message))}><LayoutTemplate size={14}/>自由设计工作台</button><button onClick={()=>void seed('设计一个柔和、适合长时间阅读的主题。先生成候选供我预览。','主题').catch(reason=>setFailure(reason.message))}><Palette size={14}/>AI 主题</button></div>
        </div>
        <div className="creation-assistant" hidden={tab!=='design'}>{children}</div>
        {tab==='themes'&&<div className="creation-management">{ready&&<button className="secondary" onClick={()=>setTab('design')}>返回 AI 候选</button>}<Appearance settings={c.settings} updateSettings={c.setSettings} onError={setStatus} onDirty={()=>{}} confirm={c.confirm} kind="appearance" previewChanged={setSelectedTheme} applied={setLastTheme}/></div>}
        {tab==='plugins'&&<div className="creation-management"><PluginManager/></div>}
        <div className="creation-tools-footer" role="status">{status}</div>
      </aside>
    </div>
  </div>;
}

function WorkbenchPreview({theme,page,scope,picking,pick}:{theme:any;page:string;scope:string;picking:boolean;pick:(scope:string)=>void}) {
  const region=(name:string)=>({'data-creation-region':name,className:'creation-region '+(scope===name?'region-selected':''),role:picking?'button':undefined,tabIndex:picking?0:undefined,'aria-label':picking?'选择'+name:undefined,onClick:()=>{if(picking)pick(name);},onKeyDown:(event:React.KeyboardEvent)=>{if(picking&&['Enter',' '].includes(event.key)){event.preventDefault();pick(name);}}});
  return <div className={'creation-sample '+(picking?'is-picking':'')} style={themeStyle(theme)}>
    <aside {...region('侧栏导航')}><strong>DeepSeek Cowork</strong><span><Plus size={15}/>新聊天</span><span><Search size={15}/>搜索</span><small>项目</small><span><Folder size={15}/>我的工作空间</span><small>最近聊天</small><span className="sample-selected">设计我的工作台</span><span>整理本周工作</span><footer><span>能力</span><span>资料库</span><span>常用任务</span><span>设置</span></footer></aside>
    <main><header {...region('顶部工具栏')}><span>我的工作台</span><PanelRight size={16}/></header>
      <section {...region(page)}>{page==='首页布局'?<><p>从一个想法，开始今天的工作</p><h2>今天，想一起完成什么？</h2><p>整理资料、分析数据，或把一个想法做出来。</p><div className="sample-tasks"><small>继续最近的任务</small><span>整理本周工作</span><span>产品方案初稿</span></div></>:page==='工作区'?<><h2>工作区</h2><div className="sample-workspace-tabs">对话　文件　任务　终端</div><h3>项目笔记.md</h3><p>保留想法与已完成的成果。</p></>:<><h2>能力</h2><div className="sample-workspace-tabs">技能　MCP 服务　专家　插件</div><div className="sample-plugin"><Puzzle size={20}/><div>创造模式<p>设计整个界面、主题与插件</p></div></div></>}</section>
      <div {...region('对话输入区')}><div className="sample-composer">描述你想完成的事…<div><Plus size={14}/><span>专家</span><span>模式</span><span>DeepSeek ↑</span></div></div><small>工作目录 · 聊天工作目录</small></div>
    </main>
  </div>;
}
