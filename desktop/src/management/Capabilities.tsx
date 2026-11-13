import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Check, Download, ExternalLink, FileCode2, FolderOpen, LoaderCircle, Pencil, Play, Plus, Search, Sparkles, Star, Trash2, Upload } from 'lucide-react';
import { Empty, ErrorState, Field, JobProgress, PageHead, PageToolbar, useFormDraft } from '../Management';
import { operation, rpc } from '../managementApi';
import { skillInMode, skillMatches, skillScenes } from '../skills';
import SkillHub from './SkillHub';

type Props = { kind: string; onError: (message: string) => void; onDirty: (value: boolean) => void;
  confirm: (title: string, detail: string, choices: string[]) => Promise<number>; openConversation: (id: string) => void };
const titles: Record<string, string> = { skills: '技能', mcp: 'MCP 服务', experts: '专家', favorites: '常用任务', schedule_history: '运行记录' };
const descriptions: Record<string, string> = { skills: '按任务选择能力，也可以安装自己的技能。', mcp: '连接已有工具与服务，让 DeepSeek Cowork 在你的工作环境中完成任务。', experts: '保存角色、工作方法与可用技能，在对话中随时选择。', favorites: '把经常做的事保存下来，一键运行或安排时间。', schedule_history: '查看任务结果、错过的计划和需要恢复的操作。' };

export default function Capabilities(props: Props) {
  const { kind, onError, confirm, openConversation } = props;
  const [items, setItems] = useState<any[]>([]), [selected, setSelected] = useState<any>(null);
  const [query, setQuery] = useState(''), [failure, setFailure] = useState(''), [busy, setBusy] = useState(false), [job, setJob] = useState<any>(null);
  const [mode, setMode] = useState('library'), [scene, setScene] = useState('');
  const reload = useCallback(async () => {
    setBusy(true); setFailure('');
    try { setItems(await rpc(kind === 'skills' ? 'skills.inventory' : kind === 'schedule_history' ? 'favorites.history' : kind + '.list')); }
    catch (error) { setFailure((error as Error).message); } finally { setBusy(false); }
  }, [kind]);
  useEffect(() => { void reload(); }, [reload]);
  async function act(method: string, params: any) {
    setBusy(true);
    try { const result = await operation(method, params, setJob); await reload(); return result; }
    catch (error) { onError((error as Error).message); } finally { setBusy(false); setJob(null); }
  }
  if (selected) return kind === 'skills' ? <SkillEditor key={selected.name} {...props} item={selected} back={() => setSelected(null)} saved={reload}/>
    : <ItemEditor key={selected.id || 'new'} {...props} item={selected} back={() => { setSelected(null); void reload(); }}/ >;
  if (kind === 'skills' && mode === 'hub') return <SkillHub back={() => setMode('library')} mine={() => {setMode('mine'); void reload();}} onError={onError}/>;
  const candidates = kind === 'skills' ? items.filter(item => skillInMode(item, mode) && skillMatches(item, query)) : items.filter(item => [item.name, item.display_name, item.description, item.user_description, item.favorite_name].some(value => String(value || '').toLowerCase().includes(query.toLowerCase())));
  const rows = candidates.filter(item => kind !== 'skills' || mode !== 'library' || !scene || item.presentation?.category === scene);
  return <>
    {kind === 'skills' && <div className="skill-modes"><div className="segmented">{[['library','发现技能'],['mine','我的能力']].map(([key,label]) => <button key={key} className={mode === key ? 'selected' : ''} onClick={() => setMode(key)}>{label}<small>{items.filter(item => skillInMode(item,key)).length}</small></button>)}</div><button className={mode === 'builtin' ? 'selected' : ''} onClick={() => setMode('builtin')}>内置技能</button></div>}
    <PageToolbar title={titles[kind]}><div className="page-search"><Search size={17}/><input aria-label={'搜索' + titles[kind]} placeholder={kind === 'skills' ? '搜索想完成的任务，例如：读 PDF、查资料、分析股票' : '搜索名称或用途'} value={query} onChange={e => setQuery(e.target.value)}/></div>{kind !== 'schedule_history' && <button className="primary" disabled={busy} onClick={async () => {
      if (kind !== 'skills') { setSelected({}); return; }
      const path = await rpc('dialog.choose', { kind: 'skill' }); if (path) await act('skills.import', { path });
    }}><Plus size={16}/>{kind === 'skills' ? '导入技能' : '新建'}</button>}</PageToolbar>
    {kind === 'skills' && mode === 'library' && <label className="skill-category-select"><span>分类</span><select aria-label="技能分类" value={scene} onChange={event => setScene(event.target.value)}>{[['','全部任务'],...skillScenes].map(([key,label]) => <option key={key} value={key}>{label} · {candidates.filter(item => !key || item.presentation?.category === key).length}</option>)}</select></label>}
    <JobProgress job={job}/>{failure && <ErrorState message={failure} retry={() => void reload()}/>}
    {busy && !items.length ? <div className="loading"><LoaderCircle className="spin"/>正在载入…</div> : !rows.length ? <Empty title={query ? '没有匹配的内容' : '从你的第一项开始'} detail={query ? '换一个名称或用途试试。' : descriptions[kind]}/> :
    <div className={kind === 'skills' ? 'capability-grid' : 'resource-list'}>{rows.map(item => <article className="resource-card" key={item.id || item.name}>
      <div className="resource-symbol">{kind === 'skills' ? <Sparkles size={20}/> : kind === 'favorites' ? <Star size={20}/> : <FileCode2 size={20}/>}</div>
      <div className="resource-body"><button className="resource-title" onClick={() => { if (kind === 'schedule_history') { if (item.session_id) openConversation(item.session_id); } else setSelected(item); }}>{item.presentation?.short_name || item.display_name || item.name || item.favorite_name}</button>
        <p>{item.presentation?.summary || item.user_description || item.description || (kind === 'mcp' ? item.url || item.command : '') || item.summary || item.error}</p>
        {kind === 'skills' && item.config_status?.has_config && !item.config_status?.complete && <button className="text-link" onClick={() => setSelected(item)}>完成连接配置</button>}
        {item.schedule && <small>{item.schedule.enabled ? item.schedule.schedule_summary : '计划已暂停'}</small>}
        {kind === 'schedule_history' && <small>{({ running: '正在运行', completed: '已完成', error: '失败', missed: '已错过', interrupted: '已停止' } as any)[item.status]} · {new Date((item.started_at || item.created_at) * 1000).toLocaleString()}{item.delivery_error && <span className="error-text">{item.delivery_error}</span>}</small>}
      </div><div className="resource-actions">
        {kind === 'skills' ? <button className={`toggle ${item.enabled ? 'on' : ''}`} role="switch" aria-checked={Boolean(item.enabled)} aria-label={(item.enabled ? '停用' : '启用') + (item.display_name || item.name)} disabled={busy} onClick={() => void act('skills.enable', { name: item.name, enabled: !item.enabled })}><span/></button> :
          kind === 'favorites' ? <button className="icon-button" title="立即运行" disabled={busy} onClick={async () => { const result = await act('favorites.run', { id: item.id }); if (result?.conversation_id) openConversation(result.conversation_id); }}><Play size={17}/></button> :
          kind === 'mcp' ? <button className="secondary" disabled={busy} onClick={() => void act('mcp.test', { id: item.id })}>测试连接</button> : null}
        {kind !== 'schedule_history' && <button className="icon-button" title="编辑" onClick={() => setSelected(item)}><Pencil size={16}/></button>}
        {kind !== 'schedule_history' && (kind !== 'skills' || item.editable) && <button className="icon-button" title="删除" disabled={busy} onClick={async () => {
          if (await confirm('删除' + (item.display_name || item.name) + '？', kind === 'skills' ? '将删除此技能的本地文件。可以先打开详情导出备份。' : '已存在的对话和运行结果会保留。', ['取消', '删除']) === 1) await act(kind + '.delete', { id: item.id, name: item.name });
        }}><Trash2 size={16}/></button>}
      </div></article>)}</div>}
    {kind === 'skills' && mode !== 'builtin' && <button className="skillhub-entry" onClick={() => setMode('hub')}>没有找到合适的？　去 SkillHub 查找更多 <ExternalLink size={15}/></button>}
  </>;
}

function ItemEditor({ item, kind, onDirty, onError, confirm, back }: Props & { item: any; back: () => void }) {
  const initial = kind === 'mcp' ? { name: '', transport: 'stdio', command: '', args: [], url: '', enabled: true, ...item }
    : kind === 'experts' ? { name: '', description: '', system_prompt: '', skill_names: [], enabled: true, ...item }
    : { name: '', description: '', prompt: '', skill_names: [], execution_mode: 'chat', workspace_dir: '', schedule: null, ...item };
  const form = useFormDraft(kind + ':' + (item.id || 'new'), initial, onDirty, ['api_key', 'headers', 'env']);
  const v = form.value, set = (key: string, value: any) => form.change({ ...v, [key]: value });
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState(''), [skills, setSkills] = useState<any[]>([]);
  const [advanced, setAdvanced] = useState(JSON.stringify({ env: item.env || {}, headers: item.headers || {}, auth: item.auth || {} }, null, 2));
  useEffect(() => { if (kind !== 'mcp') void rpc('skills.list').then(setSkills).catch(error => onError(error.message)); }, [kind]);
  const schedule = v.schedule || {};
  const setSchedule = (values: any) => set('schedule', { ...schedule, ...values, next_run_at: 0 });
  async function leave() { if (!form.dirty || await confirm('保留编辑草稿？', '下次打开可以继续编辑。', ['继续编辑', '保留并返回']) === 1) back(); }
  return <><PageHead title={(item.id ? '编辑' : '新建') + titles[kind]} back={() => void leave()}/>
    <form className="detail-form" onSubmit={async e => { e.preventDefault(); setBusy(true); setFailure('');
      try { const value = kind === 'mcp' ? { ...v, ...JSON.parse(advanced) } : v;
        await rpc(kind + '.save', { value }); form.done(); back();
      } catch (error) { setFailure((error as Error).message); } finally { setBusy(false); }
    }}><div className="form-grid">
      <Field label="名称" wide><input required value={v.name} onChange={e => set('name', e.target.value)}/></Field>
      {kind !== 'mcp' && <Field label="用途" wide><input value={v.description} onChange={e => set('description', e.target.value)}/></Field>}
      {kind === 'mcp' ? <>
        <Field label="连接方式"><select value={v.transport} onChange={e => set('transport', e.target.value)}><option value="stdio">本机程序</option><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option></select></Field>
        {v.transport === 'stdio' ? <><Field label="程序"><input required value={v.command} onChange={e => set('command', e.target.value)}/></Field><Field label="启动参数" help="每行一个参数" wide><textarea rows={4} value={v.args.join('\n')} onChange={e => set('args', e.target.value.split('\n'))}/></Field></> : <Field label="服务地址"><input type="url" required value={v.url} onChange={e => set('url', e.target.value)}/></Field>}
        <details className="wide"><summary>环境变量、请求头与认证</summary><textarea aria-label="连接配置 JSON" className="code-input" rows={10} value={advanced} onChange={e => { setAdvanced(e.target.value); onDirty(true); }}/><small>敏感凭据不会保存在表单草稿中。</small></details>
      </> : <>
        <Field label={kind === 'experts' ? '工作指引' : '任务内容'} wide><textarea rows={9} required value={v[kind === 'experts' ? 'system_prompt' : 'prompt']} onChange={e => set(kind === 'experts' ? 'system_prompt' : 'prompt', e.target.value)}/></Field>
        <details className="wide"><summary>指定可用技能 · {v.skill_names.length} 项</summary><div className="skill-picker">{skills.map(skill => <label key={skill.name}><input type="checkbox" checked={v.skill_names.includes(skill.name)} onChange={e => set('skill_names', e.target.checked ? [...v.skill_names, skill.name] : v.skill_names.filter((name: string) => name !== skill.name))}/>{skill.display_name || skill.name}</label>)}</div></details>
        {kind === 'favorites' && <>
          <Field label="工作目录" wide><div className="input-action"><input readOnly placeholder="自动创建独立工作目录" value={v.workspace_dir}/><button type="button" className="secondary" onClick={async () => { const path = await rpc('dialog.workspace'); if (path) form.change({ ...v, workspace_dir: path, execution_mode: 'workspace' }); }}><FolderOpen size={16}/>选择</button></div></Field>
          <label className="check-line wide"><input type="checkbox" checked={Boolean(schedule.enabled)} onChange={e => setSchedule({ enabled: e.target.checked, schedule_type: schedule.schedule_type || 'daily' })}/>按计划运行</label>
          {schedule.enabled && <>
            <Field label="频率"><select value={schedule.schedule_type || 'daily'} onChange={e => setSchedule({ schedule_type: e.target.value })}><option value="daily">每天</option><option value="weekly">每周</option><option value="monthly">每月</option><option value="interval">固定间隔</option><option value="once">只运行一次</option><option value="cron">Cron</option></select></Field>
            {['daily', 'weekly', 'monthly'].includes(schedule.schedule_type || 'daily') && <Field label="时间"><input type="time" required value={schedule.time_of_day || '09:00'} onChange={e => setSchedule({ time_of_day: e.target.value })}/></Field>}
            {schedule.schedule_type === 'weekly' && <div className="weekday-picker wide">{['一','二','三','四','五','六','日'].map((day, i) => <label key={day}><input type="checkbox" checked={(schedule.weekdays || [0]).includes(i)} onChange={e => setSchedule({ weekdays: e.target.checked ? [...(schedule.weekdays || [0]), i] : (schedule.weekdays || [0]).filter((n: number) => n !== i) })}/>{day}</label>)}</div>}
            {schedule.schedule_type === 'monthly' && <Field label="日期"><input type="number" min={1} max={31} value={schedule.day_of_month || 1} onChange={e => setSchedule({ day_of_month: +e.target.value })}/></Field>}
            {schedule.schedule_type === 'interval' && <Field label="间隔（分钟）"><input type="number" min={1} max={1440} value={schedule.interval_minutes || 60} onChange={e => setSchedule({ interval_minutes: +e.target.value })}/></Field>}
            {schedule.schedule_type === 'once' && <Field label="运行时间"><input required type="datetime-local" value={schedule.one_time_at ? new Date((schedule.one_time_at * 1000) - new Date().getTimezoneOffset()*60000).toISOString().slice(0,16) : ''} onChange={e => setSchedule({ one_time_at: Date.parse(e.target.value)/1000 })}/></Field>}
            {schedule.schedule_type === 'cron' && <Field label="Cron 表达式"><input required value={schedule.cron_expression || ''} onChange={e => setSchedule({ cron_expression: e.target.value })} placeholder="0 9 * * 1-5"/></Field>}
            <p className="muted wide">后台运行时自动执行。电脑关机或后台停止期间错过的计划会记录为“已错过”，可以手动运行。</p>
          </>}
        </>}
      </>}
    </div>{failure && <ErrorState message={failure}/>}<div className="form-actions"><button type="button" className="secondary" onClick={() => void leave()}>返回</button><button className="primary" disabled={busy}>{busy ? <LoaderCircle className="spin" size={16}/> : <Check size={16}/>}保存</button></div></form>
  </>;
}

function SkillEditor({ item, onDirty, onError, confirm, back, saved }: Props & { item: any; back: () => void; saved: () => Promise<void> }) {
  const [detail, setDetail] = useState<any>(null), [path, setPath] = useState('SKILL.md'), [file, setFile] = useState<any>(null), [failure, setFailure] = useState('');
  useEffect(() => { void rpc('skills.get', { name: item.name }).then(setDetail).catch(error => setFailure(error.message)); }, [item.name]);
  useEffect(() => { setFile(null); void rpc('skills.read', { name: item.name, path }).then(setFile).catch(error => setFailure(error.message)); }, [item.name, path]);
  const [config, setConfig] = useState<any>({});
  return <><PageHead title={item.display_name || item.name} detail={item.user_description || item.description} back={back}><button className="secondary" onClick={async () => { const target = await rpc('dialog.save', { name: item.name + '.zip' }); if (target) { try { await rpc('skills.export', { name: item.name, path: target }); onError('技能包已导出。'); } catch (error) { onError((error as Error).message); } } }}><Download size={16}/>导出</button></PageHead>
    {failure && <ErrorState message={failure}/>} {detail?.fields?.length > 0 && <section className="settings-section"><h3>连接配置</h3><form onSubmit={async e => { e.preventDefault(); try { await rpc('skills.configure', { name: item.name, values: config }); setConfig({}); await saved(); onError('配置已保存。'); } catch (error) { setFailure((error as Error).message); } }}><div className="form-grid">{detail.fields.map((field: any) => <Field key={field.name} label={field.label} help={field.help}>
      {field.kind === 'select' ? <select value={config[field.name] ?? detail.values[field.name] ?? field.default} onChange={e => setConfig({ ...config, [field.name]: e.target.value })}>{field.options.map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}</select> : <input type={field.kind === 'secret' ? 'password' : 'text'} autoComplete="off" placeholder={detail.configured_fields.includes(field.name) ? '已配置，填写可替换' : field.placeholder} value={config[field.name] ?? detail.values[field.name] ?? ''} onChange={e => setConfig({ ...config, [field.name]: e.target.value })}/>}
    </Field>)}</div><button className="primary">保存配置</button></form></section>}
    {detail && <section className="settings-section"><div className="section-heading"><h3>技能文件</h3><select aria-label="选择技能文件" value={path} onChange={e => setPath(e.target.value)}>{detail.files.files.map((name: string) => <option key={name}>{name}</option>)}</select></div>
      {file && <SkillFile key={path} name={item.name} path={path} content={file.content} editable={detail.files.editable} onDirty={onDirty} onError={onError} saved={saved}/>}</section>}
  </>;
}
function SkillFile({ name, path, content, editable, onDirty, onError, saved }: any) {
  const form = useFormDraft('skill:' + name + ':' + path, { content }, onDirty);
  const [original, setOriginal] = useState(content), [busy, setBusy] = useState(false);
  return <><textarea className="code-input skill-file" aria-label={path} readOnly={!editable} value={form.value.content} onChange={e => form.change({ content: e.target.value })}/>{editable && <button className="primary" disabled={busy || !form.dirty} onClick={async () => { setBusy(true); try { await rpc('skills.write', { name, path, original, content: form.value.content }); setOriginal(form.value.content); form.done(); await saved(); } catch (error) { onError((error as Error).message); } finally { setBusy(false); } }}><Check size={16}/>保存文件</button>}</>;
}
