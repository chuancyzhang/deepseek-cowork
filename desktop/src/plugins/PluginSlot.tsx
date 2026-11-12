import {useSyncExternalStore,type ReactNode} from 'react';
import type {Extension} from '../types';
import type {PluginScope} from '../host/sdk';
import {useWorkbench} from '../host/Host';
import PluginFrame from './PluginFrame';
import type {Contribution} from './registry';
export default function PluginSlot({kind,target,scope,children}:{kind:Extension['kind'];target:string;scope?:Partial<PluginScope>;children?:ReactNode}) {
  const {registry,current}=useWorkbench();useSyncExternalStore(registry.subscribe,registry.snapshot);
  const context={...(kind==='workbench'?{}:{conversationId:current?.id}),...scope};
  const active=registry.resolve(kind,target),candidate=registry.candidate;
  const proposed=candidate?registry.contributions(candidate.plugin,kind,target):[];
  const native=active.length===0||active.some(item=>item.plugin.builtin&&item.plugin.id==='cowork.workbench');
  const entries=new Map<string,{item:Contribution;active:boolean}>();
  for(const item of active.filter(item=>!(item.plugin.builtin&&item.plugin.id==='cowork.workbench')))entries.set(`${item.plugin.hash}/${item.extension.id}`,{item,active:true});
  for(const item of proposed)if(!entries.has(`${item.plugin.hash}/${item.extension.id}`))entries.set(`${item.plugin.hash}/${item.extension.id}`,{item,active:false});
  return <div className="plugin-slot">
    {native&&<div className="plugin-native">{children}</div>}
    {[...entries].map(([key,{item,active}])=><PluginFrame key={`${key}:${context.conversationId||''}:${context.filePath||''}:${context.tabId||''}`} plugin={item.plugin} extension={item.extension}
      scope={{...context,extensionId:item.extension.id}} mode={active?'active':'warm'} hidden={!active}
      readyKey={`${kind}:${target}:${context.conversationId||''}:${context.filePath||''}:${context.tabId||''}:${item.extension.id}`}/>)}
  </div>;
}
export function CandidateProbes(){
  const {registry}=useWorkbench();useSyncExternalStore(registry.subscribe,registry.snapshot);
  const plugin=registry.candidate?.plugin;
  return plugin?<>{plugin.contributes.map(extension=><PluginFrame key={`${plugin.hash}/${extension.id}`} plugin={plugin} extension={extension} scope={{extensionId:extension.id}} mode="warm" hidden readyKey={`probe:${extension.id}`}/>)}</>:null;
}
export function CommandServices(){
  const {registry}=useWorkbench();useSyncExternalStore(registry.subscribe,registry.snapshot);
  return <>{registry.inventory.plugins.filter(plugin=>plugin.enabled&&!plugin.builtin).flatMap(plugin=>plugin.contributes.filter(extension=>extension.kind==='command').map(extension=><PluginFrame key={`${plugin.hash}/${extension.id}`} plugin={plugin} extension={extension} scope={{extensionId:extension.id}} hidden/>))}</>;
}
