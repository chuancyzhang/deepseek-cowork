import {useState} from 'react';
import {LoaderCircle, RefreshCw, X} from 'lucide-react';
import {useHost, useHostField} from '../host/context';
import {receiveUpdate, type UpdateState} from '../host/updates';
import {rpc} from '../managementApi';

const sources:Record<string,string>={github:'GitHub',public:'公共更新源',enterprise:'企业中心',local:'本地安装包'};
const installLabel=(state:UpdateState)=>state.reinstall?'重启并重新安装':'重启并更新';
const install=()=>rpc('settings.action',{page:'update',action:'update.install'});

export function UpdateNotice() {
  const host=useHost(),[state]=useHostField<UpdateState|null>('updates',null);
  const [failure,setFailure]=useState(''),[busy,setBusy]=useState(false);
  if(!state?.notice)return null;
  return <section className="update-notice" aria-label="可安装的更新">
    <div className="update-notice-heading"><span role="status">{state.version} 已准备好</span>
      <button className="icon-button" title="暂时关闭更新提示" aria-label="暂时关闭更新提示" onClick={()=>void rpc<UpdateState>('updates.dismiss',{identity:state.identity}).then(value=>receiveUpdate(host,value)).catch(error=>setFailure(error.message))}><X size={14}/></button></div>
    <button className="primary" disabled={busy||!state.can_install} onClick={async()=>{setBusy(true);setFailure('');try{await install();}catch(error){setFailure((error as Error).message);}finally{setBusy(false);}}}><RefreshCw size={14}/>{installLabel(state)}</button>
    {failure&&<p role="alert">{failure}</p>}
  </section>;
}

export default function Updates() {
  const host=useHost(),[state]=useHostField<UpdateState|null>('updates',null);
  const [failure,setFailure]=useState(''),[requesting,setRequesting]=useState(false);
  async function action(method:string,params?:any) {
    setRequesting(true);setFailure('');
    try {receiveUpdate(host,await rpc<UpdateState>(method,params));}
    catch(error){setFailure((error as Error).message);}
    finally{setRequesting(false);}
  }
  if(!state)return <div className="loading"><LoaderCircle className="spin"/>正在读取更新状态…<button onClick={()=>void action('updates.get')}>重试</button>{failure&&<p role="alert">{failure}</p>}</div>;
  const installing=['planning','installing'].includes(state.phase);
  return <div className="update-settings">
    <section className="settings-section"><h3>DeepSeek Cowork</h3><p className="muted">当前版本 {state.current_version}</p>
      <div className="button-row"><button className={state.ready?'secondary':'primary'} disabled={requesting||state.busy||installing} onClick={()=>void action('updates.check')}>检查更新</button>
        <button className="secondary" disabled={requesting||installing||!state.packaged} onClick={async()=>{try{const path=await rpc('dialog.choose',{kind:'update'});if(path)await action('updates.local',{path});}catch(error){setFailure((error as Error).message);}}}>选择本地安装包</button>
        {state.busy&&!installing&&<button className="secondary" disabled={requesting} onClick={()=>void action('updates.cancel')}>取消准备</button>}</div>
      {!state.packaged&&<p className="muted">开发运行可以检查版本，请在完整 Windows 安装包中进行更新。</p>}
    </section>
    <section className="settings-section" aria-live="polite">
      {state.version&&<h3>{state.version}{state.reinstall?' · 重新安装当前版本':''}</h3>}
      {state.source&&<p className="muted">来源：{sources[state.source]||state.source}</p>}
      <p role="status">{state.message}</p>
      {state.busy&&<progress aria-label="更新准备进度" max={100} value={state.percent>=0?state.percent:undefined}/>}
      {state.notes&&<div className="update-notes">{state.notes}</div>}
      {state.ready&&<><button className="primary" disabled={!state.can_install||requesting} onClick={async()=>{setRequesting(true);setFailure('');try{await install();}catch(error){setFailure((error as Error).message);}finally{setRequesting(false);}}}>{installLabel(state)}</button>
        {state.source==='local'&&<p className="muted">{state.local_manifest_verified?'已核对配套清单、包结构及完整性。':'已检查包结构及完整性；此包未附配套清单，未验证发布者身份。'}</p>}</>}
      {(failure||state.error)&&<div className="inline-error" role="alert"><p>{failure||'可重新检查更新或选择本地安装包，原有内容已保留。'}</p>
        {state.error&&<details><summary>错误详情</summary><pre>{state.error}</pre></details>}</div>}
    </section>
  </div>;
}
