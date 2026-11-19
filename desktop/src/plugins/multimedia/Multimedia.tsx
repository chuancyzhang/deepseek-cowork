import {useEffect, useRef, useState} from 'react';
import {ArrowLeft, ArrowRight, Check, ChevronDown, LoaderCircle} from 'lucide-react';
import {ErrorState, Field, useFormDraft} from '../../Management';
import {rpc} from '../../managementApi';
import {sameFormValue} from '../../host/forms';
import './multimedia.css';

const labels = {image_generate:'图片生成', video_generate:'视频生成', video_understand:'视频理解'} as const;
type Kind = keyof typeof labels;
const kinds = Object.keys(labels) as Kind[];
const groups: {name: string; rows: [Kind, string][]}[] = [
  {name:'图片', rows:[['image_generate','生成']]},
  {name:'视频', rows:[['video_generate','生成'], ['video_understand','理解']]},
];
type Binding = {channel_id: string; model: string; adapter: string};
type Bindings = Partial<Record<Kind, Binding>>;
type Issues = Partial<Record<Kind, string>>;
type Channel = {channel_id: string; display_name: string; base_url: string; provider_type: string; configured: boolean; adapter: string};
type Addition = {kind: Kind; channel_id: string; model: string; adapter: string; service_id: string; display_name: string; base_url: string};
type Draft = {bindings: Bindings; services: Channel[]; apiKeys?: Record<string,string>; addition: Addition | null};
type MediaConfig = {
  enabled: boolean; revision: string; bindings: Bindings; channels: Channel[];
  adapters: Record<string, {label: string; capabilities: string[]; base_url: string}>;
  known_models: Record<string, {adapter: string; capabilities: string[]}>;
  options: Record<Kind, Binding[]>;
  binding_status: Partial<Record<Kind, {valid?: boolean; message?: string}>>;
};
type Props = {onDirty:(value:boolean)=>void;back:()=>void;returnToChat:()=>void;saved:()=>Promise<void>;
  confirm:(title:string,detail:string,choices:string[])=>Promise<number>};
const identity = (binding: Binding) => JSON.stringify([binding.channel_id,binding.model,binding.adapter]);
const baseline = (data: MediaConfig): Draft => ({bindings:data.bindings, services:[], apiKeys:{}, addition:null});

export default function Multimedia(props: Props) {
  const [data,setData] = useState<MediaConfig | null>(null), [failure,setFailure] = useState('');
  const [attempt,setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    void rpc<MediaConfig>('media.config').then(value => {if (live) setData(value);})
      .catch(error => {if (live) setFailure(error.message);});
    return () => {live = false;};
  }, [attempt]);
  return <div className="multimedia-plugin">{data ? <MediaForm {...props} data={data} loaded={setData}/>
    : failure ? <ErrorState message={failure} retry={() => {setFailure('');setAttempt(value => value + 1);}}/>
    : <p role="status"><LoaderCircle className="spin" size={16}/>正在读取多媒体配置…</p>}</div>;
}

function MediaForm({data,loaded,onDirty,confirm,back,returnToChat,saved}: Props & {data: MediaConfig; loaded:(value:MediaConfig)=>void}) {
  const form = useFormDraft('skill:multimedia:models', baseline(data), onDirty, ['apiKeys']);
  const draft = form.value as Draft;
  const [busy,setBusy] = useState(false), [failure,setFailure] = useState(''), [issues,setIssues] = useState<Issues>({});
  const [additionError,setAdditionError] = useState('');
  const fields = useRef<Partial<Record<Kind, HTMLSelectElement | null>>>({});
  const addInput = useRef<HTMLInputElement>(null);
  const channels = [...data.channels, ...draft.services];
  const {addition} = draft;
  const apiKeys = draft.apiKeys || {};
  const patch = (values: Partial<Draft>) => {
    const next = {...draft,...values};
    if (!Object.keys(next.apiKeys || {}).length) delete next.apiKeys;
    form.change(next);
  };
  const supports = (adapter: string, kind: Kind) => Boolean(data.adapters[adapter]?.capabilities.includes(kind));
  const usableChannels = (kind: Kind) => channels.filter(channel => channel.provider_type === 'openai' &&
    (channel.configured || Boolean(apiKeys[channel.channel_id])) && supports(channel.adapter || 'openai',kind));
  function adapterForAddress(address: string) {
    try {
      const host = new URL(address).hostname;
      return Object.entries(data.adapters).find(([,adapter]) => adapter.base_url && new URL(adapter.base_url).hostname === host)?.[0] || 'openai';
    } catch {return 'openai';}
  }
  const draftServices = () => draft.services.map(channel => ({...channel, api_key:apiKeys[channel.channel_id] || ''}));
  const bindingName = (binding: Binding) => `${channels.find(channel => channel.channel_id === binding.channel_id)?.display_name || '服务不可用'} · ${binding.model}`;
  useEffect(() => {if (addition?.kind) addInput.current?.focus();}, [addition?.kind]);

  function choose(kind: Kind, value: string, choices: Binding[]) {
    setIssues(previous => ({...previous, [kind]:undefined}));
    setAdditionError('');
    if (value === '__add') {
      const channel = usableChannels(kind)[0];
      patch({addition:{kind, channel_id:channel?.channel_id || '__new', model:'', adapter:channel?.adapter || 'openai',
        service_id:crypto.randomUUID(), display_name:'', base_url:''}});
      return;
    }
    const bindings = {...draft.bindings};
    if (!value) delete bindings[kind];
    else {
      const choice = choices.find(item => identity(item) === value);
      if (!choice) return;
      bindings[kind] = choice;
    }
    patch({bindings,addition:null});
  }

  function editAddition(values: Partial<Addition>) {
    if (!addition) return;
    patch({addition:{...addition,...values}});
    setAdditionError('');
  }
  function cancelAddition() {
    if (!addition) return;
    const keys = {...apiKeys};
    delete keys[addition.service_id];
    patch({addition:null,apiKeys:keys});
    setAdditionError('');
    fields.current[addition.kind]?.focus();
  }
  async function add() {
    if (!addition || busy) return;
    const model = addition.model.trim();
    const isNew = addition.channel_id === '__new', channelId = isNew ? addition.service_id : addition.channel_id;
    const binding = {channel_id:channelId, model, adapter:addition.adapter};
    const service = {channel_id:channelId, display_name:addition.display_name.trim(), base_url:addition.base_url.trim(),
      provider_type:'openai', adapter:adapterForAddress(addition.base_url), configured:false};
    setBusy(true);setAdditionError('');
    try {
      if (!model) throw Error('请填写模型标识。');
      if (isNew && (!service.display_name || !/^https?:\/\//.test(service.base_url) || !apiKeys[channelId])) {
        throw Error('请填写服务名称、地址和 API Key。');
      }
      const services = [...draftServices(), ...(isNew ? [{...service, api_key:apiKeys[channelId]}] : [])];
      const checks = await rpc<Record<string,{valid:boolean;message:string}>>('media.validate', {bindings:{[addition.kind]:binding},services});
      if (!checks[addition.kind]?.valid) throw Error(checks[addition.kind]?.message || '请检查模型配置。');
      patch({bindings:{...draft.bindings,[addition.kind]:binding}, services:[...draft.services,...(isNew ? [service] : [])], addition:null});
      setIssues(previous => ({...previous, [addition.kind]:undefined}));
    } catch (error) {setAdditionError((error as Error).message);}
    finally {setBusy(false);}
  }

  async function leave(action:()=>void) {
    if (!busy && (!form.dirty || await confirm('保留未保存的编辑？','草稿会保留在本机，下次进入可以继续。只有点击保存才会修改配置。',['继续编辑','保留草稿并离开']) === 1)) action();
  }
  async function save() {
    if (busy || addition) return;
    setBusy(true);setFailure('');setIssues({});
    try {
      const bindings = draft.bindings;
      const used = new Set(Object.values(bindings).flatMap(binding => binding ? [binding.channel_id] : []));
      const result = await rpc<MediaConfig | {binding_issues: Issues}>('media.save', {
        revision:data.revision, enabled:used.size > 0, bindings,
        services:draftServices().filter(channel => used.has(channel.channel_id)),
      });
      if ('binding_issues' in result) {setIssues(result.binding_issues);return;}
      form.done(baseline(result));loaded(result);await saved();
    } catch (error) {setFailure((error as Error).message);}
    finally {setBusy(false);}
  }
  async function reload() {
    setBusy(true);
    try {
      const latest = await rpc<MediaConfig>('media.config');
      const changed = kinds.filter(kind => !sameFormValue(draft.bindings[kind],form.saved.bindings[kind]));
      const conflicts = changed.filter(kind => !sameFormValue(latest.bindings[kind],form.saved.bindings[kind]) && !sameFormValue(latest.bindings[kind],draft.bindings[kind]));
      if (conflicts.length && await confirm('合并最新配置？', conflicts.map(kind=>labels[kind]).join('、') + '同时有其他修改。保留当前编辑会在这些部分使用你的内容，其余部分读取最新配置；核对后再保存。',['继续编辑','保留当前编辑并合并']) !== 1) return;
      const bindings = {...latest.bindings};
      for (const kind of changed) {
        const choice = draft.bindings[kind];
        if (choice) bindings[kind] = choice;
        else delete bindings[kind];
      }
      const persisted = new Set(latest.channels.map(channel => channel.channel_id)), keys = {...apiKeys};
      for (const id of persisted) delete keys[id];
      const next: Draft = {...draft,bindings,apiKeys:keys,services:draft.services.filter(channel => !persisted.has(channel.channel_id))};
      if (!Object.keys(keys).length) delete next.apiKeys;
      form.rebase(next,baseline(latest));
      loaded(latest);setFailure('');setIssues({});
    } catch (error) {setFailure((error as Error).message);}
    finally {setBusy(false);}
  }

  const hasBinding = Object.values(draft.bindings).some(Boolean);
  const emptyDisabled = !data.enabled && !hasBinding && sameFormValue(draft.bindings,data.bindings);
  const saveLabel = data.enabled ? (hasBinding ? '保存配置' : '保存并停用')
    : hasBinding || emptyDisabled ? '保存并启用' : '保存配置';
  return <>
    <header className="item-editor-heading"><button className="icon-button" title="返回技能" disabled={busy} onClick={()=>void leave(back)}><ArrowLeft size={18}/></button><h2>多媒体</h2></header>
    <form className="detail-form media-config-form" onSubmit={event=>{event.preventDefault();void save();}}>
      {groups.map(group => <section className="media-model-group" key={group.name}><h3>{group.name}</h3><div>{group.rows.map(([kind,label]) => {
        const binding = draft.bindings[kind], editing = addition?.kind === kind;
        const choices = data.options[kind];
        const currentValue = binding ? identity(binding) : '';
        const inChoices = choices.some(item => identity(item) === currentValue);
        const channel = channels.find(channel => channel.channel_id === binding?.channel_id);
        const missing = binding && !channel ? '服务已不可用，请选择其他模型或不配置。'
          : binding && channel && !channel.configured && !apiKeys[channel.channel_id] ? '服务尚未填写 API Key。' : '';
        const savedIssue = sameFormValue(binding,data.bindings[kind]) ? data.binding_status[kind]?.valid === false && data.binding_status[kind]?.message : '';
        const issue = issues[kind] || savedIssue || missing;
        const issueId = 'media-issue-' + kind;
        return <div className="media-model-row" key={kind}>
          <label className="media-model-select"><span>{label}</span>
            <select ref={element => {fields.current[kind] = element;}} aria-label={labels[kind] + '模型'}
              aria-invalid={Boolean(issue) && !editing || undefined} aria-describedby={issue && !editing ? issueId : undefined}
              disabled={busy || Boolean(addition && !editing)}
              value={editing ? '__add' : currentValue} onChange={event => choose(kind,event.target.value,choices)}>
              <option value="">不配置</option>
              {binding && !inChoices && <option value={currentValue} disabled={Boolean(issue)}>{bindingName(binding)}</option>}
              {choices.map(item => <option key={identity(item)} value={identity(item)}>{bindingName(item)}</option>)}
              <option value="__add">添加模型…</option>
            </select>
          </label>
          {issue && !editing && <p id={issueId} className="media-field-error error-text" role="alert">{issue}</p>}
          {editing && <div className="media-inline-model"><div className="form-grid">
            <Field label="服务"><select aria-label="模型服务" disabled={busy} value={addition.channel_id} onChange={event => {
              const channel = channels.find(item => item.channel_id === event.target.value);
              editAddition({channel_id:event.target.value,adapter:channel?.adapter || 'openai'});
            }}><option value="__new">新服务</option>{usableChannels(kind).map(item => <option value={item.channel_id} key={item.channel_id}>{item.display_name}</option>)}</select></Field>
            <Field label="模型"><input ref={addInput} aria-label="模型标识" disabled={busy} value={addition.model} onChange={event => editAddition({model:event.target.value})}
              onKeyDown={event => {if (event.key === 'Enter') {event.preventDefault();void add();}}} list={'media-models-' + kind} placeholder="填写模型标识"/>
              <datalist id={'media-models-' + kind}>{[...new Set([
                ...choices.filter(item => item.channel_id === addition.channel_id && item.adapter === addition.adapter).map(item => item.model),
                ...Object.keys(data.known_models).filter(name => data.known_models[name].adapter === addition.adapter && data.known_models[name].capabilities.includes(kind)),
              ])].map(name => <option key={name} value={name}/>)}</datalist></Field>
            {addition.channel_id === '__new' && <>
              <Field label="服务名称"><input aria-label="服务名称" disabled={busy} value={addition.display_name} onChange={event => editAddition({display_name:event.target.value})}/></Field>
              <Field label="服务地址"><input aria-label="服务地址" type="url" disabled={busy} value={addition.base_url} onChange={event => editAddition({base_url:event.target.value,adapter:adapterForAddress(event.target.value)})} placeholder="https://example.com/v1"/></Field>
              <Field label="API Key" wide><input aria-label="API Key" type="password" autoComplete="off" disabled={busy} value={apiKeys[addition.service_id] || ''} onChange={event => {patch({apiKeys:{...apiKeys,[addition.service_id]:event.target.value}});setAdditionError('');}}/></Field>
            </>}
          </div><details><summary>连接设置<ChevronDown size={14}/></summary>
            <Field label="接口协议"><select aria-label="接口协议" disabled={busy} value={addition.adapter} onChange={event => editAddition({adapter:event.target.value})}>
              {Object.entries(data.adapters).filter(([key])=>supports(key,kind) || key === addition.adapter).map(([key,value]) => <option key={key} value={key} disabled={!supports(key,kind)}>{value.label}</option>)}
            </select></Field>
          </details>
          {additionError && <ErrorState message={additionError}/>}
          <div className="button-row"><button type="button" className="secondary" disabled={busy} onClick={cancelAddition}>取消添加</button>
            <button type="button" className="primary" disabled={busy} onClick={()=>void add()}>使用此模型</button></div></div>}
        </div>;
      })}</div></section>)}
      {failure && <><ErrorState message={failure}/>
        {failure.includes('配置已更新') && <button type="button" className="text-link" disabled={busy} onClick={()=>void reload()}>读取最新配置并保留编辑</button>}
      </>}
      {!addition && <div className="media-config-actions">
        {form.dirty || !data.enabled ? <>
          {form.dirty && <button type="button" className="secondary" disabled={busy} onClick={()=>{form.reset();setFailure('');setIssues({});}}>取消修改</button>}
          <button className="primary" disabled={busy || emptyDisabled}><Check size={16}/>{busy ? '正在保存…' : saveLabel}</button>
        </> : <button type="button" className="primary" onClick={returnToChat}>去聊天使用<ArrowRight size={15}/></button>}
      </div>}
    </form>
  </>;
}
