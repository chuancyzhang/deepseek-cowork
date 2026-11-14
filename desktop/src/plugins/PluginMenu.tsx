import {useEffect,useState,useSyncExternalStore} from 'react';
import {useWorkbench} from '../host/Host';
import Dialog from '../Dialog';
export default function PluginMenu(){
  const {registry,sdk,error}=useWorkbench();useSyncExternalStore(registry.subscribe,registry.snapshot);
  const [open,setOpen]=useState(false);
  const menus=registry.inventory.plugins.filter(plugin=>plugin.enabled).flatMap(plugin=>plugin.contributes.filter(extension=>extension.kind==='menu').map(extension=>({plugin,extension})));
  useEffect(()=>{
    const listener=(event:KeyboardEvent)=>{if((event.ctrlKey||event.metaKey)&&event.shiftKey&&event.code==='KeyP'){event.preventDefault();setOpen(true);}};
    addEventListener('keydown',listener);return ()=>removeEventListener('keydown',listener);
  },[]);
  if(!menus.length)return null;
  return <><button className="host-menu-toggle" title="扩展操作（Ctrl+Shift+P）" onClick={()=>setOpen(true)}>扩展操作</button>{open&&<Dialog title="扩展操作" onClose={()=>setOpen(false)}><div className="plugin-commands">{menus.map(({plugin,extension})=><button className="secondary" key={`${plugin.id}/${extension.id}`} onClick={()=>{try{sdk.executeCommand(extension.command!);setOpen(false);}catch(reason){error((reason as Error).message);}}}>{extension.title}<small>{plugin.name}</small></button>)}</div></Dialog>}</>;
}
