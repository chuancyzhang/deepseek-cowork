import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Building2, Check, ChevronRight, ExternalLink, FileText, Folder, LoaderCircle, LogOut, MessageSquare, Plus, RefreshCw, Users } from 'lucide-react';
import { Empty, ErrorState, Field, JobProgress, PageHead, PageToolbar, useFormDraft } from '../Management';
import { operation, rpc } from '../managementApi';
type Props = { kind: string; onError: (message: string) => void; onDirty: (value: boolean) => void; openConversation: (id: string) => void;
  confirm: (title: string, detail: string, choices: string[]) => Promise<number> };
export default function Enterprise(props: Props) {
  const [state, setState] = useState<any>(null), [failure, setFailure] = useState(''), [busy, setBusy] = useState(false), [job, setJob] = useState<any>(null);
  const [providers, setProviders] = useState<any[]>([]), [provider, setProvider] = useState(''), [credentials, setCredentials] = useState({ username: '', password: '' });
  const load = useCallback(async () => { try { const value = await rpc('enterprise.status'); setState(value); return value; } catch (error) { setFailure((error as Error).message); } }, []);
  useEffect(() => { void load(); }, [load]);
  async function act(method: string, params: any = {}) { setBusy(true); setFailure(''); try { const result = await operation(method, params, setJob); await load(); return result; } catch (error) { setFailure((error as Error).message); } finally { setBusy(false); setJob(null); } }
  if (props.kind === 'team' && state?.member) return <TeamSpaces {...props}/>;
  return <><PageHead title={props.kind === 'team' ? '连接团队空间' : '企业连接'} detail="使用企业提供的账号与模型，个人资料继续保存在本机。"/>
    <JobProgress job={job}/>{failure && <ErrorState message={failure} retry={() => void load()}/>}
    {state?.member ? <section className="enterprise-account"><Building2 size={36}/><h3>{state.connection.platform_name}</h3><p>{state.member.name || state.member.display_name || state.member.username}</p><div className="button-row"><button className="secondary" disabled={busy} onClick={() => void act('enterprise.refresh')}><RefreshCw size={15}/>刷新授权</button><button className="secondary" disabled={busy} onClick={async () => { if (await props.confirm('退出企业账号？', '企业模型和空间将暂停使用。已有对话、草稿和文件会保留。', ['取消','退出']) === 1) await act('enterprise.logout'); }}><LogOut size={15}/>退出登录</button></div></section> : state?.connection ? <section className="connection-form"><Building2 size={32}/><h3>{state.connection.platform_name}</h3><p className="muted">{state.connection.platform_url}</p>
      {!providers.length ? <button className="primary" disabled={busy} onClick={async () => { const result = await act('enterprise.providers'); if (result) { setProviders(result); setProvider(result[0]?.id || ''); } }}>选择登录方式</button> : <form onSubmit={async e => { e.preventDefault(); const selected = providers.find(item => item.id === provider); const result = await act('enterprise.login', { provider: selected, ...credentials }); if (result) setCredentials({ username: '', password: '' }); }}><div className="form-grid"><Field label="登录方式" wide><select value={provider} onChange={e => setProvider(e.target.value)}>{providers.map(item => <option key={item.id} value={item.id}>{item.name || item.display_name || item.mode}</option>)}</select></Field>{providers.find(item => item.id === provider)?.mode === 'ldap' && <><Field label="账号"><input required value={credentials.username} onChange={e => setCredentials({ ...credentials, username: e.target.value })}/></Field><Field label="密码"><input required type="password" autoComplete="off" value={credentials.password} onChange={e => setCredentials({ ...credentials, password: e.target.value })}/></Field></>}</div><button className="primary" disabled={busy}>登录</button></form>}
    </section> : <ConnectionForm {...props} submit={async value => { await act('enterprise.bind', { connection: value }); }} busy={busy}/>}
  </>;
}
function ConnectionForm({ onDirty, submit, busy }: Props & { submit: (value: any) => Promise<void>; busy: boolean }) {
  const form = useFormDraft('enterprise-connection', { schema_version: 1, config_version: 1, distribution_channel: 'enterprise', platform_id: '', platform_name: '', platform_url: '' }, onDirty);
  return <form className="detail-form" onSubmit={async e => { e.preventDefault(); await submit(form.value); }}><div className="form-grid">{[['platform_name','企业名称'],['platform_id','企业标识'],['platform_url','平台地址']].map(([key,label]) => <Field key={key} label={label} wide><input required type={key === 'platform_url' ? 'url' : 'text'} value={form.value[key]} onChange={e => form.change({ ...form.value, [key]: e.target.value })}/></Field>)}</div><button className="primary" disabled={busy}>连接企业</button></form>;
}

function TeamSpaces(props: Props) {
  const [spaces, setSpaces] = useState<any[]>([]), [space, setSpace] = useState<any>(null), [tab, setTab] = useState('plans'), [items, setItems] = useState<any[]>([]);
  const [failure, setFailure] = useState(''), [busy, setBusy] = useState(false), [job, setJob] = useState<any>(null), [form, setForm] = useState<any>(null), [object, setObject] = useState<any>(null);
  async function act(method: string, params: any = {}) { setBusy(true); setFailure(''); try { return await operation(method, { space_id: space?.id, ...params }, setJob); } catch (error) { setFailure((error as Error).message); } finally { setBusy(false); setJob(null); } }
  async function load() { const result = await act('team.list'); if (result) setSpaces(result.items); }
  useEffect(() => { void load(); }, []);
  async function open(id: string) { const value = await act('team.detail', { space_id: id }); if (value) { setSpace(value); setTab('plans'); setObject(null); setForm(null); } }
  useEffect(() => { if (space) void act('team.collection', { collection: tab }).then(result => { if (result) setItems(result.items || []); }); }, [space?.id, tab, space?.revision]);
  if (form) return <TeamForm {...props} item={form} space={space} back={() => setForm(null)} saved={async result => { setForm(null); await open(result.id || space.id); }}/ >;
  const createButton = <button className="primary" disabled={busy} onClick={() => setForm({ action: space ? 'conversation' : 'space.create' })}><Plus size={16}/>{space ? '新建任务' : '创建空间'}</button>;
  return <>{space ? <PageHead title={space.name} detail={space.description} back={() => { setSpace(null); setObject(null); void load(); }}>{createButton}</PageHead> : <PageToolbar title="团队空间">{createButton}</PageToolbar>}
    <JobProgress job={job}/>{failure && <ErrorState message={failure} retry={() => void (space ? open(space.id) : load())}/>}
    {!space ? <div className="capability-grid">{spaces.map(item => <button className="library-collection" key={item.id} onClick={() => void open(item.id)}><Users size={24}/><span><strong>{item.name}</strong><small>{item.description || (item.state === 'archived' ? '已归档' : '团队协作')}</small></span><ChevronRight size={17}/></button>)}</div> : <>
      <div className="segmented">{[['plans','计划'],['assets','资料'],['shares','成果'],['snapshots','任务快照'],['members','成员'],['events','动态']].map(([id,label]) => <button key={id} className={tab === id ? 'selected' : ''} onClick={() => { setTab(id); setObject(null); }}>{label}</button>)}</div>
      {tab === 'plans' && <button className="secondary" onClick={() => setForm({ action: 'plan.create' })}><Plus size={15}/>新建计划</button>}
      {!items.length && !busy && <Empty title="这里还没有内容" detail="从创建一项计划或任务开始。"/>}
      <div className="resource-list">{items.map(item => <div className="model-row" key={item.id}><FileText size={19}/><div className="grow"><button className="resource-title" onClick={async () => { if (['members','events'].includes(tab)) return; const result = await act('team.object', { kind: ({ plans:'plan',assets:'asset',shares:'share',snapshots:'snapshot' } as any)[tab], id: item.id }); if (result) setObject(result); }}>{item.title || item.name || item.event || item.type}</button><small>{item.description || item.status || item.role || item.state}</small></div></div>)}</div>
      {object && <section className="object-detail"><div className="section-heading"><h3>{object.title || object.name}</h3><button className="secondary" onClick={() => setObject(null)}>收起</button></div><p className="preserve-lines">{object.description || object.body || object.content}</p>
        {tab === 'plans' && <button className="secondary" onClick={() => setForm({ ...object, action: 'plan.update' })}>编辑计划</button>}
        {tab === 'assets' && object.revisions?.map((revision: any) => <div className="model-row" key={revision.id}><span>版本 {revision.number}</span><button className="secondary" onClick={async () => { const path = await rpc('dialog.save', { name: object.name }); if (path) await act('team.download', { revision_id: revision.id, path }); }}>下载</button></div>)}
      </section>}
    </>}
  </>;
}
function TeamForm({ item, space, back, saved, onDirty, openConversation }: Props & { item: any; space: any; back: () => void; saved: (result: any) => Promise<void> }) {
  const initial = item.action === 'conversation' ? { title: '', prompt: '', directory: '', model_id: '', acknowledge_skills: false } : item.action === 'space.create' ? { name: '', description: '', allow_personal: true } : { title: '', description: '', assignee_id: '', priority: 'normal', inputs: [], ...item };
  const form = useFormDraft('team:' + (space?.id || '') + ':' + item.action + ':' + (item.id || 'new'), initial, onDirty);
  const [models, setModels] = useState<any[]>([]), [job, setJob] = useState<any>(null), [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  const [operationId] = useState(() => crypto.randomUUID());
  const v = form.value, set = (key: string, value: any) => form.change({ ...v, [key]: value });
  useEffect(() => { if (item.action === 'conversation') void rpc('settings.get').then(settings => setModels(settings.models)); }, []);
  return <><PageHead title={item.action === 'conversation' ? '新建空间任务' : item.action === 'space.create' ? '创建空间' : '编辑计划'} back={back}/><JobProgress job={job}/><form className="detail-form" onSubmit={async e => { e.preventDefault(); setBusy(true); setFailure(''); try {
    const result = item.action === 'conversation' ? await operation('team.conversation', { ...v, space_id: space.id, description_revision: space.description_revision }, setJob)
      : await operation('team.execute', { space_id: space?.id || '', action: item.action, payload: { ...v, action: undefined }, operation_id: operationId }, setJob);
    form.done(); if (result.conversation_id) openConversation(result.conversation_id); else await saved(result);
  } catch (error) { setFailure((error as Error).message); } finally { setBusy(false); setJob(null); } }}><div className="form-grid"><Field label="名称" wide><input required value={v.name ?? v.title} onChange={e => set(v.name !== undefined ? 'name' : 'title', e.target.value)}/></Field><Field label={item.action === 'conversation' ? '这次要完成什么' : '说明'} wide><textarea rows={8} required value={v.prompt ?? v.description} onChange={e => set(v.prompt !== undefined ? 'prompt' : 'description', e.target.value)}/></Field>
    {item.action === 'conversation' && <><Field label="空间专用目录" wide><div className="input-action"><input required readOnly value={v.directory}/><button className="secondary" type="button" onClick={async () => { const path = await rpc('dialog.workspace'); if (path) set('directory', path); }}>选择</button></div></Field><Field label="本次模型" wide><select required value={v.model_id} onChange={e => set('model_id', e.target.value)}><option value="">请选择</option>{models.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}</select></Field>{space.skills?.length > 0 && <label className="check-line wide"><input type="checkbox" checked={v.acknowledge_skills} onChange={e => set('acknowledge_skills', e.target.checked)}/>本次不使用空间指定版本的技能引用，进入对话后手动选择可用技能</label>}</>}
    {item.action.startsWith('plan.') && <><Field label="负责人"><select value={v.assignee_id} onChange={e => set('assignee_id', e.target.value)}><option value="">未分配</option>{space.members?.filter((m: any) => m.active && m.enabled).map((m: any) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></Field><Field label="优先级"><select value={v.priority} onChange={e => set('priority', e.target.value)}><option value="normal">普通</option><option value="high">高</option></select></Field></>}
  </div>{failure && <ErrorState message={failure}/>}<button className="primary" disabled={busy}><Check size={16}/>{item.action === 'conversation' ? '创建并打开对话' : '保存'}</button></form></>;
}
