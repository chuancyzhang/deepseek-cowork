import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ChevronRight, Copy, Download, Ellipsis, KeyRound, LoaderCircle, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-react';
import { Empty, ErrorState, Field, JobProgress, PageHead, useFormDraft } from '../Management';
import { operation, rpc } from '../managementApi';
import GatewaySetup from './GatewaySetup';
import ModelSelection from './ModelSelection';
import Dialog from '../Dialog';
import { ThemeSample } from '../ThemePreview';
import type { Settings } from '../types';
import { useHostField } from '../host/context';
import { useWorkbench } from '../host/Host';

type Props = { kind: string; settings: Settings | null; updateSettings: (settings: Settings) => void; onError: (message: string) => void; onDirty: (value: boolean) => void;
  confirm: (title: string, detail: string, choices: string[]) => Promise<number> };

export default function Preferences(props: Props) {
  const { kind } = props;
  if (kind === 'appearance') return <AppearanceEntry/>;
  if (kind === 'models') return <Models {...props}/>;
  if (kind === 'variables') return <Variables {...props}/>;
  if (kind === 'jobs') return <Jobs {...props}/>;
  return <General {...props}/>;
}

function Models(props: Props) {
  const { settings, updateSettings, onError, confirm } = props;
  const [channels, setChannels] = useState<any[]>([]), [edit, setEdit] = useState<any>(null), [failure, setFailure] = useState('');
  const [accountSetup, setAccountSetup] = useState(false), [loading, setLoading] = useState(true);
  const [menu, setMenu] = useState(''), [checking, setChecking] = useState(''), [deletion, setDeletion] = useState<ChannelDeletion | null>(null);
  const pendingLoad = useRef(0);
  const reload = useCallback(async () => {
    const request = ++pendingLoad.current;
    try {
      const [channels, settings] = await Promise.all([rpc('settings.channels'), rpc('settings.get')]);
      if (request === pendingLoad.current) {setChannels(channels);updateSettings(settings);setFailure('');}
    } catch (error) {if (request === pendingLoad.current) setFailure((error as Error).message);}
    finally {if (request === pendingLoad.current) setLoading(false);}
  }, [updateSettings]);
  useEffect(() => {void reload();return () => {pendingLoad.current++;};}, [reload]);
  const menuRef = useRef<HTMLDivElement>(null);
  const deleteFocus = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!menu) return;
    const closeOutside = (event: PointerEvent) => {if (!menuRef.current?.contains(event.target as Node)) setMenu('');};
    const close = () => setMenu('');
    document.addEventListener('pointerdown', closeOutside);
    window.addEventListener('blur', close);
    return () => {document.removeEventListener('pointerdown', closeOutside);window.removeEventListener('blur', close);};
  }, [menu]);
  async function previewDeletion(channelId: string, trigger: HTMLButtonElement | null) {
    deleteFocus.current = trigger;
    setMenu('');setChecking(channelId);
    try {setDeletion(await rpc<ChannelDeletion>('settings.channel_delete_preview', {channel_id:channelId}));}
    catch (error) {onError((error as Error).message);await reload();}
    finally {setChecking('');}
  }
  function closeDeletion() {
    setDeletion(null);
    requestAnimationFrame(() => deleteFocus.current?.focus());
  }
  if (accountSetup) return <GatewaySetup back={() => {setAccountSetup(false); void reload();}} saved={updateSettings} confirm={confirm}/>;
  if (edit) return <ModelForm {...props} item={edit} back={() => { setEdit(null); void reload(); }}/>;
  const byId = new Map(settings?.models.map(model => [model.id, model]));
  return <div className="model-services-page"><PageHead title="模型与服务"><button className="secondary" disabled={Boolean(checking)} onClick={() => setAccountSetup(true)}>账号登录</button><button className="primary" disabled={Boolean(checking)} onClick={() => setEdit({ models: [] })}><Plus size={16}/>填写 API Key</button></PageHead>
    {failure && <ErrorState message={failure} retry={() => void reload()}/>}
    {loading && !channels.length && <p className="muted" role="status">正在读取服务…</p>}
    {!loading && !failure && !channels.length && !settings?.models.length && <Empty title="连接第一个模型" detail="填写服务地址和 API Key 后，即可开始对话。"/>}
    <div className="model-services">{channels.map(channel => {
      const open = menu === channel.channel_id;
      return <section className="model-service" key={channel.channel_id}>
        <div className="section-heading"><div className="model-service-title"><h3>{channel.display_name}</h3>
          {!channel.configured && <small className="muted">需要 API Key</small>}
        </div><div className="button-row">
          <button className="icon-button" title="编辑服务" aria-label={'编辑服务 ' + channel.display_name}
            disabled={Boolean(checking)} onClick={() => {setMenu('');setEdit(channel);}}><Pencil size={17}/></button>
          <div className="model-service-menu" ref={open ? menuRef : undefined} onBlur={event => {if (!event.currentTarget.contains(event.relatedTarget)) setMenu('');}}
            onKeyDown={event => {if (open && event.key === 'Escape') {event.stopPropagation();setMenu('');event.currentTarget.querySelector('button')?.focus();}}}>
            <button className="icon-button" title="服务操作" aria-label={channel.display_name + ' 服务操作'} aria-expanded={open}
              disabled={Boolean(checking)} onClick={() => setMenu(open ? '' : channel.channel_id)}>
              {checking === channel.channel_id ? <LoaderCircle size={16} className="spin"/> : <Ellipsis size={17}/>}
            </button>
            {open && <div className="model-service-menu-items"><button className="error-text" onClick={() => void previewDeletion(channel.channel_id, menuRef.current?.querySelector('button') || null)}>删除服务</button></div>}
          </div>
        </div></div>
        {!channel.models.length && <p className="muted">还没有模型</p>}
        <div className="model-service-models">{channel.models.map((model: any) => {
          const status = byId.get(model.id), name = model.display_name || model.model_name;
          const current = settings?.selected_model_id === model.id;
          return <div className="model-row" key={model.id} aria-current={current || undefined}><div><strong>{name}</strong>
            {name !== model.model_name && <small>{model.model_name}</small>}
            {status?.connection_test?.success === false && <small className="error-text">{status.connection_test.message}</small>}
          </div>
            {current ? <span className="model-use" title="当前使用"><Check size={16} aria-hidden="true"/><span className="sr-only">当前使用</span></span>
              : status?.chat_supported === false ? <small>{status.purpose_label}</small>
              : channel.configured ? <button className="model-use" onClick={async () => {try {updateSettings(await rpc('settings.select_model', {model_id:model.id}));} catch (error) {onError((error as Error).message);}}}>使用</button> : <span/>}
            <button className="icon-button" title="删除模型" aria-label={`删除模型 ${name}`} onClick={async () => {
              if (await confirm('删除这个模型？', '已有对话仍然保留，可以选择其他模型继续。', ['取消', '删除']) !== 1) return;
              try {updateSettings(await rpc('settings.delete_model', {id:model.id}));await reload();} catch (error) {onError((error as Error).message);}
            }}><Trash2 size={16}/></button>
          </div>;
        })}</div>
      </section>;
    })}</div>
    {settings?.models.filter(model => model.id.startsWith('enterprise:')).map(model => <div className="model-row" key={model.id}><div><strong>{model.display_name}</strong><small>{model.channel_display_name}</small></div><button className="secondary" onClick={async () => updateSettings(await rpc('settings.select_model', { model_id: model.id }))}>使用</button></div>)}
    <button className="secondary" onClick={async () => { try { const value = await rpc('dialog.import_settings'); if (value) { updateSettings(value); await reload(); } } catch (error) { onError((error as Error).message); } }}><Download size={15}/>导入模型设置</button>
    {deletion && <DeleteChannel preview={deletion} close={closeDeletion} saved={async (settings) => {updateSettings(settings);setDeletion(null);await reload();}}/>}
  </div>;
}

type ChannelDeletion = {
  channel_id: string; name: string; model_count: number; configured: boolean; media: string[]; disable_media: boolean;
  removes_default: boolean; replacements: {id: string; label: string}[]; revision: string;
};

function DeleteChannel({preview, close, saved}: {preview: ChannelDeletion; close: () => void; saved: (settings: Settings) => Promise<void>}) {
  const [impact, setImpact] = useState(preview), [replacement, setReplacement] = useState('');
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  async function remove() {
    setBusy(true);setFailure('');
    try {
      await saved(await rpc('settings.channel_delete', {channel_id:impact.channel_id, revision:impact.revision, replacement_model_id:replacement}));
    } catch (error) {setFailure((error as Error).message);}
    finally {setBusy(false);}
  }
  async function refresh() {
    setBusy(true);
    try {setImpact(await rpc('settings.channel_delete_preview', {channel_id:impact.channel_id}));setReplacement('');setFailure('');}
    catch (error) {setFailure((error as Error).message);}
    finally {setBusy(false);}
  }
  return <Dialog title={`删除服务「${impact.name}」？`} className="model-service-delete" onClose={() => {if (!busy) close();}}>
    <p>{`从本机移除服务、${impact.model_count} 个模型${impact.configured ? '和 API Key' : ''}。聊天记录和已生成文件保留。`}</p>
    {impact.media.length > 0 && <p>{impact.disable_media ? '该服务的多媒体配置会移除，多媒体将停用。' : impact.media.join('、') + '将变为未配置。'} 未完成的多媒体任务可能无法继续查询。</p>}
    {impact.removes_default && (impact.replacements.length ? <Field label="新的默认模型">
      <select value={replacement} disabled={busy} onChange={event => setReplacement(event.target.value)}>
        <option value="" disabled>选择模型</option>{impact.replacements.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}
      </select>
    </Field> : <p>删除后将没有默认模型。</p>)}
    {failure && <><ErrorState message={failure}/><button type="button" className="text-link" disabled={busy} onClick={() => void refresh()}>重新核对</button></>}
    <div className="button-row"><button className="secondary" autoFocus disabled={busy} onClick={close}>取消</button>
      <button className="primary delete-service-confirm" disabled={busy || Boolean(failure) || Boolean(impact.replacements.length && !replacement)} onClick={() => void remove()}>{busy ? '处理中…' : '删除'}</button>
    </div>
  </Dialog>;
}

function ModelForm({ item, back, onDirty, updateSettings, confirm }: Props & { item: any; back: () => void }) {
  const form = useFormDraft('channel:' + (item.channel_id || 'new'), { display_name: '', base_url: '', provider_type: 'openai', api_key: '', ...item }, onDirty, ['api_key']);
  const v = form.value, set = (key: string, value: any) => form.change({ ...v, [key]: value });
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  const [job, setJob] = useState<any>(null), [catalog, setCatalog] = useState<any[] | null>(null), [chosen, setChosen] = useState<string[]>([]), [testResult, setTestResult] = useState('');
  const names = new Set<string>(v.models.map((model: any) => model.model_name?.trim()));
  const available = (catalog || []).filter(model => !names.has(model.model_name));
  const getModelsRef = useRef<HTMLButtonElement>(null), saveRef = useRef<HTMLButtonElement>(null);
  const catalogRef = useRef<HTMLDivElement>(null);
  const focusAfterCatalog = useRef<'get' | 'save' | null>(null);
  useEffect(() => {
    if (catalog || !focusAfterCatalog.current) return;
    (focusAfterCatalog.current === 'save' ? saveRef : getModelsRef).current?.focus();
    focusAfterCatalog.current = null;
  }, [catalog]);
  useEffect(() => {
    if (catalog && !busy) catalogRef.current?.querySelector('button')?.focus();
  }, [catalog,busy]);
  function closeCatalog(focus: 'get' | 'save') {focusAfterCatalog.current = focus;setCatalog(null);setChosen([]);}
  async function discover() {
    setBusy(true);setFailure('');
    try {
      const result = await operation('models.discover', {channel:v}, setJob);
      setCatalog([...new Map<string, any>(result.models.map((model: any) => [model.model_name, model])).values()]);
      setChosen([]);
    } catch (error) {setFailure((error as Error).message);}
    finally {setBusy(false);setJob(null);}
  }
  const latestValue=useRef(v);latestValue.current=v;
  async function test(model:any) {const tested=v;setBusy(true);setFailure('');setTestResult('');try{await operation('models.test',{channel:tested,model},setJob);setTestResult(latestValue.current===tested?(model.display_name || model.model_name)+'：对话接口测试通过':'测试已完成，配置已修改，请重新测试。');updateSettings(await rpc('settings.get'));}catch(error){setFailure((error as Error).message);}finally{setBusy(false);setJob(null);}}
  useEffect(()=>setTestResult(''),[v]);
  const add = () => set('models', [...v.models, { id: crypto.randomUUID(), display_name: '', model_name: '', api_protocol: 'chat_completions', supports_vision: false }]);
  const changeModel = (index: number, values: any) => set('models', v.models.map((model: any, i: number) => i === index ? { ...model, ...values } : model));
  async function leave() { if (!form.dirty || await confirm('保留编辑草稿？', '普通字段会保留。API Key 不写入草稿，下次需要重新填写。', ['继续编辑', '返回']) === 1) back(); }
  return <><PageHead title={item.channel_id ? '编辑模型服务' : '添加模型服务'} back={() => void leave()}/><form className="detail-form" onSubmit={async e => { e.preventDefault(); if (busy || catalog) return; setBusy(true); setFailure(''); try {
    updateSettings(await rpc('settings.channel_save', {value:v, existing:Boolean(item.channel_id)}));form.done();back();
  } catch (error) { setFailure((error as Error).message); } finally { setBusy(false); } }}><div className="form-grid">
    <Field label="服务名称"><input required value={v.display_name} onChange={e => set('display_name', e.target.value)}/></Field><Field label="服务类型"><select value={v.provider_type} onChange={e => set('provider_type', e.target.value)}><option value="openai">OpenAI 兼容</option><option value="anthropic">Anthropic</option></select></Field>
    <Field label="接口地址" wide><input type="url" required value={v.base_url} onChange={e => set('base_url', e.target.value)} placeholder="https://api.deepseek.com"/></Field>
    <Field label="API Key" wide help={item.configured ? '留空保留现有凭据。' : undefined}><input type="password" autoComplete="off" required={!item.configured} value={v.api_key} onChange={e => set('api_key', e.target.value)}/></Field>
  </div><JobProgress job={job}/>
  {catalog ? <div ref={catalogRef}><ModelSelection choices={available.map(model => ({key:model.model_name, name:model.model_name}))} disabled={busy} selected={chosen} onChange={setChosen}
    cancel={<button type="button" className="secondary" onClick={() => closeCatalog('get')}>取消</button>}
    onAdd={keys => {const picked = new Set(keys);set('models',[...v.models,...available.filter(model => picked.has(model.model_name))]);closeCatalog('save');}}/></div>
    : <div className="section-heading"><h3>可用模型</h3><div className="button-row"><button ref={getModelsRef} type="button" className="secondary" disabled={busy} onClick={() => void discover()}><RefreshCw size={15}/>获取模型</button><button type="button" className="secondary" onClick={add}><Plus size={15}/>手动添加</button></div></div>}
  <div hidden={Boolean(catalog)}>
  {v.models.map((model: any, index: number) => <fieldset className="model-config" key={model.id}><legend>{model.display_name || model.model_name || '新模型'}</legend><div className="form-grid">
    <Field label="显示名称"><input value={model.display_name || ''} onChange={e => changeModel(index, { display_name: e.target.value })}/></Field><Field label="模型标识"><input required value={model.model_name} onChange={e => changeModel(index, { model_name: e.target.value })}/></Field>
    <Field label="接口协议"><select value={model.api_protocol || 'chat_completions'} onChange={e => changeModel(index, { api_protocol: e.target.value })}><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option><option value="anthropic">Anthropic Messages</option></select></Field>
    <Field label="推理深度"><select value={model.reasoning_effort || ''} onChange={e => changeModel(index, { reasoning_effort: e.target.value })}>{['', 'none','low','medium','high','xhigh','max'].map(effort => <option key={effort} value={effort}>{effort || '服务默认'}</option>)}</select></Field>
    <label className="check-line"><input type="checkbox" checked={Boolean(model.supports_vision)} onChange={e => changeModel(index, { supports_vision: e.target.checked })}/>支持图片输入</label>
    <label className="check-line"><input type="checkbox" checked={model.supports_tools !== false} onChange={e => changeModel(index, { supports_tools: e.target.checked })}/>支持工具调用</label>
    <details className="wide"><summary>上下文与生成设置</summary><div className="form-grid"><Field label="上下文上限"><input type="number" min={1} value={model.context_length || ''} onChange={e => changeModel(index, { context_length: e.target.value ? +e.target.value : undefined })}/></Field><Field label="最大输出"><input type="number" min={1} value={model.max_tokens || ''} onChange={e => changeModel(index, { max_tokens: e.target.value ? +e.target.value : undefined })}/></Field><Field label="温度"><input type="number" min={0} max={2} step={.1} value={model.temperature ?? ''} onChange={e => changeModel(index, { temperature: e.target.value ? +e.target.value : undefined })}/></Field></div></details>
  </div><div className="button-row"><button type="button" className="secondary" disabled={busy} onClick={() => void test(model)}>测试当前配置</button><button type="button" className="secondary" onClick={() => set('models', v.models.filter((_: any, i: number) => i !== index))}><Trash2 size={15}/>移除</button></div></fieldset>)}
  {testResult && <p role="status">{testResult}</p>}
  {failure && <ErrorState message={failure}/>}<div className="form-actions"><button type="button" className="secondary" onClick={() => void leave()}>返回</button><button ref={saveRef} className="primary" disabled={busy || Boolean(catalog)}><Check size={16}/>保存服务</button></div></div></form></>;
}

function AppearanceEntry(){const c=useWorkbench();return <><PageHead compact title="外观与阅读" detail="在创造模式中统一设计主题、阅读样式和工作台。"/><button className="primary" onClick={()=>void c.enterCreation('themes').catch(reason=>c.error(reason.message))}>打开创造模式</button></>;}

export function Appearance({ onError, settings, updateSettings, confirm, previewChanged, applied }: Props & {previewChanged?:(value:any)=>void;applied?:(previous:string)=>void}) {
  const [fonts, setFonts] = useState<string[]>([]);
  useEffect(() => {void rpc('themes.fonts').then(setFonts).catch(error => setFailure(error.message));}, []);
  const [value, setValue] = useState<any>(null), [preview, setPreview] = useState<any>(null), [selected, setSelected] = useState(''), [failure, setFailure] = useState('');
  const load = useCallback(async () => { try { const data = await rpc('themes.list'); setValue(data); setSelected(data.active); } catch (error) { setFailure((error as Error).message); } }, []);
  useEffect(() => { void load(); }, [load]);
  const dark = value?.appearance.mode === 'dark' || value?.appearance.mode === 'system' && matchMedia('(prefers-color-scheme: dark)').matches;
  useEffect(() => { let alive=true;if (selected) void rpc('themes.resolve', { id: selected, dark }).then(result=>{if(alive)setPreview(result);}).catch(error => {if(alive)setFailure(error.message);});return()=>{alive=false;}; }, [selected, dark, value?.appearance.reading]);
  useEffect(()=>{if(preview)previewChanged?.(preview);},[preview,previewChanged]);
  async function appearance(next: any) { try { setValue(await rpc('themes.appearance', { value: next })); if (settings) updateSettings({ ...settings, theme: next.mode }); } catch (error) { setFailure((error as Error).message); } }
  return <><PageHead compact title="外观与阅读" detail={previewChanged?"先在左侧预览，找到适合你的颜色与阅读节奏。":"先预览，找到适合你的颜色与阅读节奏。"}><button className="secondary" onClick={async () => { const path = await rpc('dialog.choose', { kind: 'theme' }); if (path) { try { await rpc('themes.import', { path }); await load(); } catch (error) { setFailure((error as Error).message); } } }}><Download size={15}/>导入主题</button></PageHead>
    {failure && <ErrorState message={failure} retry={() => void load()}/>}{value && <>
      <div className="segmented" aria-label="明暗模式">{[['system','跟随系统'],['light','浅色'],['dark','深色']].map(([mode,label]) => <button key={mode} className={value.appearance.mode === mode ? 'selected' : ''} onClick={() => void appearance({ ...value.appearance, mode })}>{label}</button>)}</div>
      <div className="appearance-layout"><div className="theme-inventory">{value.themes.map((theme: any) => <button className={`theme-tile ${selected === theme.id ? 'selected' : ''}`} key={theme.id} onClick={() => setSelected(theme.id)}><span className={'theme-swatch ' + theme.id}/><span><strong>{theme.name}</strong><small>{theme.description || '自定义主题'}</small></span>{value.active === theme.id && <Check size={16}/>}</button>)}</div>
      {!previewChanged&&<ThemeSample theme={preview}/>}</div>
      <div className="form-actions"><button className="secondary" disabled={!selected || value.themes.find((item: any) => item.id === selected)?.builtin} onClick={async () => { const path = await rpc('dialog.save', { name: (preview?.name || '主题') + '.cowork-theme' }); if (path) { try { await rpc('themes.export', { id: selected, path }); onError('主题已导出。'); } catch (error) { setFailure((error as Error).message); } } }}>导出主题</button><button className="secondary" disabled={value.themes.find((item:any) => item.id === selected)?.builtin} onClick={async () => {if (await confirm("删除自定义主题？", "如果它正在使用，工作台会恢复内置主题。", ["取消","删除"]) === 1) {try {await rpc("themes.delete",{id:selected});await load();}catch(error){setFailure((error as Error).message);}}}}>删除主题</button><button className="primary" disabled={selected === value.active} onClick={async () => { try { setValue(await rpc('themes.activate', { id: selected })); applied?.(value.active); } catch (error) { setFailure((error as Error).message); } }}><Check size={16}/>应用主题</button></div>
      <section className="settings-section"><h3>阅读</h3><div className="form-grid">{[["font_family","界面字体"],["mono_font_family","代码字体"]].map(([key,label]) => <Field label={label} key={key}><select value={value.appearance.reading[key] || ""} onChange={event => void appearance({...value.appearance, reading:{...value.appearance.reading,[key]:event.target.value}})}><option value="">使用主题字体</option>{fonts.map(font => <option key={font} value={font}>{font}</option>)}</select></Field>)}</div><div className="form-grid"><Field label="字号"><select value={value.appearance.reading.font_scale || 1} onChange={e => void appearance({ ...value.appearance, reading: { ...value.appearance.reading, font_scale: +e.target.value } })}>{[.9,1,1.1,1.2,1.3,1.5].map(scale => <option key={scale} value={scale}>{Math.round(scale*100)}%</option>)}</select></Field><Field label="界面密度"><select value={value.appearance.reading.density || 'comfortable'} onChange={e => void appearance({ ...value.appearance, reading: { ...value.appearance.reading, density: e.target.value } })}><option value="comfortable">舒适</option><option value="standard">标准</option><option value="compact">紧凑</option></select></Field></div></section>
    </>}</>;
}

function Variables(props: Props) {
  const [items, setItems] = useState<any[]>([]), [edit, setEdit] = useState<any>(null), [failure, setFailure] = useState('');
  const load = useCallback(() => rpc('variables.list').then(setItems).catch(error => setFailure(error.message)), []);
  useEffect(() => { void load(); }, [load]);
  if (edit) return <VariableForm {...props} item={edit} back={() => { setEdit(null); void load(); }}/>;
  return <><PageHead compact title="变量与凭据" detail="重复使用的信息只需保存一次，敏感凭据单独控制读取权限。"><button className="primary" onClick={() => setEdit({})}><Plus size={16}/>添加变量</button></PageHead>{failure && <ErrorState message={failure}/>}
    {!items.length && <Empty icon={KeyRound} title="保存常用信息" detail="例如项目编号、常用地址或服务凭据。"/>}{items.map(item => <div className="model-row" key={item.id}><KeyRound size={19}/><div><strong>{item.name}</strong><small>{item.kind === 'secret' ? '敏感凭据' : item.value} · {item.description}</small></div><button className="icon-button" title="编辑" onClick={() => setEdit(item)}><Pencil size={16}/></button><button className="icon-button" title="删除" onClick={async () => { if (await props.confirm('删除这个变量？', '使用此变量的技能下次需要重新配置。', ['取消','删除']) === 1) { try { await rpc('variables.delete', { id: item.id }); await load(); } catch (error) { setFailure((error as Error).message); } } }}><Trash2 size={16}/></button></div>)}
    <button className="secondary" onClick={async () => { if (await props.confirm('恢复上一版变量？', '当前变量列表将被上一版替换。', ['取消','恢复']) === 1) { try { await rpc('variables.undo'); await load(); } catch (error) { setFailure((error as Error).message); } } }}><RefreshCw size={15}/>恢复上一版</button></>;
}
function VariableForm({ item, back, onDirty }: Props & { item: any; back: () => void }) {
  const form = useFormDraft('variable:' + (item.id || 'new'), { name: '', kind: 'text', value: '', description: '', allow_ai_read: false, ...item }, onDirty, ['value']);
  const v = form.value, set = (key: string, value: any) => form.change({ ...v, [key]: value });
  const [failure, setFailure] = useState(''), [busy, setBusy] = useState(false);
  return <><PageHead title="编辑变量" back={back}/><form className="detail-form" onSubmit={async e => { e.preventDefault(); setBusy(true); try { await rpc('variables.save', { value: { name: v.name, kind: v.kind, value: v.kind === 'secret' && !v.value && item.id ? null : v.value, description: v.description, allow_ai_read: v.allow_ai_read, variable_id: item.id || '' } }); form.done(); back(); } catch (error) { setFailure((error as Error).message); } finally { setBusy(false); } }}><div className="form-grid"><Field label="名称"><input required value={v.name} onChange={e => set('name', e.target.value)}/></Field><Field label="类型"><select value={v.kind} onChange={e => set('kind', e.target.value)}><option value="text">普通文本</option><option value="secret">敏感凭据</option></select></Field><Field label="内容" wide><input type={v.kind === 'secret' ? 'password' : 'text'} autoComplete="off" placeholder={v.kind === 'secret' && item.id ? '留空保留现有凭据' : ''} value={v.value || ''} onChange={e => set('value', e.target.value)}/></Field><Field label="用途" wide><input value={v.description} onChange={e => set('description', e.target.value)}/></Field><label className="check-line wide"><input type="checkbox" checked={v.allow_ai_read} onChange={e => set('allow_ai_read', e.target.checked)}/>允许 AI 读取内容</label></div>{failure && <ErrorState message={failure}/>}<button className="primary" disabled={busy}>保存</button></form></>;
}
const jobStatuses: Record<string,string> = {queued:'等待处理',running:'进行中',stopping:'正在取消',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
function Jobs({ onError }: Props) {
  const [jobs, setJobs] = useState<any[]>([]), [failure, setFailure] = useState(''), [busy, setBusy] = useState(false), [more, setMore] = useState(false), [copied, setCopied] = useState('');
  const [query, setQuery] = useHostField('jobs:query', ''), [status, setStatus] = useHostField('jobs:status', ''), [opened, setOpened] = useHostField('jobs:opened', '');
  const sequence = useRef(0), cursor = useRef(''), updates = useRef(new Map<string,any>());
  const matches = useCallback((job:any) => (!status || (status === 'active' ? ['queued','running','stopping'].includes(job.status) : job.status === status)) && [job.title, job.message, job.error, JSON.stringify(job.logs || [])].join(' ').toLowerCase().includes(query.trim().toLowerCase()), [query,status]);
  const load = useCallback(async (before = '') => {
    const current = ++sequence.current;
    updates.current.clear();
    setBusy(true);setFailure('');
    try {
      const items = await rpc<any[]>('jobs.list', {query:query.trim(),status,before,limit:50});
      if(current !== sequence.current)return;
      setJobs(previous => {
        const values = new Map<string,any>((before ? previous : []).map(job => [job.id,job]));
        for(const item of items)if(!values.has(item.id) || values.get(item.id).updated_at <= item.updated_at)values.set(item.id,item);
        for(const item of updates.current.values())if(!values.has(item.id) || values.get(item.id).updated_at <= item.updated_at)values.set(item.id,item);
        return [...values.values()].filter(matches).sort((a,b)=>b.id.localeCompare(a.id));
      });cursor.current=items.at(-1)?.id || '';setMore(items.length === 50);
    } catch(error) {if(current === sequence.current)setFailure((error as Error).message);}
    finally {if(current === sequence.current)setBusy(false);}
  },[query,status,matches]);
  useEffect(() => {
    setJobs([]);setMore(false);setBusy(true);
    const timer = setTimeout(()=>void load(),200);
    const unsubscribe = window.cowork.onEvents(events => {
      for(const event of events)if(event.event === 'job') {
        const last=updates.current.get(event.data.id);
        if(last && last.updated_at > event.data.updated_at)continue;
        updates.current.set(event.data.id,event.data);
        setJobs(previous => {
        const item = event.data, existing = previous.find(job => job.id === item.id);
        if(existing && existing.updated_at > item.updated_at)return previous;
        return [...previous.filter(job => job.id !== item.id), ...(matches(item) ? [item] : [])].sort((a,b)=>b.id.localeCompare(a.id));
      });}
    });
    return ()=>{clearTimeout(timer);sequence.current++;unsubscribe();};
  },[load,matches]);
  async function copy(job:any) {
    const text = [job.title, '状态：' + (jobStatuses[job.status] || job.status), '开始：' + new Date(job.created_at*1000).toLocaleString(),
      ...(job.logs_omitted ? ['较早的 '+job.logs_omitted+' 条日志已省略。'] : []),
      ...(job.logs?.length ? job.logs.map((item:any)=>new Date(item.at*1000).toLocaleString()+'  '+item.message) : ['这条旧记录未保存过程日志。',job.message,job.error].filter(Boolean))].join('\n');
    try {await rpc('clipboard.write',{text});setCopied(job.id);}catch(error){onError((error as Error).message);}
  }
  return <><header className="management-toolbar jobs-toolbar"><h2 className="sr-only">后台操作</h2><div className="page-search"><Search size={17}/><input aria-label="搜索后台操作" placeholder="搜索操作、日志或错误" value={query} onChange={event=>setQuery(event.target.value)}/></div><select aria-label="筛选操作状态" value={status} onChange={event=>setStatus(event.target.value)}>{[['','全部状态'],['active','进行中'],['failed','失败'],['completed','已完成'],['cancelled','已取消'],['interrupted','已中断']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select><button className="icon-button" title="刷新后台操作" aria-label="刷新后台操作" disabled={busy} onClick={()=>void load()}><RefreshCw size={17}/></button></header>
    {failure && <ErrorState message={failure} retry={()=>void load()}/>}
    {!busy && !failure && !jobs.length && <Empty title={query || status ? '没有匹配的操作' : '还没有后台操作'} detail={query || status ? '换一个关键词或状态。' : '导入、连接测试和资料处理的进度会显示在这里。'}/>}
    <div className="job-history">{jobs.map(job => <details key={job.id} open={opened === job.id} onToggle={event=>{if(event.currentTarget.open)setOpened(job.id);else if(opened === job.id)setOpened('');}}>
      <summary><ChevronRight size={16}/><div><strong>{job.title}</strong><time>{new Date(job.created_at*1000).toLocaleString()}</time>{job.error && opened !== job.id && <p className="error-text">{job.error}</p>}</div><span className={job.status === 'failed' ? 'error-text' : 'muted'}>{jobStatuses[job.status] || job.status}</span>{['running','queued','stopping'].includes(job.status) && <button type="button" className="secondary" disabled={job.status === 'stopping'} onClick={event=>{event.preventDefault();event.stopPropagation();void rpc('jobs.cancel',{id:job.id}).catch(error=>onError(error.message));}}>取消</button>}</summary>
      <div className="job-log"><div className="job-log-tools">{job.logs_omitted > 0 && <small>仅保留最近 {job.logs.length} 条，较早的 {job.logs_omitted} 条已省略。</small>}<button type="button" className="secondary" onClick={()=>void copy(job)}>{copied === job.id ? <Check size={14}/> : <Copy size={14}/>}复制日志</button></div>
        {job.logs?.length ? <ol>{job.logs.map((entry:any,index:number)=><li key={index}><time title={new Date(entry.at*1000).toLocaleString()}>{new Date(entry.at*1000).toLocaleTimeString()}</time><span className={entry.status === 'failed' ? 'error-text' : ''}>{entry.message}</span></li>)}</ol> : <><p className="muted">这条旧记录未保存过程日志。</p>{job.message && <p>{job.message}</p>}{job.error && job.error !== job.message && <p className="error-text">{job.error}</p>}</>}
      </div>
    </details>)}</div>{busy && <p className="loading" role="status"><LoaderCircle size={17} className="spin"/>正在读取…</p>}{more && <button className="secondary" disabled={busy} onClick={()=>void load(cursor.current)}>加载更早记录</button>}
  </>;
}
function General({ settings, onError, onDirty }: Props) {
  const [value, setValue] = useState<any>(null), [failure, setFailure] = useState('');
  useEffect(() => { void rpc('settings.general').then(setValue).catch(error => setFailure(error.message)); }, []);
  return <><PageHead compact title="工作偏好" detail="选择工作文件的位置，管理本机数据。"/>{failure && <ErrorState message={failure}/>}{value && <GeneralForm initial={value} onDirty={onDirty} onError={onError}/>}
    <section className="settings-section"><h3>本机数据</h3><p>正式版沿用原有数据目录；开发版使用隔离目录。</p><code className="data-path">{settings?.data_dir}</code><button className="secondary" onClick={() => void rpc('app.logs')}>打开诊断日志</button></section></>;
}
function GeneralForm({ initial, onDirty, onError }: any) {
  const form = useFormDraft('general', initial, onDirty), v = form.value;
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  return <form className="detail-form" onSubmit={async e => { e.preventDefault(); setBusy(true); try { await rpc('settings.general', { value: v }); form.done(); onError('工作偏好已保存。'); } catch (error) { setFailure((error as Error).message); } finally { setBusy(false); } }}><div className="form-grid"><Field label="新对话的工作目录" wide><div className="input-action"><input value={v.chat_workspace_root || ''} onChange={e => form.change({ ...v, chat_workspace_root: e.target.value })}/><button type="button" className="secondary" onClick={async () => { const path = await rpc('dialog.workspace'); if (path) form.change({ ...v, chat_workspace_root: path }); }}>选择</button></div></Field><Field label="上下文使用比例"><input type="number" min={10} max={100} value={Math.round((v.context_budget_ratio || .8)*100)} onChange={e => form.change({ ...v, context_budget_ratio: +e.target.value/100 })}/></Field></div>{failure && <ErrorState message={failure}/>}<button className="primary" disabled={busy}>保存偏好</button></form>;
}
