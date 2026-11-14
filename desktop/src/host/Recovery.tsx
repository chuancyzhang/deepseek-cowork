import {useState,useSyncExternalStore} from 'react';
import {X,RefreshCw,Code2} from 'lucide-react';
import Dialog from '../Dialog';
import ThemePreview from '../ThemePreview';
import {useHostField} from './context';
import {useWorkbench} from './Host';
import PluginFrame from '../plugins/PluginFrame';
import {CandidateProbes,CommandServices} from '../plugins/PluginSlot';
import type {Plugin} from '../types';
import PluginMenu from '../plugins/PluginMenu';
const call=<T=any,>(method:string,params?:unknown)=>window.cowork.call<T>(method,params);
export default function HostRecovery(){
  const c=useWorkbench(),{registry,host,saves,ask,setAsk,notice,setNotice,pluginPreview,setPluginPreview,error}=c;
  useSyncExternalStore(registry.subscribe,registry.snapshot);
  const [open,setOpen]=useHostField('host:recoveryOpen',false),[failure,setFailure]=useHostField('host:failure','');
  const [pending]=useHostField('query:pending',false),[index,setIndex]=useState(0),[previewReady,setPreviewReady]=useState(false);
  async function closePreview(){if(registry.switching)return;const token=pluginPreview?.token;setPluginPreview(null);setIndex(0);if(token)await call('plugins.discard',{token});}
  return <>
    <CandidateProbes/><CommandServices/><PluginMenu/>
    <button className="host-recovery-toggle" aria-label="界面恢复与诊断" title="界面恢复与诊断" onClick={()=>setOpen(true)}><RefreshCw size={14}/></button>
    {pending&&<div className="query-pending" role="status">正在同步历史记录…</div>}
    {notice&&<div className="notice host-notice" role="status"><span>{notice}</span><button className="icon-button" aria-label="关闭提示" onClick={()=>setNotice('')}><X size={15}/></button></div>}
    {failure&&<div className="service-error host-error" role="alert"><span>{failure}</span><button onClick={()=>setOpen(true)}>界面恢复</button><button onClick={()=>setFailure('')}>关闭提示</button></div>}
    {ask&&<Dialog title={ask.title} onClose={()=>{setAsk(null);ask.resolve(0);}}><p>{ask.detail}</p><div className="button-row">{ask.choices.map((choice,index)=><button key={choice} autoFocus={index===0} className={index===ask.choices.length-1?'primary':'secondary'} onClick={()=>{setAsk(null);ask.resolve(index);}}>{choice}</button>)}</div></Dialog>}
    {c.themePreview&&<ThemePreview candidate={c.themePreview} close={()=>c.setThemePreview(null)} changed={async()=>{const value=await call('themes.preview');c.setThemeCandidate(value.preview);}}/>}
    {open&&<Dialog title="界面恢复与诊断" onClose={()=>setOpen(false)}><p>恢复内置界面只替换界面代码。聊天、后台任务和未保存内容会保留。</p><div className="button-row"><button className="secondary" onClick={()=>void call('app.logs')}>查看诊断日志</button><button className="primary" disabled={registry.switching} onClick={()=>void registry.recover(saves).then(()=>{setFailure('');setOpen(false);}).catch(reason=>error(reason.message))}>恢复内置界面</button></div>{registry.inventory.plugins.filter(plugin=>!plugin.builtin&&plugin.enabled).map(plugin=><div className="recovery-plugin" key={plugin.id}><span>{plugin.name}</span><button className="secondary" onClick={()=>void registry.disable(plugin.id,saves).catch(reason=>error(reason.message))}>停用</button></div>)}</Dialog>}
    {pluginPreview&&<div className="modal-backdrop"><div className="modal preview-modal"><div className="section-heading"><div><h2>{pluginPreview.name}</h2><p>v{pluginPreview.version} · 独立预览使用示例内容</p></div><button className="icon-button" disabled={registry.switching} aria-label="取消预览" onClick={()=>void closePreview()}><X size={18}/></button></div>
      <label>预览区域 <select value={index} disabled={registry.switching} onChange={event=>setIndex(Number(event.target.value))}>{pluginPreview.contributes.map((extension,i)=><option key={extension.id} value={i}>{extension.title||extension.target} · {extension.kind}</option>)}</select></label>
      <div className="preview-frame"><PluginFrame key={`${pluginPreview.hash}/${index}`} plugin={pluginPreview} extension={pluginPreview.contributes[index]||pluginPreview.contributes[0]} scope={{extensionId:pluginPreview.contributes[index]?.id||'preview'}} mode="preview"/></div>
      <div className="button-row"><button className="secondary" disabled={registry.switching} onClick={()=>void closePreview()}>取消</button><button className="primary" disabled={registry.switching} onClick={()=>void registry.apply(pluginPreview,saves).then(()=>{setPluginPreview(null);setIndex(0);}).catch(reason=>error(reason.message))}>{registry.switching?'正在加载候选界面…':'应用这个版本'}</button></div>
    </div></div>}
  </>;
}
