import {useCallback, useEffect, useRef, useState, type ReactNode} from 'react';
import {ArrowLeft, Check, LayoutTemplate, LoaderCircle, MousePointer2, Palette, RefreshCw, Sparkles} from 'lucide-react';
import {useWorkbench} from '../../host/Host';
import {useHostField} from '../../host/context';
import type {Plugin} from '../../types';
import PluginManager from '../PluginManager';
import {Appearance} from '../../management/Preferences';
import {CandidateView, previewPages, WorkbenchPreview} from './WorkbenchPreview';

const call = <T=any,>(method:string, params?:unknown) => window.cowork.call<T>(method,params);
export const creationScopes = ['整个界面','首页布局','侧栏导航','顶部工具栏','对话输入区','工作区','管理页面','主题'];
type PreviewState = {revision:number; plugin:Plugin|null; checking:boolean; error:string};
const emptyPreview:PreviewState = {revision:0,plugin:null,checking:false,error:''};
const defaultStatus = '原对话保留，设计任务单独保存。';

export default function CreationWorkbench({children}:{children:ReactNode}) {
  const c = useWorkbench();
  const {current,registry,creationTab:tab,setCreationTab:setTab,creationScope:scope,setCreationScope:setScope,selectedId} = c;
  const cid = current!.id;
  const [picking,setPicking] = useState(false);
  const [comparing,setComparing] = useState(false);
  const [busy,setBusy] = useState(false);
  const [failure,setFailure] = useState('');
  const [resolved,setResolved] = useState<any>(null);
  const [selectedTheme,setSelectedTheme] = useState<any>(null);
  const [candidate,setCandidate] = useState<any>(null);
  const [candidateName,setCandidateName] = useState('');
  const [hasInterface,setHasInterface] = useState(false);
  const [preview,setPreview] = useState(emptyPreview);
  const [extension,setExtension] = useState(0);
  const [previewPage,setPreviewPage] = useState('对话');
  const [status,setStatus] = useState(defaultStatus);
  const [lastTheme,setLastTheme] = useHostField<string>('plugin-state:cowork.creation:previous-theme','');
  const [activeTheme,setActiveTheme] = useState('');
  const alive = useRef(true),generation = useRef(0),candidateIdentity = useRef('');
  const localPlugin = c.pluginPreview;
  const plugin = localPlugin || preview.plugin;
  const selected = plugin?.contributes[extension] || plugin?.contributes[0];
  const activeWorkbench = registry.resolve('workbench','main').find(item => !item.plugin.builtin);
  const activeToolbar = registry.resolve('toolbar','chat')[0]?.extension;
  const ready = Boolean(candidate || plugin);
  const session = {conversation_id:cid};
  const clearLocalPlugin = () => c.host.set(c.host.sessionKey('pluginPreview',cid),null);
  const enabled = () => alive.current && selectedId.current === cid;

  const receivePreview = useCallback((value:PreviewState) => {
    if (alive.current && selectedId.current === cid) setPreview(previous => value.revision >= previous.revision ? value : previous);
  },[cid,selectedId]);
  const refresh = useCallback(async () => {
    const request = ++generation.current;
    const session = {conversation_id:cid};
    const [themes,theme,generated] = await Promise.all([
      call('themes.list',session),call('themes.preview',session),call('creation.candidate',session),
    ]);
    const dark = themes.appearance.mode === 'dark' || themes.appearance.mode === 'system' && matchMedia('(prefers-color-scheme: dark)').matches;
    const active = await call('themes.resolve',{dark});
    if (!alive.current || request !== generation.current) return;
    setActiveTheme(themes.active);setResolved(active);setCandidate(theme.preview ? theme : null);setHasInterface(Boolean(generated.path));
    const id = theme.preview?.preview_id || '';
    if (id !== candidateIdentity.current) {
      candidateIdentity.current = id;setCandidateName(theme.preview?.name || '');
    }
  },[cid]);
  useEffect(() => {
    alive.current = true;
    const fail = (reason:Error) => {if (alive.current) setFailure(reason.message);};
    void refresh().catch(fail);
    const unsubscribe = window.cowork.onEvents(events => {
      for (const event of events) {
        if (event.event === 'creation_preview_changed' && event.conversation_id === cid) receivePreview(event.data);
      }
      if (events.some(event => event.event === 'appearance_changed' || event.conversation_id === cid &&
          event.event === 'runtime' && ['tool_result','final','error'].includes(event.data?.type))) void refresh().catch(fail);
    });
    void call<PreviewState>('creation.preview_state',{conversation_id:cid}).then(receivePreview).catch(fail);
    return () => {alive.current = false;generation.current++;unsubscribe();};
  },[cid,receivePreview,refresh]);
  useEffect(() => {
    setExtension(0);setComparing(false);setPicking(false);
    if (plugin) setStatus(defaultStatus);
    if (plugin?.contributes[0]?.kind === 'toolbar') setPreviewPage('对话');
  },[plugin?.hash]);

  async function perform(action:() => Promise<void>) {
    setBusy(true);setFailure('');
    try { await action(); }
    catch (reason) { if (enabled()) setFailure((reason as Error).message); }
    finally { if (enabled()) setBusy(false); }
  }
  async function tryPreview() {
    setFailure('');
    setStatus(defaultStatus);
    try { receivePreview(await call<PreviewState>('creation.preview',session)); }
    catch (reason) { if (enabled()) setFailure((reason as Error).message); }
  }
  async function selectTab(next:string) {
    if (next === 'design') await c.goRoute('chat');
    if (enabled()) setTab(next);
  }
  async function discard() {
    await perform(async () => {
      if (localPlugin?.token) {
        await call('plugins.discard',{token:localPlugin.token});
        clearLocalPlugin();
      } else if (plugin) {
        await call('creation.preview_clear',{...session,hash:plugin.hash});
      } else if (candidate) {
        await call('themes.discard_preview',{...session,id:candidate.preview.preview_id,revision:candidate.preview.revision});
        await refresh();
      }
      if (enabled()) {
        setComparing(false);setStatus('候选已放弃，设计描述和生成文件已保留。');
      }
    });
  }
  async function apply() {
    await perform(async () => {
      if (localPlugin) {
        await registry.apply(localPlugin,c.saves);
        clearLocalPlugin();
      } else if (plugin) {
        const exact = await call<Plugin>('creation.preview_use',{...session,hash:plugin.hash});
        try { await registry.apply(exact,c.saves); }
        catch (error) {
          void call('plugins.discard',{token:exact.token}).catch(reason => console.warn('creation_preview_release_failed',reason.message));
          throw error;
        }
        await call('creation.preview_clear',{...session,hash:plugin.hash});
      } else if (candidate) {
        await call('themes.commit_preview',{...session,id:candidate.preview.preview_id,revision:candidate.preview.revision,name:candidateName});
        setLastTheme(activeTheme);await refresh();
      }
      if (enabled()) {
        setComparing(false);setSelectedTheme(null);setStatus('已应用到日常工作台。');
      }
    });
  }
  async function seed(text:string, region:string) {
    if (c.draft.trim() || c.attachments.length) {
      setStatus('当前输入已保留。请先发送或清空后使用快捷设计。');setTab('design');return;
    }
    setScope(region);setStatus('已选择'+region+'，补充要求后发送。');
    c.setDraft(text);setTab('design');await c.saveDraft(cid);c.composer.current?.focus();
  }
  async function recover() {
    await perform(async () => {
      if (await c.confirm('恢复界面？','恢复内置工作台和上次应用前的主题。对话、设计草稿与生成文件保留。',['取消','恢复界面']) !== 1 || !enabled()) return;
      await registry.recover(c.saves);
      if (lastTheme) await call('themes.activate',{id:lastTheme});
      if (enabled()) {
        setSelectedTheme(null);setComparing(false);await refresh();
        setStatus('界面已恢复，可继续修改设计。');
      }
    });
  }
  function pick(region:string) {
    setScope(region);setPicking(false);setTab('design');setStatus('已选中'+region+'，在右侧描述修改要求。');
    c.composer.current?.focus();
  }
  const theme = tab === 'themes' ? selectedTheme || resolved : !plugin && !comparing ? candidate?.resolved || resolved : resolved;
  const showCandidate = tab !== 'themes' && selected && !comparing;
  const original = selected && registry.resolve(selected.kind,selected.target).find(item => !item.plugin.builtin);
  const alternate = tab === 'themes' || !plugin && candidate && !comparing ? undefined : original || activeWorkbench;
  const shown = showCandidate ? {plugin:plugin!,extension:selected} : alternate;
  const toolbar = shown?.extension.kind === 'toolbar';
  const external = shown && !toolbar;
  const locked = busy || registry.switching;
  const previewError = tab === 'design' && preview.error;
  return <div className="creation-workbench">
    <header className="creation-header"><div><Sparkles size={18}/><strong>创造模式</strong></div><div>
      <button className={picking ? 'secondary selected' : 'secondary'} aria-pressed={picking} disabled={Boolean(external)||c.active}
        onClick={() => setPicking(!picking)}><MousePointer2 size={15}/>{picking ? '点击预览中的区域' : '选择界面区域'}</button>
      <button className="secondary" disabled={locked} onClick={() => void perform(c.exitCreation)}><ArrowLeft size={15}/>退出创造</button>
    </div></header>
    <div className="creation-layout">
      <section className="creation-canvas" aria-label="界面设计预览">
        <div className="creation-preview-toolbar"><span>{showCandidate || !comparing && tab === 'design' && candidate ? '候选预览' : '工作台预览'}</span><div>
          {showCandidate && plugin!.contributes.length > 1 && <select aria-label="候选预览区域" value={extension}
            onChange={event => setExtension(Number(event.target.value))}>
            {plugin!.contributes.map((item,i) => <option value={i} key={item.id}>{item.title || item.target}</option>)}
          </select>}
          {!external && previewPages.map(([page,label]) => <button key={page} aria-pressed={previewPage === page}
            onClick={() => setPreviewPage(page)}>{label}</button>)}
        </div></div>
        <div className="creation-preview-content">
          {external ? <CandidateView plugin={shown.plugin} extension={shown.extension} theme={theme} mode={c.theme}/>
            : <WorkbenchPreview theme={theme} page={previewPage} scope={scope} picking={picking} pick={pick}
                toolbar={toolbar ? shown.extension : activeToolbar} changePage={setPreviewPage}/>}
        </div>
        <div className="creation-canvas-footer"><span>示例内容</span>
          <button disabled={locked} onClick={() => void recover()}><RefreshCw size={14}/>恢复界面</button>
        </div>
      </section>
      <aside className="creation-tools" aria-label="创造工具">
        <nav className="creation-tabs" aria-label="创造工具类型">
          {[['design','AI 设计'],['themes','主题'],['plugins','插件']].map(([id,label]) =>
            <button key={id} aria-pressed={tab === id} onClick={() => void selectTab(id)}>{label}</button>)}
        </nav>
        {(failure || previewError) && <div className="creation-error" role="alert">
          <p>{failure || previewError}</p>
          {previewError && plugin && <small>仍显示上一可用版本。</small>}
          {previewError ? <button className="secondary" disabled={preview.checking || locked} onClick={() => void tryPreview()}>重新试跑</button>
            : <button className="secondary" onClick={() => void perform(refresh)}>重新读取</button>}
        </div>}
        {preview.checking && tab === 'design' && <div className="creation-generated" role="status"><LoaderCircle size={14} className="spin"/>正在试跑新版本…</div>}
        {ready && tab !== 'themes' && <section className="creation-candidate">
          <div className="creation-candidate-heading"><strong>{plugin ? plugin.name : candidate.preview.name}</strong>
            <span>{plugin && !localPlugin ? '试跑通过' : '仅预览'}</span></div>
          {!plugin && <label>主题名称<input aria-label="候选主题名称" value={candidateName} onChange={event => setCandidateName(event.target.value)}/></label>}
          <div className="button-row">
            <button className="secondary" onClick={() => setComparing(!comparing)}>{comparing ? '回到候选' : '对照当前界面'}</button>
            <button className="secondary" disabled={locked || preview.checking} onClick={() => void discard()}>放弃</button>
            <button className="primary" disabled={locked || preview.checking || comparing || !plugin && !candidateName.trim()} onClick={() => void apply()}>
              {locked ? <LoaderCircle size={14} className="spin"/> : <Check size={14}/>}应用
            </button>
          </div>
        </section>}
        {tab === 'design' && hasInterface && !plugin && !preview.checking && !previewError && <div className="creation-generated">
          <button className="secondary" disabled={locked} onClick={() => void tryPreview()}>试跑已有候选</button>
        </div>}
        <div className="creation-design-controls" hidden={tab !== 'design'}>
          <label>修改范围<select aria-label="修改范围" value={scope} disabled={c.active} onChange={event => setScope(event.target.value)}>
            {creationScopes.map(value => <option key={value}>{value}</option>)}
          </select></label>
          <div className="button-row">
            <button onClick={() => void seed('重新设计我的工作台，让常用入口更容易找到，输入区更简洁。','整个界面').catch(reason => setFailure(reason.message))}>
              <LayoutTemplate size={14}/>自由设计工作台</button>
            <button onClick={() => void seed('设计一个柔和、适合长时间阅读的主题。先生成候选供我预览。','主题').catch(reason => setFailure(reason.message))}>
              <Palette size={14}/>AI 主题</button>
          </div>
        </div>
        <div className="creation-assistant" hidden={tab !== 'design'}>{children}</div>
        {tab === 'themes' && <div className="creation-management">
          {ready && <button className="secondary" onClick={() => setTab('design')}>返回 AI 候选</button>}
          <Appearance settings={c.settings} updateSettings={c.setSettings} onError={setStatus} onDirty={()=>{}} confirm={c.confirm} kind="appearance"
            previewChanged={setSelectedTheme} applied={setLastTheme}/>
        </div>}
        {tab === 'plugins' && <div className="creation-management"><PluginManager/></div>}
        <div className="creation-tools-footer" role="status">{status}</div>
      </aside>
    </div>
  </div>;
}
