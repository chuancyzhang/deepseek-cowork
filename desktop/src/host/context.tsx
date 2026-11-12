import {createContext,useContext,useCallback,useSyncExternalStore,useMemo} from 'react';
import {HostStore,type Update} from './store';
import type {Conversation,Run} from '../types';
import {receiveConversation} from './conversations';
export const StoreContext=createContext<HostStore|null>(null);
export function useHost() { const host=useContext(StoreContext); if (!host) throw new Error('工作台宿主尚未启动。'); return host; }
export function useHostField<T>(key:string,initial:T|(()=>T)):[T,(value:Update<T>)=>void] {
  const host=useHost();
  const read=useCallback(()=>host.get<T>(key,initial),[host,key]);
  const subscribe=useCallback((listener:()=>void)=>host.subscribe(key,listener),[host,key]);
  const value=useSyncExternalStore(subscribe,read,read);
  return [value,useCallback((next:Update<T>)=>{host.set(key,next);},[host,key])];
}
export function useSessionField<T>(field:string,initial:T|(()=>T)):[T,(value:Update<T>)=>void] {
  const host=useHost(),[id]=useHostField('navigation:selected','');
  const [value]=useHostField(host.sessionKey(field,id),initial);
  return [value,useCallback((next:Update<T>)=>{host.set(host.sessionKey(field),next);},[host,field])];
}
export function useConversation():[Conversation|null,(value:Update<Conversation|null>)=>void]{
  const host=useHost(),[value]=useSessionField<Conversation|null>('current',null),[runs]=useHostField<Record<string,Run>>('runs',{});
  const current=useMemo(()=>value?{...value,run:runs[value.id]||null}:null,[value,runs[value?.id||'']]);
  return [current,useCallback((next:Update<Conversation|null>)=>receiveConversation(host,host.get('navigation:selected',''),next),[host])];
}
