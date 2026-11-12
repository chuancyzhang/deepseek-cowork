import {createContext,useContext,useEffect,useState,type ReactNode} from 'react';
import {HostStore} from './store';
import {StoreContext,useHostField} from './context';
import {useWorkbenchController,type WorkbenchController} from './workbench';
import {PluginRegistry} from '../plugins/registry';
import {SaveCoordinator} from './saveCoordinator';
import {HostSDK} from './sdk';

type HostController=WorkbenchController & {registry:PluginRegistry;sdk:HostSDK};
const ControllerContext=createContext<HostController|null>(null);
export function useWorkbench(){const value=useContext(ControllerContext);if(!value)throw new Error('宿主尚未启动。');return value;}
function Runtime({children}:{children:ReactNode}) {
  const controller=useWorkbenchController(),{host,saves}=controller;
  const registry=host.get('pluginRegistry',()=>new PluginRegistry());
  const sdk=host.get('sdk',()=>new HostSDK(host));
  const value={...controller,registry,sdk};sdk.controller=value;
  const [started,setStarted]=useHostField('host:started',false);
  const [failure,setFailure]=useHostField('host:failure','');
  const [retrying,setRetrying]=useState(false);
  const [startupMessage,setStartupMessage]=useState('正在恢复工作台…');
  const [exiting,setExiting]=useState(false);
  async function exit(){
    setExiting(true);
    try {await window.cowork.call('app.quit');}
    catch(error){setFailure((error as Error).message);setExiting(false);}
  }
  async function retry(){
    setRetrying(true);
    try {await window.cowork.call('app.reconnect');location.reload();}
    catch(error){setFailure((error as Error).message);setRetrying(false);}
  }
  useEffect(()=>{
    let alive=true;
    async function start(){
      const recovery=await window.cowork.call<{value:Record<string,unknown>;revision:number}>('host.recovery.get');
      for(const [key,value] of Object.entries(recovery.value))if(key.startsWith('form:')||key.startsWith('plugin-state:'))host.set(key,value);
      host.set('host:recoveryRevision',recovery.revision);
      try {await registry.refresh();}catch(error){setFailure('自定义界面未能加载：'+(error as Error).message);}
      if(alive){setStarted(true);await window.cowork.call('app.ui_ready');}
    }
    void start().catch(error=>{if(alive)setFailure('恢复未保存内容失败：'+error.message);});
    const unregister=saves.register('forms',async()=>{
      const value=Object.fromEntries([...host.values].filter(([key])=>key.startsWith('form:')||key.startsWith('plugin-state:')));
      const result=await window.cowork.call<{revision:number}>('host.recovery.save',{value,revision:host.get('host:recoveryRevision',-1)});
      host.set('host:recoveryRevision',result.revision);
    });
    return ()=>{alive=false;unregister();};
  },[host,registry,saves]);
  useEffect(()=>{
    let alive=true;
    void window.cowork.call<{message?:string}|null>('app.startup_status').then(value=>{if(alive&&value?.message)setStartupMessage(value.message);});
    const unsubscribe=window.cowork.onEvents(events=>{for(const event of events)if(event.event==='service_starting')setStartupMessage((event as any).message);});
    return()=>{alive=false;unsubscribe();};
  },[]);
  useEffect(()=>window.cowork.onEvents(events=>{if(events.some(event=>event.event==='plugins_recover_requested'))host.set('host:recoveryOpen',true);}),[host]);
  if(!started)return <main className="loading host-startup" aria-busy={retrying||!failure}>
    <h1>DeepSeek Cowork</h1>
    <p role="status">{exiting?'正在停止后台并退出…':failure?'暂时无法打开工作台':startupMessage}</p>
    {failure&&<><p>原有数据仍保留。可查看诊断日志，处理后重试。</p>
      <details><summary>错误详情</summary><pre>{failure}</pre></details>
      <div className="button-row"><button className="primary" disabled={retrying||exiting} onClick={()=>void retry()}>{retrying?'正在重试…':'重试'}</button>
        <button className="secondary" onClick={()=>void window.cowork.call('app.logs').catch(error=>setFailure(error.message))}>查看诊断日志</button></div></>}
    <button className="secondary" disabled={exiting} onClick={()=>void exit()}>退出应用</button>
  </main>;
  return <ControllerContext.Provider value={value}>{children}</ControllerContext.Provider>;
}
export function HostProvider({children}:{children:ReactNode}) {
  const [store]=useState(()=>new HostStore());store.get('saveCoordinator',()=>new SaveCoordinator());
  return <StoreContext.Provider value={store}><Runtime>{children}</Runtime></StoreContext.Provider>;
}
