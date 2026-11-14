import {useEffect,useSyncExternalStore} from 'react';
import {Plus,Code2,FileText} from 'lucide-react';
import {useWorkbench} from '../host/Host';
import type {Plugin} from '../types';
const call=<T=any,>(method:string,params?:unknown)=>window.cowork.call<T>(method,params);
export default function PluginManager(){
  const {registry,saves,setPluginPreview,setNotice,error}=useWorkbench();
  useSyncExternalStore(registry.subscribe,registry.snapshot);
  useEffect(()=>{void registry.refresh().catch(reason=>error(reason.message));},[registry,error]);
  const catalog=registry.inventory.catalog||registry.inventory.plugins;
  const perform=(action:()=>Promise<unknown>)=>void action().catch(reason=>error(reason.message));
  async function preview(method:string,id?:string){const value=await call<Plugin|null>(method,id?{id}:{});if(value)setPluginPreview(value);}
  return <section aria-label="界面插件">
    <div className="management-toolbar"><button className="primary" disabled={registry.switching} onClick={()=>perform(()=>preview('plugins.preview'))}><Plus size={15}/>添加本地插件</button></div>
    <div className="plugin-list">{catalog.map(plugin=><section className="plugin-card" key={plugin.id}>
      <div className="plugin-icon"><FileText size={24}/></div><div><h3>{plugin.name}<small>v{plugin.version}{plugin.enabled===false?' · 已停用':''}</small></h3><p>{plugin.description}</p>
      <div className="button-row">
        {plugin.id!=='cowork.workbench'&&<button className="secondary" disabled={plugin.enabled===false} onClick={()=>perform(async()=>{const result=await call('plugins.export',{id:plugin.id});if(result)setNotice('插件源码已保存到：'+result.path);})}><Code2 size={14}/>导出可编辑源码</button>}
        {!plugin.builtin&&<>
          {plugin.previous&&<button className="secondary" disabled={registry.switching} onClick={()=>perform(()=>preview('plugins.rollback_preview',plugin.id))}>预览上一版</button>}
          <button className="secondary" disabled={registry.switching} onClick={()=>perform(()=>plugin.enabled===false?preview('plugins.enable_preview',plugin.id):registry.disable(plugin.id,saves))}>{plugin.enabled===false?'预览并启用':'停用'}</button>
        </>}
      </div></div>
    </section>)}</div>
    <details className="plugin-explainer"><summary>如何自定义界面</summary><p>导出源码并修改后，通过“添加本地插件”选择文件夹，预览后应用。</p><p>右下角的“界面恢复与诊断”可停用自定义插件或恢复内置界面。</p></details>
  </section>;
}
