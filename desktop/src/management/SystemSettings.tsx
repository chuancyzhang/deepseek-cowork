import { useEffect, useState } from 'react';
import { Check, ChevronRight, Download, Folder, LoaderCircle, RefreshCw } from 'lucide-react';
import { ErrorState, Field, JobProgress, PageHead, useFormDraft } from '../Management';
import { operation, rpc } from '../managementApi';

type Props = { kind: string; onError: (message: string) => void; onDirty: (dirty: boolean) => void; confirm: (title: string, detail: string, choices: string[]) => Promise<number> };
export default function SystemSettings(props: Props) {
  const [page, setPage] = useState<any>(null), [failure, setFailure] = useState(''), [revision, setRevision] = useState(0), [context, setContext] = useState('');
  useEffect(() => { let alive = true; setPage(null); setFailure(''); void rpc('settings.page', {page: props.kind, context}).then(value => {if (alive) setPage(value);}).catch(failure => {if (alive) setFailure(failure.message);}); return () => {alive = false;}; }, [props.kind, revision, context]);
  if (failure) return <ErrorState message={failure} retry={() => setRevision(value => value + 1)}/>;
  return page ? <SettingsForm key={props.kind + revision + context} {...props} page={page} context={context} setContext={setContext} reload={() => setRevision(value => value + 1)}/> : <div className="loading"><LoaderCircle className="spin" size={20}/>正在读取设置…</div>;
}
function SettingsForm({ page, kind, onDirty, onError, confirm, reload, context, setContext }: Props & {page: any; reload: () => void; context: string; setContext: (value: string) => void}) {
  const fields = page.sections.flatMap((section: any) => section.fields || []), secrets = fields.filter((field: any) => field.type === 'password').map((field: any) => field.key);
  const form = useFormDraft('settings:' + kind + ':' + context, page.values || {}, kind === 'logs' ? () => {} : onDirty, secrets);
  const [savedRevision, setSavedRevision] = useState(page.revision);
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState(''), [job, setJob] = useState<any>(null), [detail, setDetail] = useState<any>(null);
  function set(key: string, value: any) {form.change({...form.value, [key]: value});}
  async function action(item: any) {
    if (item.requires_saved && form.dirty) {setFailure('请先保存当前编辑，再执行此操作。'); return;}
    if (item.confirm && await confirm(item.title, item.confirm, ['取消', item.label]) !== 1) return;
    setBusy(true); setFailure('');
    try {
      let destination: string | null = null;
      if (item.save_name) {destination = await rpc('dialog.save', {name: item.save_name}); if (!destination) return;}
      if (item.open_kind) {destination = await rpc('dialog.choose', {kind: item.open_kind}); if (!destination) return;}
      const result = await operation('settings.action', {page: kind, context, action: item.id, values: form.value, path: destination}, setJob);
      if (result?.detail) setDetail(result.detail);
      if (result?.open_path) await rpc('system.open_path', {path: result.open_path});
      if (result?.draft_values) form.change({...form.value, ...result.draft_values});
      if (result?.message) onError(result.message);
      if (result?.saved) {form.done(result.values || form.value); reload();}
      else if (result?.refresh && !form.dirty) reload();
    } catch (failure) {setFailure((failure as Error).message);} finally {setBusy(false); setJob(null);}
  }
  function renderField(field: any) {
      const value = form.value[field.key] ?? field.default ?? '';
      if (kind === 'im' && field.key === 'enabled_provider') return <div className="segmented connection-channels wide" role="group" aria-label={field.label} key={field.key}>{field.options.map((option: any) => <button type="button" key={option.value} disabled={busy} aria-pressed={value === option.value} className={value === option.value ? 'selected' : ''} onClick={() => set(field.key, option.value)}>{option.label}</button>)}</div>;
      if (field.type === 'toggle') return <label className="check-line wide" key={field.key}><input type="checkbox" checked={Boolean(value)} disabled={busy || field.read_only} onChange={event => set(field.key, event.target.checked)}/><span>{field.label}{field.help && <small>{field.help}</small>}</span></label>;
      return <Field label={field.label} help={field.help} wide={field.wide || ['textarea','path','password'].includes(field.type)} key={field.key}>
        {field.type === 'textarea' ? <textarea rows={field.rows || 6} value={value} disabled={busy || field.read_only} onChange={event => set(field.key, event.target.value)}/> : field.type === 'select' ? <select value={value} disabled={busy || field.read_only} onChange={event => set(field.key, event.target.value)}>{field.options.map((option: any) => <option key={String(option.value)} value={option.value}>{option.label}</option>)}</select> : field.type === 'path' ? <div className="input-action"><input value={value} disabled={busy || field.read_only} onChange={event => set(field.key, event.target.value)}/><button type="button" className="secondary" disabled={busy || field.read_only} onClick={async () => {const path = await rpc('dialog.workspace'); if (path) set(field.key, path);}}><Folder size={14}/>选择</button></div> : <input type={field.type === 'password' ? 'password' : field.type === 'number' ? 'number' : 'text'} autoComplete={field.type === 'password' ? 'off' : undefined} value={value} min={field.min} max={field.max} step={field.step} placeholder={field.placeholder} disabled={busy || field.read_only} onChange={event => set(field.key, field.type === 'number' ? Number(event.target.value) : event.target.value)}/>}
      </Field>;
  }
  return <><PageHead compact title={page.title} detail={page.description}><button className="icon-button" title="刷新状态" disabled={busy || (kind !== 'logs' && form.dirty)} onClick={reload}><RefreshCw size={17}/></button></PageHead>{page.contexts && <Field label="记忆范围"><select value={context} disabled={busy} onChange={async event => {const value = event.target.value; if (!form.dirty || await confirm('保留未保存的记忆？', '编辑草稿按项目保留，下次选择该项目时可继续。', ['继续编辑','切换项目']) === 1) setContext(value);}}>{page.contexts.map((item: any) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></Field>}<JobProgress job={job}/><form className="detail-form" onSubmit={async event => {
    event.preventDefault(); setBusy(true); setFailure('');
    try {const result = await rpc('settings.page_save', {page: kind, context, value: form.value, revision: savedRevision}); form.done(result.values || form.value); setSavedRevision(result.revision); onError('设置已保存。');}
    catch (failure) {setFailure((failure as Error).message);} finally {setBusy(false);}
  }}>
    {page.sections.filter((section: any) => !section.provider || section.provider === form.value.enabled_provider).map((section: any) => <section className="settings-section" key={section.id || section.title}>
      {section.title && <h3 className={section.provider ? 'sr-only' : undefined}>{section.title}</h3>}{section.description && <p className="muted">{section.description}</p>}
      {section.fields?.length > 0 && <><div className="form-grid">{section.fields.filter((field: any) => !field.advanced).map(renderField)}</div>
        {section.fields.some((field: any) => field.advanced) && <details className="advanced-fields"><summary>更多选项</summary><div className="form-grid">{section.fields.filter((field: any) => field.advanced).map(renderField)}</div></details>}</>}
      {section.status && !(kind === 'im' && section.provider === page.values.enabled_provider) && <p role="status" className="muted">{section.status}</p>}
      {section.actions?.length > 0 && <div className="button-row">{section.actions.map((item: any) => <button type="button" className="secondary" key={item.id} disabled={busy || item.disabled || (item.requires_saved && form.dirty)} title={item.requires_saved && form.dirty ? '请先保存当前修改' : item.reason || item.label} onClick={() => void action(item)}>{item.save_name && <Download size={15}/>} {item.label}</button>)}</div>}
      {section.items?.map((item: any) => <div className="model-row" key={item.id}><div><strong>{item.name}</strong>{item.description && <small>{item.description}</small>}<small>{item.status}</small>{item.error && <p className="error-text">{item.error}</p>}</div>{item.actions?.map((actionItem: any) => <button type="button" key={actionItem.id} className="secondary" disabled={busy || actionItem.disabled} onClick={() => void action(actionItem)}>{actionItem.label}</button>)}</div>)}
    </section>)}
    {failure && <ErrorState message={failure}/>} {fields.some((field: any) => !field.read_only && !field.transient) && <div className="form-actions"><button type="button" className="secondary" disabled={busy || !form.dirty} onClick={form.reset}>取消修改</button><button className="primary" disabled={busy || !form.dirty}><Check size={16}/>{busy ? '正在保存…' : '保存'}</button></div>}
  </form>{detail && <section className="settings-detail"><div className="section-heading"><h3>{detail.title || '操作详情'}</h3><button onClick={() => setDetail(null)}>收起</button></div><pre>{detail.text || JSON.stringify(detail, null, 2)}</pre></section>}</>;
}
