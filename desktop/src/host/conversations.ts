import type {Conversation,HostEvent,Run,RuntimeEvent} from '../types';
import type {HostStore,Update} from './store';
import {mergeEvents,isActive} from '../runtime.ts';

/** Backend snapshots and durable increments enter the same host projection. */
export function receiveRun(store:HostStore,run:Run|null,id:string) {
  const previous=store.get<Record<string,Run>>('runs',{})[id];
  if(run && previous && (run.id===previous.id ? (run.updated_at||0)<(previous.updated_at||0) : (run.created_at||0)<(previous.created_at||0)))return;
  if(!run&&previous)return; // A snapshot without a run cannot erase a later accepted run.
  if(run)store.set<Record<string,Run>>('runs',value=>({...value,[id]:run}));
}
export function conversationAt(store:HostStore,id:string):Conversation|null {
  const value=store.get<Conversation|null>(store.sessionKey('current',id),null);
  return value?{...value,run:store.get<Record<string,Run>>('runs',{})[id]||null}:null;
}
export function receiveConversation(store:HostStore,id:string,update:Update<Conversation|null>) {
  const previous=conversationAt(store,id),value=typeof update==='function'?update(previous):update;
  if(!value){store.set(store.sessionKey('current',id),null);return;}
  if(value.run===null&&!isActive(previous?.run?.status))store.set<Record<string,Run>>('runs',runs=>Object.fromEntries(Object.entries(runs).filter(([key])=>key!==id)));
  if('run' in value)receiveRun(store,value.run||null,id);
  const {run,...body}=value;store.set(store.sessionKey('current',id),body);
}
export function receiveEvents(store:HostStore,batch:HostEvent[]) {
  const grouped=new Map<string,RuntimeEvent[]>();
  for(const event of batch){
    const id=event.conversation_id;if(!id)continue;
    if(event.run)receiveRun(store,event.run,id);
    if(event.event!=='runtime')continue;
    if(!grouped.has(id))grouped.set(id,[]);grouped.get(id)!.push(event.data);
    const key=store.sessionKey('interaction',id);
    if(event.data.type==='interaction_request')store.set(key,event.data.data);
    if(event.data.type==='interaction_finished')store.set<any>(key,(previous:any)=>previous?.request_id===event.data.data?.request_id?null:previous);
  }
  for(const [id,events] of grouped)store.set<RuntimeEvent[]>(store.sessionKey('events',id),previous=>mergeEvents(previous||[],events));
}
