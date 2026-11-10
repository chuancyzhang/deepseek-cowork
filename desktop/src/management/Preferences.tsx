import { useCallback, useEffect, useState } from 'react';
import { Check, Download, KeyRound, LoaderCircle, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { Empty, ErrorState, Field, JobProgress, PageHead, useFormDraft } from '../Management';
import { operation, rpc } from '../managementApi';
import GatewaySetup from './GatewaySetup';
import { ThemeSample } from '../ThemePreview';
import type { Settings } from '../types';

type Props = { kind: string; settings: Settings | null; updateSettings: (settings: Settings) => void; onError: (message: string) => void; onDirty: (value: boolean) => void;
  confirm: (title: string, detail: string, choices: string[]) => Promise<number> };

export default function Preferences(props: Props) {
  const { kind } = props;
  if (kind === 'appearance') return <Appearance {...props}/>;
  if (kind === 'models') return <Models {...props}/>;
  if (kind === 'variables') return <Variables {...props}/>;
  if (kind === 'jobs') return <Jobs {...props}/>;
  return <General {...props}/>;
}

function Models(props: Props) {
  const { settings, updateSettings, onError, confirm } = props;
  const [channels, setChannels] = useState<any[]>([]), [edit, setEdit] = useState<any>(null), [failure, setFailure] = useState('');
  const [accountSetup, setAccountSetup] = useState(false);
  const reload = useCallback(() => rpc('settings.channels').then(setChannels).catch(error => setFailure(error.message)), []);
  useEffect(() => { void reload(); }, [reload]);
  if (accountSetup) return <GatewaySetup back={() => {setAccountSetup(false); void reload();}} saved={updateSettings} confirm={confirm}/>;
  if (edit) return <ModelForm {...props} item={edit} back={() => { setEdit(null); void reload(); }}/>;
  return <><PageHead title="模型与服务" detail="在熟悉的服务中选择模型。企业模型会随登录状态同步。"><button className="secondary" onClick={() => setAccountSetup(true)}>账号登录</button><button className="primary" onClick={() => setEdit({ models: [] })}><Plus size={16}/>填写 API Key</button></PageHead>
    {failure && <ErrorState message={failure} retry={() => void reload()}/>}
    {!settings?.models.length && <Empty title="连接第一个模型" detail="填写服务地址和 API Key 后，即可开始对话。"/>}
    {channels.map(channel => <section className="settings-section" key={channel.channel_id}><div className="section-heading"><h3>{channel.display_name}</h3><button className="secondary" onClick={() => setEdit(channel)}><Pencil size={15}/>编辑服务</button></div>
      {channel.models.map((model: any) => <div className="model-row" key={model.id}><div><strong>{model.display_name || model.model_name}</strong><small>{model.model_name} · {channel.configured ? '已连接凭据' : '需要 API Key'}</small></div>
        {settings?.selected_model_id === model.id ? <span className="subtle-badge"><Check size={12}/>当前使用</span> : <button className="secondary" onClick={async () => { try { updateSettings(await rpc('settings.select_model', { model_id: model.id })); } catch (error) { onError((error as Error).message); } }}>使用</button>}
        <button className="icon-button" title="删除模型" onClick={async () => { if (await confirm('删除这个模型？', '已有对话仍然保留，可以选择其他模型继续。', ['取消', '删除']) === 1) { try { updateSettings(await rpc('settings.delete_model', { id: model.id })); await reload(); } catch (error) { onError((error as Error).message); } } }}><Trash2 size={16}/></button>
      </div>)}</section>)}
    {settings?.models.filter(model => model.id.startsWith('enterprise:')).map(model => <div className="model-row" key={model.id}><div><strong>{model.display_name}</strong><small>{model.channel_display_name}</small></div><button className="secondary" onClick={async () => updateSettings(await rpc('settings.select_model', { model_id: model.id }))}>使用</button></div>)}
    <button className="secondary" onClick={async () => { try { const value = await rpc('dialog.import_settings'); if (value) { updateSettings(value); await reload(); } } catch (error) { onError((error as Error).message); } }}><Download size={15}/>导入模型设置</button>
  </>;
}

function ModelForm({ item, back, onDirty, updateSettings, confirm }: Props & { item: any; back: () => void }) {
  const form = useFormDraft('channel:' + (item.channel_id || 'new'), { display_name: '', base_url: '', provider_type: 'openai', api_key: '', ...item }, onDirty, ['api_key']);
  const v = form.value, set = (key: string, value: any) => form.change({ ...v, [key]: value });
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  const [job, setJob] = useState<any>(null), [catalog, setCatalog] = useState<any[]>([]), [chosen, setChosen] = useState<string[]>([]), [testResult, setTestResult] = useState('');
  async function discover() {setBusy(true);setFailure('');try {const result=await operation('models.discover',{channel:v},setJob);setCatalog(result.models);setChosen(result.models.filter((model:any)=>!v.models.some((existing:any)=>existing.model_name===model.model_name)).map((model:any)=>model.model_name));}catch(error){setFailure((error as Error).message);}finally{setBusy(false);setJob(null);}}
  async function test(model:any) {setBusy(true);setFailure('');setTestResult('');try{const result=await operation('models.test',{channel:v,model},setJob);setTestResult(result.message);}catch(error){setFailure((error as Error).message);}finally{setBusy(false);setJob(null);}}
  const add = () => set('models', [...v.models, { id: crypto.randomUUID(), display_name: '', model_name: '', api_protocol: 'chat_completions', supports_vision: false }]);
  const changeModel = (index: number, values: any) => set('models', v.models.map((model: any, i: number) => i === index ? { ...model, ...values } : model));
  async function leave() { if (!form.dirty || await confirm('保留编辑草稿？', '普通字段会保留。API Key 不写入草稿，下次需要重新填写。', ['继续编辑', '返回']) === 1) back(); }
  return <><PageHead title={item.channel_id ? '编辑模型服务' : '添加模型服务'} back={() => void leave()}/><form className="detail-form" onSubmit={async e => { e.preventDefault(); setBusy(true); setFailure(''); try {
    if (!v.models.length) throw new Error('请至少添加一个模型。');
    updateSettings(await rpc('settings.channel_save', { value: v })); form.done(); back();
  } catch (error) { setFailure((error as Error).message); } finally { setBusy(false); } }}><div className="form-grid">
    <Field label="服务名称"><input required value={v.display_name} onChange={e => set('display_name', e.target.value)}/></Field><Field label="服务类型"><select value={v.provider_type} onChange={e => set('provider_type', e.target.value)}><option value="openai">OpenAI 兼容</option><option value="anthropic">Anthropic</option></select></Field>
    <Field label="接口地址" wide><input type="url" required value={v.base_url} onChange={e => set('base_url', e.target.value)} placeholder="https://api.deepseek.com"/></Field>
    <Field label="API Key" wide help={item.configured ? '留空保留现有凭据。' : undefined}><input type="password" autoComplete="off" required={!item.configured} value={v.api_key} onChange={e => set('api_key', e.target.value)}/></Field>
  </div><JobProgress job={job}/><div className="section-heading"><h3>可用模型</h3><div className="button-row"><button type="button" className="secondary" disabled={busy} onClick={() => void discover()}><RefreshCw size={15}/>获取模型</button><button type="button" className="secondary" onClick={add}><Plus size={15}/>手动添加</button></div></div>
  {catalog.length > 0 && <section className="settings-section"><div className="model-selection">{catalog.map(model=><label className="model-choice" key={model.model_name}><input type="checkbox" checked={chosen.includes(model.model_name)} onChange={()=>setChosen(previous=>previous.includes(model.model_name)?previous.filter(name=>name!==model.model_name):[...previous,model.model_name])}/><span>{model.model_name}</span></label>)}</div><div className="button-row"><button type="button" className="secondary" onClick={()=>setCatalog([])}>取消</button><button type="button" className="secondary" onClick={()=>{set('models',[...v.models,...catalog.filter(model=>chosen.includes(model.model_name)&&!v.models.some((existing:any)=>existing.model_name===model.model_name))]);setCatalog([]);}}>添加所选模型</button></div></section>}
  {v.models.map((model: any, index: number) => <fieldset className="model-config" key={model.id}><legend>{model.display_name || model.model_name || '新模型'}</legend><div className="form-grid">
    <Field label="显示名称"><input value={model.display_name || ''} onChange={e => changeModel(index, { display_name: e.target.value })}/></Field><Field label="模型标识"><input required value={model.model_name} onChange={e => changeModel(index, { model_name: e.target.value })}/></Field>
    <Field label="接口协议"><select value={model.api_protocol || 'chat_completions'} onChange={e => changeModel(index, { api_protocol: e.target.value })}><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option><option value="anthropic">Anthropic Messages</option></select></Field>
    <Field label="推理深度"><select value={model.reasoning_effort || ''} onChange={e => changeModel(index, { reasoning_effort: e.target.value })}>{['', 'none','low','medium','high','xhigh','max'].map(effort => <option key={effort} value={effort}>{effort || '服务默认'}</option>)}</select></Field>
    <label className="check-line"><input type="checkbox" checked={Boolean(model.supports_vision)} onChange={e => changeModel(index, { supports_vision: e.target.checked })}/>支持图片输入</label>
    <label className="check-line"><input type="checkbox" checked={model.supports_tools !== false} onChange={e => changeModel(index, { supports_tools: e.target.checked })}/>支持工具调用</label>
    <details className="wide"><summary>上下文与生成设置</summary><div className="form-grid"><Field label="上下文上限"><input type="number" min={1} value={model.context_length || ''} onChange={e => changeModel(index, { context_length: e.target.value ? +e.target.value : undefined })}/></Field><Field label="最大输出"><input type="number" min={1} value={model.max_tokens || ''} onChange={e => changeModel(index, { max_tokens: e.target.value ? +e.target.value : undefined })}/></Field><Field label="温度"><input type="number" min={0} max={2} step={.1} value={model.temperature ?? ''} onChange={e => changeModel(index, { temperature: e.target.value ? +e.target.value : undefined })}/></Field></div></details>
  </div><div className="button-row"><button type="button" className="secondary" disabled={busy} onClick={() => void test(model)}>测试当前配置</button><button type="button" className="secondary" onClick={() => set('models', v.models.filter((_: any, i: number) => i !== index))}><Trash2 size={15}/>移除</button></div></fieldset>)}
  {testResult && <p role="status">{testResult}</p>}
  {failure && <ErrorState message={failure}/>}<div className="form-actions"><button type="button" className="secondary" onClick={() => void leave()}>返回</button><button className="primary" disabled={busy}><Check size={16}/>保存服务</button></div></form></>;
}

function Appearance({ onError, settings, updateSettings, confirm }: Props) {
  const [fonts, setFonts] = useState<string[]>([]);
  useEffect(() => {void rpc('themes.fonts').then(setFonts).catch(error => setFailure(error.message));}, []);
  const [value, setValue] = useState<any>(null), [preview, setPreview] = useState<any>(null), [selected, setSelected] = useState(''), [failure, setFailure] = useState('');
  const load = useCallback(async () => { try { const data = await rpc('themes.list'); setValue(data); setSelected(data.active); } catch (error) { setFailure((error as Error).message); } }, []);
  useEffect(() => { void load(); }, [load]);
  const dark = value?.appearance.mode === 'dark' || value?.appearance.mode === 'system' && matchMedia('(prefers-color-scheme: dark)').matches;
  useEffect(() => { if (selected) void rpc('themes.resolve', { id: selected, dark }).then(setPreview).catch(error => setFailure(error.message)); }, [selected, dark, value?.appearance.reading]);
  async function appearance(next: any) { try { setValue(await rpc('themes.appearance', { value: next })); if (settings) updateSettings({ ...settings, theme: next.mode }); } catch (error) { setFailure((error as Error).message); } }
  return <><PageHead title="外观与阅读" detail="先在右侧预览，找到适合你的颜色与阅读节奏。"><button className="secondary" onClick={async () => { const path = await rpc('dialog.choose', { kind: 'theme' }); if (path) { try { await rpc('themes.import', { path }); await load(); } catch (error) { setFailure((error as Error).message); } } }}><Download size={15}/>导入主题</button></PageHead>
    {failure && <ErrorState message={failure} retry={() => void load()}/>}{value && <>
      <div className="segmented" aria-label="明暗模式">{[['system','跟随系统'],['light','浅色'],['dark','深色']].map(([mode,label]) => <button key={mode} className={value.appearance.mode === mode ? 'selected' : ''} onClick={() => void appearance({ ...value.appearance, mode })}>{label}</button>)}</div>
      <div className="appearance-layout"><div className="theme-inventory">{value.themes.map((theme: any) => <button className={`theme-tile ${selected === theme.id ? 'selected' : ''}`} key={theme.id} onClick={() => setSelected(theme.id)}><span className={'theme-swatch ' + theme.id}/><span><strong>{theme.name}</strong><small>{theme.description || '自定义主题'}</small></span>{value.active === theme.id && <Check size={16}/>}</button>)}</div>
      <ThemeSample theme={preview}/></div>
      <div className="form-actions"><button className="secondary" disabled={!selected || value.themes.find((item: any) => item.id === selected)?.builtin} onClick={async () => { const path = await rpc('dialog.save', { name: (preview?.name || '主题') + '.cowork-theme' }); if (path) { try { await rpc('themes.export', { id: selected, path }); onError('主题已导出。'); } catch (error) { setFailure((error as Error).message); } } }}>导出主题</button><button className="secondary" disabled={value.themes.find((item:any) => item.id === selected)?.builtin} onClick={async () => {if (await confirm("删除自定义主题？", "如果它正在使用，工作台会恢复内置主题。", ["取消","删除"]) === 1) {try {await rpc("themes.delete",{id:selected});await load();}catch(error){setFailure((error as Error).message);}}}}>删除主题</button><button className="primary" disabled={selected === value.active} onClick={async () => { try { setValue(await rpc('themes.activate', { id: selected })); } catch (error) { setFailure((error as Error).message); } }}><Check size={16}/>应用主题</button></div>
      <section className="settings-section"><h3>阅读</h3><div className="form-grid">{[["font_family","界面字体"],["mono_font_family","代码字体"]].map(([key,label]) => <Field label={label} key={key}><select value={value.appearance.reading[key] || ""} onChange={event => void appearance({...value.appearance, reading:{...value.appearance.reading,[key]:event.target.value}})}><option value="">使用主题字体</option>{fonts.map(font => <option key={font} value={font}>{font}</option>)}</select></Field>)}</div><div className="form-grid"><Field label="字号"><select value={value.appearance.reading.font_scale || 1} onChange={e => void appearance({ ...value.appearance, reading: { ...value.appearance.reading, font_scale: +e.target.value } })}>{[.9,1,1.1,1.2,1.3,1.5].map(scale => <option key={scale} value={scale}>{Math.round(scale*100)}%</option>)}</select></Field><Field label="界面密度"><select value={value.appearance.reading.density || 'comfortable'} onChange={e => void appearance({ ...value.appearance, reading: { ...value.appearance.reading, density: e.target.value } })}><option value="comfortable">舒适</option><option value="standard">标准</option><option value="compact">紧凑</option></select></Field></div></section>
    </>}</>;
}

function Variables(props: Props) {
  const [items, setItems] = useState<any[]>([]), [edit, setEdit] = useState<any>(null), [failure, setFailure] = useState('');
  const load = useCallback(() => rpc('variables.list').then(setItems).catch(error => setFailure(error.message)), []);
  useEffect(() => { void load(); }, [load]);
  if (edit) return <VariableForm {...props} item={edit} back={() => { setEdit(null); void load(); }}/>;
  return <><PageHead title="变量与凭据" detail="重复使用的信息只需保存一次，敏感凭据单独控制读取权限。"><button className="primary" onClick={() => setEdit({})}><Plus size={16}/>添加变量</button></PageHead>{failure && <ErrorState message={failure}/>}
    {!items.length && <Empty icon={KeyRound} title="保存常用信息" detail="例如项目编号、常用地址或服务凭据。"/>}{items.map(item => <div className="model-row" key={item.id}><KeyRound size={19}/><div><strong>{item.name}</strong><small>{item.kind === 'secret' ? '敏感凭据' : item.value} · {item.description}</small></div><button className="icon-button" title="编辑" onClick={() => setEdit(item)}><Pencil size={16}/></button><button className="icon-button" title="删除" onClick={async () => { if (await props.confirm('删除这个变量？', '使用此变量的技能下次需要重新配置。', ['取消','删除']) === 1) { try { await rpc('variables.delete', { id: item.id }); await load(); } catch (error) { setFailure((error as Error).message); } } }}><Trash2 size={16}/></button></div>)}
    <button className="secondary" onClick={async () => { if (await props.confirm('恢复上一版变量？', '当前变量列表将被上一版替换。', ['取消','恢复']) === 1) { try { await rpc('variables.undo'); await load(); } catch (error) { setFailure((error as Error).message); } } }}><RefreshCw size={15}/>恢复上一版</button></>;
}
function VariableForm({ item, back, onDirty }: Props & { item: any; back: () => void }) {
  const form = useFormDraft('variable:' + (item.id || 'new'), { name: '', kind: 'text', value: '', description: '', allow_ai_read: false, ...item }, onDirty, ['value']);
  const v = form.value, set = (key: string, value: any) => form.change({ ...v, [key]: value });
  const [failure, setFailure] = useState(''), [busy, setBusy] = useState(false);
  return <><PageHead title="编辑变量" back={back}/><form className="detail-form" onSubmit={async e => { e.preventDefault(); setBusy(true); try { await rpc('variables.save', { value: { name: v.name, kind: v.kind, value: v.kind === 'secret' && !v.value && item.id ? null : v.value, description: v.description, allow_ai_read: v.allow_ai_read, variable_id: item.id || '' } }); form.done(); back(); } catch (error) { setFailure((error as Error).message); } finally { setBusy(false); } }}><div className="form-grid"><Field label="名称"><input required value={v.name} onChange={e => set('name', e.target.value)}/></Field><Field label="类型"><select value={v.kind} onChange={e => set('kind', e.target.value)}><option value="text">普通文本</option><option value="secret">敏感凭据</option></select></Field><Field label="内容" wide><input type={v.kind === 'secret' ? 'password' : 'text'} autoComplete="off" placeholder={v.kind === 'secret' && item.id ? '留空保留现有凭据' : ''} value={v.value || ''} onChange={e => set('value', e.target.value)}/></Field><Field label="用途" wide><input value={v.description} onChange={e => set('description', e.target.value)}/></Field><label className="check-line wide"><input type="checkbox" checked={v.allow_ai_read} onChange={e => set('allow_ai_read', e.target.checked)}/>允许 AI 读取内容</label></div>{failure && <ErrorState message={failure}/>}<button className="primary" disabled={busy}>保存</button></form></>;
}
function Jobs({ onError }: Props) {
  const [jobs, setJobs] = useState<any[]>([]), [failure, setFailure] = useState('');
  useEffect(() => { void rpc('jobs.list').then(setJobs).catch(error => setFailure(error.message)); return window.cowork.onEvents(events => { for (const event of events) if (event.event === 'job') setJobs(previous => [event.data, ...previous.filter(item => item.id !== event.data.id)].slice(0,100)); }); }, []);
  return <><PageHead title="后台操作" detail="界面关闭后，运行记录仍然保留。"/>{failure && <ErrorState message={failure}/>} {!jobs.length && <Empty title="还没有后台操作" detail="导入、连接测试和资料处理的进度会显示在这里。"/>}{jobs.map(job => <div className="model-row" key={job.id}><div><strong>{job.title}</strong><small>{job.message} · {new Date(job.created_at*1000).toLocaleString()}</small>{job.error && <p className="error-text">{job.error}</p>}</div>{['running','queued','stopping'].includes(job.status) ? <button className="secondary" onClick={() => void rpc('jobs.cancel', { id: job.id }).catch(error => onError(error.message))}>取消</button> : <span className="subtle-badge">{({ completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' } as any)[job.status]}</span>}</div>)}</>;
}
function General({ settings, onError, onDirty }: Props) {
  const [value, setValue] = useState<any>(null), [failure, setFailure] = useState('');
  useEffect(() => { void rpc('settings.general').then(setValue).catch(error => setFailure(error.message)); }, []);
  return <><PageHead title="工作偏好" detail="选择工作文件的位置，管理本机数据。"/>{failure && <ErrorState message={failure}/>}{value && <GeneralForm initial={value} onDirty={onDirty} onError={onError}/>}
    <section className="settings-section"><h3>本机数据</h3><p>正式版沿用原有数据目录；开发版使用隔离目录。</p><code className="data-path">{settings?.data_dir}</code><button className="secondary" onClick={() => void rpc('app.logs')}>打开诊断日志</button></section></>;
}
function GeneralForm({ initial, onDirty, onError }: any) {
  const form = useFormDraft('general', initial, onDirty), v = form.value;
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  return <form className="detail-form" onSubmit={async e => { e.preventDefault(); setBusy(true); try { await rpc('settings.general', { value: v }); form.done(); onError('工作偏好已保存。'); } catch (error) { setFailure((error as Error).message); } finally { setBusy(false); } }}><div className="form-grid"><Field label="新对话的工作目录" wide><div className="input-action"><input value={v.chat_workspace_root || ''} onChange={e => form.change({ ...v, chat_workspace_root: e.target.value })}/><button type="button" className="secondary" onClick={async () => { const path = await rpc('dialog.workspace'); if (path) form.change({ ...v, chat_workspace_root: path }); }}>选择</button></div></Field><Field label="上下文使用比例"><input type="number" min={10} max={100} value={Math.round((v.context_budget_ratio || .8)*100)} onChange={e => form.change({ ...v, context_budget_ratio: +e.target.value/100 })}/></Field><Field label="技能依赖安装超时（秒）"><input type="number" min={1} value={v.skill_dependency_install_timeout_seconds || 600} onChange={e => form.change({ ...v, skill_dependency_install_timeout_seconds: +e.target.value })}/></Field></div>{failure && <ErrorState message={failure}/>}<button className="primary" disabled={busy}>保存偏好</button></form>;
}
