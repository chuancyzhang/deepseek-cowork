import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, KeyRound, Plus, RefreshCw } from 'lucide-react';
import { ErrorState, Field, JobProgress, PageHead } from '../Management';
import { operation, rpc } from '../managementApi';
import type { Settings } from '../types';

export default function GatewaySetup({ back, saved, confirm }: {back: () => void; saved: (value: Settings) => void; confirm: (title: string, detail: string, choices: string[]) => Promise<number>}) {
  const [url, setUrl] = useState(''), [platform, setPlatform] = useState('auto'), [account, setAccount] = useState<any>(null);
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [code, setCode] = useState(''), [twoFactor, setTwoFactor] = useState(false);
  const [stage, setStage] = useState('site'), [busy, setBusy] = useState(false), [failure, setFailure] = useState(''), [job, setJob] = useState<any>(null);
  const [keys, setKeys] = useState<any[]>([]), [groups, setGroups] = useState<any[]>([]), [chosenKeys, setChosenKeys] = useState<string[]>([]);
  const [records, setRecords] = useState<any[]>([]), [chosen, setChosen] = useState<string[]>([]), [name, setName] = useState('');
  const [keyName, setKeyName] = useState('Cowork'), [group, setGroup] = useState(''), [days, setDays] = useState(0), [quota, setQuota] = useState(0);
  const session = useRef('');
  useEffect(() => () => { if (session.current) void rpc('models.account_close', {id: session.current}).catch(() => {}); }, []);
  async function run(method: string, params: any, accept: (result: any) => void | Promise<void>) {
    setBusy(true); setFailure('');
    try {const result = await operation(method, {id: account?.id, ...params}, setJob); await accept(result);}
    catch (error) {setFailure((error as Error).message);} finally {setBusy(false); setJob(null);}
  }
  async function loadKeys() {
    const result = await operation('models.account_keys', {id: session.current}, setJob);
    setKeys(result.keys); setGroups(result.groups); setStage('keys');
  }
  const toggle = (values: string[], value: string) => values.includes(value) ? values.filter(item => item !== value) : [...values, value];
  return <><PageHead title="通过账号添加模型" back={back}/><JobProgress job={job}/>{failure && <ErrorState message={failure}/>}
    {stage === 'site' ? <form className="detail-form" onSubmit={event => {event.preventDefault(); void run('models.account_open', {url, platform}, result => {session.current=result.id; setAccount(result); setName(new URL(result.site_url).hostname); setStage('login');});}}><div className="form-grid">
      <Field label="站点地址" wide><input type="url" required value={url} onChange={event => setUrl(event.target.value)} placeholder="https://模型服务站点"/></Field>
      <Field label="站点类型"><select value={platform} onChange={event => setPlatform(event.target.value)}><option value="auto">自动识别</option><option value="sub2api">sub2api</option><option value="new_api">New API</option></select></Field></div>
      <button className="primary" disabled={busy}><ArrowRight size={16}/>识别站点</button></form>
    : stage === 'login' ? <form className="detail-form" onSubmit={event => {event.preventDefault(); void run(twoFactor ? 'models.account_verify' : 'models.account_login', twoFactor ? {code} : {username, password}, async result => {setPassword('');setCode('');if (result.requires_2fa) setTwoFactor(true); else await loadKeys();});}}>
      <p className="muted">{account.site_url} · {account.platform === 'new_api' ? 'New API' : 'sub2api'}</p><div className="form-grid">{twoFactor ? <Field label="验证码"><input required autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)}/></Field> : <>
      <Field label="账号"><input required autoComplete="username" value={username} onChange={event => setUsername(event.target.value)}/></Field><Field label="密码"><input required type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)}/></Field></>}</div>
      <div className="button-row"><button className="primary" disabled={busy}>{twoFactor ? '验证' : '登录'}</button><button className="secondary" type="button" onClick={() => void rpc('external.open', {url: account.site_url})}>打开站点</button></div></form>
    : stage === 'keys' ? <><div className="section-heading"><h3>选择密钥</h3><button className="icon-button" title="刷新密钥" disabled={busy} onClick={() => void run('models.account_keys', {}, result => {setKeys(result.keys);setGroups(result.groups);})}><RefreshCw size={16}/></button></div>
      {!keys.length && <p className="muted">还没有可用密钥，可以在下方创建。</p>}{keys.map(key => <label className="model-choice" key={key.id}><input type="checkbox" disabled={!key.usable || busy} checked={chosenKeys.includes(key.id)} onChange={() => setChosenKeys(toggle(chosenKeys,key.id))}/><KeyRound size={17}/><div><strong>{key.name}</strong><small>{key.group_name} · {key.quota_label} · {key.reason || '可用'}</small></div></label>)}
      <details className="settings-section"><summary>创建 Cowork 专用密钥</summary><div className="form-grid"><Field label="密钥名称"><input value={keyName} onChange={event => setKeyName(event.target.value)}/></Field><Field label="分组"><select value={group} onChange={event => setGroup(event.target.value)}><option value="">默认分组</option>{groups.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field><Field label="有效天数" help="0 表示不过期"><input type="number" min={0} value={days} onChange={event => setDays(+event.target.value)}/></Field><Field label="额度" help="0 表示不另设密钥额度上限；计价遵循站点规则"><input type="number" min={0} value={quota} onChange={event => setQuota(+event.target.value)}/></Field></div>
        <div className="button-row"><button className="secondary" disabled={busy} onClick={async () => {if (await confirm('创建站点密钥？', `将在 ${account.site_url} 创建“${keyName}”，权限与额度按上方设置。`, ['取消','创建']) === 1) await run('models.account_create_key', {name:keyName,group,days,quota}, async result => {await loadKeys();setChosenKeys([result.id]);});}}><Plus size={15}/>创建</button><button className="secondary" disabled={busy} onClick={() => void run('models.account_reconcile', {}, async result => {await loadKeys();setChosenKeys([result.id]);})}>核对创建结果</button></div></details>
      <button className="primary" disabled={busy || !chosenKeys.length} onClick={() => void run('models.account_discover', {keys:chosenKeys}, result => {setRecords(result.records);setChosen(result.records.filter((item:any) => !item.discovery?.unsupported_reason).map((item:any) => JSON.stringify([item.key_id,item.id])));setStage('models');})}>获取所选密钥的模型</button></>
    : <><Field label="服务名称"><input value={name} onChange={event => setName(event.target.value)}/></Field><div className="model-selection">{records.map(record => {const identity=JSON.stringify([record.key_id,record.id]);return <label className="model-choice" key={identity}><input type="checkbox" checked={chosen.includes(identity)} disabled={busy || Boolean(record.discovery?.unsupported_reason)} onChange={() => setChosen(toggle(chosen,identity))}/><div><strong>{record.display_name || record.id}</strong><small>{record.key_name} · {record.api_protocol} {record.discovery?.unsupported_reason || record.discovery?.metadata_warning || ''}</small></div></label>;})}</div><div className="button-row"><button className="secondary" disabled={busy} onClick={() => setStage('keys')}>重新选择密钥</button><button className="primary" disabled={busy || !chosen.length} onClick={() => void run('models.account_save', {name,selected:chosen.map(item => JSON.parse(item))}, result => {saved(result);back();})}><Check size={16}/>添加所选模型</button></div></>}
  </>;
}
